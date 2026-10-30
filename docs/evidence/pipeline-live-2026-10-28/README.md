# Live two-stage pipeline check, 2026-10-28

Script: `pipe_test.py` (local Forge API only, no cloud, no downloads). Run against a **separate backend instance** on port 8766 with `OPENFIELD_HOME` set to a scratch folder, so the user's `data/` was not touched by the module and pipeline records. (Before that, two workflow imports, `pipe-test-zimage` and `pipe-test-upscale`, were made against the running backend on 8765, which writes to `data/`. They are harmless and can be deleted from the Apps library.)

Setup: ComfyUI 0.43.0 on 127.0.0.1:8188, RTX 3080 Laptop 16 GB.

| Stage | Module | Models (installed) |
|---|---|---|
| `draft` | Z-Image Turbo text-to-image, 512 × 512, seed 42 (fixture `backend/tests/fixtures/forge/image_z_image_turbo.json`) | z_image_turbo_bf16, qwen_3_4b, ae |
| `upscale` | Hand-written API graph: LoadImage → 4xNomos8kDAT → ImageScaleBy 0.5 → SaveImage (net 2×) | 4xNomos8kDAT.pth (CC-BY-4.0) |

## Results (three runs)

| Run | Stage 1 | Handoff | Stage 2 | Pipeline state |
|---|---|---|---|---|
| 1 | COMPLETED, 512 × 512, sha256 `f8c80645…` | Output re-uploaded as `openfield_f8c8064538f9f9c1.png`; file confirmed in ComfyUI's input folder (`/view` 200) | **FAILED in VALIDATING**: "ComfyUI is not running on the configured address" (4 s after VALIDATING started) | `failed`, message names the stage. No false success. |
| 2 | COMPLETED, same sha256 (same seed, reproducible) | Same | Same failure | `failed` |
| 3 (with a latency probe) | COMPLETED (116 s RUNNING) | **Failed in `has_input`**: "ComfyUI is not reachable, so the image input cannot be checked" | Not submitted | `failed` |

Latency probe during run 3: `GET /system_stats` took **6.4 s** once, right after stage 1 completed (all other probes under 1.1 s).

## Conclusion

- Chaining logic is correct on real ComfyUI: output read, validated, re-uploaded, bound to the next stage's image port, stage 2 submitted with provenance (`pipeline_run_id`, `stage_index`, module and snapshot checksums in the receipt).
- **Root cause of failure:** ComfyUI stalls for roughly 5 to 7 s just after a job finishes. The handoff path runs exactly then, and two probes have short timeouts: `ComfyRunner.reachable()` (3 s, `generation/comfy.py`) and `_comfy_has_input` (5 s, `api_forge.py`). Either one times out, and the pipeline fails honestly.
- Failure handling is verified: a failed stage ends the run with `failed` and a stage-named message, and nothing is reported as complete.
- **End-to-end success is NOT yet verified.** It needs the probe fix plus three passing runs.

Output of stage 1: `stage1-draft.png` (checked by eye: lighthouse on a cliff at sunset, matches the prompt).

## Update: probe fix applied (same day)

- `generation/comfy.py`: `get_patient()` retries a **timeout** for up to `BUSY_WAIT` = 20 s; a refused connection fails at once. `ComfyRunner.reachable(wait=…)` waits only when asked. The job pre-flight (`jobs.py`) and the pipeline input check (`api_forge._comfy_has_input`) pass `BUSY_WAIT`; status pages keep the quick check.
- Tests: 3 new tests in `backend/tests/test_forge_module_images.py` (busy then OK, busy for good, quick versus patient). Per-file runs: module_images 17, pipeline_m4 16, modules_m3 24, forge_m3 12, generation_m5 18, providers_m6 19, take_approval 6, continuity_graph_m6 17. **All passed.**
- Stage 2 now uses `4xNomos8kDAT.pth` (CC-BY-4.0) instead of 4x-UltraSharp (CC-BY-NC-SA).
- **Live gate (G2) not yet passed.** `gate.py` was started, but **ComfyUI had stopped** (connection refused on 8188). Every run failed at once with "ComfyUI is not running", which is the correct, quick refusal. Rerun once ComfyUI is up:
  1. Start an isolated backend: `OPENFIELD_HOME=<scratch>`, port 8766.
  2. Run `python pipe_test.py <z-image workflow id> <out>`. This creates the Nomos module.
  3. Run `python gate.py http://127.0.0.1:8766 . "Upscale 2x"`. The latest module with that name is used.

## Gate G2 result: PASSED (2026-10-28, GPU)

Setup: NVIDIA driver 617.42 (signed by NVIDIA, installed by the user). ComfyUI 0.43.0 on the RTX 3080 Laptop GPU (cuda:0). Isolated backend on 8766. Stage 2 is `4xNomos8kDAT.pth` (CC-BY-4.0).

| Run | Seed | Result | Time | Stage 1 | Stage 2 |
|---|---|---|---|---|---|
| 1 | 101 | completed | 64.2 s | 512×512 | 1024×1024, sha256 `e6086fb3…` |
| 2 | 202 | completed | 68.1 s | 512×512 | 1024×1024, sha256 `7e7c4a14…` |
| 3 | 303 | completed | 54.1 s | 512×512 | 1024×1024, sha256 `2451f7ca…` |
| Forced failure | 404 | failed: "stage 'draft' did not complete (cancelled)" | — | cancelled | pending (never ran) |

Every completed stage has a receipt with the same `pipeline_run_id`, correct `stage_index`, module version and snapshot checksum. The upscaled image was checked by eye. Raw data: `gate-results.json`, `gate-*.png`.

Still out of the gate: the earlier one-off run (`stage1-draft.png`, `pipe_test.py` output) is also complete and matches.

## Chain UI (built 2026-10-28)

- `frontend/src/Chain.tsx`: "Chain two apps" on the Apps page. Step 1 is any app that makes an image from text; step 2 is any app with an image input, which receives step 1's result. Each app is saved as a module, the two become a pipeline, and the page polls the run and shows each step's state and the final image.
- `frontend/src/Chain.test.tsx`: 7 tests on the step plan rules (pass).
- `npm run build`: passes. `npx tsc --noEmit`: passes.
- Request shapes were checked against the live backend on 8766: module creation and a pipeline run (`UI check chain`, completed in 83.7 s).
- Not yet done: a click-through in the browser pane, and the UI's two-step run (the backend two-step path is covered by the gate above).
- Known limits: each click saves new modules (no reuse yet); step 1 must be text-to-image; "Use this result in…" on a finished run is not built, the chain lives in its own section.
