"""Bounding-box prompting for MOSS-Image: boxes on a canvas become the layout JSON the model reads.

Ported from the MOSS-Image provider in Content Studio (studio_models/moss_image.py, the layout maths only).
Nothing here loads a model. A box is a dict with x, y, w, h as 0-1 fractions of the canvas, a type of
"obj", "character" or "text", a description, and, for text boxes, the exact words to render.

MOSS takes bounding boxes as [y0, x0, y1, x1] in pixels, centred on the canvas (origin at the middle), in the
"abs_centralized" space. The edge rounding is the same as the model's layout node, so a box that touches the
canvas edge reaches exactly the edge.
"""
from __future__ import annotations

import json
import math
import re

LABELS = {"character": "character", "text": "text"}
MAX_BOXES = 24
MAX_TEXT = 200
MAX_DESC = 400
# Text rendering rules, as Content Studio's layout tab uses them (studio_layout.py): below TEXT_FLOOR a word garbles,
# so the box grows around its centre; the caption gets legibility wording unless it already names a type treatment.
TEXT_LOOK = "bold clean capitals, crisp evenly spaced lettering, sharp and fully legible, high contrast against the background"
TEXT_FLOOR = {"w": 0.16, "h": 0.06}
MAX_WORDS = 5          # longer wording garbles in one box
HEX = re.compile(r"^#[0-9A-F]{6}$")
_WRAP_QUOTES = (('"', '"'), ("'", "'"), ("“", "”"), ("‘", "’"), ("「", "」"))
_LOOK = re.compile(r"\b(typograph|lettering|font|typeface|serif|sans|capitals|uppercase|lowercase|hand[- ]?lettered|script)\w*", re.I)


class LayoutError(ValueError):
    """A box is malformed. The message names the box so the user can fix it."""


def _edge(frac: float, size: int, round_up: bool) -> int:
    px = max(0.0, min(1.0, float(frac))) * size
    return (math.ceil(round(px, 9)) if round_up else math.floor(round(px, 9))) - size // 2


def _clean(s: object, limit: int) -> str:
    return " ".join(str(s or "").split())[:limit]


def clean_words(s: object) -> str:
    """The exact wording: one line, without the quotes people wrap it in."""
    t = _clean(s, MAX_TEXT)
    for a, b in _WRAP_QUOTES:
        if len(t) >= 2 and t.startswith(a) and t.endswith(b):
            return t[1:-1].strip()
    return t


def legible(box: dict) -> dict:
    """Grow a too-small text box around its centre until the wording has room. The layout is kept; the letters survive."""
    if box.get("type") != "text" or not box.get("text"):
        return box
    need_w = min(0.95, max(TEXT_FLOOR["w"], math.ceil((len(box["text"]) / 60) * 1e4) / 1e4))
    w, h = max(box["w"], need_w), max(box["h"], TEXT_FLOOR["h"])
    if abs(w - box["w"]) < 1e-6 and abs(h - box["h"]) < 1e-6:
        return box
    cx, cy = box["x"] + box["w"] / 2, box["y"] + box["h"] / 2
    x = round(min(max(cx - w / 2, 0.0), 1.0 - w), 4)
    y = round(min(max(cy - h / 2, 0.0), 1.0 - h), 4)
    return {**box, "x": x, "y": y, "w": round(w, 4), "h": round(h, 4)}


def caption(box: dict) -> str:
    """The caption MOSS reads for one box. Text boxes name their look unless the description already does."""
    desc = box.get("desc") or ""
    if box.get("type") == "text":
        desc = " ".join(desc.split()) or "lettering on a clear area"
        if not _LOOK.search(desc):
            desc = f"{desc}, {TEXT_LOOK}"
    return desc


def warnings(boxes: list[dict]) -> list[list[str]]:
    """Per box, what would make the render worse. Empty list = nothing to fix."""
    out: list[list[str]] = []
    for i, b in enumerate(boxes):
        w: list[str] = []
        if b["type"] == "text":
            if len(b["text"].split()) > MAX_WORDS:
                w.append(f"more than {MAX_WORDS} words in one text box; long wording garbles")
            for j, o in enumerate(boxes):
                if j == i or o["type"] != "text" or (o["w"] * o["h"]) > 0.85:
                    continue
                if not (o["x"] >= b["x"] + b["w"] or o["x"] + o["w"] <= b["x"] or o["y"] >= b["y"] + b["h"] or o["y"] + o["h"] <= b["y"]):
                    w.append(f"overlaps text box {j + 1}; overlapping text garbles")
                    break
        out.append(w)
    return out


def normalize_boxes(boxes: list[dict]) -> list[dict]:
    """Check and tidy the boxes. Refuses anything that would silently produce a wrong layout."""
    if not isinstance(boxes, list):
        raise LayoutError("boxes must be a list")
    if len(boxes) > MAX_BOXES:
        raise LayoutError(f"at most {MAX_BOXES} boxes")
    out = []
    for i, b in enumerate(boxes, start=1):
        if not isinstance(b, dict):
            raise LayoutError(f"box {i} is not an object")
        try:
            x, y, w, h = (float(b[k]) for k in ("x", "y", "w", "h"))
        except (KeyError, TypeError, ValueError):
            raise LayoutError(f"box {i} needs numeric x, y, w and h")
        if not all(0.0 <= v <= 1.0 for v in (x, y)) or not (0.0 < w <= 1.0 and 0.0 < h <= 1.0):
            raise LayoutError(f"box {i} must lie on the canvas: x and y from 0 to 1, w and h above 0 up to 1")
        if x + w > 1.000001 or y + h > 1.000001:
            raise LayoutError(f"box {i} runs past the canvas edge")
        kind = b.get("type", "obj")
        if kind not in ("obj", "character", "text"):
            raise LayoutError(f"box {i} has an unknown type '{kind}'")
        item = {"type": kind, "x": x, "y": y, "w": w, "h": h, "desc": _clean(b.get("desc"), MAX_DESC)}
        palette = [("#" + str(c).strip().lstrip("#")).upper() for c in (b.get("palette") or [])]
        if any(not HEX.match(c) for c in palette):
            raise LayoutError(f"box {i} has a colour that is not a hex code like #C8102E")
        if palette:
            item["palette"] = palette[:5]
        if kind == "text":
            item["text"] = clean_words(b.get("text"))
            if not item["text"]:
                raise LayoutError(f"text box {i} has no words to render")
            item = legible(item)
        out.append(item)
    return out


def bbox(box: dict, width: int, height: int) -> list[int]:
    """One box as [y0, x0, y1, x1] in centred pixels."""
    x, y, w, h = box["x"], box["y"], box["w"], box["h"]
    return [_edge(y, height, False), _edge(x, width, False), _edge(y + h, height, True), _edge(x + w, width, True)]


def layout_prompt(prompt: str, boxes: list[dict] | None, width: int, height: int) -> str:
    """The prompt as MOSS layout JSON. No boxes gives back the prompt unchanged."""
    if not boxes:
        return prompt
    items = []
    for box in normalize_boxes(boxes):
        label = "text" if box["type"] == "text" else ("character" if box["type"] == "character" else "object")
        item = {"label": label, "caption": caption(box), "bbox": bbox(box, width, height)}
        if box.get("palette"):
            item["palette"] = box["palette"]
        if label == "text":
            item["text"] = box["text"]
        items.append(item)
    return json.dumps({"summary": {"caption": prompt.strip()}, "items": items,
                       "bbox_coord_space": "abs_centralized", "bbox_format": "yxyx"}, ensure_ascii=False, separators=(",", ":"))
