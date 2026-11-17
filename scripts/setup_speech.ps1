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
& $pythonExe -B -m pip install --target (Join-Path $projectRoot 'tools\asr_packages') --upgrade --only-binary=:all: --no-warn-script-location 'faster-whisper==1.2.1' 'ctranslate2==4.8.5' 'av==16.1.0'
if ($LASTEXITCODE -ne 0) { throw 'Speech dependencies could not be installed.' }
& $pythonExe -B (Join-Path $PSScriptRoot 'setup_speech.py')
if ($LASTEXITCODE -ne 0) { throw 'Speech model download failed. Check the network and try again.' }
Write-Host 'Offline speech subtitles are ready. Restart the local editor.'
