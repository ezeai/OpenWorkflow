"""HTTP routes for the model library, workflow readiness, downloads and the setup wizard.

Every state-changing route needs an explicit confirmation field. Paths are only
accepted when they match a file the scan actually found for that component.
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .comfy_detect import detect as detect_comfy, is_comfy_root
from .models.discovery import FileEntry, sha256_cached
from .models.downloads import DownloadError, InsufficientDisk, NoVerifiedSource, make_plan
from .models.store import now_iso
from .wizard import STEPS, WizardError, mark_done, mark_skipped, recommend_starter

router = APIRouter(prefix="/api")


def svc(request: Request):
    return request.app.state.services


class IdentifyBody(BaseModel):
    variant: str
    role: str
    path: str
    confirm: bool = False


class HashBody(BaseModel):
    variant: str
    role: str
    path: str


class PlanBody(BaseModel):
    variant: str


class StartBody(BaseModel):
    confirm: bool = False


class ManagedRemoveBody(BaseModel):
    variant: str
    confirm_model_id: str


class RootBody(BaseModel):
    path: str = Field(min_length=1, max_length=4096)


class ComfyRootBody(BaseModel):
    path: str = Field(min_length=1, max_length=4096)


class WizardStepBody(BaseModel):
    step: str
    action: str  # complete | skip
    workflow_id: str | None = None


def _model_or_404(s, model_id: str):
    m = s.catalog.models.get(model_id)
    if m is None:
        raise HTTPException(404, detail=f"unknown model '{model_id}'")
    return m


def _variant_or_404(m, variant_id: str):
    for v in m.variants:
        if v.id == variant_id:
            return v
    raise HTTPException(404, detail=f"unknown variant '{variant_id}' for {m.id}")


def _candidate_paths(status: dict, variant: str, role: str) -> dict[str, dict]:
    for v in status["variants"]:
        if v["id"] == variant:
            for c in v["components"]:
                if c["role"] == role:
                    return {f["path"]: f for f in c["files"]}
    return {}


def _status_for(s, model_id: str) -> dict:
    models, _, _ = s.model_statuses()
    for m in models:
        if m["id"] == model_id:
            return m
    raise HTTPException(404, detail=f"unknown model '{model_id}'")


def _workflows_using(s, model_id: str) -> list[str]:
    return [w.id for w in s.workflows.items.values() if any(r.model_id == model_id for r in w.models)]


def build_router() -> APIRouter:

    # ---------------- models ----------------
    @router.get("/models")
    def list_models(request: Request):
        s = svc(request)
        models, reg, scan = s.model_statuses()
        return {
            "models": models,
            "catalog_errors": s.catalog.errors,
            "registry_errors": s.registry.load_errors,
            "scan": {"roots": scan.roots_checked, "files_found": len(scan.files),
                     "problems": [p.__dict__ for p in scan.problems[:200]], "last_scan_at": reg.last_scan_at},
        }

    @router.post("/models/scan")
    def rescan(request: Request):
        s = svc(request)
        scan = s.scan(force=True)
        return {"files_found": len(scan.files), "problems": [p.__dict__ for p in scan.problems[:200]],
                "roots": scan.roots_checked, "scanned_at": now_iso()}

    @router.get("/models/{model_id}")
    def model_detail(model_id: str, request: Request):
        s = svc(request)
        m = _model_or_404(s, model_id)
        status = _status_for(s, model_id)
        status["download_sources"] = [
            {"variant": v.id, "available": v.download is not None,
             "reason": None if v.download else "No verified download source for this variant."} for v in m.variants]
        status["workflows"] = _workflows_using(s, model_id)
        status["license_evidence"] = m.license.model_dump()
        return status

    @router.post("/models/{model_id}/identify")
    def identify(model_id: str, body: IdentifyBody, request: Request):
        s = svc(request)
        _model_or_404(s, model_id)
        if body.confirm is not True:
            raise HTTPException(400, detail="identity is recorded only after you confirm the file")
        status = _status_for(s, model_id)
        candidates = _candidate_paths(status, body.variant, body.role)
        if body.path not in candidates:
            raise HTTPException(400, detail="that file is not a current match for this component; rescan first")
        f = candidates[body.path]
        if not f["header_ok"]:
            raise HTTPException(400, detail="file header did not validate; it cannot be confirmed")
        st = os.stat(body.path)
        data = s.registry.load()
        data.identities.setdefault(model_id, {}).setdefault(body.variant, {})[body.role] = {
            "path": body.path, "size": st.st_size, "mtime_ns": st.st_mtime_ns, "confirmed_at": now_iso()}
        s.registry.save(data)
        return {"identified": True, "model_id": model_id, "variant": body.variant, "role": body.role}

    @router.post("/models/{model_id}/verify-hash")
    def verify_hash(model_id: str, body: HashBody, request: Request):
        s = svc(request)
        _model_or_404(s, model_id)
        status = _status_for(s, model_id)
        candidates = _candidate_paths(status, body.variant, body.role)
        if body.path not in candidates:
            raise HTTPException(400, detail="that file is not a current match for this component")
        f = candidates[body.path]
        entry = FileEntry(path=body.path, name=f["name"], size=f["size"], mtime_ns=os.stat(body.path).st_mtime_ns,
                          ownership=f["ownership"], root_label=f["root"], is_symlink=f["is_symlink"],
                          link_target=f["link_target"], format=f["format"], header_ok=f["header_ok"])
        cache = s.registry.load_hashes()
        digest, from_cache = sha256_cached(entry, cache)
        s.registry.save_hashes(cache)
        return {"sha256": digest, "from_cache": from_cache,
                "authority": "No publisher checksum is recorded for this file. This value only identifies the local copy."}

    @router.post("/models/{model_id}/managed/remove")
    def remove_managed(model_id: str, body: ManagedRemoveBody, request: Request):
        s = svc(request)
        _model_or_404(s, model_id)
        if body.confirm_model_id != model_id:
            raise HTTPException(400, detail="type the model id exactly to confirm deletion of managed files")
        data = s.registry.load()
        records = (data.managed.get(model_id) or {}).get(body.variant)
        if not records:
            raise HTTPException(404, detail="no Open Workflow-managed files for this variant; nothing was removed")
        removed = []
        managed_root = s.managed_dir.resolve()
        for role, rec in records.items():
            p = Path(rec["path"])
            if p.is_symlink():
                raise HTTPException(409, detail=f"{p.name} is a symbolic link; refusing to delete")
            if managed_root not in p.resolve().parents:
                raise HTTPException(409, detail=f"{p.name} is outside the managed folder; refusing to delete")
            if p.exists():
                p.unlink()
                removed.append(str(p))
        data.managed[model_id].pop(body.variant, None)
        data.identities.get(model_id, {}).pop(body.variant, None)
        s.registry.save(data)
        s.invalidate_scan()
        return {"removed": removed}

    # ---------------- downloads ----------------
    @router.post("/models/{model_id}/plan")
    def plan_download(model_id: str, body: PlanBody, request: Request):
        s = svc(request)
        m = _model_or_404(s, model_id)
        v = _variant_or_404(m, body.variant)
        try:
            plan = make_plan(m, v, s.managed_dir, _workflows_using(s, model_id))
        except NoVerifiedSource as e:
            raise HTTPException(409, detail=str(e))
        except DownloadError as e:
            raise HTTPException(400, detail=str(e))
        s.downloads.remember(plan)
        return {"plan_id": plan.plan_id, "model_id": plan.model_id, "variant": plan.variant_id,
                "source_repo": plan.source_repo, "license": {"id": plan.license_id, "status": plan.license_status},
                "files": plan.files, "total_bytes": plan.total_bytes, "destination": plan.destination,
                "free_bytes": plan.free_bytes, "required_bytes": plan.required_bytes,
                "checksum_authority": plan.checksum_authority, "needed_for_workflows": plan.needed_for_workflows,
                "expires_at": plan.expires_at.isoformat(timespec="seconds"),
                "network": "No request has been made yet. Downloads begin only after you confirm."}

    @router.post("/downloads/{plan_id}/start", status_code=202)
    def start_download(plan_id: str, body: StartBody, request: Request):
        s = svc(request)

        def on_done(job, plan):
            if job.state != "completed":
                return
            data = s.registry.load()
            for rec in job.results:
                role = next((f["component_role"] for f in plan.files if f["relpath"] == rec["relpath"]), "other")
                data.managed.setdefault(plan.model_id, {}).setdefault(plan.variant_id, {})[role] = {
                    "path": rec["path"], "size": rec["size"], "sha256": rec["sha256"],
                    "checksum": rec["checksum"], "source_repo": plan.source_repo,
                    "downloaded_at": rec["completed_at"]}
            s.registry.save(data)
            s.invalidate_scan()
            s.completed_downloads.append(job.job_id)

        try:
            job = s.downloads.start(plan_id, confirm=body.confirm, on_done=on_done)
        except InsufficientDisk as e:
            raise HTTPException(409, detail=str(e))
        except DownloadError as e:
            raise HTTPException(400, detail=str(e))
        return _job_view(job)

    @router.get("/downloads/{job_id}")
    def get_download(job_id: str, request: Request):
        try:
            return _job_view(svc(request).downloads.job(job_id))
        except DownloadError as e:
            raise HTTPException(404, detail=str(e))

    @router.post("/downloads/{job_id}/cancel")
    def cancel_download(job_id: str, request: Request):
        try:
            return _job_view(svc(request).downloads.cancel(job_id))
        except DownloadError as e:
            raise HTTPException(404, detail=str(e))

    # ---------------- workflows ----------------
    @router.get("/workflows")
    def list_workflows(request: Request):
        s = svc(request)
        return {"workflows": s.evaluations(), "catalog_errors": s.workflows.errors}

    @router.get("/workflows/recommendation")
    def recommendation(request: Request):
        return recommend_starter(svc(request).evaluations())

    # ---------------- model folders (paths only; never moves files) ----------------
    @router.get("/model-roots")
    def list_roots(request: Request):
        s = svc(request)
        return {"roots": [{"path": str(r.path), "ownership": r.ownership, "label": r.label} for r in s.roots()],
                "note": "Adding a folder only makes Open Workflow look there. No file is moved, copied or renamed."}

    @router.post("/model-roots")
    def add_root(body: RootBody, request: Request):
        s = svc(request)
        p = Path(body.path)
        if not p.is_dir():
            raise HTTPException(400, detail="that folder does not exist or is not accessible")
        st = s.settings()
        if str(p) not in st.model_dirs:
            st.model_dirs.append(str(p))
            s.settings_store.save(st)
        s.invalidate_scan()
        return {"added": str(p)}

    @router.post("/model-roots/remove")
    def remove_root(body: RootBody, request: Request):
        s = svc(request)
        st = s.settings()
        if body.path not in st.model_dirs:
            raise HTTPException(404, detail="not in your list of folders")
        st.model_dirs.remove(body.path)
        s.settings_store.save(st)
        s.invalidate_scan()
        return {"removed_from_list": body.path, "files_touched": 0}

    @router.get("/comfy/detect")
    def detect_installs():
        return {"found": detect_comfy(), "note": "Read-only search. Nothing is changed until you choose one."}

    @router.post("/comfy/root")
    def set_comfy_root(body: ComfyRootBody, request: Request):
        s = svc(request)
        p = Path(body.path)
        if not is_comfy_root(p):
            raise HTTPException(400, detail="not a ComfyUI folder: expected ComfyUI/nodes.py and ComfyUI/main.py inside it")
        st = s.settings()
        st.comfy_root = str(p)
        s.settings_store.save(st)
        s.invalidate_scan()
        return {"comfy_root": str(p), "models_dir": str(p / "ComfyUI" / "models")}

    # ---------------- wizard ----------------
    @router.get("/wizard")
    def wizard_state(request: Request):
        s = svc(request)
        st = s.wizard.load()
        return {"steps": STEPS, "completed": st.completed, "skipped": st.skipped,
                "starter_workflow": st.starter_workflow, "updated_at": st.updated_at}

    @router.post("/wizard/step")
    def wizard_step(body: WizardStepBody, request: Request):
        s = svc(request)
        st = s.wizard.load()
        if body.step not in STEPS:
            raise HTTPException(400, detail=f"unknown step '{body.step}'")
        try:
            if body.action == "skip":
                if body.step in ("welcome", "completion"):
                    raise WizardError("this step cannot be skipped")
                mark_skipped(st, body.step)
            elif body.action == "complete":
                _verify_step(s, st, body)
            else:
                raise HTTPException(400, detail="action must be 'complete' or 'skip'")
        except WizardError as e:
            raise HTTPException(409, detail=str(e))
        s.wizard.save(st)
        return {"completed": st.completed, "skipped": st.skipped, "starter_workflow": st.starter_workflow}

    @router.post("/wizard/reset")
    def wizard_reset(request: Request):
        svc(request).wizard.reset()
        return {"reset": True, "note": "Setup can be run again. No models or files were changed."}

    return router


def _verify_step(s, st, body: WizardStepBody) -> None:
    """A step is recorded only after the server has verified its underlying action."""
    step = body.step
    if step == "welcome":
        mark_done(st, step, verified=True)
    elif step == "system_check":
        from . import hardware
        snap = hardware.snapshot([str(s.home)], s.settings().ffmpeg_path)
        mark_done(st, step, verified=bool(snap.get("ram_total_gb")), reason="hardware probe failed")
    elif step == "model_discovery":
        reg = s.registry.load()
        mark_done(st, step, verified=bool(reg.last_scan_at), reason="run a model scan first")
    elif step == "workflow_readiness":
        evals = s.evaluations()
        mark_done(st, step, verified=bool(evals), reason="no workflows are declared")
    elif step == "starter":
        ids = {w.id for w in s.workflows.items.values()}
        if body.workflow_id is not None and body.workflow_id not in ids:
            raise WizardError(f"unknown workflow '{body.workflow_id}'")
        st.starter_workflow = body.workflow_id
        mark_done(st, step, verified=True)
    elif step == "download":
        mark_done(st, step, verified=bool(s.completed_downloads),
                  reason="no download has completed in this session; skip the step if you do not want one")
    elif step == "completion":
        pending = [x for x in STEPS if x != "completion" and x not in st.completed and x not in st.skipped]
        mark_done(st, step, verified=not pending, reason=f"still pending: {', '.join(pending)}")


def _job_view(job) -> dict:
    return {"job_id": job.job_id, "plan_id": job.plan_id, "state": job.state, "bytes_done": job.bytes_done,
            "bytes_total": job.bytes_total, "current_file": job.current_file, "error": job.error,
            "results": job.results}
