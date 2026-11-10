"""Small native desktop interface: Installed, Browse, and folder-based publishing."""

import argparse
import os
import sys
import time
from pathlib import Path

from PySide6.QtCore import (
    QBuffer,
    QByteArray,
    QEvent,
    QIODevice,
    QLockFile,
    QRect,
    Qt,
    QThread,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QFont,
    QFontDatabase,
    QIcon,
    QImageReader,
    QPainter,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTabBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from signalpoint import __file__ as PACKAGE_FILE
from signalpoint import __version__
from signalpoint.api import DEFAULT_API_URL, Api, ApiError, TransferControl
from signalpoint.game import detect_games, installed_modules, validate_game
from signalpoint.manager import Manager
from signalpoint.manifest import OFFICIAL, ModError, scan
from signalpoint.state import State
from signalpoint.thumbnails import ThumbnailLoader

ASSETS = Path(PACKAGE_FILE).parent / "assets"

STYLE = """
QWidget { background: #111123; color: #e9e7f3; font-size: 13px; }
QMainWindow, QDialog { background: #111123; }
QLabel { background: transparent; }
QLabel#brand { font-size: 23px; font-weight: 650; color: #f2effb; }
QLabel#muted { color: #aaa7bf; }
QFrame#header { border-bottom: 1px solid #31304b; }
QFrame#card { background: #1b1b32; border: 1px solid #35334f; border-radius: 4px; }
QLabel#thumbnail { background: #111123; border: 1px solid #35334f; color: #77738f; }
QLabel#title { font-size: 17px; font-weight: 600; }
QPushButton { background: #29283f; border: 1px solid #514b6b; border-radius: 3px;
              padding: 7px 14px; min-height: 19px; }
QPushButton:hover { background: #3b3157; border-color: #a48bce; }
QPushButton:disabled { color: #77738f; border-color: #34314b; background: #222137; }
QPushButton#launch { background: #63459b; border: 1px solid #a58acb;
                    font-size: 15px; font-weight: 600; padding: 13px 26px; }
QPushButton#launch:hover { background: #7855b4; }
QPushButton#primary { background: #63459b; border-color: #a58acb; }
QLineEdit, QComboBox, QPlainTextEdit, QListWidget { background: #0d0e1e;
    border: 1px solid #46405e; border-radius: 3px; padding: 7px;
    selection-background-color: #59417d; }
QLineEdit:focus, QPlainTextEdit:focus { border-color: #ae8edc; }
QTabBar::tab { padding: 12px 22px; color: #aaa7bf; border-bottom: 2px solid transparent; }
QTabBar::tab:selected { color: #f0eafb; border-bottom: 2px solid #ad8bdf; }
QScrollArea { border: none; }
QProgressBar { border: none; background: #0b0c1a; border-radius: 2px; min-height: 6px;
               max-height: 6px; color: transparent; }
QProgressBar::chunk { background: #61b8ed; border-radius: 2px; }
QScrollBar:vertical { background: #111123; width: 9px; }
QScrollBar::handle:vertical { background: #51476b; min-height: 32px; border-radius: 3px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QDialogButtonBox { background: transparent; }
"""


def size(value):
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{value:.0f} B"
        value /= 1024


def label(text, name=""):
    result = QLabel(str(text))
    result.setTextFormat(Qt.TextFormat.PlainText)
    if name:
        result.setObjectName(name)
    return result


class Task(QThread):
    result = Signal(object)
    error = Signal(str)
    progress = Signal(object)

    def __init__(self, function, parent=None):
        super().__init__(parent)
        self.function = function
        self.last_report = 0
        self.last_state = ""

    def report(self, event):
        now = time.monotonic()
        state = (event.get("mod_id"), event.get("state"))
        if now - self.last_report >= 0.08 or state != self.last_state:
            self.last_report, self.last_state = now, state
            self.progress.emit(event)

    def run(self):
        try:
            self.result.emit(self.function(self.report))
        except (ModError, OSError, ValueError) as exc:
            self.error.emit(str(exc))
        except Exception as exc:
            import traceback

            traceback.print_exc()
            self.error.emit(
                f"Operation failed ({type(exc).__name__}). See the console for details."
            )


class FolderList(QListWidget):
    folders = Signal(object)
    invalid = Signal(str)

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setMinimumHeight(90)
        self.setMaximumHeight(135)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [Path(url.toLocalFile()) for url in event.mimeData().urls() if url.isLocalFile()]
        if not paths or any(not p.is_dir() for p in paths):
            self.invalid.emit(
                "Drop module folders containing SubModule.xml. Archives are unsupported."
            )
            return
        self.folders.emit(paths)
        event.acceptProposedAction()


class PublisherDialog(QDialog):
    published = Signal()

    def __init__(self, api, parent):
        super().__init__(parent)
        self.api, self.paths, self.manifest = api, [], None
        self.worker, self.control, self.closing = None, TransferControl(), False
        self.setWindowTitle("SignalPoint · Publisher")
        self.resize(800, 760)
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        title = QHBoxLayout()
        title.addWidget(label("Publish a mod", "title"))
        title.addStretch()
        admin = QPushButton("Web administration")
        admin.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(api.base + "/admin")))
        title.addWidget(admin)
        layout.addLayout(title)
        self.login_box = QWidget()
        form = QFormLayout(self.login_box)
        self.username, self.password = QLineEdit(), QLineEdit()
        self.username.setPlaceholderText("Publisher username")
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Username", self.username)
        form.addRow("Password", self.password)
        sign_in = QPushButton("Sign in")
        sign_in.clicked.connect(self.login)
        self.password.returnPressed.connect(self.login)
        form.addRow(sign_in)
        layout.addWidget(self.login_box)
        self.content = QWidget()
        body = QVBoxLayout(self.content)
        body.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        row.addWidget(label("Listing"))
        self.listing = QComboBox()
        self.listing.currentIndexChanged.connect(self.select_listing)
        row.addWidget(self.listing, 1)
        body.addLayout(row)
        self.tabs = QTabWidget()
        body.addWidget(self.tabs, 1)
        upload_tab = QWidget()
        self.tabs.addTab(upload_tab, "Mod files")
        self.page = QWidget()
        self.tabs.addTab(self.page, "Mod page")
        page = QVBoxLayout(self.page)
        image_row = QHBoxLayout()
        self.page_thumbnail = label("No thumbnail", "thumbnail")
        self.page_thumbnail.setFixedSize(128, 128)
        self.page_thumbnail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page_thumbnail.setScaledContents(True)
        image_row.addWidget(self.page_thumbnail)
        image_actions = QVBoxLayout()
        choose_image = QPushButton("Choose thumbnail")
        choose_image.clicked.connect(self.choose_thumbnail)
        clear_image = QPushButton("Remove thumbnail")
        clear_image.clicked.connect(self.clear_thumbnail)
        image_actions.addWidget(choose_image)
        image_actions.addWidget(clear_image)
        image_actions.addWidget(label("PNG, JPEG or WebP · square crop", "muted"))
        image_actions.addStretch()
        image_row.addLayout(image_actions)
        image_row.addStretch()
        page.addLayout(image_row)
        page.addWidget(label("Description"))
        self.description = QPlainTextEdit()
        self.description.setPlaceholderText(
            "Tell players about your mod, requirements and features."
        )
        page.addWidget(self.description, 1)
        self.save_page_button = QPushButton("Save mod page")
        self.save_page_button.setObjectName("primary")
        self.save_page_button.clicked.connect(self.save_page)
        page.addWidget(self.save_page_button)
        self.thumbnail_data, self.thumbnail_removed = None, False
        self.page.setEnabled(False)
        body = QVBoxLayout(upload_tab)
        body.addWidget(label("Drag module folders below, or use Open Folder.", "muted"))
        self.folders = FolderList()
        self.folders.folders.connect(self.add_folders)
        self.folders.invalid.connect(self.show_error)
        body.addWidget(self.folders)
        buttons = QHBoxLayout()
        self.open_button = QPushButton("Open Folder")
        self.open_button.clicked.connect(self.open_folder)
        self.remove_button = QPushButton("Remove selected")
        self.remove_button.clicked.connect(self.remove_folder)
        self.scan_button = QPushButton("Scan & Review")
        self.scan_button.clicked.connect(self.scan_folders)
        buttons.addWidget(self.open_button)
        buttons.addWidget(self.remove_button)
        buttons.addStretch()
        buttons.addWidget(self.scan_button)
        body.addLayout(buttons)
        self.review = QPlainTextEdit()
        self.review.setReadOnly(True)
        self.review.setPlaceholderText(
            "Detected modules, versions, dependencies, and file changes appear here."
        )
        body.addWidget(self.review, 1)
        self.notes = QPlainTextEdit()
        self.notes.setMaximumHeight(85)
        self.notes.setPlaceholderText("Change notes for players")
        body.addWidget(self.notes)
        self.confirm = QPushButton("Confirm Upload")
        self.confirm.setObjectName("primary")
        self.confirm.setEnabled(False)
        self.confirm.clicked.connect(self.upload)
        body.addWidget(self.confirm)
        notice = label(
            f"Publishing confirms your distribution rights and acceptance of the "
            f'<a style="color:#a8cdf5" href="{DEFAULT_API_URL}/terms/">Terms of Service</a>. '
            f'<a style="color:#a8cdf5" href="{DEFAULT_API_URL}/privacy/">Privacy Policy</a>',
            "muted",
        )
        notice.setTextFormat(Qt.TextFormat.RichText)
        notice.setWordWrap(True)
        notice.setOpenExternalLinks(True)
        body.addWidget(notice)
        layout.addWidget(self.content, 1)
        self.content.hide()
        self.bar = QProgressBar()
        self.bar.hide()
        layout.addWidget(self.bar)
        self.status = label("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self.reject)
        layout.addWidget(self.close_button, alignment=Qt.AlignmentFlag.AlignRight)
        self.tabs.currentChanged.connect(self.page_tab_changed)
        if self.api.token:
            self.login_box.hide()
            self.run(lambda _: self.api.publisher_mods(), self.show_mods)

    def show_error(self, message):
        self.status.setText(message)

    def page_tab_changed(self, index):
        if self.worker or not self.listing.currentData():
            return
        self.status.setText(
            "Edit the description and thumbnail, then Save mod page."
            if index == 1
            else "Select your module folder."
        )

    def run(self, work, success):
        if self.worker:
            return
        self.control = TransferControl()
        worker = self.worker = Task(work, self)
        self.confirm.setEnabled(False)
        self.scan_button.setEnabled(False)
        self.open_button.setEnabled(False)
        self.remove_button.setEnabled(False)
        self.folders.setEnabled(False)
        self.listing.setEnabled(False)
        self.page.setEnabled(False)
        self.bar.setRange(0, 0)
        self.bar.show()

        def finish():
            self.worker = None
            self.scan_button.setEnabled(True)
            self.open_button.setEnabled(True)
            self.remove_button.setEnabled(True)
            self.folders.setEnabled(True)
            self.listing.setEnabled(True)
            self.page.setEnabled(bool(self.listing.currentData()))
            self.bar.hide()
            self.confirm.setEnabled(self.manifest is not None)
            if self.closing:
                QTimer.singleShot(0, self.reject)

        def done(value):
            finish()
            if not self.closing:
                success(value)

        def failed(message):
            finish()
            self.show_error(message)
            if not self.api.token:
                self.login_box.show()

        worker.result.connect(done)
        worker.error.connect(failed)
        worker.progress.connect(self.update_progress)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def update_progress(self, value):
        total, done = value.get("total", 0), value.get("done", 0)
        self.bar.setRange(0, 1000 if total else 0)
        if total:
            self.bar.setValue(min(1000, int(done / total * 1000)))
        self.status.setText(f"{value['state']} · {size(done)} / {size(total)}")

    def login(self):
        if self.worker:
            return
        username, password = self.username.text(), self.password.text()
        self.password.clear()

        def work(_):
            self.api.login(username, password)
            return self.api.publisher_mods()

        self.run(work, self.show_mods)

    def show_mods(self, mods):
        self.login_box.hide()
        self.content.show()
        self.listing.clear()
        for mod in mods:
            self.listing.addItem(mod["name"], mod)
        self.status.setText(
            "Select your module folder."
            if mods
            else "No listings assigned. Ask the administrator to assign your mod."
        )

    def invalidate(self):
        self.manifest = None
        self.confirm.setEnabled(False)

    def select_listing(self):
        self.invalidate()
        mod = self.listing.currentData()
        self.description.setPlainText(mod.get("description", "") if mod else "")
        self.thumbnail_data, self.thumbnail_removed = None, False
        self.page_thumbnail.clear()
        self.page_thumbnail.setText("No thumbnail")
        self.page.setEnabled(bool(mod))
        if mod and mod.get("thumbnail") and not self.worker:

            def loaded(data):
                self.page_thumbnail.setPixmap(QPixmap.fromImage(self.image_from_bytes(data)))

            self.run(
                lambda _: self.api.thumbnail(mod["id"], mod["thumbnail"], authenticated=True),
                loaded,
            )

    @staticmethod
    def image_from_bytes(data):
        from PySide6.QtGui import QImage

        return QImage.fromData(data, "PNG")

    def choose_thumbnail(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose mod thumbnail", "", "Images (*.png *.jpg *.jpeg *.webp)"
        )
        if path:
            self.set_thumbnail(Path(path))

    def set_thumbnail(self, path):
        try:
            if path.stat().st_size > 32 * 1024**2:
                raise ModError("Choose an image smaller than 32 MiB.")
            reader = QImageReader(str(path))
            reader.setAutoTransform(True)
            dimensions = reader.size()
            if dimensions.width() * dimensions.height() > 32 * 1024**2:
                raise ModError("Choose an image smaller than 32 megapixels.")
            image = reader.read()
            if image.isNull():
                raise ModError("Choose a valid PNG, JPEG or WebP image.")
            image = image.scaled(
                384,
                384,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            image = image.copy((image.width() - 384) // 2, (image.height() - 384) // 2, 384, 384)
            data = QByteArray()
            buffer = QBuffer(data)
            buffer.open(QIODevice.OpenModeFlag.WriteOnly)
            if not image.save(buffer, "PNG"):
                raise ModError("Thumbnail could not be prepared.")
            buffer.close()
            self.thumbnail_data, self.thumbnail_removed = bytes(data), False
            self.page_thumbnail.setPixmap(QPixmap.fromImage(image))
        except (ModError, OSError) as exc:
            self.show_error(str(exc))

    def clear_thumbnail(self):
        self.thumbnail_data, self.thumbnail_removed = None, True
        self.page_thumbnail.clear()
        self.page_thumbnail.setText("No thumbnail")

    def save_page(self):
        mod = self.listing.currentData()
        if not mod or self.worker:
            return
        description = self.description.toPlainText()
        if len(description) > 10000:
            self.show_error("Description must be 10,000 characters or fewer.")
            return
        thumbnail, removed = self.thumbnail_data, self.thumbnail_removed

        def saved(updated):
            self.listing.blockSignals(True)
            self.listing.setItemData(self.listing.currentIndex(), updated)
            self.listing.blockSignals(False)
            self.thumbnail_data, self.thumbnail_removed = None, False
            self.status.setText("Mod page saved. No module upload or version change was needed.")
            self.published.emit()

        self.run(
            lambda _: self.api.update_page(
                mod["id"], description, thumbnail, remove_thumbnail=removed
            ),
            saved,
        )

    def open_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Choose module folder")
        if path:
            self.add_folders([Path(path)])

    def add_folders(self, paths):
        for path in paths:
            if path not in self.paths:
                self.paths.append(path)
                self.folders.addItem(str(path))
        self.invalidate()

    def remove_folder(self):
        index = self.folders.currentRow()
        if index >= 0:
            self.paths.pop(index)
            self.folders.takeItem(index)
            self.invalidate()

    def scan_folders(self):
        mod = self.listing.currentData()
        if not mod or not self.paths:
            self.show_error("Choose an assigned listing and at least one module folder.")
            return
        paths = list(self.paths)

        def work(report):
            def update(event):
                self.control.check()
                report(event)

            manifest = scan(paths, update)
            try:
                old = self.api.manifest(mod["id"])["manifest"]
            except ApiError as exc:
                if exc.status != 404:
                    raise
                old = {"files": []}
            return manifest, old

        def reviewed(value):
            manifest, old = value
            self.manifest = manifest
            included = {m["id"].casefold() for m in manifest["modules"]}
            lines = ["REVIEW UPLOAD", "", mod["name"], ""]
            for module in manifest["modules"]:
                lines += [
                    f"{module['name']}  {module['version']}",
                    f"  ID: {module['id']}",
                    f"  Destination: Modules/{module['folder']}",
                ]
                for dependency in module["dependencies"]:
                    kind = "included" if dependency["id"].casefold() in included else "external"
                    optional = ", optional" if dependency["optional"] else ""
                    lines.append(
                        f"  Requires: {dependency['id']} {dependency['version']} ({kind}{optional})"
                    )
                lines.append("")
            previous = {f["path"]: f for f in old["files"]}
            current = {f["path"]: f for f in manifest["files"]}
            changed = [
                f
                for f in manifest["files"]
                if f["path"] not in previous or previous[f["path"]]["sha256"] != f["sha256"]
            ]
            removed = set(previous) - set(current)
            dlls = [f["path"] for f in manifest["files"] if f["path"].lower().endswith(".dll")]
            lines += [
                f"Files: {len(current):,} · Installed size: "
                f"{size(sum(f['size'] for f in current.values()))}",
                f"Changed/new: {len(changed):,} · Removed: {len(removed):,}",
                f"Estimated upload: {size(sum(f['size'] for f in changed))}",
                "",
                "DLLs:",
                *dlls[:100],
                "",
                "New/changed files:",
                *[f["path"] for f in changed[:100]],
                "",
                "Removed files:",
                *sorted(removed)[:100],
                "",
                "Successful upload publishes directly. "
                "Existing installations update automatically.",
                "Research report: no report yet for this build.",
            ]
            self.review.setPlainText("\n".join(lines))
            self.confirm.setEnabled(True)
            self.status.setText("Review the module folders and content, then Confirm Upload.")

        self.run(work, reviewed)

    def upload(self):
        if not self.manifest:
            return
        mod, manifest, paths, notes = (
            self.listing.currentData(),
            self.manifest,
            list(self.paths),
            self.notes.toPlainText(),
        )

        def work(report):
            return self.api.upload(mod["id"], manifest, paths, notes, self.control, report)

        def done(value):
            self.invalidate()
            self.status.setText("Published. Players can now install this mod.")
            self.published.emit()

        self.run(work, done)

    def reject(self):
        if self.worker:
            self.closing = True
            self.control.cancel()
            self.status.setText("Cancelling…")
            return
        super().reject()

    def closeEvent(self, event):
        if self.worker:
            self.reject()
            event.ignore()
        else:
            super().closeEvent(event)


class CardThumbnail(QLabel):
    """Fill the card's left edge without stretching the publisher's artwork."""

    def __init__(self):
        super().__init__("No thumbnail")
        self.setObjectName("thumbnail")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedWidth(224)
        self.setMinimumHeight(192)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Ignored)

    def paintEvent(self, event):
        image = self.pixmap()
        if image.isNull():
            super().paintEvent(event)
            return
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#111123"))
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        scale = max(self.width() / image.width(), self.height() / image.height())
        width, height = int(self.width() / scale), int(self.height() / scale)
        source = QRect((image.width() - width) // 2, (image.height() - height) // 2, width, height)
        painter.drawPixmap(self.rect(), image, source)


class ModCard(QFrame):
    def __init__(self, mod, parent):
        super().__init__()
        self.mod, self.window = mod, parent
        self.setObjectName("card")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        body = QVBoxLayout(self)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        row = QHBoxLayout()
        row.setSpacing(0)
        self.thumbnail = CardThumbnail()
        image = parent.thumbnails.request(mod)
        if image is not None:
            self.thumbnail.setPixmap(image)
        row.addWidget(self.thumbnail)
        information = QVBoxLayout()
        information.setContentsMargins(20, 16, 16, 16)
        information.setSpacing(7)
        title = label(mod["name"], "title")
        title.setWordWrap(True)
        information.addWidget(title)
        self.primary = QPushButton("Install")
        self.primary.clicked.connect(lambda: parent.install(mod["id"]))
        versions = " · ".join(f"{m['name']} {m['version']}" for m in mod.get("modules", []))
        self.subtitle = label(versions, "muted")
        self.subtitle.setWordWrap(True)
        information.addWidget(self.subtitle)
        if mod.get("description"):
            description = label(
                mod["description"][:260].replace("\n", " ")
                + ("…" if len(mod["description"]) > 260 else "")
            )
            description.setWordWrap(True)
            description.setMaximumHeight(60)
            information.addWidget(description)
        information.addStretch()
        self.status = label("", "muted")
        self.status.setWordWrap(True)
        information.addWidget(self.status)
        row.addLayout(information, 1)
        self.bar = QProgressBar()
        self.bar.hide()
        bottom = QHBoxLayout()
        bottom.addStretch()
        self.pause = QPushButton("Pause")
        self.pause.clicked.connect(parent.pause)
        self.pause.hide()
        bottom.addWidget(self.pause)
        self.cancel = QPushButton("Cancel")
        self.cancel.clicked.connect(parent.cancel)
        self.cancel.hide()
        bottom.addWidget(self.cancel)
        self.details = QPushButton("Details / Reports")
        self.details.clicked.connect(lambda: parent.details(mod))
        actions = QVBoxLayout()
        actions.setContentsMargins(0, 16, 16, 16)
        actions.setSpacing(6)
        actions.addWidget(self.primary)
        actions.addWidget(self.details)
        actions.addStretch()
        maintenance = QHBoxLayout()
        self.repair = QPushButton("Repair")
        self.repair.clicked.connect(lambda: parent.install(mod["id"], repair=True))
        maintenance.addWidget(self.repair)
        self.remove = QPushButton("Uninstall")
        self.remove.clicked.connect(lambda: parent.uninstall(mod["id"]))
        maintenance.addWidget(self.remove)
        actions.addLayout(maintenance)
        row.addLayout(actions)
        body.addLayout(row)
        body.addWidget(self.bar)
        body.addLayout(bottom)
        self.fit_height()

    def fit_height(self):
        self.layout().invalidate()
        self.setMinimumHeight(self.layout().minimumSize().height() + 2)

    def update_state(self, receipt, event, busy):
        active = event and event["state"] not in {"Ready", "Failed", "Cancelled"}
        installed = receipt is not None
        if receipt:
            versions = " · ".join(
                f"{m['name']} {m['version']}" for m in receipt["manifest"]["modules"]
            )
            self.subtitle.setText(versions)
        self.primary.setText("Installed" if installed else "Install")
        self.primary.setEnabled(not installed and not busy)
        if not installed and event and event["state"] == "Failed":
            self.primary.setText("Retry")
        self.repair.setVisible(installed)
        self.remove.setVisible(installed)
        self.repair.setEnabled(not busy)
        self.remove.setEnabled(not busy)
        self.details.setEnabled(not busy)
        if event:
            total, done = event.get("total", 0), event.get("done", 0)
            self.bar.setVisible(bool(active))
            self.bar.setRange(0, 1000 if total else 0)
            if total:
                self.bar.setValue(min(1000, int(done / total * 1000)))
            self.status.setText(event.get("text", event["state"]))
            self.pause.setVisible(bool(active and event["state"] in {"Downloading", "Paused"}))
            self.cancel.setVisible(
                bool(active and event["state"] not in {"Installing", "Uninstalling"})
            )
        else:
            self.bar.hide()
            self.pause.hide()
            self.cancel.hide()
            self.status.setText("Installed" if installed else size(self.mod.get("size", 0)))
        self.pause.setText(
            "Resume" if self.window.control and self.window.control.paused else "Pause"
        )
        self.fit_height()


class MainWindow(QMainWindow):
    def __init__(self, state, *, api_url=None, game=None):
        super().__init__()
        self.state = state
        self.api = Api(api_url or DEFAULT_API_URL)
        self.thumbnails = ThumbnailLoader(self.api, state.directory, self)
        self.thumbnails.ready.connect(self.thumbnail_ready)
        self.game = Path(game or state.get("game")) if game or state.get("game") else None
        source = state.get("catalog_source", state.get("api", self.api.base))
        self.catalog = state.get("catalog", []) if source == self.api.base else []
        self.active, self.control, self.workers = None, None, set()
        self.events, self.cards, self.metrics = {}, {}, {}
        self.launch_requested, self.publisher_open, self.closing = False, False, False
        self.last_check = 0
        self.setWindowTitle("SignalPoint")
        self.setWindowIcon(QIcon(str(ASSETS / "signalpoint.png")))
        self.resize(1120, 780)
        self.setMinimumSize(840, 560)
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        header = QFrame()
        header.setObjectName("header")
        row = QHBoxLayout(header)
        row.setContentsMargins(24, 16, 24, 16)
        badge = label("")
        badge.setFixedSize(36, 36)
        badge.setPixmap(QIcon(str(ASSETS / "signalpoint-full.png")).pixmap(36, 36))
        row.addWidget(badge)
        row.addSpacing(6)
        row.addWidget(label("SignalPoint", "brand"))
        row.addSpacing(24)
        self.tabs = QTabBar()
        self.tabs.addTab("Installed")
        self.tabs.addTab("Browse")
        self.tabs.currentChanged.connect(self.render)
        row.addWidget(self.tabs)
        row.addStretch()
        self.publisher_button = QPushButton("Publisher")
        self.publisher_button.clicked.connect(self.publisher)
        row.addWidget(self.publisher_button)
        self.settings_button = QPushButton("Settings")
        self.settings_button.clicked.connect(self.settings)
        row.addWidget(self.settings_button)
        self.launch = QPushButton("Start Launcher")
        self.launch.setObjectName("launch")
        self.launch.clicked.connect(lambda: self.sync(launch=True))
        row.addWidget(self.launch)
        layout.addWidget(header)
        tools = QHBoxLayout()
        tools.setContentsMargins(24, 18, 24, 14)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search mods")
        self.search.textChanged.connect(self.render)
        tools.addWidget(self.search, 1)
        self.refresh = QPushButton("Check for updates")
        self.refresh.clicked.connect(lambda: self.sync())
        tools.addWidget(self.refresh)
        layout.addLayout(tools)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        layout.addWidget(self.scroll, 1)
        footer = QHBoxLayout()
        footer.setContentsMargins(24, 12, 24, 14)
        self.installation = label("Finding Bannerlord…", "muted")
        footer.addWidget(self.installation, 1)
        self.status = label("", "muted")
        self.status.setWordWrap(True)
        footer.addWidget(self.status, 1)
        layout.addLayout(footer)
        self.timer = QTimer(self)
        self.timer.setInterval(60000)
        self.timer.timeout.connect(self.background_sync)
        self.timer.start()
        self.render()
        QTimer.singleShot(0, self.startup)

    def run(self, work, success, failure=None):
        if self.active:
            return
        self.control = TransferControl()
        worker = self.active = Task(work, self)
        self.workers.add(worker)
        self.settings_button.setEnabled(False)
        self.publisher_button.setEnabled(False)
        self.refresh.setEnabled(False)
        self.update_cards()

        def finish():
            self.active = None
            self.settings_button.setEnabled(True)
            self.publisher_button.setEnabled(True)
            self.refresh.setEnabled(True)

        def done(value):
            finish()
            success(value)
            if self.closing:
                self.close()

        def failed(message):
            finish()
            for key, event in list(self.events.items()):
                if event["state"] != "Ready":
                    self.events[key] = {"state": "Failed", "text": message}
            self.status.setText(message)
            self.update_cards()
            if failure:
                failure(message)
            else:
                self.launch_requested = False
            if self.closing:
                self.close()

        worker.result.connect(done)
        worker.error.connect(failed)
        worker.progress.connect(self.on_progress)
        worker.finished.connect(lambda: self.workers.discard(worker))
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def startup(self):
        if self.game:
            self.installation.setText(str(self.game))
            self.sync()
            return

        def detected(found):
            self.state.set("detected", [{"path": str(i.path), "store": i.store} for i in found])
            if found:
                self.game = found[0].path
                self.state.set("game", str(self.game))
                self.installation.setText(f"{found[0].store} · {self.game}")
            else:
                self.installation.setText("Choose your Bannerlord folder in Settings")
            self.render()
            self.sync()

        self.run(lambda _: detect_games(), detected)

    def manager(self):
        if self.game is None:
            raise ModError("Choose your Bannerlord installation in Settings first.")
        return Manager(self.state, self.game, self.api)

    def background_sync(self):
        if not self.active and not self.publisher_open:
            self.sync()

    def event(self, event):
        if (
            event.type() == QEvent.Type.WindowActivate
            and hasattr(self, "timer")
            and time.monotonic() - self.last_check > 60
        ):
            QTimer.singleShot(0, self.background_sync)
        return super().event(event)

    def sync(self, *, launch=False):
        if self.publisher_open:
            return
        if launch and self.game is None:
            self.settings()
            return
        self.launch_requested = self.launch_requested or launch
        if self.active:
            self.status.setText("Launcher will open after updates finish.")
            return
        self.status.setText("Checking for updates…")

        def work(report):
            mods = self.api.catalog()
            self.state.set("catalog", mods)
            self.state.set("catalog_source", self.api.base)
            if self.game:
                manager = self.manager()
                manager.recover()
                receipts = self.state.installs(self.game)
                for mod in mods:
                    receipt = receipts.get(mod["id"])
                    if receipt and receipt["build_id"] != mod["current"]:
                        report({"mod_id": mod["id"], "state": "Queued", "done": 0, "total": 0})
                for mod in mods:
                    receipt = self.state.installs(self.game).get(mod["id"])
                    if receipt and receipt["build_id"] != mod["current"]:
                        self.control.check()
                        report(
                            {
                                "mod_id": mod["id"],
                                "state": "Preparing update",
                                "done": 0,
                                "total": 0,
                            }
                        )
                        value = self.api.manifest(mod["id"])
                        manager.install(
                            mod["id"],
                            mod["name"],
                            value["build_id"],
                            value["manifest"],
                            control=self.control,
                            progress=lambda e, mod=mod: report({**e, "mod_id": mod["id"]}),
                        )
            return mods

        def done(mods):
            self.catalog = mods
            self.last_check = time.monotonic()
            self.status.setText("Up to date · Checked " + time.strftime("%H:%M"))
            self.render()
            if self.launch_requested:
                self.open_launcher()

        def failed(message):
            if self.launch_requested:
                self.launch_requested = False
                result = QMessageBox.question(
                    self,
                    "Updates could not finish",
                    message + "\n\nLaunch the existing installation instead?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if result == QMessageBox.StandardButton.Yes:
                    self.open_launcher()

        self.run(work, done, failed)

    def open_launcher(self):
        self.launch_requested = False
        hint = next(
            (
                install["store"]
                for install in self.state.get("detected", [])
                if self.game and Path(install["path"]).resolve() == self.game.resolve()
            ),
            None,
        )
        self.run(
            lambda report: self.manager().launch(
                store_hint=hint, progress=report, control=self.control
            ),
            lambda _: self.status.setText("Bannerlord launch requested."),
        )

    def install(self, mod_id, *, repair=False):
        if self.active:
            return
        mod = next((m for m in self.catalog if m["id"] == mod_id), None)
        if not mod:
            return
        if (
            repair
            and QMessageBox.question(
                self, "Repair mod", "Replace missing or modified managed files?"
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        self.events[mod_id] = {"state": "Preparing download", "done": 0, "total": 0}

        def work(report):
            value = self.api.manifest(mod_id)
            return self.manager().install(
                mod_id,
                mod["name"],
                value["build_id"],
                value["manifest"],
                repair=repair,
                control=self.control,
                progress=lambda e: report({**e, "mod_id": mod_id}),
            )

        def done(_):
            self.events[mod_id] = {"state": "Ready", "text": "Installed · Up to date"}
            self.status.setText(mod["name"] + " installed.")
            self.render()
            if self.launch_requested:
                self.sync(launch=True)

        self.run(work, done)

    def uninstall(self, mod_id):
        if (
            self.active
            or QMessageBox.question(
                self,
                "Uninstall mod",
                "Remove this mod's managed files? "
                "Unrelated and locally modified files are preserved.",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return

        def work(report):
            return self.manager().uninstall(mod_id, lambda e: report({**e, "mod_id": mod_id}))

        def done(preserved):
            self.events.pop(mod_id, None)
            self.status.setText(
                "Uninstalled."
                + (f" Preserved {len(preserved)} modified files." if preserved else "")
            )
            self.render()

        self.run(work, done)

    def on_progress(self, event):
        mod_id = event.get("mod_id")
        if not mod_id:
            self.status.setText(event["state"])
            return
        now = time.monotonic()
        done, total = event.get("done", 0), event.get("total", 0)
        state = event["state"]
        if state == "Downloading" and total:
            before = self.metrics.get(mod_id, (now, done, 0.0))
            elapsed = now - before[0]
            instant = max(0, done - before[1]) / elapsed if elapsed > 0.05 else before[2]
            speed = instant if not before[2] else 0.3 * instant + 0.7 * before[2]
            self.metrics[mod_id] = (now, done, speed)
            eta = f" · {max(0, int((total - done) / speed))}s left" if speed > 0 else ""
            event["text"] = (
                f"Downloading · {done / total:.0%} · {size(done)} / {size(total)} "
                f"· {size(speed)}/s{eta}"
            )
        else:
            event["text"] = state
        self.events[mod_id] = event
        self.status.setText(event["text"])
        self.update_cards()

    def pause(self):
        if self.control:
            self.control.pause(not self.control.paused)
            for event in self.events.values():
                if event["state"] in {"Downloading", "Paused"}:
                    event["state"] = "Paused" if self.control.paused else "Downloading"
                    event["text"] = event["state"]
            self.update_cards()

    def cancel(self):
        if self.control:
            self.control.cancel()
            self.launch_requested = False

    def update_cards(self):
        receipts = self.state.installs(self.game) if self.game else {}
        for mod_id, cards in self.cards.items():
            for card in cards:
                card.update_state(receipts.get(mod_id), self.events.get(mod_id), bool(self.active))

    def thumbnail_ready(self, mod_id, identity, image):
        for card in self.cards.get(mod_id, []):
            if (card.mod.get("thumbnail") or {}).get("id") == identity:
                card.thumbnail.setPixmap(image)

    def render(self):
        if not hasattr(self, "scroll"):
            return
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(24, 4, 24, 18)
        layout.setSpacing(12)
        self.cards = {}
        receipts = self.state.installs(self.game) if self.game else {}
        mods = {m["id"]: m for m in self.catalog}
        if self.tabs.currentIndex() == 0:
            mods = {
                key: mods.get(
                    key,
                    {
                        "id": key,
                        "name": value["name"],
                        "modules": value["manifest"]["modules"],
                        "description": "Currently unavailable in the catalog.",
                    },
                )
                for key, value in receipts.items()
            }
        query = self.search.text().casefold()
        shown = 0
        for mod in mods.values():
            if query not in (mod["name"] + " " + mod.get("description", "")).casefold():
                continue
            card = ModCard(mod, self)
            self.cards.setdefault(mod["id"], []).append(card)
            layout.addWidget(card)
            shown += 1
        if self.tabs.currentIndex() == 0 and self.game:
            managed = {
                m["id"].casefold() for r in receipts.values() for m in r["manifest"]["modules"]
            }
            try:
                for module in installed_modules(self.game):
                    if (
                        module["id"].casefold() in managed | OFFICIAL
                        or query not in module["name"].casefold()
                    ):
                        continue
                    frame = QFrame()
                    frame.setObjectName("card")
                    row = QVBoxLayout(frame)
                    row.setContentsMargins(18, 14, 18, 14)
                    row.addWidget(label(module["name"] + "  " + module["version"], "title"))
                    row.addWidget(
                        label(
                            "Installed outside SignalPoint · Managed by its original installer",
                            "muted",
                        )
                    )
                    layout.addWidget(frame)
                    shown += 1
            except OSError:
                pass
        if not shown:
            empty = label(
                "No mods installed through SignalPoint yet. Find a mod in Browse."
                if self.tabs.currentIndex() == 0
                else "No published mods found.",
                "muted",
            )
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(empty, 1)
        layout.addStretch()
        self.scroll.setWidget(content)
        self.update_cards()

    def settings(self):
        if self.active:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("SignalPoint settings")
        dialog.resize(700, 260)
        form = QFormLayout(dialog)
        path = QLineEdit(str(self.game or ""))
        detected = QComboBox()
        detected.addItem("Select a detected installation…", "")
        for install in self.state.get("detected", []):
            detected.addItem(install["store"] + " · " + install["path"], install["path"])
        detected.currentIndexChanged.connect(
            lambda _: path.setText(detected.currentData()) if detected.currentData() else None
        )
        form.addRow("Detected installs", detected)
        browse = QPushButton("Open Folder")

        def choose():
            selected = QFileDialog.getExistingDirectory(dialog, "Choose Bannerlord installation")
            if selected:
                path.setText(selected)

        browse.clicked.connect(choose)
        row = QHBoxLayout()
        row.addWidget(path, 1)
        row.addWidget(browse)
        form.addRow("Bannerlord", row)
        policies = label(
            f'<a style="color:#a8cdf5" href="{DEFAULT_API_URL}/privacy/">Privacy</a>'
            f' · <a style="color:#a8cdf5" href="{DEFAULT_API_URL}/terms/">Terms of Service</a>'
        )
        policies.setTextFormat(Qt.TextFormat.RichText)
        policies.setOpenExternalLinks(True)
        form.addRow(policies)
        error = label("")
        error.setWordWrap(True)
        form.addRow(error)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        form.addRow(buttons)
        buttons.rejected.connect(dialog.reject)

        def save():
            try:
                selected = validate_game(Path(path.text())) if path.text() else None
            except (ModError, OSError) as exc:
                error.setText(str(exc))
                return
            self.game = selected
            self.state.set("game", str(selected) if selected else None)
            self.events = {}
            self.installation.setText(
                str(selected) if selected else "Choose your Bannerlord folder"
            )
            dialog.accept()
            self.render()
            self.sync()

        buttons.accepted.connect(save)
        dialog.exec()

    def publisher(self):
        if self.active:
            return
        self.publisher_open = True
        dialog = PublisherDialog(self.api, self)
        dialog.exec()
        self.publisher_open = False
        self.sync()

    def details(self, mod):
        def show(value):
            metadata, reports = value
            dialog = QDialog(self)
            dialog.setWindowTitle(mod["name"])
            dialog.resize(720, 620)
            layout = QVBoxLayout(dialog)
            text = QPlainTextEdit()
            text.setReadOnly(True)
            lines = [mod["name"], "", mod.get("description", ""), "", "Modules:"]
            for module in metadata["manifest"]["modules"]:
                lines.append(f"{module['name']} {module['version']}")
                lines.extend(
                    f"  Requires {dep['id']} {dep['version']}" for dep in module["dependencies"]
                )
            lines += ["", "Change notes:", mod.get("notes", ""), "", "Research reports:"]
            receipt = self.state.installs(self.game).get(mod["id"]) if self.game else None
            report_build = receipt["build_id"] if receipt else metadata["build_id"]
            lines.append("For the installed build:" if receipt else "For the current build:")
            matching = [r for r in reports["reports"] if r.get("build_id") == report_build]
            if not matching:
                lines.append("No report yet for this build.")
            for report in matching:
                lines += [str(report.get("verdict", "")), str(report.get("report", ""))]
            text.setPlainText("\n".join(lines))
            layout.addWidget(text)
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
            buttons.rejected.connect(dialog.reject)
            layout.addWidget(buttons)
            dialog.exec()

        self.run(
            lambda _: (
                self.api.manifest(mod["id"]),
                self.api.request("GET", f"mods/{mod['id']}/reports"),
            ),
            show,
        )

    def closeEvent(self, event):
        if self.active:
            self.closing = True
            self.cancel()
            self.status.setText("Finishing the current operation before closing…")
            event.ignore()
            return
        if self.workers:
            QTimer.singleShot(100, self.close)
            event.ignore()
            return
        self.thumbnails.stop()
        super().closeEvent(event)


def main():
    parser = argparse.ArgumentParser(description="SignalPoint Bannerlord mod manager")
    parser.add_argument("--api-url")
    parser.add_argument("--game", type=Path)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--screenshot", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--smoke-test", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    app = QApplication(sys.argv[:1])
    app.setApplicationName("SignalPoint")
    app.setApplicationVersion(__version__)
    app.setWindowIcon(QIcon(str(ASSETS / "signalpoint.png")))
    # Qt's offscreen Windows plugin has no system font discovery. This also makes
    # headless UI checks use the same Windows font as the normal application.
    if os.name == "nt" and not QFontDatabase.families():
        font = Path(os.environ.get("SystemRoot", "C:/Windows")) / "Fonts/segoeui.ttf"
        QFontDatabase.addApplicationFont(str(font))
    app.setFont(QFont("Segoe UI", 10))
    app.setStyleSheet(STYLE)
    if args.smoke_test:
        import tempfile

        # Exercise bundled Qt, TLS certificates, and SQLite without network,
        # game writes, publisher credentials, or normal user preferences.
        probe = QProgressBar()
        probe.setValue(50)
        if probe.value() != 50:
            raise RuntimeError("Qt initialization failed")
        if app.windowIcon().isNull():
            raise RuntimeError("Bundled application icon is missing")
        Api("https://example.invalid/signalpoint").http.close()
        with tempfile.TemporaryDirectory(prefix="signalpoint-smoke-") as temporary:
            probe_state = State(Path(temporary))
            probe_state.set("probe", True)
            if not probe_state.get("probe"):
                raise RuntimeError("Local database initialization failed")
        return
    directory = (
        args.data_dir or Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "SignalPoint"
    )
    state = State(directory)
    instance = QLockFile(str(directory / "desktop.lock"))
    if not instance.tryLock(100):
        QMessageBox.information(None, "SignalPoint", "SignalPoint is already running.")
        return
    window = MainWindow(state, api_url=args.api_url, game=args.game)
    window.show()
    if args.screenshot:
        capture_deadline = time.monotonic() + 20

        def capture():
            if time.monotonic() < capture_deadline and (
                window.active or window.thumbnails.active or window.thumbnails.queue
            ):
                QTimer.singleShot(100, capture)
                return
            args.screenshot.parent.mkdir(parents=True, exist_ok=True)
            window.grab().save(str(args.screenshot))
            window.close()

        QTimer.singleShot(2500, capture)
    app.exec()


if __name__ == "__main__":
    main()
