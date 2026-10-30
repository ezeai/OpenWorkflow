# Troubleshooting

## Setup

| Problem | Fix |
| --- | --- |
| "Python 3.11 or newer was not found" | Install Python 3.12 from python.org with **Add python.exe to PATH** ticked. Open a new PowerShell window and run setup again. |
| Setup cannot be run ("running scripts is disabled") | Run it with `powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1`. This only affects that one run. |
| "could not create .venv" | The repository folder is read-only or in a synced folder that is locked. Move the folder somewhere writable. |
| "dependency installation failed" | Check your internet connection and run setup again. Setup can be re-run safely. |
| "frontend\dist is missing" | The built interface should be in the repository. Restore it from git (`git checkout -- frontend/dist`) or rebuild it with Node.js (see INSTALLATION.md). |

## Starting

| Problem | Fix |
| --- | --- |
| The window closes at once | Open a Command Prompt in the folder and run `start_openworkflow.bat`, so you can read the message. |
| "The Python packages are not installed" | Run `scripts\setup_windows.ps1` once. |
| "Open Workflow is already running" | It is. The starter opens it in your browser. Close the old window to restart. |
| "No free port between 8765 and 8800" | Close other local servers, then start again. |
| The page does not open | Go to http://127.0.0.1:8765 (or the address printed by the starter) by hand. |

## ComfyUI

| Problem | Fix |
| --- | --- |
| "Not running" | Start ComfyUI and wait for its page to load. See [COMFYUI_SETUP.md](COMFYUI_SETUP.md). |
| Connection works, but a run fails at once | Read the run's message. A missing model or node is reported by name. |
| A run seems stuck | ComfyUI may be busy (a large model loading or a previous run). Check ComfyUI's console. Open Workflow shows the run as running until ComfyUI reports a result. |

## Models and apps

| Problem | Fix |
| --- | --- |
| An app says "Needs attention" | Read the named file. Place it in the right ComfyUI folder, then rescan. See [MODEL_REQUIREMENTS.md](MODEL_REQUIREMENTS.md). |
| An app says "Blocked" | A required piece is missing or not supported. The app lists it. Blocked apps cannot run. |
| A model is listed as "found, not confirmed" | Check the file yourself. Open Workflow does not treat a similar name as an exact match. |
| An app needs a custom node | Install that node pack yourself, restart ComfyUI, and check the app again. |

## Where to look

- **Application log:** `data\logs\openworkflow.log`.
- **ComfyUI log:** ComfyUI's own console window.
- **Diagnostics zip:** Settings, "Download diagnostics". It removes secrets. Nothing is sent anywhere; you attach it to a bug report yourself.

## Reporting a problem

Include: the step where it failed, the message on screen, the last 20 lines of `data\logs\openworkflow.log`, your Windows version, your ComfyUI version, and the diagnostics zip if you can.
