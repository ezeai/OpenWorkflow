"""Milestone 6 tests: continuity rules and warnings, typed graph validation, canvas run through the shared job path.

Generation is MOCKED (scripted ComfyUI). These tests check OPENFIELD's logic, not model output.
"""
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openfield.app import create_app
from openfield.generation.builders import build_graph
from openfield.generation.comfy import EngineError
from openfield.generation.jobs import JobEngine, JobStore, Resolved
from openfield.generation.prompt import compose
from openfield.projects.store import ProjectStore


class FakeRunner:
    def __init__(self, url):
        self.url = url

    def reachable(self, wait=0.0):
        return True

    def submit(self, graph):
        return "p-" + str(len(FakeRunner.submitted))

    def history(self, pid):
        FakeRunner.submitted.append(pid)
        return {"status": {"status_str": "success", "completed": True},
                "outputs": {"10": {"images": [{"filename": "o.png", "subfolder": "", "type": "output"}]}}}

    def queue(self):
        return {"queue_running": [], "queue_pending": []}

    def interrupt(self):
        pass

    def delete_pending(self, pid):
        pass

    def fetch(self, filename, subfolder, kind):
        import io
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (8, 8), (1, 2, 3)).save(buf, format="PNG")
        return buf.getvalue()


FakeRunner.submitted = []


def fake_args():
    return Resolved(graph_args={"unet": "u.safetensors", "text_encoder": "t.safetensors", "vae": "v.safetensors"}, models=[])


@pytest.fixture
def home(tmp_path):
    return tmp_path / "home"


@pytest.fixture
def client(home):
    app = create_app(home)
    engine = JobEngine(store=JobStore(home), projects_fn=lambda: ProjectStore(home / "projects"),
                       engine_url=lambda: "http://fake", resolver=lambda wid: fake_args(),
                       build_graph=build_graph, compose=compose, runner_factory=FakeRunner)
    engine.start()
    app.state.jobs = engine
    return TestClient(app)


def project(c, title="M6"):
    p = c.post("/api/projects", json={"title": title}).json()
    return p["id"], p["revision"]


def character(c, pid, rev, name="Mira", **fields):
    r = c.post(f"/api/projects/{pid}/entities", json={"base_revision": rev, "kind": "character", "name": name}).json()
    eid, rev = r["entity_id"], r["project"]["revision"]
    if fields:
        rev = c.patch(f"/api/projects/{pid}/entities/{eid}", json={"base_revision": 1, "fields": fields}).json()["revision"]
    return eid, rev


def world(c, pid, rev, name="Market", **fields):
    r = c.post(f"/api/projects/{pid}/entities", json={"base_revision": rev, "kind": "world", "name": name}).json()
    eid, rev = r["entity_id"], r["project"]["revision"]
    if fields:
        rev = c.patch(f"/api/projects/{pid}/entities/{eid}", json={"base_revision": 1, "fields": fields}).json()["revision"]
    return eid, rev


def shot_with(c, pid, rev, script="INT. ROOM - DAY\nMira sits.\n", fields=None):
    rev = c.put(f"/api/projects/{pid}/script", json={"base_revision": rev, "text": script}).json()["revision"]
    rev = c.post(f"/api/projects/{pid}/plan/apply", json={"base_revision": rev, "picks": [1]}).json()["revision"]
    sid = c.get(f"/api/projects/{pid}/director").json()["shots"][0]["id"]
    rev = c.patch(f"/api/projects/{pid}/shots/{sid}", json={"base_revision": rev,
                  "fields": {"workflow_id": "img-z-image-turbo", **(fields or {})}}).json()["revision"]
    return sid, rev


def wait_jobs(c, jids, limit=20):
    t0 = time.time()
    out = {}
    while time.time() - t0 < limit and len(out) < len(jids):
        for j in jids:
            if j not in out:
                s = c.get(f"/api/jobs/{j}").json()
                if s["state"] in ("COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED"):
                    out[j] = s
        time.sleep(0.05)
    return out


def warnings_of(c, pid):
    return c.get(f"/api/projects/{pid}/continuity").json()["warnings"]


# ---------------------------------------------------------------- continuity: built-in checks

