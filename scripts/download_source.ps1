param(
    [Parameter(Mandatory=$true)][string]$Url,
    [Parameter(Mandatory=$true)][ValidatePattern('^[a-zA-Z0-9_-]+$')][string]$ProductKey,
    [switch]$SearchOnly
)
$ErrorActionPreference = 'Stop'
$ProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
. (Join-Path $PSScriptRoot 'runtime.ps1')
$Python = Get-ProjectPython -ProjectRoot $ProjectRoot
$Node = (Get-Command node -ErrorAction Stop).Source
$Target = Join-Path $ProjectRoot "sources\$ProductKey"
$Packages = Join-Path $ProjectRoot 'tools\python_packages'
$Cache = Join-Path $ProjectRoot 'cache'
$Temporary = Join-Path $ProjectRoot 'work\download-temp'
New-Item -ItemType Directory -Path $Target,$Cache,$Temporary -Force | Out-Null
$env:PYTHONPATH = $Packages
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:TEMP = $Temporary
$env:TMP = $Temporary
$env:PIP_CACHE_DIR = Join-Path $Cache 'pip'
if (-not (Test-Path -LiteralPath (Join-Path $Packages 'yt_dlp\__init__.py'))) {
    & $Python -m pip install --target $Packages yt-dlp --disable-pip-version-check
    if ($LASTEXITCODE -ne 0) { throw 'Could not install yt-dlp into the D-drive project.' }
}
$Common = @('--no-playlist','--socket-timeout','20','--retries','2','--js-runtimes',"node:$Node",'--remote-components','ejs:github','--cache-dir',(Join-Path $Cache 'yt-dlp'),'--no-progress')
if ($SearchOnly) {
    # Supply a product model as -Url when using -SearchOnly. This fetches candidates, not videos.
    & $Python -m yt_dlp @Common --flat-playlist --skip-download --write-info-json -o (Join-Path $Target 'candidate_%(id)s.%(ext)s') "ytsearch3:$Url"
} else {
    if ($Url -notmatch '^https://(www\.)?(youtube\.com/|youtu\.be/)') { throw 'Use a public YouTube HTTPS URL.' }
    & $Python -m yt_dlp @Common --no-overwrites --write-info-json --ffmpeg-location (Join-Path $ProjectRoot 'tools\ffmpeg\bin') -f 'bv[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080][ext=mp4]' --merge-output-format mp4 -o (Join-Path $Target 'source.%(ext)s') $Url
}
if ($LASTEXITCODE -ne 0) { throw 'Download/search failed. Check connectivity or use SnapAny to save source.mp4 into the product source folder.' }
