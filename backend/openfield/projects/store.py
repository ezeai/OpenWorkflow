"""Project persistence: one folder per project, atomic writes, backup recovery, optimistic concurrency.

Layout (portable; no absolute paths inside):
  <projects_root>/<project_id>/project.json       metadata + entities (with version history)
  <projects_root>/<project_id>/project.json.bak   previous good copy, used for recovery
  <projects_root>/<project_id>/assets.json        asset registry
  <projects_root>/<project_id>/assets/images/     imported images (never overwritten)

Every write goes through a temp file and os.replace. The previous file is copied
to .bak first, so an interrupted write leaves either the old or the new state.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError

from .schema import MAX_TITLE_CHARS, SCHEMA_VERSION, ProjectMeta, check_id, new_id


class ProjectError(Exception):
    status = 400


class NotFound(ProjectError):
    status = 404


class Conflict(ProjectError):
    status = 409


class Unreadable(ProjectError):
    status = 500


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _is_valid_json(path: Path) -> bool:
    try:
        json.loads(path.read_text(encoding="utf-8"))
        return True
    except (OSError, ValueError):
        return False


def _atomic_write(path: Path, text: str, keep_backup: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # never replace the good backup with a damaged main file
    if keep_backup and path.exists() and _is_valid_json(path):
        shutil.copy2(path, path.with_name(path.name + ".bak"))
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


def read_json_with_backup(path: Path) -> tuple[dict, str | None]:
    """Return (data, recovery_note). Falls back to .bak if the main file is unreadable."""
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (OSError, ValueError) as main_err:
        bak = path.with_name(path.name + ".bak")
        try:
            data = json.loads(bak.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise Unreadable(f"{path.name} is unreadable and no usable backup exists ({type(main_err).__name__})")
        return data, f"{path.name} was unreadable; restored from the previous saved copy"


def migrate_manifest(raw: dict) -> dict:
    version = raw.get("schema_version")
    if not isinstance(version, int):
        raise Unreadable("project manifest has no schema_version")
    if version > SCHEMA_VERSION:
        raise Unreadable(f"this project was saved by a newer Open Workflow (schema {version}); update Open Workflow to open it")
    if version < 1:
        raise Unreadable(f"unsupported project schema {version}")
    if version == 2:  # v2 -> v3 adds continuity rules, warning acknowledgements and graphs (empty)
        raw.setdefault("continuity_rules", [])
        raw.setdefault("warning_acks", [])
        raw.setdefault("graphs", [])
        raw["schema_version"] = 3
    if version == 1:  # v1 -> v2 adds script, shots and dismissed suggestions with empty defaults
        raw.setdefault("script_text", "")
        raw.setdefault("shots", [])
        raw.setdefault("dismissed_suggestions", [])
        raw["schema_version"] = 2
        version = 2
    return raw


@dataclass
class ProjectSummary:
    id: str
    title: str
    updated_at: str
    readable: bool
    note: str | None = None


class ProjectStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._locks: dict[str, threading.RLock] = {}
        self._guard = threading.Lock()

    def _lock(self, pid: str) -> threading.RLock:
        with self._guard:
            return self._locks.setdefault(pid, threading.RLock())

    def dir(self, pid: str) -> Path:
        pid = check_id(pid)
        p = (self.root / pid).resolve()
        if self.root.resolve() not in p.parents:
            raise NotFound("project not found")
        return p

    def manifest_path(self, pid: str) -> Path:
        return self.dir(pid) / "project.json"

    # ---- listing ----
    def list(self) -> list[ProjectSummary]:
        out: list[ProjectSummary] = []
        for d in sorted(self.root.iterdir()) if self.root.exists() else []:
            if not d.is_dir() or (not (d / "project.json").exists() and not (d / "project.json.bak").exists()):
                continue
            try:
                meta = self.read(d.name)
                out.append(ProjectSummary(meta.id, meta.title, meta.updated_at, True))
            except ProjectError as e:
                out.append(ProjectSummary(d.name, d.name, "", False, str(e)))
        out.sort(key=lambda s: s.updated_at, reverse=True)
        return out

    # ---- read / write ----
    def read(self, pid: str) -> ProjectMeta:
        return self.read_with_note(pid)[0]

    def read_with_note(self, pid: str) -> tuple[ProjectMeta, str | None]:
        """(meta, recovery_note). recovery_note is set when the backup copy was used."""
        path = self.manifest_path(pid)
        if not path.exists() and not path.with_name(path.name + ".bak").exists():
            raise NotFound("project not found")
        raw, note = read_json_with_backup(path)
        raw = migrate_manifest(raw)
        try:
            meta = ProjectMeta.model_validate(raw)
        except ValidationError as e:
            raise Unreadable(f"project manifest is invalid: {e.error_count()} error(s)")
        return meta, note

    def write(self, meta: ProjectMeta, expected_revision: int) -> ProjectMeta:
        """Persist meta if expected_revision still matches. Bumps revision."""
        with self._lock(meta.id):
            current = self.read(meta.id)
            if current.revision != expected_revision:
                raise Conflict(f"project changed elsewhere (revision {current.revision}, you had {expected_revision}); reload to see the latest")
            meta.revision = current.revision + 1
            meta.updated_at = now_iso()
            _atomic_write(self.manifest_path(meta.id), json.dumps(meta.model_dump(), indent=2, ensure_ascii=False), keep_backup=True)
            return meta

    def mutate(self, pid: str, fn):
        """Locked read-modify-write. fn(meta) changes meta in place and returns a result."""
        with self._lock(pid):
            meta, note = self.read_with_note(pid)
            result = fn(meta)
            self.write(meta, expected_revision=meta.revision)
            return result, note

    def create(self, title: str, description: str = "") -> ProjectMeta:
        title = title.strip()
        if not title or len(title) > MAX_TITLE_CHARS:
            raise ProjectError(f"title must be 1 to {MAX_TITLE_CHARS} characters")
        pid = new_id()
        ts = now_iso()
        meta = ProjectMeta(id=pid, title=title, description=description, created_at=ts, updated_at=ts, revision=1)
        (self.root / pid).mkdir(parents=True, exist_ok=False)
        _atomic_write(self.manifest_path(pid), json.dumps(meta.model_dump(), indent=2, ensure_ascii=False), keep_backup=False)
        _atomic_write(self.dir(pid) / "assets.json", json.dumps({"schema_version": SCHEMA_VERSION, "assets": []}, indent=2), keep_backup=False)
        return meta

    def delete(self, pid: str, confirm_title: str) -> None:
        with self._lock(pid):
            meta = self.read(pid)
            if confirm_title != meta.title:
                raise ProjectError("type the exact project title to confirm deletion")
            target = self.dir(pid)
            shutil.rmtree(target)

    def assets_path(self, pid: str) -> Path:
        return self.dir(pid) / "assets.json"
