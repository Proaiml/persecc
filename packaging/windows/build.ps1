<#
.SYNOPSIS
  Builds dist\SecondX-Setup.exe from this source tree (no Python needed on the target servers).
.DESCRIPTION
  1. SecondX.exe (the agent) is built with PyInstaller in "onedir" mode: plain files in
     Program Files, nothing is unpacked to TEMP at every start.
  2. The agent folder is zipped and embedded in SecondX-Setup.exe (onefile, asks for admin rights).
  3. dist\SHA256SUMS.txt lets anyone verify the downloaded file.
  Anyone who prefers not to run a downloaded binary can build their own copy with it.
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1
#>
param([string]$Python = "py", [string[]]$PythonArgs = @("-3.11"))
$ErrorActionPreference = "Stop"
$root  = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$build = Join-Path $root "build"
$dist  = Join-Path $root "dist"
$version = ([regex]'__version__ = "([^"]+)"').Match((Get-Content (Join-Path $root "SecondX.py") -Raw)).Groups[1].Value
if (-not $version) { throw "__version__ not found in SecondX.py" }
Write-Host "Building SecondX $version"

foreach ($d in @($build, $dist)) { if (Test-Path $d) { Remove-Item $d -Recurse -Force } }
New-Item -ItemType Directory -Force $build, $dist | Out-Null

# Clean, private build environment: only requirements.txt + PyInstaller end up in the exe
# (a system Python with pandas, numpy ... would silently bloat it) and the build is reproducible.
& $Python @PythonArgs -m venv (Join-Path $build "venv")
if ($LASTEXITCODE -ne 0) { throw "could not create the build venv" }
$py = Join-Path $build "venv\Scripts\python.exe"
& $py -m pip install --disable-pip-version-check -q -r (Join-Path $root "requirements.txt") "pyinstaller>=6,<7"
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

# Windows file properties (Explorer > Properties > Details)
$v = ($version.Split(".") + @("0", "0", "0", "0"))[0..3] -join ", "
function Write-VersionFile($path, $description, $fileName) {
@"
VSVersionInfo(
  ffi=FixedFileInfo(filevers=($v), prodvers=($v), mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'Ilhan Kocaslan (Proaiml)'),
      StringStruct('FileDescription', '$description'),
      StringStruct('FileVersion', '$version'),
      StringStruct('InternalName', '$fileName'),
      StringStruct('LegalCopyright', 'MIT License - https://github.com/Proaiml/persecc'),
      StringStruct('OriginalFilename', '$fileName'),
      StringStruct('ProductName', 'SecondX'),
      StringStruct('ProductVersion', '$version')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"@ | Set-Content -Path $path -Encoding UTF8
}
Write-VersionFile (Join-Path $build "agent_version.txt") "SecondX telemetry agent" "SecondX.exe"
Write-VersionFile (Join-Path $build "setup_version.txt") "SecondX setup" "SecondX-Setup.exe"

# 1) agent
& $py -m PyInstaller --noconfirm --clean --onedir --console --name SecondX `
    --distpath (Join-Path $build "agent") --workpath (Join-Path $build "work-agent") --specpath $build `
    --version-file (Join-Path $build "agent_version.txt") `
    --hidden-import win_snapshot --exclude-module tkinter `
    --paths $root (Join-Path $root "SecondX.py")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller (agent) failed" }
& (Join-Path $build "agent\SecondX\SecondX.exe") --version
if ($LASTEXITCODE -ne 0) { throw "built SecondX.exe does not start" }

# 2) payload + setup
Compress-Archive -Path (Join-Path $build "agent\SecondX\*") -DestinationPath (Join-Path $build "payload.zip") -Force
Set-Content -Path (Join-Path $build "VERSION") -Value $version -NoNewline -Encoding ASCII
& $py -m PyInstaller --noconfirm --clean --onefile --console --uac-admin --name SecondX-Setup `
    --distpath $dist --workpath (Join-Path $build "work-setup") --specpath $build `
    --version-file (Join-Path $build "setup_version.txt") `
    --add-data "$(Join-Path $build 'payload.zip');." --add-data "$(Join-Path $root 'config.json');." `
    --add-data "$(Join-Path $build 'VERSION');." `
    (Join-Path $PSScriptRoot "secondx_setup.py")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller (setup) failed" }

# 3) checksums
$exe = Join-Path $dist "SecondX-Setup.exe"
$hash = (Get-FileHash $exe -Algorithm SHA256).Hash.ToLower()
"$hash  SecondX-Setup.exe" | Set-Content -Path (Join-Path $dist "SHA256SUMS.txt") -Encoding ASCII
Write-Host ""
Write-Host "Done: $exe ($([math]::Round((Get-Item $exe).Length / 1MB, 1)) MB)"
Write-Host "SHA256: $hash"
