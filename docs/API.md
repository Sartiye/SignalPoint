# Player API

All routes below are relative to:

```text
https://mb-cloudflare.ey.r.appspot.com/signalpoint
```

Preserve `/signalpoint` when building URLs. A player launcher can browse and
download anonymously. Do not request a publisher login for these operations.

## Requests

Send these headers to the API:

```http
Accept: application/json
Cache-Control: no-cache
X-SignalPoint-Transfers: r2
```

JSON POST bodies also use `Content-Type: application/json`. The reference client
advertises `gcs,r2,r2-multipart`; `r2` alone is sufficient for player downloads
from the hosted service. Do not advertise multipart upload support unless you
implement the [publisher flow](PUBLISHING.md).

## 1. Browse mods

`GET /api/v1/mods` returns `{"mods": [...]}`. Add URL-encoded `?q=search` to
search names/descriptions, with a maximum of 200 characters. There is currently
no pagination parameter. Only available published listings are returned.

An illustrative listing, with additional fields omitted:

```json
{
  "mods": [{
    "id": "example",
    "name": "Example Mod",
    "description": "An example listing.",
    "current": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    "modules": [{
      "id": "ExampleMod",
      "name": "Example Mod",
      "version": "v1.0.0",
      "folder": "Example",
      "dependencies": []
    }],
    "size": 256,
    "thumbnail": null,
    "inspection": "not_run"
  }]
}
```

`id` identifies a marketplace listing, which may contain several modules.
Module IDs, folder names and listing IDs are separate identities. `size` is the
sum of declared file sizes, not necessarily the bytes a particular client needs.

Store the installed listing ID and its build ID locally. Compare `current` with
that build ID to detect updates. Module version text alone is insufficient:
publishers can replace content without changing the version in SubModule.xml.
Treat a build ID as opaque; do not calculate it yourself. The reference client
checks at startup, every minute while open, on resume and before launching.

## 2. Fetch the current manifest

`GET /api/v1/mods/{mod_id}/manifest` returns:

```json
{
  "build_id": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "manifest": {
    "schema": 1,
    "modules": [{
      "id": "ExampleMod",
      "name": "Example Mod",
      "version": "v1.0.0",
      "folder": "Example",
      "dependencies": [{"id": "Native", "version": "", "optional": false}]
    }],
    "files": [{
      "path": "Example/SubModule.xml",
      "size": 256,
      "sha256": "0000000000000000000000000000000000000000000000000000000000000000",
      "md5": "AAAAAAAAAAAAAAAAAAAAAA=="
    }]
  }
}
```

File `path` uses `/` and is relative to Bannerlord's **Modules** directory,
including the module folder. `size` is in bytes, `sha256` is lowercase hex, and
`md5` is Base64 of the raw 16-byte MD5 digest. MD5 is a transfer checksum;
verify downloaded content using SHA-256. Every module has a root SubModule.xml.

Use the `build_id` returned with this manifest for its download requests, even
if it differs from an earlier catalog response. Keep that manifest and build ID
together for the operation. Compare local file hashes to download only missing
or changed files. Duplicate content hashes can share a download but may appear
at multiple destination paths.

## 3. Authorize downloads

`POST /api/v1/builds/{build_id}/downloads` accepts **1–100 hashes** from that
build's manifest per request:

```json
{"hashes": ["0000000000000000000000000000000000000000000000000000000000000000"]}
```

The response maps each requested SHA-256 to a transfer ticket:

```json
{
  "0000000000000000000000000000000000000000000000000000000000000000": {
    "mode": "download",
    "url": "https://<account>.eu.r2.cloudflarestorage.com/<signed-object-url>",
    "headers": {}
  }
}
```

Request files from the returned URL with the returned headers. File bytes travel
directly from R2. Do not construct bucket/object URLs, modify signed query strings
or persist tickets as permanent download links. Current signed URLs last about
30 minutes; request fresh tickets after expiry or a storage HTTP 403.

Keep API authorization separate from storage requests. Never forward publisher
bearer tokens or API cookies to a storage URL. Treat signed URLs as temporary
credentials and keep them out of logs. Validate destinations: production uses
HTTPS account endpoints of the form `<32 hex characters>[.eu].r2.cloudflarestorage.com`
on the default HTTPS port. The reference client also supports same-origin local
test transfers and Google Storage endpoints; those are not required for current
production R2 downloads.

