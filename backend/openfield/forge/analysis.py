"""Deterministic analysis of a converted workflow: controls, outputs, model files and readiness.

Facts come from the file and the node definitions (types, limits, choices, connections). Labels such as
"this is the positive prompt" are inferences; each one names the rule that produced it, so the UI can
show which is which. No language model is involved here.
"""
from __future__ import annotations

import ntpath
import re

from . import deps
from .convert import Conversion
from .object_info import ObjectInfo
from .workflow import UIWorkflow

_FILE = re.compile(r"\.(safetensors|ckpt|pt|pth|bin|gguf|sft|onnx)$", re.I)
_SAMPLER_INPUT_NEG = {"negative"}
_SAMPLER_INPUT_POS = {"positive"}
IMAGE_OUT = {"SaveImage", "PreviewImage", "SaveImageAdvanced", "SaveAnimatedWEBP", "SaveAnimatedPNG"}
VIDEO_OUT = {"SaveVideo", "SaveWEBM", "CreateVideo"}
AUDIO_OUT = {"SaveAudio", "SaveAudioMP3", "SaveAudioOpus", "PreviewAudio"}
# Inputs a user rarely needs; shown under "advanced" by default.
ADVANCED = {"sampler_name", "scheduler", "denoise", "cfg", "steps", "shift", "weight_dtype", "device", "batch_size", "type"}
HIDDEN = {"filename_prefix"}


def _consumers(prompt: dict) -> dict[str, list[tuple[str, str]]]:
    """exec_id -> [(consumer exec_id, consumer input name)]"""
    out: dict[str, list[tuple[str, str]]] = {}
    for eid, n in prompt.items():
        for name, v in n["inputs"].items():
            if isinstance(v, list) and len(v) == 2 and isinstance(v[0], str):
                out.setdefault(v[0], []).append((eid, name))
    return out


def _prompt_polarity(eid: str, prompt: dict, consumers: dict) -> str | None:
    """Follow a text encoder's output downstream to the sampler inputs it reaches.

    Text that reaches any positive input is the prompt, even if a zeroed copy also feeds the negative
    side (as in the Z-Image template). Paths through ConditioningZeroOut carry no text and are skipped.
    """
    seen, found, stack = set(), set(), [(eid, 0)]
    while stack:
        cur, depth = stack.pop()
        if cur in seen or depth > 12:
            continue
        seen.add(cur)
        for cid, name in consumers.get(cur, []):
            if name in _SAMPLER_INPUT_POS:
                found.add("positive")
            elif name in _SAMPLER_INPUT_NEG:
                found.add("negative")
            elif prompt.get(cid, {}).get("class_type") != "ConditioningZeroOut":
                stack.append((cid, depth + 1))
    return "positive" if "positive" in found else "negative" if "negative" in found else None


def _classify(spec, name: str, value, cls: str, eid: str, prompt: dict, consumers: dict) -> tuple[str, str]:
    """Returns (kind, rule). Kind is an inference except for media and model files, which follow from the definition."""
    o = spec.options if spec else {}
    t = spec.type if spec else type(value).__name__
    if o.get("image_upload"):
        return "image_input", "definition: image_upload"
    if o.get("video_upload"):
        return "video_input", "definition: video_upload"
    if o.get("audio_upload"):
        return "audio_input", "definition: audio_upload"
    if t == "COMBO" and isinstance(value, str) and _FILE.search(value):
        return "model_file", "combo value is a model file name"
    if name in HIDDEN:
        return "output_name", "input name"
    if t == "STRING" and (o.get("multiline") or name in {"text", "prompt"}):
        pol = _prompt_polarity(eid, prompt, consumers)
        if pol == "positive":
            return "prompt", "text reaches a sampler's positive input"
        if pol == "negative":
            return "negative_prompt", "text reaches only a sampler's negative input"
        return "prompt", "multiline text input"
    if t == "INT" and (o.get("control_after_generate") or name in {"seed", "noise_seed"}):
        return "seed", "definition: control_after_generate" if o.get("control_after_generate") else "input name"
    if t == "INT" and name in {"width", "height"}:
        return name, "input name"
    if name in {"steps", "cfg", "denoise", "sampler_name", "scheduler", "batch_size", "length", "frames", "fps", "duration"}:
        return name, "input name"
    return "setting", "default"


