"""Nodes added on the canvas, kept beside the workflow in graph_edits.json.

The imported file (source.json) is never changed: its checksum is what proves it is the file the user gave. An added
node has no links, so the stored workflow runs exactly as before. The canvas shows these nodes; wiring them into the
run is a separate step.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..projects.store import _atomic_write
from .object_info import NodeDef

MAX_ADDED = 50
MAX_POS = 100_000


class EditError(ValueError):
    """The change cannot be made. The message is safe to show."""


OPS = {"state", "title", "delete"}
STATES = {"active", "muted", "bypassed"}
MAX_OPS = 500


def _read(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(path: Path, added: list[dict], ops: list[dict]) -> None:
    _atomic_write(path, json.dumps({"added": added, "ops": ops}, indent=2), keep_backup=True)


def load(path: Path) -> list[dict]:
    added = _read(path).get("added", [])
    return added if isinstance(added, list) else []


def load_ops(path: Path) -> list[dict]:
    ops = _read(path).get("ops", [])
    return ops if isinstance(ops, list) else []


def add_op(path: Path, op: dict, known: set[str]) -> dict:
    """Records one change to a node. It is applied on top of the workflow, which is never changed."""
    kind = op.get("op")
    if kind not in OPS:
        raise EditError("unknown change")
    node = op.get("node")
    if not isinstance(node, str) or node not in known:
        raise EditError("that node is not on the canvas")
    clean = {"op": kind, "node": node}
    if kind == "state":
        if op.get("state") not in STATES:
            raise EditError("the state must be active, muted or bypassed")
        clean["state"] = op["state"]
    if kind == "title":
        title = op.get("title")
        if not isinstance(title, str) or not title.strip():
            raise EditError("the title cannot be empty")
        clean["title"] = title.strip()[:80]
    ops = load_ops(path)
    if len(ops) >= MAX_OPS:
        raise EditError(f"at most {MAX_OPS} changes per workflow")
    _write(path, load(path), ops + [clean])
    return clean


def undo(path: Path) -> dict | None:
    ops = load_ops(path)
    if not ops:
        return None
    _write(path, load(path), ops[:-1])
    return ops[-1]


def apply_ops(nodes: list[dict], links: list[dict], ops: list[dict]) -> tuple[list[dict], list[dict]]:
    """The ops in the order they were made. A deleted node takes its links with it."""
    for op in ops:
        if op["op"] == "state":
            for n in nodes:
                if n["id"] == op["node"]:
                    n["state"] = op["state"]
        elif op["op"] == "title":
            for n in nodes:
                if n["id"] == op["node"]:
                    n["title"] = op["title"]
        elif op["op"] == "delete":
            nodes = [n for n in nodes if n["id"] != op["node"]]
            links = [lk for lk in links if op["node"] not in (lk["from"], lk["to"])]
    return nodes, links


def default_params(nd: NodeDef) -> list[dict]:
    """The node's settings at ComfyUI's own defaults: the first choice for a list, the stated default otherwise."""
    out = []
    for i in nd.widgets():
        if i.type == "COMBO":
            value = i.options.get("default", i.choices[0] if i.choices else "")
        else:
            value = i.options.get("default", {"INT": 0, "FLOAT": 0.0, "BOOLEAN": False}.get(i.type, ""))
        out.append({"name": i.name, "value": value})
    return out


def add(path: Path, nd: NodeDef, pos: list[float], title: str | None = None) -> dict:
    added = load(path)
    if len(added) >= MAX_ADDED:
        raise EditError(f"at most {MAX_ADDED} added nodes per workflow")
    if not all(isinstance(v, (int, float)) and abs(v) <= MAX_POS for v in pos) or len(pos) != 2:
        raise EditError("the position must be two numbers")
    node = {"id": f"+{len(added) + 1}", "type": nd.name, "title": (title or nd.name)[:80], "state": "active",
            "ui_only": False, "group": None, "params": default_params(nd), "pos": [float(pos[0]), float(pos[1])],
            "size": None, "added": True}
    _write(path, added + [node], load_ops(path))
    return node
