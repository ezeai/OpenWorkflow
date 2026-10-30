# Open Workflow

**Bring your ComfyUI workflows. Turn them into creative apps. Connect them into production pipelines. Use AI when you want it, not because you have to.**

Open Workflow is a local-first creative application layer for [ComfyUI](https://github.com/comfyanonymous/ComfyUI). ComfyUI runs the generation. Open Workflow turns each workflow into a simple app with a form, checks what it needs, keeps a receipt for every run, and lets you chain compatible apps into a sequential pipeline. Everything runs on your own computer.

**Status: pre-release.** Image generation runs end to end through ComfyUI. Video generation is not connected yet. See [Known limitations](#known-limitations).

## How it fits together

```
ComfyUI workflow  ->  Open Workflow app  ->  creative output (+ receipt)  ->  pipeline
(runs in ComfyUI)     (form, checks)        (saved on this computer)        (sequential)
```

- **ComfyUI** is installed and started separately by you. It is the only generation engine.
- **Open Workflow** is a separate local web app on `http://127.0.0.1:8765`. It reads your workflows, shows what each one needs, runs them through ComfyUI, and saves the results.
- **AI is optional.** Manual Mode needs no model, provider, account or API key.

## What works

- **Import ComfyUI workflows** (both the editor format and the API format), or pick one of ComfyUI's templates.
- **Readiness check** for every app: ready, needs attention, blocked, or not checked. Missing model files and custom nodes are named.
- **Create an app** from a workflow's controls: labels, defaults, image inputs.
- **Run an app** through your ComfyUI. Outputs are saved with a receipt (settings, seed, model files and checksums).
- **Run history**, with thumbnails, reuse of past values, and downloads.
- **Pipelines:** chain two or more apps in order. Each step's image is handed to the next step as its input. Sequential only.
- **Export and import `.forgeapp` packages**, with checks on every file.
- **Model Library:** finds model files you place in ComfyUI's model folders and shows licence and install state. It never moves, copies or deletes model files.

## Requirements

- Windows 10 or 11.
- Python 3.11 or newer (3.12 recommended). Get it from [python.org](https://www.python.org/downloads/). Tick "Add python.exe to PATH".
- A working ComfyUI installation, started separately. Its own requirements apply (a supported GPU and drivers for good speed; CPU-only ComfyUI works but is slow).
- Model files for the workflows you want to run. They are **not included** in this repository.
- Node.js is **not** needed to run Open Workflow. The built interface ships in `frontend/dist`. Node is only needed if you rebuild the interface yourself.

## Quick start

From the folder you downloaded, in PowerShell:

```bash
powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1
```

Then start Open Workflow:

```bash
start_openworkflow.bat
```

Your browser opens `http://127.0.0.1:8765`. Start ComfyUI separately, then connect it (see [docs/COMFYUI_SETUP.md](docs/COMFYUI_SETUP.md)).

## Installation

Full steps, updating and removing: [docs/INSTALLATION.md](docs/INSTALLATION.md).

Setup creates a `.venv` folder inside this repository and installs the documented Python packages into it. It does not install ComfyUI, custom nodes or model files, and it does not change your global Python.

To check the environment at any time:

```bash
.venv\Scripts\python.exe scripts\check_environment.py
```

## Connecting ComfyUI

Open Workflow talks to ComfyUI at `http://127.0.0.1:8188` by default. To use another address, set `comfy_url` in `data\config.json` while Open Workflow is stopped (the interface has no address field yet). Only loopback addresses (`127.0.0.1`, `localhost`, `::1`) are accepted, so Open Workflow never sends your workflows to another computer. Details: [docs/COMFYUI_SETUP.md](docs/COMFYUI_SETUP.md).

## Model discovery

Open Workflow reads the model folders you point it at, plus ComfyUI's own `models` folder, in place. It reports file names and headers. It does not download models, and a file it cannot identify is shown as unknown rather than guessed. Details: [docs/MODEL_REQUIREMENTS.md](docs/MODEL_REQUIREMENTS.md).

## Custom nodes

Open Workflow reports which custom nodes a workflow needs and whether ComfyUI has them. It does **not** install custom nodes. Install them yourself through ComfyUI Manager or by hand, then restart ComfyUI.

## Manual Mode

Everything above works without any AI. Import a workflow, review its readiness, create the app, set its controls, run it, read the receipt, and build pipelines. No AI runs in the background.

## Agent-Assisted Mode (optional)


Cloud execution through Comfy Cloud exists in the code, is off by default, requires explicit consent for each paid run, and has **not been verified live**. Do not rely on it.

## Example workflows and packages

- `docs/evidence/forge-m5/image_z_image_turbo.forgeapp`: a package for the Z-Image Turbo text-to-image workflow. It needs the Z-Image Turbo model files and the core ComfyUI nodes. See [docs/MODEL_REQUIREMENTS.md](docs/MODEL_REQUIREMENTS.md).
- A live run of that workflow is recorded in `docs/evidence/contest/` (three runs, with output and receipt).
- A live two-step pipeline (Z-Image Turbo, then a 4x upscale) is recorded in `docs/evidence/pipeline-live-2026-10-28/`. The upscale step needs the 4x-UltraSharp model, whose licence has not been reviewed yet.

## Pipelines

A pipeline is a list of apps that run one after another. The output image of one step becomes the input image of the next. Each step keeps its own receipt. Pipelines are sequential: there is no branching or parallel execution, no whole-pipeline cancel, and no resume after a restart. A failed step ends the pipeline and says which step failed.

## `.forgeapp` packages

Export an app to a `.forgeapp` file to share it. Import checks the archive's file sizes, its checksums and its structure before anything is saved. Packages contain the workflow and its settings, not model weights.

## Troubleshooting

See [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md).

## Known limitations

See [docs/FEATURES_AND_LIMITATIONS.md](docs/FEATURES_AND_LIMITATIONS.md). The short version:

- Video generation is not connected. No video workflow has been verified end to end.
- Images only. Image identity locking and camera control are not applied to generation.
- Sequential pipelines only, local only.
- Comfy Cloud is unverified.
- Tested on Windows with one GPU (RTX 3080 Laptop, 16 GB). Other setups are not verified.

## Licence

- **Open Workflow code:** MIT. See [LICENSE](LICENSE).
- **Model weights are separate.** Each model has its own licence, and some carry use restrictions. Open Workflow ships no weights. Read each model's licence before you use it. See [docs/MODEL_LICENSE_REVIEW.md](docs/MODEL_LICENSE_REVIEW.md).
- **ComfyUI and custom-node packs** have their own licences.

## Documentation

- [Installation](docs/INSTALLATION.md)
- [ComfyUI setup](docs/COMFYUI_SETUP.md)
- [Model requirements](docs/MODEL_REQUIREMENTS.md)
- [Features and limitations](docs/FEATURES_AND_LIMITATIONS.md)
- [Troubleshooting](docs/TROUBLESHOOTING.md)
- [Forge: workflows to apps](docs/FORGE.md)
- [Security and data handling](docs/SECURITY.md)
- [Project format](docs/PROJECT_FORMAT.md)
