"""Backup logic shared by the PySide6 app (app_qt.py) and the Tkinter app (main.py).

Nothing here touches a GUI, so it can run on a worker thread and be tested directly.
"""

import errno
import hashlib
import json
import os
import shutil
import stat
import subprocess
import threading
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path


# Files are written under this name and renamed into place once complete,
# so an interrupted copy never leaves a half-written file that a later run
# would mistake for one that is already there.
PARTIAL_PREFIX = ".cmf-partial-"

# Added to the end too, so that a player watching the folder (MusicBee on
# Windows doesn't treat a leading dot as hidden) doesn't take it for music.
PARTIAL_SUFFIX = ".part"

CHUNK_SIZE = 4 * 1024 * 1024

MAX_NAME_BYTES = 255

PARALLEL_COPIES = 4

# Mac-only clutter that would litter a Windows drive.
JUNK_NAMES = {".DS_Store", ".localized", "Icon\r"}
JUNK_PREFIXES = ("._", PARTIAL_PREFIX)

WINDOWS_BAD_CHARACTERS = set('<>:"/\\|?*')
WINDOWS_RESERVED_NAMES = (
    {"CON", "PRN", "AUX", "NUL"}
    | {"COM" + str(number) for number in range(1, 10)}
    | {"LPT" + str(number) for number in range(1, 10)}
)

# Older Windows apps can't open paths longer than this, drive letter included.
WINDOWS_MAX_PATH = 259

BACKUPS_FOLDER = "Library Backups"
PLAYLISTS_FOLDER = "Playlists"

DEFAULT_MUSIC_FOLDER = Path.home() / "Music" / "Music"


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
    # Where it goes under the destination; differs from relative when the
    # name had to be made Windows-safe.
    target: Path = None

    def __post_init__(self):
        if self.target is None:
            self.target = self.relative


@dataclass
class Plan:
    items: list = field(default_factory=list)
    total_bytes: int = 0
    existing: int = 0
    ignored: int = 0
    renamed: int = 0
    skipped: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


def is_junk(name):
    return name in JUNK_NAMES or name.startswith(JUNK_PREFIXES)


def drop_appledouble(path):
    """Remove the "._" file macOS just made next to path, if any.

    On exFAT and FAT drives macOS keeps extended attributes in a hidden
    "._<name>" file beside every file and folder it writes. On Windows these
    show up as broken copies (MusicBee may try to import "._song.m4a"), so
    remove the one belonging to an item this app has just created.
    """
    path = Path(path)

    try:
        os.unlink(path.with_name("._" + path.name))
    except OSError:
        pass


def drop_appledoubles_in(folder):
    """Remove the "._" files inside a folder tree this app just created."""
    for root_dir, dir_names, file_names in os.walk(folder):
        for name in file_names:
            if name.startswith("._"):
                try:
                    os.unlink(os.path.join(root_dir, name))
                except OSError:
                    pass


def windows_part(name):
    """Return name as Windows and exFAT will accept it."""
    name = unicodedata.normalize("NFC", name)
    cleaned = "".join(
        "_" if character in WINDOWS_BAD_CHARACTERS or ord(character) < 32
        else character
        for character in name
    )

    # Windows silently drops trailing dots and spaces.
    cleaned = cleaned.rstrip(" .") or "_"

    if cleaned.split(".")[0].rstrip(" ").upper() in WINDOWS_RESERVED_NAMES:
        cleaned = "_" + cleaned

    return cleaned


def windows_relative(relative):
    return Path(*(windows_part(part) for part in relative.parts))


def windows_path_length(destination, target):
    """Length of the path as Windows would see it, e.g. E:\\Music\\..."""
    parts = Path(os.path.abspath(destination)).parts

    if len(parts) >= 3 and parts[1] == "Volumes":
        inside = parts[3:]
    else:
        inside = parts[1:]

    return len("E:\\" + "\\".join((*inside, *target.parts)))


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


