<#
.SYNOPSIS
  Builds dist\SecondX-Setup.exe from this source tree (no Python needed on the target servers).
.DESCRIPTION
  1. SecondX.exe (the agent) is built with PyInstaller in "onedir" mode: plain files in
     Program Files, nothing is unpacked to TEMP at every start.
  2. The agent folder is zipped and embedded in SecondX-Setup.exe (onefile, asks for admin rights).
  3. With a code-signing certificate, SecondX.exe and SecondX-Setup.exe are signed (Authenticode,
     SHA-256, RFC 3161 timestamp so the signature stays valid after the certificate expires).
     Works with a certificate in the Windows store, e.g. Certum SimplySign (cloud key, asks for
     the PIN / token): signtool.exe is used when installed, otherwise PowerShell's built-in
     Set-AuthenticodeSignature. Without a certificate the build is simply unsigned.
  4. dist\SHA256SUMS.txt lets anyone verify the downloaded file.
  Anyone who prefers not to run a downloaded binary can build their own copy with it.
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
    [switch]$AllowUntrustedCert        # only for testing the signing step with a self-signed certificate
)
$ErrorActionPreference = "Stop"
$root  = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$build = Join-Path $root "build"
$dist  = Join-Path $root "dist"
$version = ([regex]'__version__ = "([^"]+)"').Match((Get-Content (Join-Path $root "SecondX.py") -Raw)).Groups[1].Value
if (-not $version) { throw "__version__ not found in SecondX.py" }
Write-Host "Building SecondX $version"

foreach ($d in @($build, $dist)) { if (Test-Path $d) { Remove-Item $d -Recurse -Force } }
New-Item -ItemType Directory -Force $build, $dist | Out-Null

# Code signing (optional)
$cert = $null
if ($CertThumbprint) {
    $thumb = ($CertThumbprint -replace "[^0-9A-Fa-f]", "").ToUpper()
    $cert = Get-ChildItem Cert:\CurrentUser\My, Cert:\LocalMachine\My |
            Where-Object { $_.Thumbprint -eq $thumb -and $_.HasPrivateKey } | Select-Object -First 1
    if (-not $cert) { throw "Code-signing certificate $thumb with a private key not found in CurrentUser\My or LocalMachine\My" }
    if ($cert.EnhancedKeyUsageList.ObjectId -notcontains "1.3.6.1.5.5.7.3.3") { throw "Certificate $thumb is not a code-signing certificate" }
    Write-Host "Signing with: $($cert.Subject) (valid until $($cert.NotAfter.ToString('yyyy-MM-dd')))"
} else {
    Write-Host "No -CertThumbprint / SECONDX_SIGN_THUMBPRINT: building UNSIGNED files."
}
$signtool = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin\*\x64\signtool.exe" -ErrorAction SilentlyContinue |
            Sort-Object FullName | Select-Object -Last 1 -ExpandProperty FullName
function Sign-File([string]$path) {
    if (-not $cert) { return }
    if ($signtool) {
        & $signtool sign /sha1 $cert.Thumbprint /fd sha256 /tr $TimestampUrl /td sha256 /d "SecondX" /du "https://github.com/Proaiml/persecc" $path
        if ($LASTEXITCODE -ne 0) { throw "signtool failed for $path" }
    } else {
        $r = Set-AuthenticodeSignature -FilePath $path -Certificate $cert -HashAlgorithm SHA256 `
             -TimestampServer $TimestampUrl -IncludeChain NotRoot
        if (-not $r.SignerCertificate) { throw "signing failed for ${path}: $($r.StatusMessage)" }
    }
    $sig = Get-AuthenticodeSignature $path
    if ($sig.Status -ne "Valid" -and -not $AllowUntrustedCert) { throw "signature of $path is $($sig.Status): $($sig.StatusMessage)" }
    if (-not $sig.TimeStamperCertificate) { throw "signature of $path has no timestamp (server $TimestampUrl unreachable?)" }
    Write-Host "  signed: $(Split-Path $path -Leaf) [$($sig.Status)], timestamp by $($sig.TimeStamperCertificate.Subject)"
}

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
Sign-File (Join-Path $build "agent\SecondX\SecondX.exe")          # signed before it goes into the setup

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
$exe = Join-Path $dist "SecondX-Setup.exe"
Sign-File $exe

# 3) checksums (of the final, signed file)
$hash = (Get-FileHash $exe -Algorithm SHA256).Hash.ToLower()
"$hash  SecondX-Setup.exe" | Set-Content -Path (Join-Path $dist "SHA256SUMS.txt") -Encoding ASCII
Write-Host ""
Write-Host "Done: $exe ($([math]::Round((Get-Item $exe).Length / 1MB, 1)) MB)"
Write-Host "SHA256: $hash"
Write-Host "Signature: $((Get-AuthenticodeSignature $exe).Status)"
