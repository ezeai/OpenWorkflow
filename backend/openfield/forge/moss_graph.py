"""MOSS-Image text-to-image graph (turbo path) for a ComfyUI that has the MOSS support installed.

Ported from Content Studio's MOSS-Image provider (studio_models/moss_image.py), text-to-image only: the same
model files, the same node settings, and the same bounding-box layout (see moss_layout.py). The graph uses
ComfyUI core nodes plus the MOSS CLIP type. It is an API-format prompt, the same shape Forge already runs.

Not included yet: reference-image editing (needs the Ostris edit pack), CAOTICA, the base (50-step) path, and LoRA stacks.
"""
from __future__ import annotations

from . import moss_enhancer, moss_layout

UNET = "moss_image_sft_int8_convrot.safetensors"
CLIP = "qwen3.5_9b_w4a8_gptq.safetensors"          # the Qwen3.5 9B encoder MOSS was built with
VAE = r"flux 2\flux2-vae.safetensors"              # MOSS latents are 128-channel: the FLUX.2 VAE
LORA_TURBO = r"moss\moss_image_sft_4_steps_lora_v2_600_avg_rank_72_bf16.safetensors"

NEGATIVE = "nsfw, worst quality, bad anatomy, watermark, jpeg artifact, text, letters, typography, words"
NEGATIVE_LAYOUT = "nsfw, worst quality, bad anatomy, watermark, jpeg artifact, misspelled text, extra text, garbled letters"
TURBO = {"steps": 4, "cfg": 1.0, "sampler": "euler", "scheduler": "sgm_uniform"}
OUTPUT_PREFIX = "OpenField/moss_image"
MIN_SIDE, MAX_SIDE = 256, 2048


class GraphError(ValueError):
    """The request cannot make a valid graph. The message is safe to show."""


def build_t2i(prompt: str, seed: int, width: int = 1024, height: int = 1024, boxes: list[dict] | None = None) -> dict:
    prompt = (prompt or "").strip()
    if not prompt:
        raise GraphError("the prompt is empty")
    if not (MIN_SIDE <= width <= MAX_SIDE and MIN_SIDE <= height <= MAX_SIDE) or width % 16 or height % 16:
        raise GraphError(f"width and height must be multiples of 16 between {MIN_SIDE} and {MAX_SIDE}")
    if not isinstance(seed, int) or isinstance(seed, bool) or seed < 0:
        raise GraphError("the seed must be a whole number, zero or more")
    text = moss_layout.layout_prompt(prompt, boxes, width, height)
    has_text = any(b.get("type") == "text" for b in (boxes or []) if isinstance(b, dict))
    negative = NEGATIVE_LAYOUT if has_text else NEGATIVE
    return {
        "unet": {"class_type": "UNETLoader", "inputs": {"unet_name": UNET, "weight_dtype": "default"}},
        "clip": {"class_type": "CLIPLoader", "inputs": {"clip_name": CLIP, "type": "moss_image", "device": "default"}},
        "vae": {"class_type": "VAELoader", "inputs": {"vae_name": VAE}},
        "lora": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["unet", 0], "lora_name": LORA_TURBO, "strength_model": 1.0}},
        "pos": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["clip", 0], "text": text}},
        "neg": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["clip", 0], "text": negative}},
        "latent": {"class_type": "EmptyFlux2LatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
        "sample": {"class_type": "KSampler", "inputs": {
            "model": ["lora", 0], "positive": ["pos", 0], "negative": ["neg", 0], "latent_image": ["latent", 0],
            "seed": seed, "control_after_generate": "fixed", "steps": TURBO["steps"], "cfg": TURBO["cfg"],
            "sampler_name": TURBO["sampler"], "scheduler": TURBO["scheduler"], "denoise": 1.0}},
        "decode": {"class_type": "VAEDecode", "inputs": {"samples": ["sample", 0], "vae": ["vae", 0]}},
        "save": {"class_type": "SaveImage", "inputs": {"images": ["decode", 0], "filename_prefix": OUTPUT_PREFIX}},
    }


def build_text(idea: str, max_length: int = 400, system_prompt: str = "", seed: int = 42) -> dict:
    """A prompt written by the MOSS text encoder (ComfyUI core TextGenerate), shown back through PreviewAny.

    With a system_prompt, sampling is on and uses the MOSS enhancer's settings (moss_enhancer.SETTINGS); without one,
    the output is greedy. The same Qwen3.5 encoder that draws the poster writes the text, so no second model is needed.
    """
    idea = (idea or "").strip()
    if not idea:
        raise GraphError("the idea is empty")
    if not (16 <= max_length <= 400):
        raise GraphError("max_length must be between 16 and 400 tokens")
    inputs = {"clip": ["clip", 0], "prompt": idea, "max_length": max_length, "thinking": False,
              "use_default_template": True, "mtp": "off", "sampling_mode": "off"}
    if system_prompt:
        s = moss_enhancer.SETTINGS
        inputs.update({
            "system_prompt": system_prompt, "sampling_mode": "on",
            "sampling_mode.temperature": s["temperature"], "sampling_mode.top_k": s["top_k"],
            "sampling_mode.top_p": s["top_p"], "sampling_mode.min_p": s["min_p"],
            "sampling_mode.repetition_penalty": s["repetition_penalty"],
            "sampling_mode.presence_penalty": s["presence_penalty"], "sampling_mode.seed": seed,
        })
    return {
        "clip": {"class_type": "CLIPLoader", "inputs": {"clip_name": CLIP, "type": "moss_image", "device": "default"}},
        "gen": {"class_type": "TextGenerate", "inputs": inputs},
        "show": {"class_type": "PreviewAny", "inputs": {"source": ["gen", 0]}},
    }


# Poster types and their shapes. The model needs sides that are multiples of 16 (it is latent-based), so each size is
# rounded to 16. "base" is the short side in pixels: 512 for a quick draft, 1024 for the final.
ASPECTS = {
    "square": {"name": "Square", "use": "Feed post, product card", "w": 1, "h": 1},
    "portrait": {"name": "Portrait 4:5", "use": "Instagram and Facebook feed", "w": 4, "h": 5},
    "story": {"name": "Story 9:16", "use": "Stories, reels, phone wallpaper", "w": 9, "h": 16},
    "banner": {"name": "Banner 16:9", "use": "Web hero, video thumbnail, screen", "w": 16, "h": 9},
    "poster": {"name": "Poster A-series", "use": "Print poster, A-size proportions (1 : 1.414)", "w": 1, "h": 1.4142},
}
BASES = (512, 1024)


def _round16(v: float) -> int:
    return max(MIN_SIDE, min(MAX_SIDE, int(round(v / 16.0)) * 16))


def size_for(aspect: str, base: int = 512) -> tuple[int, int]:
    """Pixel width and height for a poster type, with the short side at `base`. Both sides are multiples of 16."""
    if aspect not in ASPECTS:
        raise GraphError(f"unknown poster type '{aspect}'")
    if base not in BASES:
        raise GraphError(f"base must be one of {', '.join(str(b) for b in BASES)}")
    a = ASPECTS[aspect]
    if a["w"] <= a["h"]:
        w, h = base, base * a["h"] / a["w"]
    else:
        w, h = base * a["w"] / a["h"], base
    return _round16(w), _round16(h)
