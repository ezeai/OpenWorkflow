"""Milestone 4 tests: script parsing, plan proposals, shot editing, takes, capability mapping, coverage.

Real storage on a temporary disk. No generation runs here: takes are asserted to remain 'requested'.
"""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openfield.app import create_app
from openfield.projects import director as d
from openfield.projects import schema as sc

SCRIPT = """INT. APARTMENT - NIGHT
Mira waits by the window.
MIRA: Is anyone there?
JONAS: Only the rain.
EXT. MARKET - NIGHT
Mira runs through the rain.
"""


@pytest.fixture
def home(tmp_path):
    return tmp_path / "home"


@pytest.fixture
def client(home):
    return TestClient(create_app(home))


def new_client(home):
    return TestClient(create_app(home))


def project_with_cast(c, script=SCRIPT):
    p = c.post("/api/projects", json={"title": "Director test"}).json()
    pid, rev = p["id"], p["revision"]
    ids = {}
    for kind, name in (("character", "Mira"), ("character", "Jonas"), ("world", "Apartment"), ("world", "Market"),
                       ("prop", "Lantern")):
        r = c.post(f"/api/projects/{pid}/entities", json={"base_revision": rev, "kind": kind, "name": name}).json()
        rev = r["project"]["revision"]
        ids[name] = r["entity_id"]
    r = c.put(f"/api/projects/{pid}/script", json={"base_revision": rev, "text": script})
    assert r.status_code == 200, r.text
    return pid, r.json()["revision"], ids


def shots_of(c, pid):
    return c.get(f"/api/projects/{pid}/director").json()["shots"]


# ---------------------------------------------------------------- parsing and proposals

def test_parser_splits_scenes_and_recognises_dialogue():
    scenes = d.parse_script(SCRIPT)
    assert [s["title"] for s in scenes] == ["INT. APARTMENT - NIGHT", "EXT. MARKET - NIGHT"]
    kinds = [b["kind"] for b in scenes[0]["beats"]]
    assert kinds == ["action", "dialogue", "dialogue"]
    assert scenes[0]["beats"][1]["speaker"] == "MIRA"


def test_script_without_headings_is_one_untitled_scene():
    scenes = d.parse_script("Mira sits down.\nShe waits.")
    assert len(scenes) == 1 and scenes[0]["title"] == "Untitled scene"


def test_scene_ids_are_stable_for_the_same_script():
    assert [s["id"] for s in d.parse_script(SCRIPT)] == [s["id"] for s in d.parse_script(SCRIPT)]


def test_editing_a_heading_changes_its_scene_id():
    before = d.parse_script(SCRIPT)[1]["id"]
    after = d.parse_script(SCRIPT.replace("EXT. MARKET", "EXT. HARBOUR"))[1]["id"]
    assert before != after


def test_proposal_matches_characters_and_locations_and_explains_rules(client):
    pid, rev, ids = project_with_cast(client)
    plan = client.post(f"/api/projects/{pid}/plan").json()
    props = plan["proposals"]
    assert all(p["why"].startswith("rule:") or "rule:" in p["why"] for p in props)
    speaker = next(p for p in props if p["shot_size"] == "close_up")
    assert speaker["character_ids"] == [ids["Mira"]]
    market = [p for p in props if p["scene_title"] == "EXT. MARKET - NIGHT"]
    assert market[0]["location_id"] == ids["Market"]
    run = next(p for p in market if "runs" in p["texts"]["beat"])
    assert run["camera_move"] == "tracking" and "running" in run["why"]


def test_planning_writes_nothing(client):
    pid, rev, _ = project_with_cast(client)
    client.post(f"/api/projects/{pid}/plan")
    assert client.get(f"/api/projects/{pid}/director").json()["revision"] == rev
    assert shots_of(client, pid) == []


