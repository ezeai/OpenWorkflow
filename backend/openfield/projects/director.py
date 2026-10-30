"""Director: deterministic script parsing, shot planning, presets, coverage rules and capability mapping.

Everything here is rule-based and runs without any language model. Each proposed shot
carries the rule that produced it, so the user can see why it exists.

Camera controls are intent. A control is only 'honoured' when the selected workflow
says so (workflows.json). For image workflows that means camera movement is recorded
as metadata, never presented as a physical camera move.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .schema import ProjectMeta, Shot, check_id, new_id, MIN_DURATION, MAX_DURATION
from .store import now_iso

WORKFLOWS_FILE = Path(__file__).resolve().parent.parent / "data" / "workflows.json"

HEADING = re.compile(r"^\s*(INT\.|EXT\.|INT/EXT\.|SCENE:|#)\s*(.*)$", re.IGNORECASE)
DIALOGUE = re.compile(r"^\s*([A-Za-z][A-Za-z '\-]{0,30}):\s*(.+)$")

# control -> workflow declaration key (workflows.json lists 'unsupported_controls' and 'supported_params')
CONTROL_KEYS = {
    "shot_size": "framing", "camera_angle": "camera_angle", "camera_move": "camera_move",
    "lens": "lens", "depth": "aperture", "composition": "composition", "lighting": "lighting",
    "color": "color", "move_intensity": "motion_intensity", "duration_s": "duration",
}


def scene_id_for(heading: str, ordinal: int) -> str:
    digest = hashlib.sha1(f"{ordinal}:{heading}".encode("utf-8")).hexdigest()
    return digest[:16]


def parse_script(text: str) -> list[dict]:
    """Scenes from a plain-text script. Headings: INT./EXT./SCENE:/#. No heading = one scene."""
    scenes: list[dict] = []
    current: dict | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = HEADING.match(line)
        if m:
            heading = line
            current = {"id": scene_id_for(heading, len(scenes)), "title": heading.lstrip("#").strip(),
                       "heading": heading, "beats": []}
            scenes.append(current)
            continue
        if current is None:
            current = {"id": scene_id_for("untitled", 0), "title": "Untitled scene", "heading": "", "beats": []}
            scenes.append(current)
        d = DIALOGUE.match(line)
        if d:
            current["beats"].append({"kind": "dialogue", "speaker": d.group(1).strip(), "text": d.group(2).strip()})
        else:
            current["beats"].append({"kind": "action", "speaker": None, "text": line})
    return scenes


def _location_from_heading(heading: str) -> str:
    h = re.sub(r"^(INT\.|EXT\.|INT/EXT\.)\s*", "", heading, flags=re.IGNORECASE)
    return h.split(" - ")[0].strip().lower()


def _mentions(text: str, names: dict[str, str]) -> list[str]:
    """ids of entities whose name appears as a whole word in text."""
    found = []
    low = text.lower()
    for name_low, eid in names.items():
        if re.search(rf"\b{re.escape(name_low)}\b", low):
            found.append(eid)
    return found


MOVE_RULES = [  # word forms -> suggested movement. Whole words only; the rule is shown to the user.
    (("run", "runs", "running", "ran", "chase", "chases", "chasing", "rush", "rushes", "sprint", "sprints"),
     "tracking", "beat mentions running: suggested tracking movement"),
    (("walk", "walks", "walking", "walked", "stroll", "strolls", "pace", "paces", "pacing", "paced"),
     "tracking", "beat mentions walking: suggested tracking movement"),
    (("stare", "stares", "staring", "watch", "watches", "watching", "gaze", "gazes", "gazing"),
     "push_in", "beat mentions looking: suggested slow push-in"),
    (("rain", "rains", "raining", "storm", "storms", "wind", "winds", "windy"),
     "handheld", "beat mentions weather: suggested handheld for texture"),
]


