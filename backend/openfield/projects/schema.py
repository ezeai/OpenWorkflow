"""Versioned project schema. Stable IDs, relative paths only, strict field validation.

Entities are characters, worlds, styles and props. Their fields are plain text
(bounded), and each kind has an explicit allow-list. Unknown fields are rejected.
Creative metadata and reference images are kept apart: identity data lives on the
character, and references point at project assets by id.
"""
from __future__ import annotations

import re
import uuid
from typing import Literal

from pydantic import BaseModel, Field, field_validator

SCHEMA_VERSION = 3
MAX_FIELD_CHARS = 4000
MAX_TITLE_CHARS = 120

EntityKind = Literal["character", "world", "style", "prop"]
IdentityLock = Literal["off", "balanced", "strong"]

ID_PATTERN = re.compile(r"^[a-f0-9]{32}$")


def new_id() -> str:
    return uuid.uuid4().hex


def check_id(value: str) -> str:
    if not ID_PATTERN.match(value):
        raise ValueError("invalid id")
    return value


# field allow-lists per kind: name -> label
FIELDS: dict[str, dict[str, str]] = {
    "character": {
        "description": "Description", "age_range": "Age range", "appearance": "Appearance",
        "face": "Face and features", "skin_tone": "Skin tone", "hair": "Hair", "body": "Body and silhouette",
        "clothing": "Clothing (default outfit)", "accessories": "Accessories", "distinctive_marks": "Distinctive marks",
        "personality": "Personality notes", "voice_notes": "Voice or dialogue notes (metadata only)",
        "preferred_style": "Preferred visual style", "identity_lock": "Identity lock",
    },
    "world": {
        "description": "Description", "environment_type": "Environment type", "architecture": "Architecture",
        "geography": "Geography", "weather": "Weather", "time_of_day": "Time of day",
        "lighting": "Lighting conditions", "palette": "Color palette", "atmosphere": "Atmosphere",
        "materials": "Materials and textures", "layout_notes": "Spatial layout notes", "style_rules": "Style rules",
    },
    "style": {
        "description": "Description", "genre": "Visual genre", "palette": "Color palette", "lighting": "Lighting",
        "contrast": "Contrast", "texture": "Texture", "atmosphere": "Atmosphere", "era": "Film-era inspiration",
        "composition": "Composition tendencies", "image_treatment": "Image treatment",
        "preferred_workflow": "Preferred model or workflow",
    },
    "prop": {
        "description": "Description", "material": "Material", "condition": "Condition",
        "continuity_notes": "Continuity notes",
    },
}

REF_ROLES: dict[str, list[str]] = {
    "character": ["face", "three_quarter", "profile", "full_body", "hair", "clothing", "expression", "other"],
    "world": ["reference", "other"],
    "style": ["reference", "other"],
    "prop": ["reference", "other"],
}


def validate_fields(kind: str, fields: dict) -> dict:
    allowed = FIELDS[kind]
    unknown = sorted(set(fields) - set(allowed))
    if unknown:
        raise ValueError(f"unknown field(s) for {kind}: {', '.join(unknown)}")
    clean: dict[str, str] = {}
    for k, v in fields.items():
        if not isinstance(v, str):
            raise ValueError(f"{k} must be text")
        if len(v) > MAX_FIELD_CHARS:
            raise ValueError(f"{k} is longer than {MAX_FIELD_CHARS} characters")
        clean[k] = v.strip()
    if kind == "character" and "identity_lock" in clean and clean["identity_lock"] not in ("", "off", "balanced", "strong"):
        raise ValueError("identity_lock must be off, balanced or strong")
    return clean


def validate_refs(kind: str, refs: list[dict]) -> list[dict]:
    roles = REF_ROLES[kind]
    out = []
    seen = set()
    for r in refs:
        aid = check_id(str(r.get("asset_id", "")))
        role = r.get("role", "other")
        if role not in roles:
            raise ValueError(f"role '{role}' is not valid for {kind}")
        if aid in seen:
            raise ValueError("the same image is referenced twice")
        seen.add(aid)
        out.append({"asset_id": aid, "role": role})
    return out


class Version(BaseModel):
    version: int = Field(ge=1)
    created_at: str
    fields: dict[str, str]
    refs: list[dict]
    note: str = ""


class Entity(BaseModel):
    id: str
    kind: EntityKind
    name: str = Field(min_length=1, max_length=MAX_TITLE_CHARS)
    fields: dict[str, str] = {}
    refs: list[dict] = []
    current_version: int = 1
    versions: list[Version] = []
    archived: bool = False
    revision: int = 1
    created_at: str
    updated_at: str

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        return check_id(v)


# ---- shot direction (Milestone 4). Enumerations are the only accepted values. ----

SHOT_SIZES = ("extreme_wide", "wide", "medium", "medium_close_up", "close_up", "extreme_close_up",
              "over_the_shoulder", "two_shot", "profile", "insert", "reaction", "establishing")
