"""Folder-only module scanning and the public manifest wire contract."""

import base64
import hashlib
import json
import os
import re
from collections.abc import Callable
from pathlib import Path

from defusedxml import ElementTree

OFFICIAL = {
    "native",
    "sandbox",
    "sandboxcore",
    "storymode",
    "custombattle",
    "multiplayer",
    "birthanddeath",
    "fastmode",
    "navaldlc",
}
RESERVED = re.compile(r"^(CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])(?:\.|$)", re.I)
CHUNK = 1024 * 1024
MAX_UPLOAD_FILE_SIZE = 5 * 1024**3


class ModError(Exception):
    """An actionable error that can be presented to the user."""


def validate_upload_sizes(files):
    oversized = [entry for entry in files if entry["size"] > MAX_UPLOAD_FILE_SIZE]
    if oversized:
        lines = ["Upload blocked: files exceed the 5 GiB per-file limit."]
        lines.extend(
            f"{entry['path']}: {entry['size'] / 1024**3:.2f} GiB ({entry['size']:,} bytes)"
            for entry in oversized[:20]
        )
        if len(oversized) > 20:
            lines.append(f"And {len(oversized) - 20} more oversized files.")
        lines.append("Maximum per file: 5 GiB (5,368,709,120 bytes), including TPAC files.")
        raise ModError("\n".join(lines))


def safe_path(value: str) -> str:
    if not value or len(value) > 220 or "\\" in value:
        raise ModError(f"Invalid module path: {value!r}")
    for part in value.split("/"):
        if (
            not part
            or part in {".", ".."}
            or part[-1:] in {" ", "."}
            or RESERVED.match(part)
            or any(c in '<>:"|?*' or ord(c) < 32 for c in part)
        ):
            raise ModError(f"Invalid module path: {value!r}")
    return value


