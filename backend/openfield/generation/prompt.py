"""Compose the positive and negative prompts for a shot from project data.

Camera and framing settings become prompt text. That is guidance only: the prompt is
not checked against the picture, so the job record says so.
"""
from __future__ import annotations

from ..projects.schema import ProjectMeta, Shot

NEGATIVE_DEFAULT = "blurry, low quality, distorted face, extra limbs, watermark, text, logo"

_LABEL = lambda v: v.replace("_", " ")  # noqa: E731


def compose(meta: ProjectMeta, shot: Shot) -> tuple[str, str, list[str]]:
    """Return (positive, negative, notes). notes list what was and was not included."""
    parts: list[str] = []
    notes: list[str] = []
    ents = {e.id: e for e in meta.entities}
    loc = ents.get(shot.location_id) if shot.location_id else None
    if loc and loc.kind == "world":
        desc = loc.fields.get("description") or loc.fields.get("atmosphere") or ""
        parts.append(f"{loc.name}" + (f", {desc}" if desc else ""))
    else:
        notes.append("no location description")
    for cid in shot.character_ids:
        c = ents.get(cid)
        if c is None or c.kind != "character":
            notes.append("a character on the shot no longer exists; omitted")
            continue
        desc = c.fields.get("description") or ""
        look = ", ".join(x for x in (c.fields.get("hair"), c.fields.get("clothing")) if x)
        parts.append(f"{c.name}" + (f", {desc}" if desc else "") + (f", {look}" if look else ""))
    text_bits = [shot.texts.get("beat"), shot.texts.get("action"), shot.texts.get("lighting")]
    parts += [t for t in text_bits if t]
    parts.append(f"{_LABEL(shot.shot_size)} shot")
    parts.append(f"{_LABEL(shot.camera_angle)} angle")
    parts.append(f"{_LABEL(shot.lens)} lens")
    parts.append(f"{_LABEL(shot.depth)} depth of field")
    parts.append(f"{_LABEL(shot.composition)} composition")
    if shot.camera_move != "static":
        parts.append(f"camera {_LABEL(shot.camera_move)} (guidance only)")
    if shot.prop_ids:
        props = [ents[p].name for p in shot.prop_ids if p in ents and ents[p].kind == "prop"]
        if props:
            parts.append("with " + ", ".join(props))
    seen: set[str] = set()
    unique = []
    for p in parts:  # the beat and the action are often the same sentence; say it once
        key = p.strip().rstrip(".").lower()
        if p and key and key not in seen:
            seen.add(key)
            unique.append(p.strip())
    positive = ", ".join(unique)
    if not positive:
        notes.append("shot has no description; prompt is generic")
        positive = "cinematic still photograph"
    return positive, NEGATIVE_DEFAULT, notes
