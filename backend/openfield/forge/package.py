"""The .forgeapp package: one app (workflow file, saved spec, dependency notes) as a zip.

Layout (fixed; any other entry is refused):
  manifest.json            format, schema version, names, hashes, creation time
  workflow/source.json     the workflow file exactly as it was imported
  ui-spec.json             the saved app spec
  dependencies.json        node types, custom node packs and model file names the app needs (no weights)
  README.md                a short plain-language note

A package never contains model weights, credentials, run outputs, or code to execute. Importing one
creates a new app; it never overwrites an existing one. Imported specs are validated against the
imported workflow before they are shown as usable.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from pathlib import Path

from . import uispec
from .store import WorkflowStore

FORMAT = "forgeapp"
SCHEMA_VERSION = 1
MAX_PACKAGE_BYTES = 30 * 1024 * 1024
MAX_ENTRY_BYTES = 25 * 1024 * 1024
MAX_EXPANSION = 60                          # uncompressed size may not exceed this many times the package size
ENTRIES = ("manifest.json", "workflow/source.json", "ui-spec.json", "dependencies.json", "README.md")
# Optional: the app's own work beside the workflow. Each is checked before it is written back on import.
WORKSPACE = ("workspace/canvas.json", "workspace/loras.json")          # canvas changes and added nodes; LoRA stack
COVER_ENTRIES = ("workspace/cover.png", "workspace/cover.jpg", "workspace/cover.webp")
ALLOWED = ENTRIES + WORKSPACE + COVER_ENTRIES
COVER_EXT = {"workspace/cover.png": "png", "workspace/cover.jpg": "jpg", "workspace/cover.webp": "webp"}
_SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]+")


class PackageError(ValueError):
    """The package cannot be read or is not safe to import. The message is shown to the user."""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build(store: WorkflowStore, rec: dict, analysis: dict, spec_saved: dict, created_at: str, forge_version: str,
          extras: dict[str, bytes] | None = None) -> bytes:
    extras = {k: v for k, v in (extras or {}).items() if k in ALLOWED[len(ENTRIES):]}
    workflow_bytes = store.source_bytes(rec["id"])
    spec = spec_saved["spec"]
    deps = {
        "node_types_missing": analysis["unknown_node_types"],
        "custom_node_packs": analysis["custom_node_packs"],
        "model_files": [{"file": m["file"], "input": m["input"], "class_type": m["class_type"], "status_at_export": m["status"]}
                        for m in analysis["model_files"]],
        "note": "Forge does not install custom nodes or model files. Install what is listed, then import.",
    }
    readme = (
        f"# {rec['name']}\n\n"
        "A Forge app package. It holds the ComfyUI workflow, the app's saved controls, and notes on what it needs.\n\n"
        "It does not include model files. Install the models listed in dependencies.json into ComfyUI yourself, "
        "then import this package in Forge.\n"
    )
    manifest = {
        "format": FORMAT, "schema_version": SCHEMA_VERSION, "name": rec["name"], "created_at": created_at,
        "forge_version": forge_version, "workflow_sha256": _sha(workflow_bytes), "spec_revision": spec_saved["revision"],
        "entries": {k: _sha(v) for k, v in {
            "workflow/source.json": workflow_bytes,
            "ui-spec.json": json.dumps(spec, indent=2).encode("utf-8"),
            "dependencies.json": json.dumps(deps, indent=2).encode("utf-8"),
            "README.md": readme.encode("utf-8"),
            **extras,
        }.items()},
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest, indent=2))
        z.writestr("workflow/source.json", workflow_bytes)
        z.writestr("ui-spec.json", json.dumps(spec, indent=2))
        z.writestr("dependencies.json", json.dumps(deps, indent=2))
        z.writestr("README.md", readme)
        for name, data in extras.items():
            z.writestr(name, data)
    return buf.getvalue()


def safe_filename(name: str) -> str:
    base = re.sub(r"\.{2,}", "_", _SAFE_NAME.sub("_", name)).strip("._")[:60]
    return (base or "app") + ".forgeapp"


def read(data: bytes) -> dict:
    """Check a package and return its parts. Raises PackageError with a readable reason."""
    if len(data) > MAX_PACKAGE_BYTES:
        raise PackageError(f"the package is larger than {MAX_PACKAGE_BYTES // (1024 * 1024)} MB")
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise PackageError("this file is not a valid .forgeapp package")
    with z:
        infos = z.infolist()
        names = [i.filename for i in infos]
        if len(names) != len(set(names)):
            raise PackageError("the package lists a file twice")
        for i in infos:
            n = i.filename
            if n.startswith(("/", "\\")) or ".." in n.replace("\\", "/").split("/") or ":" in n or "\\" in n:
                raise PackageError(f"the package contains an unsafe path ({n[:60]!r})")
            if n.endswith("/"):
                raise PackageError("the package contains a folder entry; packages are flat files")
            if n not in ALLOWED:
                raise PackageError(f"the package contains a file that is not part of the format ({n[:60]!r})")
            if i.file_size > MAX_ENTRY_BYTES:
                raise PackageError(f"{n} is larger than {MAX_ENTRY_BYTES // (1024 * 1024)} MB")
        total = sum(i.file_size for i in infos)
        if total > MAX_PACKAGE_BYTES or total > max(1, len(data)) * MAX_EXPANSION:
            raise PackageError("the package expands to an unreasonable size")
        missing = [e for e in ENTRIES if e not in names]
        if missing:
            raise PackageError(f"the package is missing {', '.join(missing)}")
        parts = {n: z.read(n) for n in names}
    try:
        manifest = json.loads(parts["manifest.json"].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise PackageError("manifest.json is not valid JSON")
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
        raise PackageError("this is not a Forge app package")
    sv = manifest.get("schema_version")
    if not isinstance(sv, int) or sv > SCHEMA_VERSION or sv < 1:
        raise PackageError(f"this package uses format version {sv!r}; this Forge reads up to {SCHEMA_VERSION}. Update Forge to open it.")
    hashes = manifest.get("entries") or {}
    for name, digest in hashes.items():
        if name in parts and _sha(parts[name]) != digest:
            raise PackageError(f"{name} does not match its checksum; the package was changed or damaged")
    workflow_bytes = parts["workflow/source.json"]
    if manifest.get("workflow_sha256") != _sha(workflow_bytes):
        raise PackageError("the workflow does not match the package checksum")
    try:
        spec = json.loads(parts["ui-spec.json"].decode("utf-8"))
        uispec.parse(spec)
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise PackageError("ui-spec.json is not valid JSON")
    except ValueError as e:
        raise PackageError(f"the app's controls are not valid: {e}")
    name = manifest.get("name")
    if not isinstance(name, str) or not name.strip():
        raise PackageError("the package has no app name")
    covers = [n for n in parts if n in COVER_ENTRIES]
    if len(covers) > 1:
        raise PackageError("the package holds more than one cover picture")
    extras = {n: parts[n] for n in parts if n in WORKSPACE or n in COVER_ENTRIES}
    for n in WORKSPACE:
        if n in extras:
            try:
                json.loads(extras[n].decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise PackageError(f"{n} is not valid JSON")
    return {"manifest": manifest, "workflow": workflow_bytes, "spec": spec, "name": name.strip()[:120], "extras": extras}


def write_extras(extras: dict[str, bytes], wid: str, workflows_root: Path, covers_root: Path) -> list[str]:
    """Put the package's own work beside a new app. Returns the names that were written."""
    written = []
    d = workflows_root / wid
    for n, data in extras.items():
        if n == "workspace/canvas.json":
            target = d / "graph_edits.json"
        elif n == "workspace/loras.json":
            target = d / "loras.json"
        elif n in COVER_EXT:
            covers_root.mkdir(parents=True, exist_ok=True)
            target = covers_root / f"{wid}.{COVER_EXT[n]}"
        else:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        written.append(n)
    return written


