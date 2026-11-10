import hashlib
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QFontDatabase, QImage, QPainter, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from test_modules import module

from signalpoint.api import DEFAULT_API_URL, Api
from signalpoint.desktop import (
    STYLE,
    CardThumbnail,
    MainWindow,
    PublisherDialog,
)
from signalpoint.manifest import MAX_UPLOAD_FILE_SIZE, scan
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


def test_publisher_selects_multiple_folders_and_removes_a_selection(app, tmp_path, monkeypatch):
    class Publisher:
        base = "http://127.0.0.1/signalpoint"
        token = ""

        def publisher_manifest(self, mod):
            return {"build_id": None, "manifest": {"schema": 1, "modules": [], "files": []}}

    paths = [module(tmp_path / name, name) for name in ("Alpha", "Beta", "Gamma", "Omega")]
    calls = []

    def choose(owner, directory):
        calls.append((owner, directory))
        return paths

    monkeypatch.setattr("signalpoint.desktop.choose_folders", choose)
    dialog = PublisherDialog(Publisher(), None)
    dialog.show_mods([{"id": "example", "name": "Example"}])
    dialog.show()
    wait(app, lambda: dialog.worker is None)
    dialog.open_folder()
    assert dialog.paths == paths
    assert calls == [(int(dialog.winId()), None)]
    assert dialog.folders.count() == 4
    # Cancelling the native picker leaves the existing review intact.
    dialog.manifest = {"reviewed": True}
    dialog.confirm.setEnabled(True)
    monkeypatch.setattr("signalpoint.desktop.choose_folders", lambda *_: [])
    dialog.open_folder()
    assert dialog.paths == paths and dialog.manifest is not None
    # Removing a Shift-selected range keeps the remaining row/path in sync and
    # invalidates the earlier upload review. It does not delete local folders.
    dialog.manifest = {"reviewed": True}
    dialog.confirm.setEnabled(True)
    view = dialog.folders
    QTest.mouseClick(
        view.viewport(), Qt.MouseButton.LeftButton, pos=view.visualItemRect(view.item(0)).center()
    )
    QTest.mouseClick(
        view.viewport(),
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.ShiftModifier,
        view.visualItemRect(view.item(2)).center(),
    )
    assert len(view.selectedItems()) == 3
    QTest.mouseClick(
        view.viewport(),
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.ControlModifier,
        view.visualItemRect(view.item(1)).center(),
    )
    assert len(view.selectedItems()) == 2
    dialog.remove_button.click()
    assert dialog.paths == [paths[1], paths[3]]
    assert [view.item(i).text() for i in range(view.count())] == ["Beta", "Omega"]
    assert dialog.manifest is None and not dialog.confirm.isEnabled()
    assert all(p.is_dir() for p in paths)
    dialog.close()
    app.processEvents()


def test_folder_review_and_trusted_publication(app, tmp_path):
    class Publisher:
        base = "http://127.0.0.1/signalpoint"
        token = "test-session"

        def publisher_mods(self):
            return [{"id": "example", "name": "Example"}]

        def publisher_manifest(self, mod):
            return {"build_id": None, "manifest": {"schema": 1, "modules": [], "files": []}}

        def upload_modules(
            self, mod, base_build, manifest, removed, paths, notes, control, progress
        ):
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


