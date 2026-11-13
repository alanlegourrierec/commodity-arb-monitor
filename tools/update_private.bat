@echo off
rem Morning update of the private build: LSEG rates first, then the dashboard.
rem Workspace has to be open and signed in. If it is not open yet, the page is built with the last
rem rates, the script waits for Workspace for up to ten hours, then builds again with the new ones.
rem Log in private\update_log.txt
cd /d "%~dp0.."
call tools\find_python.bat
if "%PY%"=="" (
  echo Python not found >> private\update_log.txt
  exit /b 1
)
%PY% tools\lseg_pull.py > private\update_log.txt 2>&1
if errorlevel 1 (
  echo Workspace is not open yet. Building with the last rates, then waiting for it >> private\update_log.txt
  %PY% run.py --private private >> private\update_log.txt 2>&1
  if "%1"=="--open" start "" build_private\index.html
  %PY% tools\lseg_pull.py --wait 600 >> private\update_log.txt 2>&1
  if errorlevel 1 exit /b 0
)
%PY% run.py --private private >> private\update_log.txt 2>&1
if "%1"=="--open" start "" build_private\index.html
