"""Persistent, validated application settings. One JSON file, atomic writes, secrets never stored here."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

REPO_ROOT = Path(__file__).resolve().parents[2]


def default_home() -> Path:
    return Path(os.environ.get("OPENFIELD_HOME") or REPO_ROOT / "data")


class Settings(BaseModel):
    schema_version: int = 1
    comfy_url: str = "http://127.0.0.1:8188"
    comfy_root: str = ""                       # portable install root (optional, enables launch)
    model_dirs: list[str] = Field(default_factory=list)   # extra ComfyUI "models" dirs, read in place
    projects_dir: str = ""
    ffmpeg_path: str = ""
    allow_remote_integrations: bool = False    # must stay False for the Open Weights path
    first_run_complete: bool = False
    llm_url: str = "http://127.0.0.1:11434"    # local Ollama server for Forge layout proposals (loopback only)
    llm_model: str = ""                          # chosen in Apps; empty means the first installed chat model

    @field_validator("comfy_url")
    @classmethod
    def _loopback_only(cls, v: str) -> str:
        from urllib.parse import urlparse
        host = urlparse(v).hostname or ""
        if host not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("comfy_url must be a loopback address; remote engines are not part of the local path")
        return v.rstrip("/")

    @field_validator("llm_url")
    @classmethod
    def _llm_loopback_only(cls, v: str) -> str:
        from urllib.parse import urlparse
        host = urlparse(v).hostname or ""
        if host not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("llm_url must be a loopback address; Forge does not send workflow details to remote models")
        return v.rstrip("/")


class SettingsStore:
    def __init__(self, home: Path | None = None):
        self.home = Path(home) if home else default_home()
        self.home.mkdir(parents=True, exist_ok=True)
        self.path = self.home / "config.json"

    def load(self) -> Settings:
        if not self.path.exists():
            s = Settings(projects_dir=str(self.home / "projects"))
            return s
        try:
            return Settings.model_validate_json(self.path.read_text(encoding="utf-8"))
        except Exception:
            # keep the unreadable file for recovery instead of overwriting it
            self.path.replace(self.path.with_suffix(".json.corrupt"))
            return Settings(projects_dir=str(self.home / "projects"))

    def save(self, s: Settings) -> Settings:
        s = Settings.model_validate(s.model_dump())
        fd, tmp = tempfile.mkstemp(dir=self.home, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(s.model_dump(), f, indent=2)
        os.replace(tmp, self.path)
        return s