def test_character_change_after_a_take_is_flagged_and_acceptance_keeps_it_visible(client):
    c = client
    pid, rev = project(c)
    eid, rev = character(c, pid, rev, description="short dark hair")
    sid, rev = shot_with(c, pid, rev, fields={"character_ids": [eid]})
    gen = c.post(f"/api/projects/{pid}/shots/{sid}/generate", json={"base_revision": rev, "seed": 1})
    assert gen.status_code == 202
    wait_jobs(c, [gen.json()["id"]])
    assert warnings_of(c, pid) == []  # nothing changed yet
    cur = c.get(f"/api/projects/{pid}").json()
    e_rev = next(x for x in cur["entities"] if x["id"] == eid)["revision"]
    c.patch(f"/api/projects/{pid}/entities/{eid}", json={"base_revision": e_rev, "fields": {"hair": "silver"}})
    w = warnings_of(c, pid)
    outdated = [x for x in w if x["key"].startswith("outdated_character")]
    assert len(outdated) == 1 and "changed since" in outdated[0]["message"] and outdated[0]["accepted"] is False
    rev = c.get(f"/api/projects/{pid}/continuity").json()["revision"]
    c.post(f"/api/projects/{pid}/continuity/accept", json={"base_revision": rev, "key": outdated[0]["key"],
           "reason": "keep the older look for this scene"})
    after = [x for x in warnings_of(c, pid) if x["key"] == outdated[0]["key"]][0]
    assert after["accepted"] is True and "older look" in after["accepted_reason"]


def test_acceptance_needs_a_real_reason(client):
    c = client
    pid, rev = project(c)
    r = c.post(f"/api/projects/{pid}/continuity/accept", json={"base_revision": rev, "key": "k", "reason": "x"})
    assert r.status_code == 422 or r.status_code == 400


def test_identity_lock_that_the_workflow_cannot_apply_is_flagged(client):
    c = client
    pid, rev = project(c)
    eid, rev = character(c, pid, rev, identity_lock="strong")
    shot_with(c, pid, rev, fields={"character_ids": [eid]})
    keys = [x["key"] for x in warnings_of(c, pid)]
    assert any(k.startswith("identity_unsupported") for k in keys)


def test_missing_reference_file_is_flagged(client, home):
    c = client
    pid, rev = project(c)
    eid, rev = character(c, pid, rev)
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (9, 9, 9)).save(buf, format="PNG")
    up = c.post(f"/api/projects/{pid}/assets?label=f.png", content=buf.getvalue()).json()["asset"]
    ent_rev = next(x for x in c.get(f"/api/projects/{pid}").json()["entities"] if x["id"] == eid)["revision"]
    r = c.patch(f"/api/projects/{pid}/entities/{eid}", json={"base_revision": ent_rev,
                "refs": [{"asset_id": up["id"], "role": "face"}]})
    assert r.status_code == 200, r.text
    (home / "projects" / pid / up["rel_path"]).unlink()
    assert any(x["key"].startswith("missing_reference") for x in warnings_of(c, pid))


# ---------------------------------------------------------------- continuity: user rules

def test_same_character_version_rule_flags_takes_on_old_versions(client):
    c = client
    pid, rev = project(c)
    eid, rev = character(c, pid, rev)
    sid, rev = shot_with(c, pid, rev, fields={"character_ids": [eid]})
    wait_jobs(c, [c.post(f"/api/projects/{pid}/shots/{sid}/generate", json={"base_revision": rev, "seed": 2}).json()["id"]])
    rev = c.get(f"/api/projects/{pid}/continuity").json()["revision"]
    r = c.post(f"/api/projects/{pid}/continuity/rules", json={"base_revision": rev, "kind": "same_character_version",
               "params": {"entity_id": eid}, "note": "one look per film"})
    assert r.status_code == 201, r.text
    cur = c.get(f"/api/projects/{pid}").json()
    e_rev = next(x for x in cur["entities"] if x["id"] == eid)["revision"]
    c.patch(f"/api/projects/{pid}/entities/{eid}", json={"base_revision": e_rev, "fields": {"hair": "red"}})
    assert any(x["key"].startswith("rule:") for x in warnings_of(c, pid))


def test_reference_take_rule_waits_for_an_approved_source_take(client, home):
    c = client
    pid, rev = project(c)
    sid1, rev = shot_with(c, pid, rev)
    sid2 = c.get(f"/api/projects/{pid}/director").json()["shots"][1]["id"] if len(c.get(f"/api/projects/{pid}/director").json()["shots"]) > 1 else None
    if sid2 is None:
        rev = c.post(f"/api/projects/{pid}/shots", json={"base_revision": rev}).json()["revision"]
        sid2 = c.get(f"/api/projects/{pid}/director").json()["shots"][-1]["id"]
    rev = c.post(f"/api/projects/{pid}/continuity/rules", json={"base_revision": rev, "kind": "reference_take",
                 "params": {"target_shot": sid2, "source_shot": sid1}}).json()["revision"]
    assert any(x["key"].startswith("rule:") for x in warnings_of(c, pid))
    # approve a take on the source shot (approval UI comes later; set directly)
    wait_jobs(c, [c.post(f"/api/projects/{pid}/shots/{sid1}/generate", json={"base_revision": rev, "seed": 3}).json()["id"]])
    take_id = c.get(f"/api/projects/{pid}/director").json()["shots"][0]["takes"][0]["id"]
    ProjectStore(home / "projects").mutate(pid, lambda m: [setattr(t, "state", "approved") for s in m.shots if s.id == sid1 for t in s.takes if t.id == take_id])
    assert not any(x["key"].startswith("rule:") for x in warnings_of(c, pid))


