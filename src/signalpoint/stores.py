"""Activate Steam's game link or open another installation's store client."""

import json
import os
import re
import subprocess
import time
from pathlib import Path

import psutil

from signalpoint.manifest import ModError
from signalpoint.runtime import external_environment

STEAM = "Steam"
EPIC = "Epic Games"
XBOX = "Xbox / Microsoft Store"
CLIENTS = {STEAM: {"steam.exe"}, EPIC: {"epicgameslauncher.exe"}, XBOX: {"xboxpcapp.exe"}}
PROTOCOLS = {
    STEAM: ("steam", "steam://open/main"),
    EPIC: ("com.epicgames.launcher", "com.epicgames.launcher://store/library"),
}


def identify(game: Path, hint=None):
    native = game / "Modules" / "Native"
    # These target files are what Bannerlord's own LauncherPlatform reads.
    for filename, store in (("steam.target", STEAM), ("epic.target", EPIC), ("gog.target", None)):
        if (native / filename).is_file():
            return store
    if (game / "MicrosoftGame.config").is_file() or (
        game / "bin" / "Gaming.Desktop.x64_Shipping_Client"
    ).is_dir():
        return XBOX
    return hint if hint in CLIENTS else None


def running(store):
    current_user = psutil.Process().username().casefold()
    for process in psutil.process_iter(["name", "username"]):
        try:
            if (process.info["name"] or "").casefold() in CLIENTS[store] and (
                process.info["username"] or ""
            ).casefold() == current_user:
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return False


def xbox_application():
    """Resolve the installed Xbox app's AUMID instead of guessing a versioned exe path."""
    powershell = Path(os.environ.get("SystemRoot", "C:/Windows")) / (
        "System32/WindowsPowerShell/v1.0/powershell.exe"
    )
    command = (
        "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new(); "
        "@(Get-StartApps | Where-Object { $_.AppID -like 'Microsoft.GamingApp_*!*' } "
        "| Select-Object -ExpandProperty AppID) | ConvertTo-Json -Compress"
    )
    with external_environment() as environment:
        process = subprocess.Popen(
            [str(powershell), "-NoProfile", "-NonInteractive", "-Command", command],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    try:
        output, _ = process.communicate(timeout=10)
    except subprocess.TimeoutExpired as exc:
        process.kill()
        process.communicate()
        raise ModError("Could not locate the Xbox app. Open it manually and retry.") from exc
    try:
        values = json.loads(output.decode("utf-8-sig")) if output.strip() else []
    except (ValueError, UnicodeError) as exc:
        raise ModError("Could not locate the Xbox app. Open it manually and retry.") from exc
    values = [values] if isinstance(values, str) else values
    valid = [
        value
        for value in values or []
        if isinstance(value, str)
        and re.fullmatch(r"Microsoft\.GamingApp_[A-Za-z0-9_.-]+![A-Za-z0-9_.-]+", value)
    ]
    if process.returncode or len(valid) != 1:
        raise ModError("Xbox app not found. Install/open the Xbox PC app and retry.")
    return "shell:AppsFolder\\" + valid[0]


def open_client(store, *, game_id=None):
    if os.name != "nt":
        raise ModError("Automatic store-client startup is supported on Windows.")
    if store == XBOX:
        target = xbox_application()
    else:
        import winreg

        protocol, target = PROTOCOLS[store]
        if store == STEAM and game_id is not None:
            target = f"steam://run/{game_id}"
        try:
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, protocol) as key:
                winreg.QueryValueEx(key, "URL Protocol")
        except OSError as exc:
            raise ModError(
                f"{store}'s launch handler is missing. Open the client manually and retry."
            ) from exc
    explorer = Path(os.environ.get("SystemRoot", "C:/Windows")) / "explorer.exe"
    with external_environment() as environment:
        subprocess.Popen([str(explorer), target], env=environment)


def ensure_open(store, *, progress=lambda _: None, control=None, timeout=45):
    if store is None:
        return
    if control:
        control.check()
    progress({"state": f"Checking {store}…"})
    if running(store):
        return
    progress({"state": f"Starting {store}…"})
    deadline = time.monotonic() + timeout
    try:
        open_client(store)
    except OSError as exc:
        raise ModError(f"Could not open {store}. Open it manually and retry.") from exc
    progress({"state": f"Waiting for {store} to open…"})
    while True:
        if control:
            control.check()
        if running(store):
            return
        if time.monotonic() >= deadline:
            raise ModError(f"{store} did not open. Finish any client update/sign-in and retry.")
        time.sleep(0.25)
