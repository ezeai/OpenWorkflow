"""Creator path for AI Influencer: an idea (and optionally a reference photo) becomes a portrait prompt, and the
Krea-2 creator workflow renders it. Nothing here trains anything.

The reference photo is shown to the local model so it can describe the look (hair, skin, build, clothing). That
matches a look, not a face: Krea-2 has no identity input, so the output is a new person with a similar description.
"""
from __future__ import annotations

import copy

MAX_IDEA = 600
MAX_PROMPT = 1200
RESOLUTION_NODE = "49"   # ResolutionSelector: its megapixels set width and height
ENCODER_NODE = "30:6"     # CLIPTextEncode that feeds the sampler in the Krea-2 creator workflow
SEED_NODE = "30:3"        # KSampler seed


class CreatorError(ValueError):
    """The request cannot be done. The message is safe to show."""


SYSTEM = (
    "You write one text-to-image prompt for a photorealistic portrait of an adult woman. "
    "Reply with the prompt only: one paragraph of 40 to 90 words, describing age, hair, skin tone, build, "
    "clothing, pose, setting and light. Do not name any real person. Do not add any explanation."
)
REFERENCE_NOTE = (
    "A reference photo is attached. Describe its visible look (hair, skin tone, build, clothing, expression) "
    "and use that look in the prompt. Describe the look only; do not name anyone."
)


def writer_messages(idea: str, reference_b64: str | None) -> list[dict]:
    idea = (idea or "").strip()
    if not idea and not reference_b64:
        raise CreatorError("describe the character or add a reference photo")
    if len(idea) > MAX_IDEA:
        raise CreatorError(f"the idea is longer than {MAX_IDEA} characters")
    user = {"role": "user", "content": (f"Idea: {idea}" if idea else "No idea text: use the reference photo.")}
    if reference_b64:
        user["content"] = REFERENCE_NOTE + "\n" + user["content"]
        user["images"] = [reference_b64]
    return [{"role": "system", "content": SYSTEM}, user]


def clean_prompt(text: str) -> str:
    out = " ".join((text or "").split())
    if not out:
        raise CreatorError("the model returned an empty prompt; try again or choose another model")
    return out[:MAX_PROMPT]


def krea_graph(api_prompt: dict, text: str, seed: int, megapixels: float = 1.0) -> dict:
    """The workflow's own graph with this prompt and seed. The encoder's text link is replaced by the literal text."""
    if ENCODER_NODE not in api_prompt or SEED_NODE not in api_prompt:
        raise CreatorError("this workflow is not the Krea-2 creator graph")
    g = copy.deepcopy(api_prompt)
    g[ENCODER_NODE]["inputs"]["text"] = clean_prompt(text)
    g[SEED_NODE]["inputs"]["seed"] = int(seed)
    if RESOLUTION_NODE in g:
        g[RESOLUTION_NODE]["inputs"]["megapixels"] = float(megapixels)
    for node in g.values():
        node.pop("_meta", None)
    return g
