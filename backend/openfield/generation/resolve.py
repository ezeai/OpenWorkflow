"""Resolve a workflow's model files to the names ComfyUI expects, from discovery results.

ComfyUI addresses a model by its path inside its folder type, e.g. 'z-Image-Turbo\file.safetensors'
inside 'diffusion_models'. The name is derived from where the file was found. Nothing is guessed.
"""
from __future__ import annotations

from pathlib import Path

from .jobs import Resolved
from .zimage import WORKFLOW_ID as ZIMAGE

WORKFLOW_MODELS = {ZIMAGE: ("z-image-turbo", "bf16-publisher")}
ROLE_GRAPH_ARG = {"diffusion": "unet", "text_encoder": "text_encoder", "vae": "vae"}


def comfy_name(path: str, folders: list[str]) -> str:
    parts = Path(path).parts
    lower = [p.lower() for p in parts]
    for i, p in enumerate(lower):
        if p in [f.lower() for f in folders]:
            rel = parts[i + 1:]
            if rel:
                return str(Path(*rel))
    raise ValueError(f"{Path(path).name} is not inside an expected ComfyUI folder")


def resolve(services, workflow_id: str) -> Resolved:
    if workflow_id not in WORKFLOW_MODELS:
        raise ValueError("no generator is connected for this workflow yet")
    model_id, variant_id = WORKFLOW_MODELS[workflow_id]
    models, _, _ = services.model_statuses()
    m = next(x for x in models if x["id"] == model_id)
    v = next(x for x in m["variants"] if x["id"] == variant_id)
    catalog = services.catalog.models[model_id]
    cat_variant = next(x for x in catalog.variants if x.id == variant_id)
    folders_by_role = {c.role: c.folders for c in cat_variant.components}

    graph_args: dict = {}
    provenance: list[dict] = []
    missing = []
    for comp in v["components"]:
        role = comp["role"]
        if comp["state"] == "missing":
            missing.append(role)
            continue
        f = next((x for x in comp["files"] if x["path"] == comp["confirmed_path"]), None) or comp["files"][0]
        if f["ownership"] == "managed" and not f["header_ok"]:
            missing.append(role)
            continue
        graph_args[ROLE_GRAPH_ARG[role]] = comfy_name(f["path"], folders_by_role[role])
        provenance.append({"role": role, "name": f["name"], "path": f["path"], "size": f["size"],
                           "comfy_name": graph_args[ROLE_GRAPH_ARG[role]], "ownership": f["ownership"],
                           "identity_confirmed": comp["state"] == "identified",
                           "license_status": comp["license_status"]})
    if missing:
        raise ValueError(f"missing model file(s) for {', '.join(missing)}. See the Model Library.")
    return Resolved(graph_args=graph_args, models=provenance)
