# Registers a weekday 09:15 task that runs tools\update_private.bat.
# If Workspace is not open at that time the script waits for it, so the hour is only a starting point.
# Running it again replaces the task. From PowerShell in the repo folder:  powershell -ExecutionPolicy Bypass -File tools\schedule_windows.ps1
# The task runs only while you are logged on, because Workspace needs your session.
$repo = Split-Path -Parent $PSScriptRoot
$action = New-ScheduledTaskAction -Execute "$repo\tools\update_private.bat" -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At 9:15am
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName "Freight arb private build" -Action $action -Trigger $trigger -Settings $settings -Force `
    -Description "Pulls LSEG rates into private\freight.csv and rebuilds build_private\"
