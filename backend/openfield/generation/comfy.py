"""Minimal ComfyUI HTTP client for one loopback server. Errors are raised, never hidden."""
from __future__ import annotations

import time
import uuid
from urllib.parse import urlencode

import httpx


class EngineError(Exception):
    pass


# ComfyUI can stop answering for several seconds just after a job ends (6.4 s measured on 2026-10-28, see
# docs/evidence/pipeline-live-2026-10-28). Checks made right before a run wait this long for a busy server.
BUSY_WAIT = 20.0


def get_patient(url: str, wait: float, timeout: float = 5.0, **kw) -> httpx.Response:
    """GET that rides out a busy ComfyUI. A timeout is retried until `wait` seconds have passed; any other error
    (for example a refused connection, meaning ComfyUI is not running) is raised at once."""
    deadline = time.monotonic() + wait
    while True:
        try:
            return httpx.get(url, timeout=timeout, **kw)
        except httpx.TimeoutException:
            if time.monotonic() >= deadline:
                raise
            time.sleep(1.0)


class ComfyRunner:
    def __init__(self, base_url: str, timeout: float = 10.0):
        self.base = base_url.rstrip("/")
        self.timeout = timeout
        self.client_id = uuid.uuid4().hex

    def _get(self, path: str, **kw):
        try:
            r = httpx.get(f"{self.base}{path}", timeout=kw.pop("timeout", self.timeout), **kw)
            r.raise_for_status()
            return r
        except httpx.HTTPError as e:
            raise EngineError(f"ComfyUI request failed: {type(e).__name__}")

    def reachable(self, wait: float = 0.0) -> bool:
        """Is ComfyUI answering? `wait` > 0 rides out a busy server (used right before a run); 0 answers quickly."""
        try:
            get_patient(f"{self.base}/system_stats", wait, timeout=3.0).raise_for_status()
            return True
        except httpx.HTTPError:
            return False

    def submit(self, graph: dict) -> str:
        try:
            r = httpx.post(f"{self.base}/prompt", json={"prompt": graph, "client_id": self.client_id}, timeout=30.0)
        except httpx.HTTPError as e:
            raise EngineError(f"could not submit to ComfyUI: {type(e).__name__}")
        if r.status_code != 200:
            detail = r.text[:400]
            raise EngineError(f"ComfyUI rejected the graph (HTTP {r.status_code}): {detail}")
        return r.json()["prompt_id"]

    def progress_listener(self, prompt_id: str, titles: dict[str, str]):
        from .progress import PromptProgress, ProgressListener

        return ProgressListener(self.base, self.client_id, PromptProgress(prompt_id, titles))

    def history(self, prompt_id: str) -> dict | None:
        return self._get(f"/history/{prompt_id}").json().get(prompt_id)

    def queue(self) -> dict:
        return self._get("/queue").json()

    def interrupt(self) -> None:
        try:
            httpx.post(f"{self.base}/interrupt", timeout=self.timeout)
        except httpx.HTTPError as e:
            raise EngineError(f"interrupt failed: {type(e).__name__}")

    def delete_pending(self, prompt_id: str) -> None:
        try:
            httpx.post(f"{self.base}/queue", json={"delete": [prompt_id]}, timeout=self.timeout)
        except httpx.HTTPError as e:
            raise EngineError(f"could not remove queued prompt: {type(e).__name__}")

    def fetch(self, filename: str, subfolder: str, kind: str) -> bytes:
        q = urlencode({"filename": filename, "subfolder": subfolder, "type": kind})
        return self._get(f"/view?{q}", timeout=60.0).content
