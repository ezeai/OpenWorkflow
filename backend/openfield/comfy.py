"""Minimal ComfyUI HTTP client. Health is reported honestly: reachable / unreachable, never assumed."""
from __future__ import annotations

import httpx


class ComfyClient:
    def __init__(self, base_url: str, timeout: float = 3.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def health(self) -> dict:
        try:
            r = httpx.get(f"{self.base_url}/system_stats", timeout=self.timeout)
            r.raise_for_status()
            data = r.json()
        except (httpx.HTTPError, ValueError) as e:
            return {"state": "unreachable", "url": self.base_url, "error": type(e).__name__}
        system = data.get("system", {})
        return {"state": "ok", "url": self.base_url, "comfyui_version": system.get("comfyui_version"),
                "devices": [{"name": d.get("name"), "vram_total": d.get("vram_total"), "vram_free": d.get("vram_free")}
                            for d in data.get("devices", [])]}

    def object_info(self) -> dict:
        r = httpx.get(f"{self.base_url}/object_info", timeout=30)
        r.raise_for_status()
        return r.json()

    def queue(self) -> dict:
        r = httpx.get(f"{self.base_url}/queue", timeout=self.timeout)
        r.raise_for_status()
        return r.json()
