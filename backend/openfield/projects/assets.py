"""Project asset registry. Images are copied into the project; originals are never touched.

- Format is decided from the file's bytes (magic numbers and a Pillow decode), never from the name.
- Stored under assets/images/<asset_id>.<ext>. The user's filename is kept as a label only.
- Files are never overwritten, and assets are never deleted in this milestone.
- Integrity: size is checked on every verify; sha256 is recorded at import and recomputed on request.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path

from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field

from .schema import SCHEMA_VERSION, check_id, new_id
from .store import _atomic_write, now_iso, read_json_with_backup

MAX_IMAGE_BYTES = 25 * 1024 * 1024
MAX_PIXELS = 60_000_000  # decompression-bomb guard

_FORMATS = {"PNG": ("image/png", "png"), "JPEG": ("image/jpeg", "jpg"), "WEBP": ("image/webp", "webp")}


class AssetError(Exception):
    status = 400


class Asset(BaseModel):
    id: str
    kind: str = "image"
    rel_path: str = Field(pattern=r"^assets/images/[a-f0-9]{32}\.(png|jpg|webp)$")  # relative, no traversal
    mime: str
    size: int = Field(ge=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    width: int | None = None
    height: int | None = None
    label: str = ""
    source: str = "upload"
    created_at: str
    provenance: dict = {}   # for generated outputs: job, workflow, parameters, model files, versions


class AssetFile(BaseModel):
    schema_version: int = SCHEMA_VERSION
    assets: list[Asset] = []


def inspect_image(data: bytes) -> tuple[str, str, int, int]:
    """Return (mime, extension, width, height) or raise AssetError. Decodes the image to be sure."""
    if len(data) > MAX_IMAGE_BYTES:
        raise AssetError(f"image is larger than {MAX_IMAGE_BYTES // (1024 * 1024)} MB")
    try:
        with Image.open(io.BytesIO(data)) as img:
            fmt = img.format
            if fmt not in _FORMATS:
                raise AssetError("only PNG, JPEG and WebP images are accepted")
            width, height = img.size
            if width * height > MAX_PIXELS:
                raise AssetError("image dimensions are too large")
            img.verify()
    except AssetError:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError):
        raise AssetError("the file is not a readable image")
    mime, ext = _FORMATS[fmt]
    return mime, ext, width, height


class AssetRegistry:
    def __init__(self, project_dir: Path):
        self.dir = Path(project_dir)
        self.index = self.dir / "assets.json"

    def load(self) -> tuple[AssetFile, str | None]:
        if not self.index.exists() and not self.index.with_name(self.index.name + ".bak").exists():
            return AssetFile(), None
        raw, note = read_json_with_backup(self.index)
        try:
            return AssetFile.model_validate(raw), note
        except ValueError:
            raise AssetError("asset registry is invalid")

    def save(self, af: AssetFile) -> None:
        _atomic_write(self.index, json.dumps(af.model_dump(), indent=2), keep_backup=True)

    def _safe_file(self, rel_path: str) -> Path:
        p = (self.dir / rel_path).resolve()
        if (self.dir / "assets" / "images").resolve() not in p.parents:
            raise AssetError("asset path escapes the project")
        return p

    def import_bytes(self, data: bytes, label: str = "", source: str = "upload", provenance: dict | None = None) -> Asset:
        if not data:
            raise AssetError("empty file")
        mime, ext, width, height = inspect_image(data)
        aid = new_id()
        rel = f"assets/images/{aid}.{ext}"
        target = self._safe_file(rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():  # ids are random; this would be a bug, never overwrite
            raise AssetError("asset id collision; nothing was written")
        tmp = target.with_name(target.name + ".tmp")
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
        asset = Asset(id=aid, rel_path=rel, mime=mime, size=len(data),
                      sha256=hashlib.sha256(data).hexdigest(), width=width, height=height,
                      label=label[:120], source=source, created_at=now_iso(), provenance=provenance or {})
        af, _ = self.load()
        af.assets.append(asset)
        self.save(af)
        return asset

    def get(self, aid: str) -> Asset:
        check_id(aid)
        af, _ = self.load()
        for a in af.assets:
            if a.id == aid:
                return a
        raise AssetError("asset not found")

    def verify(self, asset: Asset, deep: bool = False) -> dict:
        """status: ok | missing | size_mismatch | hash_mismatch. deep=True rehashes the file."""
        try:
            p = self._safe_file(asset.rel_path)
        except AssetError as e:
            return {"status": "invalid_path", "detail": str(e)}
        if not p.is_file():
            return {"status": "missing", "detail": "file not found in the project"}
        size = p.stat().st_size
        if size != asset.size:
            return {"status": "size_mismatch", "detail": f"expected {asset.size} bytes, found {size}"}
        if deep:
            digest = hashlib.sha256(p.read_bytes()).hexdigest()
            if digest != asset.sha256:
                return {"status": "hash_mismatch", "detail": "contents changed since import"}
        return {"status": "ok", "detail": None}

    def file_path(self, asset: Asset) -> Path:
        p = self._safe_file(asset.rel_path)
        if not p.is_file():
            raise AssetError("file missing")
        return p
