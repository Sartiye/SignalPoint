import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import psutil

from signalpoint import stores
from signalpoint.manifest import ModError, is_link, read_module
from signalpoint.runtime import external_environment

APP_ID = "261550"
GAME_NAME = "Mount & Blade II Bannerlord"
LAUNCHER = Path("bin/Win64_Shipping_Client/TaleWorlds.MountAndBlade.Launcher.exe")
PROCESS_NAMES = {
    "bannerlord.exe",
    "bannerlord.native.exe",
    "launcher.native.exe",
    "taleworlds.mountandblade.launcher.exe",
}


@dataclass(frozen=True)
class Installation:
    path: Path
    store: str


def find_launcher(path: Path) -> Path:
    binary = path / "bin"
    directories = [binary]
    if binary.is_dir():
        directories += sorted(p for p in binary.iterdir() if p.is_dir() and not is_link(p))
    for name in ("TaleWorlds.MountAndBlade.Launcher.exe", "Launcher.Native.exe", "launcher.exe"):
        for directory in directories:
            candidate = directory / name
            if (
                candidate.is_file()
                and not is_link(candidate)
                and (candidate.parent / ".." / "..").resolve() == path.resolve()
            ):
                return candidate
    raise ModError("No Bannerlord launcher was found in this installation's bin folders.")


def validate_game(path: Path, *, require_launcher=True) -> Path:
    path = path.absolute()
    if path.is_file() and path.name.casefold() in PROCESS_NAMES | {"launcher.exe"}:
        path = (path.parent / ".." / "..").resolve()
    if any(is_link(p) for p in (path, *path.parents)):
        raise ModError("Select a real Bannerlord installation, without linked parent directories.")
    if not (path / "Modules" / "Native" / "SubModule.xml").is_file():
        raise ModError("Select Bannerlord's main folder containing Modules/Native/SubModule.xml.")
    if require_launcher:
        find_launcher(path)
    if is_link(path / "Modules"):
        raise ModError("A linked Modules folder is not supported.")
    return path


def detect_steam() -> list[Installation]:
    roots = []
    if os.name == "nt":
        import winreg

        for key, field in (
            (r"Software\Valve\Steam", "SteamPath"),
            (
                r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Steam App 261550",
                "InstallLocation",
            ),
        ):
            for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                try:
                    with winreg.OpenKey(hive, key) as handle:
                        value = Path(winreg.QueryValueEx(handle, field)[0])
                        if field == "InstallLocation":
                            roots.append(value.parent.parent.parent)
                        else:
                            roots.append(value)
                except OSError:
                    pass
    for env in ("ProgramFiles(x86)", "ProgramFiles"):
        if os.environ.get(env):
            roots.append(Path(os.environ[env]) / "Steam")
    libraries = set(roots)
    for root in roots:
        file = root / "steamapps" / "libraryfolders.vdf"
        if file.is_file():
            text = file.read_text(encoding="utf-8", errors="replace")
            for path in re.findall(r'"path"\s*"((?:\\.|[^"\\])*)"', text):
                libraries.add(Path(path.replace("\\\\", "\\")))
    found = set()
    for library in libraries:
        steamapps = library / "steamapps"
        manifest = steamapps / f"appmanifest_{APP_ID}.acf"
        directory = GAME_NAME
        if manifest.is_file():
            match = re.search(
                r'"installdir"\s*"([^"/\\]+)"',
                manifest.read_text(encoding="utf-8", errors="replace"),
            )
            if match:
                directory = match[1]
        candidate = steamapps / "common" / directory
        try:
            found.add(validate_game(candidate))
        except ModError:
            pass
    return [Installation(path, "Steam") for path in sorted(found)]


def detect_epic(data: Path | None = None) -> list[Installation]:
    data = data or Path(os.environ.get("ProgramData", "C:/ProgramData")) / "Epic"
    manifests = data / "EpicGamesLauncher" / "Data" / "Manifests"
    candidates = []
    try:
        for file in manifests.glob("*.item"):
            try:
                value = json.loads(file.read_text(encoding="utf-8-sig"))
                if value.get("InstallLocation"):
                    candidates.append(Path(value["InstallLocation"]))
            except (OSError, ValueError, TypeError):
                continue
        summary = data / "UnrealEngineLauncher" / "LauncherInstalled.dat"
        if summary.is_file():
            value = json.loads(summary.read_text(encoding="utf-8-sig"))
            candidates += [
                Path(row["InstallLocation"])
                for row in value.get("InstallationList", [])
                if row.get("InstallLocation")
            ]
    except (OSError, ValueError, TypeError):
        pass
    return _validated(candidates, "Epic Games")


