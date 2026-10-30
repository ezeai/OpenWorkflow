"""Convert a UI-format workflow into the API format ComfyUI's /prompt accepts.

The ComfyUI editor does this in JavaScript before it queues a prompt. Forge does it here so a saved
workflow can run without the editor. Steps: expand subgraphs (inner nodes get ids like "57:27", as the
editor does), drop muted nodes, route links through bypassed nodes and reroutes, take values from
primitive nodes, and map each node's widgets_values to input names using the node definitions.

Every guess is reported. A mapping that could not be checked is a problem, never a silent default.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .object_info import DYNAMIC_COMBO, LINK_STYLE_V3, ObjectInfo, dynamic_option
from .workflow import (MODE_BYPASS, MODE_NEVER, SUBGRAPH_INPUT_ID, Node, Scope, UIWorkflow, UI_ONLY_TYPES)

MAX_DEPTH = 64


@dataclass
class Problem:
    severity: str          # error (blocks running) | warning (runs, but check it) | info
    message: str
    node: str | None = None

    def as_dict(self) -> dict:
        return {"severity": self.severity, "message": self.message, "node": self.node}


@dataclass
class WidgetSource:
    """Where a value in the API prompt came from in the original file."""
    exec_id: str
    input: str
    scope: str             # "graph" or the subgraph name
    node_id: str           # id inside that scope
    origin: str            # widget | primitive | missing


@dataclass
class Conversion:
    prompt: dict
    problems: list[Problem]
    sources: list[WidgetSource]
    titles: dict[str, str] = field(default_factory=dict)

    @property
    def status(self) -> str:
        if any(p.severity == "error" for p in self.problems):
            return "blocked"
        return "warnings" if any(p.severity == "warning" for p in self.problems) else "ok"


@dataclass
class _Ctx:
    scope: Scope
    prefix: str
    parent: "_Ctx | None" = None
    instance: Node | None = None
    depth: int = 0

    def exec_id(self, node_id: str) -> str:
        return self.prefix + node_id

    @property
    def label(self) -> str:
        return self.scope.name or "graph"


class _Converter:
    def __init__(self, wf: UIWorkflow, info: ObjectInfo):
        self.wf, self.info = wf, info
        self.prompt: dict = {}
        self.problems: list[Problem] = []
        self.sources: list[WidgetSource] = []
        self.titles: dict[str, str] = {}

    def problem(self, severity: str, message: str, node: str | None = None) -> None:
        self.problems.append(Problem(severity, message, node))

    # -- following links back to the node that really produces the value --
    def resolve(self, ctx: _Ctx, link_id: str, depth: int = 0):
        """Returns ("link", exec_id, slot), ("value", v) or None when nothing feeds the input."""
        if depth > MAX_DEPTH:
            self.problem("error", "a chain of reroutes, bypasses or subgraphs is too deep or loops")
            return None
        lk = ctx.scope.links.get(link_id)
        if lk is None:
            return None
        if lk.origin_id == SUBGRAPH_INPUT_ID:
            if ctx.instance is None or ctx.parent is None or lk.origin_slot >= len(ctx.scope.inputs):
                return None
            port = ctx.scope.inputs[lk.origin_slot]
            slot = next((s for s in ctx.instance.inputs if s.name == port.name), None)
            if slot is None or slot.link is None:
                return None          # the inner node keeps its own widget value (promoted widget)
            return self.resolve(ctx.parent, slot.link, depth + 1)
        node = ctx.scope.nodes.get(lk.origin_id)
        if node is None:
            return None
        if node.mode == MODE_NEVER:
            return None
        if node.mode == MODE_BYPASS:
            through = next((s for s in node.inputs if s.link is not None and (s.type == lk.type or lk.type == "*" or s.type == "*")), None)
            return self.resolve(ctx, through.link, depth + 1) if through else None
        if node.type == "Reroute":
            src = node.inputs[0].link if node.inputs else None
            return self.resolve(ctx, src, depth + 1) if src else None
        if node.type == "PrimitiveNode":
            wv = node.widgets_values
            return ("value", wv[0]) if isinstance(wv, list) and wv else None
        sub = self.wf.subgraphs.get(node.type)
        if sub is not None:
            if lk.origin_slot >= len(sub.outputs) or not sub.outputs[lk.origin_slot].link_ids:
                return None
            child = _Ctx(sub, ctx.exec_id(node.id) + ":", ctx, node, ctx.depth + 1)
            return self.resolve(child, sub.outputs[lk.origin_slot].link_ids[0], depth + 1)
        return ("link", ctx.exec_id(node.id), lk.origin_slot)

    # -- producing API nodes --
    def emit_scope(self, ctx: _Ctx) -> None:
        if ctx.depth > MAX_DEPTH:
            self.problem("error", "subgraphs are nested too deeply or include themselves")
            return
        for node in ctx.scope.nodes.values():
            if node.type in UI_ONLY_TYPES or node.mode in (MODE_NEVER, MODE_BYPASS):
                continue
            sub = self.wf.subgraphs.get(node.type)
            if sub is not None:
                self.emit_scope(_Ctx(sub, ctx.exec_id(node.id) + ":", ctx, node, ctx.depth + 1))
                continue
            self.emit_node(ctx, node)

    def emit_node(self, ctx: _Ctx, node: Node) -> None:
        eid = ctx.exec_id(node.id)
        where = f"{ctx.label} node {node.id} ({node.type})"
        d = self.info.get(node.type)
        inputs: dict = {}
        if d is None:
            self.problem("error", f"{where}: node type '{node.type}' is not installed in ComfyUI or not in the node definitions ({self.info.source})", eid)
        else:
            if d.unsupported_widgets:
                self.problem("warning", f"{where}: inputs {', '.join(d.unsupported_widgets)} use a dynamic input type Forge cannot map yet", eid)
            self._map_widgets(ctx, node, d, inputs, where, eid)
        for slot in node.inputs:
            if slot.link is None:
                continue
            res = self.resolve(ctx, slot.link)
            if res is None:
                continue                     # a widget-backed input keeps its widget value
            if res[0] == "link":
                inputs[slot.name] = [res[1], res[2]]
            else:
                inputs[slot.name] = res[1]
                self.sources.append(WidgetSource(eid, slot.name, ctx.label, node.id, "primitive"))
        if d is not None:
            for spec in d.inputs:
                if spec.required and spec.name not in inputs and not spec.socketless and spec.type not in LINK_STYLE_V3:
                    self.problem("error", f"{where}: required input '{spec.name}' has no value or connection", eid)
        self.prompt[eid] = {"class_type": node.type, "inputs": inputs, "_meta": {"title": node.title or node.type}}
        self.titles[eid] = node.title or node.type

    def _map_widgets(self, ctx: _Ctx, node: Node, d, inputs: dict, where: str, eid: str) -> None:
        widgets = d.widgets()
        wv = node.widgets_values
        if isinstance(wv, dict):
            for w in widgets:
                if w.name in wv:
                    inputs[w.name] = wv[w.name]
                    self.sources.append(WidgetSource(eid, w.name, ctx.label, node.id, "widget"))
            return
        values = wv or []
        pos = 0
        missing_required: list[str] = []

        def consume(specs, prefix: str) -> None:
            nonlocal pos
            for w in specs:
                if not w.is_widget:
                    continue
                if pos >= len(values):
                    if w.required and not prefix:
                        missing_required.append(w.name)
                    continue
                key = prefix + w.name
                inputs[key] = values[pos]
                self.sources.append(WidgetSource(eid, key, ctx.label, node.id, "dynamic" if prefix else "widget"))
                pos += 1 + w.extra_widget_values
                if w.type == DYNAMIC_COMBO:
                    nested = dynamic_option(w, inputs[key])
                    if nested is None:
                        self.problem("warning", f"{where}: '{key}' is set to '{inputs[key]}', which this ComfyUI does not offer", eid)
                    else:
                        consume(nested, key + ".")

        consume(widgets, "")
        if pos < len(values):
            self.problem("info", f"{where}: {len(values) - pos} extra saved value(s) after the defined widgets were ignored (usually editor-only display controls)", eid)
        # Fewer values than widgets is normal when a newer ComfyUI added optional inputs; they take their defaults.
        for name in missing_required:
            self.problem("warning", f"{where}: no saved value for required widget '{name}'", eid)

    def check_links(self) -> None:
        for eid, n in self.prompt.items():
            for name, v in n["inputs"].items():
                if isinstance(v, list) and len(v) == 2 and isinstance(v[0], str) and isinstance(v[1], int) and v[0] not in self.prompt:
                    self.problem("error", f"node {eid} input '{name}' is connected to node {v[0]}, which is not part of the runnable graph", eid)
        if not any((self.info.get(n["class_type"]) or _NoDef).output_node for n in self.prompt.values()):
            self.problem("error", "the workflow has no output node (such as Save Image), so ComfyUI would refuse it")


class _NoDef:
    output_node = False


def convert(wf: UIWorkflow, info: ObjectInfo) -> Conversion:
    c = _Converter(wf, info)
    for p in wf.problems:
        c.problem("error" if "does not exist" in p else "warning", p)
    c.emit_scope(_Ctx(wf.root, ""))
    c.check_links()
    return Conversion(c.prompt, c.problems, c.sources, c.titles)
