import shutil

import pytest
from test_modules import game, module

from signalpoint.api import TransferControl
from signalpoint.manager import Manager, atomic_json
from signalpoint.manifest import ModError, scan
from signalpoint.state import State


class Files:
    base = "http://127.0.0.1/signalpoint"

    def __init__(self, folders):
        self.folders = {p.name: p for p in folders}

    def prepare_downloads(self, build, entries):
        pass

    def download(self, build, entry, target, control, progress):
        control.check()
        root, relative = entry["path"].split("/", 1)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.folders[root] / relative, target)
        progress(entry["size"])


@pytest.fixture
def setup(tmp_path):
    source = module(tmp_path / "source" / "Example")
    (source / "data.txt").write_text("old")
    state = State(tmp_path / "state")
    install = game(tmp_path / "game")
    manager = Manager(state, install, Files([source]), process_check=lambda: False)
    manifest = scan([source])
    manager.install("example", "Example", "first", manifest)
    return manager, source, state


def test_failed_database_commit_restores_old_modules(setup, monkeypatch):
    manager, source, state = setup
    (source / "data.txt").write_text("replacement")
    manifest = scan([source])

    def fail(*args):
        raise OSError("simulated database failure")

    monkeypatch.setattr(state, "commit", fail)
    with pytest.raises(OSError, match="simulated"):
        manager.install("example", "Example", "second", manifest)
    assert (manager.modules / "Example/data.txt").read_text() == "old"
    assert state.installs(manager.game)["example"]["build_id"] == "first"
    manager.recover()
    assert list(manager.work.iterdir()) == []


def test_restart_recovers_interrupted_folder_swap(setup):
    manager, source, state = setup
    directory = manager.work / "interrupted"
    (directory / "old").mkdir(parents=True)
    (directory / "new").mkdir()
    atomic_json(
        directory / "journal.json",
        {"id": "interrupted", "operations": [{"folder": "Example", "had_old": True}]},
    )
    (manager.modules / "Example").rename(directory / "old" / "Example")
    module(manager.modules / "Example")
    (manager.modules / "Example/data.txt").write_text("partially installed")
    restarted = Manager(state, manager.game, manager.api, process_check=lambda: False)
    restarted.recover()
    assert (manager.modules / "Example/data.txt").read_text() == "old"
    assert not directory.exists()


def test_committed_install_survives_cleanup_failure(setup, monkeypatch):
    manager, source, state = setup
    (source / "data.txt").write_text("new")

    def fail(_):
        raise OSError("simulated cleanup failure")

    monkeypatch.setattr(manager, "_remove_work", fail)
    with pytest.raises(OSError, match="cleanup"):
        manager.install("example", "Example", "second", scan([source]))
    assert state.installs(manager.game)["example"]["build_id"] == "second"
    restarted = Manager(state, manager.game, manager.api, process_check=lambda: False)
    restarted.recover()
    assert (manager.modules / "Example/data.txt").read_text() == "new"


def test_uninstall_preserves_user_edits_and_missing_dependencies_block(setup):
    manager, source, state = setup
    (manager.modules / "Example/data.txt").write_text("keep local changes")
    preserved = manager.uninstall("example")
    assert preserved == ["Example/data.txt"]
    assert (manager.modules / "Example/data.txt").read_text() == "keep local changes"
    other = module(source.parent / "NeedsDependency", "NeedsDependency")
    manifest = scan([other])
    manifest["modules"][0]["dependencies"].append(
        {"id": "Absent", "version": "v1.0.0", "optional": False}
    )
    with pytest.raises(ModError, match="Absent"):
        manager.install("other", "Other", "other", manifest)
    assert not (manager.modules / "NeedsDependency").exists()


def test_cancelled_transfer_leaves_installed_build_unchanged(setup):
    manager, source, state = setup
    (source / "data.txt").write_text("new")
    control = TransferControl()
    control.cancel()
    with pytest.raises(ModError, match="Cancelled"):
        manager.install("example", "Example", "second", scan([source]), control=control)
    assert (manager.modules / "Example/data.txt").read_text() == "old"
    assert state.installs(manager.game)["example"]["build_id"] == "first"


def test_source_api_change_cannot_take_over_existing_install(setup):
    manager, source, state = setup
    manager.api.base = "https://other.example/signalpoint"
    with pytest.raises(ModError, match="another backend"):
        manager.install("example", "Example", "second", scan([source]))
