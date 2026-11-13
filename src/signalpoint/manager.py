"""Verified, journaled module-folder replacement. Never execute publisher install code."""

import json
import os
import shutil
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from filelock import FileLock
from filelock import Timeout as LockTimeout

from signalpoint import game as bannerlord
from signalpoint.api import TransferControl
from signalpoint.manifest import (
    ModError,
    hashes,
    is_link,
    read_module,
    safe_path,
    validate_manifest,
)


def atomic_json(path, value):
    temporary = path.with_suffix(".new")
    with temporary.open("w", encoding="utf-8") as output:
        json.dump(value, output)
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(path)


def copy_tree(source, destination):
    destination.mkdir(parents=True, exist_ok=True)
    if is_link(source):
        raise ModError(f"Linked module directory cannot be managed: {source.name}")
    for root, directories, files in os.walk(source, followlinks=False):
        root = Path(root)
        relative = root.relative_to(source)
        for name in directories:
            if is_link(root / name):
                raise ModError(
                    "A module contains a junction or linked directory; resolve it first."
                )
            (destination / relative / name).mkdir(parents=True, exist_ok=True)
        for name in files:
            src, dst = root / name, destination / relative / name
            if is_link(src) or not src.is_file():
                raise ModError("A module contains a linked or unsupported file.")
            dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(src, dst)
            except OSError:
                shutil.copy2(src, dst)


