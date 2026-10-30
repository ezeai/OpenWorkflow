# Installation (Windows)

Open Workflow runs on your computer and talks to a ComfyUI you run separately. Setup installs only Open Workflow's own Python packages, into a `.venv` folder inside this repository. It does not install ComfyUI, custom nodes or model files, and it does not change your global Python.

## Prerequisites

1. **Python 3.11 or newer** (3.12 recommended). Download from https://www.python.org/downloads/ and tick **Add python.exe to PATH**.
2. **ComfyUI**, installed and working on its own. See [COMFYUI_SETUP.md](COMFYUI_SETUP.md).
3. **Git** (optional). Use it to clone or update; a ZIP download also works.

You do **not** need Node.js to run Open Workflow.

## Setup (once)

Open PowerShell in the repository folder and run:

```bash
powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1
```

Setup:

- finds Python 3.11+ (`py -3.12`, `py -3.11` or `python`),
- creates `.venv` if it is missing,
- installs `backend\requirements.txt` into `.venv`,
- checks the built interface is present,
- runs the environment check and reports what is ready.

It is safe to run again. Each run reuses `.venv`.

If it stops with "Python 3.11 or newer was not found", install Python, open a new PowerShell window, and run it again.

## Start

```bash
start_openworkflow.bat
```

This runs `scripts\start_windows.bat`. The window stays open while Open Workflow runs. Close it to stop.

- The app opens at **http://127.0.0.1:8765**.
- If Open Workflow is already running, the starter opens it and does not start a second copy.
- If port 8765 is used by another program, the starter uses the next free port up to 8800 and prints the address.
- The server listens on `127.0.0.1` only. Other computers on your network cannot reach it.

Logs are kept in `data\logs\openworkflow.log`. They are rotated and secrets are removed before writing.

## Check the environment

```bash
.venv\Scripts\python.exe scripts\check_environment.py
```

It reports Python, the Python packages, the interface files, port 8765, and ComfyUI. It changes nothing. ComfyUI being off is reported as "Not connected", which does not stop Open Workflow from opening.

## Updating

```bash
git pull
powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1
```

Your projects, settings and outputs are in `data\` and are not touched by an update. Back up `data\` before a big change.

## Removing

Close Open Workflow, then delete the repository folder. Your data is inside `data\`; delete it too if you want a clean removal.

## Troubleshooting

See [TROUBLESHOOTING.md](TROUBLESHOOTING.md).

## Rebuilding the interface (developers only)

The built interface is committed in `frontend\dist`. To change the interface, install Node.js 20 or newer and run:

```bash
cd frontend
npm install
npm run build
```

Tests: `npm test` in `frontend`, and `python -m pytest` per file in `backend\tests` (see the README).
