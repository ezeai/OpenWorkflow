"""Per-model status, computed from catalogue facts, the current scan and recorded decisions.

Each status is its own field. None of them is collapsed into a single 'Ready' label:
  discovered         a file with an expected name exists in an expected folder
  identified         the user confirmed the file, and its size and mtime still match that confirmation
  header_ok          format header parsed (not a content or identity check)
  validated          every required component is identified and passes the header check
  license.status     from the catalogue: verified | restricted | unverified
  smoke_tested       a real inference record exists (none are produced yet)
  open_weight_eligibility  eligible | review | not_eligible, per docs/OPEN_WEIGHTS_POLICY.md
"""
from __future__ import annotations

from pathlib import Path

from .discovery import FileEntry, ScanResult
from .schema import CatalogModel, Component, Variant
from .store import RegistryData


def _nested(d, *keys):
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def _in_folders(path: str, folders: list[str]) -> bool:
    parts = {p.lower() for p in Path(path).parts}
    return any(f.lower() in parts for f in folders)


def _file_view(f: FileEntry) -> dict:
    return {"path": f.path, "name": f.name, "size": f.size, "ownership": f.ownership, "format": f.format,
            "header_ok": f.header_ok, "header_error": f.header_error, "is_symlink": f.is_symlink,
            "link_target": f.link_target, "root": f.root_label}


def _component_status(model_id: str, variant: Variant, comp: Component, scan: ScanResult, reg: RegistryData) -> dict:
    by_name = scan.by_name()
    matches: list[FileEntry] = []
    for name in comp.filenames:
        for f in by_name.get(name.lower(), []):
            if _in_folders(f.path, comp.folders) and f not in matches:
                matches.append(f)
    ident = _nested(reg.identities, model_id, variant.id, comp.role)
    confirmed = None
    if isinstance(ident, dict):
        for f in matches:
            if f.path == ident.get("path") and f.size == ident.get("size") and f.mtime_ns == ident.get("mtime_ns"):
                confirmed = f
                break
    partial = False
    if comp.require_all and matches:
        present = {f.name.lower() for f in matches}
        partial = not all(n.lower() in present for n in comp.filenames)
        if partial:
            matches = []  # an incomplete shard set is not a usable component
    if not matches:
        state = "missing"
    elif confirmed is not None:
        state = "identified"
    else:
        state = "found"
    return {
        "role": comp.role,
        "state": state,
        "license_status": comp.license_status,
        "license_note": comp.license_note,
        "files": [_file_view(f) for f in matches],
        "confirmed_path": confirmed.path if confirmed else None,
        # a confirmation exists but the file changed or disappeared since then
        "identity_stale": bool(isinstance(ident, dict) and matches and confirmed is None),
        "partial": partial,
    }


def model_status(model: CatalogModel, scan: ScanResult, reg: RegistryData) -> dict:
    variants = []
    for v in model.variants:
        comps = [_component_status(model.id, v, c, scan, reg) for c in v.components]
        discovered = any(c["state"] != "missing" for c in comps)
        complete = all(c["state"] != "missing" for c in comps)
        identified = all(c["state"] == "identified" for c in comps)
        header_ok = identified and all(
            f["header_ok"] for c in comps for f in c["files"] if f["path"] == c["confirmed_path"])
        managed_map = reg.managed.get(model.id)
        managed = managed_map.get(v.id) if isinstance(managed_map, dict) else None
        variants.append({
            "id": v.id,
            "name": v.name,
            "publisher_files": v.publisher_files,
            "discovered": discovered,
            "complete": complete,
            "identified": identified,
            "header_ok": header_ok,
            "validated": identified and header_ok and bool(_nested(reg.validations, model.id, v.id)),
            "managed": bool(managed),
            "components": comps,
        })
    discovered = any(v["discovered"] for v in variants)
    identified = any(v["identified"] for v in variants)
    managed = any(v["managed"] for v in variants)
    if managed:
        category = "managed"
    elif identified:
        category = "installed"
    elif discovered:
        category = "discovered"
    else:
        category = "missing"

    lic = model.license
    all_components_verified = all(c.license_status == "verified" for v in model.variants for c in v.components)
    if lic.status == "restricted" or model.open_weight_policy == "not_eligible":
        eligibility = "not_eligible"
    elif lic.status == "verified" and all_components_verified and model.open_weight_policy == "eligible":
        eligibility = "eligible"
    else:
        eligibility = "review"

    return {
        "id": model.id,
        "name": model.name,
        "family": model.family,
        "tasks": model.tasks,
        "publisher": model.publisher,
        "source_url": model.source_url,
        "category": category,
        "discovered": discovered,
        "identified": identified,
        "validated": any(v["validated"] for v in variants),
        "smoke_tested": any(s.get("model_id") == model.id for s in reg.smoke_tests if isinstance(s, dict)),
        "download_available": any(v.download is not None for v in model.variants),
        "license": lic.model_dump(),
        "open_weight_eligibility": eligibility,
        "variants": variants,
        "limitations": model.limitations,
        "obtain_from": model.obtain_from,
        "approx_size_gb": model.approx_size_gb,
        "install_guide": model.install_guide,
    }
