function Get-ProjectPython {
    param([Parameter(Mandatory=$true)][string]$ProjectRoot)
    $candidates = @($env:PIANKE_PYTHON,
        (Join-Path $ProjectRoot '.venv\Scripts\python.exe'),
        (Join-Path $ProjectRoot 'tools\python\python.exe'),
        (Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'))
    $systemPython = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($systemPython) { $candidates += $systemPython.Source }
    $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($launcher) {
        $launched = & $launcher.Source -3 -c 'import sys;print(sys.executable)' 2>$null
        if ($LASTEXITCODE -eq 0) { $candidates += [string]$launched }
    }
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            & $candidate -B -c 'import sys;sys.exit(0 if sys.version_info >= (3,11) and sys.maxsize > 2**32 else 1)' 2>$null
            if ($LASTEXITCODE -eq 0) { return $candidate }
        }
    }
    throw 'Install 64-bit Python 3.11+ (3.12 recommended), then retry. https://www.python.org/downloads/windows/'
}
