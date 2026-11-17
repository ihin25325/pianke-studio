$ErrorActionPreference='Stop'
$projectRoot=Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'runtime.ps1')
$pythonExe=Get-ProjectPython -ProjectRoot $projectRoot
$env:TEMP=Join-Path $projectRoot 'cache\temp'
$env:TMP=$env:TEMP
$env:PIP_CACHE_DIR=Join-Path $projectRoot 'cache\pip'
$env:PYTHONUTF8='1'
$env:PYTHONDONTWRITEBYTECODE='1'
New-Item -ItemType Directory -Force -Path $env:TEMP | Out-Null
& $pythonExe -B (Join-Path $PSScriptRoot 'setup_translation.py')
if ($LASTEXITCODE -ne 0) { throw 'Translation model download failed. Check the network and try again.' }
Write-Host 'Offline Chinese subtitles are ready. Restart the local editor.'
