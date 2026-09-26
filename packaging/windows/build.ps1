<#
.SYNOPSIS
  Builds dist\SecondX-Setup.exe from this source tree (no Python needed on the target servers).
.DESCRIPTION
  1. SecondX.exe (the agent) is built with PyInstaller in "onedir" mode inside a clean virtual
     environment: plain files in Program Files, nothing is unpacked to TEMP at every start, and
     only requirements.txt + PyInstaller end up in it.
  2. SecondX-Setup.exe is compiled with Inno Setup 6 (secondx.iss): a standard Windows installer
     with a Turkish / English wizard, silent install parameters and an Apps & features entry.
  3. With a code-signing certificate (e.g. Certum SimplySign) the agent, the setup and the
     uninstaller are signed (Authenticode, SHA-256, RFC 3161 timestamp) by sign.ps1.
     Without a certificate the build is simply unsigned.
  4. dist\SHA256SUMS.txt lets anyone verify the downloaded file.
  Anyone who prefers not to run a downloaded binary can build their own copy with it.
  Requirements: Python 3.9+ (py launcher) and Inno Setup 6 (winget install JRSoftware.InnoSetup).
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1
.EXAMPLE
  # signed build (thumbprint: certmgr.msc > Personal > Certificates > the code-signing certificate > Details)
  powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1 -CertThumbprint 0123ABCD...
#>
param(
    [string]$Python = "py",
    [string[]]$PythonArgs = @("-3.11"),
    [string]$CertThumbprint = $env:SECONDX_SIGN_THUMBPRINT,
    [string]$TimestampUrl = "http://time.certum.pl",
    [string]$Iscc = "",
    [switch]$AllowUntrustedCert        # only for testing the signing step with a self-signed certificate
)
$ErrorActionPreference = "Stop"
$root  = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$build = Join-Path $root "build"
$dist  = Join-Path $root "dist"
$version = ([regex]'__version__ = "([^"]+)"').Match((Get-Content (Join-Path $root "SecondX.py") -Raw)).Groups[1].Value
if (-not $version) { throw "__version__ not found in SecondX.py" }
Write-Host "Building SecondX $version"

if (-not $Iscc) {
    $Iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
              "$env:ProgramFiles\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $Iscc) { throw "Inno Setup 6 not found. Install it: winget install JRSoftware.InnoSetup" }

$signScript = Join-Path $PSScriptRoot "sign.ps1"
$signArgs = @()
if ($CertThumbprint) {
    $signArgs = @("-Thumbprint", $CertThumbprint, "-TimestampUrl", $TimestampUrl)
    if ($AllowUntrustedCert) { $signArgs += "-AllowUntrustedCert" }
    Write-Host "Code signing: certificate $CertThumbprint, timestamp $TimestampUrl"
} else {
    Write-Host "No -CertThumbprint / SECONDX_SIGN_THUMBPRINT: building UNSIGNED files."
}
function Sign-File([string]$path) {
    if (-not $CertThumbprint) { return }
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $signScript -Path $path @signArgs
    if ($LASTEXITCODE -ne 0) { throw "signing failed: $path" }
}

foreach ($d in @($build, $dist)) { if (Test-Path $d) { Remove-Item $d -Recurse -Force } }
New-Item -ItemType Directory -Force $build, $dist | Out-Null

# Clean, private build environment: only requirements.txt + PyInstaller end up in the exe
# (a system Python with pandas, numpy ... would silently bloat it) and the build is reproducible.
& $Python @PythonArgs -m venv (Join-Path $build "venv")
if ($LASTEXITCODE -ne 0) { throw "could not create the build venv" }
$py = Join-Path $build "venv\Scripts\python.exe"
& $py -m pip install --disable-pip-version-check -q -r (Join-Path $root "requirements.txt") "pyinstaller>=6,<7"
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

# Windows file properties of SecondX.exe (Explorer > Properties > Details)
$v = ($version.Split(".") + @("0", "0", "0", "0"))[0..3] -join ", "
@"
VSVersionInfo(
  ffi=FixedFileInfo(filevers=($v), prodvers=($v), mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'Ilhan Kocaslan (Proaiml)'),
      StringStruct('FileDescription', 'SecondX telemetry agent'),
      StringStruct('FileVersion', '$version'),
      StringStruct('InternalName', 'SecondX.exe'),
      StringStruct('LegalCopyright', 'MIT License - https://github.com/Proaiml/persecc'),
      StringStruct('OriginalFilename', 'SecondX.exe'),
      StringStruct('ProductName', 'SecondX'),
      StringStruct('ProductVersion', '$version')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"@ | Set-Content -Path (Join-Path $build "agent_version.txt") -Encoding UTF8

# 1) agent (onedir)
& $py -m PyInstaller --noconfirm --clean --onedir --console --name SecondX `
    --distpath (Join-Path $build "agent") --workpath (Join-Path $build "work-agent") --specpath $build `
    --version-file (Join-Path $build "agent_version.txt") `
    --hidden-import win_snapshot --hidden-import winservice --exclude-module tkinter `
    --paths $root (Join-Path $root "SecondX.py")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller (agent) failed" }
$agentExe = Join-Path $build "agent\SecondX\SecondX.exe"
& $agentExe --version
if ($LASTEXITCODE -ne 0) { throw "built SecondX.exe does not start" }
Sign-File $agentExe

# 2) setup (Inno Setup); Inno signs the setup and its uninstaller through sign.ps1 as well
$isccArgs = @("/Q", "/DAppVersion=$version", "/DAgentDir=$(Join-Path $build 'agent\SecondX')")
if ($CertThumbprint) {
    $cmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `$q$signScript`$q -Path `$f " + ($signArgs -join " ")
    $isccArgs += @("/DSignCommand", "/Ssecondx=$cmd")
}
& $Iscc @isccArgs (Join-Path $PSScriptRoot "secondx.iss")
if ($LASTEXITCODE -ne 0) { throw "Inno Setup compiler failed" }

# 3) checksums (of the final, signed file)
$exe = Join-Path $dist "SecondX-Setup.exe"
$hash = (Get-FileHash $exe -Algorithm SHA256).Hash.ToLower()
"$hash  SecondX-Setup.exe" | Set-Content -Path (Join-Path $dist "SHA256SUMS.txt") -Encoding ASCII
Write-Host ""
Write-Host "Done: $exe ($([math]::Round((Get-Item $exe).Length / 1MB, 1)) MB)"
Write-Host "SHA256: $hash"
Write-Host "Signature: $((Get-AuthenticodeSignature $exe).Status)"
