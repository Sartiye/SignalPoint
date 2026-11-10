import hashlib
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtGui import QFontDatabase, QImage
from PySide6.QtWidgets import QApplication
from test_modules import module

from signalpoint.api import DEFAULT_API_URL, Api, ApiError
from signalpoint.desktop import STYLE, MainWindow, PublisherDialog
from signalpoint.manifest import MAX_UPLOAD_FILE_SIZE
from signalpoint.state import State
from signalpoint.thumbnails import ThumbnailLoader


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    instance.setStyleSheet(STYLE)
    if not QFontDatabase.families() and os.name == "nt":
        QFontDatabase.addApplicationFont("C:/Windows/Fonts/segoeui.ttf")
    return instance


def wait(app, condition, timeout=5):
    deadline = time.monotonic() + timeout
    while not condition():
        app.processEvents()
        if time.monotonic() > deadline:
            raise AssertionError("Desktop operation did not finish")
        time.sleep(0.01)
    for _ in range(3):
        app.processEvents()


def test_folder_review_and_trusted_publication(app, tmp_path):
    class Publisher:
        base = "http://127.0.0.1/signalpoint"
        token = "test-session"

        def publisher_mods(self):
            return [{"id": "example", "name": "Example"}]

        def manifest(self, mod):
            raise ApiError(404, "Not yet published")

        def upload(self, mod, manifest, paths, notes, control, progress):
            assert mod == "example"
            assert manifest["modules"][0]["version"] == "v1.0.0"
            control.check()
            progress({"state": "Uploading", "done": 10, "total": 10})
            return {"status": "published"}

    dialog = PublisherDialog(Publisher(), None)
    dialog.show()
    wait(app, lambda: dialog.worker is None)
    dialog.add_folders([module(tmp_path / "Example")])
    assert not dialog.confirm.isEnabled()
    dialog.scan_folders()
    wait(app, lambda: dialog.worker is None)
    assert dialog.confirm.isEnabled()
    assert "Modules/Example" in dialog.review.toPlainText()
    assert "v1.0.0" in dialog.review.toPlainText()
    assert "publishes directly" in dialog.review.toPlainText()
    published = []
    dialog.published.connect(lambda: published.append(True))
    dialog.upload()
    wait(app, lambda: dialog.worker is None)
    assert published == [True]
    assert dialog.status.text().startswith("Published")
    assert not dialog.confirm.isEnabled()
    dialog.close()
    app.processEvents()


def test_failed_size_check_invalidates_an_earlier_checkout(app, tmp_path, monkeypatch):
    from pathlib import Path

    class Publisher:
        base = "http://127.0.0.1/signalpoint"
        token = "test-session"

        def publisher_mods(self):
            return [{"id": "example", "name": "Example"}]

        def manifest(self, mod):
            raise ApiError(404, "Not yet published")

    dialog = PublisherDialog(Publisher(), None)
    dialog.show()
    wait(app, lambda: dialog.worker is None)
    folder = module(tmp_path / "Example")
    asset = folder / "huge.tpac"
    asset.write_bytes(b"fixture")
    dialog.add_folders([folder])
    dialog.scan_folders()
    wait(app, lambda: dialog.worker is None)
    assert dialog.confirm.isEnabled()
    assert "File-size check: passed" in dialog.review.toPlainText()
    original_stat = Path.stat

    def fake_stat(path, *args, **kwargs):
        result = original_stat(path, *args, **kwargs)
        if path == asset:
            values = list(result)
            values[6] = MAX_UPLOAD_FILE_SIZE + 1
            return os.stat_result(values)
        return result

    monkeypatch.setattr(Path, "stat", fake_stat)
    dialog.scan_folders()
    wait(app, lambda: dialog.worker is None)
    assert not dialog.confirm.isEnabled() and dialog.manifest is None
    assert "huge.tpac" in dialog.review.toPlainText()
    assert "5 GiB" in dialog.status.text()
    dialog.close()
    app.processEvents()


def test_progress_stays_on_card_until_install_complete(app, tmp_path, monkeypatch):
    monkeypatch.setattr(MainWindow, "startup", lambda self: None)
    monkeypatch.setattr(MainWindow, "background_sync", lambda self: None)
    state = State(tmp_path / "state")
    state.set(
        "catalog",
        [
            {
                "id": "example",
                "name": "Example",
                "description": "Test mod",
                "size": 1000,
                "modules": [{"name": "Example", "version": "v1.0.0"}],
            }
        ],
    )
    window = MainWindow(state)
    window.tabs.setCurrentIndex(1)
    window.show()
    app.processEvents()
    card = window.cards["example"][0]
    window.on_progress({"mod_id": "example", "state": "Downloading", "done": 1000, "total": 1000})
    assert not card.bar.isHidden()
    assert card.bar.value() == 1000
    assert "100%" in card.status.text()
    window.on_progress({"mod_id": "example", "state": "Verifying", "done": 1000, "total": 1000})
    assert not card.bar.isHidden()
    assert card.status.text() == "Verifying"
    window.on_progress({"mod_id": "example", "state": "Ready", "done": 1000, "total": 1000})
    assert card.bar.isHidden()
    assert window.launch.text() == "Start Launcher"
    assert not window.windowIcon().isNull()
    assert window.launch.geometry().right() > window.tabs.geometry().right()
    window.close()
    app.processEvents()


