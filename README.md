# Open Workflow

**Turn ComfyUI workflows into creative apps. Connect those apps into production pipelines.**

Open Workflow runs on your own computer. ComfyUI does the generating. Open Workflow turns each ComfyUI workflow into a simple app with a form, checks what the app needs, saves a receipt for every output, and lets you chain apps into a pipeline where each step's image feeds the next.

**Status: pre-release.** Image generation runs end to end through ComfyUI, and two-step image pipelines have been run live. Video generation is not connected yet. See [Known limitations](#known-limitations).

---

## Start here (about 15 minutes)

You need: **Windows 10 or 11**, **ComfyUI** (installed and running separately), and **the model files** for the workflow you want to run.

### 1. Install Python
1. Go to [python.org/downloads](https://www.python.org/downloads/) and install **Python 3.12** (3.11 or newer also works).
2. In the installer, tick **Add python.exe to PATH**, then click **Install Now**.

### 2. Download Open Workflow
Click the green **Code** button on this page, then **Download ZIP**. Unzip it somewhere easy to find, such as `Documents\OpenWorkflow`.

### 3. Run setup (once)
1. Open the unzipped folder.
2. Click the address bar at the top of the folder window, type `powershell`, and press Enter. A blue window opens in that folder.
3. Paste this and press Enter:
   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1
   ```
4. Wait for "Setup finished". Setup creates a `.venv` folder inside the project and installs the Python packages. It does not install ComfyUI, custom nodes or model files, and it does not change your system Python.

### 4. Start Open Workflow
Double-click **`start_openworkflow.bat`**. Your browser opens at `http://127.0.0.1:8765`. Keep the black window open while you use the app; close it to stop.

### 5. Start ComfyUI and connect it
1. Start ComfyUI as you normally do (see [ComfyUI setup](docs/COMFYUI_SETUP.md)). It listens on `http://127.0.0.1:8188`.
2. In Open Workflow, the **Home** page shows **ComfyUI connected** when it's reachable. If it says "not running", start ComfyUI first; the page updates on its own.

### 6. Make your first app
1. Click **Discover → Import workflow** and choose a ComfyUI workflow file (`.json`), or pick a template from **Browse ComfyUI templates**.
2. Open the app. It shows what it needs: model files, custom nodes, and whether the GPU memory looks sufficient.
3. Set the prompt and settings, then click **Generate**. The output and its **receipt** appear under the result.

### 7. Try a two-step pipeline
Click **Pipelines**, choose the **Multi-step flow** tab, add two apps (step 1 makes an image, step 2 takes that image as its input), and press **Run flow**. Each step has its own receipt.

To check your installation at any time, run `.venv\Scripts\python.exe scripts\check_environment.py`. It reports Python, packages, the interface files and ComfyUI, and says what to fix.

If something goes wrong, see [Troubleshooting](docs/TROUBLESHOOTING.md).

---

## What works

- **Workflow to app:** import a ComfyUI workflow (editor or API format) or a built-in template, and turn its controls into a form with labels, defaults and image inputs.
- **Readiness check:** each app is marked *ready*, *needs attention*, *blocked*, or *not checked*. Missing model files and custom nodes are named, never guessed.
- **Local runs through ComfyUI:** one job at a time on your GPU, with cancel and clear failure messages.
- **Receipts:** every output is saved with a receipt: run ID, inputs, seed, model files, ComfyUI and Open Workflow versions, and the output's SHA-256.
- **Sequential pipelines:** chain two or more apps. Each step's output image becomes the next step's input.
- **Packages:** export an app as a `.forgeapp` file and import it elsewhere. Imports check every file before saving anything.
- **Run history:** past runs with thumbnails, receipts and "run again with these values".
- **Model Library:** shows model files you already have and their licence status. It never moves, copies or deletes model files, and it downloads nothing without your confirmation.

## Built with ComfyUI

Open Workflow is an application layer for [ComfyUI](https://github.com/comfyanonymous/ComfyUI). Every generation runs through a ComfyUI server that you run yourself. Open Workflow reads ComfyUI's node definitions, submits workflows to it, and reads back the results. It does not ship its own generation engine.

## Demo examples

- **`docs/evidence/forge-m5/image_z_image_turbo.forgeapp`:** a ready-to-import app for the Z-Image Turbo text-to-image workflow. Import it from **Discover → Import workflow**. Needs the Z-Image Turbo model files.
- **`docs/evidence/forge-m3/` and `docs/evidence/forge-m1/`:** recorded outputs and run records from the Z-Image Turbo app.
- **`docs/evidence/pipeline-live-2026-10-28/`:** a recorded two-step pipeline (Z-Image Turbo at 512×512, then a 2× upscale to 1024×1024), with per-stage records. The upscale step uses `4xNomos8kDAT`, which is CC-BY-4.0 (see [Licences](#licence)).

All example images were generated by the models above. They show no real people and use no third-party artwork.

## Pipelines

A pipeline runs its apps one after another. The output image of one step becomes the input image of the next. Each step keeps its own receipt, and a failed step ends the pipeline and names the step that failed.

Pipelines are **sequential only**. Branching, parallel steps, cancelling a whole pipeline, and resuming after a restart are not supported yet.

## Requirements

- Windows 10 or 11.
- Python 3.11 or newer (3.12 recommended).
- ComfyUI, installed and running separately. A supported NVIDIA GPU with current drivers gives reasonable speed. CPU-only ComfyUI works but is slow.
- The model files for the workflows you run. They are not included in this repository.
- Node.js is **not** needed to run Open Workflow. The built interface ships in `frontend/dist`.

## Connecting ComfyUI

Open Workflow talks to ComfyUI at `http://127.0.0.1:8188` by default. To use another address, set `comfy_url` in `data\config.json` while Open Workflow is stopped. Only loopback addresses (`127.0.0.1`, `localhost`, `::1`) are accepted, so workflows are never sent to another computer. Details: [ComfyUI setup](docs/COMFYUI_SETUP.md).

## Model files and custom nodes

- **Model discovery** reads the model folders you point it at, plus ComfyUI's own models folder, in place. Files it cannot identify are shown as unknown rather than guessed. See [Model requirements](docs/MODEL_REQUIREMENTS.md).
- **Custom nodes:** Open Workflow reports which custom nodes a workflow needs and whether ComfyUI has them. It does not install them. Install them through ComfyUI Manager or by hand, then restart ComfyUI.

## Manual Mode and AI

Everything above works without any AI. Open Workflow runs no AI model in the background and needs no account, provider or API key.

Cloud execution through Comfy Cloud exists in the code. It is **off by default**, needs your explicit consent for each paid run, and **has not been verified**. Do not rely on it.

## Troubleshooting

See [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md). Logs are in `data\logs\openworkflow.log`.

## Known limitations

Full list: [docs/FEATURES_AND_LIMITATIONS.md](docs/FEATURES_AND_LIMITATIONS.md). In short:

- **Video generation is not connected.** No video workflow has been verified end to end.
- **Images only.** Identity locking and camera control are recorded but not applied to generation.
- **Sequential pipelines only**, local only.
- **Comfy Cloud is unverified.**
- **Tested on one setup:** Windows 10, RTX 3080 Laptop (16 GB). Other setups are not verified.

## Licence

- **Open Workflow code:** MIT. See [LICENSE](LICENSE).
- **Model weights** are separate. Each model has its own licence and some carry use restrictions. Open Workflow ships no weights. Read each model's licence before you use it. See [model licence review](docs/MODEL_LICENSE_REVIEW.md).
- **Third-party notices:**
  - `4xNomos8kDAT` upscaler: CC-BY-4.0. Credit: Helaman. Used in the recorded pipeline example.
  - Z-Image Turbo: Apache-2.0 on its publisher's page. Its auxiliary files are not yet verified (see the model licence review).
  - ComfyUI and custom-node packs have their own licences.
- **Open weights:** Open Workflow does not claim that every model it can run is open-weight, because the licence review is not complete.

## Documentation

- [Installation](docs/INSTALLATION.md) · [ComfyUI setup](docs/COMFYUI_SETUP.md) · [Model requirements](docs/MODEL_REQUIREMENTS.md)
- [Forge: workflows to apps](docs/FORGE.md) · [Generation](docs/GENERATION.md) · [Pipelines and projects](docs/PROJECT_FORMAT.md)
- [Features and limitations](docs/FEATURES_AND_LIMITATIONS.md) · [Troubleshooting](docs/TROUBLESHOOTING.md) · [Security and data handling](docs/SECURITY.md)

## Demo video

A short demo (about two minutes) walks through importing a workflow, running it, reading the receipt, and running a two-step pipeline. It also explains every tool and model used alongside ComfyUI. Watch on YouTube: **https://youtu.be/OwRM_mBWLc4** (90 seconds). The film marks its time-lapse sections on screen. A copy is also in the repository: [media/OpenWorkflow_Launch_Film.mp4](media/OpenWorkflow_Launch_Film.mp4).
