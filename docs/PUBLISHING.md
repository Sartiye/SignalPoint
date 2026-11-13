# Publisher API

Optional for third-party launchers. A download-only client needs only the
[player API](API.md). All routes here use the same production base URL and
`/api/v1` namespace. No cloud SDK or cloud credentials belong in a launcher.

## Authentication and listing access

`POST /api/v1/auth/login` accepts:

```json
{"username": "publisher", "password": "publisher-supplied-password"}
```

The response contains `{token, csrf, user}`. Desktop API requests use
`Authorization: Bearer <token>`. Sessions currently last 24 hours and may be
revoked earlier. `GET /api/v1/auth/me` checks the session and
`POST /api/v1/auth/logout` revokes it. The login response also sets a browser
cookie; use a separate credential-free HTTP client for storage transfers.
Cookie-authenticated mutations require `X-CSRF-Token`; desktop bearer requests
do not need it.

Accounts/listings are assigned by the administrator. Publisher approval, enabled
status, membership/expiry, quota and listing ownership are enforced by the server.
There is no public registration or payment flow. A trusted publisher does not
need manual review before each technically valid release publishes.

| Operation | Route | Body / response |
| --- | --- | --- |
| Assigned listings | `GET /api/v1/publisher/mods` | `{mods: [...]}` |
| Current editable release | `GET /api/v1/publisher/mods/{mod_id}/manifest` | `{build_id, manifest}`; build ID is null before the first release |
| Edit description | `PATCH /api/v1/publisher/mods/{mod_id}` | `{"description": "text"}`; returns the updated listing |
| Set thumbnail | `PUT /api/v1/publisher/mods/{mod_id}/thumbnail` | Raw image bytes; returns the updated listing |
| Remove thumbnail | `DELETE /api/v1/publisher/mods/{mod_id}/thumbnail` | Returns the updated listing |

Descriptions allow 10,000 characters. Thumbnails accept a still PNG/JPEG/WebP up
to 4 MiB, at most 4096 pixels per side and 8 megapixels. The server normalizes
them to a 384 × 384 PNG and returns fresh thumbnail metadata. Page edits do not
create a file release.

## Prepare a release

Read the current publisher manifest first. For module-based edits, use
`POST /api/v1/mods/{mod_id}/module-drafts`:

```json
{
  "base_build": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "manifest": null,
  "remove_modules": ["ExampleMod"],
  "notes": "Remove this companion module from the next release."
}
```

Here `manifest` is either null for removal-only edits, or a schema-1 manifest
containing **complete replacements** for the changed/new module folders. Retained
modules need no local files. `remove_modules` contains module IDs, not folders.
For a first release, `base_build` is null. At least one module must remain in the
resulting release; the removal example above therefore requires another retained
module. Removing a module never instructs the launcher to delete a publisher's
source folder.

The response is `{build, manifest}`: a build record plus the server-assembled,
complete manifest. Use `build.id` and this returned manifest for uploads. The
backend rejects stale `base_build` values with HTTP 409, including if another
release wins before final publication. Refresh and review again rather than
silently replacing the latest release.

`POST /api/v1/mods/{mod_id}/drafts` also accepts a complete
`{"manifest": {...}, "notes": "text"}` and returns a build record directly.
The module-draft endpoint is preferable for editing an existing multi-module mod.
Identical drafts can be reused. If the returned build is already `published`,
the operation is complete.

Limits: 1–20 distinct module IDs/folders, up to 20,000 files, 100 GiB total declared
size, **5 GiB per uploaded file** (including TPAC), plus the publisher's allowance.
Each module requires a root SubModule.xml matching its declared name, ID, version
and dependencies. Paths must be safe Windows-relative module paths. Archives,
links, native module IDs/folders and nested modules are unsupported. Notes allow
5,000 characters. See [manifest.py](../src/signalpoint/manifest.py) for scanning.

## Upload tickets and small files

Send API headers including:

