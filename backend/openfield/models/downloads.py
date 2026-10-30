"""Consent-gated model downloads from verified sources only.

Flow:
  1. make_plan(): pure computation from the catalogue. NO network request.
     The plan lists exact URLs, sizes, destination, free space and the license.
  2. The user confirms the plan. start() refuses anything without confirm=True
     and a plan id that exists and has not expired.
  3. Each file is streamed to '<name>.part' inside the managed directory, checked
     for size (and sha256 when the publisher provides one), then renamed.

Safety rules enforced here:
  - destination must lie inside the managed models directory (path traversal check);
  - an existing final file is never overwritten;
  - required free space = total size + SAFETY_MARGIN_BYTES, checked before starting;
  - bounded retries with backoff on transport errors and 5xx only;
  - partial files are removed on cancel or failure. Resume happens only within one run.
"""
from __future__ import annotations

import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import httpx

from .schema import CatalogModel, DownloadSpec, Variant

SAFETY_MARGIN_BYTES = 2 * 1024**3
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = (1.0, 2.0)
PLAN_TTL = timedelta(minutes=15)
CHUNK = 1024 * 1024


class DownloadError(Exception):
    pass


class NoVerifiedSource(DownloadError):
    pass


class InsufficientDisk(DownloadError):
    pass


@dataclass
class Plan:
    plan_id: str
    model_id: str
    variant_id: str
    source_repo: str
    license_id: str
    license_status: str
    files: list[dict]
    total_bytes: int
    destination: str
    free_bytes: int | None
    required_bytes: int
    checksum_authority: str
    expires_at: datetime
    needed_for_workflows: list[str] = field(default_factory=list)


@dataclass
class Job:
    job_id: str
    plan_id: str
    state: str = "queued"      # queued | running | completed | failed | cancelled
    bytes_done: int = 0
    bytes_total: int = 0
    current_file: str | None = None
    error: str | None = None
    results: list[dict] = field(default_factory=list)
    cancel: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None


def _free_bytes(path: Path) -> int | None:
    p = path
    while not p.exists() and p != p.parent:
        p = p.parent
    try:
        import shutil
        return shutil.disk_usage(p).free
    except OSError:
        return None


def make_plan(model: CatalogModel, variant: Variant, managed_root: Path, workflows_using: list[str],
              now: datetime | None = None) -> Plan:
    spec: DownloadSpec | None = variant.download
    if spec is None:
        raise NoVerifiedSource(
            f"{model.name} ({variant.name}) has no verified download source. "
            "Open Workflow only offers downloads whose file list and publisher are verified (see docs/MODEL_LICENSE_REVIEW.md).")
    now = now or datetime.now(timezone.utc)
    dest = (Path(managed_root) / model.id / variant.id).resolve()
    if Path(managed_root).resolve() not in dest.parents:
        raise DownloadError("destination escapes the managed models directory")
    total = sum(f.size_bytes for f in spec.files)
    free = _free_bytes(Path(managed_root))
    required = total + SAFETY_MARGIN_BYTES
    return Plan(
        plan_id=uuid.uuid4().hex,
        model_id=model.id,
        variant_id=variant.id,
        source_repo=spec.source_repo,
        license_id=model.license.id,
        license_status=model.license.status,
        files=[{"relpath": f.relpath, "url": f.url, "size_bytes": f.size_bytes, "sha256": f.sha256,
                "component_role": f.component_role, "checksum": "publisher" if f.sha256 else "none"} for f in spec.files],
        total_bytes=total,
        destination=str(dest),
        free_bytes=free,
        required_bytes=required,
        checksum_authority=spec.checksum_authority,
        expires_at=now + PLAN_TTL,
        needed_for_workflows=workflows_using,
    )


