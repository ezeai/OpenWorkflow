"""Reusable modules: a complete Forge workflow wrapped in a versioned contract with typed inputs and outputs.

A module version is a snapshot. It stores the converted API prompt as it was when the version was saved, so
it keeps working when the source workflow is edited or deleted. Versions are immutable: a change is a new version.

Scope (Milestone 3): complete workflows only; no subgraph extraction; image outputs only; text, number,
integer, boolean and select inputs only (image inputs need the upload path, which is a later step).
Nothing here runs a workflow. `prepare` returns the prompt that would be sent, and the caller decides whether to send it.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from ..projects.store import _atomic_write, now_iso
from .store import ForgeError

SCHEMA = 1
INPUT_TYPES = ("text", "number", "integer", "boolean", "select", "image")
OUTPUT_CLASSES = ("SaveImage", "PreviewImage", "SaveVideo")
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
# The name ComfyUI stores an upload under (see forge/uploads.py). An image input can only be one of these.
IMAGE_NAME = re.compile(r"^openfield_[0-9a-f]{16}\.(png|jpg|webp)$")
_ID = re.compile(r"^[0-9a-f]{32}$")
MAX_TEXT = 8000


class ModuleError(ValueError):
    """Problems with a module definition or a set of values. The message is safe to show."""


class Port(BaseModel):
    name: str
    type: Literal["text", "number", "integer", "boolean", "select", "image"]
    node: str
    field: str
    label: str = ""
    default: Any = None
    required: bool = False
    options: list[str] | None = None

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not _NAME.match(v):
            raise ValueError("port names use lowercase letters, digits and underscores, starting with a letter (max 40)")
        return v


class OutputPort(BaseModel):
    name: str
    node: str
    label: str = ""

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not _NAME.match(v):
            raise ValueError("output names use lowercase letters, digits and underscores, starting with a letter (max 40)")
        return v


def _digest(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def check(inputs: list[Port], outputs: list[OutputPort], prompt: dict) -> list[str]:
    """Every problem with the ports against the snapshot prompt. An empty list means the ports are valid."""
    problems: list[str] = []
    if not outputs:
        problems.append("a module needs at least one output")
    seen = set()
    for p in inputs:
        if p.name in seen:
            problems.append(f"input name '{p.name}' is used twice")
        seen.add(p.name)
        node = prompt.get(p.node)
        if node is None:
            problems.append(f"input '{p.name}' points at node {p.node}, which is not in the workflow")
            continue
        if p.field not in (node.get("inputs") or {}):
            problems.append(f"input '{p.name}' points at {p.field}, which node {p.node} does not have")
            continue
        current = node["inputs"][p.field]
        if isinstance(current, list) and len(current) == 2:
            problems.append(f"input '{p.name}' is connected to another node; only plain values can be module inputs")
        if p.type == "image":
            if p.default is not None:
                problems.append(f"input '{p.name}' is an image and cannot have a default; the image is uploaded for each run")
        elif p.type == "select":
            if not p.options:
                problems.append(f"input '{p.name}' is a select and needs options")
            elif p.default is not None and p.default not in p.options:
                problems.append(f"input '{p.name}' default is not one of its options")
        elif p.default is not None:
            try:
                _check_value(p, p.default)
            except ModuleError as e:
                problems.append(f"input '{p.name}' default: {e}")
    seen_out = set()
    for o in outputs:
        if o.name in seen_out:
            problems.append(f"output name '{o.name}' is used twice")
        seen_out.add(o.name)
        node = prompt.get(o.node)
        if node is None:
            problems.append(f"output '{o.name}' points at node {o.node}, which is not in the workflow")
        elif node.get("class_type") not in OUTPUT_CLASSES:
            problems.append(f"output '{o.name}' must be a save or preview image node, not {node.get('class_type')}")
    return problems


def _check_value(p: Port, v) -> Any:
    if p.type == "image":
        if not isinstance(v, str) or not IMAGE_NAME.match(v):
            raise ModuleError("must be an image uploaded through Forge (its ComfyUI name, openfield_<hash>.png)")
        return v
    if p.type == "text":
        if not isinstance(v, str):
            raise ModuleError("must be text")
        if len(v) > MAX_TEXT:
            raise ModuleError(f"is longer than {MAX_TEXT} characters")
        return v
    if p.type == "boolean":
        if not isinstance(v, bool):
            raise ModuleError("must be true or false")
        return v
    if p.type == "integer":
        if isinstance(v, bool) or not isinstance(v, int):
            raise ModuleError("must be a whole number")
        return v
    if p.type == "number":
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ModuleError("must be a number")
        return v
    if p.type == "select":
        if not isinstance(v, str) or v not in (p.options or []):
            raise ModuleError("must be one of the listed options")
        return v
    raise ModuleError(f"unknown type {p.type}")


def prepare(inputs: list[Port], prompt: dict, values: dict) -> dict:
    """Apply values to a copy of the snapshot prompt. Unknown names, missing required values and bad types are refused."""
    known = {p.name: p for p in inputs}
    unknown = sorted(set(values) - set(known))
    if unknown:
        raise ModuleError(f"unknown input(s): {', '.join(unknown)}")
    out = json.loads(json.dumps(prompt))
    for p in inputs:
        if p.name in values:
            v = _check_value(p, values[p.name])
        elif p.default is not None:
            v = p.default
        elif p.required:
            raise ModuleError(f"input '{p.name}' is required")
        else:
            continue
        out[p.node]["inputs"][p.field] = v
    return out


class Contract(BaseModel):
    schema_version: int = SCHEMA
    module_id: str
    version: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    workflow_id: str
    workflow_name: str = ""
    source_sha256: str
    created_at: str
    inputs: list[Port]
    outputs: list[OutputPort]
    # Model files the snapshot needs, as the analysis named them. Checked again at every run.
    model_refs: list[dict] = []
    # Digest over the prompt and model references together, so editing either one is detected.
    snapshot_sha256: str
    prompt: dict


def snapshot_digest(prompt: dict, model_refs: list[dict]) -> str:
    return _digest({"prompt": prompt, "model_refs": model_refs})


def model_status(refs: list[dict], info) -> list[dict]:
    """Check each model reference against the live file list in the node definitions. Same rules as analysis.

    'unknown' means the list was not available (for example, ComfyUI is not running). A run refuses unknown.
    """
    out = []
    for r in refs:
        nd = info.defs.get(r["class_type"]) if getattr(info, "defs", None) else None
        spec = nd.input(r["input"]) if nd else None
        choices = spec.choices if spec is not None else None
        value = r["file"]
        if choices is None or not choices:
            status, match = "unknown", None
        elif value in choices:
            status, match = "available", value
        else:
            base = Path(str(value)).name.lower()
            alts = [c for c in choices if isinstance(c, str) and Path(c).name.lower() == base]
            if len(alts) == 1:
                status, match = "found_elsewhere", alts[0]
            elif alts:
                status, match = "ambiguous", None
            else:
                status, match = "missing", None
        out.append({**r, "status": status, "comfy_name": match})
    return out


class ModuleStore:
    """<root>/<module_id>/v<version>.json. Each file is written once and never replaced."""

    def __init__(self, root: Path):
        self.root = root

    def _dir(self, mid: str) -> Path:
        if not _ID.match(mid or ""):
            raise ForgeError("unknown module", 404)
        return self.root / mid

    def versions(self, mid: str) -> list[int]:
        d = self._dir(mid)
        if not d.is_dir():
            return []
        return sorted(int(p.stem[1:]) for p in d.glob("v*.json") if p.stem[1:].isdigit())

    def get(self, mid: str, version: int | None = None) -> Contract:
        vs = self.versions(mid)
        if not vs:
            raise ForgeError("unknown module", 404)
        v = vs[-1] if version is None else version
        p = self._dir(mid) / f"v{v}.json"
        if not p.is_file():
            raise ForgeError(f"module has no version {v}", 404)
        try:
            c = Contract.model_validate_json(p.read_text(encoding="utf-8"))
        except (ValueError, OSError) as e:
            raise ForgeError(f"stored module version {v} cannot be read: {type(e).__name__}", 409)
        if snapshot_digest(c.prompt, c.model_refs) != c.snapshot_sha256:
            raise ForgeError(f"stored module version {v} has changed since it was saved", 409)
        return c

    def list(self) -> list[dict]:
        out = []
        if self.root.is_dir():
            for d in sorted(self.root.iterdir()):
                if _ID.match(d.name) and self.versions(d.name):
                    c = self.get(d.name)
                    out.append({"module_id": c.module_id, "name": c.name, "latest_version": c.version,
                                "versions": self.versions(d.name), "workflow_id": c.workflow_id,
                                "inputs": len(c.inputs), "outputs": len(c.outputs)})
        return sorted(out, key=lambda r: r["name"].lower())

    def save(self, c: Contract) -> Contract:
        d = self._dir(c.module_id)
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"v{c.version}.json"
        if path.exists():
            raise ForgeError(f"module version {c.version} already exists; save a new version instead", 409)
        expected = (self.versions(c.module_id) or [0])[-1] + 1
        if c.version != expected:
            raise ForgeError(f"the next version is {expected}", 409)
        _atomic_write(path, c.model_dump_json(indent=2), keep_backup=False)
        return c


def new_contract(module_id: str, version: int, name: str, description: str, workflow: dict, prompt: dict,
                 inputs: list[Port], outputs: list[OutputPort], model_refs: list[dict] | None = None) -> Contract:
    """Build and check a contract. Raises ModuleError with every problem listed, so the caller can show them all."""
    problems = check(inputs, outputs, prompt)
    if problems:
        raise ModuleError("; ".join(problems))
    refs = [{"node": r["node"], "input": r["input"], "class_type": r["class_type"], "file": r["file"]} for r in (model_refs or [])]
    return Contract(module_id=module_id, version=version, name=name.strip(), description=description.strip(),
                    workflow_id=workflow["id"], workflow_name=workflow.get("name", ""),
                    source_sha256=workflow.get("sha256", ""), created_at=now_iso(),
                    inputs=inputs, outputs=outputs, model_refs=refs,
                    snapshot_sha256=snapshot_digest(prompt, refs), prompt=prompt)
