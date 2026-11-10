import json
import os
from pathlib import Path

import pytest

from signalpoint.game import detect_epic, detect_xbox, find_launcher, validate_game
from signalpoint.manifest import (
    MAX_UPLOAD_FILE_SIZE,
    ModError,
    fingerprint,
    read_module,
    scan,
    validate_upload_sizes,
)


def test_upload_size_boundary_is_per_file():
    validate_upload_sizes([{"path": "Example/exact.tpac", "size": MAX_UPLOAD_FILE_SIZE}])
    validate_upload_sizes(
        [
            {"path": "Example/first.tpac", "size": 3 * 1024**3},
            {"path": "Example/second.tpac", "size": 3 * 1024**3},
        ]
    )
    with pytest.raises(ModError, match=r"huge\.tpac") as error:
        validate_upload_sizes([{"path": "Example/huge.tpac", "size": MAX_UPLOAD_FILE_SIZE + 1}])
    assert "5,368,709,121 bytes" in str(error.value)


def test_oversized_tpac_is_rejected_before_any_file_is_hashed(tmp_path, monkeypatch):
    from signalpoint import manifest

    folder = module(tmp_path / "Example")
    asset = folder / "huge.tpac"
    asset.write_bytes(b"small test fixture")
    actual_stat = Path.stat

    def fake_stat(path, *args, **kwargs):
        result = actual_stat(path, *args, **kwargs)
        if path == asset:
            values = list(result)
            values[6] = MAX_UPLOAD_FILE_SIZE + 1
            return os.stat_result(values)
        return result

    monkeypatch.setattr(Path, "stat", fake_stat)

    def forbidden_hash(*args, **kwargs):
        raise AssertionError("Scan must check file sizes before hashing any file")

    monkeypatch.setattr(manifest, "hashes", forbidden_hash)
    with pytest.raises(ModError, match=r"huge\.tpac"):
        scan([folder])


def module(path: Path, identifier="Example", version="v1.0.0"):
    path.mkdir(parents=True, exist_ok=True)
    (path / "SubModule.xml").write_text(
        f'<Module><Name value="{identifier}"/><Id value="{identifier}"/>'
        f'<Version value="{version}"/><DependedModules/></Module>',
        encoding="utf-8",
    )
    return path


def game(path, platform="Win64_Shipping_Client"):
    module(path / "Modules" / "Native", "Native")
    launcher = path / "bin" / platform / "TaleWorlds.MountAndBlade.Launcher.exe"
    launcher.parent.mkdir(parents=True)
    launcher.touch()
    return path


def test_folder_scan_version_overrides_and_rejection(tmp_path):
    root = module(tmp_path / "Example")
    (root / "asset.tpac").write_bytes(b"initial")
    first = scan([root])
    assert first["modules"][0]["version"] == "v1.0.0"
    (root / "asset.tpac").write_bytes(b"replacement")
    second = scan([root])
    assert first["modules"] == second["modules"]
    assert fingerprint(first) != fingerprint(second)
    with pytest.raises(ModError, match="directly"):
        scan([tmp_path])
    archive = tmp_path / "mod.zip"
    archive.touch()
    with pytest.raises(ModError, match="folder"):
        scan([archive])
    with pytest.raises(ModError, match="Duplicate"):
        scan([root, root])


def test_xml_entity_payload_is_rejected(tmp_path):
    root = module(tmp_path / "Bad")
    (root / "SubModule.xml").write_text(
        '<!DOCTYPE x [<!ENTITY secret SYSTEM "file:///C:/Windows/win.ini">]>'
        "<Module><Name>&secret;</Name></Module>",
        encoding="utf-8",
    )
    with pytest.raises(ModError, match="Cannot read"):
        read_module(root)


@pytest.mark.parametrize(
    "name",
    [
        "BirthAndDeath",
        "CustomBattle",
        "FastMode",
        "Multiplayer",
        "Native",
        "NavalDLC",
        "SandBox",
        "SandBoxCore",
        "StoryMode",
    ],
)
def test_native_modules_cannot_be_uploaded(tmp_path, name):
    root = module(tmp_path / name, name)
    with pytest.raises(ModError, match="Official"):
        scan([root])
    assert read_module(root, allow_official=True)["id"] == name


def test_epic_and_xbox_discovery_resolve_actual_launcher_layouts(tmp_path):
    epic = game(tmp_path / "EpicGame", "Epic_Client")
    xbox = game(
        tmp_path / "XboxGames" / "Bannerlord" / "Content", "Gaming.Desktop.x64_Shipping_Client"
    )
    manifests = tmp_path / "Epic" / "EpicGamesLauncher" / "Data" / "Manifests"
    manifests.mkdir(parents=True)
    (manifests / "bannerlord.item").write_text(json.dumps({"InstallLocation": str(epic)}))
    assert detect_epic(tmp_path / "Epic")[0].path == epic
    assert detect_xbox(roots=[tmp_path / "XboxGames"], packages=[])[0].path == xbox
    assert validate_game(find_launcher(xbox)) == xbox
    assert find_launcher(epic).parent.parent.parent == epic


@pytest.mark.parametrize("destination", ["../evil", "X:/absolute", "Example/CON", "Example/a:b"])
def test_manifest_paths_rejected(tmp_path, destination):
    from signalpoint.manifest import validate_manifest

    value = scan([module(tmp_path / "Example")])
    value["files"][0]["path"] = destination
    with pytest.raises(ModError):
        validate_manifest(value)