def test_apply_creates_only_selected_shots_and_keeps_existing(client):
    pid, rev, _ = project_with_cast(client)
    r = client.post(f"/api/projects/{pid}/plan/apply", json={"base_revision": rev, "picks": [0]})
    assert r.status_code == 200 and len(r.json()["created"]) == 1
    rev2 = r.json()["revision"]
    existing = shots_of(client, pid)[0]["id"]
    r2 = client.post(f"/api/projects/{pid}/plan/apply", json={"base_revision": rev2, "picks": [2]})
    assert r2.status_code == 200
    shots = shots_of(client, pid)
    assert len(shots) == 2 and shots[0]["id"] == existing  # earlier shot untouched
    assert [s["number"] for s in shots] == [1, 2]


def test_apply_rejects_bad_indices_and_empty_selection(client):
    pid, rev, _ = project_with_cast(client)
    assert client.post(f"/api/projects/{pid}/plan/apply", json={"base_revision": rev, "picks": [999]}).status_code == 400
    assert client.post(f"/api/projects/{pid}/plan/apply", json={"base_revision": rev, "picks": []}).status_code == 400


def test_stale_revision_refused_on_apply(client):
    pid, rev, _ = project_with_cast(client)
    client.post(f"/api/projects/{pid}/plan/apply", json={"base_revision": rev, "picks": [0]})
    r = client.post(f"/api/projects/{pid}/plan/apply", json={"base_revision": rev, "picks": [1]})
    assert r.status_code == 409 and len(shots_of(client, pid)) == 1


# ---------------------------------------------------------------- shot editing

def test_add_patch_and_enum_validation(client):
    pid, rev, _ = project_with_cast(client)
    added = client.post(f"/api/projects/{pid}/shots", json={"base_revision": rev}).json()
    sid = added["shot_id"]
    rev = added["revision"]
    ok = client.patch(f"/api/projects/{pid}/shots/{sid}", json={"base_revision": rev,
                      "fields": {"shot_size": "close_up", "duration_s": 6.5, "lens": "portrait"}})
    assert ok.status_code == 200
    shot = next(s for s in ok.json()["shots"] if s["id"] == sid)
    assert shot["shot_size"] == "close_up" and shot["duration_s"] == 6.5
    rev = ok.json()["revision"]
    bad_enum = client.patch(f"/api/projects/{pid}/shots/{sid}", json={"base_revision": rev, "fields": {"lens": "bazooka"}})
    assert bad_enum.status_code == 400
    bad_dur = client.patch(f"/api/projects/{pid}/shots/{sid}", json={"base_revision": rev, "fields": {"duration_s": 99}})
    assert bad_dur.status_code == 400
    unknown = client.patch(f"/api/projects/{pid}/shots/{sid}", json={"base_revision": rev, "fields": {"hack": 1}})
    assert unknown.status_code == 400


def test_stale_shot_edit_refused(client):
    pid, rev, _ = project_with_cast(client)
    sid = client.post(f"/api/projects/{pid}/shots", json={"base_revision": rev}).json()["shot_id"]
    r = client.patch(f"/api/projects/{pid}/shots/{sid}", json={"base_revision": rev, "fields": {"lens": "telephoto"}})
    assert r.status_code == 409


def test_preset_sets_structured_fields_and_keeps_user_text(client):
    pid, rev, _ = project_with_cast(client)
    added = client.post(f"/api/projects/{pid}/shots", json={"base_revision": rev}).json()
    sid = added["shot_id"]
    rev = client.patch(f"/api/projects/{pid}/shots/{sid}", json={"base_revision": added["revision"],
                       "fields": {"texts": {"beat": "her choice matters"}}}).json()["revision"]
    r = client.patch(f"/api/projects/{pid}/shots/{sid}", json={"base_revision": rev, "preset": "dialogue_close_up"})
    shot = next(s for s in r.json()["shots"] if s["id"] == sid)
    assert shot["preset"] == "dialogue_close_up" and shot["shot_size"] == "close_up" and shot["lens"] == "portrait"
    assert shot["texts"]["beat"] == "her choice matters"  # not overwritten


