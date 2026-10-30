# Workflows

Declared in `backend/open_workflow/data/workflows.json`. A workflow declares what it needs. It does not prove that it runs.
Status comes from a live evaluation (`GET /api/workflows`). Nothing in this file marks a workflow as tested.

## Status meanings

| Status | Meaning |
|---|---|
| `license_blocked` | A required model is restricted for the default path. |
| `unknown_engine` | No ComfyUI folder is set, so node availability cannot be checked. |
| `missing_models` | A required component file is not on disk. |
| `unconfirmed_files` | The files are found, but you have not confirmed their identity. |
| `missing_nodes` | A required ComfyUI node class is not in the installed source. |
| `missing_packs` | A required custom-node pack folder is missing. |
| `requirements_met_untested` | Everything above passes. Nothing has been validated or run. |
| `validated` | A recorded validation exists. None are produced yet. |

Node checks are static: the installed `ComfyUI/nodes.py` and `comfy_extras/*.py` are searched for the class name. This confirms that a node name exists. It does not confirm that the node runs.

## Declared workflows (state on 2026-10-17)

Live evaluation against the ComfyUI portable install at `D:\ComfyUI_windows_portable_nvidia_cu128_py310\ComfyUI_windows_portable`:

| Workflow | Task | Status | Blocking detail | Unsupported controls |
|---|---|---|---|---|
| `img-z-image-turbo` | text to image | `unconfirmed_files` | 3 files found for the publisher variant, identity not confirmed | camera_move, identity_reference, lens, aperture |
| `img-flux-2-klein-4b` | text to image | `unconfirmed_files` | files found, identity not confirmed; encoder pairing inferred | camera_move, identity_reference, lens, aperture |
| `img-qwen-image-2.1` | text to image (GGUF) | `license_blocked` | `qwen-research` restriction (not eligible) | camera_move, identity_reference |
| `i2v-wan-2.1-14b-480p` | image to video (GGUF) | `unconfirmed_files` | files found, identity not confirmed; ComfyUI-GGUF pack present | camera_trajectory, end_frame, motion_control |

None of these is `requirements_met_untested` yet because files are unconfirmed, and none is `validated` or smoke-tested.

## What "confirmed" means here

Confirming a file records its path, size and modification time. The confirmation is cleared automatically if the file changes. A file name match alone is never treated as identity.

## Adding a workflow

1. Add an entry to `workflows.json` with a unique `id`, `version`, the required model roles and required ComfyUI nodes.
2. List every control the workflow cannot honour under `unsupported_controls`. Those controls must be reported, never silently ignored.
3. Leave `workflow_file` as `null` until a graph is exported and run. Do not claim `validated` until a real run is recorded.
