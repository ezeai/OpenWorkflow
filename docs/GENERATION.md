# Generation (Milestone 5)

## What runs
One workflow: **Z-Image Turbo, text to image** (`img-z-image-turbo`). Core ComfyUI nodes only: UNETLoader, CLIPLoader (type `lumina2`), VAELoader, ModelSamplingAuraFlow (shift 3), CLIPTextEncode, EmptySD3LatentImage, KSampler (euler, simple, 8 steps, cfg 1), VAEDecode, SaveImage. Graph: `backend/open_workflow/generation/zimage.py`.

Files: `z-Image-Turbo\z_image_turbo_bf16.safetensors` (diffusion), `qwe3 vl gguf\qwen_3_4b.safetensors` (text encoder), `ae.safetensors` (VAE). ComfyUI addresses models by their path inside the folder type, so Open Workflow derives these names from where discovery found each file. They are never typed by hand.

## Setup you need to run it
1. Start ComfyUI on loopback (the address in Settings, default `127.0.0.1:8188`). Example, using the portable install:
   ```bash
   python_embeded\python.exe -s ComfyUI\main.py --listen 127.0.0.1 --port 8188 --disable-all-custom-nodes
   ```
   `--disable-all-custom-nodes` was needed on this machine: the install's Qwen3-TTS custom node is missing its `qwen_tts` dependency, so ComfyUI refuses to start with it loaded. Nothing in that install was changed. Generation does not need any custom node.
2. Point Open Workflow at the ComfyUI folder (Settings → ComfyUI installation).
3. In the Director, set a shot's workflow to Z-Image Turbo and press **Generate take**.

## Job states
`QUEUED → VALIDATING → RUNNING → POSTPROCESSING → COMPLETED`. Side states: `FAILED`, `CANCELLING`, `CANCELLED`, `INTERRUPTED`, `RECOVERY_REQUIRED`.

- `LOADING_MODEL` exists in the list but is never entered. The ComfyUI API does not report model loading separately, so entering it would claim something we cannot observe.
- Progress is indeterminate. ComfyUI's step progress is not used.
- One worker thread owns the GPU. Jobs run one at a time.
- Every transition is saved to `<home>/jobs/<id>.json` before it is acted on.

## Cancellation
- **Queued:** cancelled immediately. No output.
- **Running:** Open Workflow asks ComfyUI to stop (`/interrupt`, or removes a pending prompt). If ComfyUI finishes first, the job **completes** and says so. A cancelled job never produces an asset.

## Failure and recovery
- Engine unreachable: fails before submission, with a message to start ComfyUI.
- Missing model files: fails before submission, naming the missing role.
- ComfyUI error, or success with no image: `FAILED`. No asset is written.
- Timeout after 20 minutes: ComfyUI is asked to stop, and the job fails.
- **Restart:** jobs left active by a previous process are marked `INTERRUPTED` and are **never re-run**. If ComfyUI finished the prompt while Open Workflow was away, the job becomes `RECOVERY_REQUIRED` and its output can be imported.
- **Retry** creates a new job on a new take. The earlier take and its output are kept.
- Retries are manual. There is no automatic retry.

## Takes
A generation creates a take (`requested → running → generated | failed | cancelled`). The take's settings are a snapshot taken at request time. An approved take is never changed by a later job (covered by a test).

## Provenance
Every output is saved as a project asset with `provenance`: job id, workflow id and version, seed, size, steps, cfg, the positive and negative prompts, each model file's name, ComfyUI-relative name, path, size, ownership, whether its identity was confirmed, and its license status, plus the ComfyUI prompt id and app version. Limitation recorded with each output: a seed does not guarantee identical output across software or hardware versions.

## Honest limits
- **Camera settings are prompt guidance only.** The prompt says things like "dolly (guidance only)". Nothing checks the picture for camera movement. The Director shows "Not executed by this workflow" for camera movement on this workflow.
- **Identity is not confirmed** for the installed files. The job records `identity_confirmed: false` and the Jobs page says so. Confirm the files in the Model Library to clear it.
- **Prompt text is not verified against the image.** The prompt notes say so on each job.
- Only one workflow has a generator. Other workflows show "No generator is connected".
- Sizes are limited to 512 and 768 square for now.

## Real run (2026-10-17)
Hardware: RTX 3080 Laptop, 16 GB VRAM (other programs were holding part of it; free VRAM was about 10 GB at start). ComfyUI 0.41.2, core nodes only.

| Run | Via | Size, steps, seed | Result | Time |
|---|---|---|---|---|
| Smoke test (graph submitted directly) | ComfyUI API | 512², 8, 42 | completed | about 89 s, cold model load |
| End-to-end through Open Workflow | app job engine, E2E script | 512², 8, 7 | COMPLETED, asset saved with provenance | 18 s |
| Browser | Director → Generate take | 512², 8, random | COMPLETED, take shows image | about 20 s |

Evidence: `docs/evidence/milestone5/` (`smoke-zimage-512.png`, `e2e-zimage-output.png`, `e2e-job.json`, `e2e-asset.json`). The output image matches the prompt, which named the market, the lanterns and the character.
