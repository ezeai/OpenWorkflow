"""Generation routes: start a generation from a shot, list and control jobs.

Only workflows with a graph builder can generate. Requests never carry file paths.
"""
from __future__ import annotations

import secrets

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .generation.builders import GENERATABLE
from .generation.comfy import ComfyRunner
from .projects.store import Conflict, ProjectError
from .workflows.registry import load_workflows

router = APIRouter(prefix="/api")
SIZES = (512, 768)


class GenerateBody(BaseModel):
    base_revision: int
    seed: int | None = Field(default=None, ge=0, le=2**31 - 1)
    width: int = 512
    height: int = 512
    steps: int = Field(default=8, ge=1, le=50)


class RetryBody(BaseModel):
    pass


def _jobs(request: Request):
    return request.app.state.jobs


def start_shot_generation(app, pid: str, sid: str, base_revision: int | None, *, seed: int | None = None,
                          width: int = 512, height: int = 512, steps: int = 8) -> dict:
    """The one generation entry point. The Director and the canvas both call this, so the job behaves the same."""
    services = app.state.services
    store = services.projects()
    try:
        meta = store.read(pid)
        from .projects import director as d
        shot = d.find_shot(meta, sid)
    except (ProjectError, KeyError) as e:
        raise HTTPException(getattr(e, "status", 404), detail="shot not found" if isinstance(e, KeyError) else str(e))
    if not shot.workflow_id or shot.workflow_id not in GENERATABLE:
        raise HTTPException(400, detail="no generator is connected for this shot's workflow yet")
    if width not in SIZES or height not in SIZES:
        raise HTTPException(400, detail=f"size must be one of {SIZES} for this workflow")
    if not 1 <= steps <= 50:
        raise HTTPException(400, detail="steps must be between 1 and 50")
    version = next(w.version for w in load_workflows().items.values() if w.id == shot.workflow_id)
    params = {"workflow_id": shot.workflow_id, "workflow_version": version, "width": width, "height": height,
              "seed": seed if seed is not None else secrets.randbelow(2**31), "steps": steps, "cfg": 1.0}
    try:
        return app.state.jobs.submit(pid, sid, base_revision, params)
    except Conflict as e:
        raise HTTPException(409, detail=str(e))
    except (ProjectError, ValueError, KeyError) as e:
        raise HTTPException(getattr(e, "status", 400), detail=str(e))


def preflight_shot_generation(app, pid: str, sid: str, *, seed: int | None, width: int, height: int, steps: int) -> dict:
    """What a generation would send, checked without queuing it.

    Reports blocking problems (engine unreachable, missing model files, nodes ComfyUI does not have) and
    returns the prompt, notes and model files the job would use. It never submits a graph or writes a job.
    """
    from .comfy import ComfyClient
    from .generation.comfy import EngineError

    services = app.state.services
    engine = app.state.jobs
    try:
        meta = services.projects().read(pid)
        from .projects import director as d
        shot = d.find_shot(meta, sid)
    except (ProjectError, KeyError, ValueError) as e:
        raise HTTPException(getattr(e, "status", 404), detail="shot not found" if isinstance(e, KeyError) else str(e))
    if not shot.workflow_id or shot.workflow_id not in GENERATABLE:
        raise HTTPException(400, detail="no generator is connected for this shot's workflow yet")
    if width not in SIZES or height not in SIZES:
        raise HTTPException(400, detail=f"size must be one of {SIZES} for this workflow")
    if not 1 <= steps <= 50:
        raise HTTPException(400, detail="steps must be between 1 and 50")
    version = next(w.version for w in load_workflows().items.values() if w.id == shot.workflow_id)
    params = {"workflow_id": shot.workflow_id, "workflow_version": version, "width": width, "height": height,
              "seed": seed if seed is not None else 0, "steps": steps, "cfg": 1.0}

    problems: list[str] = []
    url = engine.engine_url()
    if not ComfyRunner(url).reachable():
        problems.append("ComfyUI is not running on the configured address. Start it before generating.")

    models: list[dict] = []
    graph_args: dict | None = None
    try:
        resolved = engine.resolver(shot.workflow_id)
        models, graph_args = resolved.models, resolved.graph_args
    except ValueError as e:
        problems.append(str(e))

    positive = negative = ""
    notes: list[str] = []
    graph: dict = {}
    try:
        positive, negative, notes = engine.compose(meta, shot)
        if graph_args is not None:
            graph = engine.build_graph(shot.workflow_id, positive, negative, params, graph_args)
    except (KeyError, ValueError) as e:
        problems.append(f"could not build the graph: {e}")

    missing_nodes: list[str] = []
    if graph and not problems:
        try:
            known = ComfyClient(url).object_info()
            missing_nodes = sorted({n["class_type"] for n in graph.values() if n["class_type"] not in known})
        except (EngineError, OSError, ValueError):
            problems.append("could not read ComfyUI's node list to check the graph")
        if missing_nodes:
            problems.append("ComfyUI does not have these nodes: " + ", ".join(missing_nodes))

    return {
        "ready": not problems,
        "problems": problems,
        "params": params,
        "prompt": {"positive": positive, "negative": negative},
        "notes": list(notes),
        "models": models,
        "nodes_checked": len({n["class_type"] for n in graph.values()}) if graph else 0,
    }


