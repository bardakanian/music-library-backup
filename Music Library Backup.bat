@echo off
rem Double-click to launch Music Library Backup on Windows.
rem On first run it creates the virtual environment and installs PySide6.

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    py -3 -m venv .venv || python -m venv .venv || goto :failed
)

".venv\Scripts\python.exe" -c "import PySide6" 2>nul
if errorlevel 1 (
    echo Installing PySide6 ^(first run only^)...
    ".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
    ".venv\Scripts\python.exe" -m pip install --quiet PySide6 || goto :failed
)

start "" ".venv\Scripts\pythonw.exe" app_qt.py
exit /b 0

:failed
echo.
echo Could not set up Music Library Backup. Is Python 3.10 or newer installed?
pause
exit /b 1
