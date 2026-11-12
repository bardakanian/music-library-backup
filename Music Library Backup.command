#!/bin/zsh
# Double-click in Finder to launch Music Library Backup.
# On first run it creates the virtual environment and installs PySide6.

set -e

cd "$(dirname "$0")"

VENV=".venv"
PYTHON="$VENV/bin/python"

if [[ ! -x "$PYTHON" ]]; then
    echo "Creating virtual environment..."
    python3 -m venv "$VENV"
fi

if ! "$PYTHON" -c "import PySide6" 2>/dev/null; then
    echo "Installing PySide6 (first run only)..."
    "$PYTHON" -m pip install --quiet --upgrade pip
    "$PYTHON" -m pip install --quiet PySide6
fi

# Start the app detached so this Terminal window can close right away.
nohup "$PYTHON" app_qt.py >/dev/null 2>&1 &
disown

# Close the Terminal window that macOS opened for this script.
osascript -e 'tell application "Terminal" to close (every window whose name contains "Music Library Backup.command")' >/dev/null 2>&1 &
exit 0
