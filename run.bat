
@echo off
setlocal
cd /d "%~dp0"

echo ========================================
echo           OrbitDesk - Starting
echo ========================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo ERROR: OrbitDesk has not been set up.
    echo Run setup.bat first.
    pause
    exit /b 1
)

if not exist "src\orbitdesk\ui\dashboard.py" (
    echo ERROR: OrbitDesk dashboard package was not found.
    pause
    exit /b 1
)

:: Check Python version
".venv\Scripts\python.exe" -c "import sys; exit(0 if (3,10) <= sys.version_info[:2] <= (3,12) else 1)"
if errorlevel 1 (
    echo ERROR: The virtual environment must use Python 3.10, 3.11, or 3.12.
    echo Run setup.bat after correcting .venv.
    pause
    exit /b 1
)

if exist ".orbitdesk_setup.json" (
    ".venv\Scripts\python.exe" -c "import json; exit(0 if json.load(open('.orbitdesk_setup.json', encoding='utf-8')).get('mode') == 'local' else 1)"
    if errorlevel 1 goto :launch
)

:: Check Ollama installation
where ollama >nul 2>&1
if errorlevel 1 (
    echo WARNING: Ollama is not installed or not on PATH.
    echo OrbitDesk may not be able to generate AI answers.
    goto :launch
)

:: Start Ollama if its API is not responding
call :ollama_ok
if not errorlevel 1 goto :ollama_ready

echo Starting Ollama...
start "" /min ollama serve

set "OLLAMA_READY="
for /L %%i in (1,1,30) do (
    call :ollama_ok
    if not errorlevel 1 set "OLLAMA_READY=1"
    if defined OLLAMA_READY goto :ollama_ready
    timeout /t 1 /nobreak >nul
)

echo WARNING: Ollama did not respond within 30 seconds.
echo AI responses may not work.
goto :launch

:ollama_ready
echo Ollama API is responding.

:: Check for the exact model name
ollama list 2>nul | findstr /i /r /c:"^qwen2\.5:3b[ ]" >nul
if errorlevel 1 (
    echo WARNING: qwen2.5:3b was not found in the model list.
    echo Install it using: ollama pull qwen2.5:3b
    echo.
)

:launch
echo Launching OrbitDesk in the system tray...
echo Diagnostics are written to %LOCALAPPDATA%\OrbitDesk\logs\orbitdesk.log.
echo.

if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" -m orbitdesk.ui.dashboard
    exit /b 0
)

".venv\Scripts\python.exe" -m orbitdesk.ui.dashboard
set "EXIT_CODE=%errorlevel%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo OrbitDesk exited with error code %EXIT_CODE%.
    pause
)

exit /b %EXIT_CODE%

:ollama_ok
powershell -NoProfile -Command "try { Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/tags' -TimeoutSec 2 | Out-Null; exit 0 } catch { exit 1 }" >nul 2>&1
if errorlevel 1 exit /b 1
exit /b 0
