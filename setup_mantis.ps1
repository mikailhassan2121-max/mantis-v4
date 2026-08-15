$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

Write-Host "MANTIS 4.10.0 SETUP"
Write-Host "No administrator access or security-policy changes are required."

$Python = Get-Command py -ErrorAction SilentlyContinue
if ($Python) {
    & $Python.Source -3 -c "import sys; assert sys.version_info >= (3,11), 'Python 3.11 or newer is required'"
    if (-not (Test-Path -LiteralPath $VenvPython)) {
        & $Python.Source -3 -m venv (Join-Path $ProjectRoot ".venv")
    }
} else {
    $Python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $Python) { throw "Python was not found. Install Python 3.11+ from python.org, then run this script again." }
    & $Python.Source -c "import sys; assert sys.version_info >= (3,11), 'Python 3.11 or newer is required'"
    if (-not (Test-Path -LiteralPath $VenvPython)) {
        & $Python.Source -m venv (Join-Path $ProjectRoot ".venv")
    }
}

& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install -r (Join-Path $ProjectRoot "requirements.txt")
& $VenvPython (Join-Path $ProjectRoot "mantis_v4_live.py") --health-check

Write-Host ""
Write-Host "SETUP COMPLETE"
Write-Host "Run start_mantis.bat for normal observation or start_mantis.bat --demo for synthetic demo mode."
