"""Entity operations on a loaded ProjectMeta. Pure functions; the store persists the result.

Versioning rule: a version snapshot is appended only when fields or references
actually change. Old versions are never edited or removed. Restoring an old
version creates a new version that copies its content.
"""
from __future__ import annotations

from .schema import FIELDS, Entity, ProjectMeta, Version, check_id, new_id, validate_fields, validate_refs
from .store import Conflict, NotFound, ProjectError, now_iso

MAX_NAME = 120


def find(meta: ProjectMeta, eid: str) -> Entity:
    check_id(eid)
    for e in meta.entities:
        if e.id == eid:
            return e
    raise NotFound("entity not found")


def create(meta: ProjectMeta, kind: str, name: str) -> Entity:
    if kind not in FIELDS:
        raise ProjectError(f"unknown kind '{kind}'")
    name = name.strip()
    if not name or len(name) > MAX_NAME:
        raise ProjectError(f"name must be 1 to {MAX_NAME} characters")
    ts = now_iso()
    snap = Version(version=1, created_at=ts, fields={}, refs=[], note="created")
    e = Entity(id=new_id(), kind=kind, name=name, fields={}, refs=[], current_version=1,
               versions=[snap], archived=False, revision=1, created_at=ts, updated_at=ts)
    meta.entities.append(e)
    return e


def update(meta: ProjectMeta, eid: str, base_revision: int, *, name: str | None = None,
           fields: dict | None = None, refs: list | None = None, archived: bool | None = None,
           asset_ids: set[str] | None = None) -> Entity:
    e = find(meta, eid)
    if base_revision != e.revision:
        raise Conflict(f"'{e.name}' changed elsewhere (revision {e.revision}); reload to see the latest")
    changed_content = False
    if name is not None:
        name = name.strip()
        if not name or len(name) > MAX_NAME:
            raise ProjectError(f"name must be 1 to {MAX_NAME} characters")
        if name != e.name:
            e.name = name
    if fields is not None:
        new_fields = validate_fields(e.kind, {**e.fields, **fields})
        new_fields = {k: v for k, v in new_fields.items() if v != ""}
        if new_fields != e.fields:
            e.fields = new_fields
            changed_content = True
    if refs is not None:
        new_refs = validate_refs(e.kind, refs)
        if asset_ids is not None:
            missing = [r["asset_id"] for r in new_refs if r["asset_id"] not in asset_ids]
            if missing:
                raise ProjectError("a reference points to an image that is not in this project")
        if new_refs != e.refs:
            e.refs = new_refs
            changed_content = True
    if archived is not None:
        e.archived = bool(archived)
    if changed_content:
        e.current_version += 1
        e.versions.append(Version(version=e.current_version, created_at=now_iso(), fields=dict(e.fields),
                                  refs=[dict(r) for r in e.refs], note="edit"))
    e.revision += 1
    e.updated_at = now_iso()
    return e


def restore(meta: ProjectMeta, eid: str, base_revision: int, version: int) -> Entity:
    e = find(meta, eid)
    if base_revision != e.revision:
        raise Conflict(f"'{e.name}' changed elsewhere (revision {e.revision}); reload to see the latest")
    src = next((v for v in e.versions if v.version == version), None)
    if src is None:
        raise NotFound("version not found")
    e.fields = dict(src.fields)
    e.refs = [dict(r) for r in src.refs]
    e.current_version += 1
    e.versions.append(Version(version=e.current_version, created_at=now_iso(), fields=dict(e.fields),
                              refs=[dict(r) for r in e.refs], note=f"restored from version {version}"))
    e.revision += 1
    e.updated_at = now_iso()
    return e
