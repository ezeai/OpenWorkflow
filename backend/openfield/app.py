"""FastAPI application factory. Loopback only; the UI is served from the built frontend when present."""
from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from . import __version__, hardware, logging_setup
from .bind_guard import LoopbackOnly
from .api_models import build_router
from .api_projects import build_router as build_projects_router
from .api_director import build_router as build_director_router
from .api_generation import build_router as build_generation_router
from .api_continuity import build_router as build_continuity_router
from .api_export import build_router as build_export_router
from .api_forge import build_router as build_forge_router
from .api_forge import on_app_terminal as forge_on_app_terminal
from .api_moss import router as moss_router
from .generation.builders import build_graph
from .generation.jobs import JobEngine, JobStore
from .generation.prompt import compose
from .generation.resolve import resolve
from .comfy import ComfyClient
from .config import REPO_ROOT, Settings, SettingsStore
from .models.catalog import load_catalog
from .models.downloads import DownloadManager
from .models.store import ModelRegistryStore
from .services import Services
from .wizard import WizardStore
from .workflows.registry import load_workflows

FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"

# Windows resolves MIME types from the registry, where .js is sometimes text/plain; browsers then refuse module scripts.
for _ext, _type in ((".js", "text/javascript"), (".mjs", "text/javascript"), (".css", "text/css"), (".svg", "image/svg+xml"), (".json", "application/json")):
    mimetypes.add_type(_type, _ext)


def _record_smoke_test(services, job: dict) -> None:
    """A completed real generation is the evidence for 'smoke-tested'. The record names the exact files used."""
    reg = services.registry
    data = reg.load()
    data.smoke_tests.append({
        "workflow_id": job["workflow_id"], "workflow_version": job["workflow_version"],
        "model_id": "z-image-turbo" if job["workflow_id"] == "img-z-image-turbo" else None,
        "job_id": job["id"], "output_asset_id": job["output_asset_id"], "seed": job["params"].get("seed"),
        "size": [job["params"].get("width"), job["params"].get("height")], "steps": job["params"].get("steps"),
        "models": [{"role": m["role"], "name": m["name"], "identity_confirmed": m["identity_confirmed"]} for m in job["models"]],
        "completed_at": job["finished_at"], "app_version": job["app_version"],
    })
    reg.save(data)


def create_app(home: Path | None = None) -> FastAPI:
    store = SettingsStore(home)
    log = logging_setup.setup(store.home)
    app = FastAPI(title="Open Workflow", version=__version__)
    app.add_middleware(LoopbackOnly)
    app.state.store = store
    app.state.services = Services(
        home=store.home,
        settings_store=store,
        registry=ModelRegistryStore(store.home / "models"),
        catalog=load_catalog(),
        workflows=load_workflows(),
        wizard=WizardStore(store.home),
        downloads=DownloadManager(store.home / "models" / "managed"),
    )
    app.include_router(build_router())
    app.include_router(build_projects_router())
    app.include_router(build_director_router())
    app.include_router(build_generation_router())
    app.include_router(build_continuity_router())
    app.include_router(build_export_router())
    app.include_router(build_forge_router())
    app.include_router(moss_router)
    jobs = JobEngine(
        store=JobStore(store.home),
        projects_fn=app.state.services.projects,
        engine_url=lambda: app.state.services.settings().comfy_url,
        resolver=lambda wid: resolve(app.state.services, wid),
        build_graph=build_graph,
        compose=compose,
        on_complete=lambda job: _record_smoke_test(app.state.services, job),
        app_output_dir=lambda jid: store.home / "forge" / "runs" / jid,
        on_app_terminal=lambda job: forge_on_app_terminal(app, job),
    )
    app.state.jobs = jobs
    jobs.recover()   # jobs left active by a previous process are marked, never re-run
    jobs.start()

    def settings() -> Settings:
        return store.load()

    @app.get("/api/health")
    def health():
        s = settings()
        return {"app": "openfield", "version": __version__, "storage_home": str(store.home),
                "engine": ComfyClient(s.comfy_url).health(),
                "open_weights_path": not s.allow_remote_integrations,
                "first_run_complete": s.first_run_complete}

    @app.get("/api/hardware")
    def hw():
        s = settings()
        return hardware.snapshot([store.home, s.projects_dir, *s.model_dirs], s.ffmpeg_path)

    @app.get("/api/settings")
    def get_settings():
        return settings()

    @app.put("/api/settings")
    def put_settings(new: dict):
        try:
            merged = Settings.model_validate({**settings().model_dump(), **new})
        except ValidationError as e:
            raise HTTPException(422, detail=[{"field": ".".join(map(str, x["loc"])), "message": x["msg"]} for x in e.errors()])
        log.info("settings updated: %s", sorted(new))
        return store.save(merged)

    if FRONTEND_DIST.is_dir():
        app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

        @app.get("/{path:path}")
        def spa(path: str):
            f = (FRONTEND_DIST / path).resolve()
            if path and FRONTEND_DIST.resolve() in f.parents and f.is_file():
                return FileResponse(f)
            return FileResponse(FRONTEND_DIST / "index.html")

    return app


app = create_app()