def test_every_preset_value_is_a_valid_enum():
    for name, p in d.PRESETS.items():
        assert p["shot_size"] in sc.SHOT_SIZES and p["camera_move"] in sc.CAMERA_MOVES, name
        assert p["camera_angle"] in sc.CAMERA_ANGLES and p["lens"] in sc.LENSES, name
        assert p["depth"] in sc.DEPTHS and p["composition"] in sc.COMPOSITIONS, name


def test_duplicate_gets_new_id_and_no_takes(client):
    pid, rev, _ = project_with_cast(client)
    sid = client.post(f"/api/projects/{pid}/shots", json={"base_revision": rev}).json()["shot_id"]
    rev = client.get(f"/api/projects/{pid}/director").json()["revision"]
    client.post(f"/api/projects/{pid}/shots/{sid}/takes", json={"base_revision": rev, "note": "first"})
    rev = client.get(f"/api/projects/{pid}/director").json()["revision"]
    dup = client.post(f"/api/projects/{pid}/shots/{sid}/duplicate", json={"base_revision": rev}).json()
    shots = dup["shots"]
    assert len(shots) == 2 and shots[1]["id"] != shots[0]["id"]
    assert shots[1]["takes"] == [] and len(shots[0]["takes"]) == 1


def test_reorder_requires_every_shot_exactly_once(client):
    pid, rev, _ = project_with_cast(client)
    a = client.post(f"/api/projects/{pid}/shots", json={"base_revision": rev}).json()
    b = client.post(f"/api/projects/{pid}/shots", json={"base_revision": a["revision"]}).json()
    rev = b["revision"]
    ida, idb = a["shot_id"], b["shot_id"]
    assert client.put(f"/api/projects/{pid}/shots/order", json={"base_revision": rev, "order": [ida]}).status_code == 400
    assert client.put(f"/api/projects/{pid}/shots/order", json={"base_revision": rev, "order": [ida, ida]}).status_code == 400
    ok = client.put(f"/api/projects/{pid}/shots/order", json={"base_revision": rev, "order": [idb, ida]})
    assert ok.status_code == 200
    assert [s["id"] for s in ok.json()["shots"]] == [idb, ida]
    assert [s["number"] for s in ok.json()["shots"]] == [1, 2]


def test_shot_with_takes_cannot_be_removed_only_disabled(client):
    pid, rev, _ = project_with_cast(client)
    sid = client.post(f"/api/projects/{pid}/shots", json={"base_revision": rev}).json()["shot_id"]
    rev = client.get(f"/api/projects/{pid}/director").json()["revision"]
    client.post(f"/api/projects/{pid}/shots/{sid}/takes", json={"base_revision": rev})
    rev = client.get(f"/api/projects/{pid}/director").json()["revision"]
    assert client.post(f"/api/projects/{pid}/shots/{sid}/remove", json={"base_revision": rev}).status_code == 400
    dis = client.patch(f"/api/projects/{pid}/shots/{sid}", json={"base_revision": rev, "fields": {"enabled": False}})
    assert dis.status_code == 200 and dis.json()["shots"][0]["enabled"] is False


def test_empty_shot_can_be_removed(client):
    pid, rev, _ = project_with_cast(client)
    sid = client.post(f"/api/projects/{pid}/shots", json={"base_revision": rev}).json()["shot_id"]
    rev = client.get(f"/api/projects/{pid}/director").json()["revision"]
    r = client.post(f"/api/projects/{pid}/shots/{sid}/remove", json={"base_revision": rev})
    assert r.status_code == 200 and shots_of(client, pid) == []


# ---------------------------------------------------------------- takes and capabilities

def test_take_request_is_recorded_not_generated_and_history_is_kept(client):
    pid, rev, _ = project_with_cast(client)
    sid = client.post(f"/api/projects/{pid}/shots", json={"base_revision": rev}).json()["shot_id"]
    rev = client.get(f"/api/projects/{pid}/director").json()["revision"]
    t1 = client.post(f"/api/projects/{pid}/shots/{sid}/takes", json={"base_revision": rev, "note": "a"}).json()
    rev = t1["revision"]
    t2 = client.post(f"/api/projects/{pid}/shots/{sid}/takes", json={"base_revision": rev, "note": "b"}).json()
    takes = next(s for s in t2["shots"] if s["id"] == sid)["takes"]
    assert [t["number"] for t in takes] == [1, 2]
    assert all(t["state"] == "requested" and t["asset_id"] is None for t in takes)  # no generation happened
    assert "capabilities" in takes[0]["settings"]