class Manager:
    def __init__(self, state, game, api, *, process_check=bannerlord.running):
        self.state, self.game, self.api = state, bannerlord.validate_game(game), api
        self.modules = self.game / "Modules"
        self.work = self.game / ".signalpoint" / "transactions"
        self.cache = state.directory / "downloads"
        self.process_check = process_check
        self.lock = FileLock(str(state.directory / "manager.lock"), timeout=1)
        self.game_lock = FileLock(str(self.work.parent / "manager.lock"), timeout=1)

    @contextmanager
    def locked(self):
        self._safe_work(self.work / "check")
        try:
            self.work.parent.mkdir(parents=True, exist_ok=True)
            with self.lock, self.game_lock:
                yield
        except PermissionError as exc:
            raise ModError(
                "The game folder is not writable. Select an accessible installation "
                "using your store's installation settings."
            ) from exc
        except LockTimeout as exc:
            raise ModError(
                "Another SignalPoint operation is using this installation. Retry shortly."
            ) from exc

    def _safe_work(self, directory):
        if not directory.absolute().is_relative_to(self.work.absolute()) or directory == self.work:
            raise ModError("Invalid transaction directory.")
        for parent in (directory, *directory.parents):
            if is_link(parent):
                raise ModError("A transaction directory has been replaced with a link.")

    def _remove_work(self, directory):
        self._safe_work(directory)
        shutil.rmtree(directory)

    def _ensure_closed(self):
        if self.process_check():
            raise ModError("Close Bannerlord and its launcher before changing installed modules.")

    def recover(self):
        with self.locked():
            self._recover()

    def _recover(self):
        if not self.work.exists():
            return
        self._safe_work(self.work / "check")
        for directory in self.work.iterdir():
            self._safe_work(directory)
            journal = directory / "journal.json"
            if not journal.is_file():
                # Preparation never changes installed files before a journal exists.
                self._remove_work(directory)
                continue
            value = json.loads(journal.read_text(encoding="utf-8"))
            if not self.state.committed(value["id"]):
                self._ensure_closed()
                self._rollback(directory, value)
            self._remove_work(directory)
            self.state.forget_commit(value["id"])

    def _rollback(self, directory, journal):
        for operation in reversed(journal["operations"]):
            folder = safe_path(operation["folder"])
            if "/" in folder:
                raise ModError("Invalid recovery folder.")
            target = self.modules / folder
            backup = directory / "old" / folder
            staged = directory / "new" / folder
            if is_link(target) or is_link(backup) or is_link(staged):
                raise ModError("Recovery paths contain links; resolve them before continuing.")
            if backup.exists():
                if target.exists():
                    displaced = directory / "displaced" / folder
                    displaced.parent.mkdir(parents=True, exist_ok=True)
                    target.rename(displaced)
                backup.rename(target)
            elif not operation["had_old"] and target.exists() and not staged.exists():
                staged.parent.mkdir(parents=True, exist_ok=True)
                target.rename(staged)

    def _dependencies(self, manifest):
        available = {m["id"].casefold() for m in bannerlord.installed_modules(self.game)}
        available.update(m["id"].casefold() for m in manifest["modules"])
        missing = {
            dep["id"]
            for module in manifest["modules"]
            for dep in module["dependencies"]
            if not dep["optional"] and dep["id"].casefold() not in available
        }
        if missing:
            raise ModError("Install required modules first: " + ", ".join(sorted(missing)))

    def install(
        self, mod_id, name, build, manifest, *, repair=False, control=None, progress=lambda _: None
    ):
        validate_manifest(manifest)
        control = control or TransferControl()
        with self.locked():
            self._recover()
            control.check()
            self._dependencies(manifest)
            installs = self.state.installs(self.game)
            previous = installs.get(mod_id)
            if previous and previous.get("source") != self.api.base:
                raise ModError(
                    "This mod is managed through another backend. Restore its address first."
                )
            old = previous["manifest"] if previous else {"modules": [], "files": []}
            old_folders = {m["folder"] for m in old["modules"]}
            new_folders = {m["folder"] for m in manifest["modules"]}
            all_folders = old_folders | new_folders
            if len({f.casefold() for f in all_folders}) != len(all_folders):
                raise ModError("Publisher changed module-folder casing; keep it consistent.")
            adopted_folders = set()
            module_sources = {}
            retired_folders = set()
            installed_modules = bannerlord.installed_modules(self.game)
            for module in manifest["modules"]:
                for receipt in installs.values():
                    if receipt["mod_id"] != mod_id and any(
                        m["id"].casefold() == module["id"].casefold()
                        or m["folder"].casefold() == module["folder"].casefold()
                        for m in receipt["manifest"]["modules"]
                    ):
                        raise ModError("This module is already owned by another managed mod.")
                target = self.modules / module["folder"]
                if is_link(target):
                    raise ModError("A module destination is a link.")
                if target.exists() and module["folder"] not in old_folders:
                    actual = read_module(target)
                    if actual["id"].casefold() != module["id"].casefold():
                        raise ModError(
                            f"{module['folder']} contains a different module ({actual['id']})."
                        )
                matches = [
                    installed
                    for installed in installed_modules
                    if installed["id"].casefold() == module["id"].casefold()
                ]
                if len(matches) > 1:
                    folders = ", ".join(sorted(m["folder"] for m in matches))
                    raise ModError(
                        f"Module {module['id']} has multiple installed copies: {folders}. "
                        "Keep one copy before installing."
                    )
                if matches and matches[0]["folder"] not in old_folders:
                    installed_folder = matches[0]["folder"]
                    read_module(self.modules / installed_folder)
                    if module["folder"] in old_folders:
                        raise ModError(
                            f"Module {module['id']} also exists in {installed_folder}. "
                            "Keep the managed copy before updating."
                        )
                    adopted_folders.add(module["folder"])
                    if installed_folder.casefold() != module["folder"].casefold():
                        module_sources[module["folder"]] = installed_folder
                        retired_folders.add(installed_folder)
            all_folders |= retired_folders

            def installed_path(path):
                folder, relative = path.split("/", 1)
                return self.modules / module_sources.get(folder, folder) / relative

            old_files = {f["path"]: f for f in old["files"]}
            incoming_files = {f["path"]: f for f in manifest["files"]}
            added_files = {
                entry["path"]: entry
                for entry in manifest["files"]
                if entry["path"] not in old_files
            }
            verified = {}
            progress({"state": "Checking installed files", "done": 0, "total": 0})
            for path, entry in old_files.items():
                control.check()
                source = self.modules / path
                if is_link(source) or any(is_link(p) for p in source.parents if p != self.game):
                    raise ModError("A managed path has become a link.")
                candidates = [entry]
                if path in incoming_files:
                    candidates.append(incoming_files[path])
                if source.is_file() and any(
                    source.stat().st_size == candidate["size"] for candidate in candidates
                ):
                    sha = hashes(source, lambda _: control.check())[0]
                    if any(sha == candidate["sha256"] for candidate in candidates):
                        verified[path] = sha
                        continue
                if source.exists() and not repair:
                    raise ModError(
                        f"Locally modified file: {path}. Use Repair to replace managed edits."
                    )
            for path, entry in added_files.items():
                control.check()
                source = installed_path(path)
                if is_link(source) or any(is_link(p) for p in source.parents if p != self.game):
                    raise ModError("An existing module path contains a link.")
                if source.exists() and not source.is_file():
                    raise ModError(f"A directory occupies {path}. Move it before installing.")
                if (
                    source.is_file()
                    and source.stat().st_size == entry["size"]
                    and hashes(source, lambda _: control.check())[0] == entry["sha256"]
                ):
                    verified[path] = entry["sha256"]
                elif (
                    source.exists() and path.split("/", 1)[0] not in adopted_folders and not repair
                ):
                    raise ModError(
                        f"Local file differs from the published update: {path}. "
                        "Use Repair to replace it with the published file."
                    )
            needed = []
            for entry in manifest["files"]:
                if verified.get(entry["path"]) != entry["sha256"]:
                    needed.append(entry)
            # Downloads occur before the short filesystem transaction, including while
            # Bannerlord is running. Only verified content reaches the staged modules.
            needed = list({f["sha256"]: f for f in needed}.values())
            total = sum(f["size"] for f in needed)
            done = 0
            self.api.prepare_downloads(build, needed)
            for entry in needed:
                control.check()
                self.api.download(
                    build,
                    entry,
                    self.cache / (entry["sha256"] + ".part"),
                    control,
                    lambda count, done=done: progress(
                        {"state": "Downloading", "done": done + count, "total": total}
                    ),
                )
                done += entry["size"]
            while self.process_check():
                progress(
                    {"state": "Waiting for Bannerlord to close", "done": total, "total": total}
                )
                control.check()
                time.sleep(0.5)
            control.check()
            progress({"state": "Verifying", "done": total, "total": total})
            self._safe_work(self.work / "check")
            transaction = uuid.uuid4().hex
            directory = self.work / transaction
            directory.mkdir(parents=True)
            try:
                for folder in sorted(all_folders):
                    target = directory / "new" / folder
                    source = self.modules / module_sources.get(folder, folder)
                    if folder not in retired_folders and source.exists():
                        copy_tree(source, target)
                    else:
                        target.mkdir(parents=True)
                for entry in (old_files | incoming_files).values():
                    target = directory / "new" / entry["path"]
                    if target.is_file():
                        target.unlink()
                    elif target.exists():
                        raise ModError("A managed file was replaced by a directory.")
                for entry in manifest["files"]:
                    control.check()
                    target = directory / "new" / entry["path"]
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if target.exists():
                        raise ModError(f"Destination conflict: {entry['path']}")
                    if verified.get(entry["path"]) == entry["sha256"]:
                        source = installed_path(entry["path"])
                        try:
                            os.link(source, target)
                        except OSError:
                            shutil.copy2(source, target)
                    else:
                        source = self.cache / (entry["sha256"] + ".part")
                        if hashes(source)[0] != entry["sha256"]:
                            raise ModError("Cached file failed verification; retry the download.")
                        shutil.copy2(source, target)
                for entry in manifest["files"]:
                    control.check()
                    if (
                        hashes(directory / "new" / entry["path"], lambda _: control.check())[0]
                        != entry["sha256"]
                    ):
                        raise ModError(f"File changed during preparation: {entry['path']}. Retry.")
                for module in manifest["modules"]:
                    actual = read_module(directory / "new" / module["folder"])
                    if actual != module:
                        raise ModError(
                            "SubModule.xml does not match the published module metadata."
                        )
                control.check()
                receipt = {
                    "mod_id": mod_id,
                    "name": name,
                    "build_id": build,
                    "manifest": manifest,
                    "source": self.api.base,
                }
                self._commit(directory, transaction, all_folders, mod_id, receipt, progress)
            except BaseException:
                if directory.exists() and not (directory / "journal.json").exists():
                    self._remove_work(directory)
                raise
            # Cache holds only content needed for resumable unfinished transfers.
            for entry in needed:
                (self.cache / (entry["sha256"] + ".part")).unlink(missing_ok=True)
            progress({"state": "Ready", "done": total, "total": total})
            return receipt

    def _commit(self, directory, transaction, folders, mod_id, receipt, progress):
        self._ensure_closed()
        operations = [
            {"folder": f, "had_old": (self.modules / f).exists()} for f in sorted(folders)
        ]
        journal = {"id": transaction, "operations": operations}
        atomic_json(directory / "journal.json", journal)
        progress({"state": "Installing" if receipt else "Uninstalling", "done": 0, "total": 0})
        try:
            for operation in operations:
                self._ensure_closed()
                folder = operation["folder"]
                target, backup, staged = (
                    self.modules / folder,
                    directory / "old" / folder,
                    directory / "new" / folder,
                )
                backup.parent.mkdir(parents=True, exist_ok=True)
                if operation["had_old"]:
                    target.rename(backup)
                if staged.exists() and any(staged.rglob("*")):
                    staged.rename(target)
            self.state.commit(self.game, mod_id, receipt, transaction)
        except BaseException:
            if not self.state.committed(transaction):
                self._rollback(directory, journal)
            raise
        self._remove_work(directory)
        self.state.forget_commit(transaction)

    def uninstall(self, mod_id, progress=lambda _: None):
        with self.locked():
            self._recover()
            self._ensure_closed()
            installs = self.state.installs(self.game)
            receipt = installs.get(mod_id)
            if not receipt:
                raise ModError("This mod is not managed by SignalPoint.")
            removed_ids = {m["id"].casefold() for m in receipt["manifest"]["modules"]}
            for key, other in installs.items():
                if key != mod_id and any(
                    dep["id"].casefold() in removed_ids and not dep["optional"]
                    for module in other["manifest"]["modules"]
                    for dep in module["dependencies"]
                ):
                    raise ModError(f"{other['name']} still requires this mod.")
            transaction = uuid.uuid4().hex
            directory = self.work / transaction
            self._safe_work(directory)
            directory.mkdir(parents=True)
            folders = {m["folder"] for m in receipt["manifest"]["modules"]}
            preserved = []
            try:
                for folder in folders:
                    source = self.modules / folder
                    if source.exists():
                        copy_tree(source, directory / "new" / folder)
                for entry in receipt["manifest"]["files"]:
                    target = directory / "new" / entry["path"]
                    if target.is_file() and hashes(target)[0] == entry["sha256"]:
                        target.unlink()
                    elif target.exists():
                        preserved.append(entry["path"])
                for path in sorted(
                    (directory / "new").rglob("*"), key=lambda p: len(p.parts), reverse=True
                ):
                    if path.is_dir() and not any(path.iterdir()):
                        path.rmdir()
                self._commit(directory, transaction, folders, mod_id, None, progress)
            except BaseException:
                if directory.exists() and not (directory / "journal.json").exists():
                    self._remove_work(directory)
                raise
            return preserved

    def launch(self, *, store_hint=None, progress=lambda _: None, control=None):
        with self.locked():
            self._recover()
            bannerlord.start_launcher(
                self.game, store_hint=store_hint, progress=progress, control=control
            )
