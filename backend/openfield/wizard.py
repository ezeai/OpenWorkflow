"""First-run wizard state. A step is recorded only after the server verifies its underlying action.

Persisted in <home>/wizard.json. Completing the wizard does not force it to reappear;
it can be rerun from Settings, which only clears the progress record.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .models.store import _atomic_write, now_iso

STEPS = ["welcome", "system_check", "model_discovery", "workflow_readiness", "starter", "download", "completion"]


class WizardError(Exception):
    pass


@dataclass
class WizardState:
    completed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    starter_workflow: str | None = None
    updated_at: str | None = None


class WizardStore:
    def __init__(self, home: Path):
        self.path = Path(home) / "wizard.json"

    def load(self) -> WizardState:
        if not self.path.exists():
            return WizardState()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            st = WizardState(
                completed=[s for s in raw.get("completed", []) if s in STEPS],
                skipped=[s for s in raw.get("skipped", []) if s in STEPS],
                starter_workflow=raw.get("starter_workflow") if isinstance(raw.get("starter_workflow"), str) else None,
                updated_at=raw.get("updated_at"),
            )
            return st
        except (OSError, ValueError, AttributeError):
            try:
                self.path.replace(self.path.with_name("wizard.json.corrupt"))
            except OSError:
                pass
            return WizardState()

    def save(self, st: WizardState) -> None:
        st.updated_at = now_iso()
        _atomic_write(self.path, json.dumps(st.__dict__, indent=2))

    def reset(self) -> WizardState:
        if self.path.exists():
            self.path.unlink()
        return WizardState()


def mark_done(st: WizardState, step: str, verified: bool, reason: str = "") -> WizardState:
    """Record a step only when the caller has verified the underlying action."""
    if step not in STEPS:
        raise WizardError(f"unknown step {step!r}")
    if not verified:
        raise WizardError(f"step '{step}' cannot be completed yet: {reason or 'its action has not succeeded'}")
    if step not in st.completed:
        st.completed.append(step)
    if step in st.skipped:
        st.skipped.remove(step)
    return st


def mark_skipped(st: WizardState, step: str) -> WizardState:
    if step not in STEPS:
        raise WizardError(f"unknown step {step!r}")
    if step not in st.skipped and step not in st.completed:
        st.skipped.append(step)
    return st


def recommend_starter(evaluations: list[dict]) -> dict:
    """Smallest locally usable workflow among those whose requirements are met or nearly met.

    Never recommends a license-blocked workflow. If nothing qualifies, returns the
    blockers so the wizard can explain them instead of inventing a choice.
    """
    usable_states = ("validated", "requirements_met_untested", "unconfirmed_files")
    candidates = [e for e in evaluations if e["status"] in usable_states]
    if candidates:
        def weight(e: dict) -> tuple:
            order = {"validated": 0, "requirements_met_untested": 1, "unconfirmed_files": 2}[e["status"]]
            return (order, len(e["unconfirmed_files"]) + len(e["missing_models"]))
        best = sorted(candidates, key=weight)[0]
        return {"workflow_id": best["id"], "status": best["status"], "reason": _reason(best)}
    blocked = [e for e in evaluations if e["status"] == "license_blocked"]
    return {"workflow_id": None, "status": None,
            "reason": "No workflow meets its requirements yet.",
            "blockers": [b for e in blocked for b in e["blockers"]],
            "next_steps": _next_steps(evaluations)}


def _reason(e: dict) -> str:
    if e["status"] == "unconfirmed_files":
        return "Files were found but their identity is not confirmed yet. Confirm them in the Model Library."
    if e["status"] == "requirements_met_untested":
        return "Required files and nodes are present. It has not been run on this machine yet."
    return "Validated."


def _next_steps(evaluations: list[dict]) -> list[str]:
    steps: list[str] = []
    for e in evaluations:
        if e["status"] == "missing_models":
            steps.append(f"{e['name']}: missing {len(e['missing_models'])} required file(s). Point Open Workflow at the folder that holds them, or download a verified source.")
        elif e["status"] == "missing_packs":
            steps.append(f"{e['name']}: install custom-node pack(s) {', '.join(e['missing_packs'])} in ComfyUI.")
        elif e["status"] == "unknown_engine":
            steps.append("Set your ComfyUI folder in Settings so node availability can be checked.")
    return steps[:5]
