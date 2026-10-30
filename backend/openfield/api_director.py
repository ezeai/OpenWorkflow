"""Director routes: script, plan proposal, shots, takes, presets and coverage suggestions.

Writes need the project revision the client last saw. Takes are created with their job
(`generation/jobs.py`) and move through requested/running/generated; approving a take is a
Director-level choice recorded here.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError

from .projects import ProjectError
from .projects import director as d
from .projects.schema import ProjectMeta, Shot, Take, new_id
from .projects.store import Conflict
from .projects.store import now_iso
from .workflows.registry import load_workflows

router = APIRouter(prefix="/api")

SHOT_FIELDS = {"shot_size", "camera_angle", "camera_move", "lens", "depth", "composition", "move_intensity",
               "duration_s", "workflow_id", "enabled", "character_ids", "location_id", "prop_ids", "texts"}


class Base(BaseModel):
    base_revision: int


class ScriptBody(Base):
    text: str = Field(max_length=200_000)


class AddShot(Base):
    scene_id: str | None = None


class PatchShot(Base):
    fields: dict = {}
    preset: str | None = None


class ReorderBody(Base):
    order: list[str]


class TakeBody(Base):
    note: str = Field(default="", max_length=500)


class ApplyBody(Base):
    picks: list[int]


def _store(request: Request):
    return request.app.state.services.projects()


def _check_rev(meta: ProjectMeta, base: int) -> None:
    if base != meta.revision:
        raise Conflict(f"project changed elsewhere (revision {meta.revision}); reload to see the latest")


def _workflow_list() -> list[dict]:
    lw = load_workflows()
    return [{"id": w.id, "name": w.name, "task": w.task, "unsupported_controls": w.unsupported_controls}
            for w in lw.items.values()]


def _shot_view(meta: ProjectMeta, s: Shot) -> dict:
    return {**s.model_dump(), "capabilities": d.capability_map(s), "warnings": d.validate_shot(meta, s)}


def _director_view(meta: ProjectMeta) -> dict:
    scenes = d.parse_script(meta.script_text)
    return {
        "revision": meta.revision,
        "script_text": meta.script_text,
        "scenes": [{"id": s["id"], "title": s["title"], "beats": len(s["beats"])} for s in scenes],
        "shots": [_shot_view(meta, s) for s in sorted(meta.shots, key=lambda x: x.number)],
        "suggestions": d.coverage_suggestions(meta),
        "presets": [{"id": k, "label": v["label"], "fields": d.preset_fields(k)} for k, v in d.PRESETS.items()],
        "workflows": _workflow_list(),
        "characters": [{"id": e.id, "name": e.name} for e in meta.entities if e.kind == "character" and not e.archived],
        "locations": [{"id": e.id, "name": e.name} for e in meta.entities if e.kind == "world" and not e.archived],
        "props": [{"id": e.id, "name": e.name} for e in meta.entities if e.kind == "prop" and not e.archived],
    }


def _mutate(request: Request, pid: str, fn):
    store = _store(request)
    try:
        result, _ = store.mutate(pid, fn)
        meta, note = store.read_with_note(pid)
    except (ProjectError, ValueError, ValidationError, KeyError) as e:
        status = getattr(e, "status", 400)
        raise HTTPException(status, detail=str(e) if not isinstance(e, KeyError) else "not found")
    return result, meta, note


def build_router() -> APIRouter:

    @router.get("/shot-presets")
    def presets():
        return {"presets": [{"id": k, "label": v["label"], "fields": d.preset_fields(k)} for k, v in d.PRESETS.items()]}

    @router.get("/projects/{pid}/director")
    def director(pid: str, request: Request):
        store = _store(request)
        try:
            meta, note = store.read_with_note(pid)
        except ProjectError as e:
            raise HTTPException(getattr(e, "status", 400), detail=str(e))
        return {**_director_view(meta), "recovery_note": note}

    @router.put("/projects/{pid}/script")
    def set_script(pid: str, body: ScriptBody, request: Request):
        def change(meta: ProjectMeta):
            _check_rev(meta, body.base_revision)
            meta.script_text = body.text
        _, meta, _ = _mutate(request, pid, change)
        return _director_view(meta)

    @router.post("/projects/{pid}/plan")
    def plan(pid: str, request: Request):
        """Proposal only. Nothing is written."""
        store = _store(request)
        try:
            meta = store.read(pid)
        except ProjectError as e:
            raise HTTPException(getattr(e, "status", 400), detail=str(e))
        p = d.propose_plan(meta)
        return {**p, "revision": meta.revision}

    @router.post("/projects/{pid}/plan/apply")
    def apply_plan(pid: str, body: ApplyBody, request: Request):
        """Applies chosen proposal indices from a freshly computed plan. Existing shots are never replaced."""
        def change(meta: ProjectMeta):
            _check_rev(meta, body.base_revision)
            proposals = d.propose_plan(meta)["proposals"]
            picks = []
            for i in body.picks:
                if i < 0 or i >= len(proposals):
                    raise ValueError(f"proposal {i} does not exist")
                picks.append(proposals[i])
            if not picks:
                raise ValueError("select at least one proposal")
            return d.apply_proposals(meta, picks)
        created, meta, _ = _mutate(request, pid, change)
        return {"created": created, **_director_view(meta)}

    @router.post("/projects/{pid}/shots", status_code=201)
    def add_shot(pid: str, body: AddShot, request: Request):
        def change(meta: ProjectMeta):
            _check_rev(meta, body.base_revision)
            scenes = d.parse_script(meta.script_text)
            if scenes:
                scene = next((s for s in scenes if s["id"] == body.scene_id), scenes[-1])
                sid, title = scene["id"], scene["title"]
            else:
                sid, title = d.scene_id_for("untitled", 0), "Unscripted"
            s = d.new_shot(len(meta.shots) + 1, sid, title)
            meta.shots.append(s)
            d.renumber(meta)
            return s.id
        created, meta, _ = _mutate(request, pid, change)
        return {"shot_id": created, **_director_view(meta)}

    @router.patch("/projects/{pid}/shots/{shot_id}")
    def patch_shot(pid: str, shot_id: str, body: PatchShot, request: Request):
        bad = set(body.fields) - SHOT_FIELDS
        if bad:
            raise HTTPException(400, detail=f"fields not editable here: {', '.join(sorted(bad))}")

        def change(meta: ProjectMeta):
            _check_rev(meta, body.base_revision)
            s = d.find_shot(meta, shot_id)
            data = s.model_dump()
            if body.preset is not None:
                data.update(d.preset_fields(body.preset))  # structured fields only; the rest is kept
                data["preset"] = body.preset
            data.update(body.fields)
            if "workflow_id" in body.fields and body.fields["workflow_id"] not in (None, *[w["id"] for w in _workflow_list()]):
                raise ValueError("unknown workflow")
            data["updated_at"] = now_iso()
            data["revision"] = s.revision + 1
            new = Shot.model_validate(data)
            meta.shots[meta.shots.index(s)] = new
        _, meta, _ = _mutate(request, pid, change)
        return _director_view(meta)

    @router.post("/projects/{pid}/shots/{shot_id}/duplicate", status_code=201)
    def duplicate(pid: str, shot_id: str, body: Base, request: Request):
        def change(meta: ProjectMeta):
            _check_rev(meta, body.base_revision)
            src = d.find_shot(meta, shot_id)
            copy = Shot.model_validate({**src.model_dump(), "id": new_id(), "takes": [], "revision": 1,
                                        "created_at": now_iso(), "updated_at": now_iso()})
            idx = meta.shots.index(src)
            meta.shots.insert(idx + 1, copy)
            d.renumber(meta)
            return copy.id
        new_id_, meta, _ = _mutate(request, pid, change)
        return {"shot_id": new_id_, **_director_view(meta)}

    @router.post("/projects/{pid}/shots/{shot_id}/remove")
    def remove(pid: str, shot_id: str, body: Base, request: Request):
        def change(meta: ProjectMeta):
            _check_rev(meta, body.base_revision)
            s = d.find_shot(meta, shot_id)
            if s.takes:
                raise ValueError("this shot has takes. Disable it instead, so its history is kept")
            meta.shots.remove(s)
            d.renumber(meta)
        _, meta, _ = _mutate(request, pid, change)
        return _director_view(meta)

    @router.put("/projects/{pid}/shots/order")
    def reorder(pid: str, body: ReorderBody, request: Request):
        def change(meta: ProjectMeta):
            _check_rev(meta, body.base_revision)
            by_id = {s.id: s for s in meta.shots}
            if sorted(body.order) != sorted(by_id) or len(set(body.order)) != len(body.order):
                raise ValueError("order must list every shot exactly once")
            meta.shots[:] = [by_id[i] for i in body.order]
            d.renumber(meta)
        _, meta, _ = _mutate(request, pid, change)
        return _director_view(meta)

    @router.post("/projects/{pid}/shots/{shot_id}/takes", status_code=201)
    def request_take(pid: str, shot_id: str, body: TakeBody, request: Request):
        def change(meta: ProjectMeta):
            _check_rev(meta, body.base_revision)
            s = d.find_shot(meta, shot_id)
            if not s.enabled:
                raise ValueError("enable the shot before requesting a take")
            snap = {**s.model_dump(exclude={"takes"}), "capabilities": d.capability_map(s)}
            take = Take(id=new_id(), number=len(s.takes) + 1, state="requested", note=body.note,
                        settings=snap, created_at=now_iso())
            s.takes.append(take)
            s.revision += 1
            s.updated_at = now_iso()
            return take.id
        take_id, meta, _ = _mutate(request, pid, change)
        return {"take_id": take_id, **_director_view(meta)}

    @router.post("/projects/{pid}/shots/{shot_id}/takes/{take_id}/approve")
    def approve_take(pid: str, shot_id: str, take_id: str, body: Base, request: Request):
        """Choose the generated take this shot ships with. Only one is chosen; earlier takes are kept."""
        def change(meta: ProjectMeta):
            _check_rev(meta, body.base_revision)
            d.approve_take(meta, shot_id, take_id, approve=True)
        _, meta, _ = _mutate(request, pid, change)
        return _director_view(meta)

    @router.post("/projects/{pid}/shots/{shot_id}/takes/{take_id}/unapprove")
    def unapprove_take(pid: str, shot_id: str, take_id: str, body: Base, request: Request):
        """Release the chosen take back to plain 'generated'. Nothing is deleted."""
        def change(meta: ProjectMeta):
            _check_rev(meta, body.base_revision)
            d.approve_take(meta, shot_id, take_id, approve=False)
        _, meta, _ = _mutate(request, pid, change)
        return _director_view(meta)

    @router.post("/projects/{pid}/suggestions/{key}/dismiss")
    def dismiss(pid: str, key: str, body: Base, request: Request):
        def change(meta: ProjectMeta):
            _check_rev(meta, body.base_revision)
            if key not in meta.dismissed_suggestions:
                meta.dismissed_suggestions.append(key)
        _, meta, _ = _mutate(request, pid, change)
        return _director_view(meta)

    return router