def test_disabled_shot_refuses_a_take(client):
    pid, rev, _ = project_with_cast(client)
    sid = client.post(f"/api/projects/{pid}/shots", json={"base_revision": rev}).json()["shot_id"]
    rev = client.get(f"/api/projects/{pid}/director").json()["revision"]
    rev = client.patch(f"/api/projects/{pid}/shots/{sid}", json={"base_revision": rev, "fields": {"enabled": False}}).json()["revision"]
    assert client.post(f"/api/projects/{pid}/shots/{sid}/takes", json={"base_revision": rev}).status_code == 400


def test_capability_map_never_claims_camera_moves_are_executed(client):
    pid, rev, _ = project_with_cast(client)
    sid = client.post(f"/api/projects/{pid}/shots", json={"base_revision": rev}).json()["shot_id"]
    rev = client.get(f"/api/projects/{pid}/director").json()["revision"]
    rev = client.patch(f"/api/projects/{pid}/shots/{sid}", json={"base_revision": rev,
                       "fields": {"workflow_id": "img-z-image-turbo", "camera_move": "dolly"}}).json()["revision"]
    shot = shots_of(client, pid)[0]
    caps = shot["capabilities"]
    assert caps["camera_move"] == "unsupported"  # declared unsupported by the workflow
    assert "honoured" not in caps.values()
    assert any(w["code"] == "move_not_executed" for w in shot["warnings"])


def test_no_workflow_means_not_mapped(client):
    pid, rev, _ = project_with_cast(client)
    client.post(f"/api/projects/{pid}/shots", json={"base_revision": rev})
    caps = shots_of(client, pid)[0]["capabilities"]
    assert all(v == "not_mapped" for k, v in caps.items() if not k.startswith("_"))


def test_unknown_workflow_rejected(client):
    pid, rev, _ = project_with_cast(client)
    sid = client.post(f"/api/projects/{pid}/shots", json={"base_revision": rev}).json()["shot_id"]
    rev = client.get(f"/api/projects/{pid}/director").json()["revision"]
    r = client.patch(f"/api/projects/{pid}/shots/{sid}", json={"base_revision": rev, "fields": {"workflow_id": "made-up"}})
    assert r.status_code == 400


# ---------------------------------------------------------------- warnings and coverage

def test_archived_character_becomes_a_shot_warning(client):
    pid, rev, ids = project_with_cast(client)
    client.post(f"/api/projects/{pid}/plan/apply", json={"base_revision": rev, "picks": [2]})
    p = client.get(f"/api/projects/{pid}").json()
    rev = p["revision"]
    client.patch(f"/api/projects/{pid}/entities/{ids['Mira']}", json={"base_revision": next(
        e for e in p["entities"] if e["id"] == ids["Mira"])["revision"], "archived": True})
    shot = shots_of(client, pid)[0]
    assert any(w["code"] == "character_archived" for w in shot["warnings"])


def test_scene_removed_from_script_is_flagged(client):
    pid, rev, _ = project_with_cast(client)
    client.post(f"/api/projects/{pid}/plan/apply", json={"base_revision": rev, "picks": [0]})
    rev = client.get(f"/api/projects/{pid}/director").json()["revision"]
    client.put(f"/api/projects/{pid}/script", json={"base_revision": rev, "text": "INT. OFFICE - DAY\nA desk."})
    assert any(w["code"] == "scene_missing" for w in shots_of(client, pid)[0]["warnings"])