def build_plan(
    source,
    destination,
    should_cancel=None,
    on_scanned=None,
    windows_safe=False,
):
    """Find the items in source that are missing from destination.

    With windows_safe, names are changed so Windows and exFAT accept them,
    links are copied as the files they point to, and paths too long for
    Windows are flagged.
    """
    plan = Plan()
    scanned = 0
    claimed = {}

    def lstat_or_none(path):
        try:
            return os.lstat(path)
        except FileNotFoundError:
            return None

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
        target = windows_relative(relative) if windows_safe else relative

        try:
            source_info = os.lstat(source_path)
        except OSError as error:
            if is_missing_error(error):
                plan.skipped.append((relative, "no longer available"))
            else:
                plan.errors.append((relative, error.strerror or str(error)))
            return

        if windows_safe:
            key = name_key(target)

            if key in claimed:
                plan.skipped.append((
                    relative,
                    "same name on Windows as " + str(claimed[key]),
                ))
                return

            claimed[key] = relative

        try:
            destination_info = lstat_or_none(destination / target)

            # Copies made before names were made Windows-safe still count.
            # exFAT may reject the old name outright instead of not finding it.
            if destination_info is None and target != relative:
                try:
                    destination_info = lstat_or_none(destination / relative)
                except OSError:
                    destination_info = None
        except OSError as error:
            plan.errors.append((relative, error.strerror or str(error)))
            return

        if (
            windows_safe
            and destination_info is None
            and stat.S_ISLNK(source_info.st_mode)
        ):
            # exFAT can't store links, so copy what the link points to.
            try:
                source_info = os.stat(source_path)
            except OSError:
                plan.skipped.append((relative, "link to a missing file"))
                return

            if not stat.S_ISREG(source_info.st_mode):
                plan.skipped.append(
                    (relative, "link to a folder (exFAT can't store links)")
                )
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
            item = PlanItem(source_path, relative, True, 0, target)
        elif stat.S_ISREG(source_info.st_mode):
            item = PlanItem(
                source_path, relative, False, source_info.st_size, target
            )
            plan.total_bytes += source_info.st_size
        else:
            plan.skipped.append((relative, "special item"))
            return

        plan.items.append(item)

        if target != relative:
            plan.renamed += 1

        if windows_safe:
            length = windows_path_length(destination, target)

            if length > WINDOWS_MAX_PATH:
                plan.warnings.append((
                    target,
                    f"path is {length} characters; some Windows apps "
                    f"stop at {WINDOWS_MAX_PATH + 1}",
                ))

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
            if is_junk(directory_name):
                plan.ignored += 1
                continue

            directory_path = root_path / directory_name

            # Symlinked folders are copied as links, not followed.
            if os.path.islink(directory_path):
                consider(directory_path)
            else:
                real_directories.append(directory_name)

        dir_names[:] = real_directories

        for file_name in sorted(file_names):
            if is_junk(file_name):
                plan.ignored += 1
                continue

            consider(root_path / file_name)

    plan.items.sort(key=lambda item: str(item.relative).lower())
    return plan


def partial_path(destination):
    name = PARTIAL_PREFIX + destination.name + PARTIAL_SUFFIX

    if len(name.encode("utf-8", "surrogateescape")) > MAX_NAME_BYTES:
        digest = hashlib.sha1(
            destination.name.encode("utf-8", "surrogateescape")
        ).hexdigest()
        name = PARTIAL_PREFIX + digest + PARTIAL_SUFFIX

    return destination.with_name(name)


def hide_file(path):
    """Mark a file hidden on Windows, where a leading dot doesn't do it."""
    if os.name != "nt":
        return

    try:
        import ctypes

        FILE_ATTRIBUTE_HIDDEN = 0x2
        ctypes.windll.kernel32.SetFileAttributesW(
            str(path), FILE_ATTRIBUTE_HIDDEN
        )
    except (OSError, AttributeError):
        pass


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
        else:
            drop_appledouble(folder)

    return folder


def name_key(relative):
    return unicodedata.normalize("NFC", str(relative)).casefold()


def name_clash_locks(items):
    """Return a lock for each destination name shared by several items.

    exFAT and default APFS ignore case, so "Song.mp3" and "song.mp3" from a
    case-sensitive source land on the same file. Copied at the same time,
    both would pass the exists check and one would replace the other.
    """
    counts = Counter(name_key(item.target) for item in items)
    return {key: threading.Lock() for key, count in counts.items() if count > 1}


