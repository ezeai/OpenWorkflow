"""The graph shown on the canvas: a workflow's nodes and links with every subgraph opened in place.

A subgraph node is a box that holds its own nodes. Opening it puts those nodes on the canvas and joins the links that
crossed the box edge straight to them, so the picture matches what runs. Subgraphs inside subgraphs open too. The box
itself is not drawn. Read only.

Settings are named from ComfyUI's own node definitions when they are known: the exact widget names, types and limits,
in the order ComfyUI stores them. A node ComfyUI does not know is shown with the names its saved file gives.
"""
from __future__ import annotations

import json

from .object_info import InputSpec, ObjectInfo
from .workflow import UI_ONLY_TYPES

INPUT_NODE, OUTPUT_NODE = "-10", "-20"   # ComfyUI's ids for the edges of a subgraph, as strings like every other id here
MODES = {0: "active", 2: "muted", 4: "bypassed"}
SHOWN = 300          # longest value sent for one setting; a prompt is cut here, the full text stays in the editor
MAX_OPEN = 200       # bound on how many subgraph boxes are opened, so a broken file cannot loop


def _links(raw) -> list[dict]:
    """Links are stored either as lists [id, from, from_slot, to, to_slot, type] or as dicts, depending on the editor."""
    out = []
    for lk in raw or []:
        if isinstance(lk, list) and len(lk) >= 6:
            out.append({"id": lk[0], "from": lk[1], "from_slot": lk[2], "to": lk[3], "to_slot": lk[4], "type": lk[5]})
        elif isinstance(lk, dict):
            out.append({"id": lk.get("id"), "from": lk.get("origin_id"), "from_slot": lk.get("origin_slot"),
                        "to": lk.get("target_id"), "to_slot": lk.get("target_slot"), "type": lk.get("type")})
    return [{**x, "from": str(x["from"]), "to": str(x["to"])} for x in out if x["from"] is not None and x["to"] is not None]


def _value(v):
    if isinstance(v, bool) or v is None or isinstance(v, (int, float)):
        return v
    text = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    return text if len(text) <= SHOWN else text[:SHOWN] + "…"


def _limits(spec: InputSpec) -> dict:
    """The limits ComfyUI states for a setting. Only the ones it states are sent."""
    out = {}
    for key in ("min", "max", "step", "default"):
        if key in spec.options:
            out[key] = _value(spec.options[key])
    if spec.choices:
        out["choices"] = [_value(c) for c in spec.choices]
    return out


def _exact(n: dict, nd) -> list[dict]:
    """Settings in ComfyUI's own order. A widget that stores an extra control value (the seed's 'randomize') takes
    the next stored value, which is shown as '<name> control'."""
    vals = n.get("widgets_values")
    specs = nd.widgets()
    out = []
    if isinstance(vals, dict):
        for spec in specs:
            if spec.name in vals:
                out.append({"name": spec.name, "type": spec.type, "value": _value(vals[spec.name]), **_limits(spec)})
        return out
    if not isinstance(vals, list):
        # Nothing was saved: show the defaults ComfyUI would use.
        return [{"name": s.name, "type": s.type, "value": _value(s.options.get("default")), **_limits(s)} for s in specs]
    idx = 0
    for spec in specs:
        if idx >= len(vals):
            break
        out.append({"name": spec.name, "type": spec.type, "value": _value(vals[idx]), **_limits(spec)})
        idx += 1
        for _ in range(spec.extra_widget_values):
            if idx < len(vals):
                out.append({"name": f"{spec.name} control", "type": "COMBO", "value": _value(vals[idx])})
            idx += 1
    return out


def _guessed(n: dict) -> list[dict]:
    """Settings for a node ComfyUI does not know: the names its saved file gives, or 'value N' when it gives none."""
    vals = n.get("widgets_values")
    if isinstance(vals, dict):
        pairs = list(vals.items())
    elif isinstance(vals, list):
        names = [i["widget"]["name"] for i in n.get("inputs") or []
                 if isinstance(i, dict) and isinstance(i.get("widget"), dict) and i["widget"].get("name")]
        pairs = [(names[i] if i < len(names) else f"value {i + 1}", v) for i, v in enumerate(vals)]
    else:
        return []
    return [{"name": str(k), "value": _value(v)} for k, v in pairs]