def _validated(candidates, store):
    result = []
    for path in candidates:
        for root in (path, path / "Content"):
            try:
                result.append(Installation(validate_game(root), store))
                break
            except (ModError, OSError):
                continue
    return result


def xbox_roots(drives):
    roots = set()
    for drive in drives:
        roots.add(drive / "XboxGames")
        marker = drive / ".GamingRoot"
        try:
            if marker.is_file() and marker.stat().st_size <= 4096:
                name = marker.read_bytes()[8:].decode("utf-16-le").rstrip("\0").lstrip("\\/")
                if name and ":" not in name and ".." not in Path(name).parts:
                    roots.add(drive / name)
        except (OSError, UnicodeError):
            pass
    return roots


def detect_xbox(*, roots=None, packages=None) -> list[Installation]:
    if roots is None:
        drives = [
            Path(p.mountpoint)
            for p in psutil.disk_partitions()
            if p.fstype and "cdrom" not in p.opts
        ]
        roots = xbox_roots(drives)
    candidates = list(packages or [])
    for root in roots:
        try:
            candidates += [
                p
                for p in root.iterdir()
                if p.is_dir()
                and any(n in p.name.casefold() for n in ("bannerlord", "mount", "blade"))
            ]
        except OSError:
            continue
    if packages is None and os.name == "nt":
        executable = Path(os.environ.get("SystemRoot", "C:/Windows")) / (
            "System32/WindowsPowerShell/v1.0/powershell.exe"
        )
        command = (
            "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new(); "
            "@(Get-AppxPackage | Where-Object { $_.Name -match "
            "'TaleWorlds|Bannerlord|MountAndBlade' } | Select-Object "
            "-ExpandProperty InstallLocation) | ConvertTo-Json -Compress"
        )
        try:
            result = subprocess.run(
                [str(executable), "-NoProfile", "-NonInteractive", "-Command", command],
                capture_output=True,
                timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if result.returncode == 0 and result.stdout.strip():
                locations = json.loads(result.stdout.decode("utf-8-sig"))
                if isinstance(locations, str):
                    locations = [locations]
                candidates += [Path(p) for p in locations or [] if p]
        except (OSError, subprocess.TimeoutExpired, ValueError):
            pass
    return _validated(candidates, "Xbox / Microsoft Store")


def detect_games() -> list[Installation]:
    result = {}
    for install in [*detect_steam(), *detect_epic(), *detect_xbox()]:
        result.setdefault(str(install.path).casefold(), install)
    return list(result.values())


def running() -> bool:
    for process in psutil.process_iter(["name"]):
        try:
            if (process.info["name"] or "").casefold() in PROCESS_NAMES:
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return False


def installed_modules(game: Path) -> list[dict]:
    modules = []
    for folder in (game / "Modules").iterdir():
        if not folder.is_dir():
            continue
        try:
            modules.append(read_module(folder, allow_official=True))
        except (ModError, OSError):
            continue
    return modules


def start_launcher(game: Path, *, store_hint=None, progress=lambda _: None, control=None):
    game = validate_game(game)
    if running():
        raise ModError("Bannerlord or its launcher is already running.")
    store = stores.identify(game, store_hint)
    if store == stores.STEAM:
        if control:
            control.check()
        progress({"state": "Starting Bannerlord through Steam…"})
        try:
            stores.open_client(store, game_id=APP_ID)
        except OSError as exc:
            raise ModError(
                "Could not launch Bannerlord through Steam. Open Steam and retry."
            ) from exc
        return
    launcher = find_launcher(game)
    stores.ensure_open(store, progress=progress, control=control)
    if control:
        control.check()
    if running():
        raise ModError("Bannerlord or its launcher is already running.")
    progress({"state": "Starting Bannerlord launcher…"})
    with external_environment() as environment:
        subprocess.Popen([str(launcher)], cwd=launcher.parent, env=environment)