def copy_item(item, destination_root, on_bytes=None, should_cancel=None):
    """Copy one planned item. Returns False if it appeared in the meantime."""
    ensure_parent(destination_root, item.target)
    destination = destination_root / item.target

    if os.path.lexists(destination):
        return False

    if item.is_symlink:
        os.symlink(os.readlink(item.source), destination)
        drop_appledouble(destination)
        return True

    temporary = partial_path(destination)

    try:
        with open(item.source, "rb") as reader, open(temporary, "wb") as writer:
            hide_file(temporary)

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
            drop_appledouble(temporary)
            return False

        os.rename(temporary, destination)

    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        drop_appledouble(temporary)
        raise

    drop_appledouble(destination)
    return True


def default_media_folder():
    """Return Apple Music's default media folder, if it exists."""
    media = DEFAULT_MUSIC_FOLDER / "Media.localized"

    if not media.is_dir():
        media = DEFAULT_MUSIC_FOLDER / "Media"

    if (media / "Music").is_dir():
        return media / "Music"

    return media if media.is_dir() else None


def find_music_library(source=None):
    """Find the Apple Music library database (a .musiclibrary bundle).

    It normally sits next to the Media folder, so look above the source
    first, then fall back to the default location.
    """
    candidates = []

    if source:
        candidates.extend(Path(source).expanduser().parents)

    candidates.append(DEFAULT_MUSIC_FOLDER)

    for folder in candidates:
        try:
            bundles = sorted(folder.glob("*.musiclibrary"))
        except OSError:
            continue

        for bundle in bundles:
            if bundle.is_dir():
                return bundle

    return None


def snapshot_stamp(now):
    # No colons: Windows and exFAT don't allow them.
    return now.strftime("%Y-%m-%d %H.%M.%S")


def snapshot_folder(destination_root, stamp):
    """Create a new dated folder under Library Backups.

    It's always a new folder, so an earlier backup is never replaced.
    """
    if not destination_root.is_dir():
        raise DestinationGone(str(destination_root))

    backups = destination_root / BACKUPS_FOLDER

    try:
        backups.mkdir()
        drop_appledouble(backups)
    except FileExistsError:
        pass

    folder = backups / stamp
    number = 2

    while True:
        try:
            folder.mkdir()
            drop_appledouble(folder)
            return folder
        except FileExistsError:
            folder = backups / (stamp + f" ({number})")
            number += 1


def copy_tree(source, destination, should_cancel=None):
    """Copy the folder source to destination, which must not exist yet.

    The copy is written under a temporary name and renamed when complete,
    so a cancelled or failed backup never looks like a finished one.
    Returns the number of bytes copied.
    """
    temporary = partial_path(destination)
    copied = 0

    try:
        for root_dir, dir_names, file_names in os.walk(source):
            if should_cancel is not None and should_cancel():
                raise Cancelled()

            root_path = Path(root_dir)
            target_root = temporary / root_path.relative_to(source)
            target_root.mkdir(exist_ok=True)

            dir_names[:] = [name for name in dir_names if not is_junk(name)]

            for file_name in file_names:
                if is_junk(file_name):
                    continue

                source_file = root_path / file_name

                if os.path.islink(source_file):
                    continue

                shutil.copyfile(source_file, target_root / file_name)
                copied += os.path.getsize(source_file)

        os.rename(temporary, destination)

    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        drop_appledouble(temporary)
        raise

    drop_appledouble(destination)
    drop_appledoubles_in(destination)
    return copied


# Reads every ordinary playlist from Apple Music as JSON:
# [{"name": ..., "tracks": [posix path or null, ...]}, ...]
PLAYLISTS_SCRIPT = """
function run() {
    var music = Application("Music");
    var lists = music.userPlaylists();
    var result = [];

    for (var i = 0; i < lists.length; i++) {
        var list = lists[i];

        if (list.specialKind() !== "none") {
            continue;
        }

        var locations = [];

        try {
            locations = list.fileTracks.location();
        } catch (error) {}

        result.push({
            name: list.name(),
            tracks: locations.map(function (location) {
                return location ? location.toString() : null;
            })
        });
    }

    return JSON.stringify(result);
}
"""


class PlaylistError(Exception):
    pass