def _slots(items) -> list[dict]:
    """The node's sockets in the order the editor numbers them: a link's slot number is its position in this list.
    A socket that stands for a widget is marked, so the canvas can show it as a setting."""
    out = []
    for s in items or []:
        if isinstance(s, dict):
            out.append({"name": str(s.get("localized_name") or s.get("label") or s.get("name") or "")[:60],
                        "type": str(s.get("type") or "*")[:40], "widget": bool(s.get("widget"))})
    return out


def _node(raw: dict, node_id: str, group: str | None, info: ObjectInfo | None, scope: str | None = None) -> dict:
    kind = raw.get("type") or "?"
    nd = info.get(kind) if info is not None else None
    return {"id": node_id, "type": kind, "title": raw.get("title") or kind,
            "state": MODES.get(raw.get("mode", 0), f"mode {raw.get('mode')}"),
            "ui_only": kind in UI_ONLY_TYPES, "group": group, "scope": scope, "known": nd is not None,
            "params": _exact(raw, nd) if nd is not None else _guessed(raw),
            "inputs": _slots(raw.get("inputs")), "outputs": _slots(raw.get("outputs")),
            "pos": None, "size": None}


def flatten(data: dict, info: ObjectInfo | None = None) -> dict:
    """{nodes, links, subgraphs}: every node and link in the workflow, subgraphs opened (nested ones too).
    Ids inside a subgraph are prefixed with the box's id, so they stay unique."""
    defs = {s["id"]: s for s in (data.get("definitions") or {}).get("subgraphs", []) if isinstance(s, dict) and s.get("id")}
    nodes = [{"id": str(n.get("id")), "raw": n, "group": None, "scope": None} for n in data.get("nodes", []) if isinstance(n, dict)]
    links = _links(data.get("links"))
    opened: dict[str, int] = {}
    boxes = []

    for _ in range(MAX_OPEN):
        box = next((x for x in nodes if x["raw"].get("type") in defs), None)
        if box is None:
            break
        pid = box["id"]
        sg = defs[box["raw"]["type"]]
        name = sg.get("name") or "subgraph"
        label = f"{box['group']} › {name}" if box["group"] else name
        opened[sg["id"]] = opened.get(sg["id"], 0) + 1
        # The box's own nodes belong to it; its scope is the box's id. Its parent is the box it sat in, if any.
        boxes.append({"scope": pid, "parent": box["scope"], "name": name, "label": label})
        prefix = f"{pid}:"
        inner_links = _links(sg.get("links"))

        entries: dict[int, list[tuple[str, int]]] = {}
        exits: dict[int, tuple[str, int]] = {}
        for il in inner_links:
            if il["from"] == INPUT_NODE:
                entries.setdefault(il["from_slot"], []).append((prefix + il["to"], il["to_slot"]))
            elif il["to"] == OUTPUT_NODE:
                exits[il["to_slot"]] = (prefix + il["from"], il["from_slot"])
            else:
                links.append({**il, "id": f"{prefix}{il['id']}", "from": prefix + il["from"], "to": prefix + il["to"]})

        nodes = [x for x in nodes if x is not box] + [
            {"id": prefix + str(n.get("id")), "raw": n, "group": label, "scope": pid}
            for n in sg.get("nodes", []) if isinstance(n, dict)]

        rewired = []
        for l in links:
            if l["to"] == pid:
                for target, tslot in entries.get(l["to_slot"], []):
                    rewired.append({**l, "id": f"{prefix}in{l['id']}-{target}", "to": target, "to_slot": tslot})
            elif l["from"] == pid:
                if l["from_slot"] in exits:
                    origin, oslot = exits[l["from_slot"]]
                    rewired.append({**l, "id": f"{prefix}out{l['id']}", "from": origin, "from_slot": oslot})
            else:
                rewired.append(l)
        links = rewired

    defs_out = []
    for sid, count in opened.items():
        sg = defs[sid]
        defs_out.append({"id": sid, "name": sg.get("name") or "subgraph", "opened": count,
                         "nodes": len([n for n in sg.get("nodes", []) if isinstance(n, dict)]),
                         "inputs": [str(p.get("name", "")) for p in sg.get("inputs") or [] if isinstance(p, dict)],
                         "outputs": [str(p.get("name", "")) for p in sg.get("outputs") or [] if isinstance(p, dict)]})
    return {"nodes": [_node(x["raw"], x["id"], x["group"], info, x["scope"]) for x in nodes], "links": links,
            "subgraphs": defs_out, "boxes": boxes}
