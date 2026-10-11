@echo off
setlocal
cd /d "%~dp0"

rem ---- Python must exist ----
python -c "import sys" >nul 2>&1
if errorlevel 1 (
    echo.
    echo ============================================
    echo   Python was not found.
    echo   Install Python 3.11 from https://www.python.org/downloads/
    echo   and tick "Add python.exe to PATH" in the installer.
    echo.
    echo   Then run this file again.
    echo ============================================
    goto :stay
)

rem ---- Must be 64-bit ----
python -c "import struct,sys; sys.exit(0 if struct.calcsize('P')==8 else 1)" >nul 2>&1
if errorlevel 1 (
    echo.
    echo ============================================
    echo   You have 32-bit Python. The 64-bit version is required.
    echo   Install 64-bit Python 3.11 from https://www.python.org/downloads/
    echo.
    echo   Then run this file again.
    echo ============================================
    goto :stay
)

rem ---- Version: recommendation only, never blocks ----
python -c "import sys; sys.exit(0 if (3,10)<=sys.version_info[:2]<=(3,12) else 1)" >nul 2>&1
if errorlevel 1 (
    echo.
    echo   NOTE: Python 3.11 is recommended. Your version may still work,
    echo   but if you hit crashes, try installing Python 3.11.
    echo.
)

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
exit /b %EXITCODE%

:stay
echo.
echo   You can close this window when you're done reading.
cmd /k