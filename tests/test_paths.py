import os
import shutil
import subprocess
import sys
from pathlib import Path
from xml.dom import minidom

import pytest

from signalpoint.paths import filesystem_path


@pytest.mark.skipif(os.name != "nt", reason="Windows filesystem namespace")
def test_filesystem_namespace_handles_drive_unc_and_already_extended_paths():
    assert str(filesystem_path(Path(r"C:\Games\Bannerlord"))) == r"\\?\C:\Games\Bannerlord"
    assert str(filesystem_path(Path(r"\\server\share\game"))) == r"\\?\UNC\server\share\game"
    path = filesystem_path(Path("relative") / ".." / "downloads")
    assert path == filesystem_path(path)
    assert str(path).startswith("\\\\?\\") and ".." not in path.parts


@pytest.mark.skipif(os.name != "nt", reason="Windows legacy path-limit regression")
def test_legacy_staging(tmp_path):
    # Opt this copied interpreter OUT of longPathAware. The regression must work
    # even when the developer's machine normally has long paths enabled.
    winmanifest = pytest.importorskip("PyInstaller.utils.win32.winmanifest")
    executable = tmp_path / "legacy-python.exe"
    shutil.copy2(Path(sys.base_prefix) / "python.exe", executable)
    manifest = minidom.parseString(winmanifest.create_application_manifest())
    for node in manifest.getElementsByTagName("longPathAware"):
        node.firstChild.nodeValue = "false"
    winmanifest.write_manifest_to_executable(str(executable), manifest.toxml(encoding="utf-8"))
    script = tmp_path / "check.py"
    script.write_text(
        r"""
import base64
import hashlib
import shutil
from pathlib import Path
import httpx
from test_modules import game, module
from test_manager import Files
from signalpoint.api import Api, TransferControl
from signalpoint.manager import Manager
from signalpoint.manifest import scan
from signalpoint.paths import filesystem_path
from signalpoint.state import State

root = Path(__file__).parent
installation = game(root / 'game')
source = module(filesystem_path(root / 'source' / 'Napoleonic Warfare MP'), 'Napoleonic Warfare MP')
# The longest path in the published v1.2.61 release; file bytes are a tiny fixture.
relative = (
    'SceneObj/Backups/scn_nwf_battle_egypt_outskirts_artillery/'
    'ShaderCache/D3D11/terrain_shaders_header_data.bin'
)
asset = source / relative
asset.parent.mkdir(parents=True)
asset.write_bytes(b'fixture-v1')
legacy_target = (
    installation / '.signalpoint/transactions' / ('a' * 32) / 'new' / source.name / relative
)
filesystem_path(legacy_target.parent).mkdir(parents=True)
assert len(str(legacy_target)) >= 260
try:
    shutil.copy2(asset, legacy_target)
except OSError as exc:
    assert exc.winerror in {3, 206}, exc
else:
    raise AssertionError('Legacy process did not reproduce the Windows path limit')

state = State(root / 'state')
manager = Manager(state, installation, Files([source]), process_check=lambda: False)
manager.install('example', 'Example', 'first', scan([source]))
installed = manager.modules / source.name / relative
assert installed.read_bytes() == b'fixture-v1'
personal = manager.modules / source.name / 'personal.txt'
personal.write_text('keep this')
asset.write_bytes(b'fixture-v2')
original_commit = state.commit
def fail(*args):
    raise OSError('simulated database failure')
state.commit = fail
try:
    manager.install('example', 'Example', 'second', scan([source]))
except OSError as exc:
    assert 'simulated database failure' in str(exc)
else:
    raise AssertionError('Expected rollback')
state.commit = original_commit
manager.recover()
assert installed.read_bytes() == b'fixture-v1'
assert state.installs(installation)['example']['build_id'] == 'first'
manager.uninstall('example')
assert not installed.exists() and personal.read_text() == 'keep this'
assert state.installs(installation) == {}

data = b'verified download'
entry = {'path': 'Example/file', 'size': len(data),
         'sha256': hashlib.sha256(data).hexdigest(),
         'md5': base64.b64encode(hashlib.md5(data, usedforsecurity=False).digest()).decode()}
def response(request):
    if request.url.path.endswith('/downloads'):
        ticket = {'url': '/signalpoint/file', 'headers': {}}
        return httpx.Response(200, json={entry['sha256']: ticket})
    return httpx.Response(200, content=data)
api = Api(
    'http://127.0.0.1/signalpoint', client=httpx.Client(transport=httpx.MockTransport(response))
)
target = root / 'downloads' / ('a' * 80) / ('b' * 80) / (entry['sha256'] + '.part')
assert len(str(target)) >= 260
api.download('build', entry, target, TransferControl(), lambda _: None)
assert filesystem_path(target).read_bytes() == data
print('Legacy-path install, rollback, recovery, uninstall and download passed')
""",
        encoding="utf-8",
    )
    root = Path(__file__).resolve().parents[1]
    environment = {
        **os.environ,
        "PYTHONHOME": sys.base_prefix,
        "PYTHONNOUSERSITE": "1",
        "PYTHONPATH": os.pathsep.join(
            [str(root / "src"), str(root / "tests"), *filter(None, sys.path)]
        ),
        "PATH": sys.base_prefix + os.pathsep + os.environ.get("PATH", ""),
    }
    result = subprocess.run(
        [str(executable), str(script)], env=environment, capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "download passed" in result.stdout
