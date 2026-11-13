@echo off
rem Registers the daily task, or moves it after a change in tools\schedule_windows.ps1.
rem The result and the next run time are written to private\schedule_log.txt
cd /d "%~dp0.."
if not exist private mkdir private
powershell -NoProfile -ExecutionPolicy Bypass -File tools\schedule_windows.ps1 > private\schedule_log.txt 2>&1
schtasks /query /tn "Freight arb private build" /fo LIST >> private\schedule_log.txt 2>&1
