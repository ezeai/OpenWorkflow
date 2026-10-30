"""Product posters with MOSS-Image: a headline and a layout become one graph, sent to the local MOSS server.

Routes:
  GET  /api/moss/status               is the MOSS server running on this computer
  POST /api/moss/posters              start a poster (returns at once; the worker renders it)
  GET  /api/moss/posters              every poster, newest first
  GET  /api/moss/posters/{id}         the poster; checks the worker and saves the image and receipt when finished
  GET  /api/moss/posters/{id}/output  the saved image
Nothing here installs anything or downloads a model.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .forge import moss_graph, moss_layout, moss_prompt
from .forge.moss_worker import DEFAULT_URL, MossWorker, WorkerError

router = APIRouter(prefix="/api/moss")
_ID = re.compile(r"^[0-9a-f]{32}$")
MAX_PROMPT = 1500


class Box(BaseModel):
    type: str = "obj"
    x: float
    y: float
    w: float
    h: float
    desc: str = Field(default="", max_length=400)
    text: str = Field(default="", max_length=200)
    palette: list[str] = Field(default_factory=list, max_length=5)


class PosterBody(BaseModel):
    prompt: str = Field(min_length=1, max_length=MAX_PROMPT)
    headline: str = Field(default="", max_length=200)
    aspect: str = Field(default="square", max_length=20)
    base: int = 512
    width: int | None = None     # optional explicit size; overrides aspect and base
    height: int | None = None
    seed: int = Field(default=1, ge=0, le=2**31)
    boxes: list[Box] = Field(default_factory=list, max_length=moss_layout.MAX_BOXES)


def _home(request: Request) -> Path:
    return request.app.state.services.home / "forge" / "moss" / "posters"


def _worker() -> MossWorker:
    return MossWorker(os.environ.get("OPENFIELD_MOSS_URL") or DEFAULT_URL)


# The prompt helper runs on the MOSS text server. The main ComfyUI's text node fails on this model (see docs).
TEXT_URL_DEFAULT = "http://127.0.0.1:8189"


def _text_worker() -> MossWorker:
    return MossWorker(os.environ.get("OPENFIELD_MOSS_TEXT_URL") or TEXT_URL_DEFAULT)


def _record_path(request: Request, pid: str) -> Path:
    if not _ID.match(pid or ""):
        raise HTTPException(404, detail="unknown poster")
    return _home(request) / f"{pid}.json"


def _load(request: Request, pid: str) -> dict:
    p = _record_path(request, pid)
    if not p.is_file():
        raise HTTPException(404, detail="unknown poster")
    return json.loads(p.read_text(encoding="utf-8"))


def _save(request: Request, rec: dict) -> None:
    folder = _home(request)
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / f"{rec['id']}.json.tmp"
    tmp.write_text(json.dumps(rec, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(folder / f"{rec['id']}.json")


def _now() -> str:
    # microseconds, so two posters made in the same second still sort newest first
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


PRESETS = [
    {"id": "product_hero", "name": "Product hero", "description": "Product centred, headline along the bottom.",
     "boxes": [{"type": "obj", "x": 0.2, "y": 0.1, "w": 0.6, "h": 0.6, "desc": "the product, centred, sharp, as the hero"},
               {"type": "text", "x": 0.1, "y": 0.78, "w": 0.8, "h": 0.14, "desc": "bold sans-serif headline, high contrast", "text": ""}]},
    {"id": "sale_banner", "name": "Sale banner", "description": "Headline across the top, product below.",
     "boxes": [{"type": "text", "x": 0.08, "y": 0.06, "w": 0.84, "h": 0.16, "desc": "bold headline, large, high contrast", "text": ""},
               {"type": "obj", "x": 0.25, "y": 0.3, "w": 0.5, "h": 0.6, "desc": "the product, centred, below the headline"}]},
    {"id": "product_left", "name": "Product left", "description": "Product on the left, headline stacked on the right.",
     "boxes": [{"type": "obj", "x": 0.05, "y": 0.2, "w": 0.5, "h": 0.6, "desc": "the product, large, on the left"},
               {"type": "text", "x": 0.6, "y": 0.35, "w": 0.35, "h": 0.3, "desc": "bold headline, stacked, high contrast", "text": ""}]},
]


def _size(aspect: str, base: int, width: int | None, height: int | None) -> tuple[int, int]:
    if width and height:
        return width, height
    return moss_graph.size_for(aspect, base)


def _boxes(body: PosterBody) -> list[dict]:
    """The boxes MOSS receives. A headline fills the first text box; with no text box it gets a default one at the bottom."""
    boxes = [b.model_dump() for b in body.boxes]
    headline = body.headline.strip()
    if not headline:
        return boxes
    text_boxes = [b for b in boxes if b["type"] == "text"]
    if text_boxes:
        if not text_boxes[0]["text"].strip():
            text_boxes[0]["text"] = headline
    else:
        boxes.append({"type": "text", "x": 0.08, "y": 0.8, "w": 0.84, "h": 0.13,
                      "desc": "bold headline, clean sans-serif", "text": headline})
    return boxes


class LayoutBody(BaseModel):
    boxes: list[Box] = Field(default_factory=list, max_length=moss_layout.MAX_BOXES)
    aspect: str = Field(default="square", max_length=20)
    base: int = 512
    width: int | None = None
    height: int | None = None
    prompt: str = Field(default="", max_length=MAX_PROMPT)


class EnhanceBody(BaseModel):
    idea: str = Field(min_length=1, max_length=600)
    engine: str = Field(default="fast", pattern="^(fast|encoder)$")


def _fail(e: Exception):
    if isinstance(e, (moss_graph.GraphError, moss_layout.LayoutError)):
        raise HTTPException(422, detail=str(e))
    if isinstance(e, WorkerError):
        raise HTTPException(503, detail=str(e))
    raise e


@router.get("/status")
def status():
    return _worker().status()


@router.post("/posters", status_code=202)
def create(body: PosterBody, request: Request):
    try:
        w, h = _size(body.aspect, body.base, body.width, body.height)
        graph = moss_graph.build_t2i(body.prompt, body.seed, w, h, _boxes(body))
        worker = _worker()
        pid = uuid.uuid4().hex
        prompt_id = worker.submit(graph, client_id=pid)
    except Exception as e:
        _fail(e)
    graph_json = json.dumps(graph, sort_keys=True).encode()
    rec = {"id": pid, "state": "running", "created_at": _now(), "finished_at": None, "prompt_id": prompt_id,
           "worker": worker.base, "params": {"prompt": body.prompt, "headline": next((b["text"] for b in _boxes(body) if b["type"] == "text" and b.get("text")), body.headline), "aspect": body.aspect, "width": w,
                                              "height": h, "seed": body.seed, "boxes": _boxes(body)},
           "graph_sha256": hashlib.sha256(graph_json).hexdigest(), "output": None, "receipt": None, "message": None}
    _save(request, rec)
    return {"poster": rec}


@router.get("/posters")
def list_posters(request: Request):
    folder = _home(request)
    out = []
    if folder.is_dir():
        for p in folder.glob("*.json"):
            try:
                rec = json.loads(p.read_text(encoding="utf-8"))
                out.append({k: rec.get(k) for k in ("id", "state", "created_at", "finished_at")} | {"headline": rec["params"]["headline"], "prompt": rec["params"]["prompt"]})
            except (OSError, ValueError, KeyError):
                continue
    return {"posters": sorted(out, key=lambda r: r["created_at"] or "", reverse=True)}


@router.get("/posters/{pid}")
def get_poster(pid: str, request: Request):
    rec = _load(request, pid)
    if rec["state"] != "running":
        return {"poster": rec}
    worker = MossWorker(rec["worker"])
    try:
        done = worker.history(rec["prompt_id"])
    except WorkerError as e:
        return {"poster": {**rec, "message": str(e)}}
    if done is None:
        return {"poster": rec}
    if not done["ok"]:
        rec.update(state="failed", finished_at=_now(), message="The MOSS server stopped on this poster: " + "; ".join(str(m) for m in done["messages"])[:400])
        _save(request, rec)
        return {"poster": rec}
    try:
        data = worker.fetch(done["outputs"][0])
    except (WorkerError, IndexError) as e:
        return {"poster": {**rec, "message": str(e)}}
    folder = _home(request) / pid
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "output.png").write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    receipt = {"poster_id": pid, "finished_at": _now(), "worker": rec["worker"], "params": rec["params"],
               "graph_sha256": rec["graph_sha256"], "output_sha256": digest, "output_bytes": len(data),
               "model_files": {"diffusion_model": moss_graph.UNET, "text_encoder": moss_graph.CLIP,
                               "vae": moss_graph.VAE, "lora": moss_graph.LORA_TURBO},
               "settings": {"steps": moss_graph.TURBO["steps"], "cfg": moss_graph.TURBO["cfg"],
                            "sampler": moss_graph.TURBO["sampler"], "scheduler": moss_graph.TURBO["scheduler"]},
               "limitations": ["The seed does not guarantee identical output across software or hardware versions."]}
    (folder / "receipt.json").write_text(json.dumps(receipt, indent=2, ensure_ascii=False), encoding="utf-8")
    rec.update(state="completed", finished_at=receipt["finished_at"], output={"name": "output.png", "sha256": digest},
               receipt=f"/api/moss/posters/{pid}/receipt", message=None)
    _save(request, rec)
    return {"poster": rec}


@router.get("/posters/{pid}/output")
def output(pid: str, request: Request):
    rec = _load(request, pid)
    if rec["state"] != "completed":
        raise HTTPException(404, detail="this poster has no saved image yet")
    return FileResponse(_home(request) / pid / "output.png", media_type="image/png")


@router.get("/posters/{pid}/receipt")
def receipt(pid: str, request: Request):
    rec = _load(request, pid)
    if rec["state"] != "completed":
        raise HTTPException(404, detail="this poster has no receipt yet")
    return FileResponse(_home(request) / pid / "receipt.json", media_type="application/json")


@router.get("/aspects")
def aspects():
    """The poster types, with their pixel sizes at the draft (512) and final (1024) base."""
    return {"aspects": [{"id": k, **{x: v[x] for x in ("name", "use")},
                         "sizes": {str(b): moss_graph.size_for(k, b) for b in moss_graph.BASES}} for k, v in moss_graph.ASPECTS.items()],
            "bases": list(moss_graph.BASES)}


@router.get("/layout-presets")
def layout_presets():
    return {"presets": PRESETS}


@router.post("/layout")
def layout(body: LayoutBody):
    """The exact layout MOSS will read, with a per-box list of what to fix. Nothing is sent to the model."""
    raw = [b.model_dump() for b in body.boxes]
    try:
        boxes = moss_layout.normalize_boxes(raw)
        w, h = _size(body.aspect, body.base, body.width, body.height)
        text = moss_layout.layout_prompt(body.prompt or "poster", boxes, w, h)
    except (moss_layout.LayoutError, moss_graph.GraphError) as e:
        raise HTTPException(422, detail=str(e))
    notes = moss_layout.warnings(boxes)
    for i, b in enumerate(raw):
        if b["type"] == "text" and (b["w"] < moss_layout.TEXT_FLOOR["w"] or b["h"] < moss_layout.TEXT_FLOOR["h"]):
            notes[i].insert(0, "small text box: it will be grown around its centre so the words stay legible")
    return {"boxes": boxes, "warnings": notes, "layout": json.loads(text) if boxes else None,
            "message": None if boxes else "No boxes yet. Add one to place the product and the headline."}


def _enhance_dir(request: Request) -> Path:
    return request.app.state.services.home / "forge" / "moss" / "enhance"


def _enhance_load(request: Request, eid: str) -> dict:
    if not _ID.match(eid or ""):
        raise HTTPException(404, detail="unknown prompt request")
    p = _enhance_dir(request) / f"{eid}.json"
    if not p.is_file():
        raise HTTPException(404, detail="unknown prompt request")
    return json.loads(p.read_text(encoding="utf-8"))


def _enhance_save(request: Request, rec: dict) -> None:
    folder = _enhance_dir(request)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{rec['id']}.json").write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")


@router.post("/enhance", status_code=202)
def enhance_start(body: EnhanceBody, request: Request):
    """Ask the MOSS text encoder for a detailed prompt. Returns a request id; poll GET /enhance/{id} for the answer."""
    eid = uuid.uuid4().hex
    if body.engine == "fast":
        s = request.app.state.services.settings()
        rec = {"id": eid, "created_at": _now(), "engine": "fast", **moss_prompt.fast(body.idea, s.llm_url, s.llm_model)}
    else:
        rec = {"id": eid, "created_at": _now(), "engine": "encoder", **moss_prompt.start(body.idea, _text_worker(), client_id=eid)}
    _enhance_save(request, rec)
    return {"request": rec}


@router.get("/enhance/{eid}")
def enhance_get(eid: str, request: Request):
    rec = _enhance_load(request, eid)
    if rec.get("state") != "running":
        return {"request": rec}
    try:
        done = moss_prompt.finish(rec, _text_worker())
    except WorkerError as e:
        return {"request": {**rec, "note": str(e)}}
    if done is None:
        return {"request": rec}
    rec = {**rec, **done}
    _enhance_save(request, rec)
    return {"request": rec}
