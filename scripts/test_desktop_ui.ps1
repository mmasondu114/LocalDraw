$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
& (Join-Path $projectRoot '.venv/Scripts/python.exe') -X utf8 (Join-Path $PSScriptRoot 'test_native_desktop.py')
if ($LASTEXITCODE -ne 0) { throw 'Native desktop acceptance failed.' }