def read_extras(wid: str, workflows_root: Path, covers_root: Path) -> dict[str, bytes]:
    """The app's own work as it is now, in the same names a package uses."""
    out = {}
    for n, p in (("workspace/canvas.json", workflows_root / wid / "graph_edits.json"),
                 ("workspace/loras.json", workflows_root / wid / "loras.json")):
        if p.is_file():
            out[n] = p.read_bytes()
    for f in covers_root.glob(f"{wid}.*") if covers_root.is_dir() else []:
        out[f"workspace/cover.{f.suffix[1:]}"] = f.read_bytes()
        break
    return out


def import_into(store: WorkflowStore, data: bytes, forge_version: str, existing_names: list[str],
                workflows_root: Path, covers_root: Path) -> dict:
    """Create a new app from a package. Never overwrites: the new app gets a new id and, if needed, a new name."""
    parts = read(data)
    rec = store.add(parts["workflow"], _unique(parts["name"], existing_names), f"package:{parts['manifest'].get('name', '')[:60]}")
    spec = dict(parts["spec"])
    spec["workflow_id"] = rec["id"]
    spec["generated_by"] = "user"
    saved = store.save_spec(rec["id"], spec, None)
    carried = write_extras(parts["extras"], rec["id"], workflows_root, covers_root)
    return {"workflow": rec, "spec_revision": saved["revision"], "package_forge_version": parts["manifest"].get("forge_version"),
            "package_created_at": parts["manifest"].get("created_at"), "carried": carried}


def duplicate_into(store: WorkflowStore, wid: str, existing_names: list[str], workflows_root: Path, covers_root: Path) -> dict:
    """A copy of an app: same workflow file, its own copy of the saved spec, canvas changes, LoRA stack and cover.
    The original is not touched."""
    src = store.get(wid)
    saved = store.load_spec(wid)
    if saved is None:
        raise PackageError("save the app before duplicating it")
    rec = store.add(store.source_bytes(wid), _unique(f"{src['name']} copy", existing_names), f"duplicate:{wid}")
    spec = dict(saved["spec"])
    spec["workflow_id"] = rec["id"]
    spec["generated_by"] = "user"
    store.save_spec(rec["id"], spec, None)
    write_extras(read_extras(wid, workflows_root, covers_root), rec["id"], workflows_root, covers_root)
    return {"workflow": rec}


def _unique(name: str, existing: list[str]) -> str:
    taken = {n.lower() for n in existing}
    if name.lower() not in taken:
        return name
    base = re.sub(r" \(\d+\)$", "", name)
    n = 2
    while f"{base} ({n})".lower() in taken:
        n += 1
    return f"{base} ({n})"
