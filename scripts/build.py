"""Build a self-contained Windows desktop folder in an isolated DLL environment."""

import ctypes
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = (
    "PySide6-Essentials",
    "shiboken6",
    "httpx",
    "httpcore",
    "certifi",
    "anyio",
    "idna",
    "h11",
    "typing_extensions",
    "psutil",
    "filelock",
    "defusedxml",
)


def refresh_explorer_icon(executable: Path):
    """Tell Explorer that a rebuilt executable's cached icon may have changed."""
    notify = ctypes.WinDLL("shell32").SHChangeNotify
    notify.argtypes = [ctypes.c_long, ctypes.c_uint, ctypes.c_wchar_p, ctypes.c_void_p]
    notify.restype = None
    # SHCNE_UPDATEITEM, SHCNF_PATHW | SHCNF_FLUSH: notify only this executable.
    notify(0x00002000, 0x00001005, str(executable.resolve()), None)


def main():
    if sys.platform != "win32":
        raise SystemExit("Build the Windows application using Windows Python.")
    output = ROOT / "dist" / "SignalPoint"
    if not output.resolve().is_relative_to(ROOT):
        raise SystemExit("Build output escapes the checkout.")
    windows = Path(os.environ["SystemRoot"])
    environment = os.environ.copy()
    environment["PATH"] = os.pathsep.join(
        str(p)
        for p in (
            Path(sys.executable).parent,
            Path(sys.base_prefix),
            Path(sys.base_prefix) / "DLLs",
            windows / "System32",
            windows,
        )
    )
    for key in ("PYTHONPATH", "PYTHONHOME", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH"):
        environment.pop(key, None)
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",
        "--onedir",
        "--name",
        "SignalPoint",
        "--icon",
        str(ROOT / "src" / "signalpoint" / "assets" / "signalpoint.ico"),
        "--add-data",
        str(ROOT / "src" / "signalpoint" / "assets") + os.pathsep + "signalpoint/assets",
        "--paths",
        str(ROOT / "src"),
        "--specpath",
        str(ROOT / "build"),
        "--workpath",
        str(ROOT / "build"),
        "--distpath",
        str(ROOT / "dist"),
        "--exclude-module",
        "PySide6.QtWebEngineCore",
        "--exclude-module",
        "PySide6.QtQml",
        "--exclude-module",
        "PySide6.QtQuick",
    ]
    for package in PACKAGES:
        command.extend(["--copy-metadata", package])
    command.append(str(ROOT / "src" / "signalpoint" / "desktop.py"))
    subprocess.run(command, cwd=ROOT, env=environment, check=True)
    # Qt's Windows ICU imports must resolve against Windows, not an unrelated
    # Poppler/SDK ICU found on the developer PATH. Same guard as Sartiye.Albion.
    for name in ("icuuc.dll", "icuin.dll", "icu.dll"):
        collected = output / "_internal" / name
        if collected.is_file() and (windows / "System32" / name).is_file():
            collected.unlink()
    dependencies = {name: metadata.version(name) for name in PACKAGES}
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True))
    (output / "build-info.json").write_text(
        json.dumps(
            {
                "source_commit": commit,
                "source_dirty": dirty,
                "built_at_utc": datetime.now(UTC).isoformat(),
                "dependencies": dependencies,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    licenses = output / "licenses"
    for name in PACKAGES:
        distribution = metadata.distribution(name)
        for item in distribution.files or []:
            if "licenses" not in item.parts and not item.name.lower().startswith(
                ("license", "copying")
            ):
                continue
            source = Path(distribution.locate_file(item))
            if source.is_file() and not item.is_absolute():
                relative = Path(*(p for p in item.parts if p not in {"..", "."}))
                target = licenses / name / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if python_license.is_file():
        licenses.mkdir(parents=True, exist_ok=True)
        shutil.copy2(python_license, licenses / "Python.txt")
    shutil.copy2(ROOT / "README.md", output / "README.txt")
    shutil.copy2(ROOT / "src" / "signalpoint" / "assets" / "signalpoint.ico", output / "icon.ico")
    subprocess.run(
        [str(output / "SignalPoint.exe"), "--smoke-test"],
        env={**environment, "QT_QPA_PLATFORM": "offscreen"},
        capture_output=True,
        check=True,
        timeout=30,
    )
    with (output / "SignalPoint.exe").open("rb") as source:
        checksum = hashlib.file_digest(source, "sha256").hexdigest()
    (output / "SignalPoint.exe.sha256").write_text(
        checksum + "  SignalPoint.exe\n", encoding="ascii"
    )
    refresh_explorer_icon(output / "SignalPoint.exe")
    print(f"Built {output / 'SignalPoint.exe'}\nSHA256 {checksum}")


if __name__ == "__main__":
    main()
