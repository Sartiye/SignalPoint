import base64
import hashlib
import json

import httpx
import pytest

from signalpoint.api import Api, TransferControl
from signalpoint.manifest import ModError, scan


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


@pytest.mark.parametrize("region", ["", ".eu"])
def test_r2_transfer_urls_are_limited_to_real_s3_endpoints(region):
    api = Api("https://example.com/signalpoint")
    valid = (
        f"https://{'a' * 32}{region}.r2.cloudflarestorage.com/signalpoint/file?X-Amz-Signature=test"
    )
    assert api.transfer_url(valid) == valid
    for invalid in (
        valid.replace("https://", "http://"),
        valid.replace(".com/", ".com.attacker.example/"),
        valid.replace("https://", "https://user:password@"),
        valid.replace(".com/", ".com:8080/"),
        valid.replace("a" * 32, "not-an-account"),
        valid + "#fragment",
    ):
        with pytest.raises(ModError, match="destination"):
            api.transfer_url(invalid)


def test_signed_r2_download_resumes_without_publisher_authorization(tmp_path):
    content = b"verified R2 module content"
    entry = {
        "path": "Example/file",
        "size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "md5": base64.b64encode(hashlib.md5(content, usedforsecurity=False).digest()).decode(),
    }

    def handler(request):
        assert "authorization" not in request.headers
        if request.url.path.endswith("/downloads"):
            return httpx.Response(
                200,
                json={
                    entry["sha256"]: {
                        "url": f"https://{'a' * 32}.eu.r2.cloudflarestorage.com/bucket/object",
                        "headers": {},
                    }
                },
            )
        assert request.headers["range"] == "bytes=5-"
        return httpx.Response(
            206,
            content=content[5:],
            headers={
                "Content-Range": f"bytes 5-{len(content) - 1}/{len(content)}",
            },
        )

    api = Api(
        "https://example.com/signalpoint",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    api.token = "publisher-token-must-stay-private"
    target = tmp_path / "partial"
    target.write_bytes(content[:5])
    api.download("build", entry, target, TransferControl(), lambda _: None)
    assert target.read_bytes() == content


@pytest.mark.parametrize("failure", ["interrupted", "already_stored", "expired"])
def test_publisher_retries_only_the_failed_r2_file(tmp_path, monkeypatch, failure):
    from test_modules import module

    from signalpoint import api as transfers

    folder = module(tmp_path / "Example")
    content = b"published asset"
    (folder / "asset.tpac").write_bytes(content)
    manifest = scan([folder])
    asset = next(f for f in manifest["files"] if f["path"].endswith(".tpac"))
    issued, uploads, progress = [], [], []
    monkeypatch.setattr(transfers.time, "sleep", lambda _: None)

    def handler(request):
        if request.url.host.endswith(".r2.cloudflarestorage.com"):
            assert "authorization" not in request.headers
            assert request.read() == content
            uploads.append(request.url.params["ticket"])
            if len(uploads) == 1:
                if failure == "expired":
                    return httpx.Response(403)
                raise httpx.ReadError("connection lost", request=request)
            return httpx.Response(200)
        assert request.headers["authorization"] == "Bearer publisher"
        if request.url.path.endswith("/module-drafts"):
            return httpx.Response(
                201,
                json={
                    "build": {"id": "build", "status": "uploading"},
                    "manifest": manifest,
                },
            )
        if request.url.path.endswith("/uploads"):
            requested = json.loads(request.content)["hashes"]
            issued.append(requested)
            tickets = {sha: {"mode": "present"} for sha in requested}
            if len(issued) == 1 or failure != "already_stored":
                tickets[asset["sha256"]] = {
                    "mode": "s3-put",
                    "url": (
                        f"https://{'a' * 32}.eu.r2.cloudflarestorage.com/b/file"
                        f"?ticket={len(issued)}"
                    ),
                }
            return httpx.Response(200, json=tickets)
        assert request.url.path.endswith("/complete")
        return httpx.Response(200, json={"status": "published"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        api = Api("https://example.com/signalpoint", client=client)
        api.token = "publisher"
        result = api.upload_modules(
            "example",
            None,
            manifest,
            [],
            [folder],
            "",
            TransferControl(),
            progress.append,
        )
    assert result["status"] == "published"
    assert len(issued) == 2 and len(issued[0]) == 2 and issued[1] == [asset["sha256"]]
    assert uploads == (["1"] if failure == "already_stored" else ["1", "2"])
    assert any(item["state"].startswith("Retrying asset.tpac") for item in progress)


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
