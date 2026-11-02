import errno
import json
import sys
import time
from pathlib import Path

from PySide6.QtCore import QObject, QRectF, QThread, QUrl, Qt, Signal, Slot
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QFontDatabase,
    QIcon,
    QKeySequence,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QStyle,
    QVBoxLayout,
    QWidget,
)

import copy_core
from copy_core import format_size


APP_NAME = "Copy Missing Files"

# Bump with every change and add a matching entry to CHANGELOG.md.
VERSION = "1.3.0"

CONFIG_DIR = (
    Path.home()
    / "Library"
    / "Application Support"
    / APP_NAME
)

CONFIG_FILE = CONFIG_DIR / "settings.json"

MAX_LOG_ENTRIES = 200000

ACCENT = "#2f6fde"

LOG_PREFIXES = {
    "info": "",
    "missing": "+ ",
    "copied": "✓ ",
    "skipped": "– ",
    "error": "✕ ",
}

LOG_FILTERS = [
    ("All activity", None),
    ("Copied", {"copied"}),
    ("To copy", {"missing"}),
    ("Problems", {"skipped", "error"}),
]


STYLESHEET = """
QMainWindow {
    background: palette(window);
}

QLabel#title {
    font-size: 22px;
    font-weight: 700;
}

QLabel#muted, QLabel#path, QLabel#tileCaption {
    color: palette(placeholder-text);
}

QLabel#versionPill {
    color: palette(placeholder-text);
    border: 1px solid palette(mid);
    border-radius: 9px;
    padding: 2px 9px;
    font-size: 11px;
}

QFrame#card {
    background: palette(base);
    border: 1px solid palette(mid);
    border-radius: 12px;
}

QFrame#card[dropTarget="true"] {
    border: 2px dashed #2f6fde;
}

QLabel#role {
    color: palette(placeholder-text);
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 1px;
}

QLabel#folderName {
    font-size: 15px;
    font-weight: 600;
}

QLabel#info[warning="true"] {
    color: #d6453d;
}

QLabel#arrow {
    color: palette(placeholder-text);
    font-size: 22px;
}

QLabel#sectionTitle {
    font-weight: 700;
}

QLabel#statusTitle {
    font-size: 15px;
    font-weight: 600;
}

QPushButton {
    padding: 6px 14px;
}

QPushButton#primary {
    padding: 9px 22px;
    font-weight: 600;
    border-radius: 8px;
    color: white;
    background: #2f6fde;
    border: none;
}

QPushButton#primary:hover {
    background: #2a63c7;
}

QPushButton#primary:disabled {
    background: #9db7e6;
}

QProgressBar {
    border: none;
    border-radius: 4px;
    background: palette(mid);
    max-height: 8px;
}

QProgressBar::chunk {
    border-radius: 4px;
    background: #2f6fde;
}

QFrame#divider {
    background: palette(mid);
    max-height: 1px;
    border: none;
}

QLabel#tileValue {
    font-size: 20px;
    font-weight: 700;
}

QPlainTextEdit {
    background: palette(base);
    border: 1px solid palette(mid);
    border-radius: 10px;
    padding: 6px;
}
"""