def test_publisher_loads_online_modules_and_changes_only_the_chosen_module(
    app, tmp_path, monkeypatch
):
    first = module(tmp_path / "Warlords", "Warlords", "v1.3")
    second = module(tmp_path / "Armory", "Armory", "v1.8")
    baseline = scan([first, second])

    class Publisher:
        base = "http://127.0.0.1/signalpoint"
        token = "session"

        def publisher_mods(self):
            return [{"id": "warlords", "name": "Warlords"}]

        def publisher_manifest(self, mod):
            return {"build_id": "a" * 64, "manifest": baseline}

    dialog = PublisherDialog(Publisher(), None)
    dialog.show()
    wait(app, lambda: dialog.worker is None)
    assert len(dialog.module_rows) == 2 and dialog.paths == []
    assert all(entry["published"] and entry["source"] is None for entry in dialog.module_rows)
    card = dialog.folders.itemWidget(dialog.folders.item(0))
    assert card.name.text() == "Armory" and card.version.text() == "v1.8"
    replacement = module(tmp_path / "new" / "Armory", "Armory", "v1.9")
    calls = []

    def choose(owner, directory, *, multiple):
        calls.append((owner, directory, multiple))
        return [replacement]

    monkeypatch.setattr("signalpoint.desktop.choose_folders", choose)
    card.change_button.click()
    assert calls == [(int(dialog.winId()), None, False)]
    assert dialog.paths == [replacement]
    assert dialog.module_rows[1]["source"] is None
    assert dialog.folders.itemWidget(dialog.folders.item(0)).version.text() == "v1.9"
    dialog.scan_folders()
    wait(app, lambda: dialog.worker is None)
    assert dialog.confirm.isEnabled()
    assert [m["id"] for m in dialog.changed_manifest["modules"]] == ["Armory"]
    assert [m["id"] for m in dialog.manifest["modules"]] == ["Armory", "Warlords"]
    assert "1 modules kept online" in dialog.review.toPlainText()
    # Cancel and a wrong module ID leave the reviewed selection intact.
    reviewed = dialog.manifest
    monkeypatch.setattr("signalpoint.desktop.choose_folders", lambda *_, **__: [])
    dialog.change_folder("Armory")
    assert dialog.manifest is reviewed
    monkeypatch.setattr("signalpoint.desktop.choose_folders", lambda *_, **__: [first])
    dialog.change_folder("Armory")
    assert "module ID Armory" in dialog.status.text() and dialog.manifest is reviewed
    # Remove a published companion, then reset the edit to the server's cards.
    dialog.folders.itemWidget(dialog.folders.item(1)).remove_button.click()
    assert dialog.removed_modules == ["Warlords"]
    assert not dialog.confirm.isEnabled()
    dialog.reset_button.click()
    wait(app, lambda: dialog.worker is None)
    assert len(dialog.module_rows) == 2 and dialog.paths == [] and dialog.removed_modules == []
    # Removal alone needs no module folders and can still be reviewed.
    dialog.folders.itemWidget(dialog.folders.item(1)).remove_button.click()
    dialog.scan_folders()
    wait(app, lambda: dialog.worker is None)
    assert dialog.confirm.isEnabled() and dialog.changed_manifest is None
    assert [m["id"] for m in dialog.manifest["modules"]] == ["Armory"]
    dialog.close()
    app.processEvents()


def test_failed_size_check_invalidates_an_earlier_checkout(app, tmp_path, monkeypatch):

    class Publisher:
        base = "http://127.0.0.1/signalpoint"
        token = "test-session"

        def publisher_mods(self):
            return [{"id": "example", "name": "Example"}]

        def publisher_manifest(self, mod):
            return {"build_id": None, "manifest": {"schema": 1, "modules": [], "files": []}}

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
    app.processEvents()
    bar_top = card.bar.mapTo(card, QPoint(0, 0)).y()
    assert card.bar.height() >= 10
    assert bar_top - card.thumbnail.geometry().bottom() >= 12
    assert card.height() - (bar_top + card.bar.height()) >= 16
    assert card.cancel.mapTo(card, QPoint(0, card.cancel.height())).y() < bar_top
    assert card.primary.isHidden()
    window.on_progress({"mod_id": "example", "state": "Verifying", "done": 1000, "total": 1000})
    assert not card.bar.isHidden()
    assert card.status.text() == "Verifying"
    window.on_progress({"mod_id": "example", "state": "Ready", "done": 1000, "total": 1000})
    assert card.bar.isHidden()
    assert card.progress_area.isHidden() and not card.primary.isHidden()
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
        assert card.thumbnail.width() == card.thumbnail.height() == 224
        for phase in ("Checking installed files", "Downloading", "Ready"):
            window.on_progress({"mod_id": "example", "state": phase, "done": 1, "total": 2})
            app.processEvents()
            assert card.thumbnail.width() == card.thumbnail.height() == 224
            if phase != "Ready":
                assert (
                    card.bar.mapTo(card, QPoint(0, 0)).y()
                    >= card.thumbnail.geometry().bottom() + 12
                )
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


def test_square_thumbnail_preserves_the_top_and_bottom_of_portrait_artwork(app):
    image = QImage(200, 400, QImage.Format.Format_RGB32)
    image.fill(QColor("green"))
    painter = QPainter(image)
    painter.fillRect(0, 0, 200, 64, QColor("red"))
    painter.fillRect(0, 336, 200, 64, QColor("blue"))
    painter.end()
    thumbnail = CardThumbnail()
    thumbnail.setPixmap(QPixmap.fromImage(image))
    thumbnail.show()
    app.processEvents()
    result = thumbnail.grab().toImage().scaled(224, 224)
    assert result.pixelColor(112, 5) == QColor("red")
    assert result.pixelColor(112, 218) == QColor("blue")
    assert result.pixelColor(5, 112) == QColor("#111123")
    thumbnail.close()
