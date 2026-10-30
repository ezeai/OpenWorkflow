"""Continuity: rules the user defines, and checks that run on every project read.

Checks are deterministic and never block work. Each warning has a stable key. The user
can accept a warning with a written reason, and the acceptance stays visible.

Built-in checks
  outdated_character   a generated take used an older character version than now
  outdated_location    a generated take used an older location version than now
  missing_reference    a character's reference image is missing or changed on disk
  identity_unsupported a character asks for identity locking the shot's workflow cannot apply
  move_not_executed    camera movement that the workflow does not execute (shown on shots already)

User rules
  same_character_version   every generated take of a shot featuring the character uses the newest version
  reference_take           a target shot must have an approved take from a source shot before it is generated
  world_time               a world must have a given time of day wherever it is used
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .schema import ProjectMeta, check_id, new_id

RuleKind = Literal["same_character_version", "reference_take", "world_time"]
RULE_KINDS = ("same_character_version", "reference_take", "world_time")


class Rule(BaseModel):
    id: str
    kind: RuleKind
    params: dict[str, str] = Field(default_factory=dict)
    note: str = Field(default="", max_length=300)


def make_rule(kind: str, params: dict[str, str], note: str) -> dict:
    if kind not in RULE_KINDS:
        raise ValueError("unknown rule kind")
    need = {"same_character_version": ["entity_id"], "reference_take": ["target_shot", "source_shot"],
            "world_time": ["world_id", "time_of_day"]}[kind]
    missing = [k for k in need if not params.get(k)]
    if missing:
        raise ValueError(f"rule needs: {', '.join(missing)}")
    for k in need:
        if k.endswith("_shot") or k.endswith("_id"):
            check_id(params[k])
    return Rule(id=new_id(), kind=kind, params={k: str(v) for k, v in params.items()}, note=note.strip()).model_dump()


def _key(code: str, *parts: str) -> str:
    return ":".join([code, *parts])


def evaluate(meta: ProjectMeta, asset_status: dict[str, str] | None = None) -> list[dict]:
    """asset_status maps asset id -> status ('ok' or problem). Passed in by the API layer."""
    from .director import capability_map, find_shot  # local import: avoid a cycle at import time

    ents = {e.id: e for e in meta.entities}
    out: list[dict] = []

    def add(key: str, shot_id: str | None, message: str, why: str, rule_id: str | None = None):
        out.append({"key": key, "shot_id": shot_id, "message": message, "why": why, "rule_id": rule_id})

    for s in meta.shots:
        if not s.enabled:
            continue
        generated = [t for t in s.takes if t.state == "generated"]
        latest = generated[-1] if generated else None
        for cid in s.character_ids:
            e = ents.get(cid)
            if e is None:
                continue
            if latest and "versions" in latest.settings:
                used = latest.settings["versions"].get(cid)
                if used is not None and used != e.current_version:
                    add(_key("outdated_character", s.id, cid), s.id,
                        f"{e.name} changed since this shot's latest take (v{used} → v{e.current_version}).",
                        "The take was made from an older version of this character.")
            if e.fields.get("identity_lock") in ("balanced", "strong"):
                caps = capability_map(s)
                if caps.get("identity_lock") != "honoured":
                    add(_key("identity_unsupported", s.id, cid), s.id,
                        f"{e.name} asks for identity locking, which this shot's workflow does not apply.",
                        "No workflow in this build applies identity locking. The setting is saved, not applied.")
        if s.location_id and latest and "versions" in latest.settings and s.location_id in ents:
            loc = ents[s.location_id]
            used = latest.settings["versions"].get(s.location_id)
            if used is not None and used != loc.current_version:
                add(_key("outdated_location", s.id, s.location_id), s.id,
                    f"{loc.name} changed since this shot's latest take (v{used} → v{loc.current_version}).",
                    "The take was made from an older version of this location.")

    for e in meta.entities:  # reference images are checked for every character, shot or not
        if e.kind != "character" or e.archived:
            continue
        for r in e.refs:
            st = (asset_status or {}).get(r["asset_id"], "ok")
            if st != "ok":
                add(_key("missing_reference", e.id, r["asset_id"]), None,
                    f"A reference image for {e.name} is {('missing' if st == 'missing' else 'changed on disk')}.",
                    "The image is no longer the one the character was built from.")

    for rule in meta.continuity_rules:
        rid = rule["id"]
        p = rule["params"]
        if rule["kind"] == "same_character_version" and p.get("entity_id") in ents:
            e = ents[p["entity_id"]]
            for s in meta.shots:
                if p["entity_id"] not in s.character_ids or not s.enabled:
                    continue
                gen = [t for t in s.takes if t.state == "generated" and "versions" in t.settings]
                for t in gen:
                    used = t.settings["versions"].get(p["entity_id"])
                    if used is not None and used != e.current_version:
                        add(_key("rule", rid, s.id, t.id), s.id,
                            f"Take {t.number} uses {e.name} v{used}; the rule needs v{e.current_version}.",
                            rule.get("note") or "Rule: same character version", rid)
        elif rule["kind"] == "reference_take":
            tgt = next((x for x in meta.shots if x.id == p.get("target_shot")), None)
            src = next((x for x in meta.shots if x.id == p.get("source_shot")), None)
            if tgt and src and tgt.enabled and not any(t.state == "approved" for t in src.takes):
                add(_key("rule", rid, tgt.id), tgt.id,
                    f"Shot {tgt.number} needs an approved take from shot {src.number}, and there is none yet.",
                    rule.get("note") or "Rule: reference the approved take", rid)
        elif rule["kind"] == "world_time" and p.get("world_id") in ents:
            w = ents[p["world_id"]]
            if w.fields.get("time_of_day", "") != p.get("time_of_day", ""):
                used_in = [s for s in meta.shots if s.location_id == w.id and s.enabled]
                if used_in:
                    add(_key("rule", rid, w.id), used_in[0].id,
                        f"{w.name} is set to '{w.fields.get('time_of_day') or 'no time of day'}', the rule says '{p['time_of_day']}'.",
                        rule.get("note") or "Rule: keep the time of day", rid)

    acks = {a["key"]: a for a in meta.warning_acks}
    for w in out:
        a = acks.get(w["key"])
        w["accepted"] = a is not None
        w["accepted_reason"] = a["reason"] if a else None
    return out
