<#
.SYNOPSIS
  Signs one file with a code-signing certificate from the Windows certificate store
  (Authenticode, SHA-256, RFC 3161 timestamp). Used by build.ps1 and by Inno Setup (SignTool).
.DESCRIPTION
  Works with certificates whose key is in the cloud or on a token (e.g. Certum SimplySign):
  signtool.exe is used when the Windows SDK is installed, otherwise PowerShell's built-in
  Set-AuthenticodeSignature. The timestamp keeps the signature valid after the certificate expires.
#>
param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][string]$Thumbprint,
    [string]$TimestampUrl = "http://time.certum.pl",
    [switch]$AllowUntrustedCert        # only for testing with a self-signed certificate
)
$ErrorActionPreference = "Stop"
$thumb = ($Thumbprint -replace "[^0-9A-Fa-f]", "").ToUpper()
$cert = Get-ChildItem Cert:\CurrentUser\My, Cert:\LocalMachine\My |
        Where-Object { $_.Thumbprint -eq $thumb -and $_.HasPrivateKey } | Select-Object -First 1
if (-not $cert) { throw "Code-signing certificate $thumb with a private key not found in CurrentUser\My or LocalMachine\My" }
if ($cert.EnhancedKeyUsageList.ObjectId -notcontains "1.3.6.1.5.5.7.3.3") { throw "Certificate $thumb is not a code-signing certificate" }

$signtool = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin\*\x64\signtool.exe" -ErrorAction SilentlyContinue |
            Sort-Object FullName | Select-Object -Last 1 -ExpandProperty FullName
if ($signtool) {
    & $signtool sign /sha1 $cert.Thumbprint /fd sha256 /tr $TimestampUrl /td sha256 /d "SecondX" /du "https://github.com/Proaiml/persecc" $Path
    if ($LASTEXITCODE -ne 0) { throw "signtool failed for $Path" }
} else {
    $r = Set-AuthenticodeSignature -FilePath $Path -Certificate $cert -HashAlgorithm SHA256 `
         -TimestampServer $TimestampUrl -IncludeChain NotRoot
    if (-not $r.SignerCertificate) { throw "signing failed for ${Path}: $($r.StatusMessage)" }
}
$sig = Get-AuthenticodeSignature $Path
if ($sig.Status -ne "Valid" -and -not $AllowUntrustedCert) { throw "signature of $Path is $($sig.Status): $($sig.StatusMessage)" }
if (-not $sig.TimeStamperCertificate) { throw "signature of $Path has no timestamp (server $TimestampUrl unreachable?)" }
Write-Host "  signed: $(Split-Path $Path -Leaf) [$($sig.Status)], timestamp by $($sig.TimeStamperCertificate.Subject)"
