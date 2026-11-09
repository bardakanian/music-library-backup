"""Copy logic shared by the PySide6 app (app_qt.py) and the Tkinter app (main.py).

Nothing here touches a GUI, so it can run on a worker thread and be tested directly.
"""

import errno
import hashlib
import os
import shutil
import stat
import threading
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path


# Files are written under this name and renamed into place once complete,
# so an interrupted copy never leaves a half-written file that a later run
# would mistake for one that is already there.
PARTIAL_PREFIX = ".cmf-partial-"

CHUNK_SIZE = 4 * 1024 * 1024

MAX_NAME_BYTES = 255

PARALLEL_COPIES = 4


class Cancelled(Exception):
    pass


class FolderError(Exception):
    def __init__(self, title, message):
        super().__init__(message)
        self.title = title
        self.message = message


class DestinationGone(Exception):
    pass


@dataclass
class PlanItem:
    source: Path
    relative: Path
    is_symlink: bool
    size: int


@dataclass
class Plan:
    items: list = field(default_factory=list)
    total_bytes: int = 0
    existing: int = 0
    skipped: list = field(default_factory=list)
    errors: list = field(default_factory=list)


def is_missing_error(error):
    return error.errno == errno.ENOENT


def format_size(size):
    value = float(size)

    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if value < 1000 or unit == "TB":
            if unit == "bytes":
                return f"{int(value):,} bytes"
            return f"{value:,.1f} {unit}"
        value /= 1000

    return f"{size:,} bytes"


def unmounted_volume(path):
    """Return /Volumes/<name> for a path on a drive that isn't connected."""
    parts = Path(os.path.abspath(path)).parts

    if len(parts) >= 3 and parts[1] == "Volumes":
        volume = Path(*parts[:3])

        # /Volumes/Macintosh HD is a symlink to /, so resolve it first.
        if not os.path.ismount(os.path.realpath(volume)):
            return volume

    return None


def nearest_existing(path):
    for candidate in (path, *path.parents):
        if candidate.exists():
            return candidate

    return None


def free_space(path):
    existing = nearest_existing(Path(path))

    if existing is None:
        return None

    try:
        return shutil.disk_usage(existing).free
    except OSError:
        return None


def check_folders(source_text, destination_text):
    """Validate the two folders without changing anything on disk.

    Returns (source, destination) as Paths, or raises FolderError.
    The destination may not exist yet, but its parent must.
    """
    source_text = source_text.strip()
    destination_text = destination_text.strip()

    if not source_text:
        raise FolderError("Missing Source", "Select a source folder.")

    if not destination_text:
        raise FolderError(
            "Missing Destination",
            "Select a destination folder.",
        )

    source = Path(source_text).expanduser()
    destination = Path(destination_text).expanduser()

    for path, role in ((source, "source"), (destination, "destination")):
        volume = unmounted_volume(path)

        if volume is not None:
            raise FolderError(
                "Drive Not Connected",
                "The " + role + " drive \"" + volume.name
                + "\" is not connected. Connect it and try again.",
            )

    if not source.is_dir():
        raise FolderError(
            "Invalid Source",
            "The source folder does not exist.",
        )

    if destination.exists() and not destination.is_dir():
        raise FolderError(
            "Invalid Destination",
            "The destination is a file, not a folder.",
        )

    if not destination.exists() and not destination.parent.is_dir():
        raise FolderError(
            "Invalid Destination",
            "The folder that should contain the destination does not "
            "exist:\n" + str(destination.parent),
        )

    try:
        source_resolved = source.resolve()
        destination_resolved = destination.resolve()
    except OSError:
        return source, destination

    if source_resolved == destination_resolved:
        raise FolderError(
            "Invalid Folders",
            "Source and destination cannot be the same folder.",
        )

    if source_resolved in destination_resolved.parents:
        raise FolderError(
            "Invalid Destination",
            "The destination cannot be inside the source folder.",
        )

    return source, destination