def _view(job: dict) -> dict:
    return {k: job.get(k) for k in (
        "id", "state", "project_id", "shot_id", "take_id", "workflow_id", "workflow_version", "params", "created_at",
        "started_at", "finished_at", "updated_at", "models", "error", "output_asset_id", "progress", "notes", "history",
        "app_version", "prompt", "kind", "output_file", "provider")}


def build_router() -> APIRouter:

    @router.get("/generation/status")
    def status(request: Request):
        jobs = _jobs(request)
        runner = ComfyRunner(request.app.state.services.settings().comfy_url)
        return {"engine_reachable": runner.reachable(), "worker_alive": jobs.worker_alive(),
                "generatable_workflows": sorted(GENERATABLE),
                "note": "Generation runs one job at a time on the GPU. Camera settings are prompt guidance."}

    @router.post("/projects/{pid}/shots/{sid}/generate", status_code=202)
    def generate(pid: str, sid: str, body: GenerateBody, request: Request):
        return _view(start_shot_generation(request.app, pid, sid, body.base_revision, seed=body.seed,
                                           width=body.width, height=body.height, steps=body.steps))

    @router.post("/projects/{pid}/shots/{sid}/preflight")
    def preflight(pid: str, sid: str, body: GenerateBody, request: Request):
        return preflight_shot_generation(request.app, pid, sid, seed=body.seed, width=body.width,
                                         height=body.height, steps=body.steps)

    @router.get("/jobs")
    def list_jobs(request: Request):
        return {"jobs": [_view(j) for j in _jobs(request).store.all()[:100]]}

    @router.get("/jobs/{jid}")
    def get_job(jid: str, request: Request):
        try:
            return _view(_jobs(request).store.load(jid))
        except KeyError:
            raise HTTPException(404, detail="job not found")

    @router.post("/jobs/{jid}/cancel")
    def cancel(jid: str, request: Request):
        try:
            return _view(_jobs(request).request_cancel(jid))
        except KeyError:
            raise HTTPException(404, detail="job not found")
        except ValueError as e:
            raise HTTPException(409, detail=str(e))

    @router.post("/jobs/{jid}/import")
    def import_output(jid: str, request: Request):
        try:
            return _view(_jobs(request).import_recovered(jid))
        except KeyError:
            raise HTTPException(404, detail="job not found")
        except ValueError as e:
            raise HTTPException(409, detail=str(e))

    @router.post("/jobs/{jid}/retry", status_code=202)
    def retry(jid: str, request: Request):
        """A retry is a new job on a new take. Earlier takes and their outputs are kept."""
        jobs = _jobs(request)
        try:
            old = jobs.store.load(jid)
        except KeyError:
            raise HTTPException(404, detail="job not found")
        if old["state"] not in ("FAILED", "CANCELLED", "INTERRUPTED"):
            raise HTTPException(409, detail="only failed, cancelled or interrupted jobs can be retried")
        if old.get("kind") == "app":
            raise HTTPException(409, detail="run the app again from its page; app runs are not retried here")
        try:
            job = jobs.submit(old["project_id"], old["shot_id"], None, dict(old["params"], workflow_id=old["workflow_id"],
                                                                           workflow_version=old["workflow_version"]))
        except (ProjectError, ValueError, KeyError) as e:
            raise HTTPException(getattr(e, "status", 400), detail=str(e))
        return _view(job)

    return router
