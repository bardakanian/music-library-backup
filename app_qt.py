import errno
import json
import os
import shutil
import stat
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot
from PySide6.QtGui import QFontDatabase, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


APP_NAME = "Copy Missing Files"

# Bump with every change and add a matching entry to CHANGELOG.md.
VERSION = "1.2.0"

CONFIG_DIR = (
    Path.home()
    / "Library"
    / "Application Support"
    / APP_NAME
)

CONFIG_FILE = CONFIG_DIR / "settings.json"


STYLESHEET = """
QMainWindow {
    background: palette(window);
}

QLabel#title {
    font-size: 22px;
    font-weight: 600;
}

QLabel#subtitle {
    color: palette(placeholder-text);
}

QFrame#card {
    background: palette(base);
    border: 1px solid palette(mid);
    border-radius: 10px;
}

QLabel#fieldLabel {
    font-weight: 600;
}

QLineEdit {
    padding: 6px 8px;
    border: 1px solid palette(mid);
    border-radius: 6px;
}

QPushButton#primary {
    padding: 9px 18px;
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

QLabel#statValue {
    font-size: 20px;
    font-weight: 600;
}

QLabel#statLabel {
    color: palette(placeholder-text);
}

QPlainTextEdit {
    border: 1px solid palette(mid);
    border-radius: 8px;
    padding: 4px;
}
"""


def is_missing_error(error):
    return error.errno == errno.ENOENT


class CopyWorker(QObject):
    log = Signal(str)
    status = Signal(str)
    progress = Signal(int, int)
    counts = Signal(int, int, int)
    finished = Signal(int, int, int, bool)

    def __init__(self, source, destination):
        super().__init__()
        self.source = source
        self.destination = destination
        self.cancel_requested = False

    def cancel(self):
        self.cancel_requested = True

    def scan_items(self):
        items = []

        for root_dir, dir_names, file_names in os.walk(
            self.source,
            topdown=True,
            followlinks=False,
        ):
            if self.cancel_requested:
                break

            root_path = Path(root_dir)

            real_directories = []

            for directory_name in dir_names:
                directory_path = root_path / directory_name

                try:
                    info = os.lstat(directory_path)

                    if stat.S_ISLNK(info.st_mode):
                        items.append(directory_path)
                    else:
                        real_directories.append(directory_name)

                except OSError as error:
                    if is_missing_error(error):
                        items.append(directory_path)
                    else:
                        self.log.emit(
                            "Scan warning: "
                            + str(directory_path)
                            + " - "
                            + str(error)
                        )

            dir_names[:] = real_directories

            for file_name in file_names:
                items.append(root_path / file_name)

        return items

    def copy_item(self, source_path, destination_path):
        """Return "copied", "exists", "unavailable" or "special"."""
        try:
            source_info = os.lstat(source_path)
        except OSError as error:
            if is_missing_error(error):
                return "unavailable"
            raise

        if os.path.lexists(destination_path):
            return "exists"

        destination_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        if stat.S_ISLNK(source_info.st_mode):
            os.symlink(
                os.readlink(source_path),
                destination_path,
            )
            return "copied symlink"

        if not stat.S_ISREG(source_info.st_mode):
            return "special"

        try:
            shutil.copy2(source_path, destination_path)
        except OSError as error:
            if is_missing_error(error):
                raise

            # copy2 can fail on some macOS metadata or
            # extended attributes. Try a basic file copy instead.
            shutil.copyfile(source_path, destination_path)

        return "copied"

    @Slot()
    def run(self):
        copied = 0
        skipped = 0
        failed = 0

        try:
            self.status.emit("Scanning source folder...")
            self.log.emit("Scanning source folder...")

            items = self.scan_items()
            total = len(items)

            self.log.emit("Found " + f"{total:,}" + " items.")

            for index, source_path in enumerate(items, start=1):
                if self.cancel_requested:
                    break

                relative_path = source_path.relative_to(self.source)
                destination_path = self.destination / relative_path

                self.status.emit(
                    "Processing "
                    + f"{index:,}"
                    + " of "
                    + f"{total:,}"
                )
                self.progress.emit(index, total)

                try:
                    result = self.copy_item(
                        source_path,
                        destination_path,
                    )

                    if result == "copied":
                        copied += 1
                        self.log.emit("Copied: " + str(relative_path))
                    elif result == "copied symlink":
                        copied += 1
                        self.log.emit(
                            "Copied symlink: " + str(relative_path)
                        )
                    elif result == "exists":
                        skipped += 1
                    elif result == "unavailable":
                        skipped += 1
                        self.log.emit(
                            "Skipped unavailable: " + str(relative_path)
                        )
                    elif result == "special":
                        skipped += 1
                        self.log.emit(
                            "Skipped special item: " + str(relative_path)
                        )

                except OSError as error:
                    if is_missing_error(error):
                        skipped += 1
                        self.log.emit(
                            "Skipped unavailable: " + str(relative_path)
                        )
                    else:
                        failed += 1
                        self.log.emit(
                            "Failed: "
                            + str(relative_path)
                            + " - "
                            + str(error)
                        )

                except Exception as error:
                    failed += 1
                    self.log.emit(
                        "Failed: "
                        + str(relative_path)
                        + " - "
                        + str(error)
                    )

                self.counts.emit(copied, skipped, failed)

        except Exception as error:
            failed += 1
            self.log.emit("Fatal error: " + str(error))

        self.counts.emit(copied, skipped, failed)
        self.finished.emit(
            copied,
            skipped,
            failed,
            self.cancel_requested,
        )


