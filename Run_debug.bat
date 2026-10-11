@echo off
cd /d "%~dp0"
set SMARTSONG_DEBUG=1
set PYGAME_HIDE_SUPPORT_PROMPT=1
echo ============================================
echo   SmartSongShuffle - DEBUG MODE
echo   (terminal will stay open after exit)
echo ============================================
echo.
python Main.py
echo.
echo ==== Main.py exited with code %ERRORLEVEL% ====
pause