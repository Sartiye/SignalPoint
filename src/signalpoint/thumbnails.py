"""Asynchronous, bounded image fetching and hash-verified local caching."""

import hashlib
import json
import re
import time
from collections import OrderedDict, deque
from urllib.parse import quote

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from signalpoint.manifest import ModError


class ThumbnailLoader(QObject):
    ready = Signal(str, str, object)

    def __init__(self, api, directory, parent=None):
        super().__init__(parent)
        self.api, self.directory = api, directory / "thumbnails"
        self.network = QNetworkAccessManager(self)
        self.images, self.failed = OrderedDict(), {}
        self.queue, self.pending, self.active = deque(), set(), 0
        self.stopped = False

    @staticmethod
    def metadata(mod):
        value = mod.get("thumbnail")
        if not isinstance(value, dict):
            return None
        if not re.fullmatch(r"[0-9a-f]{32}", str(value.get("id", ""))) or not re.fullmatch(
            r"[0-9a-f]{64}", str(value.get("sha256", ""))
        ):
            return None
        return value

    @staticmethod
    def decode(data, sha):
        if len(data) > 1024**2 or hashlib.sha256(data).hexdigest() != sha:
            return None
        image = QImage.fromData(data, "PNG")
        if image.isNull() or image.width() != 384 or image.height() != 384:
            return None
        return QPixmap.fromImage(image)

    def request(self, mod):
        meta = self.metadata(mod)
        if not meta:
            return None
        sha, key = meta["sha256"], (mod["id"], meta["id"])
        if sha in self.images:
            self.images.move_to_end(sha)
            return self.images[sha]
        if key in self.pending or self.failed.get(key, float("-inf")) + 60 > time.monotonic():
            return None
        path = self.directory / (sha + ".png")
        try:
            image = self.decode(path.read_bytes(), sha) if path.stat().st_size <= 1024**2 else None
        except OSError:
            image = None
        if image is not None:
            self.remember(sha, image)
            return image
        self.pending.add(key)
        self.queue.append((key, meta))
        QTimer.singleShot(0, self.pump)
        return None

    def remember(self, sha, image):
        self.images[sha] = image
        self.images.move_to_end(sha)
        while len(self.images) > 64:
            self.images.popitem(last=False)

    def pump(self):
        while not self.stopped and self.queue and self.active < 4:
            key, meta = self.queue.popleft()
            self.active += 1
            path = f"mods/{quote(key[0], safe='')}/thumbnail/{key[1]}"
            self.fetch(key, meta, self.api.base + "/api/v1/" + path, {}, True)

    def fetch(self, key, meta, url, headers, ticket):
        request = QNetworkRequest(QUrl(url))
        request.setTransferTimeout(15000)
        request.setAttribute(
            QNetworkRequest.Attribute.RedirectPolicyAttribute,
            QNetworkRequest.RedirectPolicy.ManualRedirectPolicy,
        )
        for name, value in headers.items():
            request.setRawHeader(name.encode("ascii"), value.encode("ascii"))
        reply = self.network.get(request)
        limit, data = (16384 if ticket else 1024**2), bytearray()
        reply.setReadBufferSize(limit + 1)

        def consume():
            data.extend(bytes(reply.readAll()))
            if len(data) > limit:
                reply.abort()

        def done():
            consume()
            valid = (
                reply.error() == QNetworkReply.NetworkError.NoError
                and reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute) == 200
                and len(data) <= limit
            )
            reply.deleteLater()
            if valid and ticket:
                try:
                    value = json.loads(data)
                    if not isinstance(value, dict):
                        raise ValueError("Invalid thumbnail response")
                    target = self.api.transfer_url(value["url"])
                    headers = value.get("headers", {})
                    if not isinstance(headers, dict):
                        raise ValueError("Invalid thumbnail headers")
                    # Scoped local transfer headers only; never forward login credentials.
                    headers = {
                        k: v for k, v in headers.items() if k.lower() == "x-signalpoint-transfer"
                    }
                except (ModError, ValueError, KeyError, TypeError):
                    self.complete(key, meta, None)
                    return
                self.fetch(key, meta, target, headers, False)
            else:
                self.complete(key, meta, bytes(data) if valid else None)

        reply.readyRead.connect(consume)
        reply.finished.connect(done)

    def complete(self, key, meta, data):
        self.active -= 1
        self.pending.discard(key)
        image = self.decode(data, meta["sha256"]) if data is not None else None
        if image is not None:
            self.remember(meta["sha256"], image)
            try:
                self.directory.mkdir(parents=True, exist_ok=True)
                (self.directory / (meta["sha256"] + ".png")).write_bytes(data)
            except OSError:
                pass
            self.ready.emit(key[0], key[1], image)
        else:
            self.failed[key] = time.monotonic()
        self.pump()

    def stop(self):
        self.stopped = True
        self.queue.clear()
        for reply in self.network.findChildren(QNetworkReply):
            if reply.isRunning():
                reply.abort()
