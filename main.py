import errno
import json
import os
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import copy_core


APP_NAME = "Music Library Backup"

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
        self.cancel_requested = False

        self.load_settings()
        self.build_gui()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def build_gui(self):
        main = ttk.Frame(self.root, padding=15)
        main.pack(fill="both", expand=True)

        ttk.Label(
            main,
            text="Music Library Backup",
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
            text="Back Up",
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

            if not isinstance(settings, dict):
                return

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

        try:
            source, destination = copy_core.check_folders(
                self.source_var.get(),
                self.destination_var.get(),
            )
        except copy_core.FolderError as error:
            messagebox.showerror(error.title, error.message)
            return

        self.save_settings()

        self.copying = True
        self.cancel_requested = False
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

    def is_cancelled(self):
        return self.cancel_requested

    def copy_worker(self, source, destination):
        copied = 0
        skipped = 0
        failed = 0

        try:
            self.set_status("Scanning source folder...")
            self.log("Scanning source folder...")

            plan = copy_core.build_plan(
                source,
                destination,
                self.is_cancelled,
            )

            skipped = len(plan.skipped)
            failed = len(plan.errors)

            for relative_path, reason in plan.skipped:
                self.log("Skipped: " + str(relative_path) + " - " + reason)

            for error_path, reason in plan.errors:
                self.log("Failed: " + str(error_path) + " - " + reason)

            skipped += plan.existing
            total = len(plan.items)

            self.log(
                "Found "
                + f"{total:,}"
                + " missing items ("
                + copy_core.format_size(plan.total_bytes)
                + ")."
            )

            free = copy_core.free_space(destination)

            if free is not None and plan.total_bytes > free:
                self.log(
                    "Not enough space on the destination drive: needs "
                    + copy_core.format_size(plan.total_bytes)
                    + ", only "
                    + copy_core.format_size(free)
                    + " free. Nothing was copied."
                )
                self.finish_copy(copied, skipped, failed)
                return

            if plan.items and not destination.exists():
                destination.mkdir()

            for index, item in enumerate(plan.items, start=1):
                self.set_status(
                    "Copying "
                    + f"{index:,}"
                    + " of "
                    + f"{total:,}"
                )
                self.set_progress(index / total * 100)

                try:
                    if copy_core.copy_item(
                        item,
                        destination,
                        should_cancel=self.is_cancelled,
                    ):
                        copied += 1
                        self.log("Copied: " + str(item.relative))
                    else:
                        skipped += 1

                except copy_core.DestinationGone:
                    self.log(
                        "Stopped: the destination folder is no longer "
                        "available. Is the drive still connected?"
                    )
                    break

                except OSError as error:
                    if error.errno == errno.ENOSPC:
                        failed += 1
                        self.log("Stopped: the destination drive is full.")
                        break

                    if copy_core.is_missing_error(error):
                        skipped += 1
                        self.log(
                            "Skipped unavailable: " + str(item.relative)
                        )
                    else:
                        failed += 1
                        self.log(
                            "Failed: "
                            + str(item.relative)
                            + " - "
                            + str(error)
                        )

        except copy_core.Cancelled:
            self.log("Cancelled.")

        except Exception as error:
            failed += 1
            self.log("Fatal error: " + str(error))

        self.finish_copy(copied, skipped, failed)

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
        if self.copying:
            if not messagebox.askyesno(
                "Copy in Progress",
                "A copy is still running. Stop it and quit?",
            ):
                return

            self.cancel_requested = True

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