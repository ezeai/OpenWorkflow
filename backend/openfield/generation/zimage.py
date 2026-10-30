"""Z-Image Turbo text-to-image graph (ComfyUI API format), core nodes only.

Files are referenced by their exact names inside the ComfyUI model folders.
Parameters are plain values; nothing here is read from user-supplied paths.
"""
from __future__ import annotations

WORKFLOW_ID = "img-z-image-turbo"
# ComfyUI addresses models by their path relative to the models folder (e.g. 'z-Image-Turbo\file.safetensors').
# The caller passes these names, resolved from the discovered file locations; they are never guessed here.


def build(prompt: str, negative: str, width: int, height: int, seed: int, *, unet: str, text_encoder: str, vae: str,
          steps: int = 8, cfg: float = 1.0, filename_prefix: str = "openfield") -> dict:
    if width % 16 or height % 16 or not (256 <= width <= 2048 and 256 <= height <= 2048):
        raise ValueError("width and height must be multiples of 16 between 256 and 2048")
    if not (1 <= steps <= 50):
        raise ValueError("steps must be between 1 and 50")
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": unet, "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": text_encoder, "type": "lumina2", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": vae}},
        "4": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["1", 0], "shift": 3.0}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["2", 0]}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["2", 0]}},
        "7": {"class_type": "EmptySD3LatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
        "8": {"class_type": "KSampler", "inputs": {"model": ["4", 0], "seed": seed, "steps": steps, "cfg": cfg,
                                                   "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0,
                                                   "positive": ["5", 0], "negative": ["6", 0], "latent_image": ["7", 0]}},
        "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["3", 0]}},
        "10": {"class_type": "SaveImage", "inputs": {"images": ["9", 0], "filename_prefix": filename_prefix}},
    }
