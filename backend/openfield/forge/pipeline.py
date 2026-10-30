"""Pipelines (Milestone 4, first slice): module stages that run one after another, on this computer.

A stage's image output is handed to the next stage by re-upload. The file goes to ComfyUI's input folder through the
existing upload path, and the next stage's image input receives the name ComfyUI stores it under. Stages do not run in
parallel and do not branch. A stage that fails or is cancelled ends the run, and later stages are never submitted.

Every stage is a pinned module version, so a new version of a module never changes an existing pipeline.
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, Field, field_validator

from ..projects.store import _atomic_write, now_iso
from . import module, uploads
from .module import ModuleError
from .store import ForgeError

_ID = re.compile(r"^[0-9a-f]{32}$")
MAX_STAGES = 20


def is_ref(v) -> bool:
    """A value that means 'the output of an earlier stage', not a literal."""
    return isinstance(v, dict) and set(v) == {"from_stage", "output"}


class StageSpec(BaseModel):
    id: str
    module_id: str
    version: int = Field(ge=1)
    values: dict = {}

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not module._NAME.match(v):
            raise ValueError("stage ids use lowercase letters, digits and underscores, starting with a letter")
        return v


class PipelineSpec(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    stages: list[StageSpec] = Field(min_length=1, max_length=MAX_STAGES)


class PipelineStore:
    """<root>/<id>.json. Saving replaces the draft and bumps its revision; runs keep the spec they started with."""

    def __init__(self, root: Path):
        self.root = root

    def _path(self, pid: str) -> Path:
        if not _ID.match(pid or ""):
            raise ForgeError("unknown pipeline", 404)
        return self.root / f"{pid}.json"

    def get(self, pid: str) -> dict:
        p = self._path(pid)
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise ForgeError("unknown pipeline", 404)

    def list(self) -> list[dict]:
        out = []
        if self.root.is_dir():
            for p in sorted(self.root.glob("*.json")):
                try:
                    out.append(self.get(p.stem))
                except (ForgeError, ValueError, OSError):
                    continue
        return sorted(out, key=lambda r: r["name"].lower())

    def save(self, spec: PipelineSpec, pid: str | None = None) -> dict:
        pid = pid or uuid.uuid4().hex
        revision = 1
        if pid and self._path(pid).exists():
            revision = self.get(pid)["revision"] + 1
        rec = {"id": pid, "name": spec.name.strip(), "revision": revision, "updated_at": now_iso(),
               "stages": [s.model_dump() for s in spec.stages]}
        self.root.mkdir(parents=True, exist_ok=True)
        _atomic_write(self._path(pid), json.dumps(rec, indent=2), keep_backup=False)
        return rec


def validate(stages: list[StageSpec], modules: module.ModuleStore) -> list[str]:
    """Every problem with the stages. Each value must fit its port, and each stage reference must point at an
    earlier stage's output through an image input."""
    problems: list[str] = []
    index: dict[str, int] = {}
    for i, s in enumerate(stages):
        if s.id in index:
            problems.append(f"stage id '{s.id}' is used twice")
        index.setdefault(s.id, i)
    contracts: dict[int, module.Contract] = {}
    for i, s in enumerate(stages):
        try:
            contracts[i] = modules.get(s.module_id, s.version)
        except ForgeError as e:
            problems.append(f"stage '{s.id}': {e}")
    for i, s in enumerate(stages):
        if i not in contracts:
            continue
        c = contracts[i]
        ports = {p.name: p for p in c.inputs}
        for name in s.values:
            if name not in ports:
                problems.append(f"stage '{s.id}' has no input '{name}'")
        for p in c.inputs:
            if p.name not in s.values:
                if p.required and p.default is None:
                    problems.append(f"stage '{s.id}' input '{p.name}' is required")
                continue
            v = s.values[p.name]
            if is_ref(v):
                if p.type != "image":
                    problems.append(f"stage '{s.id}' input '{p.name}' is not an image, so it cannot take another stage's output")
                    continue
                j = index.get(v["from_stage"])
                if j is None:
                    problems.append(f"stage '{s.id}' input '{p.name}' takes the output of stage '{v['from_stage']}', which does not exist")
                elif j >= i:
                    problems.append(f"stage '{s.id}' input '{p.name}' must take the output of an earlier stage, not '{v['from_stage']}'")
                elif j in contracts and v["output"] not in [o.name for o in contracts[j].outputs]:
                    problems.append(f"stage '{s.id}' input '{p.name}' takes output '{v['output']}', which stage '{v['from_stage']}' does not have")
            else:
                try:
                    module._check_value(p, v)
                except ModuleError as e:
                    problems.append(f"stage '{s.id}' input '{p.name}': {e}")
    return problems


