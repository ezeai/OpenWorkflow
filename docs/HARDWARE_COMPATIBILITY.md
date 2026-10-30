# Hardware Compatibility

Reference machine (VERIFIED): RTX 3080 Laptop GPU with 16 GB VRAM, 63.7 GB RAM, Windows 10, ComfyUI 0.41.2 with torch 2.8.0+cu128.

## Live readings observed during Milestone 2 (VERIFIED, dated)

| Time (local) | GPU VRAM free | RAM available | Note |
|---|---|---|---|
| 2026-10-17 ~07:45 (during Milestone 0) | 15.6 of 16 GB in use by another process | n/a | Content Studio or another program held the GPU |
| 2026-10-17 ~09:40 (Milestone 2 UI check) | 8.6 of 16 GB free | 12.5 of 63.7 GB | |
| 2026-10-17 ~09:48 | 14.9 of 16 GB free | 11.7 of 63.7 GB | Other program released the GPU |

GPU availability changes during the day, so a single reading is not a guarantee for any generation. Open Workflow never terminates other programs to free VRAM.

## Workflow fit (NOT measured)

No workflow has been run, so no VRAM figure is known for any of them. The files are large: Z-Image Turbo BF16 is 11.5 GB, Wan 2.1 GGUF is 9.5 GB. Whether they fit in 16 GB with their encoders is UNKNOWN until a real run is recorded. Open Workflow therefore shows no fit estimate and labels every workflow as untested.

## Status vocabulary in the UI

Ready and validated (needs a recorded run), requirements met but untested, resource constrained (not yet assessed), missing dependencies, unsupported, unknown. In this milestone, only the dependency-based statuses are computed. The resource check is not implemented.

## Low-memory guidance

When free VRAM is low, the UI recommends closing other workloads, picking a validated low-memory workflow, or postponing. It does not free memory on its own.
