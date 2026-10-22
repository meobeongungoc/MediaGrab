@echo off
setlocal
cd /d "%~dp0"
if exist "releases\0.8.2\MediaGrab\MediaGrab.exe" (
    start "" "%~dp0releases\0.8.2\MediaGrab\MediaGrab.exe"
    exit /b
)
if exist ".venv\Scripts\pythonw.exe" (
    start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0launch_gui.py"
    exit /b
)
echo MediaGrab could not be found. Keep the complete application folder together.
pause
