"""Shot presets for an AI Influencer character: each preset is a framing with a prompt that keeps the character.

Presets say what the shot shows, not who the person is: identity comes from the anchor photo on every run.
The headless presets are for video models, which drift when the face is in frame.
"""
from __future__ import annotations

PRESETS: tuple[dict, ...] = (
    {"id": "close_up", "label": "Close-up", "headless": False,
     "prompt": "Close-up portrait of the same person as the reference, face and shoulders in frame, full hair visible, soft natural light, plain light background"},
    {"id": "medium", "label": "Medium shot", "headless": False,
     "prompt": "Medium shot of the same person as the reference, waist up, the same outfit, natural light, plain light background"},
    {"id": "full_body", "label": "Full body", "headless": False,
     "prompt": "Full-body shot of the same person as the reference, standing, the same outfit, natural light, plain light background"},
    {"id": "three_quarter", "label": "Three-quarter", "headless": False,
     "prompt": "Three-quarter view of the same person as the reference, turned slightly, the same outfit, soft light, plain light background"},
    {"id": "back_view", "label": "Back view", "headless": False,
     "prompt": "Back view of the same person as the reference, standing, the same hair and outfit, plain light background"},
    {"id": "headless_front", "label": "Headless front (video)", "headless": True,
     "prompt": "Full-body front view of the same person as the reference cropped at the neck so the head is not shown, the same outfit, plain light background"},
)
_BY_ID = {p["id"]: p for p in PRESETS}


class ShotError(ValueError):
    """The request cannot be done. The message is safe to show."""


def preset(preset_id: str) -> dict:
    p = _BY_ID.get(preset_id)
    if p is None:
        raise ShotError("unknown shot preset")
    return p


def caption(p: dict, character_name: str) -> str:
    """A caption for dataset tools: the preset's prompt, naming the character without any real person."""
    return f"{character_name}, {p['prompt']}"
