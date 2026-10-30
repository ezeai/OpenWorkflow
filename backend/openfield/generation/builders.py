"""Workflow id -> graph builder. Only workflows listed here can be generated."""
from __future__ import annotations

from . import catalog, zimage


def build_catalog_node(class_type: str, node_info: dict, values: dict, node_id: str = "1") -> dict:
    """One ComfyUI node whose inputs are filled from its /object_info definition. Inputs that link to other nodes
    (images, models) cannot be filled from values, so they are reported by name instead of sent empty."""
    result = catalog.fill(catalog.parse_node(node_info), values)
    if result.unfilled:
        raise ValueError(f"{class_type} needs inputs this request does not provide: {', '.join(result.unfilled)}")
    return {node_id: {"class_type": class_type, "inputs": result.inputs}}


def build_graph(workflow_id: str, positive: str, negative: str, params: dict, args: dict) -> dict:
    if workflow_id == zimage.WORKFLOW_ID:
        return zimage.build(positive, negative, params["width"], params["height"], params["seed"],
                            steps=params.get("steps", 8), cfg=params.get("cfg", 1.0),
                            unet=args["unet"], text_encoder=args["text_encoder"], vae=args["vae"])
    raise ValueError("no graph builder for this workflow")


GENERATABLE = {zimage.WORKFLOW_ID}
