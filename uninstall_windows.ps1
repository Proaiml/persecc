<#
.SYNOPSIS
  Stops and removes the SecondX scheduled task (program files and logs are kept).
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\uninstall_windows.ps1 -RemoveToken
#>
param([string]$TaskName = "SecondX", [switch]$RemoveToken)
$ErrorActionPreference = "Stop"
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Scheduled task '$TaskName' removed."
} else {
    Write-Host "Scheduled task '$TaskName' not found."
}
if ($RemoveToken) {
    [Environment]::SetEnvironmentVariable("SECONDX_INFLUX_TOKEN", $null, "Machine")
    Write-Host "SECONDX_INFLUX_TOKEN removed."
}