CAMERA_ANGLES = ("eye_level", "low", "high", "birds_eye", "ground_level", "dutch", "profile", "pov")
CAMERA_MOVES = ("static", "push_in", "pull_out", "pan", "tilt", "tracking", "dolly", "orbit", "crane",
                "handheld", "pov_movement", "aerial")
LENSES = ("wide_angle", "normal", "portrait", "telephoto", "macro", "fisheye")
DEPTHS = ("deep", "moderate", "shallow", "subject_isolation")
COMPOSITIONS = ("centered", "rule_of_thirds", "symmetrical", "negative_space", "leading_lines",
                "foreground_framing", "layered_depth")
INTENSITIES = ("low", "medium", "high")
TAKE_STATES = ("requested", "running", "generated", "failed", "cancelled", "approved", "archived")
MIN_DURATION, MAX_DURATION = 0.5, 30.0

SHOT_TEXT_FIELDS = ("purpose", "beat", "subject_position", "lighting", "color", "action", "tone",
                    "continuity_notes")


class Take(BaseModel):
    id: str
    number: int = Field(ge=1)
    state: str = "requested"
    note: str = Field(default="", max_length=500)
    settings: dict = {}              # snapshot of the shot's settings at request time
    asset_id: str | None = None
    job_id: str | None = None
    created_at: str

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        return check_id(v)

    @field_validator("state")
    @classmethod
    def _state(cls, v: str) -> str:
        if v not in TAKE_STATES:
            raise ValueError("invalid take state")
        return v


class Shot(BaseModel):
    id: str
    number: int = Field(ge=1)
    scene_id: str = Field(max_length=64)
    scene_title: str = Field(default="", max_length=200)
    character_ids: list[str] = []
    location_id: str | None = None
    prop_ids: list[str] = []
    shot_size: str = "medium"
    camera_angle: str = "eye_level"
    camera_move: str = "static"
    lens: str = "normal"
    depth: str = "moderate"
    composition: str = "rule_of_thirds"
    move_intensity: str = "medium"
    duration_s: float = Field(default=5.0, ge=MIN_DURATION, le=MAX_DURATION)
    workflow_id: str | None = None
    enabled: bool = True
    preset: str | None = None
    texts: dict[str, str] = {}
    takes: list[Take] = []
    created_at: str
    updated_at: str
    revision: int = 1

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        return check_id(v)

    @field_validator("shot_size")
    @classmethod
    def _size(cls, v: str) -> str:
        if v not in SHOT_SIZES:
            raise ValueError("unknown shot size")
        return v

    @field_validator("camera_angle")
    @classmethod
    def _angle(cls, v: str) -> str:
        if v not in CAMERA_ANGLES:
            raise ValueError("unknown camera angle")
        return v

    @field_validator("camera_move")
    @classmethod
    def _move(cls, v: str) -> str:
        if v not in CAMERA_MOVES:
            raise ValueError("unknown camera movement")
        return v

    @field_validator("lens")
    @classmethod
    def _lens(cls, v: str) -> str:
        if v not in LENSES:
            raise ValueError("unknown lens")
        return v

    @field_validator("depth")
    @classmethod
    def _depth(cls, v: str) -> str:
        if v not in DEPTHS:
            raise ValueError("unknown depth of field")
        return v

    @field_validator("composition")
    @classmethod
    def _comp(cls, v: str) -> str:
        if v not in COMPOSITIONS:
            raise ValueError("unknown composition")
        return v

    @field_validator("move_intensity")
    @classmethod
    def _int(cls, v: str) -> str:
        if v not in INTENSITIES:
            raise ValueError("unknown motion intensity")
        return v

    @field_validator("texts")
    @classmethod
    def _texts(cls, v: dict) -> dict:
        for k, val in v.items():
            if k not in SHOT_TEXT_FIELDS:
                raise ValueError(f"unknown shot text field {k}")
            if not isinstance(val, str) or len(val) > MAX_FIELD_CHARS:
                raise ValueError(f"{k} is too long or not text")
        return v


class ProjectMeta(BaseModel):
    schema_version: int = SCHEMA_VERSION
    id: str
    title: str = Field(min_length=1, max_length=MAX_TITLE_CHARS)
    description: str = Field(default="", max_length=MAX_FIELD_CHARS)
    created_at: str
    updated_at: str
    revision: int = 1
    entities: list[Entity] = []
    script_text: str = Field(default="", max_length=200_000)
    shots: list[Shot] = []
    dismissed_suggestions: list[str] = []
    continuity_rules: list[dict] = []   # validated by projects.continuity.Rule when used
    warning_acks: list[dict] = []       # {key, reason, at}: a warning the user accepted, with the reason
    graphs: list[dict] = []             # validated by projects.graph.Graph when used

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        return check_id(v)
