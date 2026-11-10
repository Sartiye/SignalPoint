"""Exercise an installer in a disposable app directory, including an in-place upgrade.

Refuses to run if SignalPoint already has an installed-program registration.
No shortcuts or game launches are created by this check.
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Sartiye.SignalPoint_is1"


def checksum(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def registration():
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            UNINSTALL_KEY,
            0,
            winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
        ) as key:
            return {
                name: winreg.QueryValueEx(key, name)[0]
                for name in ("InstallLocation", "DisplayVersion")
            }
    except FileNotFoundError:
        return None


def run(args, *, env=None):
    subprocess.run(
        args,
        env=env,
        check=True,
        timeout=120,
        capture_output=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def registration_names():
    import winreg

    names = set()
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Uninstall",
        0,
        winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
    ) as key:
        for index in range(winreg.QueryInfoKey(key)[0]):
            name = winreg.EnumKey(key, index).casefold()
            if name.startswith("sartiye.signalpoint"):
                names.add(name)
    return names


def load_installer(path):
    installer = path.resolve(strict=True)
    if not any(
        installer.is_relative_to(ROOT / parent) for parent in ("dist/installer", ".scratch")
    ):
        raise SystemExit("Only installers built inside this checkout can be tested.")
    manifest = json.loads(installer.with_suffix(".build.json").read_text(encoding="utf-8"))
    if checksum(installer) != manifest["installer_sha256"]:
        raise SystemExit("Installer checksum differs from the recorded build.")
    return installer, manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("installer", type=Path)
    parser.add_argument(
        "--upgrade-installer", type=Path, help="A newer installer to run over the first"
    )
    args = parser.parse_args()
    if sys.platform != "win32":
        raise SystemExit("Run installer verification on Windows.")
    if registration() is not None:
        raise SystemExit(
            "SignalPoint is already installed. Use a separate Windows test account or Sandbox."
        )
    installer, manifest = load_installer(args.installer)
    upgrade, upgrade_manifest = load_installer(args.upgrade_installer or args.installer)
    directory = (ROOT / ".scratch" / ("installer-check-" + uuid.uuid4().hex[:12])).resolve()
    application = directory / "app"
    if not application.is_relative_to((ROOT / ".scratch").resolve()):
        raise SystemExit("Installer test directory escapes the workspace.")
    directory.mkdir(parents=True)
    uninstaller = application / "unins000.exe"
    common = [
        "/VERYSILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        "/NOCLOSEAPPLICATIONS",
        "/NORESTARTAPPLICATIONS",
        "/NOICONS",
        '/TASKS=""',
    ]
    environment = os.environ.copy()
    for key in ("PYTHONPATH", "PYTHONHOME", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH"):
        environment.pop(key, None)
    environment["QT_QPA_PLATFORM"] = "offscreen"
    try:
        run([str(installer), *common, f"/DIR={application}", f"/LOG={directory / 'install.log'}"])
        record = registration()
        assert record and Path(record["InstallLocation"]).resolve() == application
        assert record["DisplayVersion"] == manifest["app_version"]
        for name, expected in manifest["files"].items():
            assert checksum(application / name) == expected, name
        run([str(application / "SignalPoint.exe"), "--smoke-test"], env=environment)
        print("Installed file hashes and packaged startup passed.", flush=True)

        note = application / "user-note.txt"
        note.write_text("User-created files are not installer payload.", encoding="utf-8")
        (application / "SignalPoint.exe").write_bytes(
            b"Replacement check: upgrade must restore this file."
        )
        # No /DIR override: the newer installer must rediscover the original folder.
        run([str(upgrade), *common, f"/LOG={directory / 'upgrade.log'}"])
        record = registration()
        assert record and Path(record["InstallLocation"]).resolve() == application
        assert record["DisplayVersion"] == upgrade_manifest["app_version"]
        assert registration_names() == {"sartiye.signalpoint_is1"}
        for name, expected in upgrade_manifest["files"].items():
            assert checksum(application / name) == expected, name
        assert len(list(application.glob("unins*.exe"))) == 1
        assert note.exists()
        run([str(application / "SignalPoint.exe"), "--smoke-test"], env=environment)
        print(
            f"Upgrade {manifest['app_version']} -> {upgrade_manifest['app_version']} "
            "reused the installation and preserved the user file.",
            flush=True,
        )
    finally:
        if uninstaller.exists():
            # This exact target is inside the checked disposable workspace directory.
            run(
                [
                    str(uninstaller),
                    "/VERYSILENT",
                    "/SUPPRESSMSGBOXES",
                    "/NORESTART",
                    f"/LOG={directory / 'uninstall.log'}",
                ]
            )
            deadline = time.monotonic() + 20
            while registration() is not None and time.monotonic() < deadline:
                time.sleep(0.1)
    assert registration() is None
    assert not (application / "SignalPoint.exe").exists()
    assert not (application / "_internal/python313.dll").exists()
    for name in manifest["files"].keys() | upgrade_manifest["files"].keys():
        assert not (application / name).exists(), name
    assert (application / "user-note.txt").exists()
    result = {
        "installed_files_verified": len(manifest["files"]),
        "upgraded_files_verified": len(upgrade_manifest["files"]),
        "upgrade_verified": True,
        "uninstall_verified": True,
        "unknown_user_file_preserved": True,
        "installer_sha256": manifest["installer_sha256"],
        "initial_installer_version": manifest["app_version"],
        "upgrade_installer_version": upgrade_manifest["app_version"],
        "install_directory_reused": True,
        "single_uninstaller": True,
        "single_program_entry": True,
    }
    (directory / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"Install / upgrade / uninstall passed. Evidence: {directory / 'result.json'}")


if __name__ == "__main__":
    main()
