"""Job engine: one worker owns the GPU, every state change is persisted, recovery after restart.

States: QUEUED, VALIDATING, LOADING_MODEL, RUNNING, POSTPROCESSING, COMPLETED, FAILED,
CANCELLING, CANCELLED, INTERRUPTED, RECOVERY_REQUIRED.

LOADING_MODEL is part of the state list but is never entered: the ComfyUI API does not
report model loading separately, so entering it would be a claim we cannot back.

Cancellation is best-effort. A running prompt is interrupted; if it finishes first the
job is COMPLETED and says so. A cancelled job never produces an asset.
"""
from __future__ import annotations

import hashlib
import json
import logging
import queue
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..projects.assets import AssetError, AssetRegistry
from ..projects.store import ProjectError, now_iso
from .comfy import BUSY_WAIT, ComfyRunner, EngineError
from .progress import node_titles

log = logging.getLogger("openfield.jobs")

QUEUED, VALIDATING, LOADING_MODEL, RUNNING, POSTPROCESSING = "QUEUED", "VALIDATING", "LOADING_MODEL", "RUNNING", "POSTPROCESSING"
COMPLETED, FAILED, CANCELLING, CANCELLED, INTERRUPTED, RECOVERY_REQUIRED = (
    "COMPLETED", "FAILED", "CANCELLING", "CANCELLED", "INTERRUPTED", "RECOVERY_REQUIRED")
ACTIVE = {VALIDATING, RUNNING, POSTPROCESSING, CANCELLING}
TERMINAL = {COMPLETED, FAILED, CANCELLED, INTERRUPTED, RECOVERY_REQUIRED}
MAX_RUNTIME_S = 20 * 60
POLL_S = 1.5
MISSING_LIMIT = 4
APP_VERSION = "0.1.0"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
VIDEO_EXTENSIONS = {".mp4", ".webm", ".gif"}
APP = "app"          # a Forge app run: a prebuilt API prompt, no shot or take


@dataclass
class Resolved:
    graph_args: dict          # unet, text_encoder, vae (ComfyUI-relative names)
    models: list[dict]        # provenance: role, name, path, size, identity_confirmed


class JobStore:
    def __init__(self, home: Path):
        self.dir = Path(home) / "jobs"
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def path(self, jid: str) -> Path:
        if len(jid) != 32 or any(c not in "0123456789abcdef" for c in jid):
            raise KeyError("job not found")
        return self.dir / f"{jid}.json"

    def save(self, job: dict) -> None:
        with self._lock:
            p = self.path(job["id"])
            tmp = p.with_name(p.name + ".tmp")
            tmp.write_text(json.dumps(job, indent=2, ensure_ascii=False), encoding="utf-8")
            # On Windows, replacing a file fails while another handle has it open (a scanner, for
            # example). Reads below hold the same lock, so only outside programs can collide; retry briefly.
            for attempt in range(20):
                try:
                    tmp.replace(p)
                    break
                except PermissionError:
                    if attempt == 19:
                        raise
                    time.sleep(0.05)

    def load(self, jid: str) -> dict:
        p = self.path(jid)
        with self._lock:
            if not p.exists():
                raise KeyError("job not found")
            return json.loads(p.read_text(encoding="utf-8"))

    def all(self) -> list[dict]:
        out = []
        with self._lock:
            for p in sorted(self.dir.glob("*.json"), reverse=True):
                try:
                    out.append(json.loads(p.read_text(encoding="utf-8")))
                except (OSError, ValueError):
                    log.warning("unreadable job record %s", p.name)
        return sorted(out, key=lambda j: j.get("created_at", ""), reverse=True)


