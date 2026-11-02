# Copy Missing Files

A safe, add-only folder sync for macOS: copy everything that's missing from a
destination folder, such as a music library backup on an external drive, without
ever overwriting or deleting what's already there.

Copy Missing Files compares a source folder with a destination folder, shows you
exactly what's missing, and copies only those files. It's built with Python and
PySide6 and runs entirely on your Mac.

## Highlights

- Adds only what's missing. Files already in the destination are never
  overwritten, modified, or deleted.
- **Preview** lists every missing file and the total size before anything is copied.
- Live progress shows megabytes copied, speed, time left, and the file being copied.
- Counts for To copy, Copied, Already there, Skipped, and Failed update as it runs.
- Crash-safe copies: each file is written under a temporary name and renamed into
  place only when complete, so an interrupted copy never leaves a half-written file
  that a later run would mistake for a finished one.
- Detects an unplugged external drive, both before starting and mid-copy, instead
  of silently writing to the Mac's internal disk.
- Checks free space before copying and stops cleanly if the drive fills up.
- **Cancel** stops right away, even in the middle of a large file, and removes the
  partial copy.
- Flags files that exist in both places with different sizes, and leaves them alone.
- Reports folders it can't read instead of skipping them silently.
- Drag folders from Finder onto the Source or Destination cards.
- Filterable activity log (All, Copied, To copy, Problems), with **Copy Log** and
  **Show in Finder**.
- Remembers the last source and destination folders.
- Optional Applications launcher with its own icon.

## Screenshots

The screenshots below were captured from a disposable demo library containing
fictional artist names, album names, and filenames. They contain no personal
files or real library paths.

### Preview what's missing

See every missing file and its size before copying anything. One file exists in
both places with a different size, so it's flagged and left untouched.

![Preview listing missing files from a fictional demo library](assets/screenshots/preview.png)

### Copy with live progress

Follow the size copied, speed, time left, and the current file. Cancel at any time.

![Copy in progress with size, speed and time left](assets/screenshots/copying.png)

### Review the result

A summary of what was copied, what was already there, and anything that needs
attention.

![Finished copy summary with counts and activity log](assets/screenshots/finished.png)

## How it works

1. Choose a **source** folder (for example your Music library) and a
   **destination** folder (for example a folder on an external drive).
2. The app walks the source folder and, for every file, checks whether the same
   relative path exists in the destination. Symlinks are recreated as symlinks
   rather than followed.
3. Anything missing is added to a plan. Files that already exist are counted as
   **Already there**. If the sizes differ, they're listed under **Problems** and
   left as they are.
4. **Preview** stops here and shows the plan. **Copy Missing Files** first checks
   that the destination has enough free space, then copies each planned file
   through a temporary file that is renamed into place when complete.

Matching is by path only, so a file that changed in the source isn't recopied if a
file with the same name is already in the destination (unless the sizes differ, in
which case it's reported). Empty folders aren't copied.

## Requirements

- macOS 11 or later
- Python 3.10 or newer
- [PySide6](https://doc.qt.io/qtforpython-6/), the only dependency

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/bardakanian/copy-missing-files.git
cd copy-missing-files
```

### 2. Create a virtual environment and install PySide6

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install PySide6
```

Alternatively, double-click **Copy Missing Files.command** in Finder. On first run
it creates `.venv`, installs PySide6, and starts the app. If macOS blocks it,
right-click the file and choose **Open**.

### 3. Launch the app

```bash
.venv/bin/python app_qt.py
```

### 4. Optional: add it to Applications

```bash
.venv/bin/python make_launcher.py
```

This builds `Copy Missing Files.app`, with its icon and version, and installs it in
`/Applications`, or in `~/Applications` if `/Applications` isn't writable. You can
then start it from Launchpad, Spotlight, or the Dock. Use `--no-install` to build it
only into `build/`.

The app runs `app_qt.py` from this project with the project's `.venv`, so it always
starts your latest code. Run the script again if you move the project folder.

## Using the app

- Click **Choose…** on the Source and Destination cards, or drag a folder from
  Finder onto a card.
- The destination card shows the free space on the drive. If the folder doesn't
  exist yet, the app offers to create it, as long as the folder that should
  contain it exists.
- Click **Preview** to see what would be copied, or **Copy Missing Files** (`⌘↩`)
  to start copying.
- Click **Cancel** (`⌘.`) to stop. Files that were fully copied stay. The file in
  progress is removed.
- Use the **Activity** menu to show all activity, only copied files, files still to
  copy, or problems.
- If you quit while a copy is running, the app asks first.

## Privacy and local data

Copy Missing Files works entirely offline. It has no account sign-in, telemetry,
analytics, advertising, or network access of any kind.

The only data it stores is the last-used source and destination folder paths, in:

```text
~/Library/Application Support/Copy Missing Files/settings.json
```

This file contains absolute paths on your computer. Don't publish or commit it.

The app only reads from the source folder. In the destination it only creates new
folders and files, plus a short-lived hidden temporary file (`.cmf-partial-…`) for
the file being copied.

## Project structure

```text
app_qt.py                    PySide6 app: window, folder cards, progress, worker thread
copy_core.py                 Copy logic with no GUI: validation, planning, safe copying
main.py                      Original Tkinter version (uses the same copy logic)
make_launcher.py             Builds and installs Copy Missing Files.app
Copy Missing Files.command   Double-click launcher that also sets up .venv
CHANGELOG.md                 Version history (the version itself is VERSION in app_qt.py)
tests/                       Copy logic, worker, version, and launcher tests
assets/screenshots/          Sanitized documentation screenshots
```

## Development

Run the tests from the project root:

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest -v
```

Check that the modules compile:

```bash
.venv/bin/python -m compileall -q app_qt.py main.py copy_core.py make_launcher.py
```

Each change bumps `VERSION` in `app_qt.py` and adds an entry to `CHANGELOG.md`. A
test checks that they match.

## Troubleshooting

- **"Drive not connected":** the source or destination is on an external drive
  that isn't mounted. Connect it, then switch back to the window to refresh.
- **A file is listed under Problems as "a different version is already there":**
  the destination has a file with the same name but a different size. The app never
  overwrites it. Compare the two and replace it yourself if needed.
- **"Not enough space":** free up space on the destination drive or choose a
  different one. Nothing is copied when there isn't room for everything.
- **The Applications launcher says the project folder was moved:** run
  `make_launcher.py` again from the project's new location.

## Project status

The app is a personal tool under active development. Keep a backup of anything
important before copying large libraries.

## Development approach

Developed with AI-assisted tools under human direction, review, testing, and refinement.

## License

This project is available under the [MIT License](LICENSE).
