"""Forge routes: import workflows, list them, and analyse them. Nothing here runs a workflow."""
from __future__ import annotations

import hashlib
import copy
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from .forge import analysis, convert, deps, document, graphedits, graphview, llm, llm_providers, loras, module, navtabs, object_info, operator, package, pipeline, providers, propose, uispec, uploads, workflow
from .forge import run as forge_run
from .forge import characters, creator, identity, shots
from .forge.store import MAX_BYTES, ForgeError, WorkflowStore

router = APIRouter(prefix="/api/forge")
_TEMPLATE_NAME = re.compile(r"^[A-Za-z0-9_.\-]{1,160}$")


class RenameBody(BaseModel):
    name: str


class ModuleBody(BaseModel):
    workflow_id: str
    name: str
    description: str = ""
    inputs: list[module.Port] = []
    outputs: list[module.OutputPort]


class ModuleVersionBody(BaseModel):
    name: str | None = None
    description: str | None = None
    inputs: list[module.Port] = []
    outputs: list[module.OutputPort]


class PrepareBody(BaseModel):
    values: dict = {}


class ModuleRunBody(BaseModel):
    values: dict = {}
    version: int | None = None
    provider: Literal["local"] = "local"


def _comfy_has_input(url: str, name: str) -> bool:
    """Read-only check: does ComfyUI's input folder hold this file? Refuses (raises) when ComfyUI cannot be asked."""
    import httpx
    from .generation.comfy import BUSY_WAIT, get_patient
    try:
        r = get_patient(f"{url.rstrip('/')}/view", BUSY_WAIT, params={"filename": name, "type": "input"})
    except httpx.HTTPError:
        raise module.ModuleError("ComfyUI is not reachable, so the image input cannot be checked. Start ComfyUI and try again.")
    return r.status_code == 200


def _model_problem(s: dict) -> str:
    if s["status"] == "unknown":
        return f"model file '{s['file']}' cannot be checked; start ComfyUI so its model list is available"
    if s["status"] == "ambiguous":
        return f"model file '{s['file']}' matches more than one installed file"
    return f"model file '{s['file']}' is not installed in ComfyUI"


class DeleteBody(BaseModel):
    confirm_name: str


class TemplateBody(BaseModel):
    name: str


class SpecBody(BaseModel):
    spec: dict
    base_revision: int | None = None


class ProposeBody(BaseModel):
    request: str = Field(default="", max_length=2000)
    model: str | None = Field(default=None, max_length=100)
    current_spec: dict | None = None


TERMINAL_STATES = {"COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED", "RECOVERY_REQUIRED"}


class ConsentBody(BaseModel):
    acknowledged: bool = False
    digest: str = Field(default="", max_length=64)


class OperatorBody(BaseModel):
    endpoint: str = Field(default="ollama", max_length=40)
    base: str | None = Field(default=None, max_length=200)
    kind: Literal["ollama", "openai"] | None = None
    model: str = Field(min_length=1, max_length=200)
    message: str = Field(min_length=1, max_length=2000)
    history: list[dict] = Field(default_factory=list, max_length=12)


def _operator_endpoint(body: OperatorBody) -> llm_providers.Endpoint:
    if body.endpoint == "custom":
        if not body.base:
            raise llm_providers.ProviderError("give the server address")
        return llm_providers.custom(body.base, body.kind or "openai")
    preset = next((p for p in llm_providers.PRESETS if p.id == body.endpoint), None)
    if preset is None:
        raise llm_providers.ProviderError("unknown model server")
    return preset


