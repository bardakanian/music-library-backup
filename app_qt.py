import errno
import json
import sys
import threading
import time
from datetime import datetime
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import nullcontext
from pathlib import Path

from PySide6.QtCore import (
    QObject,
    QProcess,
    QRectF,
    QThread,
    QTimer,
    QUrl,
    Qt,
    Signal,
    Slot,
)
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
    QCheckBox,
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


APP_NAME = "Music Library Backup"

# Bump with every change and add a matching entry to CHANGELOG.md.
VERSION = "2.1.1"

CONFIG_DIR = (
    Path.home()
    / "Library"
    / "Application Support"
    / APP_NAME
)

CONFIG_FILE = CONFIG_DIR / "settings.json"

# Settings saved before the app was renamed from Copy Missing Files.
OLD_CONFIG_FILE = (
    Path.home()
    / "Library"
    / "Application Support"
    / "Copy Missing Files"
    / "settings.json"
)

MAX_LOG_ENTRIES = 200000

ACCENT = "#2f6fde"

LOG_PREFIXES = {
    "info": "",
    "missing": "+ ",
    "copied": "✓ ",
    "skipped": "– ",
    "warning": "⚠ ",
    "error": "✕ ",
}

LOG_FILTERS = [
    ("All activity", None),
    ("Copied", {"copied"}),
    ("To copy", {"missing"}),
    ("Problems", {"skipped", "warning", "error"}),
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

    def __init__(
        self,
        source,
        destination,
        preview=False,
        windows_safe=False,
        library=None,
        export_playlists=False,
        check_folders=True,
    ):
        super().__init__()
        self.source = source
        self.destination = destination
        self.preview = preview
        self.windows_safe = windows_safe
        self.library = library
        self.export_playlists = export_playlists
        self.check_folders = check_folders
        self.cancel_requested = False

        self.pending_log = []
        self.last_emit = 0.0
        self.copy_started = None

        # Copy threads only touch done_bytes and active_items, under this
        # lock. Everything else, including signals, stays on the worker thread.
        self.copy_lock = threading.Lock()
        self.active_items = {}
        self.halted = False

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
            "warnings": 0,
            "snapshot": "",
            "mismatch": None,
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

    def stop(self, message):
        self.halted = True
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
                self.windows_safe,
            )
            self.record_plan(plan)
            mismatch = None

            # Far more missing than already there may mean the folders are
            # one level apart, which would copy the whole library again.
            if self.check_folders and len(plan.items) > plan.existing:
                mismatch = copy_core.better_folder_match(
                    self.source, self.destination
                )

            if mismatch is not None:
                stats["mismatch"] = mismatch
                stats["warnings"] += 1
                self.add_log("warning", mismatch["message"])

            if mismatch is not None and not self.preview:
                stats["phase"] = "mismatch"
                self.add_log("info", "Nothing was copied.")
            elif self.preview:
                for item in plan.items:
                    size = "link" if item.is_symlink else format_size(item.size)
                    name = str(item.relative)

                    if item.target != item.relative:
                        name += "  → " + str(item.target)

                    self.add_log("missing", name + "  (" + size + ")")

                self.describe_library_backup()
            else:
                self.copy_items(plan)

                if not self.should_stop_copy() and (
                    self.library is not None or self.export_playlists
                ):
                    self.back_up_library()

        except copy_core.Cancelled:
            stats["phase"] = "cancelled"

        except Exception as error:
            stats["failed"] += 1
            self.stop("Unexpected error: " + str(error))

        if stats["phase"] in ("scanning", "copying", "library"):
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

        stats["warnings"] = len(plan.warnings)

        for target, reason in plan.warnings:
            self.add_log("warning", str(target) + " — " + reason)

        summary = (
            "Found "
            + plural(len(plan.items), "missing file")
            + " ("
            + format_size(plan.total_bytes)
            + "), "
            + f"{plan.existing:,}"
            + " already in the destination."
        )

        if plan.renamed:
            summary += (
                " " + plural(plan.renamed, "name")
                + " changed to be Windows-safe."
            )

        if plan.ignored:
            summary += (
                " Left out " + plural(plan.ignored, "Mac-only system file") + "."
            )

        self.add_log("info", summary)

    def describe_library_backup(self):
        if self.library is not None:
            self.add_log(
                "info",
                "Will also save a dated copy of the library database ("
                + self.library.name + ").",
            )

        if self.export_playlists:
            self.add_log(
                "info",
                "Will also export playlists as .m3u8 files for MusicBee.",
            )

    def back_up_library(self):
        """Save the library database and playlists in a new dated folder."""
        stats = self.stats
        stats["phase"] = "library"
        stats["current"] = ""
        self.emit_progress(force=True)

        try:
            if not self.destination.exists():
                self.destination.mkdir()
                copy_core.drop_appledouble(self.destination)

            folder = copy_core.snapshot_folder(
                self.destination,
                copy_core.snapshot_stamp(datetime.now()),
            )
        except copy_core.DestinationGone:
            self.stop_once(
                "The destination folder is no longer available. "
                "Is the drive still connected?"
            )
            return
        except OSError as error:
            stats["failed"] += 1
            self.add_log(
                "error",
                "Could not create the library backup folder — "
                + (error.strerror or str(error)),
            )
            return

        stats["snapshot"] = str(folder)

        try:
            if self.library is not None:
                self.back_up_database(folder)

            if self.export_playlists and not self.should_stop_copy():
                self.save_playlists(folder)
        finally:
            # Don't leave an empty dated folder after a failure or cancel.
            try:
                folder.rmdir()
            except OSError:
                pass
            else:
                stats["snapshot"] = ""

    def back_up_database(self, folder):
        stats = self.stats

        if copy_core.music_is_running():
            self.add_log(
                "info",
                "Apple Music is open. Quit it first for the most reliable "
                "database backup.",
            )

        stats["current"] = "Backing up " + self.library.name
        self.emit_progress(force=True)

        try:
            size = copy_core.copy_tree(
                self.library,
                folder / self.library.name,
                self.should_stop_copy,
            )
        except copy_core.Cancelled:
            raise
        except OSError as error:
            stats["failed"] += 1

            if error.errno == errno.ENOSPC:
                self.stop_once("The destination drive is full.")
            else:
                self.add_log(
                    "error",
                    "Could not back up the library database — "
                    + (error.strerror or str(error)),
                )
            return

        self.add_log(
            "copied",
            "Library database saved to "
            + str(folder.relative_to(self.destination) / self.library.name)
            + " (" + format_size(size) + ")",
        )

    def save_playlists(self, folder):
        stats = self.stats
        stats["current"] = "Reading playlists from Apple Music"
        self.emit_progress(force=True)

        try:
            playlists = copy_core.read_playlists(self.should_stop_copy)
            written, tracks, left_out = copy_core.write_playlists(
                playlists,
                self.source,
                self.destination,
                folder / copy_core.PLAYLISTS_FOLDER,
                self.windows_safe,
            )
        except copy_core.PlaylistError as error:
            stats["failed"] += 1
            self.add_log("error", str(error))
            return
        except OSError as error:
            stats["failed"] += 1
            self.add_log(
                "error",
                "Could not save playlists — " + (error.strerror or str(error)),
            )
            return

        message = (
            "Exported " + plural(written, "playlist")
            + " (" + plural(tracks, "track") + ") to "
            + str(folder.relative_to(self.destination) / copy_core.PLAYLISTS_FOLDER)
        )

        if left_out:
            message += (
                ". Left out " + plural(left_out, "track")
                + " that aren't files in the source folder"
                + " (streamed, cloud-only or stored elsewhere)"
            )

        self.add_log("copied", message + ".")

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
            copy_core.drop_appledouble(self.destination)
            self.add_log("info", "Created " + str(self.destination))

        stats["phase"] = "copying"
        self.copy_started = time.monotonic()

        guards = copy_core.name_clash_locks(plan.items)
        cancelled = False

        with ThreadPoolExecutor(
            max_workers=copy_core.PARALLEL_COPIES,
            thread_name_prefix="copy",
        ) as pool:
            upcoming = iter(plan.items)
            waiting = {}

            # Only a few files are queued at once: wait() scans every
            # future, which would crawl with a whole library queued.
            while True:
                while (
                    not self.should_stop_copy()
                    and len(waiting) < copy_core.PARALLEL_COPIES * 2
                ):
                    item = next(upcoming, None)

                    if item is None:
                        break

                    waiting[pool.submit(self.copy_one, item, guards)] = item

                # Once stopped, this still drains what's running so files
                # that finished in the meantime are counted.
                if not waiting:
                    break

                done, _ = wait(
                    waiting,
                    timeout=0.1,
                    return_when=FIRST_COMPLETED,
                )

                for future in done:
                    item = waiting.pop(future)

                    try:
                        copied, item_bytes = future.result()
                    except copy_core.Cancelled:
                        cancelled = True
                    except (copy_core.DestinationGone, OSError) as error:
                        self.record_failure(item, error, error.item_bytes)
                    else:
                        self.record_success(item, copied, item_bytes)

                self.update_current()
                self.emit_progress()

        if self.cancel_requested and stats["phase"] != "stopped":
            if cancelled or stats["processed"] < len(plan.items):
                raise copy_core.Cancelled()

    def should_stop_copy(self):
        return self.cancel_requested or self.halted

    def copy_one(self, item, guards):
        """Runs on a copy thread. Returns (copied, bytes written)."""
        if self.should_stop_copy():
            raise copy_core.Cancelled()

        item_bytes = 0

        def on_bytes(count):
            nonlocal item_bytes
            item_bytes += count

            with self.copy_lock:
                self.stats["done_bytes"] += count

        guard = guards.get(copy_core.name_key(item.target), nullcontext())

        with guard:
            with self.copy_lock:
                self.active_items[item.target] = None

            try:
                copied = copy_core.copy_item(
                    item,
                    self.destination,
                    on_bytes,
                    self.should_stop_copy,
                )
            except copy_core.Cancelled:
                with self.copy_lock:
                    self.stats["done_bytes"] -= item_bytes
                raise
            except (copy_core.DestinationGone, OSError) as error:
                error.item_bytes = item_bytes
                raise
            finally:
                with self.copy_lock:
                    del self.active_items[item.target]

        return copied, item_bytes

    def update_current(self):
        with self.copy_lock:
            active = list(self.active_items)

        if not active:
            return

        current = str(active[0])

        if len(active) > 1:
            current += f"  (+{len(active) - 1} more)"

        self.stats["current"] = current

    def finish_item(self, item, item_bytes):
        with self.copy_lock:
            self.stats["done_bytes"] += item.size - item_bytes

        self.stats["processed"] += 1

    def record_success(self, item, copied, item_bytes):
        stats = self.stats

        if copied:
            stats["copied"] += 1
            stats["copied_bytes"] += item.size
            self.add_log("copied", str(item.target))
        else:
            stats["existing"] += 1

        self.finish_item(item, item_bytes)

    def stop_once(self, message):
        if self.stats["phase"] != "stopped":
            self.stop(message)

    def record_failure(self, item, error, item_bytes):
        stats = self.stats
        gone = (
            "The destination folder is no longer available. "
            "Is the drive still connected?"
        )

        if isinstance(error, copy_core.DestinationGone):
            self.stop_once(gone)
            return

        if error.errno == errno.ENOSPC:
            stats["failed"] += 1
            self.stop_once("The destination drive is full.")
            return

        if copy_core.is_missing_error(error):
            if not self.destination.is_dir():
                self.stop_once(gone)
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

        self.finish_item(item, item_bytes)


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
        self.eject_process = None
        self.pending_mismatch = None

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
        main.addLayout(self.build_options())
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
            "Copies your Apple Music library to a USB stick or backup drive. "
            "It only adds what's missing; nothing there is ever overwritten "
            "or deleted."
        )
        subtitle.setWordWrap(True)
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
            "Music folder",
            "Select Music Folder",
            style.standardIcon(QStyle.SP_DirIcon),
            self.describe_source,
        )
        self.source_field.changed.connect(self.on_folders_changed)
        row.addWidget(self.source_field, 1)

        arrow = QLabel("→")
        arrow.setObjectName("arrow")
        row.addWidget(arrow)

        self.destination_field = FolderCard(
            "Backup / USB stick",
            "Select Destination Folder",
            style.standardIcon(QStyle.SP_DriveHDIcon),
            self.describe_destination,
        )
        self.destination_field.changed.connect(self.on_folders_changed)
        row.addWidget(self.destination_field, 1)

        return row

    def build_options(self):
        row = QHBoxLayout()
        row.setSpacing(18)

        self.windows_box = QCheckBox("Windows-safe names")
        self.windows_box.setToolTip(
            "Replaces characters Windows and exFAT don't allow (such as ? : *),\n"
            "copies links as real files and flags paths too long for Windows."
        )

        self.database_box = QCheckBox("Back up library database")
        self.database_box.setToolTip(
            "Saves a dated copy of the Music Library database (ratings, play\n"
            "counts, playlists) in Library Backups. Earlier copies are kept."
        )

        self.playlists_box = QCheckBox("Export playlists (.m3u8)")
        self.playlists_box.setToolTip(
            "Saves each Apple Music playlist as an .m3u8 file that MusicBee\n"
            "and other Windows players can import."
        )

        for box in (self.windows_box, self.database_box, self.playlists_box):
            box.setChecked(True)
            box.toggled.connect(lambda _checked: self.save_settings())
            row.addWidget(box)

        row.addStretch(1)

        self.music_button = QPushButton("Use Apple Music Library")
        self.music_button.setToolTip(
            "Set the music folder to Apple Music's default media folder."
        )
        self.music_button.clicked.connect(self.use_music_library)
        row.addWidget(self.music_button)

        return row

    def build_actions(self):
        row = QHBoxLayout()
        row.setSpacing(10)

        hint = QLabel("Tip: Preview shows what's missing without copying.")
        hint.setObjectName("muted")
        row.addWidget(hint, 1)

        self.eject_button = QPushButton("Eject")
        self.eject_button.clicked.connect(self.eject_destination)
        self.eject_button.hide()
        row.addWidget(self.eject_button)

        self.preview_button = QPushButton("Preview")
        self.preview_button.clicked.connect(lambda: self.start(preview=True))
        row.addWidget(self.preview_button)

        self.start_button = QPushButton("Back Up")
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
        self.detail_label.setWordWrap(True)
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
            self.update_options()

        super().changeEvent(event)

    def on_folders_changed(self):
        self.save_settings()
        self.show_idle()
        self.update_options()

    def update_options(self):
        library = copy_core.find_music_library(self.source_field.text())

        if library is None:
            self.database_box.setText("Back up library database (not found)")
        else:
            self.database_box.setText("Back up library database")

        self.database_box.setEnabled(library is not None and self.thread is None)

        volume = copy_core.volume_of(self.destination_field.text() or "/")
        self.eject_button.setVisible(volume is not None and self.thread is None)

        if volume is not None:
            self.eject_button.setText("Eject “" + volume.name + "”")

    def use_music_library(self):
        media = copy_core.default_media_folder()

        if media is None:
            QMessageBox.information(
                self,
                "Apple Music Library Not Found",
                "Couldn't find Apple Music's media folder in "
                + str(copy_core.DEFAULT_MUSIC_FOLDER)
                + ".\n\nIf you keep your music somewhere else, choose that "
                "folder instead (Music → Settings → Files shows where).",
            )
            return

        self.source_field.set_text(str(media))
        self.on_folders_changed()

    def eject_destination(self):
        volume = copy_core.volume_of(self.destination_field.text() or "/")

        if volume is None or self.eject_process is not None:
            return

        self.eject_button.setEnabled(False)
        self.append_log([("info", "Ejecting " + volume.name + " …")])

        # diskutil can take a few seconds; QProcess keeps the window responsive.
        process = QProcess(self)
        process.finished.connect(
            lambda code, _status: self.eject_finished(process, volume, code)
        )
        self.eject_process = process
        process.start("diskutil", ["eject", str(volume)])

    def eject_finished(self, process, volume, code):
        output = bytes(process.readAllStandardError()).decode(errors="replace")
        self.eject_process = None
        process.deleteLater()
        self.eject_button.setEnabled(True)

        if code == 0:
            self.append_log([("info", volume.name + " was ejected. You can unplug it now.")])
        else:
            self.append_log([(
                "error",
                "Could not eject " + volume.name + " — "
                + (output.strip() or "it may still be in use"),
            )])

        self.destination_field.refresh()
        self.update_options()

    def load_settings(self):
        settings = None

        for path in (CONFIG_FILE, OLD_CONFIG_FILE):
            try:
                with path.open("r", encoding="utf-8") as file:
                    settings = json.load(file)
                break
            except (OSError, ValueError):
                continue

        if not isinstance(settings, dict):
            self.update_options()
            return

        source = settings.get("source", "")
        destination = settings.get("destination", "")

        if isinstance(source, str):
            self.source_field.set_text(source)

        if isinstance(destination, str):
            self.destination_field.set_text(destination)

        for key, box in self.option_boxes().items():
            value = settings.get(key)

            if isinstance(value, bool):
                box.blockSignals(True)
                box.setChecked(value)
                box.blockSignals(False)

        self.update_options()

    def option_boxes(self):
        return {
            "windows_safe": self.windows_box,
            "back_up_database": self.database_box,
            "export_playlists": self.playlists_box,
        }

    def save_settings(self):
        try:
            CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)

            settings = {
                "source": self.source_field.text(),
                "destination": self.destination_field.text(),
            }

            for key, box in self.option_boxes().items():
                settings[key] = box.isChecked()

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
            "Choose your music folder and the backup drive, then Preview or Back Up."
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

        for box in (self.windows_box, self.playlists_box, self.music_button):
            box.setEnabled(not running)

        if running:
            self.database_box.setEnabled(False)
            self.eject_button.hide()

    def start(self, preview, check_folders=True):
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

        library = None

        if self.database_box.isChecked():
            library = copy_core.find_music_library(source)

        self.thread = QThread(self)
        self.worker = CopyWorker(
            source,
            destination,
            preview,
            windows_safe=self.windows_box.isChecked(),
            library=library,
            export_playlists=self.playlists_box.isChecked(),
            check_folders=check_folders,
        )
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

        if stats["phase"] == "library":
            self.status_title.setText("Saving library backup…")
            self.percent_label.setText("")
            self.progress_bar.setRange(0, 0)
            self.detail_label.setText(stats["current"])
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

        problems = stats["skipped"] + stats["failed"] + stats["warnings"]
        problem_text = (
            " · " + plural(problems, "item")
            + (" needs" if problems == 1 else " need")
            + " attention — choose Problems below"
            if problems
            else ""
        )

        if stats["phase"] == "mismatch":
            title = "Check your folders"
            detail = "Nothing was copied. " + stats["mismatch"]["message"]
            self.pending_mismatch = stats["mismatch"]
        elif stats["phase"] == "stopped":
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

            if stats["mismatch"]:
                detail = "Check your folders: " + stats["mismatch"]["message"]
            self.progress_bar.setValue(0)
            self.filter_box.setCurrentIndex(0)
        else:
            title = (
                "Already up to date"
                if stats["to_copy"] == 0
                else "Copied " + plural(stats["copied"], "file")
            )
            detail = format_size(stats["copied_bytes"]) + " copied" + problem_text

            if stats["snapshot"]:
                detail += " · library backup in " + Path(stats["snapshot"]).name

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
        self.update_options()

        if self.pending_mismatch is not None:
            mismatch = self.pending_mismatch
            self.pending_mismatch = None
            QTimer.singleShot(0, lambda: self.ask_about_mismatch(mismatch))

    def ask_about_mismatch(self, mismatch):
        role = "music folder" if mismatch["role"] == "source" else "destination"

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Check Your Folders")
        box.setText("This would copy your music a second time.")
        box.setInformativeText(
            mismatch["message"]
            + "\n\nUse that folder as the " + role + " instead?"
        )
        use_button = box.addButton("Use That Folder", QMessageBox.AcceptRole)
        anyway_button = box.addButton("Back Up Anyway", QMessageBox.DestructiveRole)
        box.addButton(QMessageBox.Cancel)
        box.setDefaultButton(use_button)
        box.exec()

        clicked = box.clickedButton()

        if clicked is use_button:
            field = (
                self.source_field if mismatch["role"] == "source"
                else self.destination_field
            )
            field.set_text(mismatch["path"])
            self.on_folders_changed()
            self.start(preview=True)
        elif clicked is anyway_button:
            self.start(preview=False, check_folders=False)

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
