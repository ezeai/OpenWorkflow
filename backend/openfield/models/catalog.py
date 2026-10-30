"""Loads the built-in model catalogue. A malformed entry is reported and skipped, never fatal."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from .schema import CatalogModel

DATA = Path(__file__).resolve().parent.parent / "data"


@dataclass
class LoadedCatalog:
    models: dict[str, CatalogModel] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


def load_catalog(path: Path = DATA / "model_catalog.json") -> LoadedCatalog:
    out = LoadedCatalog()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        out.errors.append(f"catalog unreadable: {type(e).__name__}: {e}")
        return out
    if raw.get("schema_version") != 1:
        out.errors.append(f"unsupported catalog schema_version {raw.get('schema_version')!r}")
        return out
    for i, entry in enumerate(raw.get("models", [])):
        try:
            m = CatalogModel.model_validate(entry)
        except ValidationError as e:
            name = entry.get("id", f"#{i}") if isinstance(entry, dict) else f"#{i}"
            out.errors.append(f"model {name} skipped: {e.error_count()} validation error(s)")
            continue
        if m.id in out.models:
            out.errors.append(f"duplicate catalog id {m.id}; later entry skipped")
            continue
        out.models[m.id] = m
    return out