def run_cancellable(command, should_cancel=None, timeout=600):
    """Run command, killing it if should_cancel() turns true."""
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    waited = 0.0

    while True:
        try:
            stdout, stderr = process.communicate(timeout=0.25)
            return process.returncode, stdout, stderr
        except subprocess.TimeoutExpired:
            waited += 0.25

            if should_cancel is not None and should_cancel():
                process.kill()
                process.communicate()
                raise Cancelled()

            if waited >= timeout:
                process.kill()
                process.communicate()
                raise


def read_playlists(should_cancel=None, timeout=600):
    """Ask Apple Music for its playlists. Returns [(name, [Path or None])]."""
    try:
        returncode, stdout, stderr = run_cancellable(
            ["osascript", "-l", "JavaScript", "-e", PLAYLISTS_SCRIPT],
            should_cancel,
            timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise PlaylistError("Apple Music didn't answer: " + str(error))

    if returncode != 0:
        message = stderr.strip()

        if "-1743" in message:
            raise PlaylistError(
                "Not allowed to read Apple Music playlists. Allow it in "
                "System Settings → Privacy & Security → Automation, "
                "then try again."
            )

        raise PlaylistError("Apple Music couldn't list playlists: " + message)

    try:
        data = json.loads(stdout)
    except ValueError as error:
        raise PlaylistError("Unexpected answer from Apple Music.") from error

    return [
        (
            str(entry.get("name") or "Untitled"),
            [Path(track) if track else None for track in entry.get("tracks", [])],
        )
        for entry in data
    ]


def relative_to_source(track, source):
    for base in (source, source.resolve()):
        for path in (track, track.resolve()):
            try:
                return path.relative_to(base)
            except ValueError:
                pass

    return None


def write_playlists(
    playlists,
    source,
    destination_root,
    folder,
    windows_safe=False,
):
    """Write each playlist as an .m3u8 file inside folder.

    Paths are relative to the playlist file, with Windows backslashes, so
    they keep working whatever drive letter the stick gets. Tracks that
    aren't in the source folder or weren't copied are left out.
    Returns (playlists written, tracks written, tracks left out).
    """
    folder.mkdir()
    drop_appledouble(folder)
    used = set()
    written = tracks_written = left_out = 0

    for name, tracks in playlists:
        lines = ["#EXTM3U"]

        for track in tracks:
            relative = None if track is None else relative_to_source(track, source)

            if relative is None:
                left_out += 1
                continue

            target = windows_relative(relative) if windows_safe else relative
            on_destination = destination_root / target

            if not on_destination.exists() and target != relative:
                on_destination = destination_root / relative

            if not on_destination.exists():
                left_out += 1
                continue

            lines.append(
                os.path.relpath(on_destination, folder).replace(os.sep, "\\")
            )
            tracks_written += 1

        file_name = windows_part(name)
        candidate = file_name
        number = 2

        while candidate.casefold() in used:
            candidate = file_name + f" ({number})"
            number += 1

        used.add(candidate.casefold())

        playlist = folder / (candidate + ".m3u8")

        with open(playlist, "w", encoding="utf-8") as file:
            file.write("\r\n".join(lines) + "\r\n")

        drop_appledouble(playlist)

        written += 1

    return written, tracks_written, left_out


def windows_drive_of(path):
    """Return E:\\ for a path on a drive other than the system drive."""
    if os.name != "nt":
        return None

    drive = os.path.splitdrive(os.path.abspath(path))[0]
    system = os.environ.get("SystemDrive", "C:").rstrip("\\/").upper()

    if len(drive) != 2 or drive[1] != ":" or drive.upper() == system:
        return None

    return Path(drive + "\\")


def ejectable_drive(path):
    """The external drive a path is on, or None if it isn't on one."""
    path = path or ""

    if not path.strip():
        return None

    drive = windows_drive_of(path)

    if drive is not None:
        return drive if drive.exists() else None

    return volume_of(path)


def drive_label(volume):
    return volume.name or str(volume).rstrip("\\/")


def volume_of(path):
    """Return /Volumes/<name> for a path on an external drive, else None."""
    parts = Path(os.path.abspath(path)).parts

    if len(parts) >= 3 and parts[1] == "Volumes":
        volume = Path(*parts[:3])

        if os.path.ismount(volume) and not os.path.islink(volume):
            return volume

    return None


def music_is_running():
    try:
        return subprocess.run(
            ["pgrep", "-x", "Music"], capture_output=True
        ).returncode == 0
    except OSError:
        return False


# Only look one level further into folders with this few subfolders, so a
# drive full of artist folders isn't scanned twice.
MATCH_MAX_SUBFOLDERS = 20
MATCH_MIN_SHARED = 3


def child_keys(folder):
    try:
        names = os.listdir(folder)
    except OSError:
        return set()

    return {
        name_key(Path(windows_part(name)))
        for name in names
        if not is_junk(name) and name != BACKUPS_FOLDER
    }


def nearby_folders(root):
    """Subfolders of root, and theirs, as long as there are only a few."""
    found = []
    level = [root]

    for _depth in range(2):
        next_level = []

        for folder in level:
            try:
                subfolders = [
                    entry.path for entry in os.scandir(folder)
                    if entry.is_dir(follow_symlinks=False)
                    and not is_junk(entry.name)
                    and entry.name != BACKUPS_FOLDER
                ]
            except OSError:
                continue

            if len(subfolders) > MATCH_MAX_SUBFOLDERS:
                continue

            next_level.extend(Path(path) for path in subfolders)

        found.extend(next_level)
        level = next_level

    return found


def better_folder_match(source, destination):
    """Spot a destination that holds this music one level up or down.

    For example the music was backed up to "Stick/Music" but the
    destination is now "Stick", or the music folder is now "~/Music/Music"
    instead of ".../Media.localized/Music". Every path then differs, so the
    whole library would be copied again. Returns None, or a dict with
    "role" ("source" or "destination"), "path" and "message".
    """
    source_keys = child_keys(source)
    destination_keys = child_keys(destination)

    if not source_keys:
        return None

    current = len(source_keys & destination_keys)
    best = None

    for folder in nearby_folders(destination):
        shared = len(source_keys & child_keys(folder))

        if best is None or shared > best[0]:
            best = (shared, "destination", folder)

    for folder in nearby_folders(source):
        folder_keys = child_keys(folder)
        shared = len(folder_keys & destination_keys)

        if best is None or shared > best[0]:
            best = (shared, "source", folder)

    if best is None:
        return None

    shared, role, folder = best

    if shared < MATCH_MIN_SHARED or shared <= current * 2:
        return None

    if role == "destination":
        message = (
            "\"" + str(folder) + "\" already has " + f"{shared:,}"
            + " of the same folders as your music folder. Choose it as the "
            "destination, or this backup will copy your music a second time."
        )
    else:
        message = (
            "The destination matches \"" + str(folder) + "\" inside your "
            "music folder (" + f"{shared:,}" + " of the same folders). Choose "
            "it as the music folder, or this backup will copy your music a "
            "second time."
        )

    return {"role": role, "path": str(folder), "message": message}


def list_drive_roots():
    """Return the root folders of the drives that are plugged in.

    On Windows that is every drive letter except the system drive; elsewhere
    it is each volume under /Volumes (macOS) or /media and /mnt (Linux).
    """
    if os.name == "nt":
        import string

        system = os.environ.get("SystemDrive", "C:").rstrip("\\/").upper()
        roots = []

        for letter in string.ascii_uppercase:
            if letter + ":" == system:
                continue

            root = Path(letter + ":\\")

            try:
                if root.exists():
                    roots.append(root)
            except OSError:
                continue

        return roots

    roots = []

    for base in ("/Volumes", "/media", "/mnt"):
        try:
            roots.extend(
                Path(entry.path) for entry in os.scandir(base)
                if entry.is_dir() and not entry.is_symlink()
            )
        except OSError:
            continue

    return roots


def find_backup_folder(root):
    """Find the music folder a Music Library Backup left on a drive.

    The backup folder is the one that holds "Library Backups". Failing that,
    a folder named "Music" on the drive is used. Returns None if neither exists.
    """
    root = Path(root)

    try:
        if (root / BACKUPS_FOLDER).is_dir():
            return root

        children = sorted(
            (
                Path(entry.path) for entry in os.scandir(root)
                if entry.is_dir(follow_symlinks=False) and not is_junk(entry.name)
            ),
            key=lambda path: path.name.casefold(),
        )
    except OSError:
        return None

    for child in children:
        try:
            if (child / BACKUPS_FOLDER).is_dir():
                return child
        except OSError:
            continue

    for child in children:
        if child.name.casefold() == "music":
            return child

    return None


def find_backup_drives(roots=None):
    """Return [(drive root, backup folder)] for drives holding a backup."""
    found = []

    for root in (list_drive_roots() if roots is None else roots):
        folder = find_backup_folder(root)

        if folder is not None:
            found.append((Path(root), folder))

    return found


def latest_playlists_folder(root):
    """Return the Playlists folder of the newest library backup, or None."""
    backups = Path(root) / BACKUPS_FOLDER

    try:
        folders = sorted(
            (entry for entry in backups.iterdir() if entry.is_dir()),
            key=lambda path: path.name,
            reverse=True,
        )
    except OSError:
        return None

    for folder in folders:
        playlists = folder / PLAYLISTS_FOLDER

        if playlists.is_dir():
            return playlists

    return None


def default_import_folder():
    """Where music is imported to on this computer by default."""
    return Path.home() / "Music" / "Apple Music Library"


def musicbee_playlists_folder():
    """MusicBee's own Playlists folder, if MusicBee has been set up here."""
    folder = Path.home() / "Music" / "MusicBee" / "Playlists"
    return folder if folder.is_dir() else None


def load_playlist_state(state_file):
    """Read {playlist name: fingerprint of what this app last wrote}."""
    try:
        with open(state_file, encoding="utf-8") as file:
            state = json.load(file)
    except (OSError, ValueError):
        return {}

    return state if isinstance(state, dict) else {}


def save_playlist_state(state_file, state):
    state_file = Path(state_file)
    state_file.parent.mkdir(parents=True, exist_ok=True)

    with open(state_file, "w", encoding="utf-8") as file:
        json.dump(state, file, indent=2)


def fingerprint(data):
    return hashlib.sha256(data).hexdigest()


def add_playlists_to_musicbee(playlists_folder, target_folder, state_file=None):
    """Copy .m3u8 playlists into MusicBee's Playlists folder.

    The backup writes playlists with paths relative to their own folder,
    which stop working once a file is moved, so the copies get full paths.
    Tracks that aren't on this computer are left out.

    A playlist this app wrote earlier is updated, but only while it still
    matches what was written; one you've since edited in MusicBee, or made
    yourself, is never touched. state_file remembers what was written.
    Returns a dict of counts: written, updated, unchanged, kept_edited,
    kept_other, tracks and left_out.
    """
    playlists_folder = Path(playlists_folder)
    target_folder = Path(target_folder)
    state = load_playlist_state(state_file) if state_file else {}
    mine = state.setdefault(str(target_folder).casefold(), {})
    counts = dict(
        written=0, updated=0, unchanged=0, kept_edited=0, kept_other=0,
        tracks=0, left_out=0,
    )

    for playlist in sorted(playlists_folder.glob("*.m3u8")):
        target = target_folder / playlist.name
        key = playlist.name.casefold()
        lines = ["#EXTM3U"]
        tracks = left_out = 0

        with open(playlist, encoding="utf-8-sig") as file:
            entries = [line.strip() for line in file]

        for entry in entries:
            if not entry or entry.startswith("#"):
                continue

            track = Path(os.path.normpath(
                playlists_folder / entry.replace("\\", os.sep).replace("/", os.sep)
            ))

            if track.is_file():
                lines.append(str(track))
                tracks += 1
            else:
                left_out += 1

        data = ("\r\n".join(lines) + "\r\n").encode("utf-8")
        new_print = fingerprint(data)
        outcome = "written"

        if target.exists():
            current = fingerprint(target.read_bytes())

            if key not in mine:
                outcome = "kept_other"
            elif current == new_print:
                outcome = "unchanged"
            elif current == mine[key]:
                outcome = "updated"
            else:
                outcome = "kept_edited"

        if outcome in ("written", "updated"):
            target_folder.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            mine[key] = new_print
        elif outcome == "unchanged":
            mine[key] = new_print

        counts[outcome] += 1

        if outcome in ("written", "updated", "unchanged"):
            counts["tracks"] += tracks
            counts["left_out"] += left_out

    if state_file:
        save_playlist_state(state_file, state)

    return counts
