@echo off
setlocal EnableDelayedExpansion
rem One-time setup on Windows. Workspace must be open and signed in.
rem It installs Python if needed, the packages, stores your LSEG app key, pulls the rates once,
rem registers the 07:30 weekday task and opens the private build.
cd /d "%~dp0.."
set LOG=private\setup_log.txt
if not exist private mkdir private
echo ==== %date% %time% > %LOG%

call tools\find_python.bat
if "!PY!"=="" (
  echo [0/5] Python is not installed. Installing Python 3.12 for your user account...
  winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
  call tools\find_python.bat
)
if "!PY!"=="" (
  echo.
  echo Python could not be installed automatically.
  echo Install it from https://www.python.org/downloads/ ^(tick "Add python.exe to PATH"^),
  echo then double-click setup_windows.bat again.
  pause
  exit /b 1
)
echo Using !PY!
echo Using !PY! >> %LOG%

echo [1/5] Python packages
!PY! -m pip install --quiet --upgrade pip >> %LOG% 2>&1
!PY! -m pip install --quiet -r requirements.txt -r requirements-lseg.txt >> %LOG% 2>&1
if errorlevel 1 (
  echo Package install failed, see %LOG%
  pause
  exit /b 1
)

if "%LSEG_APP_KEY%"=="" (
  echo.
  echo [2/5] LSEG app key
  echo In Workspace, search APPKEY, register an app with EDP API and Eikon Data API ticked, copy the key.
  set /p KEY="Paste the key here and press Enter: "
  setx LSEG_APP_KEY "!KEY!" >nul
  set "LSEG_APP_KEY=!KEY!"
) else (
  echo [2/5] LSEG app key already set
)

echo [3/5] Pulling today's rates
!PY! tools\lseg_pull.py >> %LOG% 2>&1
type %LOG% | findstr /C:" ok" /C:"no data" /C:"stale"

echo [4/5] Daily task at 07:30, Monday to Friday
powershell -NoProfile -ExecutionPolicy Bypass -File tools\schedule_windows.ps1 >> %LOG% 2>&1

echo [5/5] Building the private dashboard
!PY! run.py --private private >> %LOG% 2>&1
if exist build_private\index.html (
  start "" build_private\index.html
  echo.
  echo Done. Full log in %LOG%
) else (
  echo.
  echo The build failed. Tell Claude, the details are in %LOG%
)
pause
