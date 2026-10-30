# Open Workflow Milestone 0 Audit

Audit date: 2026-10-17. Labels: VERIFIED (observed on this machine/page today), DOCUMENTED (stated in a doc I read),
INFERRED, UNKNOWN, PROPOSED.

## 1. Machine (VERIFIED)
| Item | Value |
|---|---|
| OS | Windows 10 Pro 10.0.19045, 64-bit |
| CPU | Intel i9-11980HK, 8C/16T |
| RAM | 63.7 GB |
| GPU | NVIDIA GeForce RTX 3080 Laptop, 16 GB VRAM, driver 616.92 (760 MiB in use at audit time) |
| Disk | C: 20.6 GB free (**tight**); D: 173.8 GB free |
| Node / npm | v24.16.0 / 11.13.0 |
| Python (system) | 3.12.3 (torch 2.11.0+cu128, numpy 2.3.0, Pillow 12.1.1 importable) |
| Git | 2.42.0 |
| FFmpeg | **not on PATH**. Binaries exist inside Krita, Free Download Manager, Topaz Video AI (third-party, not redistributable by us). |

C: free space is small, so Open Workflow projects/cache default to D: or a user-chosen path; never write large data to C:.

## 2. ComfyUI (VERIFIED)
- Portable install: `D:\ComfyUI_windows_portable_nvidia_cu128_py310\ComfyUI_windows_portable`
- ComfyUI 0.41.2 (`comfyui_version.py`), embedded Python 3.10.11, torch 2.8.0+cu128, frontend package 1.55.17.
- Models total roughly 0.7 TB on D: (see MODEL_INVENTORY.md). No `extra_model_paths.yaml`.
- Custom nodes relevant to filmmaking: ComfyUI-WanVideoWrapper, comfyui-videohelpersuite, seedvr2_videoupscaler,
  ComfyUI-AutoCropFaces, GGUF, plus ~40 others (not audited individually).
- **Open Workflow node pack already installed** at `ComfyUI/custom_nodes/comfyui-open_workflow` (15 nodes, imports clean with the embedded Python).

## 3. Running services (VERIFIED)
| Port | Owner (inferred) | Note |
|---|---|---|
| 7860 | Content Studio app (its `APP_PORT`) | ComfyUI runs *inside* its process, no server |
| 11434 | Ollama (local LLM endpoint) | usable as optional local planner, UNKNOWN which models are pulled |
| 8188 | nothing | Open Workflow may start its own ComfyUI server here on loopback |

Consequence: Content Studio may hold the GPU. Open Workflow must detect a busy GPU (free VRAM via nvidia-smi) and warn, never kill other processes.

## 4. Content Studio, read-only review
Read only: README, PACKAGING, `CHARACTER_CINEMA.md`, `IDENTITYLOCK.md`, `studio_config.py` (head), `engine.py` (docstring + grep),
`studio_cinema/library.py`, `studio_identity/registry.py`, directory listings. **Nothing was modified or executed.** Its tests were NOT run
(they may touch its databases).

Lessons (DOCUMENTED unless noted):
1. Runs ComfyUI's execution engine in-process with its own Python runtime; avoids a server and port. Open Workflow instead uses ComfyUI's HTTP API
   so it stays decoupled and can use any ComfyUI (PROPOSED).
2. Per-model adapter modules (`studio_models/*.py`) with `build(params, refs)` returning a ComfyUI API graph and `MAX_REFS`/`missing_files()`.
   Same idea as the Open Workflow adapter contract.
3. Capability registry **probed from the real adapter** (build a graph with N refs, count image slots) instead of hand-asserted flags. Adopt the principle.
4. Character DNA hash + immutable versions, 15 reference roles, lock levels OFF/AUTO/SOFT/STRONG/IMMUTABLE. Open Workflow uses Off/Balanced/Strong per brief.
5. Shot presets with a single free-text `camera_motion` per preset: no ordered moves or trajectories. Open Workflow's move vocabulary is richer (see spec.md).
6. SQLite per subsystem with forward-only `PRAGMA user_version` migrations and an event log. Adopt for Open Workflow.

Implement independently (no import): all of the above. UNKNOWN: its job-queue/GPU-lock implementation (not read), its recovery behavior.

## 5. Prior work in this repo (VERIFIED)
- `spec.md`: behavior-only spec of higgsfield.ai (Cinema Studio camera controls, Soul ID, Genjutsu, Canvas) from REA 6.4.0 captures in `evidence/` (logged-out only).
- `comfyui-open_workflow/`: 15-node ComfyUI pack, 9 unit tests passing (`cd tests && python -m pytest -q`). Not yet exercised inside a running ComfyUI.

## 6. Baseline
- Open Workflow repo location: `<local path> User\Documents\My Apps\Open Workflow` (own folder, no `.git` yet; will `git init` here, never inside Content Studio).
- Tests: 9 passed (node pack core). No application code exists yet.
- User data to protect: everything under the ComfyUI model dirs and Content Studio (`contentstudio.db`, `projects`, `runtime`, `cache`...). Open Workflow only reads these.