For resume, use `Accept-Encoding: identity` and `Range: bytes=N-`, where N is the
local partial size. For HTTP 206, validate Content-Range starts at N and declares
the expected full size. If storage returns HTTP 200, restart the file rather than
appending it. Reject unexpected encoding, excess bytes or incorrect ranges.
After completion, verify exact size and SHA-256 before installing. An ETag is
not a substitute for SHA-256, especially for multipart objects.

## 4. Install and record the result

The API supplies content; the launcher owns installation. Stage verified files
and apply recoverable folder replacements after Bannerlord and its launcher close.
Resolve dependencies, reject unsafe paths/links and prevent writes outside Modules.
Do not overwrite or remove Bannerlord's native modules. The protected names/IDs
are Native, SandBox, SandBoxCore, StoryMode, CustomBattle, Multiplayer,
BirthAndDeath, FastMode and NavalDLC.

Preserve unrelated local files. Existing matching module IDs may be adopted and
matching files reused; avoid creating two copies of one module ID. Keep local
receipts containing the installed build, manifest and managed files. Detect local
edits before replacement, and preserve unrelated/edited files during uninstall.
Record the new build as installed only after the filesystem operation commits.
See [manager.py](../src/signalpoint/manager.py) for the reference transaction flow.

## Python quick start

After installing this checkout with `python -m pip install -e .` on Python 3.13+:

```python
from pathlib import Path
from signalpoint.api import Api, DEFAULT_API_URL, TransferControl

api = Api(DEFAULT_API_URL)
mods = api.catalog()
if not mods:
    raise SystemExit("No published mods are available")

release = api.manifest(mods[0]["id"])
entry = next(
    f for f in release["manifest"]["files"]
    if f["path"].casefold().endswith("/submodule.xml")
)
target = Path("downloads") / (entry["sha256"] + ".part")
api.download(release["build_id"], entry, target, TransferControl(), lambda _: None)
print("Verified module XML downloaded to", target)
```

This downloads one module XML file to a local folder; it does not install a mod.
The helper renews download tickets, resumes partial files and verifies SHA-256.

## Thumbnails, reports and errors

| Operation | Route | Response |
| --- | --- | --- |
| Thumbnail ticket | `GET /api/v1/mods/{mod_id}/thumbnail/{identity}` | `{mode, url, headers}`; use `thumbnail.id` from the listing |
| Image for a website | `GET /mods/{mod_id}/thumbnail/{identity}` | HTTP 302 to the image on hosted storage |
| Research reports | `GET /api/v1/mods/{mod_id}/reports` | `{current_build, reports}`; match reports to the exact build |
| Official desktop release | `GET /api/v1/client-release` | `{version, sha256, size, published_at, filename}` |

Thumbnail metadata, when present, contains `id`, `sha256` and `size`. Verify the
downloaded image against that metadata. Treat descriptions and notes as untrusted
text when rendering them. `inspection: "not_run"` and an empty reports list mean
no research verdict is available, not that a mod passed a security assessment.

API errors normally contain `{"detail": "message"}`. Invalid request fields
also include `fields`. Handle status codes rather than depending on exact wording:

| Status | Client action |
| --- | --- |
| 400 / 422 | Correct the request; a hash may not belong to the selected build |
| 401 / 403 | Check publisher authentication/permissions for protected operations |
| 404 | Listing, build or content is unavailable; refresh the catalog |
| 409 | Refresh conflicting draft/session state; publication may be incomplete |
| 410 | Old build/content is no longer available; fetch the current manifest |
| 413 | Request, file or publisher allowance exceeds a limit |
| 426 | Update supported transfer capabilities/client behavior |
| 408 / 429 / 500 / 502 / 503 / 504 | Retry with a bounded delay; honor Retry-After if supplied |

Transport errors can leave useful partial files. Keep them for Retry and verify
them before use. Refresh a ticket on **storage** HTTP 403; do not confuse that
with an **API** permission failure. Respect `Cache-Control: no-store` on API
responses and do not assume old release files are retained as a version archive.
