Bump `VERSION` in `app_qt.py` and add an entry here with every change.

# Changelog

## 2.1.1 — 2026-11-11

- The GitHub repository is now `bardakanian/music-library-backup` (renamed from `copy-missing-files`; old links redirect). README clone instructions updated.

## 2.1.0 — 2026-11-11

- New safety check: when far more files look missing than are already there, the app checks whether the music folder and destination are one folder level apart (for example `~/Music/Music` instead of `Media.localized/Music`, or `Stick` instead of `Stick/Music`), which would copy the whole library a second time. Preview shows a warning; Back Up stops before copying and offers **Use That Folder**, **Back Up Anyway** or Cancel.
- Long status messages now wrap instead of being cut off.

## 2.0.1 — 2026-11-11

- Fix: on exFAT and FAT drives macOS wrote a hidden `._` file next to every copied file and new folder, which shows up on Windows as broken duplicates (such as `._song.m4a`) that MusicBee may try to import. The app now removes the `._` file of each item it creates, including library backups and playlists. Tested on a real exFAT volume.
- Fix: a failed or cancelled library backup left an empty dated folder in `Library Backups`.
- Fix: when two files would get the same Windows name, which one was copied could change between runs; it's now always the same one.

## 2.0.0 — 2026-11-11

- Renamed to **Music Library Backup**, focused on copying an Apple Music library to a USB stick for Windows and on backups. Saved folders from Copy Missing Files carry over; the launcher is now `Music Library Backup.command` and the bundle `Music Library Backup.app`.
- New **Windows-safe names** option (on by default): replaces characters Windows and exFAT reject, fixes trailing dots/spaces and reserved names, stores names as NFC, copies links as real files, flags paths over 259 characters, and skips files whose Windows names would clash. Copies made under the original names still count as already there.
- Mac-only clutter (`.DS_Store`, `._*`, `.localized`, `Icon\r`) is no longer copied.
- New **Back up library database** option: each run saves `Music Library.musiclibrary` in a new dated folder under `Library Backups/`, so earlier backups are never replaced.
- New **Export playlists (.m3u8)** option: reads playlists from Apple Music and writes them with relative Windows paths for MusicBee, leaving out tracks that aren't files in the music folder.
- New **Use Apple Music Library** button and an **Eject** button for the destination drive.
- Preview shows each file's new Windows-safe name; path-length warnings appear under Problems.

## 1.4.0 — 2026-11-08

- Copy up to 4 files at the same time (PySide6 app). Progress shows the files in flight, and Cancel, a full drive or an unplugged drive stop all of them and remove their partial copies.
- Names that differ only by case or Unicode form are copied one after the other, so neither can replace the other on drives that ignore case.

## 1.3.1 — 2026-11-02

- Fix: the summary said "1 item need attention" instead of "1 item needs attention".
- Add README with screenshots, MIT license, and sanitized demo screenshots in `assets/screenshots/`.

## 1.3.0 — 2026-11-02

- Redesigned window: app icon and version badge, Source → Destination folder cards with drop targets, free space and "drive not connected" warnings, and a progress card with size, speed, time left and the current file.
- New Preview button that lists what's missing (and its total size) without copying anything.
- Activity log can be filtered (Copied / To copy / Problems), copied to the clipboard, and the destination opened in Finder.
- New `make_launcher.py` builds `Copy Missing Files.app` with the app icon and version and installs it in Applications.
- Copy logic moved to `copy_core.py`, shared by the PySide6 and Tkinter apps.
- Fix: an interrupted copy left a half-written file that later runs treated as already copied; files are now written under a temporary name and renamed when complete.
- Fix: with the external drive unplugged, the destination path was recreated under `/Volumes` on the internal disk and filled there; a missing drive is now reported, also if it disconnects mid-copy.
- Fix: folders that couldn't be read were silently skipped; they're now reported as failures.
- Fix: the destination folder was created before checking it wasn't inside the source.
- Fix: a full destination drive failed every remaining file; free space is now checked first and the copy stops if the drive fills up.
- Fix: Cancel only took effect between files; it now stops within a large file and removes the partial copy.
- Fix: per-file UI updates slowed large copies; updates are now batched.
- Fix: a settings file that wasn't a JSON object crashed the app at startup.
- Fix: the Tkinter app quit mid-copy without asking.
- Tests no longer read the real settings file.

## 1.2.0 — 2026-11-01

- Show the version number in the window title.
- Add `VERSION` constant in `app_qt.py` as the single source of truth, also set as the Qt application version.
- Add tests that the title shows `VERSION` and that this changelog has a matching entry.

## 1.1.0 — 2026-10-30

- New PySide6 interface (`app_qt.py`) with folder cards, progress bar and live Copied / Skipped / Failed counts.
- Add a Cancel button, and ask before quitting while a copy is running.
- Accept folders dragged from Finder onto the folder fields.
- Add `Copy Missing Files.command` launcher that sets up `.venv` and PySide6 on first run.

## 1.0.0 — 2026-08-14

- First release: Tkinter app (`main.py`) that copies files present in the source folder but missing from the destination, without overwriting or deleting anything.
- Recreates symlinks, skips special files and files that disappear mid-copy, and falls back to a plain copy when macOS metadata can't be copied.
- Remembers the source and destination folders between runs.
