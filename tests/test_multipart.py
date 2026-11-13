import base64
import hashlib
from urllib.parse import parse_qs

import httpx
import pytest

from signalpoint import multipart
from signalpoint.api import Api, TransferControl
from signalpoint.manifest import ModError


def checksum(data):
    return base64.b64encode(hashlib.md5(data, usedforsecurity=False).digest()).decode()


@pytest.fixture
def upload_case(tmp_path, monkeypatch):
    monkeypatch.setattr(multipart, "PART_SIZE", 4)
    monkeypatch.setattr(multipart, "CHUNK", 2)
    monkeypatch.setattr(multipart.time, "sleep", lambda _: None)
    path = tmp_path / "file.tpac"
    path.write_bytes(b"123456789")
    entry = {
        "path": "Example/file.tpac",
        "size": 9,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "md5": checksum(path.read_bytes()),
    }
    return path, entry


@pytest.mark.parametrize("failure", ["none", "lost_reply", "expired_link", "complete_reply"])
def test_streamed_parts_retry_with_fresh_links_without_resending_completed_parts(
    upload_case, failure
):
    path, entry = upload_case
    stored, attempts, links, checksums = {}, [], [], []
    completed, failed = False, False

    def handler(req):
        nonlocal checksums, completed, failed
        if req.url.host == "example.com":
            assert req.headers["authorization"] == "Bearer private"
            import json

            value = json.loads(req.content)
            if req.url.path.endswith("/part"):
                n = value["number"]
                links.append(n)
                return httpx.Response(
                    200,
                    json={
                        "mode": "s3-part",
                        "url": f"https://{'a' * 32}.r2.cloudflarestorage.com/bucket/key?n={n}",
                        "headers": {
                            "Content-MD5": checksums[n - 1],
                            "Content-Length": str(min(4, 9 - (n - 1) * 4)),
                        },
                    },
                )
            if req.url.path.endswith("/complete"):
                assert b"".join(stored[n] for n in sorted(stored)) == path.read_bytes()
                if failure == "complete_reply" and not completed:
                    completed = True
                    raise httpx.ReadError("lost completion response")
                completed = True
                return httpx.Response(200, json={"mode": "present"})
            checksums = value["checksums"]
            return httpx.Response(
                200,
                json={
                    "mode": "s3-multipart",
                    "upload_id": "session",
                    "part_size": 4,
                    "parts": [
                        {
                            "PartNumber": n,
                            "ETag": hashlib.md5(data, usedforsecurity=False).hexdigest(),
                        }
                        for n, data in stored.items()
                    ],
                },
            )
        assert "authorization" not in req.headers
        n = int(parse_qs(req.url.query.decode())["n"][0])
        attempts.append(n)
        assert checksum(req.content) == req.headers["content-md5"]
        assert len(req.content) == int(req.headers["content-length"])
        if failure == "expired_link" and n == 2 and not failed:
            failed = True
            return httpx.Response(403)
        stored[n] = req.content
        if failure == "lost_reply" and n == 2 and not failed:
            failed = True
            raise httpx.ReadError("lost part response")
        return httpx.Response(
            200, headers={"ETag": hashlib.md5(req.content, usedforsecurity=False).hexdigest()}
        )

    api = Api(
        "https://example.com/signalpoint",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    api.token = "private"
    progress = []
    multipart.upload(
        api,
        "build",
        {"part_size": 4},
        path,
        entry,
        TransferControl(),
        lambda *event: progress.append(event),
    )
    assert completed
    expected = [1, 2, 2, 3] if failure == "expired_link" else [1, 2, 3]
    assert attempts == links == expected
    assert all(0 <= event[0] <= entry["size"] for event in progress)
    assert progress[-1][0] == entry["size"]


def test_changed_source_is_rejected_before_creating_a_session(upload_case):
    path, entry = upload_case
    path.write_bytes(b"new edits")
    api = Api(
        "https://example.com/signalpoint",
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _: pytest.fail("A changed file must not create an upload session")
            )
        ),
    )
    with pytest.raises(ModError, match="Source changed since review"):
        multipart.upload(
            api, "build", {"part_size": 4}, path, entry, TransferControl(), lambda *_: None
        )
