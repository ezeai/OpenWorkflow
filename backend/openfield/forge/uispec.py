"""The versioned UI specification for a Forge app, its deterministic generator and its validator.

A spec is data, not code: a list of components from a small fixed catalogue. Each component that sets a
workflow value carries a binding (node id + input name). A binding is trusted only after `validate`
finds that exact input in the workflow's analysis and the component fits its type and limits. The
renderer shows only what validation did not reject; nothing here, and nothing a language model proposes
later, can name a node that is not in the workflow.
"""
from __future__ import annotations

import copy
import re
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

SCHEMA_VERSION = 1
# Only components whose behaviour exists today. Video, audio and gallery components arrive with the milestones that run them.
CATALOG = ("text", "prompt", "number", "slider", "select", "toggle", "seed", "image_upload")
ComponentType = Literal["text", "prompt", "number", "slider", "select", "toggle", "seed", "image_upload"]
Layout = Literal["simple_generator", "reference_guided", "advanced_workspace"]
LAYOUTS = ("simple_generator", "reference_guided", "advanced_workspace")
_COMPAT: dict[str, set[str]] = {
    "text": {"STRING"}, "prompt": {"STRING"}, "number": {"INT", "FLOAT"}, "slider": {"INT", "FLOAT"},
    "select": {"COMBO"}, "toggle": {"BOOLEAN"}, "seed": {"INT"}, "image_upload": {"COMBO"},
}
_NUMERIC = {"INT", "FLOAT"}


class Binding(BaseModel):
    node: str = Field(max_length=64)
    input: str = Field(max_length=64)

    @property
    def id(self) -> str:
        return f"{self.node}.{self.input}"


class Props(BaseModel):
    min: int | float | None = None
    max: int | float | None = None
    step: int | float | None = None
    default: str | int | float | bool | None = None
    choices: list[str | int | float | bool] | None = Field(default=None, max_length=500)
    rows: int | None = Field(default=None, ge=1, le=20)
    placeholder: str | None = Field(default=None, max_length=200)


class Component(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,80}$")
    type: ComponentType
    label: str = Field(min_length=1, max_length=80)
    help: str = Field(default="", max_length=300)
    binding: Binding
    group: Literal["main", "advanced"] = "main"
    props: Props = Field(default_factory=Props)
    hidden: bool = False
    role: Literal["identity", "layout", "garment", "style", "other"] | None = None   # what an image input is for
    reason: str | None = Field(default=None, max_length=200)   # why this control exists, from the rule that chose it

    @field_validator("label", "help", mode="after")
    @classmethod
    def _plain(cls, v: str) -> str:
        return re.sub(r"[\x00-\x1f]", " ", v).strip()


class OutputSpec(BaseModel):
    node: str = Field(max_length=64)
    media: Literal["image", "video", "audio", "comparison", "other"]
    label: str = Field(default="Result", max_length=80)


class UISpec(BaseModel):
    schema_version: int = SCHEMA_VERSION
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    workflow_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    workflow_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    layout: Layout = "simple_generator"
    components: list[Component] = Field(default_factory=list, max_length=200)
    outputs: list[OutputSpec] = Field(default_factory=list, max_length=20)
    generated_by: Literal["deterministic", "user", "model"] = "deterministic"
    rules: str | None = Field(default=None, max_length=40)   # the rule version that built this spec; None for older specs


RULES_VERSION = "deterministic-1"   # bump when the rules change what a workflow produces


def add_reasons(saved: UISpec, generated: UISpec) -> tuple[UISpec, list[str]]:
    """The saved spec with a reason added to each control that has none, and the rule version recorded.
    Labels, groups, values and anything else the user changed are kept as they are. Returns the ids that changed."""
    fresh = {c.id: c for c in generated.components}
    changed = []
    comps = []
    for c in saved.components:
        if c.reason is None and c.id in fresh and fresh[c.id].reason:
            c = c.model_copy(update={"reason": fresh[c.id].reason})
            changed.append(c.id)
        comps.append(c)
    rules_changed = saved.rules != RULES_VERSION
    if not changed and not rules_changed:
        return saved, []
    return saved.model_copy(update={"components": comps, "rules": RULES_VERSION}), changed


