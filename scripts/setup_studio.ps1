param([string]$PythonExe)
$ErrorActionPreference = 'Stop'
$studioRoot = Split-Path -Parent $PSScriptRoot
if (-not $PythonExe) {
    . (Join-Path $PSScriptRoot 'runtime.ps1')
    $PythonExe = Get-ProjectPython -ProjectRoot $studioRoot
}
$env:TEMP = Join-Path $studioRoot 'cache\temp'
$env:TMP = $env:TEMP
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONUTF8 = '1'
$env:PIP_CACHE_DIR = Join-Path $studioRoot 'cache\pip'
New-Item -ItemType Directory -Force -Path $env:TEMP, $env:PIP_CACHE_DIR | Out-Null
$target = Join-Path $studioRoot 'tools\vision_packages'
& $PythonExe -B -c "import sys,importlib.util;sys.path.insert(0,sys.argv[1]);sys.exit(0 if all(importlib.util.find_spec(m) for m in ['rapidocr_onnxruntime','onnxruntime','cv2','numpy','PIL','tokenizers']) else 1)" $target
if ($LASTEXITCODE -ne 0) {
    & $PythonExe -B -m pip install --disable-pip-version-check --no-cache-dir --no-deps --target $target -r (Join-Path $PSScriptRoot 'requirements-studio.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Local vision runtime installation failed. Use Windows Python 3.12 x64.' }
}
& $PythonExe -B -c "import sys;sys.path.insert(0,sys.argv[1]);import rapidocr_onnxruntime,onnxruntime,cv2,numpy,PIL,tokenizers" $target
if ($LASTEXITCODE -ne 0) { throw 'Vision runtime import failed; preserve this folder and inspect the error before reinstalling.' }
& $PythonExe -B (Join-Path $PSScriptRoot 'setup_director_model.py')
if ($LASTEXITCODE -ne 0) { throw 'Model download/check failed. Existing media is unchanged; retry setup.' }
Write-Host "Local CPU vision is ready. Data: $studioRoot"
Write-Host 'No API key required. Close and reopen the workbench if it is already running.'