class FolderField(QWidget):
    changed = Signal()

    def __init__(self, label, dialog_title):
        super().__init__()
        self.dialog_title = dialog_title

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        title = QLabel(label)
        title.setObjectName("fieldLabel")
        layout.addWidget(title)

        row = QHBoxLayout()
        row.setSpacing(8)

        self.edit = QLineEdit()
        self.edit.setPlaceholderText(
            "Choose a folder or drop one here"
        )
        self.edit.editingFinished.connect(self.changed)
        row.addWidget(self.edit, 1)

        self.browse_button = QPushButton("Browse…")
        self.browse_button.clicked.connect(self.browse)
        row.addWidget(self.browse_button)

        layout.addLayout(row)

        self.setAcceptDrops(True)

    def text(self):
        return self.edit.text().strip()

    def set_text(self, value):
        self.edit.setText(value)

    def setEnabled(self, enabled):
        self.edit.setEnabled(enabled)
        self.browse_button.setEnabled(enabled)

    def browse(self):
        current = self.text()

        if current and os.path.isdir(current):
            initial_dir = current
        else:
            initial_dir = str(Path.home())

        folder = QFileDialog.getExistingDirectory(
            self,
            self.dialog_title,
            initial_dir,
        )

        if folder:
            self.edit.setText(folder)
            self.changed.emit()

    def dragEnterEvent(self, event):
        urls = event.mimeData().urls()

        if urls and urls[0].isLocalFile():
            if os.path.isdir(urls[0].toLocalFile()):
                event.acceptProposedAction()

    def dropEvent(self, event):
        path = event.mimeData().urls()[0].toLocalFile()
        self.edit.setText(path)
        self.changed.emit()


