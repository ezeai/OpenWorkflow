"""Portable export and import of a project.

An export is a zip with:
  project/                 project.json, assets.json, assets/images/ (optional)
  manifests/models.json    models used, licence status and eligibility, source page, file names
  manifests/dependencies.json   Open Workflow version, workflows, custom-node packs they need
  manifests/reproducibility.json  per generated output: seed, size, steps, prompts, models, versions
  workflows/<job>.json     the ComfyUI graph that produced an output (rebuilt from the job record)
  README.txt

Never included: model weights, settings, logs, or any absolute path. Paths are relative.
Import re-checks every file: archive paths, sizes, asset checksums, and schema version.
"""
from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import PurePosixPath

from .schema import SCHEMA_VERSION, new_id
from .store import ProjectError, now_iso

MAX_IMPORT_BYTES = 4 * 1024**3
MAX_IMPORT_FILES = 5000
APP_VERSION = "0.1.0"


def _safe_name(name: str) -> bool:
    p = PurePosixPath(name)
    return not p.is_absolute() and ".." not in p.parts and "" not in p.parts and not name.startswith("\\")


def build_export(store, pid: str, jobs: list[dict], catalog, workflows, include_assets: bool, build_graph) -> bytes:
    meta = store.read(pid)
    pdir = store.dir(pid)
    buf = io.BytesIO()
    created = now_iso()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name in ("project.json", "assets.json"):
            p = pdir / name
            if p.exists():
                z.write(p, f"project/{name}")
        if include_assets:
            for p in sorted((pdir / "assets" / "images").glob("*")):
                if p.is_file() and p.suffix != ".tmp":
                    z.write(p, f"project/assets/images/{p.name}")

        # workflows used by this project's shots
        used = sorted({s.workflow_id for s in meta.shots if s.workflow_id})
        wf_items = {w.id: w for w in workflows.items.values()}
        model_ids = sorted({r.model_id for wid in used if wid in wf_items for r in wf_items[wid].models})

        models = []
        for mid in model_ids:
            m = catalog.models.get(mid)
            if m is None:
                continue
            files = []
            for job in jobs:
                if job.get("project_id") != pid:
                    continue
                for f in job.get("models", []):
                    files.append({"role": f["role"], "file_name": f["name"], "size_bytes": f["size"],
                                  "comfy_name": f.get("comfy_name"), "identity_confirmed": f.get("identity_confirmed", False)})
            models.append({
                "id": m.id, "name": m.name, "publisher": m.publisher, "source_url": m.source_url,
                "license_id": m.license.id, "license_status": m.license.status,
                "license_note": m.license.commercial_restriction or m.license.notes,
                "open_weight_policy": m.open_weight_policy,
                "files_used_for_outputs": [json.loads(x) for x in sorted({json.dumps(x, sort_keys=True) for x in files})],
                "note": "Weights are NOT included. Obtain them from the source and place them as described in docs/OPTIONAL_MODELS.md.",
            })
        z.writestr("manifests/models.json", json.dumps({"created_at": created, "models": models}, indent=2, ensure_ascii=False))

        deps = {
            "openfield_version": APP_VERSION,
            "project_schema_version": SCHEMA_VERSION,
            "workflows": [{"id": wid, "version": wf_items[wid].version, "name": wf_items[wid].name,
                           "required_nodes": wf_items[wid].required_nodes, "required_packs": wf_items[wid].required_packs}
                          for wid in used if wid in wf_items],
            "note": "Custom-node packs listed must be installed in ComfyUI. Versions are not pinned here.",
        }
        z.writestr("manifests/dependencies.json", json.dumps(deps, indent=2, ensure_ascii=False))

        repro = []
        for job in jobs:
            if job.get("project_id") != pid or job.get("state") != "COMPLETED" or not job.get("output_asset_id"):
                continue
            repro.append({
                "job_id": job["id"], "output_asset_id": job["output_asset_id"], "workflow_id": job["workflow_id"],
                "workflow_version": job["workflow_version"], "parameters": job["params"],
                "prompt": job.get("prompt"), "models": job.get("models", []), "app_version": job.get("app_version"),
                "created_at": job.get("created_at"), "finished_at": job.get("finished_at"),
                "limitations": ["A seed does not guarantee identical output across software or hardware versions.",
                                "Camera settings are prompt guidance only."],
            })
            # the exact graph that ran, rebuilt from the job record (only if names were recorded)
            args = {}
            for f in job.get("models", []):
                key = {"diffusion": "unet", "text_encoder": "text_encoder", "vae": "vae"}.get(f["role"])
                if key and f.get("comfy_name"):
                    args[key] = f["comfy_name"]
            if job.get("prompt") and len(args) == 3:
                try:
                    g = build_graph(job["workflow_id"], job["prompt"]["positive"], job["prompt"]["negative"], job["params"], args)
                    z.writestr(f"workflows/{job['id']}.json", json.dumps(g, indent=2, ensure_ascii=False))
                except (ValueError, KeyError):
                    pass
        z.writestr("manifests/reproducibility.json", json.dumps({"created_at": created, "outputs": repro}, indent=2, ensure_ascii=False))

        z.writestr("README.txt", (
            f"Open Workflow project export ({created}).\n\n"
            "Contents: project/ (the project folder), manifests/ (models and licences, dependencies, reproducibility),\n"
            "workflows/ (the ComfyUI graphs that produced outputs).\n\n"
            "NOT included: model weights, your ComfyUI settings, logs, or any absolute path.\n"
            "To use this project elsewhere: install Open Workflow, import this zip, then install the models listed in\n"
            "manifests/models.json yourself. Read each model's licence before you use it.\n"
            "Outputs may differ on other hardware or software versions even with the same seed.\n"))
    return buf.getvalue()


