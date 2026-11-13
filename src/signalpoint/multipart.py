"""Stream large uploads in resumable parts without buffering entire parts in RAM."""

import base64
import hashlib
import time

import httpx

from signalpoint.api import TRANSIENT_HTTP, ApiConnectionError, ApiError
from signalpoint.manifest import CHUNK, ModError

PART_SIZE = 500 * 1024**2


def request(api, route, value, control):
    for attempt in range(3):
        control.check()
        try:
            return api.request("POST", route, value, authenticated=True)
        except ApiError as exc:
            if exc.status not in TRANSIENT_HTTP or attempt == 2:
                raise
        except ApiConnectionError:
            if attempt == 2:
                raise
        time.sleep(1 + attempt)


def plan(path, entry, control):
    checksums, size, in_part = [], 0, 0
    sha = hashlib.sha256()
    whole_md5 = hashlib.md5(usedforsecurity=False)
    part_md5 = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as source:
        while block := source.read(min(CHUNK, PART_SIZE - in_part)):
            control.check()
            size += len(block)
            if size > entry["size"]:
                raise ModError(f"Source changed since review. Scan again: {entry['path']}")
            sha.update(block)
            whole_md5.update(block)
            part_md5.update(block)
            in_part += len(block)
            if in_part == PART_SIZE:
                checksums.append(base64.b64encode(part_md5.digest()).decode())
                part_md5 = hashlib.md5(usedforsecurity=False)
                in_part = 0
    if in_part:
        checksums.append(base64.b64encode(part_md5.digest()).decode())
    if (
        size != entry["size"]
        or sha.hexdigest() != entry["sha256"]
        or base64.b64encode(whole_md5.digest()).decode() != entry["md5"]
    ):
        raise ModError(f"Source changed since review. Scan again: {entry['path']}")
    return checksums


def upload(api, build, ticket, path, entry, control, progress):
    if ticket["part_size"] != PART_SIZE:
        raise ModError("Unsupported upload part size. Update SignalPoint and retry.")
    progress(0, f"Preparing parts for {path.name}")
    checksums = plan(path, entry, control)
    route = f"builds/{build}/multipart/{entry['sha256']}"

    def start():
        return request(api, route, {"checksums": checksums}, control)

    session = start()
    if session["mode"] == "present":
        progress(entry["size"])
        return
    if session.get("part_size") != PART_SIZE:
        raise ModError("Upload part size changed. Retry the upload.")

    def completed(value):
        parts = set()
        for part in value["parts"]:
            number = part["PartNumber"]
            if (
                number in parts
                or not 1 <= number <= len(checksums)
                or part["ETag"].strip('"') != base64.b64decode(checksums[number - 1]).hex()
            ):
                raise ModError("Stored upload parts failed verification. Retry the upload.")
            parts.add(number)
        return parts

    uploaded = completed(session)

    def part_size(number):
        return min(PART_SIZE, entry["size"] - (number - 1) * PART_SIZE)

    done = sum(part_size(number) for number in uploaded)
    progress(done, f"Resuming {path.name}" if done else "Uploading")
    for number, checksum in enumerate(checksums, 1):
        if number in uploaded:
            continue
        size = part_size(number)
        for attempt in range(3):
            control.check()
            state = f"Uploading {path.name} (part {number}/{len(checksums)})"
            if attempt:
                progress(done, f"Retrying {path.name}, part {number} ({attempt + 1}/3)")
                time.sleep(attempt)
                resumed = start()
                if resumed["mode"] == "present":
                    progress(entry["size"])
                    return
                if resumed["upload_id"] != session["upload_id"]:
                    raise ModError("Upload session expired. Click Confirm Upload to resume.")
                if number in completed(resumed):
                    break
            part_ticket = request(
                api,
                route + "/part",
                {"upload_id": session["upload_id"], "number": number},
                control,
            )
            if part_ticket["mode"] == "present":
                progress(entry["size"])
                return

            def content(number=number, size=size, checksum=checksum, state=state, base=done):
                md5, sent = hashlib.md5(usedforsecurity=False), 0
                with path.open("rb") as source:
                    source.seek((number - 1) * PART_SIZE)
                    while sent < size:
                        control.check()
                        block = source.read(min(CHUNK, size - sent))
                        if not block:
                            raise ModError("Upload source changed. Scan the module again.")
                        md5.update(block)
                        yield block
                        sent += len(block)
                        progress(base + sent, state)
                if base64.b64encode(md5.digest()).decode() != checksum:
                    raise ModError("Upload source changed. Scan the module again.")

            try:
                response = api.http.put(
                    api.transfer_url(part_ticket["url"]),
                    headers=part_ticket["headers"],
                    content=content(),
                )
            except httpx.TransportError:
                failure = "Connection interrupted"
            else:
                if response.is_success:
                    expected = base64.b64decode(checksum).hex()
                    if response.headers.get("etag", "").strip('"') != expected:
                        raise ModError("Uploaded part failed checksum verification")
                    break
                failure = f"Storage returned HTTP {response.status_code}"
                if response.status_code not in TRANSIENT_HTTP | {403, 409}:
                    raise ModError(f"{failure}. Click Confirm Upload to resume.")
        else:
            raise ModError(f"{failure}. Click Confirm Upload to resume completed parts.")
        uploaded.add(number)
        done += size
        progress(done, state)
    control.check()
    progress(entry["size"], f"Completing {path.name}")
    request(api, route + "/complete", {"upload_id": session["upload_id"]}, control)
