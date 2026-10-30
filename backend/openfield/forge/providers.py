"""Execution providers for Forge runs: local ComfyUI (default) and Comfy Cloud (explicit, with consent).

Both providers expose the same runner interface the job engine already uses (submit, history, fetch, queue,
interrupt), so a cloud run has the same lifecycle as a local run. A job is always bound to one provider when it
is created. Nothing moves a failed local job to the cloud, and nothing moves a cloud job to the local engine.

Comfy Cloud is reached through the official Python SDK (`comfy-sdk`), using only the calls its documentation
shows: `Comfy(api_key=...)`, `workflows.from_file`, `client.run`, `job.get_outputs`, `output.to_file`.
The SDK is imported only when a cloud run is requested. Its raw HTTP endpoints are not documented, so none are
called directly here.

The API key comes from the environment variable named below. It is never written to a file, a job record, a
package, or a log. Live cloud behaviour has NOT been verified: no key was available for these tests.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import tempfile
import uuid
from pathlib import Path
from urllib.parse import urlparse

from ..generation.comfy import EngineError

KEY_ENV = "OPENFIELD_COMFY_API_KEY"
DEFAULT_CLOUD_URL = "https://cloud.comfy.org"
OUTPUT_CLASSES = {"SaveImage", "PreviewImage", "SaveImageAdvanced", "SaveVideo", "CreateVideo"}


class ProviderError(Exception):
    """A provider cannot be used. The message is safe to show; it never contains the key."""


def cloud_base_url() -> str:
    """The SDK reads COMFY_BASE_URL itself; this only reports what it will use."""
    return (os.environ.get("COMFY_BASE_URL") or "").strip() or DEFAULT_CLOUD_URL


def cloud_status() -> dict:
    key_set = bool(os.environ.get(KEY_ENV, "").strip())
    sdk = importlib.util.find_spec("comfy_sdk") is not None
    if not key_set:
        state, message = "not_configured", f"Comfy Cloud is not configured. Set the {KEY_ENV} environment variable to your key, then restart Open Workflow."
    elif not sdk:
        state, message = "sdk_missing", "The Comfy Cloud SDK (comfy-sdk) is not installed. Installing it needs your approval."
    else:
        state, message = "ready", "Comfy Cloud is ready. Each run still needs your consent, and nothing is sent until you confirm."
    url = cloud_base_url()
    return {"state": state, "message": message, "key_set": key_set, "sdk_installed": sdk,
            "host": urlparse(url).hostname, "verified_live": False}


def _sdk_client():
    key = os.environ.get(KEY_ENV, "").strip()
    if not key:
        raise ProviderError("Comfy Cloud is not configured")
    try:
        from comfy_sdk import Comfy       # imported only when a cloud run is requested
    except ImportError:
        raise ProviderError("the Comfy Cloud SDK (comfy-sdk) is not installed")
    return Comfy(api_key=key)


class CloudRunner:
    """The runner interface over the documented SDK calls. `submit` runs the job to completion (the SDK's `run`
    call blocks), so the GPU worker waits for the cloud job. Cancelling a cloud run is not supported yet."""

    def __init__(self, client_factory=None):
        self._factory = client_factory or _sdk_client
        self._done: dict[str, dict] = {}

    def reachable(self, wait: float = 0.0) -> bool:
        return cloud_status()["state"] == "ready"

    def submit(self, graph: dict) -> str:
        try:
            client = self._factory()
        except ProviderError as e:
            raise EngineError(str(e))
        with tempfile.TemporaryDirectory(prefix="openfield-cloud-") as tmp:
            wf_path = Path(tmp) / "workflow.json"
            wf_path.write_text(json.dumps(graph), encoding="utf-8")
            try:
                wf = client.workflows.from_file(str(wf_path))
                job = client.run(wf)
                files: dict[str, bytes] = {}
                images = []
                for nid, node in graph.items():
                    if node.get("class_type") not in OUTPUT_CLASSES:
                        continue
                    for i, output in enumerate(job.get_outputs(nid)):
                        out_path = Path(tmp) / f"{nid}-{i}.bin"
                        output.to_file(str(out_path))
                        name = f"{nid}-{i}-{out_path.stem}.png"
                        files[name] = out_path.read_bytes()
                        images.append({"node": nid, "filename": name})
            except ProviderError as e:
                raise EngineError(str(e))
            except Exception as e:        # never include the message body: it may echo request details
                raise EngineError(f"Comfy Cloud run failed ({type(e).__name__})")
        pid = uuid.uuid4().hex
        self._done[pid] = {"images": images, "files": files}
        return pid

    def history(self, prompt_id: str) -> dict | None:
        r = self._done.get(prompt_id)
        if r is None:
            return None
        outputs: dict = {}
        for im in r["images"]:
            outputs.setdefault(im["node"], {"images": []})["images"].append({"filename": im["filename"], "subfolder": "", "type": "output"})
        return {"status": {"status_str": "success", "completed": True}, "outputs": outputs}

    def queue(self) -> dict:
        return {"queue_running": [], "queue_pending": []}

    def interrupt(self) -> None:
        raise EngineError("cancelling a Comfy Cloud run is not supported yet")

    def delete_pending(self, prompt_id: str) -> None:
        raise EngineError("cancelling a Comfy Cloud run is not supported yet")

    def fetch(self, filename: str, subfolder: str, kind: str) -> bytes:
        for r in self._done.values():
            if filename in r["files"]:
                return r["files"][filename]
        raise EngineError("the cloud output is no longer available")


def preview(api_prompt: dict) -> dict:
    """What a cloud run would send. The digest binds the user's consent to exactly this payload."""
    canonical = json.dumps(api_prompt, sort_keys=True, separators=(",", ":"))
    texts = [v for n in api_prompt.values() for v in n.get("inputs", {}).values() if isinstance(v, str)]
    return {
        "provider": "Comfy Cloud",
        "host": urlparse(cloud_base_url()).hostname,
        "sends": [
            {"item": "workflow", "detail": f"the converted workflow: {len(api_prompt)} nodes, in API format"},
            {"item": "text", "detail": f"{len(texts)} text value(s), {sum(len(t) for t in texts)} characters in total, including your prompts"},
            {"item": "settings", "detail": "numeric and choice settings, such as size, seed and steps"},
        ],
        "does_not_send": ["local file paths", "model files", "your API key", "other projects or apps"],
        "digest": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "cost_note": "Cloud runs are billed by Comfy. Open Workflow does not show prices it has not verified.",
        "verified_live": False,
    }