class JobEngine:
    """Owns the single GPU worker thread. Constructed once per process."""

    def __init__(self, store: JobStore, projects_fn: Callable, engine_url: str,
                 resolver: Callable[[str], Resolved], build_graph: Callable, compose: Callable,
                 runner_factory: Callable[[str], ComfyRunner] | None = None, on_complete: Callable | None = None,
                 app_output_dir: Callable[[str], Path] | None = None, cloud_runner_factory: Callable[[], Any] | None = None,
                 on_app_terminal: Callable | None = None):
        self.store = store
        self.on_app_terminal = on_app_terminal  # (job) -> None, after a Forge app run reaches a terminal state (pipelines use it)
        self.projects_fn = projects_fn       # () -> ProjectStore
        self.engine_url = engine_url          # callable: always the current setting
        self.resolver = resolver             # (workflow_id) -> Resolved, raises on missing models
        self.build_graph = build_graph       # (workflow_id, positive, negative, params, resolved) -> graph
        self.compose = compose               # (meta, shot) -> (positive, negative, notes)
        self.runner_factory = runner_factory or (lambda url: ComfyRunner(url))
        self.on_complete = on_complete       # called after a real output is saved (used to record a smoke test)
        self.app_output_dir = app_output_dir  # (job_id) -> folder for Forge app outputs
        self.cloud_runner_factory = cloud_runner_factory  # () -> runner for Comfy Cloud app runs (see forge/providers.py)
        self._q: queue.Queue[str] = queue.Queue()
        self._cancel_flags: set[str] = set()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    # ---------- lifecycle ----------
    def worker_alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._loop, name="openfield-gpu-worker", daemon=True)
            self._thread.start()

    def recover(self) -> list[str]:
        """After a restart, no job can still be running in this process. Mark what was active."""
        changed = []
        runner = self.runner_factory(self.engine_url())
        for job in self.store.all():
            if job["state"] not in ACTIVE:
                continue
            prompt_id = job.get("comfy_prompt_id")
            outcome = None
            if job.get("provider") == "cloud":
                # A cloud job's status is not checked after a restart: no poll is made, so no result is claimed.
                self._set_take(job, "failed")
                self._transition(job, INTERRUPTED, note="Open Workflow stopped during a Comfy Cloud run. Its status was not checked and it was not re-run.")
                changed.append(job["id"])
                continue
            if prompt_id and runner.reachable():
                try:
                    h = runner.history(prompt_id)
                    if h and h.get("status", {}).get("completed") and h.get("outputs"):
                        outcome = RECOVERY_REQUIRED
                except EngineError:
                    outcome = None
            state = outcome or INTERRUPTED
            self._set_take(job, "failed" if state == INTERRUPTED else "running")
            self._transition(job, state, note=("ComfyUI finished this prompt while Open Workflow was not running; "
                                               "its output can be imported" if outcome else
                                               "Open Workflow stopped while this job was active. It was not re-run."))
            changed.append(job["id"])
        return changed

    # ---------- submission ----------
    def submit(self, project_id: str, shot_id: str, base_revision: int | None, params: dict, *, notes: list[str] | None = None) -> dict:
        """base_revision None means 'the revision current under the lock' (used by retries)."""
        """Create the take and the job together, under the project lock. Returns the job."""
        store = self.projects_fn()
        jid = uuid.uuid4().hex
        created = {}

        def change(meta):
            from ..projects import director as d
            from ..projects.store import Conflict
            if base_revision is not None and base_revision != meta.revision:
                raise Conflict(f"project changed elsewhere (revision {meta.revision}); reload to see the latest")
            shot = d.find_shot(meta, shot_id)
            if not shot.enabled:
                raise ValueError("enable the shot before generating")
            if not shot.workflow_id:
                raise ValueError("choose a workflow for this shot before generating")
            from ..projects.schema import Take, new_id
            take = Take(id=new_id(), number=len(shot.takes) + 1, state="requested", note="generation requested",
                        settings={**shot.model_dump(exclude={"takes"}), "job_id": jid}, job_id=jid, created_at=now_iso())
            # record the character and location versions this take was made from (continuity checks compare them)
            ents = {e.id: e for e in meta.entities}
            take.settings["versions"] = {eid: ents[eid].current_version for eid in
                                         shot.character_ids + ([shot.location_id] if shot.location_id else [])
                                         if eid in ents}
            shot.takes.append(take)
            created["take_id"] = take.id
            created["scene"] = shot.scene_title
            created["workflow_id"] = shot.workflow_id
        store.mutate(project_id, change)
        job = {
            "id": jid, "kind": "image", "state": QUEUED, "project_id": project_id, "shot_id": shot_id,
            "take_id": created["take_id"], "workflow_id": params["workflow_id"], "workflow_version": params["workflow_version"],
            "params": {k: params[k] for k in ("width", "height", "seed", "steps", "cfg") if k in params},
            "created_at": now_iso(), "started_at": None, "finished_at": None, "updated_at": now_iso(),
            "models": [], "comfy_prompt_id": None, "comfy_url": self.engine_url(), "error": None,
            "output_asset_id": None, "progress": {"mode": "indeterminate"}, "notes": list(notes or []),
            "app_version": APP_VERSION, "history": [{"state": QUEUED, "at": now_iso(), "note": "accepted"}],
        }
        self.store.save(job)
        self._q.put(jid)
        return job

    def _runner(self, job: dict):
        """A job runs on the provider it was created with. A cloud job never uses the local engine, and the reverse."""
        if job.get("provider") == "cloud":
            if self.cloud_runner_factory is None:
                from ..forge.providers import CloudRunner
                return CloudRunner()
            return self.cloud_runner_factory()
        return self.runner_factory(self.engine_url())

    def submit_app(self, workflow_id: str, workflow_version: str, api_prompt: dict, models: list[dict], params: dict,
                   provenance: dict, notes: list[str], provider: str = "local", consent: dict | None = None) -> dict:
        """A Forge app run. The prompt is already converted and its values already applied and checked."""
        jid = uuid.uuid4().hex
        job = {
            "id": jid, "kind": APP, "provider": provider, "consent": consent, "state": QUEUED, "project_id": None, "shot_id": None, "take_id": None,
            "workflow_id": workflow_id, "workflow_version": workflow_version, "params": params, "api_prompt": api_prompt,
            "provenance": provenance, "created_at": now_iso(), "started_at": None, "finished_at": None, "updated_at": now_iso(),
            "models": models, "comfy_prompt_id": None, "comfy_url": self.engine_url(), "error": None,
            "output_asset_id": None, "output_file": None, "prompt": None, "progress": {"mode": "indeterminate"},
            "notes": list(notes), "app_version": APP_VERSION, "history": [{"state": QUEUED, "at": now_iso(), "note": "accepted"}],
        }
        self.store.save(job)
        self._q.put(jid)
        return job

    def request_cancel(self, jid: str) -> dict:
        job = self.store.load(jid)
        if job["state"] in TERMINAL:
            raise ValueError(f"job is already {job['state'].lower()}")
        with self._lock:
            self._cancel_flags.add(jid)
        if job["state"] == QUEUED:
            self._set_take(job, "cancelled")  # take first: a terminal job must never show a stale take
            self._transition(job, CANCELLED, note="cancelled before it started")
        else:
            self._transition(job, CANCELLING, note="cancel requested; ComfyUI will be asked to stop")
        return self.store.load(jid)

    def import_recovered(self, jid: str) -> dict:
        """Import the output of a job that ComfyUI finished while Open Workflow was not running."""
        job = self.store.load(jid)
        if job["state"] != RECOVERY_REQUIRED:
            raise ValueError("only jobs marked recovery-required can be imported")
        runner = self._runner(job)
        try:
            h = runner.history(job["comfy_prompt_id"])
        except EngineError as e:
            raise ValueError(f"ComfyUI is not reachable: {e}")
        if not h or not h.get("outputs"):
            self._fail(job, "ComfyUI no longer has the output for this job")
        else:
            self._postprocess(job, runner, h)
        return self.store.load(jid)

    # ---------- worker ----------
    def _loop(self) -> None:
        while True:
            jid = self._q.get()
            try:
                self._process(jid)
            except Exception as e:  # never let the worker die silently
                log.exception("job %s crashed", jid)
                try:
                    job = self.store.load(jid)
                    if job["state"] not in TERMINAL:
                        self._set_take(job, "failed")
                        self._transition(job, FAILED, note=f"internal error: {type(e).__name__}")
                except Exception:
                    pass
            finally:
                self._q.task_done()

    def _process(self, jid: str) -> None:
        job = self.store.load(jid)
        if job["state"] != QUEUED:
            return
        if jid in self._cancel_flags:
            self._set_take(job, "cancelled")
            self._transition(job, CANCELLED, note="cancelled before it started")
            return
        self._transition(job, VALIDATING, note="checking engine, shot and model files")
        runner = self._runner(job)
        if not runner.reachable(wait=BUSY_WAIT):
            return self._fail(job, "ComfyUI is not running on the configured address. Start it, then generate again.")
        if job.get("kind") == APP:
            graph = job["api_prompt"]
        else:
            try:
                meta, shot = self._shot(job)
                resolved = self.resolver(job["workflow_id"])
                positive, negative, notes = self.compose(meta, shot)
                graph = self.build_graph(job["workflow_id"], positive, negative, job["params"], resolved.graph_args)
            except (EngineError, ProjectError, KeyError, ValueError) as e:
                return self._fail(job, str(e))
            job["models"] = resolved.models
            job["prompt"] = {"positive": positive, "negative": negative}
            job["notes"] = list(job.get("notes", [])) + notes + [
                "Camera, framing and lens settings are written into the prompt as guidance. They are not verified against the image."]
        try:
            prompt_id = runner.submit(graph)
        except EngineError as e:
            return self._fail(job, str(e))
        job["comfy_prompt_id"] = prompt_id
        job["started_at"] = now_iso()
        self._transition(job, RUNNING, note="submitted to ComfyUI")
        self._set_take(job, "running")
        self._watch(job, runner, graph)

    def _watch(self, job: dict, runner: ComfyRunner, graph: dict) -> None:
        listener = None
        if hasattr(runner, "progress_listener"):
            listener = runner.progress_listener(job["comfy_prompt_id"], node_titles(graph))
            listener.start()
        try:
            return self._watch_loop(job, runner, listener)
        finally:
            if listener is not None:
                listener.close()

    def _publish_progress(self, job: dict, listener) -> None:
        if listener is None:
            return
        record = listener.record()
        keys = ("mode", "node", "step", "steps", "nodes_finished")
        if any(record[k] != job.get("progress", {}).get(k) for k in keys):
            job["progress"] = record
            self.store.save(job)

    def _watch_loop(self, job: dict, runner: ComfyRunner, listener) -> None:
        jid, prompt_id = job["id"], job["comfy_prompt_id"]
        started = time.monotonic()
        missing = 0
        interrupt_sent = False
        while True:
            time.sleep(POLL_S)
            self._publish_progress(job, listener)
            if jid in self._cancel_flags and not interrupt_sent:
                interrupt_sent = True
                try:
                    q = runner.queue()
                    pending = [x for x in q.get("queue_pending", []) if x[1] == prompt_id]
                    if pending:
                        runner.delete_pending(prompt_id)
                    else:
                        runner.interrupt()
                except EngineError as e:
                    job["notes"].append(f"cancel request reported: {e}")
            if time.monotonic() - started > MAX_RUNTIME_S:
                runner.interrupt()
                return self._fail(job, f"timed out after {MAX_RUNTIME_S // 60} minutes; ComfyUI was asked to stop")
            try:
                h = runner.history(prompt_id)
            except EngineError:
                missing += 1
                if missing >= MISSING_LIMIT:
                    return self._fail(job, "lost contact with ComfyUI while the job was running")
                continue
            if h is None:
                missing = 0 if self._in_queue(runner, prompt_id) else missing + 1
                if missing >= MISSING_LIMIT:
                    return self._fail(job, "ComfyUI no longer knows this job and produced no record")
                continue
            status = h.get("status", {})
            if status.get("status_str") == "error":
                # Only ComfyUI's own interrupt event (or our cancel request) means "stopped". Any other error is a failure.
                interrupted = any(m and m[0] == "execution_interrupted" for m in status.get("messages", []))
                if interrupted or jid in self._cancel_flags:
                    return self._cancelled(job)
                return self._fail(job, "ComfyUI reported an error while generating. See the job log for details.")
            if status.get("completed"):
                if jid in self._cancel_flags:
                    job["notes"].append("ComfyUI finished before the cancel took effect, so the job completed.")
                return self._postprocess(job, runner, h)

    def _in_queue(self, runner: ComfyRunner, prompt_id: str) -> bool:
        try:
            q = runner.queue()
        except EngineError:
            return False
        return any(x[1] == prompt_id for x in q.get("queue_running", []) + q.get("queue_pending", []))

    def _postprocess(self, job: dict, runner: ComfyRunner, h: dict) -> None:
        self._transition(job, POSTPROCESSING, note="fetching output and registering it")
        images = []
        for node_out in h.get("outputs", {}).values():
            images += node_out.get("images", []) + node_out.get("videos", []) + node_out.get("gifs", [])
        if not images:
            return self._fail(job, "ComfyUI finished but returned no image or video")
        img = images[0]
        try:
            data = runner.fetch(img["filename"], img.get("subfolder", ""), img.get("type", "output"))
        except EngineError as e:
            return self._fail(job, f"output could not be fetched: {e}")
        if job.get("kind") == APP:
            return self._postprocess_app(job, data, img)
        proj = self.projects_fn()
        prov = {
            "job_id": job["id"], "workflow_id": job["workflow_id"], "workflow_version": job["workflow_version"],
            "parameters": {**job["params"], "positive_prompt": job["prompt"]["positive"], "negative_prompt": job["prompt"]["negative"]},
            "models": job["models"], "comfy_url": job["comfy_url"], "comfy_prompt_id": job["comfy_prompt_id"],
            "app_version": job["app_version"], "created_at": now_iso(),
            "limitations": ["Seeds do not guarantee identical output across software or hardware versions."],
        }
        try:
            reg = AssetRegistry(proj.dir(job["project_id"]))
            asset = reg.import_bytes(data, label=f"generated shot {job['shot_id'][:6]}", source="generated", provenance=prov)
        except (AssetError, ProjectError) as e:
            return self._fail(job, f"output could not be saved: {e}")
        job["output_asset_id"] = asset.id
        self._set_take(job, "generated", asset_id=asset.id)
        self._transition(job, COMPLETED, note="output saved as a project asset")
        if self.on_complete:
            try:
                self.on_complete(job)
            except Exception:  # a registry write must not turn a saved output into a failure
                log.exception("on_complete hook failed for job %s", job["id"])

    def _postprocess_app(self, job: dict, data: bytes, img: dict) -> None:
        """Keep the output and a provenance record in the app's run folder. Forge apps are not projects,
        so there is no asset registry entry and no smoke-test record."""
        if self.app_output_dir is None:
            return self._fail(job, "no folder is configured for app outputs")
        ext = Path(img.get("filename", "")).suffix.lower()
        media = "video" if ext in VIDEO_EXTENSIONS else "image"
        ext = ext if ext in IMAGE_EXTENSIONS | VIDEO_EXTENSIONS else ".png"
        d = self.app_output_dir(job["id"])
        d.mkdir(parents=True, exist_ok=True)
        target = d / f"output{ext}"
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(target)
        digest = hashlib.sha256(data).hexdigest()
        job["output_file"] = {"name": target.name, "sha256": digest, "bytes": len(data), "media": media}
        prov = {**job.get("provenance", {}), "job_id": job["id"], "comfy_prompt_id": job["comfy_prompt_id"],
                "comfy_url": job["comfy_url"], "comfy_filename": img.get("filename"), "output_sha256": digest,
                "models": job["models"], "app_version": job["app_version"], "completed_at": now_iso(),
                "limitations": ["Seeds do not guarantee identical output across software or hardware versions."]}
        (d / "run.json").write_text(json.dumps(prov, indent=2, ensure_ascii=False), encoding="utf-8")
        self._transition(job, COMPLETED, note="output saved in the app's run folder")

    def _cancelled(self, job: dict) -> None:
        self._set_take(job, "cancelled")
        self._transition(job, CANCELLED, note="stopped at your request. No output was saved.")

    def _fail(self, job: dict, message: str) -> None:
        job["error"] = message
        self._set_take(job, "failed")
        self._transition(job, FAILED, note=message)

    # ---------- helpers ----------
    def _shot(self, job: dict):
        from ..projects import director as d
        meta = self.projects_fn().read(job["project_id"])
        return meta, d.find_shot(meta, job["shot_id"])

    def _transition(self, job: dict, state: str, note: str = "") -> None:
        job["state"] = state
        job["updated_at"] = now_iso()
        job.setdefault("history", []).append({"state": state, "at": job["updated_at"], "note": note})
        if state in TERMINAL:
            job["finished_at"] = job["updated_at"]
        self.store.save(job)
        if state in TERMINAL and job.get("kind") == APP and self.on_app_terminal:
            try:
                self.on_app_terminal(job)
            except Exception:  # a pipeline bookkeeping failure must not change the job's own state
                log.exception("on_app_terminal hook failed for job %s", job["id"])

    def _set_take(self, job: dict, state: str, asset_id: str | None = None) -> None:
        """Only the take this job created changes. Other takes, including approved ones, are never touched."""
        from ..projects import director as d
        from ..projects.store import ProjectError as PE
        if job.get("kind") == APP or not job.get("take_id"):
            return          # Forge app runs have no shot or take

        def change(meta):
            try:
                shot = d.find_shot(meta, job["shot_id"])
            except KeyError:
                return
            for t in shot.takes:
                if t.id == job["take_id"]:
                    if t.state == "approved":
                        return
                    t.state = state
                    if asset_id:
                        t.asset_id = asset_id
        try:
            self.projects_fn().mutate(job["project_id"], change)
        except PE:
            log.warning("could not update take for job %s", job["id"])