def propose_plan(meta: ProjectMeta) -> dict:
    """Proposal only. Nothing is written until the user applies a selection."""
    scenes = parse_script(meta.script_text)
    chars = {e.name.lower(): e.id for e in meta.entities if e.kind == "character" and not e.archived}
    worlds = {e.name.lower(): e.id for e in meta.entities if e.kind == "world" and not e.archived}
    props = {e.name.lower(): e.id for e in meta.entities if e.kind == "prop" and not e.archived}
    proposals: list[dict] = []
    for scene in scenes:
        loc_name = _location_from_heading(scene["heading"]) if scene["heading"] else ""
        loc_id = next((wid for name, wid in worlds.items() if loc_name and (name in loc_name or loc_name in name)), None)
        base = {"scene_id": scene["id"], "scene_title": scene["title"], "location_id": loc_id}
        proposals.append({**base, "shot_size": "establishing", "camera_move": "static", "camera_angle": "eye_level",
                          "character_ids": [], "prop_ids": [], "texts": {"purpose": "Establish the location",
                                                                          "beat": scene["title"]},
                          "why": "rule: every scene opens with an establishing shot"})
        last_speaker = None
        for beat in scene["beats"]:
            if beat["kind"] == "dialogue":
                speaker_id = chars.get(beat["speaker"].lower())
                proposals.append({**base, "shot_size": "close_up", "camera_move": "static", "camera_angle": "eye_level",
                                  "character_ids": [speaker_id] if speaker_id else [], "prop_ids": [],
                                  "texts": {"purpose": "Dialogue", "beat": beat["text"], "action": f"{beat['speaker']} speaks"},
                                  "why": "rule: dialogue line gets a close-up of the speaker"})
                last_speaker = speaker_id
                listeners = [v for v in chars.values() if v != speaker_id][:1]
                if listeners:
                    proposals.append({**base, "shot_size": "reaction", "camera_move": "static", "camera_angle": "eye_level",
                                      "character_ids": listeners, "prop_ids": [],
                                      "texts": {"purpose": "Reaction", "beat": beat["text"]},
                                      "why": "rule: dialogue is followed by a reaction shot of another character"})
            else:
                move, why_move = "static", None
                low = beat["text"].lower()
                for words, mv, why in MOVE_RULES:
                    if any(re.search(rf"\b{re.escape(w)}\b", low) for w in words):  # whole-word match
                        move, why_move = mv, why
                        break
                ids = _mentions(beat["text"], chars)
                pids = _mentions(beat["text"], props)
                proposals.append({**base, "shot_size": "medium" if ids else "wide", "camera_move": move,
                                  "camera_angle": "eye_level", "character_ids": ids, "prop_ids": pids,
                                  "texts": {"purpose": "Action", "beat": beat["text"], "action": beat["text"]},
                                  "why": "rule: action beat" + (f"; {why_move}" if why_move else "")})
    return {"scenes": [{"id": s["id"], "title": s["title"]} for s in scenes], "proposals": proposals,
            "note": "Proposal only. Select the shots you want and apply them. Existing shots are never replaced."}


PRESETS = {
    "dialogue_close_up": {"label": "Dialogue close-up", "shot_size": "close_up", "camera_angle": "eye_level",
                          "camera_move": "static", "lens": "portrait", "depth": "shallow", "composition": "rule_of_thirds"},
    "hero_entrance": {"label": "Hero entrance", "shot_size": "medium_close_up", "camera_angle": "low",
                      "camera_move": "push_in", "lens": "normal", "depth": "moderate", "composition": "centered"},
    "environmental_establishing": {"label": "Environmental establishing", "shot_size": "establishing", "camera_angle": "high",
                                   "camera_move": "crane", "lens": "wide_angle", "depth": "deep", "composition": "layered_depth"},
    "suspense_reveal": {"label": "Suspense reveal", "shot_size": "medium", "camera_angle": "dutch",
                        "camera_move": "push_in", "lens": "normal", "depth": "shallow", "composition": "negative_space"},
    "product_hero": {"label": "Product hero", "shot_size": "insert", "camera_angle": "eye_level", "camera_move": "orbit",
                     "lens": "macro", "depth": "subject_isolation", "composition": "centered"},
    "emotional_reaction": {"label": "Emotional reaction", "shot_size": "reaction", "camera_angle": "eye_level",
                           "camera_move": "static", "lens": "portrait", "depth": "shallow", "composition": "centered"},
    "chase_tracking": {"label": "Chase tracking", "shot_size": "medium_close_up", "camera_angle": "eye_level",
                       "camera_move": "tracking", "lens": "wide_angle", "depth": "moderate", "composition": "leading_lines"},
    "quiet_portrait": {"label": "Quiet portrait", "shot_size": "medium_close_up", "camera_angle": "eye_level",
                       "camera_move": "static", "lens": "portrait", "depth": "subject_isolation", "composition": "rule_of_thirds"},
}


