# Architecture (PROPOSED, justified by the audit)

## Stack
| Layer | Choice | Why (evidence) |
|---|---|---|
| Backend | Python 3.12 + FastAPI/uvicorn + Pydantic v2 + SQLite (stdlib) | all installed (VERIFIED); typed contracts at boundaries; no new native deps |
| Engine | ComfyUI over its **HTTP/WebSocket API on 127.0.0.1:8188**, started by Open Workflow from the user's portable install or an existing server | decoupled from Content Studio (which embeds ComfyUI); works with any ComfyUI; port 8188 free |
| Frontend | React 19 + TypeScript + Vite; `@xyflow/react` for the canvas | npm registry reachable (VERIFIED, react 19.3.1); mature node-graph lib |
| Desktop shell | **Deferred (P1).** MVP = local launcher (`open_workflow.bat`) serving UI on loopback and opening the browser | least moving parts; Electron only if time remains |
| Persistence | SQLite for metadata + managed folders for media; portable project = folder with `project.json` + `assets/` | media outside SQLite |
| Video | FFmpeg discovered, never bundled; features degrade honestly if absent (not on PATH today) | VERIFIED |
| Node pack | `ComfyUI-Open Workflow` (the existing `comfyui-open_workflow/`, renamed) | independently installable |

## Process diagram
```
Browser UI (React)  <--HTTP/SSE-->  Open Workflow backend (FastAPI, 127.0.0.1:8765)
                                      |  project store (SQLite + files)
                                      |  model registry / hardware / consent downloads
                                      |  job engine (single GPU owner, state machine)
                                      v
                                  ComfyUI server (127.0.0.1:8188)  + ComfyUI-Open Workflow nodes + open-weight models
```
The Director and the Canvas both call `jobs.submit(workflow_id, inputs)`; there is exactly one execution path.

## Backend modules (`backend/open_workflow/`)
`config`, `hardware`, `comfy` (client + health + launch), `models` (registry, discovery, downloads, license), `adapters` (contract + one file per workflow),
`workflows` (registry + validation), `projects` (schema, migrations, assets, versions), `entities` (characters/worlds/styles/props), `director` (script parser, shot compiler, coverage),
`camera` (capability-aware intent), `jobs` (queue, state machine, recovery), `continuity`, `video` (probe/transform), `graph` (typed ports + validation + executor), `export`, `diagnostics`, `api` (routers).
No global mutable state beyond one `AppContext` object passed explicitly.

## Adapter contract (`adapters/base.py`)
Fields: `id, version, name, description, task, required_models, optional_models, min_hardware, input_schema, output_schema, supported_params, unsupported_params,
license_info, workflow_path, capabilities{control -> unsupported|prompt_only|conditioned|native|unvalidated}`.
Methods: `validate(inputs, ctx) -> Report`, `build(inputs, ctx) -> comfy_api_graph`, `parse_progress(msg)`, `cancel(job)`, `collect_outputs(history)`.
`validate` **rejects or flags** any requested control the adapter marks unsupported, so nothing is silently ignored.

## Job state machine
`QUEUED -> VALIDATING -> LOADING_MODEL -> RUNNING -> POSTPROCESSING -> COMPLETED`; side exits `FAILED, CANCELLING, CANCELLED, INTERRUPTED, RECOVERY_REQUIRED`.
Persisted before execution; on startup any RUNNING job becomes INTERRUPTED (never silently re-run). One GPU slot; retries bounded (max 1 automatic, user-initiated otherwise).
Progress: only percentage when ComfyUI emits step progress; else indeterminate.

## Project schema v1 (stable IDs, relative paths)
`Project{id,schema_version,...}`, `Character{id,versions[]}` (identity -> scene appearance -> shot override), `World`, `Prop`, `StyleProfile`, `Script`, `Scene`, `Shot{id,intent,refs,workflow,settings}`,
`Take{id,shot_id,job_id,asset_id,status,approved}`, `Asset{id,rel_path,mime,size,sha256,parents,source_job,provenance}`, `Job`, `ContinuityRule`, `Warning`, `Graph`, `ExportRecord`.
Migrations: forward-only `PRAGMA user_version`; writes via temp file + atomic rename.

## Camera honesty
Each shot stores *intent* (framing, angle, movement, lens, DoF, composition). The compiler maps intent per adapter capability. Image adapters: movement is metadata only and the UI says so.
Video adapters (Wan/LTX via prompt): `prompt_only`. A numeric trajectory (Open Workflow `Camera Trajectory`) is `native` only for an adapter that really consumes poses (none validated yet).

## Security
Loopback-only binding; path allow-list (project dir, declared model dirs); no shell from user input; secrets never logged; no telemetry; downloads need consent record.

## What is UNKNOWN now
Exact ComfyUI API graph for each starter workflow (will be exported from real runs); Wan/LTX node availability in the installed pack versions; whether Ollama has a usable planner model.