def test_hosted_default_replaces_saved_development_address(app, tmp_path, monkeypatch):
    monkeypatch.setattr(MainWindow, "startup", lambda self: None)
    state = State(tmp_path / "state")
    local = "http://127.0.0.1:8000/signalpoint"
    state.set("api", local)
    state.set("catalog", [{"id": "local-only"}])
    window = MainWindow(state)
    assert window.api.base == DEFAULT_API_URL
    assert window.catalog == []
    window.close()
    development = MainWindow(state, api_url=local)
    assert development.api.base == local
    development.close()
    app.processEvents()


def test_publisher_can_save_a_mod_page_without_uploading_module_files(app, tmp_path):
    class Publisher:
        base = DEFAULT_API_URL
        token = "test-session"

        def publisher_mods(self):
            return [{"id": "example", "name": "Example", "description": "Original"}]

        def update_page(self, mod_id, description, thumbnail, *, remove_thumbnail):
            assert mod_id == "example" and description == "New description"
            assert thumbnail.startswith(b"\x89PNG")
            assert not remove_thumbnail
            return {"id": mod_id, "name": "Example", "description": description}

    dialog = PublisherDialog(Publisher(), None)
    dialog.show()
    wait(app, lambda: dialog.worker is None)
    dialog.tabs.setCurrentIndex(1)
    dialog.description.setPlainText("New description")
    image = QImage(720, 480, QImage.Format.Format_RGB32)
    image.fill(0x334477)
    path = tmp_path / "thumbnail.png"
    image.save(str(path))
    dialog.set_thumbnail(path)
    assert not dialog.page_thumbnail.pixmap().isNull()
    dialog.save_page()
    wait(app, lambda: dialog.worker is None)
    assert dialog.listing.currentData()["description"] == "New description"
    assert "Mod page saved" in dialog.status.text()
    assert dialog.manifest is None
    dialog.close()
    app.processEvents()


def test_card_loads_a_verified_thumbnail_asynchronously_and_reuses_disk_cache(
    app, tmp_path, monkeypatch
):
    monkeypatch.setattr(MainWindow, "startup", lambda self: None)
    monkeypatch.setattr(MainWindow, "background_sync", lambda self: None)
    image = QImage(384, 384, QImage.Format.Format_RGB32)
    image.fill(0x334477)
    path = tmp_path / "image.png"
    image.save(str(path))
    data = path.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    hits = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append((self.path, self.headers.get("Authorization"), self.headers.get("Cookie")))
            if self.path == "/thumbnail.png":
                content, kind = data, "image/png"
            else:
                content = json.dumps(
                    {
                        "url": origin + "/thumbnail.png",
                        "headers": {
                            "Authorization": "must-not-forward",
                            "Cookie": "must-not-forward",
                        },
                    }
                ).encode()
                kind = "application/json"
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, *args):
            pass

    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{http.server_port}"
    base = origin + "/signalpoint"
    state = State(tmp_path / "state")
    mod = {
        "id": "example",
        "name": "Example",
        "description": "Description beside the image.",
        "modules": [],
        "thumbnail": {"id": "a" * 32, "sha256": sha, "size": len(data)},
    }
    state.set("catalog", [mod])
    cache = state.directory / "thumbnails" / (sha + ".png")
    cache.parent.mkdir()
    cache.write_bytes(b"corrupt cached image")
    window = MainWindow(state, api_url=base)
    try:
        window.tabs.setCurrentIndex(1)
        window.show()
        card = window.cards["example"][0]
        wait(app, lambda: not card.thumbnail.pixmap().isNull())
        assert 192 <= card.thumbnail.height() <= card.height() < 260
        window.on_progress({"mod_id": "example", "state": "Downloading", "done": 1, "total": 2})
        app.processEvents()
        assert card.bar.geometry().top() >= card.thumbnail.geometry().bottom()
        assert len(hits) == 2 and all(auth is None and cookie is None for _, auth, cookie in hits)
        assert cache.read_bytes() == data
        loader = ThumbnailLoader(Api(base), state.directory)
        assert loader.request(mod) is not None
        assert len(hits) == 2
        assert (
            loader.request({"id": "example", "thumbnail": {"id": "../bad", "sha256": "../bad"}})
            is None
        )
        loader.stop()
    finally:
        window.close()
        app.processEvents()
        http.shutdown()
        http.server_close()
        thread.join(timeout=5)
