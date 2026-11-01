Bump `VERSION` in `app_qt.py` and add an entry here with every change.

# Changelog

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