def build_plan(source, destination, should_cancel=None, on_scanned=None):
    """Find the items in source that are missing from destination."""
    plan = Plan()
    scanned = 0

    def walk_error(error):
        plan.errors.append(
            (Path(error.filename or source), error.strerror or str(error))
        )

    def consider(source_path):
        nonlocal scanned
        scanned += 1

        if on_scanned is not None:
            on_scanned(scanned)

        relative = source_path.relative_to(source)

        try:
            source_info = os.lstat(source_path)
        except OSError as error:
            if is_missing_error(error):
                plan.skipped.append((relative, "no longer available"))
            else:
                plan.errors.append((relative, error.strerror or str(error)))
            return

        try:
            destination_info = os.lstat(destination / relative)
        except FileNotFoundError:
            destination_info = None
        except OSError as error:
            plan.errors.append((relative, error.strerror or str(error)))
            return

        if destination_info is not None:
            if (
                stat.S_ISREG(source_info.st_mode)
                and stat.S_ISREG(destination_info.st_mode)
                and source_info.st_size != destination_info.st_size
            ):
                plan.skipped.append(
                    (relative, "a different version is already there")
                )
            else:
                plan.existing += 1
            return

        if stat.S_ISLNK(source_info.st_mode):
            plan.items.append(PlanItem(source_path, relative, True, 0))
        elif stat.S_ISREG(source_info.st_mode):
            plan.items.append(
                PlanItem(source_path, relative, False, source_info.st_size)
            )
            plan.total_bytes += source_info.st_size
        else:
            plan.skipped.append((relative, "special item"))

    for root_dir, dir_names, file_names in os.walk(
        source,
        topdown=True,
        onerror=walk_error,
        followlinks=False,
    ):
        if should_cancel is not None and should_cancel():
            raise Cancelled()

        root_path = Path(root_dir)
        real_directories = []

        for directory_name in dir_names:
            directory_path = root_path / directory_name

            # Symlinked folders are copied as links, not followed.
            if os.path.islink(directory_path):
                consider(directory_path)
            else:
                real_directories.append(directory_name)

        dir_names[:] = real_directories

        for file_name in file_names:
            consider(root_path / file_name)

    plan.items.sort(key=lambda item: str(item.relative).lower())
    return plan


def partial_path(destination):
    name = PARTIAL_PREFIX + destination.name

    if len(name.encode("utf-8", "surrogateescape")) > MAX_NAME_BYTES:
        digest = hashlib.sha1(
            destination.name.encode("utf-8", "surrogateescape")
        ).hexdigest()
        name = PARTIAL_PREFIX + digest

    return destination.with_name(name)


def ensure_parent(destination_root, relative):
    """Create the folders for relative under destination_root.

    Unlike mkdir(parents=True) this never recreates destination_root
    itself, so a drive that was unplugged mid-copy can't turn into a
    folder of the same name on the internal disk.
    """
    if not destination_root.is_dir():
        raise DestinationGone(str(destination_root))

    folder = destination_root

    for part in relative.parent.parts:
        folder = folder / part

        try:
            folder.mkdir()
        except FileExistsError:
            pass
        except FileNotFoundError as error:
            raise DestinationGone(str(destination_root)) from error

    return folder


def name_key(relative):
    return unicodedata.normalize("NFC", str(relative)).casefold()


def name_clash_locks(items):
    """Return a lock for each destination name shared by several items.

    exFAT and default APFS ignore case, so "Song.mp3" and "song.mp3" from a
    case-sensitive source land on the same file. Copied at the same time,
    both would pass the exists check and one would replace the other.
    """
    counts = Counter(name_key(item.relative) for item in items)
    return {key: threading.Lock() for key, count in counts.items() if count > 1}


def copy_item(item, destination_root, on_bytes=None, should_cancel=None):
    """Copy one planned item. Returns False if it appeared in the meantime."""
    ensure_parent(destination_root, item.relative)
    destination = destination_root / item.relative

    if os.path.lexists(destination):
        return False

    if item.is_symlink:
        os.symlink(os.readlink(item.source), destination)
        return True

    temporary = partial_path(destination)

    try:
        with open(item.source, "rb") as reader, open(temporary, "wb") as writer:
            while True:
                if should_cancel is not None and should_cancel():
                    raise Cancelled()

                chunk = reader.read(CHUNK_SIZE)

                if not chunk:
                    break

                writer.write(chunk)

                if on_bytes is not None:
                    on_bytes(len(chunk))

        try:
            shutil.copystat(item.source, temporary)
        except OSError:
            # Some drives (e.g. exFAT) can't hold every macOS flag or
            # timestamp; the file contents are what matter.
            pass

        if os.path.lexists(destination):
            os.unlink(temporary)
            return False

        os.rename(temporary, destination)

    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise

    return True
