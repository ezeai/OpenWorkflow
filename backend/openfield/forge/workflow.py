"""Parse ComfyUI workflow files into a typed graph without changing them.

Two formats exist. The UI format (what the ComfyUI editor saves, version 0.4) has `nodes` and `links`,
optionally `definitions.subgraphs`. The API format (what ComfyUI's /prompt accepts) maps node ids to
`{class_type, inputs}`. The original bytes are kept by the caller; nothing here rewrites them.
"""
from __future__ import annotations

from dataclasses import dataclass, field

UI_ONLY_TYPES = {"Note", "MarkdownNote", "Reroute", "PrimitiveNode"}   # frontend-only; never sent to the server
MODE_ALWAYS, MODE_NEVER, MODE_BYPASS = 0, 2, 4
SUBGRAPH_INPUT_ID, SUBGRAPH_OUTPUT_ID = "-10", "-20"


class WorkflowError(ValueError):
    """The file is not a workflow this parser understands. The message is safe to show."""


@dataclass
class Link:
    id: str
    origin_id: str
    origin_slot: int
    target_id: str
    target_slot: int
    type: str


@dataclass
class InputSlot:
    name: str
    type: str
    link: str | None
    widget: str | None         # name of the widget this input stands for, when it was a widget
    label: str | None = None


@dataclass
class OutputSlot:
    name: str
    type: str
    links: list[str]


@dataclass
class Node:
    id: str
    type: str
    mode: int
    inputs: list[InputSlot]
    outputs: list[OutputSlot]
    widgets_values: list | dict | None
    title: str | None = None
    properties: dict = field(default_factory=dict)


@dataclass
class SubgraphPort:
    name: str
    type: str
    link_ids: list[str]
    label: str | None = None


@dataclass
class Scope:
    """A set of nodes and links: the top-level graph, or one subgraph definition."""
    nodes: dict[str, Node]
    links: dict[str, Link]
    inputs: list[SubgraphPort] = field(default_factory=list)      # subgraph scopes only
    outputs: list[SubgraphPort] = field(default_factory=list)
    name: str = ""


@dataclass
class UIWorkflow:
    root: Scope
    subgraphs: dict[str, Scope]
    linear_data: dict | None         # ComfyUI App Builder selection, if present
    version: str | None
    problems: list[str]


def detect_format(data) -> str:
    if isinstance(data, dict) and isinstance(data.get("nodes"), list):
        return "ui"
    if isinstance(data, dict) and data and all(isinstance(v, dict) and "class_type" in v for v in data.values()):
        return "api"
    if isinstance(data, dict) and isinstance(data.get("prompt"), dict):
        return "api_wrapped"
    return "unsupported"


def _sid(v) -> str:
    return str(v)


def _parse_link(raw) -> Link:
    if isinstance(raw, list) and len(raw) >= 6:
        return Link(_sid(raw[0]), _sid(raw[1]), int(raw[2]), _sid(raw[3]), int(raw[4]), str(raw[5]))
    if isinstance(raw, dict):
        return Link(_sid(raw["id"]), _sid(raw["origin_id"]), int(raw["origin_slot"]), _sid(raw["target_id"]), int(raw["target_slot"]), str(raw.get("type", "*")))
    raise WorkflowError("a link entry is neither a 6-item list nor an object")


def _parse_node(raw: dict) -> Node:
    if not isinstance(raw, dict) or "id" not in raw or "type" not in raw:
        raise WorkflowError("a node has no id or type")
    inputs = []
    for i in raw.get("inputs") or []:
        w = i.get("widget")
        inputs.append(InputSlot(str(i.get("name", "")), str(i.get("type", "*")), _sid(i["link"]) if i.get("link") is not None else None,
                                str(w["name"]) if isinstance(w, dict) and "name" in w else None, i.get("label")))
    outputs = [OutputSlot(str(o.get("name", "")), str(o.get("type", "*")), [_sid(x) for x in (o.get("links") or [])]) for o in raw.get("outputs") or []]
    wv = raw.get("widgets_values")
    return Node(_sid(raw["id"]), str(raw["type"]), int(raw.get("mode") or 0), inputs, outputs,
                wv if isinstance(wv, (list, dict)) else None, raw.get("title"), raw.get("properties") or {})


