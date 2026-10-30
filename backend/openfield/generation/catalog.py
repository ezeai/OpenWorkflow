"""Fill a ComfyUI node's inputs from its own published definition, by name and type.

ComfyUI describes every node in /object_info: each input is [type, options] (for example ["INT", {"default": 8}])
or, for a choice, a list of allowed values. This module reads that definition and fills what it can from the
values a generation already has (prompt, size, seed, steps). Anything it cannot fill is reported by name, so
a bad or incomplete request is never sent silently. Nothing here is specific to one model.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SCALAR_TYPES = {"INT", "FLOAT", "STRING", "BOOLEAN"}
PROMPT_NAMES = {"prompt", "text", "positive", "positive_prompt", "prompt_text"}
NEGATIVE_NAMES = {"negative", "negative_prompt", "negative_text"}


@dataclass(frozen=True)
class InputSpec:
    name: str
    type: str                      # "INT", "FLOAT", "STRING", "BOOLEAN", or "COMBO"
    required: bool
    default: Any = None
    options: tuple = ()            # allowed values for a COMBO
    minimum: float | None = None
    maximum: float | None = None


@dataclass
class FillResult:
    inputs: dict[str, Any] = field(default_factory=dict)
    unfilled: list[str] = field(default_factory=list)   # required inputs we could not fill, by name


def parse_node(node_info: dict) -> list[InputSpec]:
    """Read one node's entry from /object_info into input specs, required inputs first."""
    specs: list[InputSpec] = []
    for section, required in (("required", True), ("optional", False)):
        for name, spec in (node_info.get("input", {}).get(section) or {}).items():
            specs.append(_parse_input(name, spec, required))
    return specs


def _parse_input(name: str, spec: list, required: bool) -> InputSpec:
    head = spec[0] if spec else None
    opts = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
    if isinstance(head, list):
        return InputSpec(name, "COMBO", required, default=opts.get("default", head[0] if head else None),
                         options=tuple(head))
    return InputSpec(name, str(head), required, default=opts.get("default"),
                     minimum=opts.get("min"), maximum=opts.get("max"))


def fill(specs: list[InputSpec], values: dict[str, Any]) -> FillResult:
    """Fill each spec from `values` (keyed by role: prompt, negative, width, height, seed, steps, cfg).

    A spec is filled when a role matches its name and its type fits. Otherwise its default is used. A required
    spec with neither a value nor a default is listed in `unfilled`.
    """
    result = FillResult()
    for spec in specs:
        value = _value_for(spec, values)
        if value is None and spec.default is not None:
            value = spec.default
        if value is None:
            if spec.required:
                result.unfilled.append(spec.name)
            continue
        result.inputs[spec.name] = _coerce(spec, value)
    return result


def _value_for(spec: InputSpec, values: dict[str, Any]) -> Any:
    name = spec.name.lower()
    if spec.type == "STRING":
        if name in NEGATIVE_NAMES:
            return values.get("negative")
        if name in PROMPT_NAMES:
            return values.get("prompt")
        return None
    if spec.type in ("INT", "FLOAT") and name in values:
        return values[name]
    if spec.type == "COMBO" and name in values and values[name] in spec.options:
        return values[name]
    return None


def _coerce(spec: InputSpec, value: Any) -> Any:
    if spec.type == "INT":
        value = int(round(float(value)))
    elif spec.type == "FLOAT":
        value = float(value)
    if spec.minimum is not None and isinstance(value, (int, float)):
        value = max(value, spec.minimum)
    if spec.maximum is not None and isinstance(value, (int, float)):
        value = min(value, spec.maximum)
    return value