def preset_fields(name: str) -> dict:
    if name not in PRESETS:
        raise KeyError(name)
    p = PRESETS[name]
    return {k: v for k, v in p.items() if k != "label"}


def _workflows() -> dict:
    raw = json.loads(WORKFLOWS_FILE.read_text(encoding="utf-8"))
    return {w["id"]: w for w in raw.get("workflows", [])}


def capability_map(shot: Shot) -> dict:
    """For each control: honoured | prompt_only | unsupported | not_mapped, from the chosen workflow's declaration."""
    if not shot.workflow_id:
        return {c: "not_mapped" for c in CONTROL_KEYS} | {"_note": "No workflow chosen. Intent is stored only."}
    wf = _workflows().get(shot.workflow_id)
    if wf is None:
        return {c: "not_mapped" for c in CONTROL_KEYS} | {"_note": f"Unknown workflow {shot.workflow_id}."}
    unsupported = set(wf.get("unsupported_controls", []))
    out = {}
    for control, key in CONTROL_KEYS.items():
        # 'prompt_only' is text guidance. No workflow here has validated motion conditioning yet.
        out[control] = "unsupported" if key in unsupported else "prompt_only"
    out["_note"] = "Prompt guidance only where marked prompt_only. Nothing here is verified by a run."
    return out


def validate_shot(meta: ProjectMeta, shot: Shot) -> list[dict]:
    warnings: list[dict] = []
    ids = {e.id: e for e in meta.entities}
    scene_ids = {s["id"] for s in parse_script(meta.script_text)}
    if scene_ids and shot.scene_id not in scene_ids:
        warnings.append({"level": "warn", "code": "scene_missing",
                         "message": "This shot's scene is no longer in the script. Its scene link needs review."})
    for cid in shot.character_ids:
        e = ids.get(cid)
        if e is None or e.kind != "character":
            warnings.append({"level": "error", "code": "character_missing", "message": "A character on this shot no longer exists."})
        elif e.archived:
            warnings.append({"level": "warn", "code": "character_archived", "message": f"{e.name} is archived."})
    if shot.location_id and (ids.get(shot.location_id) is None or ids[shot.location_id].kind != "world"):
        warnings.append({"level": "error", "code": "location_missing", "message": "The location for this shot no longer exists."})
    caps = capability_map(shot)
    if caps.get("camera_move") == "unsupported":
        warnings.append({"level": "warn", "code": "move_not_executed",
                         "message": f"The workflow cannot execute '{shot.camera_move}'. It is kept as shot metadata."})
    if shot.shot_size != "establishing" and not (shot.texts.get("action") or shot.texts.get("beat")) and not shot.character_ids:
        warnings.append({"level": "info", "code": "no_content", "message": "This shot has no action, beat or character yet."})
    return warnings


