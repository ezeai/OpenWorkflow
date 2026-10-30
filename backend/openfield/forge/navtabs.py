"""Nav tabs: imported apps pinned to the main navigation, in the order and with the labels the user chose.

The list lives in one JSON file in the Forge home, so it survives restarts and is the same for every page.
A tab only names an app id; it never copies the app, so deleting an app leaves its tab to be dropped on read.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

from ..projects.store import _atomic_write

MAX_TABS = 20
_APP_ID = re.compile(r"^[0-9a-f]{32}$")


class NavTab(BaseModel):
    app_id: str
    label: str = Field(min_length=1, max_length=40)

    @field_validator("app_id")
    @classmethod
    def _app_id(cls, v: str) -> str:
        if not _APP_ID.match(v):
            raise ValueError("an app id is 32 lowercase hex characters")
        return v

    @field_validator("label")
    @classmethod
    def _label(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("a tab needs a name")
        return v


class NavTabsBody(BaseModel):
    tabs: list[NavTab] = Field(max_length=MAX_TABS)

    @field_validator("tabs")
    @classmethod
    def _unique(cls, v: list[NavTab]) -> list[NavTab]:
        ids = [t.app_id for t in v]
        if len(set(ids)) != len(ids):
            raise ValueError("an app can only have one tab")
        return v


def load(path: Path) -> list[dict]:
    """The saved tabs. A missing or unreadable file means no tabs; it never fails the page."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    tabs = data.get("tabs") if isinstance(data, dict) else None
    return [t for t in tabs or [] if isinstance(t, dict) and _APP_ID.match(str(t.get("app_id", "")))]


def save(path: Path, body: NavTabsBody) -> list[dict]:
    rows = [t.model_dump() for t in body.tabs]
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(path, json.dumps({"tabs": rows}, indent=2), keep_backup=False)
    return rows
