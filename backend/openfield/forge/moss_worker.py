"""A client for a MOSS-Image ComfyUI server. Local only: the address must be a loopback address.

The MOSS server is a separate ComfyUI with the MOSS support installed. Open Workflow sends it API-format graphs over
HTTP (POST /prompt), watches /history, and copies the finished image. It never installs or changes that server.
"""
from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

import httpx

# The main ComfyUI, which is the MOSS-capable build. Set OPENFIELD_MOSS_URL to use another server instead.
DEFAULT_URL = "http://127.0.0.1:8188"
TIMEOUT_S = 10


class WorkerError(Exception):
    """The MOSS server cannot be used. The message is safe to show."""


def check_loopback(url: str) -> str:
    u = urlparse(url or DEFAULT_URL)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise WorkerError("the MOSS server address must be an http or https address")
    if u.hostname != "localhost":
        try:
            if not ipaddress.ip_address(u.hostname).is_loopback:
                raise WorkerError("the MOSS server must run on this computer (a loopback address)")
        except ValueError:
            raise WorkerError("the MOSS server must run on this computer (a loopback address)")
    return f"{u.scheme}://{u.netloc}".rstrip("/")


class MossWorker:
    def __init__(self, url: str = DEFAULT_URL):
        self.base = check_loopback(url)

    def status(self) -> dict:
        try:
            r = httpx.get(f"{self.base}/system_stats", timeout=TIMEOUT_S)
            r.raise_for_status()
        except httpx.HTTPError:
            return {"state": "not_running", "url": self.base,
                    "message": "The MOSS server is not running. Start it, then try again."}
        return {"state": "ready", "url": self.base, "message": "The MOSS server is running on this computer."}

    def submit(self, graph: dict, client_id: str) -> str:
        try:
            r = httpx.post(f"{self.base}/prompt", json={"prompt": graph, "client_id": client_id}, timeout=60)
        except httpx.HTTPError:
            raise WorkerError("The MOSS server did not answer. Is it running?")
        if r.status_code != 200:
            raise WorkerError(f"The MOSS server refused the graph ({r.status_code}): {r.text[:300]}")
        body = r.json()
        if body.get("node_errors"):
            raise WorkerError(f"The MOSS server rejected some nodes: {list(body['node_errors'])[:5]}")
        return body["prompt_id"]

    def history(self, prompt_id: str) -> dict | None:
        """None while the job is still queued or running. Otherwise {"ok": bool, "outputs": [...], "messages": ...}."""
        try:
            r = httpx.get(f"{self.base}/history/{prompt_id}", timeout=TIMEOUT_S)
            r.raise_for_status()
        except httpx.HTTPError:
            raise WorkerError("The MOSS server did not answer while checking the job.")
        entry = r.json().get(prompt_id)
        if not entry:
            return None
        st = entry.get("status", {})
        if st.get("status_str") == "error":
            return {"ok": False, "outputs": [], "messages": st.get("messages", [])}
        if not st.get("completed"):
            return None
        outs = [i for n in entry.get("outputs", {}).values() for i in n.get("images", [])]
        return {"ok": True, "outputs": outs, "messages": []}

    def fetch(self, image: dict) -> bytes:
        try:
            r = httpx.get(f"{self.base}/view", params={"filename": image["filename"], "subfolder": image.get("subfolder", ""),
                                                      "type": image.get("type", "output")}, timeout=60)
            r.raise_for_status()
        except (httpx.HTTPError, KeyError):
            raise WorkerError("The finished image could not be copied from the MOSS server.")
        return r.content

    def text_result(self, prompt_id: str) -> dict | None:
        """For a text job: None while it runs, else {"ok": bool, "text": str | None, "messages": list}."""
        try:
            r = httpx.get(f"{self.base}/history/{prompt_id}", timeout=TIMEOUT_S)
            r.raise_for_status()
        except httpx.HTTPError:
            raise WorkerError("The MOSS server did not answer while checking the prompt helper.")
        entry = r.json().get(prompt_id)
        if not entry:
            return None
        st = entry.get("status", {})
        if st.get("status_str") == "error":
            return {"ok": False, "text": None, "messages": st.get("messages", [])}
        if not st.get("completed"):
            return None
        texts = [t for n in entry.get("outputs", {}).values() for t in n.get("text", [])]
        return {"ok": bool(texts), "text": texts[0] if texts else None, "messages": []}
