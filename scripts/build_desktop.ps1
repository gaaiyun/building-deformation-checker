param(
    [switch]$BuildMsi,
    [string]$Version = "2.1.11",
    [string]$WixToolPath = ""
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root

# Avoid leaking unrelated local projects into PyInstaller's module graph.
$env:PYTHONPATH = $null

$pyinstallerConfigDir = if ($env:PYINSTALLER_CONFIG_DIR) {
    $env:PYINSTALLER_CONFIG_DIR
} else {
    Join-Path $root "build\pyinstaller-cache"
}
New-Item -ItemType Directory -Force -Path $pyinstallerConfigDir | Out-Null
$env:PYINSTALLER_CONFIG_DIR = $pyinstallerConfigDir

function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,
        [Parameter(Mandatory = $true)]
        [string[]]$ArgumentList
    )

    & $FilePath @ArgumentList
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $FilePath $($ArgumentList -join ' ')"
    }
}

Write-Host "==> Building desktop executable"
Invoke-Native "python" @("-m", "PyInstaller", "build_desktop.spec", "--clean", "--noconfirm")

$exe = Join-Path $root "dist\BuildingDeformationChecker.exe"
if (-not (Test-Path -LiteralPath $exe)) {
    throw "PyInstaller finished but executable was not found: $exe"
}

Write-Host "==> EXE ready: $exe"

if (-not $BuildMsi) {
    Write-Host "==> MSI skipped. Re-run with -BuildMsi after installing WiX Toolset."
    exit 0
}

$wixExe = $null
if ($WixToolPath) {
    if (-not (Test-Path -LiteralPath $WixToolPath)) {
        throw "WiX Toolset path was provided but not found: $WixToolPath"
    }
    $wixExe = (Resolve-Path -LiteralPath $WixToolPath).Path
} else {
    $wix = Get-Command wix.exe -ErrorAction SilentlyContinue
    if ($wix) {
        $wixExe = $wix.Source
    } elseif (Test-Path -LiteralPath "G:\dev-cache\dotnet-tools\wix.exe") {
        $wixExe = "G:\dev-cache\dotnet-tools\wix.exe"
    } else {
        throw "WiX Toolset 4 is required for MSI. Install it first, for example: dotnet tool install --tool-path G:\dev-cache\dotnet-tools wix --version 4.0.6"
    }
}

$msi = Join-Path $root "dist\BuildingDeformationChecker-$Version.msi"
$wxs = Join-Path $root "packaging\BuildingDeformationChecker.wxs"

Write-Host "==> Building MSI installer"
Invoke-Native $wixExe @(
    "build",
    $wxs,
    "-d",
    "SourceDir=$(Join-Path $root 'dist')",
    "-d",
    "AssetsDir=$(Join-Path $root 'assets')",
    "-d",
    "ProductVersion=$Version",
    "-out",
    $msi
)

if (-not (Test-Path -LiteralPath $msi)) {
    throw "WiX finished but MSI was not found: $msi"
}

Write-Host "==> MSI ready: $msi"

$stableMsi = Join-Path $root "dist\BuildingDeformationChecker.msi"
Copy-Item -LiteralPath $msi -Destination $stableMsi -Force
Write-Host "==> MSI stable copy ready: $stableMsi"
