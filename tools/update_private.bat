@echo off
rem Morning update of the private build: LSEG rates first, then the dashboard.
rem Workspace has to be open and signed in. Log in private\update_log.txt
cd /d "%~dp0.."
call tools\find_python.bat
if "%PY%"=="" (
  echo Python not found >> private\update_log.txt
  exit /b 1
)
%PY% tools\lseg_pull.py > private\update_log.txt 2>&1
if errorlevel 1 echo LSEG returned nothing, building with the last rates in private\freight.csv >> private\update_log.txt
%PY% run.py --private private >> private\update_log.txt 2>&1
if "%1"=="--open" start "" build_private\index.html