def test_world_time_rule(client):
    c = client
    pid, rev = project(c)
    wid, rev = world(c, pid, rev, time_of_day="day")
    sid, rev = shot_with(c, pid, rev, fields={"location_id": wid})
    rev = c.post(f"/api/projects/{pid}/continuity/rules", json={"base_revision": rev, "kind": "world_time",
                 "params": {"world_id": wid, "time_of_day": "night"}}).json()["revision"]
    assert any(x["key"].startswith("rule:") and "night" in x["message"] for x in warnings_of(c, pid))


def test_rule_needs_its_parameters_and_a_known_kind(client):
    c = client
    pid, rev = project(c)
    assert c.post(f"/api/projects/{pid}/continuity/rules", json={"base_revision": rev, "kind": "world_time",
                  "params": {}}).status_code == 400
    assert c.post(f"/api/projects/{pid}/continuity/rules", json={"base_revision": rev, "kind": "make_it_so",
                  "params": {}}).status_code == 400


def test_rule_can_be_removed(client):
    c = client
    pid, rev = project(c)
    wid, rev = world(c, pid, rev, time_of_day="day")
    rev = c.post(f"/api/projects/{pid}/continuity/rules", json={"base_revision": rev, "kind": "world_time",
                 "params": {"world_id": wid, "time_of_day": "day"}}).json()["revision"]
    rid = c.get(f"/api/projects/{pid}/continuity").json()["rules"][0]["id"]
    r = c.post(f"/api/projects/{pid}/continuity/rules/{rid}/remove", json={"base_revision": rev})
    assert r.status_code == 200 and r.json()["rules"] == []


# ---------------------------------------------------------------- graph validation

def test_template_needs_a_shot_before_it_is_valid(client):
    c = client
    pid, rev = project(c)
    shot_with(c, pid, rev)
    rev = c.get(f"/api/projects/{pid}").json()["revision"]
    g = c.post(f"/api/projects/{pid}/graphs", json={"base_revision": rev, "name": "G", "template": "shot_to_image"}).json()
    assert g["validation"]["ok"] is False and any(e["code"] == "shot_missing" for e in g["validation"]["errors"])


def test_type_mismatch_and_bad_ports_are_rejected(client):
    c = client
    pid, rev = project(c)
    eid, rev = character(c, pid, rev)
    sid, rev = shot_with(c, pid, rev)
    g = c.post(f"/api/projects/{pid}/graphs", json={"base_revision": rev, "name": "G"}).json()
    rev = c.get(f"/api/projects/{pid}").json()["revision"]
    nodes = [{"id": "a" * 32, "type": "character", "params": {"entity_id": eid}},
             {"id": "b" * 32, "type": "zimage", "params": {}}]
    edges = [{"id": "c" * 32, "src": "a" * 32, "src_port": "character", "dst": "b" * 32, "dst_port": "shot"}]
    v = c.post(f"/api/projects/{pid}/graphs/{g['id']}/validate").json()
    rev = c.put(f"/api/projects/{pid}/graphs/{g['id']}", json={"base_revision": rev, "nodes": nodes, "edges": edges}).json()
    codes = {e["code"] for e in rev["validation"]["errors"]}
    assert "type_mismatch" in codes and "input_missing" in codes


def test_cycle_and_double_input_are_rejected(client):
    c = client
    pid, rev = project(c)
    sid, rev = shot_with(c, pid, rev)
    sid_id = c.get(f"/api/projects/{pid}/director").json()["shots"][0]["id"]
    g = c.post(f"/api/projects/{pid}/graphs", json={"base_revision": rev, "name": "G"}).json()
    rev = c.get(f"/api/projects/{pid}").json()["revision"]
    nodes = [{"id": "a" * 32, "type": "zimage", "params": {}}, {"id": "b" * 32, "type": "zimage", "params": {}},
             {"id": "d" * 32, "type": "shot", "params": {"shot_id": sid_id}}]
    edges = [{"id": "1" * 32, "src": "a" * 32, "src_port": "asset", "dst": "b" * 32, "dst_port": "shot"},
             {"id": "2" * 32, "src": "b" * 32, "src_port": "asset", "dst": "a" * 32, "dst_port": "shot"},
             {"id": "3" * 32, "src": "d" * 32, "src_port": "shot", "dst": "a" * 32, "dst_port": "shot"},
             {"id": "4" * 32, "src": "d" * 32, "src_port": "shot", "dst": "a" * 32, "dst_port": "shot"}]
    saved = c.put(f"/api/projects/{pid}/graphs/{g['id']}", json={"base_revision": rev, "nodes": nodes, "edges": edges}).json()
    codes = {e["code"] for e in saved["validation"]["errors"]}
    assert "cycle" in codes and "multiple_inputs" in codes