def coverage_suggestions(meta: ProjectMeta) -> list[dict]:
    """Deterministic rules. Each suggestion has a stable key so the user can dismiss it."""
    out: list[dict] = []
    scenes = parse_script(meta.script_text)
    active = [s for s in meta.shots if s.enabled]

    def add(key: str, message: str, why: str):
        if key not in meta.dismissed_suggestions:
            out.append({"key": key, "message": message, "why": why})

    for s in scenes:
        in_scene = [x for x in active if x.scene_id == s["id"]]
        if in_scene and not any(x.shot_size in ("establishing", "wide", "extreme_wide") for x in in_scene):
            add(f"no-establishing:{s['id']}", f"Scene '{s['title']}' has no establishing or wide shot.",
                "Audiences need to see where a scene happens before close coverage.")
        has_dialogue = any(b["kind"] == "dialogue" for b in s["beats"])
        if has_dialogue and not any(x.shot_size == "reaction" for x in in_scene):
            add(f"no-reaction:{s['id']}", f"Scene '{s['title']}' has dialogue but no reaction shot.",
                "Dialogue scenes usually cut to listeners.")
    for i in range(len(active) - 3):
        window = active[i:i + 4]
        if len({x.shot_size for x in window}) == 1 and window[0].scene_id == window[-1].scene_id:
            add(f"repeat-framing:{window[0].id}", f"Four shots in a row use {window[0].shot_size.replace('_', ' ')}.",
                "Repeated framing reads as monotonous. Vary size or angle.")
            break
    chars_in_script = {e.id for e in meta.entities if e.kind == "character" and not e.archived
                       and re.search(rf"\b{re.escape(e.name.lower())}\b", meta.script_text.lower())}
    covered = {c for x in active for c in x.character_ids}
    for cid in sorted(chars_in_script - covered):
        name = next(e.name for e in meta.entities if e.id == cid)
        add(f"uncovered:{cid}", f"{name} appears in the script but has no shot.",
            "Every named character should appear on screen somewhere.")
    for a, b in zip(scenes, scenes[1:]):
        la, lb = _location_from_heading(a["heading"]), _location_from_heading(b["heading"])
        if la and lb and la != lb:
            key = f"location-jump:{b['id']}"
            add(key, f"Location changes to '{b['title']}' without an establishing shot.",
                "An abrupt change of place confuses viewers.")
    return out


def new_shot(number: int, scene_id: str, scene_title: str, now: str | None = None) -> Shot:
    ts = now or now_iso()
    return Shot(id=new_id(), number=number, scene_id=scene_id, scene_title=scene_title, created_at=ts, updated_at=ts)


def renumber(meta: ProjectMeta) -> None:
    for i, s in enumerate(meta.shots, start=1):
        s.number = i


def apply_proposals(meta: ProjectMeta, picks: list[dict]) -> list[str]:
    """Append chosen proposals (never replacing existing shots). Returns new shot ids."""
    created = []
    scene_titles = {s["id"]: s["title"] for s in parse_script(meta.script_text)}
    for p in picks:
        if p.get("scene_id") not in scene_titles:
            raise ValueError("proposal refers to a scene that is not in the script")
        s = new_shot(len(meta.shots) + 1, p["scene_id"], scene_titles[p["scene_id"]])
        s.location_id = p.get("location_id")
        s.character_ids = list(p.get("character_ids", []))
        s.prop_ids = list(p.get("prop_ids", []))
        s.shot_size = p.get("shot_size", "medium")
        s.camera_angle = p.get("camera_angle", "eye_level")
        s.camera_move = p.get("camera_move", "static")
        s.texts = {k: v for k, v in p.get("texts", {}).items() if k in ("purpose", "beat", "action")}
        meta.shots.append(s)
        created.append(s.id)
    renumber(meta)
    return created


def find_shot(meta: ProjectMeta, sid: str) -> Shot:
    check_id(sid)
    for s in meta.shots:
        if s.id == sid:
            return s
    raise KeyError(sid)


def approve_take(meta: ProjectMeta, shot_id: str, take_id: str, *, approve: bool) -> None:
    """Choose or unchoose a generated take. One chosen take per shot; others and never-deleted takes are kept."""
    s = find_shot(meta, shot_id)
    take = next((t for t in s.takes if t.id == take_id), None)
    if take is None:
        raise KeyError(take_id)
    if approve:
        if take.state != "generated" or not take.asset_id:
            raise ValueError("only a generated take with an output can be chosen")
        for t in s.takes:                                   # a shot ships one take; the rest revert to generated
            if t.state == "approved":
                t.state = "generated"
        take.state = "approved"
    else:
        if take.state != "approved":
            raise ValueError("this take is not the chosen one")
        take.state = "generated"
    s.revision += 1
    s.updated_at = now_iso()


__all__ = ["parse_script", "propose_plan", "PRESETS", "preset_fields", "capability_map", "validate_shot",
           "coverage_suggestions", "apply_proposals", "find_shot", "renumber", "new_shot",
           "approve_take", "MIN_DURATION", "MAX_DURATION"]
