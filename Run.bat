@echo off
cd /d "%~dp0"

REM --- dependency check (visible; only does work on first run) ---
python -c "import librosa, numpy, pandas, matplotlib, pygame, mutagen, PIL, winsdk" 2>nul
if errorlevel 1 (
    echo Installing missing dependencies...
    python -m pip install librosa numpy pandas matplotlib pygame mutagen pillow winsdk
    if errorlevel 1 (
        echo.
        echo Installation failed. Fix the error above and try again.
        pause
        exit /b 1
    )
)

REM --- launch the GUI detached, then close this terminal ---
start "" pythonw Main.py
exit