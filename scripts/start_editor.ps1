param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$localUrl = 'http://127.0.0.1:8765'
$logFolder = Join-Path $projectRoot 'work\editor_server'
$tempFolder = Join-Path $projectRoot 'cache\temp'
New-Item -ItemType Directory -Force -Path $logFolder, $tempFolder | Out-Null
$env:TEMP = $tempFolder
$env:TMP = $tempFolder
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONDONTWRITEBYTECODE = '1'
function Test-Editor {
    try {
        $response = Invoke-RestMethod -Uri "$localUrl/api/health" -TimeoutSec 2
        return $response.ok -and $response.app -eq 'product-video-editor'
    } catch { return $false }
}
if (-not (Test-Editor)) {
    . (Join-Path $PSScriptRoot 'runtime.ps1')
    $pythonExe = Get-ProjectPython -ProjectRoot $projectRoot
    & $pythonExe -B -c 'import PIL' 2>$null
    if ($LASTEXITCODE -ne 0) { throw 'Core dependencies missing. Run SETUP.bat first.' }
    $serverScript = Join-Path $PSScriptRoot 'editor_server.py'
    $serverArguments = '-B "' + $serverScript + '"'
    $launchStamp = Get-Date -Format 'yyyyMMdd_HHmmss_fff'
    $process = Start-Process -FilePath $pythonExe -ArgumentList $serverArguments -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logFolder "$launchStamp.out.log") -RedirectStandardError (Join-Path $logFolder "$launchStamp.err.log") -PassThru
    $ready = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        if (Test-Editor) { $ready = $true; break }
        if ($process.HasExited) { break }
        Start-Sleep -Milliseconds 400
    }
    if (-not $ready) { throw "Editor did not start. See $logFolder" }
}
Write-Host "Ready: $localUrl"
Write-Host "Data: $projectRoot"
if (-not $NoBrowser) { Start-Process $localUrl }
