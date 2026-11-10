param(
    [switch]$BuildApp,
    [string]$Iscc
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
$installerArgs = @('scripts/build_installer.py')
if ($BuildApp) { $installerArgs += '--build-app' }
if ($Iscc) { $installerArgs += @('--iscc', $Iscc) }
& '.\.venv\Scripts\python.exe' @installerArgs
if ($LASTEXITCODE -ne 0) { throw 'Installer build failed.' }
