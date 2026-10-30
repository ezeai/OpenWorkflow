"""Custom-node dependency analysis: which node classes a workflow needs, which ComfyUI has, and candidate packs.

Evidence, strongest first:
- registered: the class is in the node list of a running ComfyUI (live /object_info). Only this means "available".
- snapshot core: the class is in the bundled or cached core list. It is core, so it is available.
- candidate packs: ComfyUI Manager's extension-node-map.json, which lists the classes each repository provides.
  This is a candidate, never proof that a pack is installed or working.

Nothing here installs, downloads, or runs anything. A class with no registered status is never called
"installed". A folder in custom_nodes is not matched to a pack by name, because folder names do not reliably
match repositories.
"""
from __future__ import annotations

import json
from pathlib import Path

from .object_info import ObjectInfo
from .workflow import MODE_BYPASS, MODE_NEVER, UIWorkflow

UI_ONLY = {"Note", "MarkdownNote", "Reroute", "PrimitiveNode"}
MANAGER_MAP = Path("ComfyUI") / "custom_nodes" / "comfyui-manager" / "extension-node-map.json"


def required_nodes(wf: UIWorkflow | None, prompt: dict | None) -> list[dict]:
    """Every node class a run would use. Bypassed and muted nodes do not run; subgraph wrappers are expanded."""
    out: list[dict] = []
    if wf is None:
        for eid, n in (prompt or {}).items():
            out.append({"class": n["class_type"], "node": eid, "title": (n.get("_meta") or {}).get("title"), "scope": "graph"})
        return out

    def visit(scope, prefix: str, depth: int) -> None:
        if depth > 64:
            return
        for n in scope.nodes.values():
            if n.mode in (MODE_NEVER, MODE_BYPASS) or n.type in UI_ONLY:
                continue
            if n.type in wf.subgraphs:
                visit(wf.subgraphs[n.type], f"{prefix}{n.id}:", depth + 1)
                continue
            out.append({"class": n.type, "node": prefix + n.id, "title": n.title, "scope": scope.name or "graph"})

    visit(wf.root, "", 0)
    return out


_CACHE: dict[str, tuple[float, dict]] = {}


def load_pack_index(comfy_root: str | None) -> dict | None:
    """Class name -> candidate packs, from Manager's mapping file. None if the file cannot be read.

    Parsed once per file version: the file has about 4,000 repositories and is read on every analysis otherwise.
    """
    if not comfy_root:
        return None
    path = Path(comfy_root) / MANAGER_MAP
    try:
        stamp = path.stat().st_mtime
    except OSError:
        return None
    hit = _CACHE.get(str(path))
    if hit and hit[0] == stamp:
        return hit[1]
    index = _parse_index(path)
    if index is not None:
        _CACHE[str(path)] = (stamp, index)
    return index


def _parse_index(path: Path) -> dict | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    index: dict[str, list[dict]] = {}
    for repo, entry in raw.items():
        if not (isinstance(entry, list) and len(entry) == 2 and isinstance(entry[0], list)):
            continue
        meta = entry[1] if isinstance(entry[1], dict) else {}
        title = meta.get("title_aux") if isinstance(meta.get("title_aux"), str) else None
        for cls in entry[0]:
            if isinstance(cls, str):
                index.setdefault(cls, []).append({"repository": repo, "title": title})
    return index


def build(required: list[dict], info: ObjectInfo, pack_index: dict | None, custom_nodes_folders: int | None) -> dict:
    by_class: dict[str, list[dict]] = {}
    for r in required:
        by_class.setdefault(r["class"], []).append(r)
    items = []
    for cls in sorted(by_class):
        nodes = by_class[cls]
        if info.get(cls) is not None:
            available, source = True, ("runtime" if info.source == "live" else "snapshot")
        elif info.source == "live":
            available, source = False, "runtime"
        else:
            available, source = None, "unchecked"
        if available is True and source == "runtime":
            confidence, status = "registered", "registered"
            candidates = []
        elif available is True:
            confidence, status = "core", "core"
            candidates = []
        else:
            candidates = [] if pack_index is None else [dict(c, match="exact class name", source="manager mapping") for c in pack_index.get(cls, [])]
            if available is None:
                confidence, status = "unchecked", "unchecked"
            elif not candidates:
                confidence, status = "unknown", "missing"
            elif len(candidates) == 1:
                confidence, status = "likely", "candidate"
            else:
                confidence, status = "ambiguous", "choose"
        items.append({
            "class": cls, "nodes": [n["node"] for n in nodes], "titles": sorted({n["title"] for n in nodes if n.get("title")}),
            "available": available, "availability_source": source, "status": status, "confidence": confidence,
            "candidates": candidates[:10], "candidate_count": len(candidates),
            "installed": None,          # never claimed here: only a runtime check after installation can say so
            "verification": "registered in the running ComfyUI" if status == "registered" else "not verified",
        })
    counts = {s: sum(1 for i in items if i["status"] == s) for s in ("registered", "core", "unchecked", "missing", "candidate", "choose")}
    if info.source != "live":
        headline = "ComfyUI is not running, so custom nodes cannot be checked. Start ComfyUI to check them."
    elif counts["missing"] or counts["choose"] or counts["candidate"]:
        headline = "Some node classes are not registered in ComfyUI."
    else:
        headline = "Every node class this workflow needs is registered in ComfyUI."
    return {
        "headline": headline,
        "counts": counts,
        "requirements": items,
        "node_classes_needed": len(items),
        "node_instances_needed": len(required),
        "pack_index": ("Manager mapping: " + str(sum(len(v) for v in pack_index.values())) + " class entries") if pack_index is not None else "ComfyUI Manager's mapping file was not found",
        "custom_nodes_folders": custom_nodes_folders,
        "availability_source": info.source,
        "notes": [
            "Candidate packs come from ComfyUI Manager's mapping. A candidate is not installed, and its code has not run.",
            "Nothing is installed by Forge yet. Installing custom-node code will need your approval.",
        ],
    }
