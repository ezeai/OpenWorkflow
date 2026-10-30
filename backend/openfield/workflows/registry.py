"""Workflow capability registry. Declares what a workflow needs; never claims it works.

Statuses, in precedence order:
  license_blocked          a required model is restricted for the default path
  unknown_engine           no ComfyUI root configured, so node availability cannot be checked
  missing_models           a required component file is absent
  unconfirmed_files        files exist but the user has not confirmed their identity
  missing_nodes            a required ComfyUI node class is not in the installed core
  missing_packs            a required custom-node pack folder is absent
  requirements_met_untested  everything above passes, but no validation or smoke test is recorded
  validated                a validation record exists for this workflow (none are produced yet)

Node availability is a STATIC text check of the installed ComfyUI source. It
does not start ComfyUI, so it confirms that a class name exists, not that the
node runs.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

DATA = Path(__file__).resolve().parent.parent / "data"


class ModelRef(BaseModel):
    model_id: str
    required_roles: list[str] = Field(min_length=1)


class Workflow(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9.\-]*$")
    version: str
    name: str
    task: str
    models: list[ModelRef]
    required_nodes: list[str] = []
    required_packs: list[str] = []
    inputs: list[str] = []
    supported_params: list[str] = []
    unsupported_controls: list[str] = []
    license_requirements: str
    workflow_file: str | None = None
    limitations: list[str] = []


@dataclass
class LoadedWorkflows:
    items: dict[str, Workflow] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


def load_workflows(path: Path = DATA / "workflows.json") -> LoadedWorkflows:
    out = LoadedWorkflows()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        out.errors.append(f"workflow file unreadable: {type(e).__name__}: {e}")
        return out
    if raw.get("schema_version") != 1:
        out.errors.append(f"unsupported workflow schema_version {raw.get('schema_version')!r}")
        return out
    for i, entry in enumerate(raw.get("workflows", [])):
        try:
            w = Workflow.model_validate(entry)
        except ValidationError as e:
            name = entry.get("id", f"#{i}") if isinstance(entry, dict) else f"#{i}"
            out.errors.append(f"workflow {name} skipped: {e.error_count()} validation error(s)")
            continue
        if w.id in out.items:
            out.errors.append(f"duplicate workflow id {w.id}; later entry skipped")
            continue
        out.items[w.id] = w
    return out


class CoreNodes:
    """Membership test over installed core source: a quoted mapping key or a class definition."""
    def __init__(self, blob: str):
        self.blob = blob

    def __contains__(self, name: str) -> bool:
        return f'"{name}"' in self.blob or f"class {name}(" in self.blob


def core_node_index(comfy_root: Path | None) -> CoreNodes | None:
    """Core node index for the installed ComfyUI, or None if the root or nodes.py is missing."""
    if comfy_root is None:
        return None
    src = comfy_root / "ComfyUI"
    if not (src / "nodes.py").is_file():
        return None
    texts = [(src / "nodes.py").read_text(encoding="utf-8", errors="replace")]
    extras = src / "comfy_extras"
    if extras.is_dir():
        texts += [p.read_text(encoding="utf-8", errors="replace") for p in extras.glob("*.py")]
    return CoreNodes("\n".join(texts))


def evaluate(wf: Workflow, model_status: dict[str, dict], nodes, custom_packs_present: set[str],
             validated_ids: set[str], smoke_ids: set[str] | None = None) -> dict:
    blockers: list[str] = []
    missing_models: list[dict] = []
    unconfirmed: list[dict] = []

    for ref in wf.models:
        st = model_status.get(ref.model_id)
        if st is None:
            missing_models.append({"model_id": ref.model_id, "reason": "not in catalogue"})
            continue
        if st["open_weight_eligibility"] == "not_eligible":
            blockers.append(f"{st['name']}: {st['license']['id']} ({st['license']['status']})")
        # best variant = most identified roles
        best = max(st["variants"], key=lambda v: sum(c["state"] == "identified" for c in v["components"]), default=None)
        if best is None:
            missing_models.append({"model_id": ref.model_id, "reason": "no variants"})
            continue
        states = {c["role"]: c["state"] for c in best["components"]}
        for role in ref.required_roles:
            s = states.get(role, "missing")
            if s == "missing":
                missing_models.append({"model_id": ref.model_id, "variant": best["id"], "role": role})
            elif s == "found":
                unconfirmed.append({"model_id": ref.model_id, "variant": best["id"], "role": role})

    missing_nodes = [] if nodes is None else [n for n in wf.required_nodes if n not in nodes]
    missing_packs = [p for p in wf.required_packs if p not in custom_packs_present]

    if blockers:
        status = "license_blocked"
    elif nodes is None:
        status = "unknown_engine"
    elif missing_models:
        status = "missing_models"
    elif unconfirmed:
        status = "unconfirmed_files"
    elif missing_nodes:
        status = "missing_nodes"
    elif missing_packs:
        status = "missing_packs"
    elif wf.id in validated_ids:
        status = "validated"
    else:
        status = "requirements_met_untested"

    return {
        "id": wf.id,
        "version": wf.version,
        "name": wf.name,
        "task": wf.task,
        "status": status,
        "blockers": blockers,
        "missing_models": missing_models,
        "unconfirmed_files": unconfirmed,
        "missing_nodes": missing_nodes,
        "missing_packs": missing_packs,
        "smoke_tested": wf.id in (smoke_ids or set()),  # True only after a real generation completed
        "supported_params": wf.supported_params,
        "unsupported_controls": wf.unsupported_controls,
        "license_requirements": wf.license_requirements,
        "limitations": wf.limitations,
        "workflow_file": wf.workflow_file,
    }
