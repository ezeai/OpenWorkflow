"""Canonical description of an imported workflow: what it is, what it contains, and what is wrong with it.

Read only. It takes the exact stored bytes and the store record, and returns a plain dict. Nothing here
changes the source file, and nothing here decides how it runs. Analysis and execution still use
workflow.py and convert.py; this module is the shared summary that the workflow list and the App
Builder can read without re-parsing the graph themselves.
"""
from __future__ import annotations

import hashlib
import json

from . import workflow

# Top-level keys ComfyUI's editor format defines. Any other key is kept in the source file but not modelled here.
KNOWN_UI_KEYS = {"id", "revision", "last_node_id", "last_link_id", "nodes", "links", "groups", "config",
                 "extra", "version", "definitions", "state"}
STATE_LABEL = {workflow.MODE_ALWAYS: "active", workflow.MODE_NEVER: "muted", workflow.MODE_BYPASS: "bypassed"}


def _node_row(scope_label: str, n: workflow.Node) -> dict:
    return {
        "scope": scope_label,
        "id": n.id,
        "class_type": n.type,
        "title": n.title or n.type,
        "state": STATE_LABEL.get(n.mode, f"mode {n.mode}"),
        "ui_only": n.type in workflow.UI_ONLY_TYPES,
        "input_links": sum(1 for s in n.inputs if s.link is not None),
        "output_links": sum(len(o.links) for o in n.outputs),
    }


def describe(data: bytes, record: dict) -> dict:
    """Summarise one stored workflow. `data` is the exact source bytes; `record` is its store record."""
    digest = hashlib.sha256(data).hexdigest()
    out = {
        "id": record["id"],
        "name": record["name"],
        "origin": record.get("origin"),
        "imported_at": record.get("imported_at"),
        "source": {"sha256": digest, "matches_record": digest == record.get("sha256"), "size": len(data), "format": None},
        "validation": {"state": "valid", "problems": []},
        "nodes": [],
        "counts": {"nodes": 0, "links": 0, "subgraphs": 0, "muted": 0, "bypassed": 0, "ui_only": 0},
        "editor_metadata": {"version": None, "has_app_builder_selection": False, "preserved_top_level_keys": []},
    }
    try:
        parsed = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        out["validation"] = {"state": "invalid", "problems": ["the stored source is not valid JSON"]}
        return out

    fmt = workflow.detect_format(parsed)
    out["source"]["format"] = fmt
    if fmt == "unsupported":
        out["validation"] = {"state": "invalid", "problems": ["not a ComfyUI editor workflow or API prompt"]}
        return out

    problems: list[str] = []
    if fmt == "ui":
        try:
            wf = workflow.parse_ui(parsed)
        except workflow.WorkflowError as e:
            out["validation"] = {"state": "invalid", "problems": [str(e)]}
            return out
        problems.extend(wf.problems)
        scopes = [("graph", wf.root)] + [(f"subgraph {sc.name}", sc) for sc in wf.subgraphs.values()]
        out["counts"]["subgraphs"] = len(wf.subgraphs)
        out["editor_metadata"]["version"] = wf.version
        out["editor_metadata"]["has_app_builder_selection"] = wf.linear_data is not None
        out["editor_metadata"]["preserved_top_level_keys"] = sorted(k for k in parsed if k not in KNOWN_UI_KEYS)
    else:
        try:
            prompt = workflow.parse_api(parsed)
        except workflow.WorkflowError as e:
            out["validation"] = {"state": "invalid", "problems": [str(e)]}
            return out
        scopes = [("graph", workflow.Scope(
            {nid: workflow.Node(nid, str(n["class_type"]), workflow.MODE_ALWAYS, [], [], None,
                                (n.get("_meta") or {}).get("title"))
             for nid, n in prompt.items()}, {}))]

    for label, sc in scopes:
        out["counts"]["links"] += len(sc.links)
        for n in sc.nodes.values():
            row = _node_row(label, n)
            out["nodes"].append(row)
            out["counts"]["nodes"] += 1
            if row["state"] == "muted":
                out["counts"]["muted"] += 1
            elif row["state"] == "bypassed":
                out["counts"]["bypassed"] += 1
            if row["ui_only"]:
                out["counts"]["ui_only"] += 1

    out["validation"] = {"state": "invalid" if problems else "valid", "problems": problems}
    return out
