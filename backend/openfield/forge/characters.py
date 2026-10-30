"""AI Influencer characters: a name, an anchor photo and a history of identity checks. No model weights are stored.

A character is data. Its anchor image is passed as a reference to a workflow on every run, and each output is
checked against the anchor. Nothing here trains anything.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

_ID = re.compile(r"^[0-9a-f]{32}$")
MAX_NAME = 60
MAX_NOTES = 500
MAX_HISTORY = 200
MAX_DATASET = 500


class CharacterError(ValueError):
    """The request cannot be done. The message is safe to show."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class CharacterStore:
    def __init__(self, root: Path):
        self.root = root

    def _path(self, cid: str) -> Path:
        if not _ID.match(cid):
            raise CharacterError("unknown character")
        return self.root / f"{cid}.json"

    def _write(self, rec: dict) -> dict:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._path(rec["id"])
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(rec, indent=2), encoding="utf-8")
        tmp.replace(path)
        return rec

    def create(self, name: str, notes: str = "") -> dict:
        name = (name or "").strip()
        if not name or len(name) > MAX_NAME:
            raise CharacterError(f"give the character a name of 1 to {MAX_NAME} characters")
        if len(notes or "") > MAX_NOTES:
            raise CharacterError(f"notes are longer than {MAX_NOTES} characters")
        rec = {"id": uuid.uuid4().hex, "name": name, "notes": (notes or "").strip(), "created_at": _now(),
               "anchor": None, "history": []}
        return self._write(rec)

    def get(self, cid: str) -> dict:
        try:
            return json.loads(self._path(cid).read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise CharacterError("unknown character")

    def list(self) -> list[dict]:
        out = []
        if self.root.is_dir():
            for p in self.root.glob("*.json"):
                try:
                    rec = json.loads(p.read_text(encoding="utf-8"))
                    out.append({k: rec[k] for k in ("id", "name", "created_at")} | {
                        "anchor": bool(rec.get("anchor")), "checks": len(rec.get("history", []))})
                except (OSError, ValueError, KeyError):
                    continue
        return sorted(out, key=lambda r: r["created_at"], reverse=True)

    def set_anchor(self, cid: str, anchor: dict) -> dict:
        rec = self.get(cid)
        rec["anchor"] = anchor
        return self._write(rec)

    def add_dataset_item(self, cid: str, item: dict) -> dict:
        rec = self.get(cid)
        rec["dataset"] = (rec.get("dataset", []) + [item])[-MAX_DATASET:]
        return self._write(rec)

    def remove_dataset_item(self, cid: str, item_id: str) -> dict:
        rec = self.get(cid)
        rec["dataset"] = [i for i in rec.get("dataset", []) if i["id"] != item_id]
        return self._write(rec)

    def add_check(self, cid: str, check: dict) -> dict:
        rec = self.get(cid)
        rec["history"] = (rec.get("history", []) + [check])[-MAX_HISTORY:]
        return self._write(rec)
