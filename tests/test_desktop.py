import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication
from test_modules import module

from signalpoint.api import DEFAULT_API_URL, ApiError
from signalpoint.desktop import STYLE, MainWindow, PublisherDialog
from signalpoint.state import State


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