def make_icon_pixmap(size):
    """Draw the app icon: a blue rounded square with a music note."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)

    s = float(size)
    margin = s * 0.08
    rect = QRectF(margin, margin, s - 2 * margin, s - 2 * margin)

    gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
    gradient.setColorAt(0, QColor("#5b97ff"))
    gradient.setColorAt(1, QColor("#2445c9"))

    painter.setPen(Qt.NoPen)
    painter.setBrush(gradient)
    painter.drawRoundedRect(rect, s * 0.2, s * 0.2)

    note = QPainterPath()
    note.addEllipse(QRectF(s * 0.27, s * 0.59, s * 0.19, s * 0.14))
    note.addEllipse(QRectF(s * 0.55, s * 0.53, s * 0.19, s * 0.14))
    note.addRect(QRectF(s * 0.415, s * 0.30, s * 0.045, s * 0.37))
    note.addRect(QRectF(s * 0.695, s * 0.24, s * 0.045, s * 0.37))
    note.moveTo(s * 0.415, s * 0.30)
    note.lineTo(s * 0.74, s * 0.24)
    note.lineTo(s * 0.74, s * 0.33)
    note.lineTo(s * 0.415, s * 0.39)
    note.closeSubpath()
    note.setFillRule(Qt.WindingFill)

    painter.setBrush(QColor("white"))
    painter.drawPath(note)
    painter.end()

    return pixmap


def make_app_icon():
    icon = QIcon()

    for size in (16, 32, 64, 128, 256, 512):
        icon.addPixmap(make_icon_pixmap(size))

    return icon


def format_duration(seconds):
    if seconds < 60:
        return "less than a minute"

    minutes = round(seconds / 60)

    if minutes < 60:
        return f"about {minutes} min"

    return f"about {minutes // 60} h {minutes % 60} min"


def plural(count, word):
    return f"{count:,} {word}" + ("" if count == 1 else "s")


class CopyWorker(QObject):
    log = Signal(list)
    progress = Signal(dict)
    finished = Signal(dict)

    def __init__(self, source, destination, preview=False):
        super().__init__()
        self.source = source
        self.destination = destination
        self.preview = preview
        self.cancel_requested = False

        self.pending_log = []
        self.last_emit = 0.0
        self.copy_started = None
        self.item_bytes = 0

        self.stats = {
            "preview": preview,
            "phase": "scanning",
            "scanned": 0,
            "to_copy": 0,
            "total_bytes": 0,
            "done_bytes": 0,
            "processed": 0,
            "copied": 0,
            "copied_bytes": 0,
            "existing": 0,
            "skipped": 0,
            "failed": 0,
            "current": "",
            "copy_seconds": 0.0,
            "message": "",
        }

    def cancel(self):
        self.cancel_requested = True

    def is_cancelled(self):
        return self.cancel_requested

    def add_log(self, kind, text):
        self.pending_log.append((kind, text))

    def emit_progress(self, force=False):
        # Updates are batched so a library of 100,000 files doesn't
        # flood the UI thread with signals.
        now = time.monotonic()

        if not force and now - self.last_emit < 0.1:
            return

        self.last_emit = now

        if self.copy_started is not None:
            self.stats["copy_seconds"] = now - self.copy_started

        if self.pending_log:
            self.log.emit(self.pending_log)
            self.pending_log = []

        self.progress.emit(dict(self.stats))

    def on_scanned(self, count):
        self.stats["scanned"] = count
        self.emit_progress()

    def on_bytes(self, count):
        self.stats["done_bytes"] += count
        self.item_bytes += count
        self.emit_progress()

    def stop(self, message):
        self.stats["phase"] = "stopped"
        self.stats["message"] = message
        self.add_log("error", message)

    @Slot()
    def run(self):
        stats = self.stats

        try:
            self.add_log("info", "Scanning " + str(self.source) + " …")
            self.emit_progress(force=True)

            plan = copy_core.build_plan(
                self.source,
                self.destination,
                self.is_cancelled,
                self.on_scanned,
            )
            self.record_plan(plan)

            if self.preview:
                for item in plan.items:
                    size = "link" if item.is_symlink else format_size(item.size)
                    self.add_log(
                        "missing",
                        str(item.relative) + "  (" + size + ")",
                    )
            else:
                self.copy_items(plan)

        except copy_core.Cancelled:
            stats["phase"] = "cancelled"

        except Exception as error:
            stats["failed"] += 1
            self.stop("Unexpected error: " + str(error))

        if stats["phase"] in ("scanning", "copying"):
            stats["phase"] = "done"

        stats["current"] = ""
        self.emit_progress(force=True)
        self.finished.emit(dict(stats))

    def record_plan(self, plan):
        stats = self.stats
        stats["to_copy"] = len(plan.items)
        stats["total_bytes"] = plan.total_bytes
        stats["existing"] = plan.existing
        stats["skipped"] = len(plan.skipped)
        stats["failed"] = len(plan.errors)

        for relative, reason in plan.skipped:
            self.add_log("skipped", str(relative) + " — " + reason)

        for path, reason in plan.errors:
            self.add_log("error", "Could not read " + str(path) + " — " + reason)

        self.add_log(
            "info",
            "Found "
            + plural(len(plan.items), "missing file")
            + " ("
            + format_size(plan.total_bytes)
            + "), "
            + f"{plan.existing:,}"
            + " already in the destination.",
        )

    def copy_items(self, plan):
        stats = self.stats

        free = copy_core.free_space(self.destination)

        if free is not None and plan.total_bytes > free:
            self.stop(
                "Not enough space on the destination drive: needs "
                + format_size(plan.total_bytes)
                + ", only "
                + format_size(free)
                + " free. Nothing was copied."
            )
            return

        if not plan.items:
            return

        if not self.destination.exists():
            self.destination.mkdir()
            self.add_log("info", "Created " + str(self.destination))

        stats["phase"] = "copying"
        self.copy_started = time.monotonic()

        for item in plan.items:
            if self.cancel_requested:
                raise copy_core.Cancelled()

            stats["current"] = str(item.relative)
            self.item_bytes = 0

            try:
                if copy_core.copy_item(
                    item,
                    self.destination,
                    self.on_bytes,
                    self.is_cancelled,
                ):
                    stats["copied"] += 1
                    stats["copied_bytes"] += item.size
                    self.add_log("copied", str(item.relative))
                else:
                    stats["existing"] += 1

            except copy_core.Cancelled:
                stats["done_bytes"] -= self.item_bytes
                raise

            except copy_core.DestinationGone:
                self.stop(
                    "The destination folder is no longer available. "
                    "Is the drive still connected?"
                )
                return

            except OSError as error:
                if error.errno == errno.ENOSPC:
                    stats["failed"] += 1
                    self.stop("The destination drive is full.")
                    return

                if copy_core.is_missing_error(error):
                    if not self.destination.is_dir():
                        self.stop(
                            "The destination folder is no longer "
                            "available. Is the drive still connected?"
                        )
                        return

                    stats["skipped"] += 1
                    self.add_log(
                        "skipped",
                        str(item.relative) + " — no longer available",
                    )
                else:
                    stats["failed"] += 1
                    self.add_log(
                        "error",
                        str(item.relative) + " — " + (error.strerror or str(error)),
                    )

            stats["done_bytes"] += item.size - self.item_bytes
            stats["processed"] += 1
            self.emit_progress()


class ElidedLabel(QLabel):
    """A label that shortens long text in the middle instead of growing."""

    def __init__(self, text=""):
        super().__init__()
        self.full_text = text
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(40)
        self.update_text()

    def set_full_text(self, text):
        self.full_text = text
        self.setToolTip(text)
        self.update_text()

    def update_text(self):
        self.setText(
            self.fontMetrics().elidedText(
                self.full_text,
                Qt.ElideMiddle,
                max(self.width(), 40),
            )
        )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_text()


class FolderCard(QFrame):
    changed = Signal()

    def __init__(self, role, dialog_title, icon, describe):
        super().__init__()
        self.setObjectName("card")
        self.setAcceptDrops(True)
        self.setProperty("dropTarget", False)

        self.dialog_title = dialog_title
        self.describe = describe
        self.path = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        role_label = QLabel(role.upper())
        role_label.setObjectName("role")
        layout.addWidget(role_label)

        row = QHBoxLayout()
        row.setSpacing(10)

        icon_label = QLabel()
        icon_label.setPixmap(icon.pixmap(32, 32))
        icon_label.setFixedSize(32, 32)
        row.addWidget(icon_label, 0, Qt.AlignTop)

        text_column = QVBoxLayout()
        text_column.setSpacing(2)

        self.name_label = ElidedLabel()
        self.name_label.setObjectName("folderName")
        text_column.addWidget(self.name_label)

        self.path_label = ElidedLabel()
        self.path_label.setObjectName("path")
        text_column.addWidget(self.path_label)

        row.addLayout(text_column, 1)
        layout.addLayout(row)

        bottom = QHBoxLayout()

        self.info_label = QLabel()
        self.info_label.setObjectName("info")
        bottom.addWidget(self.info_label, 1)

        self.choose_button = QPushButton("Choose…")
        self.choose_button.clicked.connect(self.browse)
        bottom.addWidget(self.choose_button)

        layout.addLayout(bottom)

        self.refresh()

    def text(self):
        return self.path

    def set_text(self, path):
        self.path = path.strip()
        self.refresh()

    def set_locked(self, locked):
        self.choose_button.setEnabled(not locked)
        self.setAcceptDrops(not locked)

    def refresh(self):
        if self.path:
            self.name_label.set_full_text(Path(self.path).name or self.path)
            self.path_label.set_full_text(self.path)
            info, warning = self.describe(self.path)
        else:
            self.name_label.set_full_text("No folder selected")
            self.path_label.set_full_text("Choose a folder or drop one here")
            info, warning = "", False

        self.info_label.setText(info)
        self.info_label.setProperty("warning", warning)
        self.restyle(self.info_label)

    def restyle(self, widget):
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    def browse(self):
        if self.path and Path(self.path).is_dir():
            initial_dir = self.path
        else:
            initial_dir = str(Path.home())

        folder = QFileDialog.getExistingDirectory(
            self,
            self.dialog_title,
            initial_dir,
        )

        if folder:
            self.set_text(folder)
            self.changed.emit()

    def dropped_folder(self, event):
        urls = event.mimeData().urls()

        if urls and urls[0].isLocalFile():
            path = urls[0].toLocalFile()

            if Path(path).is_dir():
                return path

        return None

    def set_drop_highlight(self, on):
        self.setProperty("dropTarget", on)
        self.restyle(self)

    def dragEnterEvent(self, event):
        if self.dropped_folder(event):
            event.acceptProposedAction()
            self.set_drop_highlight(True)

    def dragLeaveEvent(self, event):
        self.set_drop_highlight(False)

    def dropEvent(self, event):
        self.set_drop_highlight(False)
        path = self.dropped_folder(event)

        if path:
            self.set_text(path)
            self.changed.emit()


class StatTile(QWidget):
    def __init__(self, caption, color=None):
        super().__init__()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.value = QLabel("–")
        self.value.setObjectName("tileValue")

        if color:
            self.value.setStyleSheet("color: " + color + ";")

        layout.addWidget(self.value)

        caption_label = QLabel(caption)
        caption_label.setObjectName("tileCaption")
        layout.addWidget(caption_label)

    def set_value(self, number):
        self.value.setText("–" if number is None else f"{number:,}")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME + " " + VERSION)
        self.resize(880, 780)
        self.setMinimumSize(760, 640)

        self.thread = None
        self.worker = None
        self.log_entries = []

        self.build_gui()
        self.load_settings()

    def build_gui(self):
        central = QWidget()
        self.setCentralWidget(central)

        main = QVBoxLayout(central)
        main.setContentsMargins(24, 20, 24, 20)
        main.setSpacing(16)

        main.addLayout(self.build_header())
        main.addLayout(self.build_folders())
        main.addLayout(self.build_actions())
        main.addWidget(self.build_progress())
        main.addLayout(self.build_activity_header())

        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setLineWrapMode(QPlainTextEdit.NoWrap)

        log_font = QFontDatabase.systemFont(QFontDatabase.FixedFont)
        log_font.setPointSize(11)
        self.log_text.setFont(log_font)

        main.addWidget(self.log_text, 1)

        self.show_idle()

    def build_header(self):
        header = QHBoxLayout()
        header.setSpacing(14)

        icon = QLabel()
        icon.setPixmap(make_icon_pixmap(96).scaled(
            48, 48, Qt.KeepAspectRatio, Qt.SmoothTransformation
        ))
        header.addWidget(icon)

        text = QVBoxLayout()
        text.setSpacing(2)

        title = QLabel(APP_NAME)
        title.setObjectName("title")
        text.addWidget(title)

        subtitle = QLabel(
            "Adds files that are missing from the destination. "
            "Nothing there is ever overwritten or deleted."
        )
        subtitle.setObjectName("muted")
        text.addWidget(subtitle)

        header.addLayout(text, 1)

        version = QLabel("v" + VERSION)
        version.setObjectName("versionPill")
        header.addWidget(version, 0, Qt.AlignTop)

        return header

    def build_folders(self):
        row = QHBoxLayout()
        row.setSpacing(10)

        style = self.style()

        self.source_field = FolderCard(
            "Source",
            "Select Source Folder",
            style.standardIcon(QStyle.SP_DirIcon),
            self.describe_source,
        )
        self.source_field.changed.connect(self.on_folders_changed)
        row.addWidget(self.source_field, 1)

        arrow = QLabel("→")
        arrow.setObjectName("arrow")
        row.addWidget(arrow)

        self.destination_field = FolderCard(
            "Destination",
            "Select Destination Folder",
            style.standardIcon(QStyle.SP_DriveHDIcon),
            self.describe_destination,
        )
        self.destination_field.changed.connect(self.on_folders_changed)
        row.addWidget(self.destination_field, 1)

        return row

    def build_actions(self):
        row = QHBoxLayout()
        row.setSpacing(10)

        hint = QLabel("Tip: Preview shows what's missing without copying.")
        hint.setObjectName("muted")
        row.addWidget(hint, 1)

        self.preview_button = QPushButton("Preview")
        self.preview_button.clicked.connect(lambda: self.start(preview=True))
        row.addWidget(self.preview_button)

        self.start_button = QPushButton("Copy Missing Files")
        self.start_button.setObjectName("primary")
        self.start_button.setCursor(Qt.PointingHandCursor)
        self.start_button.setShortcut(QKeySequence("Ctrl+Return"))
        self.start_button.clicked.connect(lambda: self.start(preview=False))
        row.addWidget(self.start_button)

        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setShortcut(QKeySequence("Ctrl+."))
        self.cancel_button.clicked.connect(self.cancel_copy)
        self.cancel_button.hide()
        row.addWidget(self.cancel_button)

        return row

    def build_progress(self):
        card = QFrame()
        card.setObjectName("card")

        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(8)

        top = QHBoxLayout()

        self.status_title = QLabel()
        self.status_title.setObjectName("statusTitle")
        top.addWidget(self.status_title, 1)

        self.percent_label = QLabel()
        self.percent_label.setObjectName("muted")
        top.addWidget(self.percent_label)

        layout.addLayout(top)

        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setRange(0, 1000)
        layout.addWidget(self.progress_bar)

        self.detail_label = QLabel()
        self.detail_label.setObjectName("muted")
        layout.addWidget(self.detail_label)

        self.current_label = ElidedLabel()
        self.current_label.setObjectName("path")
        layout.addWidget(self.current_label)

        divider = QFrame()
        divider.setObjectName("divider")
        divider.setFixedHeight(1)
        layout.addSpacing(4)
        layout.addWidget(divider)
        layout.addSpacing(4)

        tiles = QHBoxLayout()
        tiles.setSpacing(28)

        self.to_copy_tile = StatTile("To copy", ACCENT)
        self.copied_tile = StatTile("Copied", "#2e9d57")
        self.existing_tile = StatTile("Already there")
        self.skipped_tile = StatTile("Skipped", "#c98a12")
        self.failed_tile = StatTile("Failed", "#d6453d")

        for tile in (
            self.to_copy_tile,
            self.copied_tile,
            self.existing_tile,
            self.skipped_tile,
            self.failed_tile,
        ):
            tiles.addWidget(tile)

        tiles.addStretch(1)
        layout.addLayout(tiles)

        return card

    def build_activity_header(self):
        row = QHBoxLayout()
        row.setSpacing(8)

        title = QLabel("Activity")
        title.setObjectName("sectionTitle")
        row.addWidget(title)
        row.addStretch(1)

        self.filter_box = QComboBox()

        for label, _kinds in LOG_FILTERS:
            self.filter_box.addItem(label)

        self.filter_box.currentIndexChanged.connect(self.render_log)
        row.addWidget(self.filter_box)

        copy_log_button = QPushButton("Copy Log")
        copy_log_button.clicked.connect(self.copy_log)
        row.addWidget(copy_log_button)

        self.reveal_button = QPushButton("Show in Finder")
        self.reveal_button.clicked.connect(self.reveal_destination)
        row.addWidget(self.reveal_button)

        return row

    def describe_source(self, path):
        volume = copy_core.unmounted_volume(path)

        if volume is not None:
            return "Drive \"" + volume.name + "\" is not connected", True

        if not Path(path).is_dir():
            return "Folder not found", True

        return "", False

    def describe_destination(self, path):
        volume = copy_core.unmounted_volume(path)

        if volume is not None:
            return "Drive \"" + volume.name + "\" is not connected", True

        destination = Path(path)
        free = copy_core.free_space(destination)
        free_text = "" if free is None else format_size(free) + " free"

        if not destination.exists():
            if not destination.parent.is_dir():
                return "Folder not found", True

            return "Will be created · " + free_text, False

        return free_text, False

    def changeEvent(self, event):
        # Drives come and go while the window is open, so recheck them
        # whenever the window is brought back to the front.
        if event.type() == event.Type.ActivationChange and self.isActiveWindow():
            self.source_field.refresh()
            self.destination_field.refresh()

        super().changeEvent(event)

    def on_folders_changed(self):
        self.save_settings()
        self.show_idle()

    def load_settings(self):
        try:
            with CONFIG_FILE.open("r", encoding="utf-8") as file:
                settings = json.load(file)
        except (OSError, ValueError):
            return

        if not isinstance(settings, dict):
            return

        source = settings.get("source", "")
        destination = settings.get("destination", "")

        if isinstance(source, str):
            self.source_field.set_text(source)

        if isinstance(destination, str):
            self.destination_field.set_text(destination)

    def save_settings(self):
        try:
            CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)

            settings = {
                "source": self.source_field.text(),
                "destination": self.destination_field.text(),
            }

            with CONFIG_FILE.open("w", encoding="utf-8") as file:
                json.dump(settings, file, indent=4)

        except OSError as error:
            self.append_log([("error", "Could not save settings: " + str(error))])

    def current_filter(self):
        return LOG_FILTERS[self.filter_box.currentIndex()][1]

    def format_entry(self, entry):
        kind, text = entry
        return LOG_PREFIXES[kind] + text

    @Slot(list)
    def append_log(self, entries):
        self.log_entries.extend(entries)

        if len(self.log_entries) > MAX_LOG_ENTRIES:
            del self.log_entries[: len(self.log_entries) - MAX_LOG_ENTRIES]

        kinds = self.current_filter()
        lines = [
            self.format_entry(entry)
            for entry in entries
            if kinds is None or entry[0] in kinds
        ]

        if lines:
            self.log_text.appendPlainText("\n".join(lines))

    def render_log(self):
        kinds = self.current_filter()
        self.log_text.setPlainText(
            "\n".join(
                self.format_entry(entry)
                for entry in self.log_entries
                if kinds is None or entry[0] in kinds
            )
        )
        self.log_text.verticalScrollBar().setValue(
            self.log_text.verticalScrollBar().maximum()
        )

    def copy_log(self):
        QApplication.clipboard().setText(self.log_text.toPlainText())

    def reveal_destination(self):
        destination = Path(self.destination_field.text())
        target = destination if destination.is_dir() else copy_core.nearest_existing(destination)

        if target is not None and self.destination_field.text():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    def set_tiles(self, stats=None):
        if stats is None:
            for tile in (
                self.to_copy_tile,
                self.copied_tile,
                self.existing_tile,
                self.skipped_tile,
                self.failed_tile,
            ):
                tile.set_value(None)
            return

        self.to_copy_tile.set_value(stats["to_copy"])
        self.copied_tile.set_value(None if stats["preview"] else stats["copied"])
        self.existing_tile.set_value(stats["existing"])
        self.skipped_tile.set_value(stats["skipped"])
        self.failed_tile.set_value(stats["failed"])

    def show_idle(self):
        self.status_title.setText("Ready")
        self.percent_label.setText("")
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setValue(0)
        self.detail_label.setText(
            "Choose the two folders, then Preview or Copy Missing Files."
        )
        self.current_label.set_full_text("")
        self.set_tiles()

    def set_running(self, running):
        self.start_button.setVisible(not running)
        self.preview_button.setVisible(not running)
        self.cancel_button.setVisible(running)
        self.cancel_button.setEnabled(running)
        self.source_field.set_locked(running)
        self.destination_field.set_locked(running)

    def start(self, preview):
        if self.thread is not None:
            return

        try:
            source, destination = copy_core.check_folders(
                self.source_field.text(),
                self.destination_field.text(),
            )
        except copy_core.FolderError as error:
            QMessageBox.critical(self, error.title, error.message)
            return

        if not preview and not destination.exists():
            answer = QMessageBox.question(
                self,
                "Create Destination Folder?",
                "The destination folder doesn't exist yet:\n\n"
                + str(destination)
                + "\n\nCreate it and copy into it?",
            )

            if answer != QMessageBox.Yes:
                return

        self.save_settings()

        self.log_entries = []
        self.log_text.clear()
        self.set_tiles()
        self.set_running(True)

        self.thread = QThread(self)
        self.worker = CopyWorker(source, destination, preview)
        self.worker.moveToThread(self.thread)

        self.thread.started.connect(self.worker.run)
        self.worker.log.connect(self.append_log)
        self.worker.progress.connect(self.update_progress)
        self.worker.finished.connect(self.finish_copy)
        self.worker.finished.connect(self.thread.quit)
        self.thread.finished.connect(self.cleanup_thread)

        self.thread.start()

    def cancel_copy(self):
        if self.worker is not None:
            self.worker.cancel()
            self.cancel_button.setEnabled(False)
            self.status_title.setText("Cancelling…")

    @Slot(dict)
    def update_progress(self, stats):
        self.set_tiles(stats)

        if stats["phase"] == "scanning":
            self.status_title.setText("Checking what's missing…")
            self.percent_label.setText("")
            self.progress_bar.setRange(0, 0)
            self.detail_label.setText(f"{stats['scanned']:,} items checked")
            self.current_label.set_full_text("")
            return

        if stats["phase"] != "copying":
            return

        total = stats["total_bytes"]
        done = min(stats["done_bytes"], total)
        fraction = done / total if total else stats["processed"] / max(stats["to_copy"], 1)

        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setValue(int(fraction * 1000))
        self.percent_label.setText(f"{fraction:.0%}")

        self.status_title.setText(
            "Copying "
            + f"{min(stats['processed'] + 1, stats['to_copy']):,}"
            + " of "
            + plural(stats["to_copy"], "file")
        )

        detail = format_size(done) + " of " + format_size(total)
        seconds = stats["copy_seconds"]

        if seconds > 2 and done > 0:
            speed = done / seconds
            detail += " · " + format_size(speed) + "/s"
            detail += " · " + format_duration((total - done) / speed) + " left"

        self.detail_label.setText(detail)
        self.current_label.set_full_text(stats["current"])

    @Slot(dict)
    def finish_copy(self, stats):
        self.set_tiles(stats)
        self.progress_bar.setRange(0, 1000)
        self.current_label.set_full_text("")

        problems = stats["skipped"] + stats["failed"]
        problem_text = (
            " · " + plural(problems, "item") + " need attention — "
            "choose Problems below"
            if problems
            else ""
        )

        if stats["phase"] == "stopped":
            title = "Stopped"
            detail = stats["message"]
        elif stats["phase"] == "cancelled":
            title = "Cancelled"
            detail = (
                "Copied " + plural(stats["copied"], "file")
                + " before stopping. Partly copied files were removed."
            )
        elif stats["preview"]:
            title = (
                "Nothing missing"
                if stats["to_copy"] == 0
                else plural(stats["to_copy"], "file") + " missing"
            )
            detail = (
                format_size(stats["total_bytes"])
                + " would be copied. Nothing was changed."
                + problem_text
            )
            self.progress_bar.setValue(0)
            self.filter_box.setCurrentIndex(0)
        else:
            title = (
                "Already up to date"
                if stats["to_copy"] == 0
                else "Copied " + plural(stats["copied"], "file")
            )
            detail = format_size(stats["copied_bytes"]) + " copied" + problem_text
            self.progress_bar.setValue(1000)

        self.status_title.setText(title)
        self.percent_label.setText("")
        self.detail_label.setText(detail)
        self.append_log([("info", title + ". " + detail)])

        self.set_running(False)
        self.destination_field.refresh()
        QApplication.alert(self)

    @Slot()
    def cleanup_thread(self):
        self.worker.deleteLater()
        self.thread.deleteLater()
        self.worker = None
        self.thread = None

    def closeEvent(self, event):
        if self.thread is not None:
            answer = QMessageBox.question(
                self,
                "Copy in Progress",
                "A copy is still running. Stop it and quit?",
            )

            if answer != QMessageBox.Yes:
                event.ignore()
                return

            # The worker checks for cancel between chunks, so this
            # returns quickly and the partial file is cleaned up.
            self.worker.cancel()
            self.thread.quit()
            self.thread.wait()

        self.save_settings()
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(VERSION)
    app.setWindowIcon(make_app_icon())
    app.setStyleSheet(STYLESHEET)

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