class StatBox(QWidget):
    def __init__(self, label, color):
        super().__init__()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.value = QLabel("0")
        self.value.setObjectName("statValue")
        self.value.setStyleSheet("color: " + color + ";")
        layout.addWidget(self.value)

        caption = QLabel(label)
        caption.setObjectName("statLabel")
        layout.addWidget(caption)

    def set_value(self, number):
        self.value.setText(f"{number:,}")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME + " " + VERSION)
        self.resize(780, 640)
        self.setMinimumSize(680, 540)

        self.thread = None
        self.worker = None

        self.build_gui()
        self.load_settings()

    def build_gui(self):
        central = QWidget()
        self.setCentralWidget(central)

        main = QVBoxLayout(central)
        main.setContentsMargins(20, 20, 20, 20)
        main.setSpacing(14)

        title = QLabel(APP_NAME)
        title.setObjectName("title")
        main.addWidget(title)

        subtitle = QLabel(
            "Copies files that exist in the source but not in "
            "the destination. Nothing is overwritten or deleted."
        )
        subtitle.setObjectName("subtitle")
        subtitle.setWordWrap(True)
        main.addWidget(subtitle)

        folders_card = QFrame()
        folders_card.setObjectName("card")
        folders_layout = QVBoxLayout(folders_card)
        folders_layout.setContentsMargins(16, 16, 16, 16)
        folders_layout.setSpacing(14)

        self.source_field = FolderField(
            "Source Folder",
            "Select Source Folder",
        )
        self.source_field.changed.connect(self.save_settings)
        folders_layout.addWidget(self.source_field)

        self.destination_field = FolderField(
            "Destination Folder",
            "Select Destination Folder",
        )
        self.destination_field.changed.connect(self.save_settings)
        folders_layout.addWidget(self.destination_field)

        main.addWidget(folders_card)

        button_row = QHBoxLayout()

        self.start_button = QPushButton("Copy Missing Files")
        self.start_button.setObjectName("primary")
        self.start_button.setCursor(Qt.PointingHandCursor)
        self.start_button.clicked.connect(self.start_copy)
        button_row.addWidget(self.start_button, 1)

        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_copy)
        button_row.addWidget(self.cancel_button)

        main.addLayout(button_row)

        progress_card = QFrame()
        progress_card.setObjectName("card")
        progress_layout = QVBoxLayout(progress_card)
        progress_layout.setContentsMargins(16, 14, 16, 14)
        progress_layout.setSpacing(10)

        self.status_label = QLabel("Ready")
        progress_layout.addWidget(self.status_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        progress_layout.addWidget(self.progress_bar)

        stats = QGridLayout()
        stats.setHorizontalSpacing(30)

        self.copied_stat = StatBox("Copied", "#2e9d57")
        self.skipped_stat = StatBox("Skipped", "#8a8f98")
        self.failed_stat = StatBox("Failed", "#d6453d")

        stats.addWidget(self.copied_stat, 0, 0)
        stats.addWidget(self.skipped_stat, 0, 1)
        stats.addWidget(self.failed_stat, 0, 2)
        stats.setColumnStretch(3, 1)

        progress_layout.addLayout(stats)

        main.addWidget(progress_card)

        log_label = QLabel("Log")
        log_label.setObjectName("fieldLabel")
        main.addWidget(log_label)

        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumBlockCount(50000)

        log_font = QFontDatabase.systemFont(
            QFontDatabase.FixedFont
        )
        log_font.setPointSize(11)
        self.log_text.setFont(log_font)

        main.addWidget(self.log_text, 1)

    def load_settings(self):
        try:
            if not CONFIG_FILE.exists():
                return

            with CONFIG_FILE.open("r", encoding="utf-8") as file:
                settings = json.load(file)

            self.source_field.set_text(settings.get("source", ""))
            self.destination_field.set_text(
                settings.get("destination", "")
            )

        except (OSError, json.JSONDecodeError):
            pass

    def save_settings(self):
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)

            settings = {
                "source": self.source_field.text(),
                "destination": self.destination_field.text(),
            }

            with CONFIG_FILE.open("w", encoding="utf-8") as file:
                json.dump(settings, file, indent=4)

        except OSError as error:
            self.append_log("Could not save settings: " + str(error))

    @Slot(str)
    def append_log(self, message):
        self.log_text.appendPlainText(message)
        self.log_text.moveCursor(QTextCursor.End)

    @Slot(int, int)
    def update_progress(self, index, total):
        self.progress_bar.setValue(int(index / total * 100))

    @Slot(int, int, int)
    def update_counts(self, copied, skipped, failed):
        self.copied_stat.set_value(copied)
        self.skipped_stat.set_value(skipped)
        self.failed_stat.set_value(failed)

    def show_error(self, title, message):
        QMessageBox.critical(self, title, message)

    def validate_folders(self):
        source_text = self.source_field.text()
        destination_text = self.destination_field.text()

        if not source_text:
            self.show_error("Missing Source", "Select a source folder.")
            return None

        if not destination_text:
            self.show_error(
                "Missing Destination",
                "Select a destination folder.",
            )
            return None

        source = Path(source_text)
        destination = Path(destination_text)

        if not source.is_dir():
            self.show_error(
                "Invalid Source",
                "The source folder does not exist.",
            )
            return None

        try:
            destination.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            self.show_error("Destination Error", str(error))
            return None

        try:
            source_resolved = source.resolve()
            destination_resolved = destination.resolve()

            if source_resolved == destination_resolved:
                self.show_error(
                    "Invalid Folders",
                    "Source and destination cannot be the same folder.",
                )
                return None

            if source_resolved in destination_resolved.parents:
                self.show_error(
                    "Invalid Destination",
                    "The destination cannot be inside the source folder.",
                )
                return None

        except OSError:
            pass

        return source, destination

    def set_running(self, running):
        self.start_button.setEnabled(not running)
        self.cancel_button.setEnabled(running)
        self.source_field.setEnabled(not running)
        self.destination_field.setEnabled(not running)

    def start_copy(self):
        if self.thread is not None:
            return

        folders = self.validate_folders()

        if folders is None:
            return

        source, destination = folders

        self.save_settings()

        self.log_text.clear()
        self.progress_bar.setValue(0)
        self.update_counts(0, 0, 0)
        self.set_running(True)

        self.thread = QThread(self)
        self.worker = CopyWorker(source, destination)
        self.worker.moveToThread(self.thread)

        self.thread.started.connect(self.worker.run)
        self.worker.log.connect(self.append_log)
        self.worker.status.connect(self.status_label.setText)
        self.worker.progress.connect(self.update_progress)
        self.worker.counts.connect(self.update_counts)
        self.worker.finished.connect(self.finish_copy)
        self.worker.finished.connect(self.thread.quit)
        self.thread.finished.connect(self.cleanup_thread)

        self.thread.start()

    def cancel_copy(self):
        if self.worker is not None:
            self.worker.cancel()
            self.cancel_button.setEnabled(False)
            self.status_label.setText("Cancelling...")

    @Slot(int, int, int, bool)
    def finish_copy(self, copied, skipped, failed, cancelled):
        message = (
            ("Cancelled" if cancelled else "Finished")
            + " | Copied: "
            + f"{copied:,}"
            + " | Skipped: "
            + f"{skipped:,}"
            + " | Failed: "
            + f"{failed:,}"
        )

        self.append_log(message)
        self.status_label.setText(message)

        if not cancelled:
            self.progress_bar.setValue(100)

        self.set_running(False)

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

            self.worker.cancel()
            self.thread.quit()
            self.thread.wait()

        self.save_settings()
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(VERSION)
    app.setStyleSheet(STYLESHEET)

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
