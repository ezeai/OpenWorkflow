# Model requirements

## Why models are not included

Model weights are large and each has its own licence, some with use restrictions. Open Workflow ships no weights and downloads none. You place the files yourself, in the folders ComfyUI already uses.

## Where models are stored

- **ComfyUI's own folders**, for example `ComfyUI\models\checkpoints`, `models\diffusion_models`, `models\loras`, `models\vae`, `models\text_encoders`, `models\upscale_models`. These are the normal places.
- **Extra folders** you add in Settings. Open Workflow reads them in place.
- **Open Workflow's managed folder**, `data\models\managed`. Used only for files Open Workflow itself downloads after you confirm a verified source. This release has no such download source, so it is normally empty.

## How Open Workflow discovers them

- It reads the file names and headers of files in the folders above. It does not scan the whole computer.
- Each file is shown with its location, its size and whether its header reads correctly.
- A file it cannot identify with confidence is shown as **unknown**. It is never matched by a similar name alone.
- Discovery results are cached. Use **Rescan** in the Model Library after you add or remove files.

## How workflow requirements are matched

When you import a workflow, Open Workflow reads the model files it names. Each requirement is then:

- **Found and confirmed**: a file with the right name, located where the workflow expects it.
- **Found, not confirmed**: a file with a similar name or in another folder. Check it yourself before running.
- **Missing**: no file found. The app shows **Needs attention** and names the file.
- **Unknown**: the workflow names something Open Workflow cannot check. Run it yourself and confirm.

A readiness state of **Ready** is only shown when every requirement is confirmed.

## Resolving a missing model

1. Find the exact file name the app shows (Model Library or the app's readiness card).
2. Download it from the model's publisher page yourself. Read its licence first.
3. Place it in the ComfyUI folder the app names (or an extra folder you added in Settings).
4. Click **Rescan** in the Model Library, then check the app again.

## Custom nodes

A workflow can need a custom node pack as well as model files. Open Workflow lists the node classes it needs and whether ComfyUI reports them. It does not install them. Install packs yourself through ComfyUI Manager or by copying them into ComfyUI's `custom_nodes` folder, then restart ComfyUI.

## What the example package needs

`docs/evidence/forge-m5/image_z_image_turbo.forgeapp` (Z-Image Turbo text-to-image):

| Requirement | Kind | Where it goes | Licence note |
| --- | --- | --- | --- |
| Z-Image Turbo diffusion model (bf16) | diffusion model | `models\diffusion_models` | Apache-2.0 per the model card; check the file you download |
| Qwen 3 4B text encoder | text encoder | `models\text_encoders` | Check the publisher's licence |
| Z-Image VAE (`ae`) | VAE | `models\vae` | Check the publisher's licence |

The workflow uses ComfyUI's core nodes only. No custom node pack is needed.

The 4x-UltraSharp upscaler used in the pipeline example has **not** had its licence reviewed. Review it before you publish a pipeline that uses it. See [MODEL_LICENSE_REVIEW.md](MODEL_LICENSE_REVIEW.md).
