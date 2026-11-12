r"""Create a "Music Library Backup" shortcut on the Windows desktop.

The shortcut starts app_qt.py from this project with the project's .venv, so
it always runs your latest code. Run it again if you move the project folder.

    .venv\Scripts\python.exe make_launcher_windows.py [--no-desktop]

--no-desktop puts the shortcut in build\ only.
"""

import os
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

import app_qt

PROJECT = Path(__file__).resolve().parent


def make_icon(path):
    app = QApplication.instance() or QApplication([])
    pixmap = app_qt.make_icon_pixmap(256)
    path.parent.mkdir(exist_ok=True)

    if not pixmap.save(str(path), "ICO"):
        raise SystemExit("Could not write the icon " + str(path))

    return app


def desktop_folder():
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "[Environment]::GetFolderPath('Desktop')"],
        capture_output=True, text=True, check=True,
    )
    return Path(result.stdout.strip())


def make_shortcut(link, icon):
    python = PROJECT / ".venv" / "Scripts" / "pythonw.exe"

    if not python.exists():
        raise SystemExit("Run Music Library Backup.bat once first to create .venv.")

    script = (
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:LNK);"
        "$s.TargetPath=$env:PY;$s.Arguments='app_qt.py';"
        "$s.WorkingDirectory=$env:DIR;$s.IconLocation=$env:ICO;"
        "$s.Description='Music Library Backup';$s.Save()"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        check=True,
        env={**os.environ, "LNK": str(link), "PY": str(python),
             "DIR": str(PROJECT), "ICO": str(icon)},
    )


def main():
    icon = PROJECT / "build" / "Music Library Backup.ico"
    make_icon(icon)
    folder = PROJECT / "build" if "--no-desktop" in sys.argv else desktop_folder()
    link = folder / (app_qt.APP_NAME + ".lnk")
    make_shortcut(link, icon)
    print("Created " + str(link))


if __name__ == "__main__":
    main()
