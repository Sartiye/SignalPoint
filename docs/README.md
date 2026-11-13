# SignalPoint integration docs

For developers building another launcher or mod manager against SignalPoint.
These documents describe the hosted HTTP API used by desktop **0.1.5**.

- [Player API](API.md): browse, detect updates, fetch manifests and download files
  without a player account.
- [Publisher API](PUBLISHING.md): optional publisher login, module edits and
  resumable uploads.
- [Product design](../DESIGN.md): installation behavior and planned features.

Production base URL: `https://mb-cloudflare.ey.r.appspot.com/signalpoint`

[Interactive API reference](https://mb-cloudflare.ey.r.appspot.com/signalpoint/docs)
and [OpenAPI JSON](https://mb-cloudflare.ey.r.appspot.com/signalpoint/openapi.json)
list the routes and request schemas. Several response schemas are currently
unspecified there; use the examples here and the reference client below.

The API is a public beta. `/api/v1` is the current API namespace, not a promise
that every field will remain unchanged. Ignore additional response fields and
handle unknown transfer modes explicitly. Examples use fictional IDs, hashes and
content; obtain real values from the server rather than copying example hashes.

## Reference implementation

The public client is MIT licensed; retain its license notice when reusing code.
The backend implementation stays private. A player launcher needs neither backend
source, publisher credentials nor cloud credentials.

| Source | Purpose |
| --- | --- |
| [api.py](../src/signalpoint/api.py) | HTTP API, transfer authorization and verified resumable downloads |
| [manifest.py](../src/signalpoint/manifest.py) | Module layout, path validation and hashes |
| [manager.py](../src/signalpoint/manager.py) | Staging, existing-file reuse, receipts, rollback and uninstall |
| [game.py](../src/signalpoint/game.py) | Bannerlord installation discovery and launching |
| [multipart.py](../src/signalpoint/multipart.py) | Resumable publisher uploads |

The client license covers this repository, not marketplace mods or unrestricted
use of the hosted service. See the public [Terms](https://mb-cloudflare.ey.r.appspot.com/signalpoint/terms/)
and [Privacy Policy](https://mb-cloudflare.ey.r.appspot.com/signalpoint/privacy/).
