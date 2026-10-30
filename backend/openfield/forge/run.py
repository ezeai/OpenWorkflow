"""Turn a saved Forge app and the user's values into a prompt ComfyUI can run.

Nothing runs unless every control is verified and every model file is installed. The prompt is the
converted workflow with only the values the app's controls bind to changed. Model names are the ones
the analysis found on disk, never names typed by the user.
"""
from __future__ import annotations

import copy

from . import uispec


class RunRefused(Exception):
    """The app cannot run as it is. The message says why, in words the user can act on."""


def prepare(spec: uispec.UISpec, validation: dict, analysis: dict, api_prompt: dict, values: dict) -> tuple[dict, list[dict], list[str]]:
    """Returns (prompt, models, notes). Raises RunRefused or uispec.ApplyError."""
    if analysis["readiness"] == "blocked":
        raise RunRefused("The workflow is blocked: " + "; ".join(analysis["reasons"][:3]))
    if validation["status"] != "valid":
        bad = [r for r in validation["components"] if r["status"] != "verified"]
        detail = "; ".join(f"{r['component']}: {r['reasons'][0] if r['reasons'] else r['status']}" for r in bad[:3])
        raise RunRefused("Every control must be verified before the app can run. " + (detail or "; ".join(validation["problems"][:3])))
    prompt = copy.deepcopy(api_prompt)
    models: list[dict] = []
    for m in analysis["model_files"]:
        if m["status"] == "available":
            chosen = m["file"]
        elif m["status"] == "found_elsewhere" and m["comfy_name"]:
            chosen = m["comfy_name"]
        elif m["status"] == "unknown":
            raise RunRefused(f"Model file '{m['file']}' could not be checked. Start ComfyUI so Forge can see its model list.")
        elif m["status"] == "ambiguous":
            raise RunRefused(f"Model file '{m['file']}' matches more than one installed file. Choose one in the workflow first.")
        else:
            raise RunRefused(f"Model file '{m['file']}' is not installed in ComfyUI.")
        prompt[m["node"]]["inputs"][m["input"]] = chosen
        models.append({"role": m["input"], "node": m["node"], "file": m["file"], "comfy_name": chosen, "status": m["status"]})
    prompt = uispec.apply_values(spec, validation, analysis, prompt, values)
    for node in prompt.values():
        node.pop("_meta", None)
    notes = []
    if analysis["node_definitions"]["source"] != "live":
        notes.append("ComfyUI was not running when this app was prepared, so its node list came from a saved copy.")
    return prompt, models, notes
