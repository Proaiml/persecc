<#
.SYNOPSIS
  Installs SecondX as a Windows background service (Scheduled Task, runs as SYSTEM at boot).
.EXAMPLE
  # Run in an elevated PowerShell inside the SecondX folder:
  powershell -ExecutionPolicy Bypass -File .\install_windows.ps1 -InfluxToken "<token>"
#>
param(
    [string]$InfluxToken = "",
    [string]$InstallDir = $PSScriptRoot,
    [string]$TaskName = "SecondX"
)
$ErrorActionPreference = "Stop"

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Please run this script in an elevated (Administrator) PowerShell."
}

# 1) Python
$py = Get-Command py -ErrorAction SilentlyContinue
$pyArgs = @("-3")
if (-not $py) { $py = Get-Command python -ErrorAction SilentlyContinue; $pyArgs = @() }
if (-not $py) { throw "Python 3.8+ not found. Install it from https://www.python.org/downloads/ (tick 'Add python.exe to PATH')." }

# 2) Private virtual environment + requirements
$venv = Join-Path $InstallDir ".venv"
$venvPy = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    Write-Host "Creating virtual environment in $venv"
    & $py.Source @pyArgs -m venv $venv
}
& $venvPy -m pip install --disable-pip-version-check -q -r (Join-Path $InstallDir "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "pip install failed (check internet / proxy settings)." }

# 3) Token as a machine-level environment variable (kept out of config.json)
if ($InfluxToken) {
    [Environment]::SetEnvironmentVariable("SECONDX_INFLUX_TOKEN", $InfluxToken, "Machine")
    $env:SECONDX_INFLUX_TOKEN = $InfluxToken
    Write-Host "InfluxDB token saved to machine environment variable SECONDX_INFLUX_TOKEN."
}

# 4) Validate configuration before registering
& $venvPy (Join-Path $InstallDir "SecondX.py") --config (Join-Path $InstallDir "config.json") --check
if ($LASTEXITCODE -ne 0) { Write-Warning "Config check reported a problem (see above). The service is installed anyway and keeps retrying." }

# 5) Scheduled task: start at boot, run as SYSTEM, restart on failure, no time limit
$arguments = '"{0}" --config "{1}"' -f (Join-Path $InstallDir "SecondX.py"), (Join-Path $InstallDir "config.json")
$action   = New-ScheduledTaskAction -Execute $venvPy -Argument $arguments -WorkingDirectory $InstallDir
$trigger  = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
            -StartWhenAvailable -MultipleInstances IgnoreNew
$system   = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $system -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName

Write-Host ""
Write-Host "SecondX installed and started as scheduled task '$TaskName'."
Write-Host "  Status : Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo"
Write-Host "  Logs   : $InstallDir\logs\secondx.log"
Write-Host "  Remove : powershell -ExecutionPolicy Bypass -File .\uninstall_windows.ps1"