def test_unknown_node_type_is_reported_not_crashing(client):
    c = client
    pid, rev = project(c)
    g = c.post(f"/api/projects/{pid}/graphs", json={"base_revision": rev, "name": "G"}).json()
    rev = c.get(f"/api/projects/{pid}").json()["revision"]
    saved = c.put(f"/api/projects/{pid}/graphs/{g['id']}", json={"base_revision": rev,
                  "nodes": [{"id": "a" * 32, "type": "teleporter", "params": {}}], "edges": []}).json()
    assert any(e["code"] == "unknown_type" for e in saved["validation"]["errors"])


def test_malformed_graph_is_refused_on_save(client):
    c = client
    pid, rev = project(c)
    g = c.post(f"/api/projects/{pid}/graphs", json={"base_revision": rev, "name": "G"}).json()
    rev = c.get(f"/api/projects/{pid}").json()["revision"]
    r = c.put(f"/api/projects/{pid}/graphs/{g['id']}", json={"base_revision": rev,
              "nodes": [{"id": "not-an-id", "type": "shot", "params": {}}], "edges": []})
    assert r.status_code == 400


def test_run_refused_for_an_invalid_graph(client):
    c = client
    pid, rev = project(c)
    g = c.post(f"/api/projects/{pid}/graphs", json={"base_revision": rev, "name": "G", "template": "shot_to_image"}).json()
    rev = c.get(f"/api/projects/{pid}").json()["revision"]
    r = c.post(f"/api/projects/{pid}/graphs/{g['id']}/run", json={"base_revision": rev})
    assert r.status_code == 400


def test_canvas_run_uses_the_same_job_path_as_the_director(client, home):
    c = client
    pid, rev = project(c)
    eid, rev = character(c, pid, rev, description="short dark hair")
    sid, rev = shot_with(c, pid, rev, fields={"character_ids": [eid]})
    sid_id = sid
    g = c.post(f"/api/projects/{pid}/graphs", json={"base_revision": rev, "name": "Canvas", "template": "shot_to_image"}).json()
    gid = g["id"]
    shot_node = next(n for n in g["nodes"] if n["type"] == "shot")
    shot_node["params"] = {"shot_id": sid_id}
    rev = c.get(f"/api/projects/{pid}").json()["revision"]
    saved = c.put(f"/api/projects/{pid}/graphs/{gid}", json={"base_revision": rev, "nodes": g["nodes"], "edges": g["edges"]}).json()
    assert saved["validation"]["ok"], saved["validation"]
    rev = c.get(f"/api/projects/{pid}").json()["revision"]
    run = c.post(f"/api/projects/{pid}/graphs/{gid}/run", json={"base_revision": rev, "seed": 11})
    assert run.status_code == 202, run.text
    job_canvas = wait_jobs(c, run.json()["jobs"])[run.json()["jobs"][0]]
    rev = c.get(f"/api/projects/{pid}").json()["revision"]
    director_job = c.post(f"/api/projects/{pid}/shots/{sid_id}/generate", json={"base_revision": rev, "seed": 11}).json()
    job_director = wait_jobs(c, [director_job["id"]])[director_job["id"]]
    assert job_canvas["state"] == job_director["state"] == "COMPLETED"
    assert set(job_canvas.keys()) == set(job_director.keys())
    assert job_canvas["workflow_id"] == job_director["workflow_id"] == "img-z-image-turbo"
    assert job_canvas["params"]["seed"] == job_director["params"]["seed"] == 11
    assert job_canvas["prompt"] == job_director["prompt"]


def test_schema_v2_project_migrates_to_v3_with_empty_graphs(client, home):
    c = client
    pid, rev = project(c)
    path = home / "projects" / pid / "project.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["schema_version"] = 2
    for k in ("continuity_rules", "warning_acks", "graphs"):
        raw.pop(k, None)
    path.write_text(json.dumps(raw))
    (home / "projects" / pid / "project.json.bak").unlink(missing_ok=True)
    got = c.get(f"/api/projects/{pid}/graphs").json()
    assert got["graphs"] == []
    rev = got["revision"]
    assert c.post(f"/api/projects/{pid}/graphs", json={"base_revision": rev, "name": "after"}).status_code == 201
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 3
