$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'runtime.ps1')
$pythonExe = Get-ProjectPython -ProjectRoot $projectRoot
$env:PYTHONUTF8 = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
& $pythonExe -B (Join-Path $PSScriptRoot 'demo.py') --render
exit $LASTEXITCODE
