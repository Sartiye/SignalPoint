"""HTTP-only client. API credentials are never sent to object-storage endpoints."""

import hashlib
import re
import threading
import time
from pathlib import Path
from urllib.parse import quote, urljoin, urlsplit

import httpx

from signalpoint.manifest import CHUNK, ModError, hashes, validate_manifest, validate_upload_sizes

DEFAULT_API_URL = "https://mb-cloudflare.ey.r.appspot.com/signalpoint"


class ApiError(ModError):
    def __init__(self, status, detail):
        super().__init__(detail)
        self.status = status


class Cancelled(ModError):
    pass


class TransferControl:
    def __init__(self):
        self.paused = False
        self.cancelled = False
        self.condition = threading.Condition()

    def pause(self, value):
        with self.condition:
            self.paused = value
            self.condition.notify_all()

    def cancel(self):
        with self.condition:
            self.cancelled = True
            self.condition.notify_all()

    def check(self):
        with self.condition:
            while self.paused and not self.cancelled:
                self.condition.wait(0.3)
            if self.cancelled:
                raise Cancelled("Cancelled; installed files have been preserved.")


class Api:
    def __init__(self, base, *, client=None):
        self.base = base.rstrip("/")
        parsed = urlsplit(self.base)
        if (
            not parsed.hostname
            or parsed.username
            or parsed.query
            or parsed.fragment
            or parsed.scheme not in {"http", "https"}
            or (parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1"})
        ):
            raise ModError("Use an HTTPS backend URL, or HTTP on localhost for development.")
        self.origin = f"{parsed.scheme}://{parsed.netloc}"
        self.http = client or httpx.Client(
            timeout=httpx.Timeout(60, connect=10), follow_redirects=False
        )
        self.token = ""
        self._tickets = {}

    def request(self, method, path, value=None, *, authenticated=False, content=None):
        headers = {
            "Cache-Control": "no-cache",
            "Accept": "application/json",
            "X-SignalPoint-Transfers": "gcs,r2",
        }
        if authenticated:
            if not self.token:
                raise ModError("Sign in as a publisher first.")
            headers["Authorization"] = "Bearer " + self.token
        payload = {"json": value} if content is None else {"content": content}
        if content is not None:
            headers["Content-Type"] = "image/png"
        try:
            response = self.http.request(
                method, self.base + "/api/v1/" + path.lstrip("/"), headers=headers, **payload
            )
        except httpx.HTTPError as exc:
            raise ModError(
                "Cannot reach SignalPoint. Check your internet connection and try again."
            ) from exc
        if not response.is_success:
            try:
                detail = response.json()["detail"]
            except (ValueError, KeyError):
                detail = f"Server returned HTTP {response.status_code}"
            raise ApiError(response.status_code, str(detail))
        return response.json()

    def catalog(self, query=""):
        return self.request("GET", "mods?q=" + quote(query))["mods"]

    def manifest(self, mod_id):
        result = self.request("GET", f"mods/{quote(mod_id, safe='')}/manifest")
        validate_manifest(result["manifest"])
        return result

    def login(self, username, password):
        value = self.request("POST", "auth/login", {"username": username, "password": password})
        self.token = value["token"]
        return value["user"]

    def publisher_mods(self):
        return self.request("GET", "publisher/mods", authenticated=True)["mods"]

    def update_page(self, mod_id, description, thumbnail=None, *, remove_thumbnail=False):
        path = "publisher/mods/" + quote(mod_id, safe="")
        mod = self.request("PATCH", path, {"description": description}, authenticated=True)
        if thumbnail is not None:
            mod = self.request("PUT", path + "/thumbnail", authenticated=True, content=thumbnail)
        elif remove_thumbnail:
            mod = self.request("DELETE", path + "/thumbnail", authenticated=True)
        return mod

    def thumbnail(self, mod_id, metadata, *, authenticated=False):
        path = f"mods/{quote(mod_id, safe='')}/thumbnail/{quote(metadata['id'], safe='')}"
        ticket = self.request("GET", path, authenticated=authenticated)
        data = bytearray()
        with self.http.stream(
            "GET", self.transfer_url(ticket["url"]), headers=ticket.get("headers", {}), timeout=15
        ) as response:
            if response.status_code != 200:
                raise ModError("Thumbnail could not be downloaded.")
            for block in response.iter_bytes():
                data.extend(block)
                if len(data) > 1024**2:
                    raise ModError("Thumbnail exceeds the image size limit.")
        if hashlib.sha256(data).hexdigest() != metadata["sha256"]:
            raise ModError("Thumbnail failed verification.")
        return bytes(data)

    def transfer_url(self, value):
        url = urljoin(self.origin + "/", value)
        parsed = urlsplit(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        google = parsed.hostname == "storage.googleapis.com" or (parsed.hostname or "").endswith(
            ".storage.googleapis.com"
        )
        r2 = bool(
            re.fullmatch(
                r"[0-9a-f]{32}(?:\.eu)?\.r2\.cloudflarestorage\.com", parsed.hostname or ""
            )
        )
        try:
            r2 = r2 and parsed.port in {None, 443}
        except ValueError as exc:
            raise ModError("The server returned an unsupported file-transfer destination.") from exc
        if (
            parsed.username
            or parsed.fragment
            or (origin != self.origin and not ((google or r2) and parsed.scheme == "https"))
        ):
            raise ModError("The server returned an unsupported file-transfer destination.")
        return url

    def prepare_downloads(self, build, entries):
        needed = list(dict.fromkeys(f["sha256"] for f in entries))
        for index in range(0, len(needed), 100):
            self._authorize(build, needed[index : index + 100])

    def _authorize(self, build, digests):
        result = self.request("POST", f"builds/{build}/downloads", {"hashes": digests})
        for sha, ticket in result.items():
            self._tickets[(build, sha)] = (time.monotonic(), ticket)

    def download(self, build, entry, target: Path, control, progress):
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_file() and target.stat().st_size == entry["size"]:
            if hashes(target)[0] == entry["sha256"]:
                progress(entry["size"])
                return
            target.unlink()
        for attempt in range(3):
            control.check()
            key = (build, entry["sha256"])
            if key not in self._tickets or time.monotonic() - self._tickets[key][0] > 1200:
                self._authorize(build, [entry["sha256"]])
            ticket = self._tickets[key][1]
            offset = target.stat().st_size if target.exists() else 0
            if offset > entry["size"]:
                target.unlink()
                offset = 0
            headers = {**ticket.get("headers", {}), "Accept-Encoding": "identity"}
            if offset:
                headers["Range"] = f"bytes={offset}-"
            try:
                with self.http.stream(
                    "GET", self.transfer_url(ticket["url"]), headers=headers
                ) as response:
                    if response.status_code == 403 and attempt < 2:
                        self._tickets.pop(key, None)
                        continue
                    if response.status_code in {408, 429, 500, 502, 503, 504} and attempt < 2:
                        control.check()
                        time.sleep(0.25 * (attempt + 1))
                        continue
                    if response.status_code not in {200, 206}:
                        raise ApiError(
                            response.status_code,
                            f"Download failed (HTTP {response.status_code}); retry or refresh.",
                        )
                    if response.headers.get("content-encoding", "identity") != "identity":
                        raise ModError(
                            "Unexpected transfer encoding; cannot safely resume this file."
                        )
                    if response.status_code == 206:
                        content_range = response.headers.get("content-range", "")
                        if not content_range.startswith(
                            f"bytes {offset}-"
                        ) or not content_range.endswith(f"/{entry['size']}"):
                            raise ModError("Server returned an incorrect resume range.")
                    else:
                        offset = 0
                    progress(offset)
                    with target.open("ab" if offset else "wb") as output:
                        for block in response.iter_bytes(CHUNK):
                            control.check()
                            offset += len(block)
                            if offset > entry["size"]:
                                raise ModError("Download exceeds the published file size.")
                            output.write(block)
                            progress(offset)
                if offset != entry["size"]:
                    raise httpx.ReadError("Incomplete transfer")
                if hashes(target)[0] != entry["sha256"]:
                    target.unlink(missing_ok=True)
                    raise ModError(
                        "Downloaded file failed verification. Retry to fetch a fresh copy."
                    )
                return
            except httpx.HTTPError as exc:
                if attempt == 2:
                    raise ModError("Connection interrupted. Retry to resume the download.") from exc
                control.check()
        raise ModError("Download authorization expired. Retry the operation.")

    def upload(self, mod_id, manifest, folders, notes, control, progress):
        validate_upload_sizes(manifest["files"])
        draft = self.request(
            "POST",
            f"mods/{quote(mod_id, safe='')}/drafts",
            {"manifest": manifest, "notes": notes},
            authenticated=True,
        )
        return self._upload_draft(draft, manifest, folders, control, progress)

    def publisher_manifest(self, mod_id):
        return self.request(
            "GET", f"publisher/mods/{quote(mod_id, safe='')}/manifest", authenticated=True
        )

    def upload_modules(
        self, mod_id, base_build, changes, removed, folders, notes, control, progress
    ):
        if changes is not None:
            validate_upload_sizes(changes["files"])
        prepared = self.request(
            "POST",
            f"mods/{quote(mod_id, safe='')}/module-drafts",
            {
                "base_build": base_build,
                "manifest": changes,
                "remove_modules": removed,
                "notes": notes,
            },
            authenticated=True,
        )
        validate_manifest(prepared["manifest"])
        return self._upload_draft(
            prepared["build"], prepared["manifest"], folders, control, progress
        )

    def _upload_draft(self, draft, manifest, folders, control, progress):
        if draft["status"] in {"draft", "published"}:
            return draft
        sources = {folder.name: folder for folder in folders}
        entries = {}
        for entry in manifest["files"]:
            if entry["sha256"] not in entries or entry["path"].split("/", 1)[0] in sources:
                entries[entry["sha256"]] = entry
        unique = list(entries)
        total = sum(f["size"] for f in entries.values())
        completed = 0
        for start in range(0, len(unique), 100):
            control.check()
            tickets = self.request(
                "POST",
                f"builds/{draft['id']}/uploads",
                {"hashes": unique[start : start + 100]},
                authenticated=True,
            )
            for sha, ticket in tickets.items():
                control.check()
                entry = entries[sha]

                def update(count, base=completed):
                    progress({"state": "Uploading", "done": base + count, "total": total})

                if ticket["mode"] == "present":
                    update(entry["size"])
                    completed += entry["size"]
                    continue
                root, rest = entry["path"].split("/", 1)
                if root not in sources:
                    raise ModError(
                        f"Stored module files are unavailable: {entry['path']}. "
                        "Select Change for this module to restore its files."
                    )
                path = sources[root] / rest
                if not path.is_file():
                    raise ModError(f"Source changed since review. Scan again: {entry['path']}")
                validate_upload_sizes([{"path": entry["path"], "size": path.stat().st_size}])
                if hashes(path) != (sha, entry["md5"]):
                    raise ModError(f"Source changed since review. Scan again: {entry['path']}")
                url = self.transfer_url(ticket["url"])
                if ticket["mode"] == "resumable":
                    self._resumable_upload(url, ticket, path, entry, control, update)
                else:

                    def content(path=path):
                        sent = 0
                        with path.open("rb") as source:
                            while block := source.read(CHUNK):
                                control.check()
                                yield block
                                sent += len(block)
                                update(sent)

                    try:
                        response = self.http.put(
                            url,
                            headers={
                                **ticket.get("headers", {}),
                                "Content-Length": str(entry["size"]),
                            },
                            content=content(),
                        )
                    except httpx.HTTPError as exc:
                        raise ModError("Upload interrupted. Retry the reviewed upload.") from exc
                    if not response.is_success and not (
                        ticket["mode"] == "s3-put" and response.status_code == 412
                    ):
                        raise ModError(
                            f"Upload failed (HTTP {response.status_code}). Rescan and retry."
                        )
                completed += entry["size"]
        control.check()
        progress({"state": "Checking upload", "done": total, "total": total})
        return self.request("POST", f"builds/{draft['id']}/complete", {}, authenticated=True)

    def _resumable_upload(self, url, ticket, path, entry, control, progress):
        size, offset = entry["size"], 0
        with path.open("rb") as source:
            while True:
                control.check()
                source.seek(offset)
                block = source.read(8 * CHUNK)
                end = offset + len(block) - 1
                headers = {
                    **ticket.get("headers", {}),
                    "Content-Length": str(len(block)),
                    "Content-Range": f"bytes {offset}-{end}/{size}" if size else "bytes */0",
                }
                try:
                    response = self.http.put(url, headers=headers, content=block)
                except httpx.HTTPError:
                    control.check()
                    try:
                        response = self.http.put(
                            url,
                            headers={"Content-Length": "0", "Content-Range": f"bytes */{size}"},
                            content=b"",
                        )
                    except httpx.HTTPError as exc:
                        raise ModError("Upload interrupted. Retry to recover the draft.") from exc
                if response.status_code in {200, 201}:
                    progress(size)
                    return
                if response.status_code != 308:
                    raise ModError(f"Storage upload failed (HTTP {response.status_code}). Retry.")
                committed = response.headers.get("Range", "")
                next_offset = int(committed.rsplit("-", 1)[-1]) + 1 if committed else 0
                if next_offset <= offset:
                    raise ModError("Storage made no upload progress. Retry the upload.")
                offset = next_offset
                progress(offset)