def import_project(store, data: bytes):
    """Validate and import an export. Returns the new project id. Raises ProjectError on any problem."""
    if len(data) > MAX_IMPORT_BYTES:
        raise ProjectError("archive is too large")
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ProjectError("not a valid Open Workflow export (not a zip file)")
    names = z.namelist()
    if len(names) > MAX_IMPORT_FILES:
        raise ProjectError("archive has too many files")
    total = 0
    for info in z.infolist():
        if not _safe_name(info.filename):
            raise ProjectError(f"unsafe path in archive: {info.filename!r}")
        total += info.file_size
    if total > MAX_IMPORT_BYTES:
        raise ProjectError("archive expands to too much data")
    if "project/project.json" not in names:
        raise ProjectError("archive has no project/project.json")
    manifest = json.loads(z.read("project/project.json").decode("utf-8"))
    version = manifest.get("schema_version")
    if not isinstance(version, int) or version > SCHEMA_VERSION:
        raise ProjectError(f"this export was made by a newer Open Workflow (schema {version}); update Open Workflow to import it")

    pid = new_id()
    manifest["id"] = pid
    dest = store.root / pid
    dest.mkdir(parents=True, exist_ok=False)
    from .store import _atomic_write
    try:
        default_assets = json.dumps({"schema_version": SCHEMA_VERSION, "assets": []})
        assets_raw = z.read("project/assets.json").decode("utf-8") if "project/assets.json" in names else default_assets
        assets = json.loads(assets_raw).get("assets", [])
        for a in assets:
            rel = a.get("rel_path", "")
            if not rel.startswith("assets/images/"):
                raise ProjectError("an asset points outside the project's image folder")
            if f"project/{rel}" not in names:
                raise ProjectError(f"asset file missing from export: {rel}")
            content = z.read(f"project/{rel}")
            if hashlib.sha256(content).hexdigest() != a.get("sha256") or len(content) != a.get("size"):
                raise ProjectError(f"asset {a.get('id')} does not match its recorded checksum")
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        _atomic_write(dest / "assets.json", json.dumps(json.loads(assets_raw), indent=2), keep_backup=False)
        manifest["updated_at"] = now_iso()
        _atomic_write(dest / "project.json", json.dumps(manifest, indent=2, ensure_ascii=False), keep_backup=False)
        # validate the imported manifest with the real model before reporting success
        store.read(pid)
    except Exception:
        import shutil
        shutil.rmtree(dest, ignore_errors=True)
        raise
    return pid