def parse(data: dict) -> UISpec:
    """Raises ValueError with a readable message. Newer schema versions are refused, not guessed at."""
    if not isinstance(data, dict):
        raise ValueError("spec must be an object")
    v = data.get("schema_version", SCHEMA_VERSION)
    if not isinstance(v, int) or v > SCHEMA_VERSION or v < 1:
        raise ValueError(f"spec schema version {v!r} is not supported (this build reads up to {SCHEMA_VERSION})")
    try:
        spec = UISpec.model_validate(data)
    except ValidationError as e:
        raise ValueError("; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors()[:6]))
    seen: set[str] = set()
    for c in spec.components:
        if c.id in seen:
            raise ValueError(f"component id '{c.id}' is used twice")
        seen.add(c.id)
    return spec


# ---------------- generation ----------------

def _humanize(s: str) -> str:
    s = s.replace("_", " ").replace(".", " ").strip()
    return (s[:1].upper() + s[1:]) if s else "Value"


def _slug(control_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.:-]", "_", control_id)[:80]


def _reason(c: dict) -> str:
    """One sentence: what the control is and the rule that chose it. Built only from the analysis, so it is stable."""
    kind = c["inferred"]["kind"]
    rule = c["inferred"].get("rule") or "the input's type"
    what = {"prompt": "A prompt", "negative_prompt": "A negative prompt", "seed": "A seed", "image_input": "An input image"}.get(kind, "A setting")
    return f"{what}: {rule}."[:200]


def _component_for(c: dict) -> Component | None:
    t = c["fact"]["type"]
    kind = c["inferred"]["kind"]
    k = c["fact"]["constraints"]
    value = c["fact"]["value"]
    group = "main" if c["suggested"]["show"] else "advanced"
    base = dict(id=_slug(c["id"]), binding=Binding(node=c["node"], input=c["input"]), group=group, reason=_reason(c))
    label = {"prompt": "Prompt", "negative_prompt": "Negative prompt", "seed": "Seed", "image_input": "Input image"}.get(kind) or _humanize(c["input"])
    help_ = f"{c['node_title'] or c['class_type']} · {c['input']}"
    if t == "STRING":
        return Component(type="prompt" if kind in {"prompt", "negative_prompt"} or k.get("multiline") else "text", label=label, help=help_,
                         props=Props(default=value if isinstance(value, str) else None, rows=4 if kind in {"prompt", "negative_prompt"} else None), **base)
    if t == "BOOLEAN":
        return Component(type="toggle", label=label, help=help_, props=Props(default=bool(value)), **base)
    if t == "COMBO":
        if kind == "image_input":
            return Component(type="image_upload", label=label, help=help_, props=Props(default=value if isinstance(value, str) else None), **base)
        return Component(type="select", label=label, help=help_,
                         props=Props(default=value if isinstance(value, (str, int, float, bool)) else None, choices=k.get("choices")), **base)
    if t == "INT" and kind == "seed":
        return Component(type="seed", label=label, help=help_, props=Props(min=k.get("min"), max=k.get("max"), default=value if isinstance(value, int) else None), **base)
    if t in _NUMERIC:
        lo, hi = k.get("min"), k.get("max")
        slider = lo is not None and hi is not None and (hi - lo) <= 100
        return Component(type="slider" if slider else "number", label=label, help=help_,
                         props=Props(min=lo, max=hi, step=k.get("step") or (1 if t == "INT" else None),
                                     default=value if isinstance(value, (int, float)) and not isinstance(value, bool) else None), **base)
    return None          # unknown type (dynamic inputs, etc.): not offered as a control


def generate(workflow: dict, analysis: dict) -> UISpec:
    """The same workflow always gives the same spec. No language model is involved."""
    pairs = []
    for c in analysis["controls"]:
        if c["suggested"]["hidden"]:
            continue
        comp = _component_for(c)
        if comp is not None:
            pairs.append((comp, c))
    counts: dict[str, int] = {}
    for comp, _ in pairs:
        counts[comp.label] = counts.get(comp.label, 0) + 1
    for comp, ctl in pairs:
        if counts[comp.label] > 1:
            comp.label = f"{comp.label} ({ctl['node_title'] or ctl['class_type']} #{ctl['node']})"[:80]
    comps = [comp for comp, _ in pairs]
    comps.sort(key=lambda c: (c.group != "main", 0 if c.type == "image_upload" else 1 if c.type == "prompt" else 2))
    family = analysis["task"]["family"]
    layout = "reference_guided" if any(c.type == "image_upload" and c.group == "main" for c in comps) else \
        "simple_generator" if family.startswith("text-to-") and sum(c.group == "main" for c in comps) <= 6 else "advanced_workspace"
    outputs = [OutputSpec(node=o["node"], media=o["media"] if o["media"] in {"image", "video", "audio", "comparison"} else "other",
                          label=(o["title"] or "Result")[:80]) for o in analysis["outputs"]]
    return UISpec(name=workflow["name"][:120], description="", workflow_id=workflow["id"], workflow_sha256=workflow["sha256"],
                  layout=layout, components=comps, outputs=outputs, generated_by="deterministic", rules=RULES_VERSION)


# ---------------- validation ----------------

def _check(c: Component, ctl: dict | None) -> dict:
    """One component's verdict: verified, unresolved (cannot be checked yet) or invalid, with the evidence used."""
    if ctl is None:
        return {"component": c.id, "status": "invalid", "reasons": [f"the workflow has no editable input '{c.binding.input}' on node {c.binding.node}"], "evidence": None}
    fact = ctl["fact"]
    ev = {"control": ctl["id"], "type": fact["type"], "checked_against": fact["checked_against"], "constraints": fact["constraints"]}
    if fact["type"] is None:
        return {"component": c.id, "status": "unresolved", "reasons": ["this input's type could not be checked (no node definition)"], "evidence": ev}
    if fact["type"] not in _COMPAT[c.type]:
        return {"component": c.id, "status": "invalid", "reasons": [f"a {c.type} component cannot set a {fact['type']} input"], "evidence": ev}
    reasons: list[str] = []
    invalid = False
    k = fact["constraints"]
    if c.type in {"number", "slider", "seed"}:
        for key, bound, cmp in (("min", k.get("min"), lambda a, b: a < b), ("max", k.get("max"), lambda a, b: a > b)):
            v = getattr(c.props, key)
            # Compare as floats: a browser turns 2^64-1 into 2^64 when it sends the spec back, and that is not widening.
            if bound is not None and v is not None and cmp(float(v), float(bound)):
                invalid = True
                reasons.append(f"{key} {v} is outside the workflow's limit {bound}")
        if c.type == "slider" and (c.props.min is None or c.props.max is None):
            invalid = True
            reasons.append("a slider needs a minimum and a maximum")
    if c.type == "image_upload":
        # An image input is checked by its node definition, not by a list of files. The file is verified when it
        # is uploaded (see forge/uploads.py), so the list length does not matter here.
        if ctl["inferred"]["kind"] != "image_input":
            invalid = True
            reasons.append("this input is not an image input")
    if c.type == "select":
        known = k.get("choices")
        if known is None or len(known) >= 200:
            reasons.append("the list of allowed values is not known (ComfyUI is not running, or the list is long)")
        elif c.type == "select" and c.props.choices is not None and not set(map(str, c.props.choices)) <= set(map(str, known)):
            invalid = True
            reasons.append("the component offers values the node does not accept")
    if invalid:
        return {"component": c.id, "status": "invalid", "reasons": reasons, "evidence": ev}
    return {"component": c.id, "status": "unresolved" if reasons else "verified", "reasons": reasons, "evidence": ev}


def validate(spec: UISpec, workflow: dict, analysis: dict) -> dict:
    controls = {c["id"]: c for c in analysis["controls"]}
    results = [_check(c, controls.get(c.binding.id)) for c in spec.components]
    bound = [c.binding.id for c in spec.components]
    dup = sorted({b for b in bound if bound.count(b) > 1})
    problems = [f"input {b} is bound by more than one component" for b in dup]
    if spec.workflow_id != workflow["id"] or spec.workflow_sha256 != workflow["sha256"]:
        problems.append("the spec was made for a different version of this workflow")
    out_nodes = {o["node"] for o in analysis["outputs"]}
    problems += [f"output node {o.node} is not an output of this workflow" for o in spec.outputs if o.node not in out_nodes]
    counts = {s: sum(r["status"] == s for r in results) for s in ("verified", "unresolved", "invalid")}
    status = "invalid" if counts["invalid"] or problems else "needs_attention" if counts["unresolved"] else "valid"
    return {"status": status, "counts": counts, "components": results, "problems": problems,
            "runnable": status != "invalid" and analysis["readiness"] != "blocked"}


# ---------------- applying values ----------------

class ApplyError(ValueError):
    pass


def apply_values(spec: UISpec, validation: dict, analysis: dict, api_prompt: dict, values: dict) -> dict:
    """A copy of the API prompt with the user's values set. Only inputs bound by a not-invalid component can change."""
    status = {r["component"]: r["status"] for r in validation["components"]}
    comps = {c.id: c for c in spec.components}
    controls = {c["id"]: c for c in analysis["controls"]}
    out = copy.deepcopy(api_prompt)
    for cid, v in values.items():
        c = comps.get(cid)
        if c is None:
            raise ApplyError(f"unknown component '{cid}'")
        if status.get(cid) == "invalid":
            raise ApplyError(f"component '{c.label}' is invalid and cannot be used")
        ctl = controls.get(c.binding.id)
        node = out.get(c.binding.node)
        if ctl is None or node is None or c.binding.input not in node["inputs"]:
            raise ApplyError(f"component '{c.label}' no longer matches the workflow")
        t = ctl["fact"]["type"]
        k = ctl["fact"]["constraints"]
        if t == "INT":
            if isinstance(v, bool) or not isinstance(v, int):
                raise ApplyError(f"{c.label}: expected a whole number")
        elif t == "FLOAT":
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise ApplyError(f"{c.label}: expected a number")
        elif t == "BOOLEAN":
            if not isinstance(v, bool):
                raise ApplyError(f"{c.label}: expected true or false")
        elif t in {"STRING", "COMBO"}:
            if not isinstance(v, str):
                raise ApplyError(f"{c.label}: expected text")
            if len(v) > 20000:
                raise ApplyError(f"{c.label}: text is too long")
        if t in _NUMERIC:
            if k.get("min") is not None and v < k["min"] or k.get("max") is not None and v > k["max"]:
                raise ApplyError(f"{c.label}: {v} is outside {k.get('min')}..{k.get('max')}")
        known = k.get("choices")
        if t == "COMBO" and known is not None and len(known) < 200 and v not in known:
            raise ApplyError(f"{c.label}: '{v}' is not one of the values ComfyUI offers")
        node["inputs"][c.binding.input] = v
    return out
