"""Typed node graph (canvas). Ports have types; connections are checked before anything runs.

Only node types with real behaviour exist here. Each node type declares its inputs and outputs.
Running a graph submits the same generation the Director submits, through the same job engine.
"""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from .schema import ProjectMeta, check_id, new_id

PORT_TYPES = ("shot_spec", "character", "location", "asset", "report")

NODE_TYPES: dict[str, dict] = {
    "shot": {"label": "Shot", "inputs": {}, "outputs": {"shot": "shot_spec"}, "params": ["shot_id"]},
    "character": {"label": "Character", "inputs": {}, "outputs": {"character": "character"}, "params": ["entity_id"]},
    "location": {"label": "Location", "inputs": {}, "outputs": {"location": "location"}, "params": ["entity_id"]},
    "zimage": {"label": "Generate: Z-Image Turbo", "inputs": {"shot": "shot_spec"}, "outputs": {"asset": "asset"}, "params": []},
    "report": {"label": "Report", "inputs": {"asset": "asset"}, "outputs": {}, "params": []},
}

TEMPLATES = {
    "shot_to_image": {
        "name": "Shot to image (Z-Image Turbo)",
        "description": "One shot, one generation. The smallest end-to-end graph.",
        "nodes": [("shot", 40, 60, {}), ("zimage", 340, 60, {}), ("report", 620, 60, {})],
        "edges": [(0, "shot", 1, "shot"), (1, "asset", 2, "asset")],
    },
    "character_location_to_image": {
        "name": "Character and location to shot image",
        "description": "Shows which character and location feed a shot. Connect them to the shot node to include them.",
        "nodes": [("character", 40, 20, {}), ("location", 40, 170, {}), ("shot", 340, 90, {}),
                  ("zimage", 620, 90, {}), ("report", 900, 90, {})],
        "edges": [(2, "shot", 3, "shot"), (3, "asset", 4, "asset")],
    },
}


class GNode(BaseModel):
    id: str
    type: str
    x: float = 0
    y: float = 0
    params: dict[str, str] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        return check_id(v)


class GEdge(BaseModel):
    id: str
    src: str
    src_port: str
    dst: str
    dst_port: str


class Graph(BaseModel):
    id: str
    name: str = Field(min_length=1, max_length=120)
    nodes: list[GNode] = []
    edges: list[GEdge] = []
    updated_at: str = ""


def new_graph(name: str, template: str | None = None, now: str = "") -> dict:
    g = Graph(id=new_id(), name=name.strip() or "Untitled graph", updated_at=now)
    if template:
        t = TEMPLATES.get(template)
        if t is None:
            raise ValueError("unknown template")
        ids = []
        for typ, x, y, params in t["nodes"]:
            nid = new_id()
            ids.append(nid)
            g.nodes.append(GNode(id=nid, type=typ, x=x, y=y, params=params))
        for si, sp, di, dp in t["edges"]:
            g.edges.append(GEdge(id=new_id(), src=ids[si], src_port=sp, dst=ids[di], dst_port=dp))
    return g.model_dump()


def validate(meta: ProjectMeta, raw: dict) -> dict:
    """Return {ok, errors[], warnings[], run_plan[]}. Reports problems in user graphs; never raises for them."""
    errors: list[dict] = []
    warnings: list[dict] = []
    try:
        g = Graph.model_validate(raw)
    except Exception as e:
        return {"ok": False, "errors": [{"code": "malformed", "message": f"graph is malformed: {type(e).__name__}"}],
                "warnings": [], "run_plan": []}
    ids = {n.id: n for n in g.nodes}
    ents = {e.id: e for e in meta.entities}
    shots = {s.id: s for s in meta.shots}

    for n in g.nodes:
        spec = NODE_TYPES.get(n.type)
        if spec is None:
            errors.append({"node": n.id, "code": "unknown_type", "message": f"'{n.type}' is not a node type"})
            continue
        if n.type == "shot":
            s = shots.get(n.params.get("shot_id", ""))
            if s is None:
                errors.append({"node": n.id, "code": "shot_missing", "message": "this shot node has no shot, or the shot was removed"})
            elif not s.enabled:
                errors.append({"node": n.id, "code": "shot_disabled", "message": "the shot is disabled"})
        if n.type in ("character", "location"):
            e = ents.get(n.params.get("entity_id", ""))
            want = "character" if n.type == "character" else "world"
            if e is None or e.kind != want or e.archived:
                errors.append({"node": n.id, "code": "entity_missing", "message": f"the {n.type} node does not point at an active {want}"})

    seen_in: dict[tuple[str, str], int] = {}
    for e in g.edges:
        if e.src not in ids or e.dst not in ids:
            errors.append({"edge": e.id, "code": "dangling", "message": "a connection points at a removed node"})
            continue
        sn, dn = ids[e.src], ids[e.dst]
        so = NODE_TYPES.get(sn.type, {}).get("outputs", {})
        di = NODE_TYPES.get(dn.type, {}).get("inputs", {})
        if e.src_port not in so:
            errors.append({"edge": e.id, "code": "bad_output", "message": f"'{sn.type}' has no output '{e.src_port}'"})
            continue
        if e.dst_port not in di:
            errors.append({"edge": e.id, "code": "bad_input", "message": f"'{dn.type}' has no input '{e.dst_port}'"})
            continue
        if so[e.src_port] != di[e.dst_port]:
            errors.append({"edge": e.id, "code": "type_mismatch", "message": f"cannot connect {so[e.src_port]} to {di[e.dst_port]}"})
            continue
        seen_in[(e.dst, e.dst_port)] = seen_in.get((e.dst, e.dst_port), 0) + 1
    for (nid, port), count in seen_in.items():
        if count > 1:
            errors.append({"node": nid, "code": "multiple_inputs", "message": f"input '{port}' has more than one connection"})

    for n in g.nodes:
        for port in NODE_TYPES.get(n.type, {}).get("inputs", {}):
            if (n.id, port) not in seen_in and n.type == "zimage":
                errors.append({"node": n.id, "code": "input_missing", "message": "connect a shot node to this generator"})
            if (n.id, port) not in seen_in and n.type == "report":
                warnings.append({"node": n.id, "code": "report_unconnected", "message": "this report has nothing connected"})

    if _has_cycle(g):
        errors.append({"code": "cycle", "message": "the graph loops back on itself"})

    plan = []
    if not errors:
        for n in g.nodes:
            if n.type == "zimage":
                src = next(e for e in g.edges if e.dst == n.id and e.dst_port == "shot")
                plan.append({"node": n.id, "shot_id": ids[src.src].params["shot_id"]})
    return {"ok": not errors, "errors": errors, "warnings": warnings, "run_plan": plan}


def _has_cycle(g: Graph) -> bool:
    adj: dict[str, list[str]] = {n.id: [] for n in g.nodes}
    for e in g.edges:
        if e.src in adj and e.dst in adj:
            adj[e.src].append(e.dst)
    state: dict[str, int] = {}

    def visit(u: str) -> bool:
        state[u] = 1
        for v in adj[u]:
            if state.get(v) == 1 or (state.get(v) is None and visit(v)):
                return True
        state[u] = 2
        return False

    return any(state.get(n) is None and visit(n) for n in adj)
