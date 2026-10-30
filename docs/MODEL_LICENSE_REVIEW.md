# Model License Review

Review date: 2026-10-17. Method: WebFetch of the publisher's Hugging Face model page (YAML `license` field and license section),
plus secondary sources only where the primary page did not state the terms. Verdicts follow `OPEN_WEIGHTS_POLICY.md`.

**Important:** "verified" here means the publisher's model card states the license identifier. It does not mean the LICENSE file was
read line by line, that the auxiliary files were checked, or that a legal review was done. Each row lists what remains unverified.

## Summary
| Family / variant installed | Publisher repo (evidence) | License identifier (evidence) | Verdict for Open Workflow | Outstanding |
|---|---|---|---|---|
| Z-Image Turbo (`z_image_turbo_bf16`, `z-image-turbo_fp8_scaled_e4m3fn_KJ`) | `Tongyi-MAI/Z-Image-Turbo`, primary HF page fetched | `apache-2.0` (YAML). Card: "licensed under the Apache 2.0 License" | **License verified. Open-weight eligibility: REVIEW**: text encoder and VAE not verified | Auxiliary files (`qwen_3_4b`, `ae.safetensors`) unverified; the KJ fp8 file is a third-party repackage, its own license is unverified |
| FLUX.2 klein 4B (`flux-2-klein-4b`) | `black-forest-labs/FLUX.2-klein-4B`, primary HF page fetched | `apache-2.0`. Card: "Open weights available for commercial use under the Apache 2.0 license" | **License verified. Open-weight eligibility: REVIEW**: VAE and text encoder unverified | The **9B** variant (`flux-2-klein-9b-*`) is **not** Apache. Secondary sources say it is the FLUX Non-Commercial License. Do not infer 9B from 4B. |
| Qwen-Image-2.1 (GGUF Q4_K_M, INT8 ConvRot unet, VAE, Qwen2.5-VL encoder) | `Qwen/Qwen-Image-2.1`, primary HF page fetched | `qwen-research` ("Qwen Research License Agreement", `LICENSE` file on repo). **Page text did not state the commercial terms.** | **NOT eligible for the default open-weights path.** Secondary sources (vendor blog, one analysis) say non-commercial, commercial use needs a separate license. Not read from the LICENSE file. | Read `LICENSE` in the repo. Until then: restricted, excluded from starter workflows. Earlier Qwen-Image releases were Apache-2.0, so do not generalize. |
| Wan 2.1 I2V 14B 480P (`wan2.1-i2v-14b-480p-Q4_0.gguf`, `wan_2.1_vae`, `umt5_xxl_fp8`, `clip_vision_h`) | `Wan-AI/Wan2.1-I2V-14B-480P`, primary HF page fetched | `apache-2.0`. Card: "licensed under the Apache 2.0 License" | **License verified for the publisher checkpoint. The installed file is a GGUF conversion from a third party**: its license is inherited from the publisher per the card, but the conversion repo was not checked | Confirm the GGUF conversion repo license; UMT5 and CLIP-vision licenses unverified |

Verdicts use the policy language: "verified" = identifier on the publisher's page. "Eligible" needs every file, auxiliary component, and the workflow reviewed. None of the four is eligible yet.

## Per-model evidence

### Z-Image Turbo
- Repo: https://huggingface.co/Tongyi-MAI/Z-Image-Turbo (fetched 2026-10-17). YAML `license: apache-2.0`.
- Secondary (not relied on for the verdict): third-party docs and quantization repos cite Apache-2.0.
- Note: one secondary source listed a 60B parameter count, which conflicts with the 6B used elsewhere. Ignored, not relied on.
- Commercial restrictions: none stated on the publisher's page beyond the Apache-2.0 terms.
- Redistribution: Apache-2.0 terms (attribution and license notice). Open Workflow does not redistribute weights.

### FLUX.2 klein 4B
- Repo: https://huggingface.co/black-forest-labs/FLUX.2-klein-4B (fetched). YAML `license: apache-2.0`; card says commercial use allowed under Apache-2.0.
- Secondary: BFL help article says the 4B is Apache-2.0 and the 9B is under a non-commercial license.
- Installed file `flux-2-klein-4b.safetensors` is matched by **filename only**. Its checksum is not compared to the publisher's, so identity is UNKNOWN until the user confirms it or a hash is recorded.

### Qwen-Image-2.1
- Repo: https://huggingface.co/Qwen/Qwen-Image-2.1 (fetched). YAML `license: qwen-research`; license file `LICENSE`; "Qwen Research License Agreement".
- The page text did not include the restriction sentence. Secondary sources (vendor blog, an analysis) report a non-commercial research license with commercial use under separate agreement. **Unverified against the LICENSE text.**
- Verdict: restricted. Excluded from starter workflows; the Model Library shows the restriction.

### Wan 2.1 I2V 14B 480P
- Repo: https://huggingface.co/Wan-AI/Wan2.1-I2V-14B-480P (fetched). YAML `license: apache-2.0`; card: "licensed under the Apache 2.0 License".
- Installed file is a GGUF Q4_0 conversion, not the publisher's safetensors. Identity and license of that conversion are unverified.

## Not reviewed in this milestone
Installed families outside the four candidates (MiniMax H3, Krea2, Ideogram4, HiDream, Ming, Viggle, YuE, ACE-Step, SeedVR2, SAM variants, InstantID, IP-Adapter, etc.). They are listed as **license unverified** and excluded from workflows.

## Policy consequences
1. No workflow is labelled "open-weight eligible" in this milestone.
2. Qwen-Image-2.1 is excluded from starter and default workflows.
3. Downloads: none of these models is downloaded automatically. The download manager only offers files whose exact manifest was verified (none yet; see `MODEL_INVENTORY.md`).

## Upscalers for the contest pipeline demo (checked 2026-10-28 on openmodeldb.info)

| File | Author | Licence | Use |
|---|---|---|---|
| `4x-UltraSharp.pth` | Kim2091 | CC-BY-NC-SA-4.0 (non-commercial) | Used in the first live pipeline check only. Not used in the demo. |
| `4xNomos8kDAT.pth` | Helaman | CC-BY-4.0 | Stage 2 of the demo pipeline (`docs/evidence/pipeline-live-2026-10-28/pipe_test.py`). Attribution is needed in the README. |