def test_coverage_rules_fire_and_dismissal_persists(client, home):
    pid, rev, _ = project_with_cast(client)
    keys = {s["key"] for s in client.get(f"/api/projects/{pid}/director").json()["suggestions"]}
    assert any(k.startswith("uncovered:") for k in keys)  # Mira and Jonas appear in the script with no shot
    assert any(k.startswith("location-jump") for k in keys)
    target = next(k for k in keys if k.startswith("location-jump"))
    rev = client.get(f"/api/projects/{pid}/director").json()["revision"]
    r = client.post(f"/api/projects/{pid}/suggestions/{target}/dismiss", json={"base_revision": rev})
    assert r.status_code == 200
    assert target not in {s["key"] for s in r.json()["suggestions"]}
    assert target not in {s["key"] for s in new_client(home).get(f"/api/projects/{pid}/director").json()["suggestions"]}


def test_repeated_framing_rule(client):
    script = "INT. ROOM - DAY\n" + "\n".join(f"Mira looks at the door number {i}." for i in range(5))
    pid, rev, _ = project_with_cast(client, script=script)
    client.post(f"/api/projects/{pid}/plan/apply", json={"base_revision": rev, "picks": [1, 2, 3, 4, 5]})
    keys = {s["key"] for s in client.get(f"/api/projects/{pid}/director").json()["suggestions"]}
    assert any(k.startswith("repeat-framing") for k in keys)


# ---------------------------------------------------------------- persistence and migration

def test_shots_and_takes_survive_restart(client, home):
    pid, rev, _ = project_with_cast(client)
    client.post(f"/api/projects/{pid}/plan/apply", json={"base_revision": rev, "picks": [0, 1]})
    rev = client.get(f"/api/projects/{pid}/director").json()["revision"]
    sid = shots_of(client, pid)[0]["id"]
    client.post(f"/api/projects/{pid}/shots/{sid}/takes", json={"base_revision": rev, "note": "kept"})
    before = shots_of(client, pid)
    after = shots_of(new_client(home), pid)
    assert [s["id"] for s in after] == [s["id"] for s in before]
    assert after[0]["takes"][0]["note"] == "kept"


def test_v1_project_is_migrated_to_v2_without_loss(client, home):
    p = client.post("/api/projects", json={"title": "Old project"}).json()
    pid = p["id"]
    path = home / "projects" / pid / "project.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["schema_version"] = 1
    for k in ("script_text", "shots", "dismissed_suggestions"):
        raw.pop(k, None)
    path.write_text(json.dumps(raw))
    (home / "projects" / pid / "project.json.bak").unlink(missing_ok=True)
    got = new_client(home).get(f"/api/projects/{pid}")
    assert got.status_code == 200 and got.json()["title"] == "Old project"
    rev = got.json()["revision"]
    client.close()
    c2 = new_client(home)
    assert c2.put(f"/api/projects/{pid}/script", json={"base_revision": rev, "text": SCRIPT}).status_code == 200
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["schema_version"] == 2 and saved["script_text"] == SCRIPT


def test_director_endpoint_reports_recovery_note_for_damaged_manifest(client, home):
    pid, rev, _ = project_with_cast(client)
    (home / "projects" / pid / "project.json").write_text("{damaged")
    body = new_client(home).get(f"/api/projects/{pid}/director").json()
    assert body["recovery_note"] and "restored" in body["recovery_note"]
    # the backup is the state before the most recent save, which was before the script was written
    assert body["script_text"] == ""



def test_movement_keywords_match_whole_words_only():
    from openfield.projects.schema import ProjectMeta
    script = "INT. ROOM - DAY\nMira waits by the window.\nJonas paces the place.\nThe train runs late.\n"
    meta = ProjectMeta(id="0" * 32, title="t", created_at="x", updated_at="x", script_text=script)
    props = d.propose_plan(meta)["proposals"]
    by_beat = {p["texts"]["beat"]: p for p in props if p["texts"].get("purpose") == "Action"}
    assert by_beat["Mira waits by the window."]["camera_move"] == "static"   # 'window' is not 'wind'
    assert by_beat["Jonas paces the place."]["camera_move"] == "tracking"    # 'paces' is a walking word
    assert by_beat["The train runs late."]["camera_move"] == "tracking"      # 'runs' is a running word; 'train' is not 'rain'
