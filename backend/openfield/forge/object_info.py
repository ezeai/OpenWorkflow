"""Node definitions from ComfyUI's /object_info, used to map UI-format widget values to input names.

Sources, in order: the live server, the cached copy of the last live read (in the data folder), then
the bundled core snapshot. Every result says which source it came from, because a mapping checked
against a stale or partial snapshot is weaker evidence than one checked against the running server.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

BUNDLED = Path(__file__).resolve().parent.parent / "data" / "forge" / "object_info_core.json"
WIDGET_TYPES = {"INT", "FLOAT", "STRING", "BOOLEAN", "COMBO", "COMFY_DYNAMICCOMBO_V3"}
DYNAMIC_COMBO = "COMFY_DYNAMICCOMBO_V3"
# V3 types whose values arrive through links under their own slot names, not as saved widget values.
LINK_STYLE_V3 = {"COMFY_AUTOGROW_V3", "COMFY_MATCHTYPE_V3"}
UPLOAD_FLAGS = ("image_upload", "video_upload", "audio_upload")
_FILE_LIKE = re.compile(r"\.[A-Za-z0-9]{2,12}$")


@dataclass
class InputSpec:
    name: str
    type: str                 # INT, FLOAT, STRING, BOOLEAN, COMBO, or a link type such as MODEL
    required: bool
    options: dict
    choices: list | None = None

    @property
    def is_widget(self) -> bool:
        # A union such as "FLOAT,INT" is a number widget when every part is a widget type.
        parts = self.type.split(",")
        widget = all(p in WIDGET_TYPES for p in parts)
        return widget and not self.options.get("forceInput") and not self.socketless

    @property
    def socketless(self) -> bool:
        """Editor-only controls such as the Image Compare slider; never part of the prompt."""
        return bool(self.options.get("socketless"))

    @property
    def extra_widget_values(self) -> int:
        """Values the frontend stores after this widget: the seed control, or an upload button."""
        n = 1 if self.options.get("control_after_generate") else 0
        return n + sum(1 for f in UPLOAD_FLAGS if self.options.get(f))


@dataclass
class NodeDef:
    name: str
    inputs: list[InputSpec]
    outputs: list[str]
    output_names: list[str]
    output_node: bool
    category: str = ""
    unsupported_widgets: list[str] = field(default_factory=list)   # V3 dynamic types the converter cannot map yet

    def widgets(self) -> list[InputSpec]:
        return [i for i in self.inputs if i.is_widget]

    def input(self, name: str) -> InputSpec | None:
        return next((i for i in self.inputs if i.name == name), None)


def _parse_input(name: str, spec, required: bool) -> InputSpec:
    t = spec[0] if isinstance(spec, (list, tuple)) and spec else spec
    opts = spec[1] if isinstance(spec, (list, tuple)) and len(spec) > 1 and isinstance(spec[1], dict) else {}
    if isinstance(t, list):
        return InputSpec(name, "COMBO", required, opts, list(t))
    if t == "COMBO":
        return InputSpec(name, "COMBO", required, opts, list(opts.get("options") or []))
    if t == DYNAMIC_COMBO:
        return InputSpec(name, t, required, opts, [o.get("key") for o in opts.get("options") or [] if isinstance(o, dict)])
    return InputSpec(name, str(t), required, opts)


def parse_inputs(sections: dict, order: dict | None = None) -> list[InputSpec]:
    order = order or {sec: list((sections.get(sec) or {}).keys()) for sec in ("required", "optional")}
    out = []
    for sec in ("required", "optional"):
        specs = sections.get(sec) or {}
        out += [_parse_input(n, specs[n], sec == "required") for n in order.get(sec, []) if n in specs]
    return out


def dynamic_option(spec: InputSpec, key) -> list[InputSpec] | None:
    """The nested inputs a dynamic combo shows when `key` is selected, or None if the key is unknown."""
    for opt in spec.options.get("options") or []:
        if isinstance(opt, dict) and opt.get("key") == key:
            return parse_inputs(opt.get("inputs") or {})
    return None


def parse_node(name: str, raw: dict) -> NodeDef:
    sections = raw.get("input") or {}
    order = raw.get("input_order") or {sec: list((sections.get(sec) or {}).keys()) for sec in ("required", "optional")}
    inputs: list[InputSpec] = []
    unsupported: list[str] = []
    for sec in ("required", "optional"):
        specs = sections.get(sec) or {}
        for n in order.get(sec, []):
            if n not in specs:
                continue
            spec = _parse_input(n, specs[n], sec == "required")
            if spec.type.endswith("_V3") and spec.type != DYNAMIC_COMBO and spec.type not in LINK_STYLE_V3:
                unsupported.append(n)
            inputs.append(spec)
    return NodeDef(name=name, inputs=inputs, outputs=list(raw.get("output") or []), output_names=list(raw.get("output_name") or []),
                   output_node=bool(raw.get("output_node")), category=str(raw.get("category") or ""), unsupported_widgets=unsupported)


@dataclass
class ObjectInfo:
    defs: dict[str, NodeDef]
    source: str              # live | cache | bundled | none
    captured_at: str | None
    comfyui_version: str | None
    note: str = ""

    def get(self, cls: str) -> NodeDef | None:
        return self.defs.get(cls)

    def describe(self) -> dict:
        return {"source": self.source, "captured_at": self.captured_at, "comfyui_version": self.comfyui_version,
                "node_classes": len(self.defs), "note": self.note}


def from_raw(raw: dict, source: str, captured_at: str | None, version: str | None, note: str = "") -> ObjectInfo:
    defs = {}
    for k, v in raw.items():
        if isinstance(v, dict):
            try:
                defs[k] = parse_node(k, v)
            except Exception:       # one malformed definition must not hide the rest
                continue
    return ObjectInfo(defs, source, captured_at, version, note)


def _file_like(values) -> bool:
    return any(isinstance(x, str) and _FILE_LIKE.search(x) for x in values)


def strip_for_bundle(raw: dict) -> dict:
    """Remove tooltips and file-name choices (they list the user's own model files)."""
    out = {}
    for cls, d in raw.items():
        inp = {}
        for sec in ("required", "optional"):
            specs = {}
            for n, spec in ((d.get("input") or {}).get(sec) or {}).items():
                t = spec[0] if isinstance(spec, list) and spec else spec
                opts = dict(spec[1]) if isinstance(spec, list) and len(spec) > 1 and isinstance(spec[1], dict) else {}
                opts.pop("tooltip", None)
                if isinstance(t, list) and _file_like(t):
                    t, opts["file_choices_removed"] = [], True
                if isinstance(opts.get("options"), list) and _file_like(opts["options"]):
                    opts["options"], opts["file_choices_removed"] = [], True
                specs[n] = [t, opts] if opts else [t]
            if specs:
                inp[sec] = specs
        out[cls] = {"input": inp, "input_order": d.get("input_order"), "output": d.get("output"), "output_name": d.get("output_name"),
                    "output_node": d.get("output_node"), "category": d.get("category")}
    return out


def load(home: Path, client_get=None) -> ObjectInfo:
    """client_get(path) returns parsed JSON from the configured ComfyUI, or raises. It is never required."""
    cache = home / "forge" / "object_info.json"
    if client_get is not None:
        try:
            raw = client_get("/object_info")
            version = None
            try:
                version = client_get("/system_stats")["system"]["comfyui_version"]
            except Exception:
                pass
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            cache.parent.mkdir(parents=True, exist_ok=True)
            tmp = cache.with_suffix(".tmp")
            tmp.write_text(json.dumps({"captured_at": now, "comfyui_version": version, "object_info": raw}), encoding="utf-8")
            tmp.replace(cache)
            return from_raw(raw, "live", now, version)
        except Exception:
            pass
    for path, source in ((cache, "cache"), (BUNDLED, "bundled")):
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        note = "" if source == "cache" else "Core nodes only, from the bundled snapshot. Custom nodes cannot be checked until ComfyUI is running."
        return from_raw(d["object_info"], source, d.get("captured_at"), d.get("comfyui_version"), note)
    return ObjectInfo({}, "none", None, None, "No node definitions available.")
