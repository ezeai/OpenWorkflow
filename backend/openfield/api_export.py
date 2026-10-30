"""Export, import and diagnostics routes."""
from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from . import __version__, hardware
from .projects import exporting
from .projects.store import ProjectError, now_iso

router = APIRouter(prefix="/api")
SECRET = re.compile(r"(?i)(api[_-]?key|token|password|secret|authorization)(\s*[=:]\s*)([^\s,;'\"]+)")


def _redact(text: str) -> str:
    return SECRET.sub(r"\1\2[REDACTED]", text)


def build_router() -> APIRouter:

    @router.get("/projects/{pid}/export")
    def export(pid: str, request: Request, include_assets: bool = True):
        app = request.app
        services = app.state.services
        store = services.projects()
        try:
            data = exporting.build_export(store, pid, app.state.jobs.store.all(), services.catalog, services.workflows,
                                          include_assets, _build_graph())
        except ProjectError as e:
            raise HTTPException(getattr(e, "status", 400), detail=str(e))
        name = re.sub(r"[^A-Za-z0-9_-]+", "_", store.read(pid).title)[:60] or "project"
        return Response(content=data, media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="openworkflow-{name}.zip"'})

    @router.post("/projects/import", status_code=201)
    async def import_(request: Request):
        store = request.app.state.services.projects()
        data = await request.body()
        try:
            pid = exporting.import_project(store, data)
        except ProjectError as e:
            raise HTTPException(getattr(e, "status", 400), detail=str(e))
        return {"project_id": pid, "note": "Imported as a new project. Models are not included; see manifests/models.json."}

    @router.get("/diagnostics")
    def diagnostics(request: Request):
        """A zip for bug reports: versions, hardware, recent job states, and the log tail with secrets removed."""
        app = request.app
        s = app.state.services.settings()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            info = {"openfield_version": __version__, "created_at": now_iso(), "storage_home": str(app.state.services.home),
                    "engine_url": s.comfy_url, "comfy_root_set": bool(s.comfy_root),
                    "catalog_errors": app.state.services.catalog.errors, "workflow_errors": app.state.services.workflows.errors,
                    "note": "Settings values other than paths are omitted. Nothing in this file is sent anywhere."}
            z.writestr("info.json", json.dumps(info, indent=2))
            z.writestr("hardware.json", json.dumps(hardware.snapshot([], s.ffmpeg_path), indent=2))
            jobs = [{k: j.get(k) for k in ("id", "state", "workflow_id", "workflow_version", "created_at", "finished_at", "error")}
                    for j in app.state.jobs.store.all()[:50]]
            z.writestr("jobs.json", json.dumps(jobs, indent=2))
            log = Path(app.state.services.home) / "logs" / "openworkflow.log"
            if log.exists():
                tail = log.read_text(encoding="utf-8", errors="replace").splitlines()[-500:]
                z.writestr("log-tail.txt", _redact("\n".join(tail)))
        return Response(content=buf.getvalue(), media_type="application/zip",
                        headers={"Content-Disposition": 'attachment; filename="openworkflow-diagnostics.zip"'})

    return router


def _build_graph():
    from .generation.builders import build_graph
    return build_graph
