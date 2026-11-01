import errno
import json
import os
import shutil
import stat
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk


APP_NAME = "Copy Missing Files"

CONFIG_DIR = (
    Path.home()
    / "Library"
    / "Application Support"
    / APP_NAME
)

CONFIG_FILE = CONFIG_DIR / "settings.json"


class CopyMissingFilesApp:
    def __init__(self, root):
        self.root = root
        self.root.title(APP_NAME)
        self.root.geometry("760x560")
        self.root.minsize(700, 500)

        self.source_var = tk.StringVar()
        self.destination_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Ready")
        self.progress_var = tk.DoubleVar(value=0)

        self.copying = False

        self.load_settings()
        self.build_gui()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def build_gui(self):
        main = ttk.Frame(self.root, padding=15)
        main.pack(fill="both", expand=True)

        ttk.Label(
            main,
            text="Copy Missing Files",
            font=("Helvetica", 20, "bold"),
        ).pack(anchor="w", pady=(0, 15))

        source_frame = ttk.LabelFrame(
            main,
            text="Source Folder",
            padding=10,
        )
        source_frame.pack(fill="x", pady=(0, 10))

        source_entry = ttk.Entry(
            source_frame,
            textvariable=self.source_var,
        )
        source_entry.pack(
            side="left",
            fill="x",
            expand=True,
            padx=(0, 10),
        )

        ttk.Button(
            source_frame,
            text="Browse",
            command=self.select_source,
        ).pack(side="right")

        destination_frame = ttk.LabelFrame(
            main,
            text="Destination Folder",
            padding=10,
        )
        destination_frame.pack(fill="x", pady=(0, 10))

        destination_entry = ttk.Entry(
            destination_frame,
            textvariable=self.destination_var,
        )
        destination_entry.pack(
            side="left",
            fill="x",
            expand=True,
            padx=(0, 10),
        )

        ttk.Button(
            destination_frame,
            text="Browse",
            command=self.select_destination,
        ).pack(side="right")

        self.start_button = ttk.Button(
            main,
            text="Copy Missing Files",
            command=self.start_copy,
        )
        self.start_button.pack(fill="x", pady=(5, 10))

        self.progress = ttk.Progressbar(
            main,
            variable=self.progress_var,
            maximum=100,
        )
        self.progress.pack(fill="x", pady=(0, 5))

        ttk.Label(
            main,
            textvariable=self.status_var,
        ).pack(anchor="w", pady=(0, 10))

        log_frame = ttk.LabelFrame(
            main,
            text="Log",
            padding=5,
        )
        log_frame.pack(fill="both", expand=True)

        self.log_text = tk.Text(
            log_frame,
            wrap="word",
            state="disabled",
            font=("Menlo", 11),
        )

        scrollbar = ttk.Scrollbar(
            log_frame,
            orient="vertical",
            command=self.log_text.yview,
        )

        self.log_text.configure(
            yscrollcommand=scrollbar.set
        )

        scrollbar.pack(
            side="right",
            fill="y",
        )

        self.log_text.pack(
            side="left",
            fill="both",
            expand=True,
        )

    def load_settings(self):
        try:
            if not CONFIG_FILE.exists():
                return

            with CONFIG_FILE.open(
                "r",
                encoding="utf-8",
            ) as file:
                settings = json.load(file)

            source = settings.get("source", "")
            destination = settings.get("destination", "")

            if source:
                self.source_var.set(source)

            if destination:
                self.destination_var.set(destination)

        except (OSError, json.JSONDecodeError):
            pass

    def save_settings(self):
        try:
            CONFIG_DIR.mkdir(
                parents=True,
                exist_ok=True,
            )

            settings = {
                "source": self.source_var.get().strip(),
                "destination": (
                    self.destination_var.get().strip()
                ),
            }

            with CONFIG_FILE.open(
                "w",
                encoding="utf-8",
            ) as file:
                json.dump(
                    settings,
                    file,
                    indent=4,
                )

        except OSError as error:
            self.log(
                "Could not save settings: "
                + str(error)
            )

    def select_source(self):
        current = self.source_var.get().strip()

        if current and os.path.isdir(current):
            initial_dir = current
        else:
            initial_dir = str(Path.home())

        folder = filedialog.askdirectory(
            title="Select Source Folder",
            initialdir=initial_dir,
        )

        if folder:
            self.source_var.set(folder)
            self.save_settings()

    def select_destination(self):
        current = self.destination_var.get().strip()

        if current and os.path.isdir(current):
            initial_dir = current
        else:
            initial_dir = str(Path.home())

        folder = filedialog.askdirectory(
            title="Select Destination Folder",
            initialdir=initial_dir,
        )

        if folder:
            self.destination_var.set(folder)
            self.save_settings()

    def log(self, message):
        def update():
            self.log_text.configure(state="normal")
            self.log_text.insert("end", message + "\n")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")

        self.root.after(0, update)

    def set_status(self, message):
        self.root.after(
            0,
            self.status_var.set,
            message,
        )

    def set_progress(self, value):
        self.root.after(
            0,
            self.progress_var.set,
            value,
        )

    def start_copy(self):
        if self.copying:
            return

        source_text = self.source_var.get().strip()
        destination_text = (
            self.destination_var.get().strip()
        )

        if not source_text:
            messagebox.showerror(
                "Missing Source",
                "Select a source folder.",
            )
            return

        if not destination_text:
            messagebox.showerror(
                "Missing Destination",
                "Select a destination folder.",
            )
            return

        source = Path(source_text)
        destination = Path(destination_text)

        if not source.is_dir():
            messagebox.showerror(
                "Invalid Source",
                "The source folder does not exist.",
            )
            return

        try:
            destination.mkdir(
                parents=True,
                exist_ok=True,
            )
        except OSError as error:
            messagebox.showerror(
                "Destination Error",
                str(error),
            )
            return

        try:
            source_resolved = source.resolve()
            destination_resolved = destination.resolve()

            if source_resolved == destination_resolved:
                messagebox.showerror(
                    "Invalid Folders",
                    "Source and destination cannot "
                    "be the same folder.",
                )
                return

            if source_resolved in destination_resolved.parents:
                messagebox.showerror(
                    "Invalid Destination",
                    "The destination cannot be inside "
                    "the source folder.",
                )
                return

        except OSError:
            pass

        self.save_settings()

        self.copying = True
        self.start_button.configure(state="disabled")

        self.progress_var.set(0)

        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

        thread = threading.Thread(
            target=self.copy_worker,
            args=(source, destination),
            daemon=True,
        )
        thread.start()

    def scan_items(self, source):
        items = []

        for root_dir, dir_names, file_names in os.walk(
            source,
            topdown=True,
            followlinks=False,
        ):
            root_path = Path(root_dir)

            real_directories = []

            for directory_name in dir_names:
                directory_path = (
                    root_path / directory_name
                )

                try:
                    info = os.lstat(directory_path)

                    if stat.S_ISLNK(info.st_mode):
                        items.append(directory_path)
                    else:
                        real_directories.append(
                            directory_name
                        )

                except OSError as error:
                    if error.errno == errno.ENOENT:
                        items.append(directory_path)
                    else:
                        self.log(
                            "Scan warning: "
                            + str(directory_path)
                            + " - "
                            + str(error)
                        )

            dir_names[:] = real_directories

            for file_name in file_names:
                items.append(
                    root_path / file_name
                )

        return items

    def copy_worker(self, source, destination):
        copied = 0
        skipped = 0
        failed = 0

        try:
            self.set_status("Scanning source folder...")
            self.log("Scanning source folder...")

            items = self.scan_items(source)

            total = len(items)

            self.log(
                "Found "
                + f"{total:,}"
                + " items."
            )

            if total == 0:
                self.finish_copy(
                    copied,
                    skipped,
                    failed,
                )
                return

            for index, source_path in enumerate(
                items,
                start=1,
            ):
                try:
                    relative_path = (
                        source_path.relative_to(source)
                    )

                    destination_path = (
                        destination / relative_path
                    )

                    self.set_status(
                        "Processing "
                        + f"{index:,}"
                        + " of "
                        + f"{total:,}"
                    )

                    progress = (
                        index / total
                    ) * 100

                    self.set_progress(progress)

                    try:
                        source_info = os.lstat(
                            source_path
                        )

                    except OSError as error:
                        if error.errno == errno.ENOENT:
                            skipped += 1
                            self.log(
                                "Skipped unavailable: "
                                + str(relative_path)
                            )
                            continue

                        raise

                    if os.path.lexists(
                        destination_path
                    ):
                        skipped += 1
                        continue

                    destination_path.parent.mkdir(
                        parents=True,
                        exist_ok=True,
                    )

                    if stat.S_ISLNK(
                        source_info.st_mode
                    ):
                        try:
                            link_target = os.readlink(
                                source_path
                            )

                            os.symlink(
                                link_target,
                                destination_path,
                            )

                            copied += 1

                            self.log(
                                "Copied symlink: "
                                + str(relative_path)
                            )

                        except OSError as error:
                            if error.errno == errno.ENOENT:
                                skipped += 1
                                self.log(
                                    "Skipped unavailable "
                                    "symlink: "
                                    + str(relative_path)
                                )
                            else:
                                raise

                        continue

                    if not stat.S_ISREG(
                        source_info.st_mode
                    ):
                        skipped += 1
                        self.log(
                            "Skipped special item: "
                            + str(relative_path)
                        )
                        continue

                    try:
                        shutil.copy2(
                            source_path,
                            destination_path,
                        )

                    except FileNotFoundError:
                        skipped += 1

                        self.log(
                            "Skipped unavailable: "
                            + str(relative_path)
                        )

                        continue

                    except OSError as error:
                        if error.errno == errno.ENOENT:
                            skipped += 1

                            self.log(
                                "Skipped unavailable: "
                                + str(relative_path)
                            )

                            continue

                        # copy2 can fail on some macOS
                        # metadata or extended attributes.
                        # Try a basic file copy instead.
                        try:
                            shutil.copyfile(
                                source_path,
                                destination_path,
                            )

                        except FileNotFoundError:
                            skipped += 1

                            self.log(
                                "Skipped unavailable: "
                                + str(relative_path)
                            )

                            continue

                        except OSError as fallback_error:
                            if (
                                fallback_error.errno
                                == errno.ENOENT
                            ):
                                skipped += 1

                                self.log(
                                    "Skipped unavailable: "
                                    + str(relative_path)
                                )

                                continue

                            raise fallback_error

                    copied += 1

                    self.log(
                        "Copied: "
                        + str(relative_path)
                    )

                except OSError as error:
                    if error.errno == errno.ENOENT:
                        skipped += 1

                        try:
                            relative_display = (
                                source_path.relative_to(
                                    source
                                )
                            )
                        except ValueError:
                            relative_display = (
                                source_path
                            )

                        self.log(
                            "Skipped unavailable: "
                            + str(relative_display)
                        )

                    else:
                        failed += 1

                        self.log(
                            "Failed: "
                            + str(source_path)
                            + " - "
                            + str(error)
                        )

                except Exception as error:
                    failed += 1

                    self.log(
                        "Failed: "
                        + str(source_path)
                        + " - "
                        + str(error)
                    )

            self.finish_copy(
                copied,
                skipped,
                failed,
            )

        except Exception as error:
            self.log(
                "Fatal error: "
                + str(error)
            )

            self.root.after(
                0,
                self.start_button.configure,
                {"state": "normal"},
            )

            self.copying = False

    def finish_copy(
        self,
        copied,
        skipped,
        failed,
    ):
        message = (
            "Finished | Copied: "
            + f"{copied:,}"
            + " | Skipped: "
            + f"{skipped:,}"
            + " | Failed: "
            + f"{failed:,}"
        )

        self.log(message)
        self.set_status(message)
        self.set_progress(100)

        self.copying = False

        self.root.after(
            0,
            self.start_button.configure,
            {"state": "normal"},
        )

    def on_close(self):
        self.save_settings()
        self.root.destroy()


def main():
    root = tk.Tk()

    try:
        style = ttk.Style(root)

        if "aqua" in style.theme_names():
            style.theme_use("aqua")

    except tk.TclError:
        pass

    CopyMissingFilesApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()