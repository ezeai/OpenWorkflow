# Features and limitations

Status words used here: **Works** (tested, and shown live in the evidence folders), **Partly** (built, with known gaps), **Not connected** (not built or not verified).

## Works

| Feature | Status | Evidence |
| --- | --- | --- |
| Import ComfyUI workflows (editor and API formats) | Works | Forge tests; `docs/evidence/contest/` |
| Readiness and missing-file report per app | Works | Forge tests; app cards |
| Create an app from a workflow's controls | Works | Forge tests |
| Run an app through ComfyUI, output saved | Works | Three live runs in `docs/evidence/contest/` |
| Run receipt (settings, seed, checksums) | Works | `docs/evidence/forge-m3/run.json` |
| Same seed gives the same output file | Works | Byte-identical hashes on repeated runs |
| Two-step sequential pipeline | Works | 3 of 3 live runs in `docs/evidence/pipeline-live-2026-10-28/`; a forced failure ends as failed |
| Run history with thumbnails and reuse of values | Works | Frontend tests |
| `.forgeapp` export and import, with file checks | Works | `docs/evidence/forge-m5/`; import tests |
| Model Library: finds files, shows licence and state | Works | Model library tests |
| Projects, characters, worlds (saved locally) | Works | Project tests |
| First-run wizard with skip and resume | Works | Wizard state tests |
| Environment check script | Works | `backend/tests/test_check_environment.py` |

## Partly

| Feature | Gap |
| --- | --- |
| Director (shots, takes) | Takes are images. Video is not connected. Camera moves are written into prompts only. |
| Canvas (node graph) | Typed node graph; runs use the same generation as the Director. Not part of the competition path. |
| AI proposals (Forge operator) | Local runtimes only, reviewed by you before use. Output quality depends on the model you choose. |
| Model discovery | Reads file names and headers in known folders. Does not verify file contents against publisher checksums unless the publisher provides them. |

## Not connected or not verified

| Feature | Status |
| --- | --- |
| Video generation (any model) | **Not connected.** No video workflow has been verified end to end. |
| Magekyo video swap | **Not verified.** No recorded run. |
| Identity locking across shots | Stored on characters; not applied to generation. The AI Influencer identity match has not been measured to a threshold. |
| Comfy Cloud execution | **Not verified live.** Off by default. |
| Parallel or branching pipelines | Not built. |
| Pipeline-wide cancel and resume after restart | Not built. A cancelled step ends the pipeline. |
| Automatic model downloads | Not built, on purpose. You download models yourself. |
| Automatic custom-node installs | Not built, on purpose. |
| Installer (.exe) | Not built. Run from source with the setup script. |
| ComfyUI address field in the interface | Not built. Set `comfy_url` in `data\config.json` while Open Workflow is stopped. An invalid settings file is renamed and defaults are used, with no error shown. |
| Agent Centre (one page for providers and models) | Not built. The assistant is opened from the Apps page. |

## Environment limits

- Tested on Windows 10 with an NVIDIA RTX 3080 Laptop GPU (16 GB), ComfyUI 0.43.x.
- A clean Windows install of this release has **not** been verified on a second machine.
- The interface is tested at desktop and phone widths in a browser; not on a physical phone.

## Licences

- Open Workflow code: MIT.
- Model weights: separate licences, not bundled. See [MODEL_LICENSE_REVIEW.md](MODEL_LICENSE_REVIEW.md). Some entries are not yet reviewed.
- ComfyUI and custom-node packs: their own licences.