def is_link(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


def hashes(path: Path, tick: Callable[[int], None] | None = None) -> tuple[str, str]:
    sha = hashlib.sha256()
    md5 = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as source:
        while block := source.read(CHUNK):
            sha.update(block)
            md5.update(block)
            if tick:
                tick(len(block))
    return sha.hexdigest(), base64.b64encode(md5.digest()).decode("ascii")


def read_module(folder: Path, *, allow_official=False) -> dict:
    if not folder.is_dir() or is_link(folder):
        raise ModError("Choose a readable module folder, not a file, archive, or linked folder.")
    safe_path(folder.name)
    if not allow_official and folder.name.casefold() in OFFICIAL:
        raise ModError("Official Bannerlord modules cannot be distributed through SignalPoint.")
    matches = [p for p in folder.iterdir() if p.name.casefold() == "submodule.xml"]
    if len(matches) != 1 or not matches[0].is_file() or is_link(matches[0]):
        raise ModError("Choose the module folder with SubModule.xml directly inside it.")
    if matches[0].stat().st_size > 1024 * 1024:
        raise ModError("SubModule.xml exceeds the 1 MiB metadata limit.")
    try:
        root = ElementTree.parse(matches[0]).getroot()
    except Exception as exc:
        raise ModError(f"Cannot read SubModule.xml: {exc}") from exc
    if root.tag != "Module":
        raise ModError("SubModule.xml must have a Module root element.")

    def value(tag):
        element = root.find(tag)
        result = element.get("value", "").strip() if element is not None else ""
        if not result or len(result) > 200:
            raise ModError(f"SubModule.xml needs a valid {tag} value.")
        return result

    module = {
        "id": value("Id"),
        "name": value("Name"),
        "version": value("Version"),
        "folder": folder.name,
        "dependencies": [],
    }
    if not allow_official and module["id"].casefold() in OFFICIAL:
        raise ModError("Official Bannerlord module IDs are reserved.")
    for node in root.findall("./DependedModules/DependedModule"):
        dependency = node.get("Id", "").strip()
        if not dependency:
            raise ModError("A module dependency is missing its Id.")
        module["dependencies"].append(
            {
                "id": dependency,
                "version": node.get("DependentVersion", ""),
                "optional": node.get("Optional", "false").casefold() == "true",
            }
        )
    return module


def fingerprint(manifest: dict) -> str:
    return hashlib.sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


def scan(folders: list[Path], progress: Callable[[dict], None] = lambda _: None) -> dict:
    if not folders:
        raise ModError("Choose at least one module folder.")
    modules, paths, seen_ids, seen_paths = [], [], set(), set()
    for selected in folders:
        folder = selected.absolute()
        if any(is_link(parent) for parent in (folder, *folder.parents)):
            raise ModError("Select real module folders, without symlinks or junctions.")
        module = read_module(folder)
        if module["id"].casefold() in seen_ids:
            raise ModError(f"Duplicate module ID: {module['id']}")
        seen_ids.add(module["id"].casefold())
        modules.append(module)
        for directory, dirs, names in os.walk(folder, followlinks=False):
            directory = Path(directory)
            for name in dirs:
                if is_link(directory / name):
                    raise ModError(f"Linked folder cannot be uploaded: {directory / name}")
            for name in sorted(names):
                path = directory / name
                if is_link(path) or not path.is_file():
                    raise ModError(f"Only ordinary files can be uploaded: {path}")
                if directory != folder and name.casefold() == "submodule.xml":
                    raise ModError("Nested modules must be added as separate module folders.")
                relative = safe_path(f"{folder.name}/{path.relative_to(folder).as_posix()}")
                if relative.casefold() in seen_paths:
                    raise ModError(f"Duplicate destination: {relative}")
                seen_paths.add(relative.casefold())
                paths.append((relative, path, path.stat()))
    if len(paths) > 20000:
        raise ModError("A build may contain at most 20,000 files.")
    validate_upload_sizes([{"path": relative, "size": stat.st_size} for relative, _, stat in paths])
    total, done, files = sum(stat.st_size for _, _, stat in paths), 0, []

    def tick(count):
        nonlocal done
        done += count
        progress({"state": "Scanning", "done": done, "total": total})

    for relative, path, before in sorted(paths):
        sha, md5 = hashes(path, tick)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ModError(f"File changed during scanning. Scan again: {relative}")
        files.append({"path": relative, "size": after.st_size, "sha256": sha, "md5": md5})
    result = {"schema": 1, "modules": sorted(modules, key=lambda m: m["folder"]), "files": files}
    validate_manifest(result)
    return result


def validate_manifest(manifest: dict) -> None:
    try:
        if manifest["schema"] != 1 or not 1 <= len(manifest["modules"]) <= 20:
            raise ModError("Unsupported manifest or module count.")
        folders, ids, seen, total = set(), set(), set(), 0
        for module in manifest["modules"]:
            folder = safe_path(module["folder"])
            if "/" in folder or folder.casefold() in OFFICIAL:
                raise ModError("Invalid module folder.")
            if folder.casefold() in folders or module["id"].casefold() in ids:
                raise ModError("Duplicate module folder or ID.")
            if module["id"].casefold() in OFFICIAL or not module["id"]:
                raise ModError("Reserved or empty module ID.")
            folders.add(folder.casefold())
            ids.add(module["id"].casefold())
        if not 1 <= len(manifest["files"]) <= 20000:
            raise ModError("Invalid file count.")
        for entry in manifest["files"]:
            path = safe_path(entry["path"])
            if path.split("/")[0].casefold() not in folders or "/" not in path:
                raise ModError("File is outside the declared modules.")
            if path.casefold() in seen or not re.fullmatch("[0-9a-f]{64}", entry["sha256"]):
                raise ModError("Duplicate path or invalid file hash.")
            if len(base64.b64decode(entry["md5"], validate=True)) != 16:
                raise ModError("Invalid transfer checksum.")
            if type(entry["size"]) is not int or not 0 <= entry["size"] <= 64 * 1024**3:
                raise ModError("Invalid file size.")
            total += entry["size"]
            seen.add(path.casefold())
        if total > 100 * 1024**3:
            raise ModError("Build exceeds the 100 GiB limit.")
        for folder in folders:
            if f"{folder}/submodule.xml" not in seen:
                raise ModError("Every module needs its root SubModule.xml.")
        # Windows cannot represent a file and a child of that file simultaneously.
        for path in seen:
            pieces = path.split("/")
            if any("/".join(pieces[:i]) in seen for i in range(1, len(pieces))):
                raise ModError("A file conflicts with a directory in the manifest.")
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ModError("Malformed mod manifest.") from exc
