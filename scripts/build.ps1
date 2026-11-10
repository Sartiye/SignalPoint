$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
& '.\.venv\Scripts\python.exe' scripts/build.py
if ($LASTEXITCODE -ne 0) { throw 'Application build failed.' }