class LoraItem(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    strength: float = 1.0
    enabled: bool = True


class LoraBody(BaseModel):
    items: list[LoraItem] = Field(default_factory=list, max_length=loras.MAX_LORAS)


class CharacterBody(BaseModel):
    name: str = Field(default="", max_length=200)
    notes: str = Field(default="", max_length=1000)


class CreatorPromptBody(BaseModel):
    idea: str = Field(default="", max_length=600)
    reference_b64: str | None = Field(default=None, max_length=8_000_000)
    endpoint: str = Field(default="ollama", max_length=40)
    base: str | None = Field(default=None, max_length=200)
    kind: Literal["ollama", "openai"] | None = None
    model: str = Field(min_length=1, max_length=200)


class CreatorRunBody(BaseModel):
    workflow_id: str = Field(min_length=32, max_length=32)
    prompt: str = Field(min_length=1, max_length=1200)
    count: int = Field(default=2, ge=1, le=4)
    seed_base: int = Field(default=1000, ge=0, le=2_000_000_000)
    megapixels: float = Field(default=1.0, ge=0.5, le=3.0)   # 1.0 is about 1K; 2.25 is about 1.5K


class DatasetBody(BaseModel):
    job_id: str = Field(min_length=32, max_length=32)
    preset_id: str | None = Field(default=None, max_length=40)


class AnchorFromRunBody(BaseModel):
    job_id: str = Field(min_length=32, max_length=32)


class CheckBody(BaseModel):
    job_id: str = Field(min_length=32, max_length=32)


class RunBody(BaseModel):
    values: dict = Field(default_factory=dict, max_length=200)
    provider: Literal["local", "cloud"] = "local"
    consent: ConsentBody | None = None
    replay_of: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")   # the run this one repeats, if any


# Loader node -> (input that lists its files, kind). Lists come from ComfyUI's own node definitions.
MODEL_LOADERS = {
    "LoraLoader": ("lora_name", "loras"), "LoraLoaderModelOnly": ("lora_name", "loras"),
    "CheckpointLoaderSimple": ("ckpt_name", "checkpoints"), "UNETLoader": ("unet_name", "diffusion_models"),
    "VAELoader": ("vae_name", "vae"), "CLIPLoader": ("clip_name", "text_encoders"),
    "UpscaleModelLoader": ("model_name", "upscale_models"), "ControlNetLoader": ("control_net_name", "controlnet"),
}


def _lora_store(request: Request) -> loras.Store:
    return loras.Store(request.app.state.services.home / "forge" / "workflows")


LORA_CLASSES = (("LoraLoader", "lora_name"), ("LoraLoaderModelOnly", "lora_name"))


def _known_loras(request: Request) -> set[str] | None:
    """ComfyUI's LoRA file names, or None when ComfyUI cannot be asked (names are then not checked)."""
    info = _info(request)
    if getattr(info, "source", "none") == "none":
        return None
    names: set[str] = set()
    for cls, inp in LORA_CLASSES:
        nd = info.defs.get(cls)
        spec = nd.input(inp) if nd else None
        if spec:
            names.update(c for c in (spec.choices or []) if isinstance(c, str))
    return names


def _store(request: Request) -> WorkflowStore:
    return WorkflowStore(request.app.state.services.home / "forge" / "workflows")


def _workflows_root(request: Request) -> Path:
    return request.app.state.services.home / "forge" / "workflows"


def _covers_root(request: Request) -> Path:
    return request.app.state.services.home / "forge" / "covers"


class AddNodeBody(BaseModel):
    type: str = Field(min_length=1, max_length=120)
    pos: list[float] = Field(min_length=2, max_length=2)


class GraphOpBody(BaseModel):
    op: str = Field(min_length=1, max_length=20)
    node: str = Field(min_length=1, max_length=80)
    state: str | None = Field(default=None, max_length=20)
    title: str | None = Field(default=None, max_length=200)


def _repeatable(values: dict, rec: dict) -> dict:
    """Whether a run can be repeated exactly: its seed was fixed when it ran, and no setting asked for a random one."""
    why = []
    seeds = [v for k, v in values.items() if "seed" in k.lower()]
    if not any(isinstance(v, int) and not isinstance(v, bool) for v in seeds):
        why.append("no fixed seed was recorded")
    if any(str(v).lower() == "randomize" for v in values.values()):
        why.append("a setting asks for a random seed when the run starts")
    return {"repeatable": not why, "why": why}


def _edits_path(request: Request, wid: str) -> Path:
    """Where a workflow's canvas-added nodes are kept. The id is checked by the store before this is used."""
    _store(request).get(wid)
    return request.app.state.services.home / "forge" / "workflows" / wid / "graph_edits.json"


def _templates_dir(request: Request) -> Path | None:
    root = request.app.state.services.settings().comfy_root
    if not root:
        return None
    p = Path(root) / "python_embeded" / "Lib" / "site-packages" / "comfyui_workflow_templates_json" / "templates"
    return p if p.is_dir() else None


INFO_TTL_S = 60.0          # the node list is ~8 MB from a running ComfyUI; re-read at most once a minute
_INFO: dict = {"at": 0.0, "url": None, "info": None}


def _info(request: Request) -> object_info.ObjectInfo:
    return info_for_app(request.app)


def info_for_app(app) -> object_info.ObjectInfo:
    """The node list for this app's configured ComfyUI, cached briefly. Used by routes and by pipeline runs.

    An expired list is served at once and refreshed in the background, so a page never waits on the ~9 s
    re-read of ComfyUI's node list. Only the very first read blocks, because there is nothing to show yet.
    """
    import threading
    import time

    url = app.state.services.settings().comfy_url.rstrip("/")
    cached = _INFO["info"] is not None and _INFO["url"] == url
    if cached and time.monotonic() - _INFO["at"] < INFO_TTL_S:
        return _INFO["info"]
    if cached:
        if not _INFO.get("refreshing"):
            _INFO["refreshing"] = True
            threading.Thread(target=_refresh_in_background, args=(app, url), daemon=True).start()
        return _INFO["info"]
    return _refresh_info(app, url)


def _refresh_in_background(app, url: str) -> None:
    import logging
    try:
        _refresh_info(app, url)
    except Exception:  # ComfyUI may be closing; the last good list stays in use and the next request retries
        logging.getLogger("openfield.forge").warning("node list refresh failed; keeping the last one", exc_info=True)


def _refresh_info(app, url: str) -> object_info.ObjectInfo:
    import httpx
    import time

    def get(path: str):
        r = httpx.get(url + path, timeout=30 if path == "/object_info" else 3)
        r.raise_for_status()
        return r.json()
    try:
        info = object_info.load(app.state.services.home, get)
        _INFO.update(at=time.monotonic(), url=url, info=info)
        return info
    finally:
        _INFO["refreshing"] = False


def _err(e: Exception):
    if isinstance(e, ForgeError):
        raise HTTPException(e.status, detail=str(e))
    if isinstance(e, workflow.WorkflowError):
        raise HTTPException(422, detail=str(e))
    if isinstance(e, module.ModuleError):
        raise HTTPException(422, detail=str(e))
    if isinstance(e, loras.LoraError):
        raise HTTPException(422, detail=str(e))
    raise e


def _module_store(request: Request) -> module.ModuleStore:
    return module.ModuleStore(request.app.state.services.home / "forge" / "modules")


def _public(c: module.Contract) -> dict:
    """A contract without the snapshot prompt, which is large and is served by /prepare when needed."""
    return c.model_dump(exclude={"prompt"})


def _forge_home(app) -> Path:
    return app.state.services.home / "forge"


def _nav_tabs_path(request: Request) -> Path:
    return _forge_home(request.app) / "nav_tabs.json"


def _pipeline_store(request: Request) -> pipeline.PipelineStore:
    return pipeline.PipelineStore(_forge_home(request.app) / "pipelines")


def _job_view(job: dict) -> dict:
    return {k: job.get(k) for k in ("id", "state", "kind", "provider", "workflow_id", "created_at", "models", "notes")}


def _comfy_free(url: str) -> None:
    """POST /free to ComfyUI: unload its models and free memory. Raises if the server refuses or cannot be reached."""
    import httpx
    r = httpx.post(f"{url.rstrip('/')}/free", json={"unload_models": True, "free_memory": True}, timeout=120)
    r.raise_for_status()


def _pipeline_context(app) -> pipeline.Context:
    """Everything a pipeline run needs, built from the app. The job engine calls on_app_terminal with the same app."""
    home = _forge_home(app)
    services = app.state.services

    def output_path(jid: str, job: dict) -> Path:
        return home / "runs" / jid / job["output_file"]["name"]

    return pipeline.Context(
        modules=module.ModuleStore(home / "modules"),
        runs=pipeline.RunStore(home / "pipeline_runs"),
        jobs=app.state.jobs,
        info=lambda: info_for_app(app),
        comfy_url=lambda: services.settings().comfy_url,
        has_input=_comfy_has_input,
        upload=uploads.send_to_comfy,
        output_path=output_path,
        free=_comfy_free,
    )


def on_app_terminal(app, job: dict) -> None:
    """Registered with the job engine. Moves a pipeline run on when one of its stages reaches a terminal state."""
    pipeline.on_terminal(job, _pipeline_context(app))


def _deps_ctx(request: Request) -> dict:
    """Manager's candidate mapping and the custom_nodes folder count, from the configured ComfyUI folder. Read only."""
    root = request.app.state.services.settings().comfy_root
    folders = None
    if root:
        cn = Path(root) / "ComfyUI" / "custom_nodes"
        if cn.is_dir():
            folders = sum(1 for p in cn.iterdir() if p.is_dir() and not p.name.startswith(("__", ".")))
    return {"pack_index": deps.load_pack_index(root), "custom_nodes_folders": folders}


def _weights(request: Request, model_files: list[dict]) -> dict:
    """Sizes of the model files this app uses, read from the model scan. File size is a floor for VRAM, not a measure of it."""
    sizes: dict[str, int] = {}
    for f in request.app.state.services.scan().files:
        sizes.setdefault(f.name.lower(), f.size)
    unique: dict[str, dict] = {}
    for m in model_files:
        if m["status"] not in ("available", "found_elsewhere"):
            continue
        name = Path(str(m["comfy_name"] or m["file"])).name
        unique.setdefault(name.lower(), {"file": name, "bytes": sizes.get(name.lower())})
    files = list(unique.values())
    measured = [f["bytes"] for f in files if f["bytes"] is not None]
    return {"total_bytes": sum(measured), "measured": len(measured), "unmeasured": len(files) - len(measured), "files": files}


def analyse_record(store: WorkflowStore, wid: str, info: object_info.ObjectInfo, deps_ctx: dict | None = None) -> dict:
    """Parse, convert and analyse one stored workflow. Shared by the routes and the tests."""
    rec = store.get(wid)
    data = store.source(wid)
    fmt = workflow.detect_format(data)
    if fmt == "ui":
        wf = workflow.parse_ui(data)
        conv = convert.convert(wf, info)
    else:
        prompt = workflow.parse_api(data)
        wf = None
        conv = convert.Conversion(prompt, [], [convert.WidgetSource(nid, k, "graph", nid, "widget")
                                               for nid, n in prompt.items() for k, v in (n.get("inputs") or {}).items()
                                               if not (isinstance(v, list) and len(v) == 2)],
                                  {nid: (n.get("_meta") or {}).get("title") or n["class_type"] for nid, n in prompt.items()})
        for n in prompt.values():
            if info.get(n["class_type"]) is None:
                conv.problems.append(convert.Problem("error", f"node type '{n['class_type']}' is not available ({info.source})"))
    return {"workflow": rec, "analysis": analysis.analyse(wf, conv, info, deps_ctx), "api_prompt": conv.prompt}


def build_router() -> APIRouter:

    @router.get("/workflows")
    def list_workflows(request: Request):
        """Each workflow with its readiness, so the app list can show it without opening every app. Read only."""
        store = _store(request)
        out = []
        for rec in store.list():
            try:
                an = analyse_record(store, rec["id"], _info(request), _deps_ctx(request))["analysis"]
                out.append({**rec, "readiness": an["readiness"], "reasons": an["reasons"][:3]})
            except Exception:
                out.append({**rec, "readiness": None, "reasons": []})
        return {"workflows": out}

    @router.post("/workflows")
    async def import_workflow(request: Request, name: str = "workflow.json", copy: bool = False):
        """Body: the workflow file's bytes. A file already imported is not stored twice unless copy is set."""
        length = request.headers.get("content-length") or ""
        if length.isdigit() and int(length) > MAX_BYTES:
            raise HTTPException(413, detail=f"workflow file is larger than {MAX_BYTES // (1024 * 1024)} MB")
        data = await request.body()
        try:
            rec = _store(request).add(data, name, "upload", allow_copy=copy)
        except Exception as e:
            _err(e)
        return {"workflow": rec, "duplicate": bool(rec.get("duplicate"))}

    @router.post("/workflows/preview")
    async def preview_workflow(request: Request, name: str = "workflow.json"):
        """What a file contains, before anything is imported: its format, how many nodes and subgraphs, the node types
        ComfyUI does not know, and any app that already holds the same file. Nothing is stored."""
        length = request.headers.get("content-length") or ""
        if length.isdigit() and int(length) > MAX_BYTES:
            raise HTTPException(413, detail=f"workflow file is larger than {MAX_BYTES // (1024 * 1024)} MB")
        data = await request.body()
        try:
            parsed = json.loads(data.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise HTTPException(422, detail="this file is not valid JSON")
        fmt = workflow.detect_format(parsed)
        if fmt == "unsupported":
            raise HTTPException(422, detail="this JSON is not a ComfyUI workflow (expected an editor workflow with 'nodes', or an API prompt)")
        info = _info(request)
        if fmt == "ui":
            g = graphview.flatten(parsed, info)
            nodes = [n for n in g["nodes"] if not n["ui_only"]]
            unknown = sorted({n["type"] for n in nodes if not n["known"]})
            subgraphs = len(g["subgraphs"])
        else:
            nodes = [v for v in parsed.values() if isinstance(v, dict)]
            unknown = sorted({str(v.get("class_type")) for v in nodes if info.get(str(v.get("class_type"))) is None})
            subgraphs = 0
        digest = hashlib.sha256(data).hexdigest()
        same = [{"id": r["id"], "name": r["name"]} for r in _store(request).list() if r.get("sha256") == digest]
        return {"name": name.strip()[:120] or "workflow.json", "format": fmt, "bytes": len(data), "sha256": digest,
                "nodes": len(nodes), "subgraphs": subgraphs, "unknown_node_types": unknown[:50],
                "unknown_count": len(unknown), "already_imported": same}

    @router.get("/templates")
    def list_templates(request: Request):
        d = _templates_dir(request)
        if d is None:
            return {"available": False, "reason": "Set the ComfyUI folder in Settings to browse its installed templates.", "templates": []}
        try:
            index = json.loads((d / "index.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"available": False, "reason": "ComfyUI's template index could not be read.", "templates": []}
        out = []
        for cat in index:
            for t in cat.get("templates") or []:
                name = t.get("name")
                if not isinstance(name, str) or not _TEMPLATE_NAME.match(name) or not (d / f"{name}.json").is_file():
                    continue
                out.append({"name": name, "title": t.get("title") or name, "category": cat.get("category"), "group": cat.get("title"),
                            "description": t.get("description") or "", "models": t.get("models") or [], "media": t.get("mediaType"),
                            "open_source": bool(t.get("openSource")), "uses_paid_api": name.startswith("api_") or not t.get("openSource", True)})
        return {"available": True, "source": "ComfyUI installed templates (comfyui-workflow-templates, MIT)", "templates": out}

    @router.post("/templates/import")
    def import_template(body: TemplateBody, request: Request):
        d = _templates_dir(request)
        if d is None or not _TEMPLATE_NAME.match(body.name):
            raise HTTPException(404, detail="template not found")
        path = d / f"{body.name}.json"
        if not path.is_file() or path.resolve().parent != d.resolve():
            raise HTTPException(404, detail="template not found")
        title = body.name
        try:
            for cat in json.loads((d / "index.json").read_text(encoding="utf-8")):
                for t in cat.get("templates") or []:
                    if t.get("name") == body.name and isinstance(t.get("title"), str):
                        title = t["title"]
        except (OSError, json.JSONDecodeError):
            pass
        try:
            rec = _store(request).add(path.read_bytes(), title, f"comfyui-template:{body.name}")
        except Exception as e:
            _err(e)
        return {"workflow": rec}

    @router.get("/workflows/{wid}")
    def get_workflow(wid: str, request: Request):
        try:
            return {"workflow": _store(request).get(wid)}
        except Exception as e:
            _err(e)

    @router.get("/models")
    def search_models(request: Request, q: str = "", folder: str = "", workflow: str = ""):
        """Files ComfyUI can load, from its own loader lists. `folder` narrows to one kind; `workflow` marks what it needs."""
        info = _info(request)
        needed: set[str] = set()
        if workflow:
            try:
                needed = {Path(m["file"]).name.lower() for m in analyse_record(_store(request), workflow, info)["analysis"]["model_files"]}
            except Exception as e:
                _err(e)
        needle = q.strip().lower()
        seen: dict[tuple[str, str], dict] = {}
        for node_class, (inp, kind) in MODEL_LOADERS.items():
            nd = info.defs.get(node_class)
            spec = nd.input(inp) if nd else None
            for name in (spec.choices or []) if spec else []:
                if not isinstance(name, str) or (folder and kind != folder):
                    continue
                if needle and needle not in name.lower():
                    continue
                key = (kind, name)
                row = seen.setdefault(key, {"kind": kind, "file": name, "loaded_by": [], "needed": Path(name).name.lower() in needed})
                row["loaded_by"].append(node_class)
        rows = sorted(seen.values(), key=lambda r: (not r["needed"], r["kind"], r["file"].lower()))
        return {"available": info.source != "none", "source": info.source, "count": len(rows), "items": rows[:500],
                "folders": sorted({k for k, _ in MODEL_LOADERS.values()})}

    @router.get("/workflows/{wid}/graph")
    def get_graph(wid: str, request: Request):
        """Read-only: the workflow's top-level nodes and the links between them, for viewing. Nothing is edited here."""
        try:
            data = _store(request).source(wid)
            if workflow.detect_format(data) != "ui":
                return {"format": workflow.detect_format(data), "nodes": [], "links": [],
                        "note": "API-format prompts have no editor layout; the links are shown as inputs."}
            workflow.parse_ui(data)   # refuses a file the editor could not have written
        except Exception as e:
            _err(e)
        # Subgraphs are opened in place, so the canvas shows every node that runs, not just the outer boxes.
        g = graphview.flatten(data, _info(request))
        path = _edits_path(request, wid)
        nodes, links = graphedits.apply_ops(g["nodes"] + graphedits.load(path), g["links"], graphedits.load_ops(path))
        return {"format": "ui", **g, "nodes": nodes, "links": links, "changes": len(graphedits.load_ops(path))}

    @router.get("/node-types")
    def node_types(request: Request, q: str = "", category: str = "", limit: int = 40):
        """ComfyUI node types whose name or category contains `q`, optionally one category, for the canvas's add-node search."""
        needle = q.strip().lower()[:80]
        folder = category.strip().lower()[:120]
        defs = _info(request).defs
        hits = [d for d in defs.values()
                if (not needle or needle in d.name.lower() or needle in d.category.lower())
                and (not folder or d.category.lower().startswith(folder))]
        # Names that start with the search come first, then the rest alphabetically.
        ranked = sorted(hits, key=lambda d: (not d.name.lower().startswith(needle), d.name))
        items = [{"type": d.name, "category": d.category,
                  "inputs": [{"name": i.name, "type": i.type} for i in d.inputs if not i.is_widget],
                  "widgets": [i.name for i in d.widgets()],
                  "outputs": [d.output_names[k] if k < len(d.output_names) else o for k, o in enumerate(d.outputs)],
                  "output_node": d.output_node}
                 for d in ranked[:min(max(limit, 1), 200)]]
        return {"count": len(hits), "items": items}

    @router.post("/workflows/{wid}/graph/ops")
    def add_graph_op(wid: str, body: GraphOpBody, request: Request):
        """Records one change to a node on the canvas: its state, its title, or removing it from the canvas.
        The imported file is not changed; the change is applied on top of it and can be undone."""
        data = _store(request).source(wid)
        if workflow.detect_format(data) != "ui":
            raise HTTPException(422, "only editor-format workflows can be changed on the canvas")
        path = _edits_path(request, wid)
        known = {n["id"] for n in graphview.flatten(data, _info(request))["nodes"]} | {n["id"] for n in graphedits.load(path)}
        try:
            return graphedits.add_op(path, body.model_dump(exclude_none=True), known)
        except graphedits.EditError as e:
            raise HTTPException(422, str(e))

    @router.post("/workflows/{wid}/graph/ops/undo")
    def undo_graph_op(wid: str, request: Request):
        """Takes back the most recent change on the canvas."""
        _store(request).get(wid)
        undone = graphedits.undo(_edits_path(request, wid))
        if undone is None:
            raise HTTPException(404, "there is no change to undo")
        return undone

    @router.get("/node-categories")
    def node_categories(request: Request):
        """The top-level ComfyUI categories with how many node types each holds, for the picker's category chips."""
        counts: dict[str, int] = {}
        for d in _info(request).defs.values():
            top = (d.category or "other").split("/")[0].strip() or "other"
            counts[top] = counts.get(top, 0) + 1
        return {"items": [{"name": k, "count": v} for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]}

    @router.post("/workflows/{wid}/graph/nodes")
    def add_node(wid: str, body: AddNodeBody, request: Request):
        """Adds a node to the canvas only. The imported file is not changed and the added node has no links, so runs are unaffected."""
        _store(request).get(wid)
        nd = _info(request).get(body.type)
        if nd is None:
            raise HTTPException(422, f"'{body.type}' is not a node ComfyUI knows about")
        try:
            return graphedits.add(_edits_path(request, wid), nd, body.pos)
        except graphedits.EditError as e:
            raise HTTPException(422, str(e))

    @router.get("/workflows/{wid}/loras")
    def get_loras(wid: str, request: Request):
        try:
            _store(request).get(wid)
        except Exception as e:
            _err(e)
        return {"items": _lora_store(request).load(wid), "max": loras.MAX_LORAS,
                "available": _known_loras(request) is not None}

    @router.put("/workflows/{wid}/loras")
    def put_loras(wid: str, body: LoraBody, request: Request):
        """Save the stack. Names are checked against ComfyUI's LoRA folder when ComfyUI is running."""
        try:
            _store(request).get(wid)
            items = loras.clean([i.model_dump() for i in body.items], _known_loras(request))
            return {"items": _lora_store(request).save(wid, items)}
        except Exception as e:
            _err(e)

    @router.get("/workflows/{wid}/cover")
    def get_cover(wid: str, request: Request):
        """The app's picture: the cover the user set, else the output of its latest completed run. 404 when neither exists."""
        try:
            _store(request).get(wid)
        except Exception as e:
            _err(e)
        home = request.app.state.services.home / "forge"
        covers = home / "covers"
        for f in sorted(covers.glob(f"{wid}.*")) if covers.is_dir() else []:
            return FileResponse(f, media_type=f"image/{'jpeg' if f.suffix == '.jpg' else f.suffix[1:]}")
        jobs = [j for j in request.app.state.jobs.store.all()
                if j.get("kind") == "app" and j.get("workflow_id") == wid and j.get("state") == "COMPLETED" and j.get("output_file")]
        if jobs:
            latest = max(jobs, key=lambda j: j.get("finished_at") or "")
            out = home / "runs" / latest["id"] / latest["output_file"]["name"]
            if out.is_file():
                return FileResponse(out, media_type="image/png" if out.suffix == ".png" else "image/jpeg")
        raise HTTPException(404, detail="no picture for this app yet")

    @router.put("/workflows/{wid}/cover")
    async def set_cover(wid: str, request: Request):
        """Body: the picture's bytes. PNG, JPEG or WebP only; the same checks as image uploads."""
        try:
            _store(request).get(wid)
            data = await request.body()
            info = uploads.check(data)
        except uploads.UploadError as e:
            raise HTTPException(422, detail=str(e))
        except Exception as e:
            _err(e)
        covers = request.app.state.services.home / "forge" / "covers"
        covers.mkdir(parents=True, exist_ok=True)
        for old in covers.glob(f"{wid}.*"):
            old.unlink()
        (covers / f"{wid}.{info['format']}").write_bytes(data)
        return {"ok": True, "format": info["format"], "width": info["width"], "height": info["height"]}

    @router.delete("/workflows/{wid}/cover")
    def clear_cover(wid: str, request: Request):
        covers = request.app.state.services.home / "forge" / "covers"
        for old in covers.glob(f"{wid}.*") if covers.is_dir() else []:
            old.unlink()
        return {"ok": True}

    @router.get("/workflows/{wid}/document")
    def get_document(wid: str, request: Request):
        """Read-only summary of the stored source: format, nodes, states, metadata and validation."""
        try:
            store = _store(request)
            rec = store.get(wid)
            return document.describe(store.source_bytes(wid), rec)
        except Exception as e:
            _err(e)

    @router.get("/modules")
    def list_modules(request: Request):
        try:
            return {"modules": _module_store(request).list()}
        except Exception as e:
            _err(e)

    @router.post("/modules", status_code=201)
    def create_module(body: ModuleBody, request: Request):
        """Wrap a complete workflow as module version 1. The converted prompt is snapshotted into the version."""
        try:
            res = analyse_record(_store(request), body.workflow_id, _info(request), _deps_ctx(request))
            if res["analysis"]["readiness"] == "blocked":
                raise module.ModuleError("this workflow is blocked: " + "; ".join(res["analysis"]["reasons"][:3]))
            c = module.new_contract(uuid.uuid4().hex, 1, body.name, body.description, res["workflow"],
                                    res["api_prompt"], body.inputs, body.outputs, res["analysis"]["model_files"])
            return {"module": _public(_module_store(request).save(c))}
        except Exception as e:
            _err(e)

    @router.get("/modules/{mid}")
    def get_module(mid: str, request: Request, version: int | None = None):
        try:
            store = _module_store(request)
            c = store.get(mid, version)
            return {"module": _public(c), "versions": store.versions(mid)}
        except Exception as e:
            _err(e)

    @router.post("/modules/{mid}/versions", status_code=201)
    def add_module_version(mid: str, body: ModuleVersionBody, request: Request):
        """A new version keeps the snapshot of the latest one and takes new ports, name and description."""
        try:
            store = _module_store(request)
            latest = store.get(mid)
            c = module.new_contract(mid, latest.version + 1, body.name or latest.name,
                                    latest.description if body.description is None else body.description,
                                    {"id": latest.workflow_id, "name": latest.workflow_name, "sha256": latest.source_sha256},
                                    latest.prompt, body.inputs, body.outputs, latest.model_refs)
            return {"module": _public(store.save(c)), "versions": store.versions(mid)}
        except Exception as e:
            _err(e)

    @router.post("/modules/{mid}/prepare")
    def prepare_module(mid: str, body: PrepareBody, request: Request, version: int | None = None):
        """Apply values to the pinned snapshot. Returns the prompt that would be sent. Nothing is run."""
        try:
            c = _module_store(request).get(mid, version)
            prompt = module.prepare(c.inputs, c.prompt, body.values)
            return {"module_id": c.module_id, "version": c.version, "prompt": prompt}
        except Exception as e:
            _err(e)

    @router.post("/modules/{mid}/run", status_code=202)
    def run_module(mid: str, body: ModuleRunBody, request: Request):
        """Run one module version on this computer. Every model file must be found in ComfyUI's current list; nothing is downloaded."""
        try:
            c = _module_store(request).get(mid, body.version)
            job = pipeline.submit_version(request.app.state.jobs, c, body.values, _info(request),
                                          request.app.state.services.settings().comfy_url, _comfy_has_input, {})
            return {"job": _job_view(job)}
        except Exception as e:
            _err(e)

    @router.get("/nav-tabs")
    def get_nav_tabs(request: Request):
        """The apps pinned to the main navigation, in order. Tabs whose app was deleted are left out."""
        known = {w["id"] for w in _store(request).list()}
        tabs = [t for t in navtabs.load(_nav_tabs_path(request)) if t.get("app_id") in known]
        return {"tabs": tabs}

    @router.put("/nav-tabs")
    def put_nav_tabs(body: navtabs.NavTabsBody, request: Request):
        """Replace the whole list. Every tab must name an app that exists."""
        known = {w["id"] for w in _store(request).list()}
        missing = [t.app_id for t in body.tabs if t.app_id not in known]
        if missing:
            raise HTTPException(400, detail="a tab names an app that is not imported")
        return {"tabs": navtabs.save(_nav_tabs_path(request), body)}

    @router.get("/pipelines")
    def list_pipelines(request: Request):
        try:
            return {"pipelines": _pipeline_store(request).list()}
        except Exception as e:
            _err(e)

    @router.post("/pipelines", status_code=201)
    def create_pipeline(body: pipeline.PipelineSpec, request: Request):
        """Save a draft. Nothing is checked against ComfyUI until a run starts; the stages are checked here."""
        try:
            problems = pipeline.validate([pipeline.StageSpec.model_validate(s.model_dump()) for s in body.stages], _module_store(request))
            if problems:
                raise module.ModuleError("; ".join(problems))
            return {"pipeline": _pipeline_store(request).save(body)}
        except Exception as e:
            _err(e)

    @router.get("/pipelines/{pid}")
    def get_pipeline(pid: str, request: Request):
        try:
            return {"pipeline": _pipeline_store(request).get(pid)}
        except Exception as e:
            _err(e)

    @router.post("/pipelines/{pid}/validate")
    def validate_pipeline(pid: str, request: Request):
        try:
            rec = _pipeline_store(request).get(pid)
            problems = pipeline.validate([pipeline.StageSpec.model_validate(s) for s in rec["stages"]], _module_store(request))
            return {"valid": not problems, "problems": problems}
        except Exception as e:
            _err(e)

    @router.post("/pipelines/{pid}/run", status_code=202)
    def run_pipeline(pid: str, request: Request):
        """Start the saved pipeline. Stages run one at a time; a failed stage stops the run."""
        try:
            rec = _pipeline_store(request).get(pid)
            run = pipeline.start(rec, _pipeline_context(request.app))
            return {"run": run}
        except Exception as e:
            _err(e)

    @router.get("/pipeline-runs/{rid}")
    def get_pipeline_run(rid: str, request: Request):
        try:
            return {"run": pipeline.RunStore(_forge_home(request.app) / "pipeline_runs").get(rid)}
        except Exception as e:
            _err(e)

    @router.get("/workflows/{wid}/analysis")
    def get_analysis(wid: str, request: Request):
        try:
            result = analyse_record(_store(request), wid, _info(request), _deps_ctx(request))
        except Exception as e:
            _err(e)
        result.pop("api_prompt")
        result["analysis"]["weights"] = _weights(request, result["analysis"]["model_files"])
        return result

    @router.get("/workflows/{wid}/api-prompt")
    def get_api_prompt(wid: str, request: Request):
        """The converted prompt, for inspection. Model names are as saved in the file."""
        try:
            return {"api_prompt": analyse_record(_store(request), wid, _info(request))["api_prompt"]}
        except Exception as e:
            _err(e)

    def _spec_view(store: WorkflowStore, wid: str, info) -> dict:
        res = analyse_record(store, wid, info)
        rec, an = res["workflow"], res["analysis"]
        saved = store.load_spec(wid)
        stale = False
        if saved is not None:
            try:
                spec = uispec.parse(saved["spec"])
                stale = spec.workflow_sha256 != rec["sha256"]
            except ValueError:
                saved, stale = None, False
        if saved is None:
            spec, revision, source = uispec.generate(rec, an), 0, "generated"
        else:
            revision, source = saved["revision"], "saved"
        return {"spec": spec.model_dump(mode="json"), "revision": revision, "source": source, "stale": stale,
                "validation": uispec.validate(spec, rec, an), "readiness": an["readiness"], "reasons": an["reasons"],
                "catalog": list(uispec.CATALOG), "layouts": list(uispec.LAYOUTS)}

    @router.get("/workflows/{wid}/spec")
    def get_spec(wid: str, request: Request):
        """The saved spec, or a freshly generated one that is not saved until the user saves it."""
        try:
            return _spec_view(_store(request), wid, _info(request))
        except Exception as e:
            _err(e)

    @router.post("/workflows/{wid}/spec/regenerate")
    def regenerate_spec(wid: str, request: Request):
        try:
            store = _store(request)
            res = analyse_record(store, wid, _info(request))
            spec = uispec.generate(res["workflow"], res["analysis"])
            return {"spec": spec.model_dump(mode="json"), "revision": (store.load_spec(wid) or {}).get("revision", 0), "source": "generated",
                    "stale": False, "validation": uispec.validate(spec, res["workflow"], res["analysis"]),
                    "readiness": res["analysis"]["readiness"], "reasons": res["analysis"]["reasons"],
                    "catalog": list(uispec.CATALOG), "layouts": list(uispec.LAYOUTS)}
        except Exception as e:
            _err(e)

    @router.post("/workflows/{wid}/spec/add-reasons")
    def add_spec_reasons(wid: str, request: Request):
        """Adds the 'why' note to controls saved before reasons existed. Edits to labels, groups and values are kept."""
        try:
            store = _store(request)
            saved = store.load_spec(wid)
            if saved is None:
                raise HTTPException(409, detail="save the app before adding reasons")
            res = analyse_record(store, wid, _info(request))
            generated = uispec.generate(res["workflow"], res["analysis"])
            current = uispec.parse(saved["spec"])
            merged, changed = uispec.add_reasons(current, generated)
            if not changed and merged is current:
                return {"changed": [], "revision": saved["revision"]}
            out = store.save_spec(wid, merged.model_dump(mode="json"), saved["revision"])
            return {"changed": changed, "revision": out["revision"]}
        except HTTPException:
            raise
        except Exception as e:
            _err(e)

    @router.post("/workflows/{wid}/spec/validate")
    def validate_spec(wid: str, body: SpecBody, request: Request):
        try:
            res = analyse_record(_store(request), wid, _info(request))
            spec = uispec.parse(body.spec)
        except ValueError as e:
            if isinstance(e, workflow.WorkflowError):
                _err(e)
            raise HTTPException(422, detail=str(e))
        except Exception as e:
            _err(e)
        return {"validation": uispec.validate(spec, res["workflow"], res["analysis"])}

    @router.put("/workflows/{wid}/spec")
    def save_spec(wid: str, body: SpecBody, request: Request):
        """Saves a spec unless a binding is invalid. Unresolved bindings may be saved; they stay flagged."""
        store = _store(request)
        try:
            res = analyse_record(store, wid, _info(request))
            spec = uispec.parse(body.spec)
            spec.generated_by = "user"
            v = uispec.validate(spec, res["workflow"], res["analysis"])
            if v["status"] == "invalid":
                bad = [r for r in v["components"] if r["status"] == "invalid"]
                msg = "; ".join(f"{r['component']}: {r['reasons'][0]}" for r in bad[:3]) or "; ".join(v["problems"][:3])
                raise HTTPException(422, detail=f"the spec has invalid bindings and was not saved: {msg}")
            saved = store.save_spec(wid, spec.model_dump(mode="json"), body.base_revision)
        except HTTPException:
            raise
        except ValueError as e:
            if isinstance(e, workflow.WorkflowError):
                _err(e)
            raise HTTPException(422, detail=str(e))
        except Exception as e:
            _err(e)
        return {"revision": saved["revision"], "validation": v}

    @router.patch("/workflows/{wid}")
    def rename(wid: str, body: RenameBody, request: Request):
        try:
            return {"workflow": _store(request).rename(wid, body.name)}
        except Exception as e:
            _err(e)

    @router.post("/workflows/{wid}/delete")
    def delete(wid: str, body: DeleteBody, request: Request):
        try:
            _store(request).delete(wid, body.confirm_name)
        except Exception as e:
            _err(e)
        return {"deleted": wid}

    def _prepare_run(request: Request, wid: str, values: dict):
        """The checked, converted prompt for a saved app with these values. Raises HTTPException on refusal."""
        store = _store(request)
        try:
            res = analyse_record(store, wid, _info(request))
            saved = store.load_spec(wid)
            if saved is None:
                raise HTTPException(409, detail="save the app before running it")
            spec = uispec.parse(saved["spec"])
            if spec.workflow_sha256 != res["workflow"]["sha256"]:
                raise HTTPException(409, detail="the workflow file changed since this app was saved; regenerate and save the app")
            validation = uispec.validate(spec, res["workflow"], res["analysis"])
            prompt, models, notes = forge_run.prepare(spec, validation, res["analysis"], res["api_prompt"], values)
            prompt, applied = loras.apply(prompt, _lora_store(request).load(wid))
        except HTTPException:
            raise
        except (forge_run.RunRefused, uispec.ApplyError, ValueError) as e:
            raise HTTPException(422, detail=str(e))
        except Exception as e:
            _err(e)
        return {"res": res, "saved": saved, "spec": spec, "prompt": prompt, "models": models, "notes": notes, "loras": applied}

    @router.post("/workflows/{wid}/cloud-preview")
    def cloud_preview(wid: str, body: RunBody, request: Request):
        """What a Comfy Cloud run would send for these values. The digest must be sent back with consent."""
        prep = _prepare_run(request, wid, body.values)
        if prep["models"] or prep["loras"]:
            raise HTTPException(422, detail="this app uses model files or LoRAs. Cloud model availability is not verified yet, so it can only run locally.")
        return {"preview": providers.preview(prep["prompt"]), "status": providers.cloud_status()}

    @router.post("/workflows/{wid}/run", status_code=202)
    def run_app(wid: str, body: RunBody, request: Request):
        """Run the saved app with these values, locally by default. A cloud run needs consent to the exact preview."""
        prep = _prepare_run(request, wid, body.values)
        res, saved, spec, prompt, models, notes = prep["res"], prep["saved"], prep["spec"], prep["prompt"], prep["models"], prep["notes"]
        consent = None
        if body.provider == "cloud":
            # One paid run at a time per identical request: a double click must not bill twice (idempotency).
            for other in request.app.state.jobs.store.all():
                if other.get("kind") == "app" and other.get("provider") == "cloud" and other.get("state") not in TERMINAL_STATES                         and (other.get("consent") or {}).get("digest") == providers.preview(prompt)["digest"]:
                    raise HTTPException(409, detail=f"this exact cloud run is already in progress (run {other['id'][:8]}). Wait for it to finish.")
            if models or prep["loras"]:
                raise HTTPException(422, detail="this app uses model files or LoRAs, which cannot be verified on Comfy Cloud yet. Run it locally.")
            st = providers.cloud_status()
            if st["state"] != "ready":
                raise HTTPException(409, detail=st["message"])
            pv = providers.preview(prompt)
            if body.consent is None or not body.consent.acknowledged or body.consent.digest != pv["digest"]:
                raise HTTPException(409, detail="consent is required: review what is sent and confirm. The preview may have changed.")
            consent = {"digest": pv["digest"], "host": pv["host"], "confirmed_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        jobs = request.app.state.jobs
        provenance = {"workflow_id": wid, "workflow_sha256": res["workflow"]["sha256"], "workflow_name": res["workflow"]["name"],
                      "spec_revision": saved["revision"], "values": body.values, "source_format": res["workflow"]["format"],
                      "node_definitions": res["analysis"]["node_definitions"], "provider": body.provider,
                      "image_roles": {c.id: c.role for c in spec.components if c.type == "image_upload" and c.role},
                      "loras": [{"name": i["name"], "strength": i["strength"]} for i in prep["loras"]],
                      "replay_of": body.replay_of}
        seed = next((body.values[c.id] for c in spec.components if c.type == "seed" and c.id in body.values), None)
        job = jobs.submit_app(wid, res["workflow"]["sha256"][:12], prompt, models, {"seed": seed, "app": spec.name},
                              provenance, notes, provider=body.provider, consent=consent)
        return {"job": {k: job.get(k) for k in ("id", "state", "kind", "provider", "workflow_id", "created_at", "models", "notes")}}

    class _Apps:
        """What the operator may read: each app's readiness and its main controls. Read only."""

        def __init__(self, request: Request):
            self.store, self.info, self.deps = _store(request), _info(request), _deps_ctx(request)

        def list_apps(self) -> list[dict]:
            out = []
            for rec in self.store.list():
                try:
                    an = analyse_record(self.store, rec["id"], self.info, self.deps)["analysis"]
                    out.append({"id": rec["id"], "name": rec["name"], "readiness": an["readiness"], "reasons": an["reasons"][:3]})
                except Exception:
                    out.append({"id": rec["id"], "name": rec["name"], "readiness": "unchecked", "reasons": []})
            return out

        def get_app(self, app_id: str) -> dict | None:
            try:
                view = _spec_view(self.store, app_id, self.info)
            except Exception:
                return None
            controls = [{"id": c["id"], "type": c["type"], "label": c["label"], "default": c["props"].get("default"),
                         "options": c["props"].get("choices")}
                        for c in view["spec"]["components"] if c["group"] == "main" and not c["hidden"]]
            return {"id": app_id, "name": view["spec"]["name"], "readiness": view["readiness"],
                    "reasons": view["reasons"][:3], "components": controls}

    @router.get("/operator/providers")
    def operator_providers():
        """Which local model servers answer on this computer, and their models. Never fails: unreachable is an answer."""
        return {"endpoints": llm_providers.probe_all()}

    @router.post("/operator/chat")
    def operator_chat(body: OperatorBody, request: Request):
        """One operator turn. It reads apps and returns run cards; the page runs a card, never the server."""
        try:
            ep = _operator_endpoint(body)
            return operator.Operator(_Apps(request), lambda msgs: llm_providers.chat(ep, body.model, msgs)).turn(body.message, body.history)
        except llm_providers.ProviderError as e:
            raise HTTPException(503, detail=str(e))
        except operator.OperatorError as e:
            raise HTTPException(422, detail=str(e))

    def _characters(request: Request) -> characters.CharacterStore:
        return characters.CharacterStore(request.app.state.services.home / "forge" / "characters")

    @router.get("/characters")
    def list_characters(request: Request):
        return {"characters": _characters(request).list()}

    @router.post("/characters", status_code=201)
    async def create_character(body: CharacterBody, request: Request):
        try:
            return {"character": _characters(request).create(body.name, body.notes)}
        except characters.CharacterError as e:
            raise HTTPException(422, detail=str(e))

    @router.get("/characters/{cid}")
    def get_character(cid: str, request: Request):
        try:
            return {"character": _characters(request).get(cid)}
        except characters.CharacterError as e:
            raise HTTPException(404, detail=str(e))

    @router.post("/characters/{cid}/anchor", status_code=201)
    async def set_anchor(cid: str, request: Request, name: str = "anchor.png"):
        """The character's anchor photo: checked, kept as a copy, and sent to ComfyUI for runs to use."""
        length = request.headers.get("content-length") or ""
        if length.isdigit() and int(length) > uploads.MAX_BYTES:
            raise HTTPException(413, detail=f"the image is larger than {uploads.MAX_BYTES // (1024 * 1024)} MB")
        data = await request.body()
        store = _characters(request)
        try:
            store.get(cid)
            info = uploads.check(data)
            s = request.app.state.services.settings()
            stored = uploads.send_to_comfy(s.comfy_url, data, info)
            local = uploads.keep_copy(request.app.state.services.home / "forge" / "characters" / "anchors", data, info)
            uploads.keep_copy(request.app.state.services.home / "forge" / "uploads", data, info)   # so GET /uploads/{name} can show it
            rec = store.set_anchor(cid, {"comfy_name": stored, "path": str(local), "sha256": info["sha256"],
                                         "width": info["width"], "height": info["height"], "original_name": name[:120]})
        except uploads.UploadError as e:
            raise HTTPException(422, detail=str(e))
        except characters.CharacterError as e:
            raise HTTPException(404, detail=str(e))
        return {"character": rec}

    @router.post("/characters/{cid}/check")
    def check_output(cid: str, body: CheckBody, request: Request):
        """Score one finished run's face against the character's anchor. A screening flag, not proof of identity."""
        store = _characters(request)
        try:
            rec = store.get(cid)
        except characters.CharacterError as e:
            raise HTTPException(404, detail=str(e))
        if not rec.get("anchor"):
            raise HTTPException(422, detail="give the character an anchor photo first")
        try:
            job = request.app.state.jobs.store.load(body.job_id)
        except Exception:
            raise HTTPException(404, detail="no run with that id")
        if job.get("state") != "COMPLETED" or not job.get("output_file"):
            raise HTTPException(422, detail="that run has no finished image to check")
        home = request.app.state.services.home
        out = home / "forge" / "runs" / job["id"] / job["output_file"]["name"]
        try:
            result = identity.score(request.app.state.services.settings().comfy_root, Path(rec["anchor"]["path"]), out)
        except identity.IdentityError as e:
            raise HTTPException(422, detail=str(e))
        entry = {**result, "job_id": job["id"], "at": characters._now()}
        return {"check": entry, "character": store.add_check(cid, entry)}

    @router.post("/creator/prompt")
    def creator_prompt(body: CreatorPromptBody):
        """Write a portrait prompt from an idea, and from a reference photo's look when one is given. A local model only."""
        try:
            ep = _operator_endpoint(OperatorBody(endpoint=body.endpoint, base=body.base, kind=body.kind, model=body.model, message="x"))
            reply = llm_providers.chat(ep, body.model, creator.writer_messages(body.idea, body.reference_b64))
            return {"prompt": creator.clean_prompt(reply), "model": body.model, "reference_used": bool(body.reference_b64)}
        except creator.CreatorError as e:
            raise HTTPException(422, detail=str(e))
        except llm_providers.ProviderError as e:
            raise HTTPException(503, detail=str(e))

    @router.post("/creator/runs", status_code=202)
    def creator_runs(body: CreatorRunBody, request: Request):
        """Render the creator workflow with this prompt, once per seed. Each render is an ordinary local job."""
        store = _store(request)
        try:
            res = analyse_record(store, body.workflow_id, _info(request))
        except Exception as e:
            _err(e)
        refused = [m["file"] for m in res["analysis"]["model_files"] if m["status"] not in ("available", "found_elsewhere")]
        if refused:
            raise HTTPException(422, detail=f"model files not installed in ComfyUI: {', '.join(refused[:4])}")
        base = copy.deepcopy(res["api_prompt"])
        for m in res["analysis"]["model_files"]:   # the same installed-name rule the app run uses
            chosen = m["file"] if m["status"] == "available" else m.get("comfy_name")
            base[m["node"]]["inputs"][m["input"]] = chosen
        jobs = []
        for i in range(body.count):
            seed = body.seed_base + i
            try:
                graph = creator.krea_graph(base, body.prompt, seed, body.megapixels)
            except creator.CreatorError as e:
                raise HTTPException(422, detail=str(e))
            provenance = {"workflow_id": body.workflow_id, "workflow_sha256": res["workflow"]["sha256"],
                          "workflow_name": res["workflow"]["name"], "source": "creator", "prompt": body.prompt, "seed": seed,
                          "provider": "local", "source_format": res["workflow"]["format"]}
            job = request.app.state.jobs.submit_app(body.workflow_id, res["workflow"]["sha256"][:12], graph,
                                                    res["analysis"]["model_files"], {"seed": seed, "app": "Creator"},
                                                    provenance, [], provider="local", consent=None)
            jobs.append({"id": job["id"], "seed": seed, "state": job["state"]})
        return {"jobs": jobs}

    @router.post("/characters/{cid}/anchor-from-run", status_code=201)
    def anchor_from_run(cid: str, body: AnchorFromRunBody, request: Request):
        """Make a finished render this character's anchor. The render must have completed."""
        store = _characters(request)
        try:
            store.get(cid)
            job = request.app.state.jobs.store.load(body.job_id)
        except characters.CharacterError as e:
            raise HTTPException(404, detail=str(e))
        except Exception:
            raise HTTPException(404, detail="no run with that id")
        if job.get("state") != "COMPLETED" or not job.get("output_file"):
            raise HTTPException(422, detail="that run has no finished image")
        home = request.app.state.services.home
        data = (home / "forge" / "runs" / job["id"] / job["output_file"]["name"]).read_bytes()
        try:
            info = uploads.check(data)
            stored = uploads.send_to_comfy(request.app.state.services.settings().comfy_url, data, info)
            local = uploads.keep_copy(home / "forge" / "characters" / "anchors", data, info)
            uploads.keep_copy(home / "forge" / "uploads", data, info)
        except uploads.UploadError as e:
            raise HTTPException(422, detail=str(e))
        rec = store.set_anchor(cid, {"comfy_name": stored, "path": str(local), "sha256": info["sha256"], "width": info["width"],
                                     "height": info["height"], "original_name": f"render {job['id'][:8]}", "from_job": job["id"]})
        return {"character": rec}

    @router.get("/shots/presets")
    def shot_presets():
        return {"presets": list(shots.PRESETS)}

    @router.get("/characters/{cid}/dataset")
    def get_dataset(cid: str, request: Request):
        try:
            return {"dataset": _characters(request).get(cid).get("dataset", [])}
        except characters.CharacterError as e:
            raise HTTPException(404, detail=str(e))

    @router.get("/characters/{cid}/dataset/{item_id}/image")
    def dataset_image(cid: str, item_id: str, request: Request):
        """One dataset item's image. Only ids and files this server issued are served."""
        if not re.fullmatch(r"[0-9a-f]{12}", item_id):
            raise HTTPException(404, detail="not found")
        try:
            item = next(i for i in _characters(request).get(cid).get("dataset", []) if i["id"] == item_id)
        except (characters.CharacterError, StopIteration):
            raise HTTPException(404, detail="not found")
        folder = (request.app.state.services.home / "forge" / "characters" / "dataset" / cid).resolve()
        path = (folder / item["file"]).resolve()
        if path.parent != folder or not path.is_file():
            raise HTTPException(404, detail="not found")
        return FileResponse(path, headers={"Cache-Control": "no-store"})

    @router.delete("/characters/{cid}/dataset/{item_id}")
    def remove_from_dataset(cid: str, item_id: str, request: Request):
        """Take an item out of the dataset and delete its copied files. The render itself is kept."""
        if not re.fullmatch(r"[0-9a-f]{12}", item_id):
            raise HTTPException(404, detail="not found")
        store = _characters(request)
        try:
            item = next(i for i in store.get(cid).get("dataset", []) if i["id"] == item_id)
        except (characters.CharacterError, StopIteration):
            raise HTTPException(404, detail="not found")
        folder = (request.app.state.services.home / "forge" / "characters" / "dataset" / cid).resolve()
        for f in (folder / item["file"], folder / f"{item_id}.txt"):
            if f.resolve().parent == folder and f.is_file():
                f.unlink()
        return {"character": store.remove_dataset_item(cid, item_id)}

    @router.post("/characters/{cid}/dataset", status_code=201)
    def add_to_dataset(cid: str, body: DatasetBody, request: Request):
        """Keep a finished render as a dataset item, with a caption file. The render is copied, not moved."""
        store = _characters(request)
        try:
            rec = store.get(cid)
            job = request.app.state.jobs.store.load(body.job_id)
        except characters.CharacterError as e:
            raise HTTPException(404, detail=str(e))
        except Exception:
            raise HTTPException(404, detail="no run with that id")
        if job.get("state") != "COMPLETED" or not job.get("output_file"):
            raise HTTPException(422, detail="that run has no finished image")
        preset = None
        if body.preset_id:
            try:
                preset = shots.preset(body.preset_id)
            except shots.ShotError as e:
                raise HTTPException(422, detail=str(e))
        home = request.app.state.services.home / "forge"
        src = home / "runs" / job["id"] / job["output_file"]["name"]
        folder = home / "characters" / "dataset" / cid
        folder.mkdir(parents=True, exist_ok=True)
        item_id = uuid.uuid4().hex[:12]
        ext = src.suffix.lower()
        dest = folder / f"{item_id}{ext}"
        dest.write_bytes(src.read_bytes())
        prompt = (preset or {}).get("prompt") or (job.get("provenance") or {}).get("prompt") or ""
        caption = shots.caption({"prompt": prompt}, rec["name"]) if prompt else rec["name"]
        (folder / f"{item_id}.txt").write_text(caption, encoding="utf-8")
        item = {"id": item_id, "job_id": job["id"], "preset": body.preset_id, "file": dest.name, "caption": caption,
                "added_at": characters._now()}
        return {"item": item, "character": store.add_dataset_item(cid, item)}

    @router.get("/providers")
    def providers_status(request: Request):
        """Both providers, with what is and is not configured. Never returns a key."""
        from openfield.generation.comfy import ComfyRunner
        url = request.app.state.services.settings().comfy_url
        local_ok = ComfyRunner(url).reachable()
        return {"local": {"state": "ready" if local_ok else "not_running", "url": url,
                          "message": "ComfyUI is running on this computer." if local_ok else "ComfyUI is not running. Start it to run locally."},
                "cloud": providers.cloud_status(), "default": "local"}

    @router.get("/runs/{jid}/receipt")
    def run_receipt(jid: str, request: Request):
        """The provenance record of a finished app run: workflow hash, spec revision, values, models, output hash."""
        jobs = request.app.state.jobs
        try:
            job = jobs.store.load(jid)
        except KeyError:
            raise HTTPException(404, detail="run not found")
        if job.get("kind") != "app" or not job.get("output_file"):
            raise HTTPException(404, detail="this run has no receipt")
        path = (request.app.state.services.home / "forge" / "runs" / jid / "run.json").resolve()
        if not path.is_file():
            raise HTTPException(404, detail="the receipt is missing")
        return json.loads(path.read_text(encoding="utf-8"))

    @router.post("/runs/{jid}/replay", status_code=202)
    def replay_run(jid: str, request: Request):
        """Runs the app again with the exact values, models and LoRAs of this run's receipt.
        Refused when the workflow file has changed, because the same values would then mean something else."""
        rec = run_receipt(jid, request)
        wid = rec["workflow_id"]
        current = _store(request).get(wid)
        if current["sha256"] != rec["workflow_sha256"]:
            raise HTTPException(409, detail="this app's workflow file has changed since the run, so a replay would not match it")
        saved_rev = (_store(request).load_spec(wid) or {}).get("revision")
        out = run_app(wid, RunBody(values=rec["values"], provider="local", replay_of=jid), request)
        return {**out, "replayed": jid, "spec_changed": saved_rev != rec.get("spec_revision")}

    @router.get("/runs/{a}/compare/{b}")
    def compare_runs(a: str, b: str, request: Request):
        """What differs between two runs' receipts: the app, the workflow file, the values, the LoRAs, and whether each
        run can be repeated exactly. Read only."""
        ra = run_receipt(a, request)
        rb = run_receipt(b, request)
        va, vb = ra.get("values") or {}, rb.get("values") or {}
        changed = [{"name": k, "a": va.get(k), "b": vb.get(k)} for k in sorted(set(va) | set(vb)) if va.get(k) != vb.get(k)]
        return {"same_app": ra.get("workflow_id") == rb.get("workflow_id"),
                "same_workflow_file": ra.get("workflow_sha256") == rb.get("workflow_sha256"),
                "spec_revision": {"a": ra.get("spec_revision"), "b": rb.get("spec_revision")},
                "values": changed, "loras_same": ra.get("loras") == rb.get("loras"),
                "repeatable": {"a": _repeatable(va, ra), "b": _repeatable(vb, rb)}}

    @router.get("/runs/{jid}/output")
    def run_output(jid: str, request: Request):
        """The saved output of a finished app run. Only files inside that run's folder are served."""
        jobs = request.app.state.jobs
        try:
            job = jobs.store.load(jid)
        except KeyError:
            raise HTTPException(404, detail="run not found")
        out = job.get("output_file")
        if job.get("kind") != "app" or not out:
            raise HTTPException(404, detail="this run has no saved output")
        folder = (request.app.state.services.home / "forge" / "runs" / jid).resolve()
        path = (folder / out["name"]).resolve()
        if path.parent != folder or not path.is_file():
            raise HTTPException(404, detail="the saved output is missing")
        media = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
                 ".mp4": "video/mp4", ".webm": "video/webm", ".gif": "image/gif"}.get(path.suffix, "application/octet-stream")
        return FileResponse(path, media_type=media, headers={"Cache-Control": "no-store"})

    @router.post("/uploads", status_code=201)
    async def upload_image(request: Request, name: str = "image"):
        """An image for a run's image input. Checked, copied, and sent to ComfyUI's input folder. Returns the stored name."""
        length = request.headers.get("content-length") or ""
        if length.isdigit() and int(length) > uploads.MAX_BYTES:
            raise HTTPException(413, detail=f"the image is larger than {uploads.MAX_BYTES // (1024 * 1024)} MB")
        data = await request.body()
        try:
            info = uploads.check(data)
            s = request.app.state.services.settings()
            stored = uploads.send_to_comfy(s.comfy_url, data, info)
            uploads.keep_copy(request.app.state.services.home / "forge" / "uploads", data, info)
        except uploads.UploadError as e:
            raise HTTPException(422, detail=str(e))
        return {"name": stored, "sha256": info["sha256"], "width": info["width"], "height": info["height"], "format": info["format"],
                "original_name": name[:120]}

    @router.post("/links", status_code=201)
    async def paste_link(request: Request):
        """Body: {"url", "start", "end"}. Downloads a TikTok, Instagram or X video and keeps a trimmed clip (up to 15 s)."""
        from .forge import links

        body = await request.json()
        s = request.app.state.services.settings()
        store = request.app.state.services.home / "forge" / "links"
        try:
            clip = await run_in_threadpool(
                links.fetch_clip, str(body.get("url", "")), float(body.get("start", 0)), float(body.get("end", 0)),
                store, None, s.ffmpeg_path or None)
        except (links.LinkError, TypeError, ValueError) as e:
            raise HTTPException(422, detail=str(e))
        try:
            clip["comfy_name"] = await run_in_threadpool(uploads.send_video_to_comfy, s.comfy_url, store / clip["name"], clip["name"])
        except uploads.UploadError as e:
            # the clip is kept on disk; only the hand-off to ComfyUI failed
            raise HTTPException(503, detail=f"the clip was saved as {clip['name']}, but {e}")
        return clip

    @router.get("/uploads/{name}")
    def get_upload(name: str, request: Request):
        """A copy of an image input this app was run with. Only names this server issued are served."""
        m = re.fullmatch(r"openfield_([0-9a-f]{16})\.(png|jpg|webp)", name)
        if not m:
            raise HTTPException(404, detail="image not found")
        folder = (request.app.state.services.home / "forge" / "uploads").resolve()
        hits = [p for p in folder.glob(f"{m.group(1)}*.{m.group(2)}") if p.resolve().parent == folder]
        if not hits:
            raise HTTPException(404, detail="image not found")
        media = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}[m.group(2)]
        return FileResponse(hits[0], media_type=media, headers={"Cache-Control": "no-store"})

    @router.get("/workflows/{wid}/runs")
    def list_runs(wid: str, request: Request):
        """Every run of this app, newest first, with its provider, state and receipt link. Read only."""
        try:
            _store(request).get(wid)
        except ForgeError as e:
            _err(e)
        runs = []
        for j in request.app.state.jobs.store.all():
            if j.get("kind") != "app" or j.get("workflow_id") != wid:
                continue
            runs.append({"id": j["id"], "state": j.get("state"), "provider": j.get("provider") or "local",
                         "created_at": j.get("created_at"), "finished_at": j.get("finished_at"),
                         "has_output": bool(j.get("output_file")), "error": j.get("error"),
                         "receipt_url": f"/api/forge/runs/{j['id']}/receipt" if j.get("output_file") else None})
        runs.sort(key=lambda r: r.get("created_at") or "", reverse=True)
        return {"runs": runs[:100]}

    @router.get("/llm/status")
    def llm_status(request: Request):
        """Whether a local chat model is reachable, and which models are installed. Never fails: 'unreachable' is an answer."""
        s = request.app.state.services.settings()
        try:
            st = llm.status(s.llm_url)
        except llm.LLMError as e:
            st = {"state": "unreachable", "url": s.llm_url, "models": [], "reason": str(e)}
        return {**st, "default_model": llm.default_model(st["models"], s.llm_model), "timeout_seconds": llm.TIMEOUT_S}

    @router.post("/workflows/{wid}/spec/propose")
    def propose_spec(wid: str, body: ProposeBody, request: Request):
        """A layout proposal from the local model. Returns it for review; nothing is saved."""
        store = _store(request)
        s = request.app.state.services.settings()
        try:
            res = analyse_record(store, wid, _info(request))
            rec, an = res["workflow"], res["analysis"]
            if body.current_spec is not None:
                base = uispec.parse(body.current_spec)
            else:
                saved = store.load_spec(wid)
                base = uispec.parse(saved["spec"]) if saved else uispec.generate(rec, an)
        except ValueError as e:
            if isinstance(e, workflow.WorkflowError):
                _err(e)
            raise HTTPException(422, detail=str(e))
        except Exception as e:
            _err(e)
        try:
            st = llm.status(s.llm_url)
            if st["state"] != "ok":
                raise HTTPException(503, detail=st["reason"] or "No local model is available.")
            model = llm.default_model(st["models"], body.model or s.llm_model)
            if model is None:
                raise HTTPException(503, detail="No chat model is installed in Ollama.")
            system, user = propose.build_messages(base, body.request)
            obj = llm.chat_json(s.llm_url, model, system, user)
            prop, warnings = propose.parse_proposal(obj, {c.binding.id for c in base.components})
        except HTTPException:
            raise
        except llm.LLMError as e:
            raise HTTPException(503, detail=str(e))
        except propose.ProposalError as e:
            raise HTTPException(422, detail=f"The model's proposal was not usable: {e}")
        spec = propose.apply_proposal(base, prop)
        before = {c.id: c.model_dump() for c in base.components}
        changed = [c.binding.id for c in spec.components if c.model_dump() != before.get(c.id)]
        v = uispec.validate(spec, rec, an)
        return {"model": model, "spec": spec.model_dump(mode="json"), "validation": v, "warnings": warnings,
                "changed": changed, "source": "model"}

    @router.get("/workflows/{wid}/export")
    def export_app(wid: str, request: Request):
        """A .forgeapp package of a saved app. Model weights are never included."""
        from datetime import datetime, timezone
        from openfield import __version__
        store = _store(request)
        try:
            res = analyse_record(store, wid, _info(request))
            saved = store.load_spec(wid)
            if saved is None:
                raise HTTPException(409, detail="save the app before exporting it")
            extras = package.read_extras(wid, _workflows_root(request), _covers_root(request))
            data = package.build(store, res["workflow"], res["analysis"], saved,
                                 datetime.now(timezone.utc).isoformat(timespec="seconds"), __version__, extras=extras)
        except HTTPException:
            raise
        except Exception as e:
            _err(e)
        name = package.safe_filename(res["workflow"]["name"])
        return Response(content=data, media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})

    @router.post("/packages/import", status_code=201)
    async def import_app(request: Request, name: str = "package.forgeapp"):
        """Body: the .forgeapp file's bytes. Creates a new app; never overwrites one."""
        from openfield import __version__
        length = request.headers.get("content-length") or ""
        if length.isdigit() and int(length) > package.MAX_PACKAGE_BYTES:
            raise HTTPException(413, detail=f"the package is larger than {package.MAX_PACKAGE_BYTES // (1024 * 1024)} MB")
        data = await request.body()
        store = _store(request)
        try:
            names = [w["name"] for w in store.list()]
            out = package.import_into(store, data, __version__, names, _workflows_root(request), _covers_root(request))
        except package.PackageError as e:
            raise HTTPException(422, detail=str(e))
        except Exception as e:
            _err(e)
        return out

    @router.post("/workflows/{wid}/duplicate", status_code=201)
    def duplicate_app(wid: str, request: Request):
        store = _store(request)
        try:
            names = [w["name"] for w in store.list()]
            return package.duplicate_into(store, wid, names, _workflows_root(request), _covers_root(request))
        except package.PackageError as e:
            raise HTTPException(409, detail=str(e))
        except Exception as e:
            _err(e)

    return router
