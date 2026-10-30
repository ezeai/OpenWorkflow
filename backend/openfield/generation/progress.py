"""Progress for one ComfyUI prompt, built from its websocket events.

ComfyUI sends "executing" when a node starts and "progress" for sampler steps, and both carry the node id.
The node id is shown as the node's title from the submitted graph, so the Jobs page can say "KSampler, step
12 of 20" rather than a raw id. Progress is best effort: when no step numbers arrive the record stays
indeterminate and the job still completes normally.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone

from websockets.sync.client import connect


def node_titles(graph: dict) -> dict[str, str]:
    return {nid: (node.get("_meta", {}).get("title") or node.get("class_type") or nid) for nid, node in graph.items()}


class PromptProgress:
    def __init__(self, prompt_id: str, titles: dict[str, str]):
        self.prompt_id = prompt_id
        self.titles = titles
        self.node: str | None = None
        self.step: int | None = None
        self.steps: int | None = None
        self.finished = 0

    def feed(self, message: dict) -> bool:
        """Apply one decoded websocket message. Returns True when the record changed."""
        kind, data = message.get("type"), message.get("data") or {}
        if data.get("prompt_id") not in (None, self.prompt_id):
            return False
        before = (self.node, self.step, self.steps, self.finished)
        if kind == "executing":
            node = data.get("node")
            if node is None:
                self.node, self.step, self.steps = None, None, None  # ComfyUI sends a null node when the prompt is done
            else:
                if self.node is not None and self.node != node:
                    self.finished += 1
                self.node, self.step, self.steps = node, None, None
        elif kind == "progress" and isinstance(data.get("value"), int) and isinstance(data.get("max"), int):
            self.node = data.get("node", self.node)
            self.step, self.steps = data["value"], data["max"]
        return (self.node, self.step, self.steps, self.finished) != before

    def record(self) -> dict:
        determinate = self.step is not None and bool(self.steps)
        return {
            "mode": "determinate" if determinate else "indeterminate",
            "node": self.titles.get(self.node) if self.node else None,
            "step": self.step,
            "steps": self.steps,
            "nodes_finished": self.finished,
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }


class ProgressListener:
    """Reads ComfyUI's websocket on a background thread and keeps the latest record for one prompt.

    Best effort: if the socket cannot connect or drops, the record stays as it was and the job carries on.
    Binary frames (live previews) are skipped.
    """

    def __init__(self, base_url: str, client_id: str, progress: PromptProgress):
        ws_base = base_url.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
        self.url = f"{ws_base}/ws?clientId={client_id}"
        self.progress = progress
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, name="comfy-progress", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def record(self) -> dict:
        with self.lock:
            return self.progress.record()

    def close(self) -> None:
        self.stop.set()

    def _run(self) -> None:
        try:
            with connect(self.url, open_timeout=5, max_size=None) as ws:
                while not self.stop.is_set():
                    try:
                        raw = ws.recv(timeout=1.0)
                    except TimeoutError:
                        continue
                    if isinstance(raw, bytes):
                        continue
                    try:
                        message = json.loads(raw)
                    except ValueError:
                        continue
                    with self.lock:
                        self.progress.feed(message)
        except Exception:  # progress is optional; the job's own status polling still decides the outcome
            return