def submit_version(jobs, c: module.Contract, values: dict, info, comfy_url: str, has_input, extra_provenance: dict) -> dict:
    """The one way a module version is sent to ComfyUI. Refuses before submitting if anything is not checked."""
    prompt = module.prepare(c.inputs, c.prompt, values)
    for p in c.inputs:
        if p.type == "image" and p.name in values and not has_input(comfy_url, values[p.name]):
            raise ModuleError(f"input '{p.name}' is not in ComfyUI's input folder; upload the image again")
    statuses = module.model_status(c.model_refs, info)
    refused = [s for s in statuses if s["status"] not in ("available", "found_elsewhere")]
    if refused:
        raise ModuleError("; ".join(_model_problem(s) for s in refused))
    models = []
    for s in statuses:
        prompt[s["node"]]["inputs"][s["input"]] = s["comfy_name"]
        models.append({"role": s["input"], "node": s["node"], "file": s["file"], "comfy_name": s["comfy_name"], "status": s["status"]})
    for node in prompt.values():
        node.pop("_meta", None)
    provenance = {"module_id": c.module_id, "module_version": c.version, "module_name": c.name,
                  "snapshot_sha256": c.snapshot_sha256, "workflow_id": c.workflow_id,
                  "workflow_sha256": c.source_sha256, "workflow_name": c.workflow_name,
                  "values": values, "provider": "local", "source_format": "module", **extra_provenance}
    return jobs.submit_app(c.module_id, f"v{c.version}", prompt, models, {"seed": None, "app": c.name},
                           provenance, [], provider="local", consent=None)


def _model_problem(s: dict) -> str:
    if s["status"] == "unknown":
        return f"model file '{s['file']}' cannot be checked; start ComfyUI so its model list is available"
    if s["status"] == "ambiguous":
        return f"model file '{s['file']}' matches more than one installed file"
    return f"model file '{s['file']}' is not installed in ComfyUI"


