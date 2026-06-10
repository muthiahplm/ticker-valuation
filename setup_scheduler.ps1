# setup_scheduler.ps1
# ──────────────────────────────────────────────────────────────
# Registers PLM Ticker Watcher daily recalculation as a
# Windows Task Scheduler job that runs every day at 7:30 AM.
#
# USAGE (run once, as Administrator in PowerShell):
#   Set-ExecutionPolicy -Scope Process Bypass
#   .\setup_scheduler.ps1
#
# To change the time: edit $TriggerTime below, then re-run.
# To remove the task: Unregister-ScheduledTask -TaskName "PLM Ticker Watcher Recalc" -Confirm:$false
# ──────────────────────────────────────────────────────────────

$TaskName    = "PLM Ticker Watcher Recalc"
$ProjectDir  = Split-Path -Parent $MyInvocation.MyCommand.Definition
$BatchFile   = Join-Path $ProjectDir "run_recalc.bat"
$TriggerTime = "07:30"   # <-- change this to your preferred daily run time

if (-not (Test-Path $BatchFile)) {
    Write-Error "run_recalc.bat not found at: $BatchFile"
    Write-Error "Make sure you run this script from your project folder."
    exit 1
}

# Remove existing task if present
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Removing existing task '$TaskName'..."
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

# Build the task
$trigger = New-ScheduledTaskTrigger -Daily -At $TriggerTime
$action  = New-ScheduledTaskAction `
               -Execute  "cmd.exe" `
               -Argument "/c `"$BatchFile`" >> `"$ProjectDir\daily_recalc.log`" 2>&1" `
               -WorkingDirectory $ProjectDir
$settings = New-ScheduledTaskSettingsSet `
               -ExecutionTimeLimit  (New-TimeSpan -Minutes 30) `
               -StartWhenAvailable  `
               -RunOnlyIfNetworkAvailable

Register-ScheduledTask `
    -TaskName  $TaskName `
    -Trigger   $trigger `
    -Action    $action `
    -Settings  $settings `
    -RunLevel  Highest `
    -Force

Write-Host ""
Write-Host "✅ Task '$TaskName' registered — runs daily at $TriggerTime"
Write-Host "   Project dir : $ProjectDir"
Write-Host "   Log file    : $ProjectDir\daily_recalc.log"
Write-Host ""
Write-Host "To run immediately for testing:"
Write-Host "   Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "   -- or --"
Write-Host "   python daily_recalc.py --dry-run"
