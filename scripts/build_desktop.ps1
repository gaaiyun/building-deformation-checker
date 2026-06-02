param(
    [switch]$BuildMsi,
    [string]$Version = "2.1.0"
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root

Write-Host "==> Building desktop executable"
python -m PyInstaller build_desktop.spec --clean --noconfirm

$exe = Join-Path $root "dist\BuildingDeformationChecker.exe"
if (-not (Test-Path -LiteralPath $exe)) {
    throw "PyInstaller finished but executable was not found: $exe"
}

Write-Host "==> EXE ready: $exe"

if (-not $BuildMsi) {
    Write-Host "==> MSI skipped. Re-run with -BuildMsi after installing WiX Toolset."
    exit 0
}

$wix = Get-Command wix.exe -ErrorAction SilentlyContinue
if (-not $wix) {
    throw "WiX Toolset is required for MSI. Install it first, for example: dotnet tool install --global wix"
}

$msi = Join-Path $root "dist\BuildingDeformationChecker-$Version.msi"
$wxs = Join-Path $root "packaging\BuildingDeformationChecker.wxs"

Write-Host "==> Building MSI installer"
& $wix.Source build $wxs -d "SourceDir=$(Join-Path $root 'dist')" -d "ProductVersion=$Version" -out $msi

if (-not (Test-Path -LiteralPath $msi)) {
    throw "WiX finished but MSI was not found: $msi"
}

Write-Host "==> MSI ready: $msi"
