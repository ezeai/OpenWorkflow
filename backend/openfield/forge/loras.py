"""A LoRA stack for a Forge app: extra LoRA loader nodes placed between the workflow's model (and clip) and everything that uses them.

The stack is stored beside the app, not in the workflow file, so the imported workflow is never changed. At run time
the loaders are added to a copy of the prompt. Every LoRA is checked against ComfyUI's own list of LoRA files, and
strengths are limited, so a stack cannot be sent with a file ComfyUI does not have.
"""
from __future__ import annotations

import json
from pathlib import Path

MAX_LORAS = 8
MIN_STRENGTH, MAX_STRENGTH = -2.0, 2.0
MODEL_LOADER = "LoraLoaderModelOnly"
FULL_LOADER = "LoraLoader"


class LoraError(ValueError):
    """The stack cannot be used as it is. The message is safe to show."""


def clean(items: list[dict], known: set[str] | None) -> list[dict]:
    """Check each entry. `known` is ComfyUI's LoRA list; None means ComfyUI is not reachable, so names are not checked."""
    if not isinstance(items, list):
        raise LoraError("the LoRA stack must be a list")
    if len(items) > MAX_LORAS:
        raise LoraError(f"at most {MAX_LORAS} LoRAs in one stack")
    out = []
    for i, it in enumerate(items, start=1):
        if not isinstance(it, dict) or not isinstance(it.get("name"), str) or not it["name"].strip():
            raise LoraError(f"LoRA {i} needs a file name")
        name = it["name"].strip()
        if known is not None and name not in known:
            raise LoraError(f"LoRA {i} ('{name}') is not in ComfyUI's LoRA folder")
        try:
            strength = float(it.get("strength", 1.0))
        except (TypeError, ValueError):
            raise LoraError(f"LoRA {i} needs a numeric strength")
        if not (MIN_STRENGTH <= strength <= MAX_STRENGTH):
            raise LoraError(f"LoRA {i} strength must be between {MIN_STRENGTH:g} and {MAX_STRENGTH:g}")
        out.append({"name": name, "strength": round(strength, 3), "enabled": bool(it.get("enabled", True))})
    return out


class Store:
    """<home>/forge/workflows/<id>/loras.json: the stack for one app. Written whole, replaced atomically."""

    def __init__(self, root: Path):
        self.root = root

    def load(self, wid: str) -> list[dict]:
        p = self.root / wid / "loras.json"
        if not p.is_file():
            return []
        try:
            return json.loads(p.read_text(encoding="utf-8")).get("items", [])
        except (OSError, ValueError, AttributeError):
            return []

    def save(self, wid: str, items: list[dict]) -> list[dict]:
        d = self.root / wid
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / "loras.json.tmp"
        tmp.write_text(json.dumps({"items": items}, indent=2), encoding="utf-8")
        tmp.replace(d / "loras.json")
        return items


def apply(prompt: dict, items: list[dict]) -> tuple[dict, list[dict]]:
    """A copy of the prompt with the enabled LoRAs chained after the model source, in list order.

    Returns (prompt, applied). Every node whose "model" input used the old source now uses the last LoRA; the same
    goes for "clip" when the stack has a clip-capable loader. Nothing else is touched.
    """
    import copy

    active = [it for it in items if it.get("enabled", True)]
    if not active:
        return prompt, []
    out = copy.deepcopy(prompt)

    def roots(key: str) -> list[tuple]:
        """The loaders that produce the model or clip: nodes that take no model/clip input of their own.
        The LoRA goes right after them, once, however many nodes use them (a shift node, say, sits after the UNET)."""
        used = {tuple(n["inputs"][key]) for n in out.values()
                if isinstance(n.get("inputs"), dict) and isinstance(n["inputs"].get(key), list) and len(n["inputs"][key]) == 2}
        return sorted(src for src in used if not (isinstance(out.get(src[0], {}).get("inputs"), dict) and key in out[src[0]]["inputs"]))

    model_sources = roots("model")
    clip_sources = roots("clip")
    if not model_sources:
        raise LoraError("this workflow has no model input to attach a LoRA to")

    replace_model: dict[tuple, tuple] = {}
    replace_clip: dict[tuple, tuple] = {}
    for si, src in enumerate(model_sources):
        last = list(src)
        for i, it in enumerate(active):
            nid = f"forge_lora_m{si}_{i}"
            out[nid] = {"class_type": MODEL_LOADER, "inputs": {"model": last, "lora_name": it["name"], "strength_model": it["strength"]}}
            last = [nid, 0]
        replace_model[src] = tuple(last)
    for si, src in enumerate(clip_sources):
        last = list(src)
        for i, it in enumerate(active):
            nid = f"forge_lora_c{si}_{i}"
            out[nid] = {"class_type": FULL_LOADER, "inputs": {"model": list(replace_model.get(model_sources[0], model_sources[0])),
                                                             "clip": last, "lora_name": it["name"],
                                                             "strength_model": it["strength"], "strength_clip": it["strength"]}}
            last = [nid, 1]
        replace_clip[src] = tuple(last)

    for nid, node in out.items():
        if nid.startswith("forge_lora_"):
            continue
        ins = node.get("inputs")
        if not isinstance(ins, dict):
            continue
        for key, mapping in (("model", replace_model), ("clip", replace_clip)):
            v = ins.get(key)
            if isinstance(v, list) and len(v) == 2 and tuple(v) in mapping:
                ins[key] = list(mapping[tuple(v)])
    # the clip loaders take their model from the model chain, which must not feed itself
    for nid, node in out.items():
        if nid.startswith("forge_lora_c"):
            src_model = node["inputs"]["model"]
            node["inputs"]["model"] = list(replace_model.get(tuple(src_model), tuple(src_model)))
    return out, active
