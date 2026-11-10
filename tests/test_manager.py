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
        self.downloaded = []

    def prepare_downloads(self, build, entries):
        pass

    def download(self, build, entry, target, control, progress):
        control.check()
        self.downloaded.append(entry["path"])
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


def test_existing_module_is_reused_without_downloads_and_becomes_managed(tmp_path):
    source = module(tmp_path / "source" / "Example")
    (source / "asset.tpac").write_bytes(b"already installed")
    install = game(tmp_path / "game")
    local = install / "Modules/Example"
    shutil.copytree(source, local)
    (local / "personal.txt").write_text("keep this")
    state = State(tmp_path / "state")
    api = Files([source])
    manager = Manager(state, install, api, process_check=lambda: False)
    manifest = scan([source])
    manager.install("example", "Example", "first", manifest)
    assert api.downloaded == []
    assert state.installs(install)["example"]["manifest"] == manifest
    assert (local / "personal.txt").read_text() == "keep this"
    manager.uninstall("example")
    assert not (local / "SubModule.xml").exists()
    assert (local / "personal.txt").read_text() == "keep this"


@pytest.mark.parametrize("fail_commit", [False, True])
def test_existing_bundle_downloads_only_changes_and_can_roll_back(
    tmp_path, monkeypatch, fail_commit
):
    source = module(tmp_path / "source/Example", version="v1.1.0")
    (source / "changed.tpac").write_bytes(b"new content")
    (source / "same.tpac").write_bytes(b"matching asset")
    (source / "missing.txt").write_text("new file")
    companion = module(tmp_path / "source/Companion", "Companion")
    (companion / "asset.tpac").write_bytes(b"complete companion")
    install = game(tmp_path / "game")
    local = module(install / "Modules/Example", version="v1.0.0")
    (local / "changed.tpac").write_bytes(b"old content")
    (local / "same.tpac").write_bytes(b"matching asset")
    (local / "personal.txt").write_text("keep settings")
    shutil.copytree(companion, install / "Modules/Companion")
    state = State(tmp_path / "state")
    api = Files([source, companion])
    manager = Manager(state, install, api, process_check=lambda: False)

    def broken_commit(*_):
        raise OSError("simulated adoption commit failure")

    if fail_commit:
        monkeypatch.setattr(state, "commit", broken_commit)
        with pytest.raises(OSError, match="adoption commit failure"):
            manager.install("example", "Example", "first", scan([source, companion]))
        manager.recover()
        assert "v1.0.0" in (local / "SubModule.xml").read_text()
        assert (local / "changed.tpac").read_bytes() == b"old content"
        assert not (local / "missing.txt").exists()
        assert state.installs(install) == {}
    else:
        manager.install("example", "Example", "first", scan([source, companion]))
        assert "v1.1.0" in (local / "SubModule.xml").read_text()
        assert (local / "changed.tpac").read_bytes() == b"new content"
        assert (local / "missing.txt").read_text() == "new file"
    assert sorted(api.downloaded) == [
        "Example/SubModule.xml",
        "Example/changed.tpac",
        "Example/missing.txt",
    ]
    assert (local / "personal.txt").read_text() == "keep settings"
    assert (install / "Modules/Companion/asset.tpac").read_bytes() == b"complete companion"


def test_existing_folder_for_a_different_module_is_not_adopted(tmp_path):
    source = module(tmp_path / "source/Example", "Example")
    install = game(tmp_path / "game")
    local = module(install / "Modules/Example", "Unrelated")
    state = State(tmp_path / "state")
    api = Files([source])
    manager = Manager(state, install, api, process_check=lambda: False)
    with pytest.raises(ModError, match="different module"):
        manager.install("example", "Example", "first", scan([source]))
    assert "Unrelated" in (local / "SubModule.xml").read_text()
    assert api.downloaded == [] and state.installs(install) == {}


def test_another_managed_mod_keeps_ownership(setup):
    manager, source, state = setup
    with pytest.raises(ModError, match="owned by another managed mod"):
        manager.install("another", "Another", "first", scan([source]))
    assert set(state.installs(manager.game)) == {"example"}
