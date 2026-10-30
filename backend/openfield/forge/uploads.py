"""Image inputs for Forge runs: check the file, keep a copy with its checksum, and hand it to ComfyUI.

ComfyUI reads input images from its own input folder by name. The upload goes through ComfyUI's
documented /upload/image route, under a name derived from the file's SHA-256. A user's file name is never
used on disk. Only real PNG, JPEG or WebP images are accepted; SVG and anything else is refused.
"""
from __future__ import annotations

import hashlib
import io
from pathlib import Path

import httpx
from PIL import Image

MAX_BYTES = 20 * 1024 * 1024
MAX_SIDE = 4096
FORMATS = {"PNG": "png", "JPEG": "jpg", "WEBP": "webp"}


class UploadError(ValueError):
    """The file is not an acceptable image. The message is safe to show."""


def check(data: bytes) -> dict:
    if not data:
        raise UploadError("the file is empty")
    if len(data) > MAX_BYTES:
        raise UploadError(f"the image is larger than {MAX_BYTES // (1024 * 1024)} MB")
    try:
        with Image.open(io.BytesIO(data)) as im:
            fmt = im.format
            w, h = im.size
            im.verify()
    except Exception:
        raise UploadError("this is not a readable image")
    if fmt not in FORMATS:
        raise UploadError(f"{fmt or 'this'} images are not accepted; use PNG, JPEG or WebP")
    if w > MAX_SIDE or h > MAX_SIDE or w < 1 or h < 1:
        raise UploadError(f"the image must be at most {MAX_SIDE} pixels on each side")
    return {"format": FORMATS[fmt], "width": w, "height": h, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


def comfy_name(info: dict) -> str:
    return f"openfield_{info['sha256'][:16]}.{info['format']}"


def send_to_comfy(url: str, data: bytes, info: dict) -> str:
    """Upload to ComfyUI's input folder. Returns the name ComfyUI stores it under."""
    name = comfy_name(info)
    try:
        r = httpx.post(f"{url.rstrip('/')}/upload/image", data={"overwrite": "true"},
                       files={"image": (name, data, f"image/{info['format']}")}, timeout=60)
        r.raise_for_status()
        body = r.json()
    except (httpx.HTTPError, ValueError) as e:
        raise UploadError(f"ComfyUI did not accept the image ({type(e).__name__}). Is ComfyUI running?")
    stored = body.get("name") if isinstance(body, dict) else None
    if not isinstance(stored, str) or not stored:
        raise UploadError("ComfyUI did not return a name for the image")
    return stored


def keep_copy(folder: Path, data: bytes, info: dict) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{info['sha256']}.{info['format']}"
    if not target.exists():
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(target)
    return target


def send_video_to_comfy(url: str, path: Path, name: str) -> str:
    """Upload a clip (MP4) to ComfyUI's input folder through /upload/image, the route Open Workflow's
    Magekyo swap already uses for VHS_LoadVideo. Returns the name ComfyUI stores it under."""
    try:
        with path.open("rb") as fileobj:
            r = httpx.post(f"{url.rstrip('/')}/upload/image", files={"image": (name, fileobj, "video/mp4")},
                           data={"type": "input", "overwrite": "true"}, timeout=None)
        r.raise_for_status()
        stored = r.json().get("name")
    except (httpx.HTTPError, ValueError) as e:
        raise UploadError(f"ComfyUI did not accept the clip ({type(e).__name__}). Is ComfyUI running?")
    if not isinstance(stored, str) or not stored:
        raise UploadError("ComfyUI did not return a name for the clip")
    return stored
