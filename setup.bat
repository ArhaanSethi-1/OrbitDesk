@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo ========================================
echo       OrbitDesk Jarvis Setup
echo ========================================
echo.

set "PYTHON_LAUNCHER="
where py >nul 2>&1
if not errorlevel 1 (
    for %%V in (3.12 3.11 3.10) do (
        py -%%V -c "import sys" >nul 2>&1
        if not errorlevel 1 if not defined PYTHON_LAUNCHER set "PYTHON_LAUNCHER=py -%%V"
    )
)

if not defined PYTHON_LAUNCHER (
    where python >nul 2>&1
    if errorlevel 1 (
        echo Python 3.10, 3.11, or 3.12 was not found.
        echo Install Python from https://www.python.org/downloads/
        pause
        exit /b 1
    )
    python -c "import sys; exit(0 if (3,10) <= sys.version_info[:2] <= (3,12) else 1)"
    if errorlevel 1 (
        echo Python 3.10 through 3.12 is required.
        pause
        exit /b 1
    )
    set "PYTHON_LAUNCHER=python"
)

if not defined PYTHON_LAUNCHER (
    echo Python 3.10, 3.11, or 3.12 was not found.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo Creating the OrbitDesk virtual environment...
    %PYTHON_LAUNCHER% -m venv .venv
    if errorlevel 1 goto :error
)

".venv\Scripts\python.exe" -c "import sys; exit(0 if (3,10) <= sys.version_info[:2] <= (3,12) else 1)"
if errorlevel 1 (
    echo The existing .venv must use Python 3.10, 3.11, or 3.12.
    echo Rename .venv and run setup.bat again to recreate it.
    goto :error
)

".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :error
".venv\Scripts\python.exe" scripts\setup_app.py
if errorlevel 1 goto :error
exit /b 0

:error
echo.
echo Setup could not complete. Review the error above.
pause
exit /b 1
