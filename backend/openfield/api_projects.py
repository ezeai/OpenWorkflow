"""Project, entity and asset routes. Writes need the revision the client last saw (optimistic concurrency).

Paths are never accepted from clients for files. Images are sent as the request body
and identified by their bytes. Files are served only from inside the project's asset folder.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .projects import AssetError, AssetRegistry, FIELDS, REF_ROLES, ProjectError, ProjectStore
from .projects import entities as ent
from .projects.assets import MAX_IMAGE_BYTES
from .projects.schema import ProjectMeta
from .projects.store import Conflict

router = APIRouter(prefix="/api")

KIND_LABELS = {"character": "Characters", "world": "Worlds", "style": "Styles", "prop": "Props"}


class CreateProject(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=4000)


class PatchProject(BaseModel):
    base_revision: int
    title: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=4000)


class DeleteProject(BaseModel):
    confirm_title: str


class CreateEntity(BaseModel):
    base_revision: int
    kind: str
    name: str = Field(min_length=1, max_length=120)


class PatchEntity(BaseModel):
    base_revision: int
    name: str | None = None
    fields: dict | None = None
    refs: list | None = None
    archived: bool | None = None


class RestoreBody(BaseModel):
    base_revision: int
    version: int


class VerifyBody(BaseModel):
    deep: bool = False


def _store(request: Request) -> ProjectStore:
    return request.app.state.services.projects()


def _err(e: Exception) -> HTTPException:
    """Map domain errors to HTTP. ValueError covers schema validation (pydantic is a ValueError)."""
    return HTTPException(getattr(e, "status", 400), detail=str(e))


def _asset_view(reg: AssetRegistry, a) -> dict:
    v = reg.verify(a)
    return {**a.model_dump(), "status": v["status"], "status_detail": v["detail"]}


def _project_view(store: ProjectStore, meta: ProjectMeta, note: str | None) -> dict:
    reg = AssetRegistry(store.dir(meta.id))
    af, _ = reg.load()
    return {
        "id": meta.id,
        "title": meta.title,
        "description": meta.description,
        "created_at": meta.created_at,
        "updated_at": meta.updated_at,
        "revision": meta.revision,
        "recovery_note": note,
        "entities": [e.model_dump() for e in meta.entities],
        "assets": [_asset_view(reg, a) for a in af.assets],
        "field_labels": FIELDS,
        "ref_roles": REF_ROLES,
    }


def build_router() -> APIRouter:

    @router.get("/entity-kinds")
    def kinds():
        return {"kinds": [{"id": k, "label": KIND_LABELS[k], "fields": FIELDS[k], "ref_roles": REF_ROLES[k]} for k in FIELDS]}

    @router.get("/projects")
    def list_projects(request: Request):
        return {"projects": [s.__dict__ for s in _store(request).list()]}

    @router.post("/projects", status_code=201)
    def create_project(body: CreateProject, request: Request):
        store = _store(request)
        try:
            meta = store.create(body.title, body.description)
        except (ProjectError, ValueError) as e:
            raise _err(e)
        return _project_view(store, meta, None)

    @router.get("/projects/{pid}")
    def get_project(pid: str, request: Request):
        store = _store(request)
        try:
            meta, note = store.read_with_note(pid)
        except (ProjectError, ValueError) as e:
            raise _err(e)
        return _project_view(store, meta, note)

    @router.patch("/projects/{pid}")
    def patch_project(pid: str, body: PatchProject, request: Request):
        store = _store(request)

        def change(meta: ProjectMeta):
            if body.base_revision != meta.revision:
                raise Conflict(f"project changed elsewhere (revision {meta.revision}); reload to see the latest")
            if body.title is not None:
                t = body.title.strip()
                if not t:
                    raise ProjectError("title cannot be empty")
                meta.title = t
            if body.description is not None:
                meta.description = body.description
        try:
            _, note = store.mutate(pid, change)
            meta, _ = store.read_with_note(pid)
        except (ProjectError, ValueError) as e:
            raise _err(e)
        return _project_view(store, meta, note)

    @router.post("/projects/{pid}/delete")
    def delete_project(pid: str, body: DeleteProject, request: Request):
        store = _store(request)
        try:
            store.delete(pid, body.confirm_title)
        except (ProjectError, ValueError) as e:
            raise _err(e)
        return {"deleted": pid, "note": "The project folder and its assets were removed. This cannot be undone."}

    @router.post("/projects/{pid}/entities", status_code=201)
    def create_entity(pid: str, body: CreateEntity, request: Request):
        store = _store(request)

        def change(meta: ProjectMeta):
            if body.base_revision != meta.revision:
                raise Conflict(f"project changed elsewhere (revision {meta.revision}); reload to see the latest")
            return ent.create(meta, body.kind, body.name).id
        try:
            eid, _ = store.mutate(pid, change)
            meta, note = store.read_with_note(pid)
        except (ProjectError, ValueError) as e:
            raise _err(e)
        return {"entity_id": eid, "project": _project_view(store, meta, note)}

    @router.patch("/projects/{pid}/entities/{eid}")
    def patch_entity(pid: str, eid: str, body: PatchEntity, request: Request):
        store = _store(request)
        reg = AssetRegistry(store.dir(pid))
        try:
            af, _ = reg.load()
            asset_ids = {a.id for a in af.assets}

            def change(meta: ProjectMeta):
                ent.update(meta, eid, body.base_revision, name=body.name, fields=body.fields,
                           refs=body.refs, archived=body.archived, asset_ids=asset_ids)
            store.mutate(pid, change)
            meta, note = store.read_with_note(pid)
        except (ProjectError, AssetError, ValueError) as e:
            raise _err(e)
        return _project_view(store, meta, note)

    @router.get("/projects/{pid}/entities/{eid}/versions")
    def versions(pid: str, eid: str, request: Request):
        store = _store(request)
        try:
            meta = store.read(pid)
            e = ent.find(meta, eid)
        except (ProjectError, ValueError) as err:
            raise _err(err)
        return {"current_version": e.current_version, "versions": [v.model_dump() for v in reversed(e.versions)]}

    @router.post("/projects/{pid}/entities/{eid}/restore")
    def restore(pid: str, eid: str, body: RestoreBody, request: Request):
        store = _store(request)

        def change(meta: ProjectMeta):
            ent.restore(meta, eid, body.base_revision, body.version)
        try:
            store.mutate(pid, change)
            meta, note = store.read_with_note(pid)
        except (ProjectError, ValueError) as e:
            raise _err(e)
        return _project_view(store, meta, note)

    # ---- assets ----
    @router.post("/projects/{pid}/assets", status_code=201)
    async def upload(pid: str, request: Request, label: str = ""):
        store = _store(request)
        try:
            store.read(pid)  # project must exist
        except (ProjectError, ValueError) as e:
            raise _err(e)
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > MAX_IMAGE_BYTES:
            raise HTTPException(413, detail=f"image is larger than {MAX_IMAGE_BYTES // (1024 * 1024)} MB")
        data = await request.body()
        if len(data) > MAX_IMAGE_BYTES:
            raise HTTPException(413, detail=f"image is larger than {MAX_IMAGE_BYTES // (1024 * 1024)} MB")
        try:
            asset = AssetRegistry(store.dir(pid)).import_bytes(data, label=label)
        except (AssetError, ValueError) as e:
            raise _err(e)
        return {"asset": asset.model_dump()}

    @router.get("/projects/{pid}/assets/{aid}/file")
    def asset_file(pid: str, aid: str, request: Request):
        store = _store(request)
        reg = AssetRegistry(store.dir(pid))
        try:
            a = reg.get(aid)
            path: Path = reg.file_path(a)
        except (ProjectError, AssetError, ValueError) as e:
            raise _err(e)
        return FileResponse(path, media_type=a.mime, headers={"Cache-Control": "private, max-age=3600"})

    @router.post("/projects/{pid}/assets/{aid}/verify")
    def verify_asset(pid: str, aid: str, body: VerifyBody, request: Request):
        store = _store(request)
        reg = AssetRegistry(store.dir(pid))
        try:
            a = reg.get(aid)
        except (ProjectError, AssetError, ValueError) as e:
            raise _err(e)
        return {"asset_id": aid, **reg.verify(a, deep=body.deep), "deep": body.deep}

    return router
