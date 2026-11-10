import os
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from test_modules import game as make_game

from signalpoint import game, stores
from signalpoint.api import Cancelled, TransferControl
from signalpoint.manifest import ModError


@pytest.mark.parametrize(
    "marker,expected",
    [("steam.target", stores.STEAM), ("epic.target", stores.EPIC), ("gog.target", None)],
)
def test_native_target_overrides_store_hint(tmp_path, marker, expected):
    root = make_game(tmp_path / "Game")
    (root / "Modules/Native" / marker).touch()
    assert stores.identify(root, stores.XBOX) == expected


def test_xbox_layout_and_detection_hint(tmp_path):
    root = make_game(tmp_path / "Game", "Gaming.Desktop.x64_Shipping_Client")
    assert stores.identify(root) == stores.XBOX
    other = make_game(tmp_path / "Other")
    assert stores.identify(other, stores.EPIC) == stores.EPIC
    assert stores.identify(other) is None


@pytest.mark.parametrize("store", [stores.EPIC, stores.XBOX])
def test_closed_store_opens_before_bannerlord(tmp_path, monkeypatch, store):
    root = make_game(tmp_path / "Game")
    events = []
    states = iter([False, False, True])
    monkeypatch.setattr(game, "running", lambda: False)
    monkeypatch.setattr(stores, "running", lambda selected: next(states))
    monkeypatch.setattr(stores, "open_client", lambda selected: events.append(("client", selected)))
    monkeypatch.setattr(stores.time, "sleep", lambda _: None)
    monkeypatch.setattr(
        game.subprocess, "Popen", lambda args, **kwargs: events.append(("game", args, kwargs))
    )
    progress = []
    game.start_launcher(root, store_hint=store, progress=progress.append)
    assert events[0] == ("client", store)
    assert events[1][0] == "game"
    assert events[1][2]["cwd"] == game.find_launcher(root).parent
    assert any("Waiting for" in event["state"] for event in progress)


def test_open_client_is_reused_without_reopening(tmp_path, monkeypatch):
    root = make_game(tmp_path / "Game")
    events = []
    monkeypatch.setattr(game, "running", lambda: False)
    monkeypatch.setattr(stores, "running", lambda _: True)
    monkeypatch.setattr(stores, "open_client", lambda _: pytest.fail("Client should not reopen"))
    monkeypatch.setattr(game.subprocess, "Popen", lambda *a, **kw: events.append("game"))
    game.start_launcher(root, store_hint=stores.EPIC)
    assert events == ["game"]


def test_store_timeout_blocks_game_launch(tmp_path, monkeypatch):
    root = make_game(tmp_path / "Game")
    monkeypatch.setattr(game, "running", lambda: False)
    monkeypatch.setattr(stores, "running", lambda _: False)
    monkeypatch.setattr(stores, "open_client", lambda _: None)
    original = stores.ensure_open
    monkeypatch.setattr(stores, "ensure_open", lambda store, **kw: original(store, timeout=0, **kw))
    monkeypatch.setattr(
        game.subprocess, "Popen", lambda *a, **kw: pytest.fail("Game must not start")
    )
    with pytest.raises(ModError, match="did not open"):
        game.start_launcher(root, store_hint=stores.EPIC)


def test_cancelling_client_wait_blocks_game_launch(tmp_path, monkeypatch):
    root = make_game(tmp_path / "Game")
    control = TransferControl()
    monkeypatch.setattr(game, "running", lambda: False)
    monkeypatch.setattr(stores, "running", lambda _: False)
    monkeypatch.setattr(stores, "open_client", lambda _: control.cancel())
    monkeypatch.setattr(
        game.subprocess, "Popen", lambda *a, **kw: pytest.fail("Game must not start")
    )
    with pytest.raises(Cancelled):
        game.start_launcher(root, store_hint=stores.EPIC, control=control)


def test_client_start_failure_blocks_game_launch(tmp_path, monkeypatch):
    root = make_game(tmp_path / "Game")
    monkeypatch.setattr(game, "running", lambda: False)
    monkeypatch.setattr(stores, "running", lambda _: False)

    def fail(_):
        raise OSError("Client activation failed")

    monkeypatch.setattr(stores, "open_client", fail)
    monkeypatch.setattr(
        game.subprocess, "Popen", lambda *a, **kw: pytest.fail("Game must not start")
    )
    with pytest.raises(ModError, match="Could not open"):
        game.start_launcher(root, store_hint=stores.EPIC)


