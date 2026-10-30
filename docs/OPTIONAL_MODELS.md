# Optional models

Open Workflow never downloads model weights for you. The optional models below are listed with their license, where to get them, exactly where to put the files, and how to check the install. Nothing here starts a download.

## How to check an install

1. Put the files in your ComfyUI `models` folder, using the subfolders shown below. Subfolder names are free: Open Workflow matches by file name inside the folder type.
2. Open **Model Library** in Open Workflow and press **Scan folders**.
3. Open the model. Each file shows its status. A file found by name is not yet confirmed. Confirm the files you trust.
4. Open **Workflows** in the Model Library. A workflow shows "requirements met, untested" only when all of its files and nodes are present. It does not run until a generator is connected.

## Models

| Model | License (verified on the model page) | Open-weight eligible? | Size | Where to get it |
|---|---|---|---|---|
| **LTX-2.5 image to video** (distilled, NVFP4 single stage) | LTX-2.x Community License. Companies over USD 10M annual revenue need a paid agreement. | No. Use is restricted. | Not verified | https://huggingface.co/Lightricks/LTX-2.5 |
| **MiniMax H3** (first and last frame to video, W4A8) | MiniMax H3 Community License. Excludes the US, EU, UK and South Korea. Revenue above USD 20M needs authorization. Must show "MiniMax H3" in your UI. | No. Use is restricted. | Not verified | https://huggingface.co/MiniMaxAI/MiniMax-H3 |
| **Wan 2.1 image to video** (480p, full precision) | Apache-2.0 (publisher checkpoint, verified) | Yes, on the publisher checkpoint. Third-party conversions are not verified. | About 82 GB for the repository | https://huggingface.co/Wan-AI/Wan2.1-I2V-14B-480P |

"Open-weight eligible" here is Open Workflow's own policy check, not a legal opinion. The licenses above restrict use in ways that are not open-source. Read them before you use the model, and check whether they apply to you.

## Where each file goes

**LTX-2.5** (single stage)
- `diffusion_models/ltx 2.5/LTX25-distilled-DiT-comfy-nvfp4.safetensors`
- `text_encoders/ltx 2.5/gemma4-12b-ltx25-comfy-w4a8.safetensors`
- `vae/ltx 2.5/ltx-2.5-video-vae-bf16.safetensors`

Not included: the two-stage upscaler and prompt-enhancer in the official template.

**MiniMax H3** (needs a custom node pack)
- `diffusion_models/minimax h3/minimax_h3_fl2va_pruned_w4a8_mixed.safetensors`
- `text_encoders/minimax h3/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors`
- `vae/minimax_h3_video_vae_fp16.safetensors`
- Custom-node pack `ComfyUI-MiniMaxH3Mod` in `custom_nodes/`. ComfyUI must be able to load custom nodes.

Not included: the hosted prompt-interpretation and 2K stages, which MiniMax serves through its API.

**Wan 2.1** (publisher checkpoint, 7 shards)
- `diffusion_models/Wan2.1/diffusion_pytorch_model-00001-of-00007.safetensors` through `-00007-of-00007` (all seven are required)
- `text_encoders/models_t5_umt5-xxl-enc-bf16.pth`
- `vae/Wan2.1_VAE.pth`
- `clip_vision/models_clip_open-clip-xlm-roberta-large-vit-huge-14.pth`

Full precision needs roughly 16 GB of VRAM plus offloading to system RAM. It has not been tested on this machine.

## Status of the workflows

No optional workflow has a generator yet. The Director shows "No generator is connected for this workflow yet" for them. Adding a generator is a separate step that needs a graph run on the real files.

## Custom nodes

Your ComfyUI may stop at startup if an installed custom node is missing a dependency. Open Workflow does not change ComfyUI's custom nodes. If ComfyUI will not start, fix that node or start ComfyUI with `--disable-all-custom-nodes`. That flag also disables the nodes these models may need.
