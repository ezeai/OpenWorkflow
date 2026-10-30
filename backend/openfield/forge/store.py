"""Imported workflows, kept as the exact bytes the user gave, plus a small record beside them.

Layout: <home>/forge/workflows/<id>/source.json (never modified) and record.json.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import uuid
from pathlib import Path

from ..projects.store import _atomic_write, now_iso
from .workflow import WorkflowError, detect_format

MAX_BYTES = 20 * 1024 * 1024
_ID = re.compile(r"^[0-9a-f]{32}$")


class ForgeError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _clean_name(name: str) -> str:
    name = re.sub(r"\.(app\.)?json$", "", (name or "").strip(), flags=re.I)
    name = re.sub(r"[\x00-\x1f]", "", name)[:120].strip()
    return name or "Untitled workflow"


class WorkflowStore:
    def __init__(self, root: Path):
        self.root = root

    def _dir(self, wid: str) -> Path:
        if not _ID.match(wid or ""):
            raise ForgeError("unknown workflow", 404)
        return self.root / wid

    def add(self, data: bytes, name: str, origin: str, allow_copy: bool = True) -> dict:
        """Store a workflow file as a new app. With allow_copy False, a file already imported is not stored again:
        the existing app is returned, marked duplicate."""
        if len(data) > MAX_BYTES:
            raise ForgeError(f"workflow file is larger than {MAX_BYTES // (1024 * 1024)} MB", 413)
        try:
            parsed = json.loads(data.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise ForgeError(f"not valid JSON: {type(e).__name__}")
        fmt = detect_format(parsed)
        if fmt == "unsupported":
            raise ForgeError("this JSON is not a ComfyUI workflow (expected an editor workflow with 'nodes', or an API prompt)")
        if not allow_copy:
            digest = hashlib.sha256(data).hexdigest()
            for rec in self.list():
                if rec.get("sha256") == digest:
                    return {**rec, "duplicate": True}
        wid = uuid.uuid4().hex
        d = self.root / wid
        d.mkdir(parents=True, exist_ok=False)
        (d / "source.json").write_bytes(data)
        record = {"id": wid, "name": _clean_name(name), "origin": origin, "format": fmt, "sha256": hashlib.sha256(data).hexdigest(),
                  "size": len(data), "imported_at": now_iso()}
        _atomic_write(d / "record.json", json.dumps(record, indent=2), keep_backup=False)
        return record

    def get(self, wid: str) -> dict:
        p = self._dir(wid) / "record.json"
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise ForgeError("unknown workflow", 404)

    def source_bytes(self, wid: str) -> bytes:
        """The stored workflow file exactly as imported, after its checksum is confirmed."""
        rec = self.get(wid)
        data = (self._dir(wid) / "source.json").read_bytes()
        if hashlib.sha256(data).hexdigest() != rec["sha256"]:
            raise ForgeError("the stored workflow file has changed since import; re-import it", 409)
        return data

    def source(self, wid: str) -> dict:
        rec = self.get(wid)
        data = (self._dir(wid) / "source.json").read_bytes()
        if hashlib.sha256(data).hexdigest() != rec["sha256"]:
            raise ForgeError("the stored workflow file has changed since import; re-import it", 409)
        try:
            return json.loads(data.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise WorkflowError("stored workflow is no longer valid JSON")

    def list(self) -> list[dict]:
        out = []
        if self.root.is_dir():
            for d in self.root.iterdir():
                try:
                    out.append(self.get(d.name))
                except (ForgeError, json.JSONDecodeError, OSError):
                    continue
        return sorted(out, key=lambda r: r["imported_at"], reverse=True)

    def load_spec(self, wid: str) -> dict | None:
        """The saved UI spec with its revision, or None. A spec file that cannot be read is treated as absent."""
        self.get(wid)
        try:
            return json.loads((self._dir(wid) / "spec.json").read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    def save_spec(self, wid: str, spec: dict, base_revision: int | None) -> dict:
        cur = self.load_spec(wid)
        have = cur["revision"] if cur else 0
        if base_revision is not None and base_revision != have:
            raise ForgeError("the app was changed elsewhere; reload it before saving", 409)
        saved = {"revision": have + 1, "saved_at": now_iso(), "spec": spec}
        _atomic_write(self._dir(wid) / "spec.json", json.dumps(saved, indent=2), keep_backup=True)
        return saved

    def rename(self, wid: str, name: str) -> dict:
        rec = self.get(wid)
        rec["name"] = _clean_name(name)
        _atomic_write(self._dir(wid) / "record.json", json.dumps(rec, indent=2), keep_backup=False)
        return rec

    def delete(self, wid: str, confirm_name: str) -> None:
        rec = self.get(wid)
        if confirm_name != rec["name"]:
            raise ForgeError("type the workflow's exact name to delete it")
        shutil.rmtree(self._dir(wid))
