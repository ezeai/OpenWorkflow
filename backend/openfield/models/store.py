"""Versioned registry of user decisions and verified facts about local model files.

Holds only what the user or a verified check has established: identity
confirmations, recorded validations, managed-download records, and the hash
cache. Discovery results are recomputed each scan and are NOT stored as truth.

Files: <home>/models/registry.json and <home>/models/hash_cache.json.
Writes are atomic. An unreadable file is renamed to *.corrupt, never overwritten.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class RegistryData:
    identities: dict = field(default_factory=dict)   # model -> variant -> role -> {path,size,mtime_ns,confirmed_at}
    validations: dict = field(default_factory=dict)  # model -> variant -> {validated_at, checks}
    managed: dict = field(default_factory=dict)      # model -> variant -> role -> {path,size,sha256,source_url,downloaded_at}
    smoke_tests: list = field(default_factory=list)  # reserved: a real inference record (not produced yet)
    workflow_validations: dict = field(default_factory=dict)  # workflow id -> {validated_at, checks, version}
    last_scan_at: str | None = None


class ModelRegistryStore:
    def __init__(self, models_dir: Path):
        self.dir = Path(models_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "registry.json"
        self.hash_path = self.dir / "hash_cache.json"
        self.load_errors: list[str] = []

    # ---- registry ----
    def load(self) -> RegistryData:
        self.load_errors = []
        if not self.path.exists():
            return RegistryData()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or raw.get("schema_version") != SCHEMA_VERSION:
                raise ValueError(f"unsupported registry schema {raw.get('schema_version') if isinstance(raw, dict) else type(raw).__name__}")
        except (OSError, ValueError) as e:
            self._quarantine(self.path)
            self.load_errors.append(f"registry unreadable, moved aside: {type(e).__name__}: {e}")
            return RegistryData()
        data = RegistryData(
            identities=self._dict(raw, "identities"),
            validations=self._dict(raw, "validations"),
            managed=self._dict(raw, "managed"),
            smoke_tests=raw.get("smoke_tests") if isinstance(raw.get("smoke_tests"), list) else [],
            workflow_validations=self._dict(raw, "workflow_validations"),
            last_scan_at=raw.get("last_scan_at") if isinstance(raw.get("last_scan_at"), str) else None,
        )
        return data

    def _dict(self, raw: dict, key: str) -> dict:
        val = raw.get(key)
        if isinstance(val, dict):
            return val
        self.load_errors.append(f"registry section '{key}' malformed; ignored")
        return {}

    def save(self, data: RegistryData) -> None:
        payload = {"schema_version": SCHEMA_VERSION, "identities": data.identities, "validations": data.validations,
                   "managed": data.managed, "smoke_tests": data.smoke_tests,
                   "workflow_validations": data.workflow_validations, "last_scan_at": data.last_scan_at}
        _atomic_write(self.path, json.dumps(payload, indent=2))

    # ---- hash cache (keyed by path; valid only while size and mtime match) ----
    def load_hashes(self) -> dict:
        if not self.hash_path.exists():
            return {}
        try:
            raw = json.loads(self.hash_path.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
        except (OSError, ValueError):
            self._quarantine(self.hash_path)
            return {}

    def save_hashes(self, cache: dict) -> None:
        _atomic_write(self.hash_path, json.dumps(cache, indent=2))

    def _quarantine(self, p: Path) -> None:
        target = p.with_name(p.name + ".corrupt")
        try:
            os.replace(p, target)
        except OSError:
            pass


def _atomic_write(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
