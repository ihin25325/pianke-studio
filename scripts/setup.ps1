param([switch]$AllFonts, [switch]$SkipFFmpeg, [switch]$SkipFonts)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'runtime.ps1')
$bootstrapPython = Get-ProjectPython -ProjectRoot $projectRoot
$env:PYTHONUTF8 = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:TEMP = Join-Path $projectRoot 'cache\temp'
$env:TMP = $env:TEMP
$env:PIP_CACHE_DIR = Join-Path $projectRoot 'cache\pip'
New-Item -ItemType Directory -Force -Path $env:TEMP,$env:PIP_CACHE_DIR | Out-Null
$venv = Join-Path $projectRoot '.venv'
if (-not (Test-Path -LiteralPath (Join-Path $venv 'Scripts\python.exe'))) {
    & $bootstrapPython -B -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create local Python environment.' }
}
$pythonExe = Join-Path $venv 'Scripts\python.exe'
& $pythonExe -B -m pip install --disable-pip-version-check -r (Join-Path $projectRoot 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Core package install failed; check PyPI connectivity.' }
$arguments = @('-B',(Join-Path $PSScriptRoot 'setup_core.py'))
if ($SkipFFmpeg) { $arguments += '--skip-ffmpeg' }
if ($SkipFonts) { $arguments += '--skip-fonts' }
& $pythonExe @arguments
if ($LASTEXITCODE -ne 0) { throw 'Media tools/fonts setup failed. You can rerun safely.' }
if ($AllFonts) {
    & $pythonExe -B -m pip install --disable-pip-version-check 'fonttools>=4.60,<5'
    if ($LASTEXITCODE -ne 0) { throw 'fonttools install failed.' }
    & $pythonExe -B (Join-Path $PSScriptRoot 'download_fonts.py')
    if ($LASTEXITCODE -ne 0) { throw 'Base font pack download failed.' }
    & $pythonExe -B (Join-Path $PSScriptRoot 'download_premium_fonts.py')
    if ($LASTEXITCODE -ne 0) { throw 'Extra font pack download failed.' }
}
& $pythonExe -B (Join-Path $PSScriptRoot 'doctor.py')
if ($LASTEXITCODE -ne 0) { throw 'Some required checks failed. Follow the doctor output.' }
Write-Host 'Ready. START_EDITOR.bat opens the workbench; DEMO.bat runs an offline demo.'
