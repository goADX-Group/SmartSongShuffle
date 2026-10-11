@echo off
setlocal
cd /d "%~dp0"

python Main.py
set "EXITCODE=%ERRORLEVEL%"

if not "%EXITCODE%"=="0" (
    echo.
    echo ============================================
    echo   Main.py crashed or exited with code %EXITCODE%
    echo   Terminal will stay open so you can read errors.
    echo   Press any key to close.
    echo ============================================
    pause >nul
)

endlocal