```http
X-SignalPoint-Transfers: r2,r2-multipart
Authorization: Bearer <token>
```

`POST /api/v1/builds/{build_id}/uploads` takes `{"hashes": [...]}`, in batches
of 1–100 manifest hashes, and returns a hash-to-ticket map. Deduplicate hashes
while preserving every destination in the manifest. Tickets may be:

- `{"mode": "present"}`: content is already verified in storage; skip it.
- `{"mode": "s3-put", "url": "...", "headers": {...}}`: PUT the exact whole
  file to storage using the returned headers, including length, Base64 Content-MD5
  and create-only condition. Never send API bearer tokens/cookies to storage.
- `{"mode": "s3-multipart", "part_size": 524288000}`: use the flow below.

Rehash source files against the reviewed manifest before uploading. A transient
single-PUT failure needs a newly authorized ticket and may require restarting the
file. Reauthorize after a lost response: `present` means the file arrived. A
create-only HTTP 412 may mean another attempt already stored it; the server's
presence/publication check is the authority. Bound retries rather than looping
indefinitely. Clients advertising only `r2` retain single-PUT uploads for all sizes.

## Multipart files over 500 MiB

Files **larger than** 524,288,000 bytes use 500 MiB parts; only the final part may
be smaller. The current 5 GiB limit permits at most 11 parts. Compute each part's
Base64 MD5 while also verifying the file's full size, SHA-256 and MD5 against the
manifest. Stream parts in small buffers rather than loading 500 MiB into RAM.

Use the following authenticated API requests:

1. `POST /api/v1/builds/{build_id}/multipart/{sha}` with
   `{"checksums": ["<part-1 Base64 MD5>", "<part-2 Base64 MD5>"]}`.
   For a larger file, include every part in order. This starts or resumes the
   server-owned session for that listing and content hash.
2. If the response mode is `present`, the file is done. Otherwise it contains
   `mode: "s3-multipart"`, `upload_id`, `part_size`, `parts`, plus session
   metadata. `parts` contains verified `{PartNumber, ETag}` records. Part numbers
   are one-based. Skip those parts; check their ETags against your part MD5s.
3. For each remaining part, POST to the same route plus `/part` with
   `{"upload_id": "<returned ID>", "number": 1}`. It returns either `present`
   for the entire file or an `s3-part` ticket with URL and checksum/length headers.
   PUT that exact byte range directly to R2. Its returned ETag must match the
   part's hex MD5, ignoring surrounding quotation marks.
4. On a lost response, repeat the start/resume request and inspect `parts` before
   resending. On transient failure or expired part links, obtain a fresh ticket.
   Do not discard already completed parts. An expired/replaced session can
   require starting again; use its newly returned upload ID.
5. POST to the file route plus `/complete` with `{"upload_id": "<returned ID>"}`.
   The server checks every stored part and conditionally completes the object.
   Success returns `{"mode": "present"}`. Retrying after a lost completion
   response is safe for the same file.

Signed part URLs currently last about 30 minutes. Sessions expire seven days
after creation. Cancel/interruption keeps completed parts for Retry, including
after reopening the app. Expired parts are eligible for manual server cleanup;
R2's default lifecycle also aborts incomplete uploads after seven days. A
multipart object ETag is composite, not the whole-file MD5 or SHA-256.

## Publish

After every file is present, `POST /api/v1/builds/{build_id}/complete` with `{}`.
The server checks content, SubModule.xml, permissions and release conflicts.
Success returns the build record with `status: "published"`; players then see
it as the listing's current build. This final request publishes the release
immediately for trusted publishers. Completing a single multipart file alone
does not publish the release.

On HTTP 409, distinguish missing files (retry transfers) from stale module edits
(reload the listing and review the new base). See [player API errors](API.md#thumbnails-reports-and-errors)
and the reference [API client](../src/signalpoint/api.py) and
[multipart uploader](../src/signalpoint/multipart.py) for retry behavior.