class RunStore:
    """<root>/<run id>.json: one record per pipeline run, written as the run moves between stages."""

    def __init__(self, root: Path):
        self.root = root

    def _path(self, rid: str) -> Path:
        if not _ID.match(rid or ""):
            raise ForgeError("unknown pipeline run", 404)
        return self.root / f"{rid}.json"

    def get(self, rid: str) -> dict:
        try:
            return json.loads(self._path(rid).read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise ForgeError("unknown pipeline run", 404)

    def save(self, run: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        _atomic_write(self._path(run["id"]), json.dumps(run, indent=2), keep_backup=False)


@dataclass
class Context:
    modules: module.ModuleStore
    runs: RunStore
    jobs: Any
    info: Callable[[], Any]
    comfy_url: Callable[[], str]
    has_input: Callable[[str, str], bool]
    upload: Callable[[str, bytes, dict], str]
    output_path: Callable[[str, dict], Path]   # (job id, job) -> the saved output file
    free: Callable[[str], None]                # (comfy url) -> unload models and free memory on the ComfyUI server


def start(pipeline: dict, ctx: Context) -> dict:
    """Validate the saved pipeline and start its first stage. Raises ModuleError with every problem listed."""
    stages = [StageSpec.model_validate(s) for s in pipeline["stages"]]
    problems = validate(stages, ctx.modules)
    if problems:
        raise ModuleError("; ".join(problems))
    run = {"id": uuid.uuid4().hex, "pipeline_id": pipeline["id"], "pipeline_name": pipeline["name"],
           "pipeline_revision": pipeline["revision"], "started_at": now_iso(), "state": "running",
           "message": None, "result": None, "spec": [s.model_dump() for s in stages],
           "stages": [{"id": s.id, "state": "pending", "job_id": None, "comfy_name": None, "message": None} for s in stages]}
    ctx.runs.save(run)
    _launch(ctx, run, 0)
    return run


def _launch(ctx: Context, run: dict, idx: int) -> None:
    spec = run["spec"][idx]
    c = ctx.modules.get(spec["module_id"], spec["version"])
    values = {}
    for name, v in spec["values"].items():
        if is_ref(v):
            src = next(i for i, s in enumerate(run["spec"]) if s["id"] == v["from_stage"])
            name_in_comfy = run["stages"][src]["comfy_name"]
            if not name_in_comfy:
                raise ModuleError(f"stage '{v['from_stage']}' has not produced an image yet")
            values[name] = name_in_comfy
        else:
            values[name] = v
    job = submit_version(ctx.jobs, c, values, ctx.info(), ctx.comfy_url(), ctx.has_input,
                         {"pipeline_run_id": run["id"], "stage_index": idx, "stage_id": spec["id"]})
    run["stages"][idx].update(state="running", job_id=job["id"])
    ctx.runs.save(run)


def _fail(ctx: Context, run: dict, message: str) -> None:
    run["state"] = "failed"
    run["message"] = message
    ctx.runs.save(run)


def _feeds_later(run: dict, idx: int) -> bool:
    """True when a later stage takes this stage's output. Stages that feed nothing forward are not re-uploaded."""
    sid = run["spec"][idx]["id"]
    return any(is_ref(v) and v["from_stage"] == sid for s in run["spec"][idx + 1:] for v in s["values"].values())


def _flush(ctx: Context, run: dict) -> None:
    """Free ComfyUI's models and memory once a stage has finished, before the next stage loads its own.
    A failed flush stops the run: the next stage would otherwise start on a full card."""
    try:
        ctx.free(ctx.comfy_url())
    except Exception as e:
        _fail(ctx, run, f"could not free ComfyUI memory after a stage finished: {type(e).__name__}")


def on_terminal(job: dict, ctx: Context) -> None:
    """Called by the job engine when a Forge app job reaches a terminal state. Ignores jobs that are not pipeline stages."""
    prov = job.get("provenance") or {}
    rid = prov.get("pipeline_run_id")
    if not rid:
        return
    try:
        run = ctx.runs.get(rid)
    except ForgeError:
        return
    idx = prov.get("stage_index")
    if run["state"] != "running" or idx is None or run["stages"][idx]["job_id"] != job["id"]:
        return
    st = run["stages"][idx]
    if job["state"] != "COMPLETED":
        st.update(state=job["state"].lower(), message=job.get("error") or job["state"].lower())
        _fail(ctx, run, f"stage '{st['id']}' did not complete ({job['state'].lower()})")
        return
    st["state"] = "completed"
    try:
        if idx == len(run["stages"]) - 1:
            _flush(ctx, run)
            if run["state"] != "running":
                return
            run["state"] = "completed"
            run["result"] = {"stage_id": st["id"], "job_id": job["id"], "file": job["output_file"]["name"]}
            ctx.runs.save(run)
            return
        if _feeds_later(run, idx):
            data = ctx.output_path(job["id"], job).read_bytes()
            st["comfy_name"] = ctx.upload(ctx.comfy_url(), data, uploads.check(data))
        ctx.runs.save(run)
        _flush(ctx, run)
        if run["state"] != "running":
            return
        _launch(ctx, run, idx + 1)
    except Exception as e:  # the message of a refusal is safe to show; anything else is named by type
        message = str(e) if isinstance(e, (ModuleError, uploads.UploadError)) else f"{type(e).__name__}"
        _fail(ctx, run, f"handing stage '{st['id']}' output to the next stage failed: {message}")