def analyse(wf: UIWorkflow | None, conv: Conversion, info: ObjectInfo, deps_ctx: dict | None = None) -> dict:
    prompt = conv.prompt
    consumers = _consumers(prompt)
    linear = (wf.linear_data if wf else None) or {}
    selected = {(str(n), str(i)) for n, i in (linear.get("inputs") or []) if isinstance(n, (str, int))}

    def picked(eid: str, name: str) -> bool:
        tail = eid.split(":")[-1]
        return (eid, name) in selected or (tail, name) in selected

    controls, models = [], []
    for src in conv.sources:
        node = prompt.get(src.exec_id)
        if node is None or src.input not in node["inputs"]:
            continue
        value = node["inputs"][src.input]
        if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str):
            continue                                   # replaced by a connection
        d = info.get(node["class_type"])
        spec = None
        if d:
            spec = d.input(src.input.split(".")[0]) if "." not in src.input else None
        kind, rule = _classify(spec, src.input, value, node["class_type"], src.exec_id, prompt, consumers)
        constraints = {}
        if spec:
            for k in ("min", "max", "step", "default", "multiline"):
                if k in spec.options:
                    constraints[k] = spec.options[k]
            if spec.choices is not None and not spec.options.get("file_choices_removed"):
                constraints["choices"] = spec.choices if len(spec.choices) <= 200 else spec.choices[:200]
        entry = {
            "id": f"{src.exec_id}.{src.input}", "node": src.exec_id, "input": src.input, "class_type": node["class_type"],
            "node_title": conv.titles.get(src.exec_id), "scope": src.scope,
            "fact": {"type": spec.type if spec else None, "value": value, "constraints": constraints, "origin": src.origin,
                     "checked_against": info.source if spec else None},
            "inferred": {"kind": kind, "rule": rule},
            "app_builder_selected": picked(src.exec_id, src.input),
        }
        if kind == "model_file":
            models.append(_model_status(entry, spec, info))
            continue
        entry["suggested"] = {"show": kind in {"prompt", "negative_prompt", "seed", "width", "height", "image_input", "video_input", "audio_input"} or entry["app_builder_selected"],
                              "advanced": kind in ADVANCED or src.input in ADVANCED, "hidden": kind == "output_name"}
        controls.append(entry)

    outputs = []
    for eid, n in prompt.items():
        d = info.get(n["class_type"])
        if d and d.output_node:
            cls = n["class_type"]
            media = "comparison" if cls == "ImageCompare" else "image" if cls in IMAGE_OUT else "video" if cls in VIDEO_OUT else "audio" if cls in AUDIO_OUT else "other"
            outputs.append({"node": eid, "class_type": cls, "title": conv.titles.get(eid), "media": media,
                            "app_builder_selected": any(str(o) in (eid, eid.split(":")[-1]) for o in linear.get("outputs") or [])})

    missing_types = sorted({n["class_type"] for n in prompt.values() if info.get(n["class_type"]) is None})
    ui_nodes = [n for sc in ([wf.root, *wf.subgraphs.values()] if wf else []) for n in sc.nodes.values()]
    packs = sorted({(str(n.properties.get("cnr_id")), str(n.properties.get("ver") or "")) for n in ui_nodes
                    if n.type in missing_types and n.properties.get("cnr_id")})
    task = _task(controls, outputs)
    readiness, reasons = _readiness(conv, models, missing_types, info, outputs)
    return {
        "task": task, "readiness": readiness, "reasons": reasons,
        "counts": {"nodes": len(prompt), "controls": len(controls), "outputs": len(outputs), "model_files": len(models),
                   "unknown_node_types": len(missing_types), "problems": len(conv.problems)},
        "controls": controls, "outputs": outputs, "model_files": models,
        "unknown_node_types": missing_types, "custom_node_packs": [{"id": p, "version": v} for p, v in packs],
        "problems": [p.as_dict() for p in conv.problems], "conversion_status": conv.status,
        "node_definitions": info.describe(), "app_builder": bool(linear),
        "dependencies": deps.build(deps.required_nodes(wf, prompt), info, (deps_ctx or {}).get("pack_index"),
                                   (deps_ctx or {}).get("custom_nodes_folders")),
    }


def _model_status(entry: dict, spec, info: ObjectInfo) -> dict:
    value = entry["fact"]["value"]
    choices = spec.choices if spec else None
    if spec is None or spec.options.get("file_choices_removed") or choices is None:
        status, match = "unknown", None
    elif value in choices:
        status, match = "available", value
    else:
        base = ntpath.basename(str(value)).lower()
        alts = [c for c in choices if isinstance(c, str) and ntpath.basename(c).lower() == base]
        status, match = ("found_elsewhere", alts[0]) if len(alts) == 1 else ("ambiguous", None) if alts else ("missing", None)
    return {"id": entry["id"], "node": entry["node"], "input": entry["input"], "class_type": entry["class_type"],
            "file": value, "status": status, "comfy_name": match, "alternatives": [] if status != "ambiguous" else
            [c for c in choices if ntpath.basename(c).lower() == ntpath.basename(str(value)).lower()],
            "checked_against": info.source}


def _task(controls: list[dict], outputs: list[dict]) -> dict:
    kinds = {c["inferred"]["kind"] for c in controls}
    media = {o["media"] for o in outputs}
    src = "image" if "image_input" in kinds else "video" if "video_input" in kinds else "audio" if "audio_input" in kinds else "text"
    out = "video" if "video" in media else "image" if "image" in media else "audio" if "audio" in media else "other"
    return {"family": f"{src}-to-{out}", "rule": "media inputs and output node types", "inferred": True}


def _readiness(conv: Conversion, models: list[dict], missing_types: list[str], info: ObjectInfo, outputs: list[dict]):
    reasons = []
    if missing_types:
        reasons.append(f"{len(missing_types)} node type(s) are not available in ComfyUI: {', '.join(missing_types[:6])}")
    for m in models:
        if m["status"] == "missing":
            reasons.append(f"model file '{m['file']}' is not in ComfyUI's {m['class_type']} list")
        elif m["status"] == "ambiguous":
            reasons.append(f"model file '{m['file']}' matches several installed files; choose one")
    if not outputs:
        reasons.append("no output node")
    errors = [p for p in conv.problems if p.severity == "error"]
    if errors and not reasons:
        reasons.append(f"{len(errors)} conversion error(s)")
    if reasons or errors:
        return "blocked", reasons
    notes = []
    if info.source != "live":
        notes.append("ComfyUI is not running, so node definitions and model files were checked against a saved copy")
    if any(m["status"] == "found_elsewhere" for m in models):
        notes.append("some model files are installed under a different folder; Forge will use the installed name")
    if any(m["status"] == "unknown" for m in models):
        notes.append("model files could not be checked")
    if any(p.severity == "warning" for p in conv.problems):
        notes.append("some values need checking (see problems)")
    return ("needs_attention" if notes else "ready"), notes
