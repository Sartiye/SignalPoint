"""Package the checked Windows dist folder with Inno Setup 7."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tomllib
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def checksum(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def compiler_path(override=None):
    candidates = [override, os.environ.get("SIGNALPOINT_ISCC"), shutil.which("ISCC.exe")]
    for root in (os.environ.get("LOCALAPPDATA"), os.environ.get("ProgramFiles")):
        if root:
            base = Path(root) / "Programs" if root == os.environ.get("LOCALAPPDATA") else Path(root)
            candidates.append(base / "Inno Setup 7" / "ISCC.exe")
    for value in candidates:
        if value and Path(value).is_file():
            return Path(value).resolve()
    raise SystemExit(
        "Install Inno Setup 7 (winget install --id JRSoftware.InnoSetup.7 -e -s winget "
        "--scope user), or pass --iscc PATH."
    )


def artwork(bundle, directory):
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QColor, QImage, QPainter

    directory.mkdir(parents=True, exist_ok=True)
    source = QImage(str(bundle / "_internal/signalpoint/assets/signalpoint-full.png"))
    if source.isNull():
        raise SystemExit("The bundle is missing its SignalPoint artwork. Rebuild the app.")
    for name, width, height, area in (
        ("wizard.bmp", 164, 314, QRect(18, 84, 128, 128)),
        ("wizard-small.bmp", 55, 55, QRect(3, 3, 49, 49)),
    ):
        image = QImage(width, height, QImage.Format.Format_RGB32)
        image.fill(QColor("#111123"))
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawImage(area, source)
        painter.end()
        if not image.save(str(directory / name), "BMP"):
            raise SystemExit("Could not prepare installer artwork.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iscc", type=Path)
    parser.add_argument("--build-app", action="store_true")
    args = parser.parse_args()
    if sys.platform != "win32":
        raise SystemExit("Build the Windows installer on Windows.")
    compiler = compiler_path(args.iscc)
    if args.build_app:
        subprocess.run([sys.executable, str(ROOT / "scripts/build.py")], cwd=ROOT, check=True)
    bundle = ROOT / "dist/SignalPoint"
    required = (
        "SignalPoint.exe",
        "SignalPoint.exe.sha256",
        "icon.ico",
        "build-info.json",
        "_internal/python313.dll",
        "_internal/PySide6/Qt6Core.dll",
        "licenses/Python.txt",
    )
    if any(not (bundle / name).is_file() for name in required):
        raise SystemExit("Build the desktop bundle first: scripts/build.ps1")
    executable_hash = checksum(bundle / "SignalPoint.exe")
    if executable_hash != (bundle / "SignalPoint.exe.sha256").read_text().split()[0]:
        raise SystemExit("The bundled EXE changed after its build. Rebuild the desktop bundle.")
    files = {}
    for path in sorted(bundle.rglob("*")):
        if path.is_symlink() or path.is_junction():
            raise SystemExit(
                "The distribution contains a linked path; rebuild it before packaging."
            )
        if path.is_file():
            files[path.relative_to(bundle).as_posix()] = checksum(path)
    environment = os.environ.copy()
    for key in ("PYTHONPATH", "PYTHONHOME", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH"):
        environment.pop(key, None)
    subprocess.run(
        [str(bundle / "SignalPoint.exe"), "--smoke-test"],
        env={**environment, "QT_QPA_PLATFORM": "offscreen"},
        capture_output=True,
        check=True,
        timeout=30,
    )
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]
    images = ROOT / "build/installer-artwork"
    artwork(bundle, images)
    output = ROOT / "dist/installer"
    output.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            str(compiler),
            "/Qp",
            f"/DBundleDir={bundle}",
            f"/DAppVersion={version}",
            f"/DArtworkDir={images}",
            f"/O{output}",
            str(ROOT / "installer/SignalPoint.iss"),
        ],
        cwd=ROOT,
        env=environment,
        check=True,
    )
    installer = output / f"SignalPoint-Setup-{version}-x64.exe"
    digest = checksum(installer)
    installer.with_suffix(".exe.sha256").write_text(
        digest + "  " + installer.name + "\n", encoding="ascii"
    )
    installer.with_suffix(".build.json").write_text(
        json.dumps(
            {
                "built_at_utc": datetime.now(UTC).isoformat(),
                "installer_sha256": digest,
                "app_version": version,
                "application_sha256": executable_hash,
                "application_build": json.loads((bundle / "build-info.json").read_text()),
                "files": files,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Built {installer}\nSHA256 {digest}")


if __name__ == "__main__":
    main()
