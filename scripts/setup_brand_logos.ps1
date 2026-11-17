$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'runtime.ps1')
$pythonExe = Get-ProjectPython -ProjectRoot $projectRoot
$packageFolder = Join-Path $projectRoot 'tools\logo_packages'
$tempFolder = Join-Path $projectRoot 'cache\temp'
New-Item -ItemType Directory -Force -Path $packageFolder,$tempFolder | Out-Null
$env:TEMP = $tempFolder
$env:TMP = $tempFolder
$env:PYTHONDONTWRITEBYTECODE = '1'
& $pythonExe -B -m pip install --no-cache-dir --disable-pip-version-check --only-binary=:all: --no-deps --target $packageFolder 'resvg-py==0.5.0'
if ($LASTEXITCODE -ne 0) { throw 'Logo SVG support installation failed. PNG/JPG uploads still work.' }
Write-Host 'Logo SVG support ready. No API key or model required.'
