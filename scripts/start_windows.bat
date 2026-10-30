@echo off
rem Open Workflow starter for Windows. Serves the UI and API on 127.0.0.1 only; nothing is exposed to the network.
rem Uses .venv if setup created it. If Open Workflow is already running on 8765, opens it instead of starting a second copy.
rem Close this window to stop the server. Logs: data\logs\openworkflow.log
setlocal enabledelayedexpansion
set "ROOT=%~dp0.."

cd /d "%ROOT%\backend" || (echo Could not find the backend folder. & pause & exit /b 1)

if exist "%ROOT%\.venv\Scripts\python.exe" (
  set "PY=%ROOT%\.venv\Scripts\python.exe"
) else (
  set "PY=python"
)

"!PY!" -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3, 11) else 1)" >nul 2>nul || (echo Python 3.11 or newer was not found. Run scripts\setup_windows.ps1 after installing Python 3.12. & pause & exit /b 1)
"!PY!" -c "import uvicorn, fastapi" >nul 2>nul || (echo The Python packages are not installed. Run scripts\setup_windows.ps1 once. & pause & exit /b 1)
if not exist "%ROOT%\frontend\dist\index.html" (echo The interface files are missing. Restore frontend\dist from git. & pause & exit /b 1)

rem Already running? Only an Open Workflow answer counts; any other program on 8765 is left alone.
"!PY!" -c "import urllib.request, json, sys; sys.exit(0 if json.load(urllib.request.urlopen('http://127.0.0.1:8765/api/health', timeout=2)).get('app') == 'openfield' else 1)" >nul 2>nul
if not errorlevel 1 (
  echo Open Workflow is already running at http://127.0.0.1:8765 . Opening it; no second copy was started.
  start "" http://127.0.0.1:8765
  exit /b 0
)

set PORT=8765
:findport
netstat -ano | findstr /R /C:"127\.0\.0\.1:!PORT! " | findstr LISTENING >nul
if not errorlevel 1 (
  set /a PORT+=1
  if !PORT! GTR 8800 (echo No free port between 8765 and 8800. Close another program and try again. & pause & exit /b 1)
  goto findport
)

set URL=http://127.0.0.1:!PORT!
echo Starting Open Workflow at !URL!
echo Close this window to stop it.

set EDGE="%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"
if not exist !EDGE! set EDGE="%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"
if exist !EDGE! (
  start "" cmd /c "timeout /t 3 /nobreak >nul & start "" !EDGE! --app=!URL!"
) else (
  start "" cmd /c "timeout /t 3 /nobreak >nul & start "" !URL!"
)

"!PY!" -m uvicorn openfield.app:app --host 127.0.0.1 --port !PORT!
echo.
echo Open Workflow stopped.
pause