def _parse_scope(nodes_raw, links_raw, problems: list[str], label: str) -> Scope:
    nodes: dict[str, Node] = {}
    for r in nodes_raw or []:
        n = _parse_node(r)
        if n.id in nodes:
            problems.append(f"{label}: node id {n.id} appears twice; the second copy was ignored")
            continue
        nodes[n.id] = n
    links: dict[str, Link] = {}
    for r in links_raw or []:
        lk = _parse_link(r)
        links[lk.id] = lk
    return Scope(nodes, links)


def _check_links(scope: Scope, problems: list[str], label: str, boundary: bool) -> None:
    known = set(scope.nodes)
    if boundary:
        known |= {SUBGRAPH_INPUT_ID, SUBGRAPH_OUTPUT_ID}
    for lk in scope.links.values():
        for end, nid in (("from", lk.origin_id), ("to", lk.target_id)):
            if nid not in known:
                problems.append(f"{label}: link {lk.id} points {end} node {nid}, which does not exist")
    for n in scope.nodes.values():
        for s in n.inputs:
            if s.link is not None and s.link not in scope.links:
                problems.append(f"{label}: node {n.id} ({n.type}) input '{s.name}' uses link {s.link}, which does not exist")


def parse_ui(data: dict) -> UIWorkflow:
    if detect_format(data) != "ui":
        raise WorkflowError("not a ComfyUI editor workflow (no 'nodes' list)")
    problems: list[str] = []
    root = _parse_scope(data.get("nodes"), data.get("links"), problems, "graph")
    _check_links(root, problems, "graph", boundary=False)
    subgraphs: dict[str, Scope] = {}
    for s in (data.get("definitions") or {}).get("subgraphs") or []:
        if not isinstance(s, dict) or "id" not in s:
            problems.append("a subgraph definition has no id and was ignored")
            continue
        label = f"subgraph '{s.get('name') or s['id']}'"
        sc = _parse_scope(s.get("nodes"), s.get("links"), problems, label)
        sc.name = str(s.get("name") or s["id"])
        sc.inputs = [SubgraphPort(str(p.get("name", "")), str(p.get("type", "*")), [_sid(x) for x in p.get("linkIds") or []], p.get("label")) for p in s.get("inputs") or []]
        sc.outputs = [SubgraphPort(str(p.get("name", "")), str(p.get("type", "*")), [_sid(x) for x in p.get("linkIds") or []], p.get("label")) for p in s.get("outputs") or []]
        _check_links(sc, problems, label, boundary=True)
        subgraphs[str(s["id"])] = sc
    linear = (data.get("extra") or {}).get("linearData")
    return UIWorkflow(root, subgraphs, linear if isinstance(linear, dict) else None, str(data.get("version")) if data.get("version") is not None else None, problems)


def parse_api(data: dict) -> dict[str, dict]:
    """Validate the shape of an API-format prompt. Returns it unchanged."""
    if detect_format(data) == "api_wrapped":
        data = data["prompt"]
    if detect_format(data) != "api":
        raise WorkflowError("not a ComfyUI API prompt (each entry needs a class_type)")
    for nid, n in data.items():
        if not isinstance(n.get("inputs", {}), dict):
            raise WorkflowError(f"node {nid}: inputs must be an object")
        for name, v in (n.get("inputs") or {}).items():
            if isinstance(v, list) and len(v) == 2 and isinstance(v[1], int) and str(v[0]) not in data:
                raise WorkflowError(f"node {nid} input '{name}' links to node {v[0]}, which does not exist")
    return data
