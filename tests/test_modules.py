import json
from pathlib import Path

import pytest

from signalpoint.game import detect_epic, detect_xbox, find_launcher, validate_game
from signalpoint.manifest import ModError, fingerprint, read_module, scan


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
