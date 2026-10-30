# Open Workflow setup for Windows. Safe to run again.
#
# What it does:  creates .venv inside this repository and installs backend/requirements.txt into it,
#                then runs scripts/check_environment.py.
# What it does not do: install ComfyUI, install custom nodes, download model weights, change your
#                global Python, touch your ComfyUI folder, or require Node.js (the interface is prebuilt).
#
# Usage (from the repository root):  powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Fail($message) {
    Write-Host ""
    Write-Host "Setup stopped: $message" -ForegroundColor Red
    exit 1
}

# 1. Find Python 3.11 or newer. The py launcher is preferred; a plain python on PATH is accepted if new enough.
$pythonExe = $null
$pythonArgs = @()
$candidates = @(
    @{ exe = "py"; args = @("-3.12") },
    @{ exe = "py"; args = @("-3.11") },
    @{ exe = "python"; args = @() }
)
foreach ($c in $candidates) {
    if (-not (Get-Command $c.exe -ErrorAction SilentlyContinue)) { continue }
    $version = & $c.exe @($c.args) -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
    if ($LASTEXITCODE -eq 0 -and $version -match '^(\d+)\.(\d+)$' -and ([int]$matches[1] -gt 3 -or ([int]$matches[1] -eq 3 -and [int]$matches[2] -ge 11))) {
        $pythonExe = $c.exe
        $pythonArgs = $c.args
        break
    }
}
if (-not $pythonExe) {
    Fail "Python 3.11 or newer was not found. Install Python 3.12 from https://www.python.org/downloads/ (tick 'Add python.exe to PATH'), then open a new window and run this again."
}
Write-Host "Using Python: $pythonExe $($pythonArgs -join ' ')"

# 2. Create the application's own virtual environment (only if it is missing).
$venvPython = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "Creating .venv in this folder..."
    & $pythonExe @pythonArgs -m venv (Join-Path $root ".venv")
    if ($LASTEXITCODE -ne 0) { Fail "could not create .venv. Check that the folder is writable." }
}

# 3. Install the documented backend dependencies into .venv only.
Write-Host "Installing backend dependencies (backend\requirements.txt)..."
& $venvPython -m pip install --quiet --disable-pip-version-check -r (Join-Path $root "backend\requirements.txt")
if ($LASTEXITCODE -ne 0) { Fail "dependency installation failed. Check your internet connection and the messages above." }

# 4. The prebuilt interface must be present. Rebuilding it needs Node.js and is not part of setup.
if (-not (Test-Path (Join-Path $root "frontend\dist\index.html"))) {
    Write-Host "Warning: frontend\dist is missing. Restore it from git, or see docs\INSTALLATION.md to rebuild it." -ForegroundColor Yellow
}

# 5. Report what is ready. ComfyUI is reported, not installed.
Write-Host ""
& $venvPython (Join-Path $root "scripts\check_environment.py")
$checkExit = $LASTEXITCODE

Write-Host ""
if ($checkExit -eq 0) {
    Write-Host "Setup finished. Start Open Workflow with start_openworkflow.bat, or scripts\start_windows.bat." -ForegroundColor Green
} else {
    Write-Host "Setup finished, but something still needs attention (see above). Fix it, then run this again." -ForegroundColor Yellow
}
exit $checkExit