class DownloadManager:
    def __init__(self, managed_root: Path, client_factory: Callable[[], httpx.Client] | None = None,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], datetime] | None = None):
        self.managed_root = Path(managed_root)
        self._client_factory = client_factory or (lambda: httpx.Client(timeout=30.0, follow_redirects=True))
        self._sleep = sleep
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._plans: dict[str, Plan] = {}
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    # ---- planning (no network) ----
    def remember(self, plan: Plan) -> Plan:
        with self._lock:
            self._plans[plan.plan_id] = plan
        return plan

    def get_plan(self, plan_id: str) -> Plan:
        with self._lock:
            plan = self._plans.get(plan_id)
        if plan is None:
            raise DownloadError("unknown plan id")
        if self._clock() > plan.expires_at:
            raise DownloadError("plan expired; create a new plan to review the download again")
        return plan

    # ---- execution (only after explicit confirmation) ----
    def start(self, plan_id: str, confirm: bool, on_done: Callable[["Job", Plan], None] | None = None) -> Job:
        if confirm is not True:
            raise DownloadError("download refused: explicit confirmation is required")
        plan = self.get_plan(plan_id)
        dest = Path(plan.destination)
        if self.managed_root.resolve() not in dest.resolve().parents:
            raise DownloadError("destination escapes the managed models directory")
        if plan.free_bytes is not None and plan.free_bytes < plan.required_bytes:
            raise InsufficientDisk(
                f"needs {plan.required_bytes / 1024**3:.1f} GiB free (including safety margin); "
                f"{plan.free_bytes / 1024**3:.1f} GiB available")
        for f in plan.files:
            if (dest / f["relpath"]).exists():
                raise DownloadError(f"{f['relpath']} already exists; it will not be overwritten")
        job = Job(job_id=uuid.uuid4().hex, plan_id=plan.plan_id, bytes_total=plan.total_bytes)
        with self._lock:
            self._jobs[job.job_id] = job
            plan_used = self._plans.pop(plan.plan_id, None)  # a plan can be confirmed only once
        if plan_used is None:
            raise DownloadError("plan already used")
        job.thread = threading.Thread(target=self._run, args=(job, plan, on_done), daemon=True,
                                      name=f"download-{job.job_id[:8]}")
        job.thread.start()
        return job

    def job(self, job_id: str) -> Job:
        with self._lock:
            j = self._jobs.get(job_id)
        if j is None:
            raise DownloadError("unknown job id")
        return j

    def cancel(self, job_id: str) -> Job:
        j = self.job(job_id)
        if j.state in ("queued", "running"):
            j.cancel.set()
        return j

    def wait(self, job_id: str, timeout: float = 30.0) -> Job:
        j = self.job(job_id)
        if j.thread:
            j.thread.join(timeout)
        return j

    def _run(self, job: Job, plan: Plan, on_done=None) -> None:
        job.state = "running"
        dest = Path(plan.destination)
        try:
            dest.mkdir(parents=True, exist_ok=True)
            with self._client_factory() as client:
                for f in plan.files:
                    if job.cancel.is_set():
                        raise _Cancelled()
                    job.current_file = f["relpath"]
                    results = self._fetch(client, job, f, dest / f["relpath"])
                    job.results.append(results)
            job.state = "completed"
        except _Cancelled:
            job.state = "cancelled"
            self._cleanup_partials(dest, plan)
        except DownloadError as e:
            job.state = "failed"
            job.error = str(e)
            self._cleanup_partials(dest, plan)
        except Exception as e:  # unexpected: never leave a partial behind
            job.state = "failed"
            job.error = f"{type(e).__name__}: {e}"
            self._cleanup_partials(dest, plan)
        finally:
            if on_done is not None:
                try:
                    on_done(job, plan)
                except Exception as e:  # registry write failure must not hide the download outcome
                    job.error = (job.error or "") + f" [registry update failed: {type(e).__name__}]"

    def _fetch(self, client: httpx.Client, job: Job, f: dict, final: Path) -> dict:
        part = final.with_name(final.name + ".part")
        expected = f["size_bytes"]
        attempt = 0
        while True:
            attempt += 1
            try:
                return self._attempt(client, job, f, final, part, expected)
            except _Cancelled:
                raise
            except _Retryable as e:
                if attempt >= MAX_ATTEMPTS:
                    raise DownloadError(f"{f['relpath']}: failed after {MAX_ATTEMPTS} attempts ({e})")
                self._sleep(BACKOFF_SECONDS[min(attempt - 1, len(BACKOFF_SECONDS) - 1)])

    def _attempt(self, client, job, f, final, part, expected) -> dict:
        offset = part.stat().st_size if part.exists() else 0
        if offset > expected:
            part.unlink()
            offset = 0
        headers = {"Range": f"bytes={offset}-"} if offset else {}
        try:
            with client.stream("GET", f["url"], headers=headers) as r:
                if r.status_code >= 500 or r.status_code == 429:
                    raise _Retryable(f"HTTP {r.status_code}")
                if r.status_code == 416 and offset == expected:
                    pass  # already complete from an earlier attempt
                elif r.status_code not in (200, 206):
                    raise DownloadError(f"{f['relpath']}: source answered HTTP {r.status_code}")
                elif r.status_code == 200 and offset:
                    offset = 0  # server ignored Range: restart the file
                mode = "ab" if offset else "wb"
                with open(part, mode) as out:
                    for chunk in r.iter_bytes(CHUNK):
                        if job.cancel.is_set():
                            raise _Cancelled()
                        out.write(chunk)
                        job.bytes_done += len(chunk)
        except httpx.TransportError as e:
            raise _Retryable(type(e).__name__)
        size = part.stat().st_size
        if size != expected:
            if size < expected:
                raise _Retryable(f"incomplete ({size} of {expected} bytes)")
            part.unlink()
            raise DownloadError(f"{f['relpath']}: size {size} does not match expected {expected}")
        digest = _sha256_file(part)
        if f.get("sha256") and digest != f["sha256"]:
            part.unlink()
            raise DownloadError(f"{f['relpath']}: sha256 mismatch; partial file removed")
        if final.exists():
            part.unlink()
            raise DownloadError(f"{f['relpath']} appeared during download; not overwritten")
        os.replace(part, final)
        return {"relpath": f["relpath"], "path": str(final), "size": size, "sha256": digest,
                "checksum": "publisher-verified" if f.get("sha256") else "none-published (sha256 computed locally)",
                "completed_at": self._clock().isoformat(timespec="seconds")}

    def _cleanup_partials(self, dest: Path, plan: Plan) -> None:
        for f in plan.files:
            part = dest / (f["relpath"] + ".part")
            try:
                if part.exists():
                    part.unlink()
            except OSError:
                pass


class _Cancelled(Exception):
    pass


class _Retryable(Exception):
    pass


def _sha256_file(p: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()
