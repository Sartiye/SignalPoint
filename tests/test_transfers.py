import base64
import hashlib

import httpx
import pytest

from signalpoint.api import Api, TransferControl
from signalpoint.manifest import ModError


def test_download_resumes_with_range_and_verifies_hash(tmp_path):
    content = b"a" * 100 + b"b" * 500
    entry = {
        "path": "Example/file",
        "size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "md5": base64.b64encode(hashlib.md5(content, usedforsecurity=False).digest()).decode(),
    }
    seen = []

    def handler(request):
        if request.url.path.endswith("/downloads"):
            assert "authorization" not in request.headers
            return httpx.Response(
                200,
                json={
                    entry["sha256"]: {
                        "url": "/signalpoint/file",
                        "headers": {"X-SignalPoint-Transfer": "ticket"},
                    }
                },
            )
        seen.append(request.headers.get("range"))
        assert "authorization" not in request.headers
        assert request.headers["x-signalpoint-transfer"] == "ticket"
        return httpx.Response(
            206, headers={"Content-Range": "bytes 100-599/600"}, content=content[100:]
        )

    api = Api(
        "http://127.0.0.1/signalpoint", client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    api.token = "private-publisher-token"
    partial = tmp_path / "partial"
    partial.write_bytes(content[:100])
    api.download("build", entry, partial, TransferControl(), lambda _: None)
    assert seen == ["bytes=100-"]
    assert partial.read_bytes() == content


def test_transfer_destinations_restricted_and_bad_ranges_rejected(tmp_path):
    api = Api("http://127.0.0.1/signalpoint")
    with pytest.raises(ModError, match="destination"):
        api.transfer_url("https://attacker.example/download")
    assert api.transfer_url("https://storage.googleapis.com/bucket/file").startswith("https://")


def test_launcher_uses_its_binary_directory(tmp_path, monkeypatch):
    from test_modules import game

    from signalpoint import game as discovery

    root = game(tmp_path / "Xbox" / "Content", "Gaming.Desktop.x64_Shipping_Client")
    calls = []
    monkeypatch.setattr(discovery, "running", lambda: False)
    monkeypatch.setattr(discovery.stores, "ensure_open", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        discovery.subprocess, "Popen", lambda args, **kwargs: calls.append((args, kwargs))
    )
    discovery.start_launcher(root)
    executable = discovery.find_launcher(root)
    assert calls == [([str(executable)], {"cwd": executable.parent, "env": None})]
    assert (executable.parent / "../../Modules").resolve() == root / "Modules"


def test_frozen_launcher_environment_excludes_bundle_paths(tmp_path, monkeypatch):
    import ctypes
    import os

    from signalpoint import runtime as discovery

    if os.name != "nt":
        pytest.skip("Windows DLL search-path isolation")
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    monkeypatch.setattr(discovery.sys, "frozen", True, raising=False)
    monkeypatch.setattr(discovery.sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setenv(
        "PATH", os.pathsep.join([str(bundle), str(bundle / "PySide6"), "C:/Windows/System32"])
    )
    monkeypatch.setenv("QT_PLUGIN_PATH", str(bundle / "plugins"))
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetDllDirectoryW.argtypes = [ctypes.c_uint32, ctypes.c_wchar_p]
    kernel.GetDllDirectoryW.restype = ctypes.c_uint32
    length = kernel.GetDllDirectoryW(0, None)
    before = ctypes.create_unicode_buffer(length + 1)
    kernel.GetDllDirectoryW(len(before), before)
    with discovery.external_environment() as environment:
        assert environment["PATH"] == "C:/Windows/System32"
        assert "QT_PLUGIN_PATH" not in environment
    after = ctypes.create_unicode_buffer(length + 1)
    kernel.GetDllDirectoryW(len(after), after)
    assert before.value == after.value
