"""Continuity and graph routes. Writes need the project revision the client last saw.

Running a graph does not have its own generator. Each generator node calls the same
start_shot_generation() that the Director uses.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .api_generation import start_shot_generation
from .projects import continuity as C
from .projects import graph as G
from .projects.assets import AssetRegistry
from .projects.schema import ProjectMeta
from .projects.store import Conflict, ProjectError, now_iso

router = APIRouter(prefix="/api")


class RuleBody(BaseModel):
    base_revision: int
    kind: str
    params: dict[str, str] = {}
    note: str = Field(default="", max_length=300)


class Base(BaseModel):
    base_revision: int


class AcceptBody(Base):
    key: str = Field(min_length=1, max_length=300)
    reason: str = Field(min_length=3, max_length=500)


class GraphCreate(Base):
    name: str = Field(min_length=1, max_length=120)
    template: str | None = None


class GraphSave(Base):
    name: str | None = Field(default=None, max_length=120)
    nodes: list[dict] = []
    edges: list[dict] = []


class RunBody(Base):
    seed: int | None = None


def _store(request: Request):
    return request.app.state.services.projects()


def _check(meta: ProjectMeta, base: int) -> None:
    if base != meta.revision:
        raise Conflict(f"project changed elsewhere (revision {meta.revision}); reload to see the latest")


def _asset_status(store, pid: str, meta: ProjectMeta) -> dict[str, str]:
    reg = AssetRegistry(store.dir(pid))
    af, _ = reg.load()
    return {a.id: reg.verify(a)["status"] for a in af.assets}


def _continuity_view(store, pid: str, meta: ProjectMeta) -> dict:
    return {
        "revision": meta.revision,
        "rules": meta.continuity_rules,
        "rule_kinds": list(C.RULE_KINDS),
        "warnings": C.evaluate(meta, _asset_status(store, pid, meta)),
        "acks": meta.warning_acks,
    }


def _graph_view(meta: ProjectMeta, g: dict) -> dict:
    return {**g, "validation": G.validate(meta, g), "revision": meta.revision}


def _err(e: Exception) -> HTTPException:
    return HTTPException(getattr(e, "status", 400), detail=str(e))


def build_router() -> APIRouter:

    @router.get("/graph-templates")
    def templates():
        return {"templates": [{"id": k, "name": v["name"], "description": v["description"]} for k, v in G.TEMPLATES.items()],
                "node_types": G.NODE_TYPES, "port_types": list(G.PORT_TYPES)}

    @router.get("/projects/{pid}/continuity")
    def get_continuity(pid: str, request: Request):
        store = _store(request)
        try:
            meta = store.read(pid)
        except ProjectError as e:
            raise _err(e)
        return _continuity_view(store, pid, meta)

    @router.post("/projects/{pid}/continuity/rules", status_code=201)
    def add_rule(pid: str, body: RuleBody, request: Request):
        store = _store(request)

        def change(meta: ProjectMeta):
            _check(meta, body.base_revision)
            meta.continuity_rules.append(C.make_rule(body.kind, body.params, body.note))
        try:
            store.mutate(pid, change)
            meta = store.read(pid)
        except (ProjectError, ValueError) as e:
            raise _err(e)
        return _continuity_view(store, pid, meta)

    @router.post("/projects/{pid}/continuity/rules/{rid}/remove")
    def remove_rule(pid: str, rid: str, body: Base, request: Request):
        store = _store(request)

        def change(meta: ProjectMeta):
            _check(meta, body.base_revision)
            before = len(meta.continuity_rules)
            meta.continuity_rules = [r for r in meta.continuity_rules if r["id"] != rid]
            if len(meta.continuity_rules) == before:
                raise ValueError("rule not found")
        try:
            store.mutate(pid, change)
            meta = store.read(pid)
        except (ProjectError, ValueError) as e:
            raise _err(e)
        return _continuity_view(store, pid, meta)

    @router.post("/projects/{pid}/continuity/accept")
    def accept(pid: str, body: AcceptBody, request: Request):
        """Accepting a warning keeps it visible with your reason. It does not hide the problem."""
        store = _store(request)

        def change(meta: ProjectMeta):
            _check(meta, body.base_revision)
            if any(a["key"] == body.key for a in meta.warning_acks):
                return
            meta.warning_acks.append({"key": body.key, "reason": body.reason.strip(), "at": now_iso()})
        try:
            store.mutate(pid, change)
            meta = store.read(pid)
        except ProjectError as e:
            raise _err(e)
        return _continuity_view(store, pid, meta)

    # ---------------- graphs ----------------
    @router.get("/projects/{pid}/graphs")
    def list_graphs(pid: str, request: Request):
        store = _store(request)
        try:
            meta = store.read(pid)
        except ProjectError as e:
            raise _err(e)
        return {"revision": meta.revision,
                "graphs": [{"id": g["id"], "name": g["name"], "nodes": len(g["nodes"]), "edges": len(g["edges"]),
                            "valid": G.validate(meta, g)["ok"], "updated_at": g.get("updated_at", "")} for g in meta.graphs]}

    @router.post("/projects/{pid}/graphs", status_code=201)
    def create_graph(pid: str, body: GraphCreate, request: Request):
        store = _store(request)

        def change(meta: ProjectMeta):
            _check(meta, body.base_revision)
            meta.graphs.append(G.new_graph(body.name, body.template, now_iso()))
            return meta.graphs[-1]["id"]
        try:
            gid, _ = store.mutate(pid, change)
            meta = store.read(pid)
        except (ProjectError, ValueError) as e:
            raise _err(e)
        return _graph_view(meta, next(g for g in meta.graphs if g["id"] == gid))

    @router.get("/projects/{pid}/graphs/{gid}")
    def get_graph(pid: str, gid: str, request: Request):
        store = _store(request)
        try:
            meta = store.read(pid)
        except ProjectError as e:
            raise _err(e)
        g = next((x for x in meta.graphs if x["id"] == gid), None)
        if g is None:
            raise HTTPException(404, detail="graph not found")
        return _graph_view(meta, g)

    @router.put("/projects/{pid}/graphs/{gid}")
    def save_graph(pid: str, gid: str, body: GraphSave, request: Request):
        store = _store(request)

        def change(meta: ProjectMeta):
            _check(meta, body.base_revision)
            idx = next((i for i, x in enumerate(meta.graphs) if x["id"] == gid), None)
            if idx is None:
                raise ValueError("graph not found")
            candidate = {"id": gid, "name": body.name or meta.graphs[idx]["name"], "nodes": body.nodes,
                         "edges": body.edges, "updated_at": now_iso()}
            try:
                G.Graph.model_validate(candidate)  # shape must be valid; connection problems are reported, not blocked
            except Exception:
                raise ValueError("the graph has malformed nodes or connections")
            meta.graphs[idx] = candidate
        try:
            store.mutate(pid, change)
            meta = store.read(pid)
        except (ProjectError, ValueError) as e:
            raise _err(e)
        return _graph_view(meta, next(g for g in meta.graphs if g["id"] == gid))

    @router.post("/projects/{pid}/graphs/{gid}/run", status_code=202)
    def run_graph(pid: str, gid: str, body: RunBody, request: Request):
        store = _store(request)
        try:
            meta = store.read(pid)
        except ProjectError as e:
            raise _err(e)
        _check(meta, body.base_revision)
        g = next((x for x in meta.graphs if x["id"] == gid), None)
        if g is None:
            raise HTTPException(404, detail="graph not found")
        v = G.validate(meta, g)
        if not v["ok"]:
            raise HTTPException(400, detail={"message": "fix the graph before running", "errors": v["errors"]})
        jobs = []
        for step in v["run_plan"]:
            job = start_shot_generation(request.app, pid, step["shot_id"], body.base_revision, seed=body.seed)
            jobs.append(job["id"])
            body.base_revision = store.read(pid).revision  # each job takes a new revision; keep the chain consistent
        return {"graph_id": gid, "jobs": jobs, "note": "Each generator node started the same job the Director starts."}

    return router
