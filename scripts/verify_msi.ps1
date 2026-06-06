param(
    [string]$MsiPath = "dist\BuildingDeformationChecker.msi",
    [switch]$Install,
    [string]$InstallLog = "output\msi_install_verify.log",
    [string]$UninstallLog = "output\msi_uninstall_verify.log"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$msi = Join-Path $root $MsiPath

if (-not (Test-Path -LiteralPath $msi)) {
    throw "MSI not found: $msi"
}

$resolvedMsi = (Resolve-Path -LiteralPath $msi).Path
$hash = Get-FileHash -LiteralPath $resolvedMsi -Algorithm SHA256
$signature = Get-AuthenticodeSignature -LiteralPath $resolvedMsi

Write-Host "MSI: $resolvedMsi"
Write-Host "Size: $((Get-Item -LiteralPath $resolvedMsi).Length) bytes"
Write-Host "SHA256: $($hash.Hash)"
Write-Host "Signature: $($signature.Status)"

if (-not $Install) {
    Write-Host "Install switch not set. Metadata verification only."
    Write-Host "To perform a per-user silent install/uninstall smoke test, run:"
    Write-Host "  powershell -ExecutionPolicy Bypass -File scripts/verify_msi.ps1 -Install"
    exit 0
}

$installLogPath = Join-Path $root $InstallLog
$uninstallLogPath = Join-Path $root $UninstallLog
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $installLogPath) | Out-Null

Write-Host "Installing MSI silently..."
$installArgs = @("/i", $resolvedMsi, "/qn", "/norestart", "/L*v", $installLogPath)
$p = Start-Process -FilePath "msiexec.exe" -ArgumentList $installArgs -Wait -PassThru
if ($p.ExitCode -ne 0) {
    throw "msiexec install failed with exit code $($p.ExitCode). Log: $installLogPath"
}

$installDir = Join-Path $env:LOCALAPPDATA "Building Deformation Checker"
$exe = Join-Path $installDir "BuildingDeformationChecker.exe"
if (-not (Test-Path -LiteralPath $exe)) {
    throw "Installed EXE not found: $exe"
}
Write-Host "Installed EXE: $exe"

Write-Host "Launching installed EXE smoke..."
$app = Start-Process -FilePath $exe -WindowStyle Hidden -PassThru
Start-Sleep -Seconds 8
if ($app.HasExited) {
    throw "Installed EXE exited early with code $($app.ExitCode)"
}
Stop-Process -Id $app.Id -Force
Write-Host "Installed EXE launch smoke passed."

Write-Host "Uninstalling MSI silently..."
$uninstallArgs = @("/x", $resolvedMsi, "/qn", "/norestart", "/L*v", $uninstallLogPath)
$p = Start-Process -FilePath "msiexec.exe" -ArgumentList $uninstallArgs -Wait -PassThru
if ($p.ExitCode -ne 0) {
    throw "msiexec uninstall failed with exit code $($p.ExitCode). Log: $uninstallLogPath"
}

Write-Host "MSI install/uninstall smoke passed."