@pytest.mark.parametrize("steam_open", [False, True])
def test_steam_launches_via_game_link_without_a_separate_launcher(
    tmp_path, monkeypatch, steam_open
):
    root = make_game(tmp_path / "Game")
    (root / "Modules/Native/steam.target").touch()
    events = []
    monkeypatch.setattr(game, "running", lambda: False)
    monkeypatch.setattr(stores, "running", lambda _: steam_open)
    monkeypatch.setattr(
        stores, "ensure_open", lambda *a, **kw: pytest.fail("Do not open the Steam library first")
    )
    monkeypatch.setattr(stores, "open_client", lambda store, **kw: events.append((store, kw)))
    monkeypatch.setattr(
        game.subprocess, "Popen", lambda *a, **kw: pytest.fail("Steam must start the launcher")
    )
    progress = []
    game.start_launcher(root, store_hint=stores.EPIC, progress=progress.append)
    assert events == [(stores.STEAM, {"game_id": "261550"})]
    assert progress[-1]["state"] == "Starting Bannerlord through Steam…"


def test_cancelled_steam_launch_does_not_activate_game_link(tmp_path, monkeypatch):
    root = make_game(tmp_path / "Game")
    control = TransferControl()
    control.cancel()
    monkeypatch.setattr(game, "running", lambda: False)
    monkeypatch.setattr(
        stores,
        "open_client",
        lambda *a, **kw: pytest.fail("Cancelled launch must not activate Steam"),
    )
    with pytest.raises(Cancelled):
        game.start_launcher(root, store_hint=stores.STEAM, control=control)


def test_steam_activation_failure_does_not_fall_back_to_the_executable(tmp_path, monkeypatch):
    root = make_game(tmp_path / "Game")
    monkeypatch.setattr(game, "running", lambda: False)

    def fail(*args, **kwargs):
        raise OSError("Activation failed")

    monkeypatch.setattr(stores, "open_client", fail)
    monkeypatch.setattr(
        game.subprocess,
        "Popen",
        lambda *a, **kw: pytest.fail("Do not launch the executable after Steam activation fails"),
    )
    with pytest.raises(ModError, match="through Steam"):
        game.start_launcher(root, store_hint=stores.STEAM)


@pytest.mark.skipif(os.name != "nt", reason="Windows protocol handler")
def test_registered_steam_protocol_receives_the_bannerlord_game_url(monkeypatch):
    import winreg

    calls = []
    monkeypatch.setattr(winreg, "OpenKey", lambda *args: nullcontext(object()))
    monkeypatch.setattr(winreg, "QueryValueEx", lambda *args: ("", winreg.REG_SZ))
    monkeypatch.setattr(stores.subprocess, "Popen", lambda args, **kw: calls.append((args, kw)))
    stores.open_client(stores.STEAM, game_id=game.APP_ID)
    assert len(calls) == 1
    assert calls[0][0][-1] == "steam://run/261550"
    assert calls[0][0][0].lower().endswith("explorer.exe")


def test_only_actual_client_for_current_user_counts(monkeypatch):
    processes = [
        SimpleNamespace(info={"name": "SteamService.exe", "username": "PC\\me"}),
        SimpleNamespace(info={"name": "steam.exe", "username": "PC\\other"}),
    ]
    monkeypatch.setattr(
        stores.psutil, "Process", lambda: SimpleNamespace(username=lambda: "PC\\me")
    )
    monkeypatch.setattr(stores.psutil, "process_iter", lambda fields: processes)
    assert not stores.running(stores.STEAM)
    processes.append(SimpleNamespace(info={"name": "Steam.exe", "username": "PC\\ME"}))
    assert stores.running(stores.STEAM)


def test_xbox_app_uses_installed_aumid(monkeypatch):
    identifier = "Microsoft.GamingApp_8wekyb3d8bbwe!Microsoft.Xbox.App"
    process = SimpleNamespace(
        returncode=0, communicate=lambda timeout: (('["' + identifier + '"]').encode(), b"")
    )
    calls = []

    def spawn(args, **kwargs):
        calls.append((args, kwargs))
        return process

    monkeypatch.setattr(stores.subprocess, "Popen", spawn)
    assert stores.xbox_application() == "shell:AppsFolder\\" + identifier
    assert "Get-StartApps" in calls[0][0][-1]


def test_unsafe_xbox_application_id_is_rejected(monkeypatch):
    process = SimpleNamespace(returncode=0, communicate=lambda timeout: (b'["evil:command"]', b""))
    monkeypatch.setattr(stores.subprocess, "Popen", lambda *a, **kw: process)
    with pytest.raises(ModError, match="not found"):
        stores.xbox_application()
