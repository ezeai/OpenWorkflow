"""Milestone 5 tests: job state machine, cancellation, failure, recovery, retry, provenance.

MOCKED ComfyUI: FakeRunner scripts the server's answers. These tests prove OPENFIELD's own
logic. They do not prove that any real model runs. The real run is recorded separately
in docs/evidence/milestone5 and in docs/IMPLEMENTATION_PLAN.md.
"""
import io
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from openfield.app import create_app
from openfield.generation import jobs as J
from openfield.generation.builders import build_graph
from openfield.generation.comfy import EngineError
from openfield.generation.jobs import JobEngine, JobStore
from openfield.generation.prompt import compose
from openfield.generation.resolve import comfy_name
from openfield.generation.zimage import build as build_zimage
from openfield.projects.store import ProjectStore


def png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), (90, 30, 30)).save(buf, format="PNG")
    return buf.getvalue()


class FakeRunner:
    """Scripted ComfyUI. script: 'success' | 'error' | 'hang' | 'unreachable' | 'no_output'."""
    shared = {"script": "success", "interrupted": False, "submitted": []}

    def __init__(self, url):
        self.url = url

    def reachable(self, wait=0.0):
        return self.shared["script"] != "unreachable"

    def submit(self, graph):
        if self.shared["script"] == "unreachable":
            raise EngineError("could not submit")
        self.shared["submitted"].append(graph)
        return "fake-prompt-1"

    def history(self, pid):
        s = self.shared["script"]
        if s == "hang" and not self.shared["interrupted"]:
            return None
        if s == "hang" and self.shared["interrupted"]:
            return {"status": {"status_str": "error", "completed": False,
                               "messages": [["execution_interrupted", {}]]}, "outputs": {}}
        if s == "error":
            return {"status": {"status_str": "error", "completed": False, "messages": [["execution_error", {}]]}, "outputs": {}}
        if s == "no_output":
            return {"status": {"status_str": "success", "completed": True}, "outputs": {}}
        return {"status": {"status_str": "success", "completed": True},
                "outputs": {"10": {"images": [{"filename": "out_00001_.png", "subfolder": "", "type": "output"}]}}}

    def queue(self):
        return {"queue_running": [], "queue_pending": []}

    def interrupt(self):
        self.shared["interrupted"] = True

    def delete_pending(self, pid):
        self.shared["interrupted"] = True

    def fetch(self, filename, subfolder, kind):
        return png()


def wait_terminal(client, jid, limit=20.0):
    t0 = time.time()
    while time.time() - t0 < limit:
        j = client.get(f"/api/jobs/{jid}").json()
        if j["state"] in ("COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED", "RECOVERY_REQUIRED"):
            return j
        time.sleep(0.05)
    raise AssertionError(f"job stuck in {j['state']}")


def resolved_args():
    from openfield.generation.jobs import Resolved
    return Resolved(graph_args={"unet": "z-Image-Turbo\\z_image_turbo_bf16.safetensors",
                                "text_encoder": "qwe3 vl gguf\\qwen_3_4b.safetensors", "vae": "ae.safetensors"},
                    models=[{"role": "diffusion", "name": "z_image_turbo_bf16.safetensors", "identity_confirmed": False}])


@pytest.fixture
def setup(tmp_path):
    FakeRunner.shared.update({"script": "success", "interrupted": False, "submitted": []})
    home = tmp_path / "home"
    app = create_app(home)
    store = ProjectStore(home / "projects")
    engine = JobEngine(store=JobStore(home), projects_fn=lambda: ProjectStore(home / "projects"),
                       engine_url=lambda: "http://fake", resolver=lambda wid: resolved_args(),
                       build_graph=build_graph, compose=compose, runner_factory=FakeRunner)
    engine.start()
    app.state.jobs = engine
    client = TestClient(app)
    p = client.post("/api/projects", json={"title": "Gen"}).json()
    pid, rev = p["id"], p["revision"]
    rev = client.put(f"/api/projects/{pid}/script", json={"base_revision": rev, "text": "EXT. STREET - NIGHT\nMira walks.\n"}).json()["revision"]
    rev = client.post(f"/api/projects/{pid}/plan/apply", json={"base_revision": rev, "picks": [1]}).json()["revision"]
    sid = client.get(f"/api/projects/{pid}/director").json()["shots"][0]["id"]
    rev = client.patch(f"/api/projects/{pid}/shots/{sid}", json={"base_revision": rev,
                       "fields": {"workflow_id": "img-z-image-turbo"}}).json()["revision"]
    return client, home, pid, sid, rev, engine


def generate(client, pid, sid, rev, seed=7):
    r = client.post(f"/api/projects/{pid}/shots/{sid}/generate", json={"base_revision": rev, "seed": seed})
    assert r.status_code == 202, r.text
    return r.json()


def takes_of(client, pid, sid):
    return next(s for s in client.get(f"/api/projects/{pid}/director").json()["shots"] if s["id"] == sid)["takes"]


# ---------------------------------------------------------------- success path and provenance

def test_successful_generation_registers_asset_with_provenance(setup):
    client, home, pid, sid, rev, _ = setup
    job = generate(client, pid, sid, rev)
    j = wait_terminal(client, job["id"])
    assert j["state"] == "COMPLETED", j
    assert [h["state"] for h in j["history"]] == ["QUEUED", "VALIDATING", "RUNNING", "POSTPROCESSING", "COMPLETED"]
    proj = client.get(f"/api/projects/{pid}").json()
    a = next(x for x in proj["assets"] if x["id"] == j["output_asset_id"])
    assert a["source"] == "generated" and a["status"] == "ok"
    prov = a["provenance"]
    assert prov["job_id"] == job["id"] and prov["workflow_id"] == "img-z-image-turbo"
    assert prov["parameters"]["seed"] == 7 and prov["models"][0]["identity_confirmed"] is False
    take = takes_of(client, pid, sid)[0]
    assert take["state"] == "generated" and take["asset_id"] == a["id"]


def test_generated_graph_uses_the_resolved_names_and_the_seed(setup):
    client, home, pid, sid, rev, _ = setup
    wait_terminal(client, generate(client, pid, sid, rev, seed=99)["id"])
    g = FakeRunner.shared["submitted"][-1]
    assert g["1"]["inputs"]["unet_name"] == "z-Image-Turbo\\z_image_turbo_bf16.safetensors"
    assert g["8"]["inputs"]["seed"] == 99 and g["8"]["inputs"]["steps"] == 8


# ---------------------------------------------------------------- failure modes

def test_engine_unreachable_fails_before_submission(setup):
    client, home, pid, sid, rev, _ = setup
    FakeRunner.shared["script"] = "unreachable"
    j = wait_terminal(client, generate(client, pid, sid, rev)["id"])
    assert j["state"] == "FAILED" and "not running" in j["error"]
    assert FakeRunner.shared["submitted"] == []
    assert takes_of(client, pid, sid)[0]["state"] == "failed"


def test_comfyui_error_fails_the_job_and_no_asset_appears(setup):
    client, home, pid, sid, rev, _ = setup
    FakeRunner.shared["script"] = "error"
    before = len(client.get(f"/api/projects/{pid}").json()["assets"])
    j = wait_terminal(client, generate(client, pid, sid, rev)["id"])
    assert j["state"] == "FAILED"
    assert len(client.get(f"/api/projects/{pid}").json()["assets"]) == before


def test_success_without_image_is_a_failure_not_a_completion(setup):
    client, home, pid, sid, rev, _ = setup
    FakeRunner.shared["script"] = "no_output"
    j = wait_terminal(client, generate(client, pid, sid, rev)["id"])
    assert j["state"] == "FAILED" and "no image" in j["error"]


def test_missing_model_files_block_the_job(setup):
    client, home, pid, sid, rev, engine = setup
    engine.resolver = lambda wid: (_ for _ in ()).throw(ValueError("missing model file(s) for diffusion. See the Model Library."))
    j = wait_terminal(client, generate(client, pid, sid, rev)["id"])
    assert j["state"] == "FAILED" and "missing model" in j["error"]
    assert FakeRunner.shared["submitted"] == []


# ---------------------------------------------------------------- cancellation

def test_cancel_while_running_ends_cancelled_with_no_asset(setup):
    client, home, pid, sid, rev, _ = setup
    FakeRunner.shared["script"] = "hang"
    job = generate(client, pid, sid, rev)
    for _ in range(100):
        if client.get(f"/api/jobs/{job['id']}").json()["state"] == "RUNNING":
            break
        time.sleep(0.05)
    assert client.post(f"/api/jobs/{job['id']}/cancel").status_code == 200
    j = wait_terminal(client, job["id"])
    assert j["state"] == "CANCELLED"
    assert takes_of(client, pid, sid)[0]["state"] == "cancelled"
    assert j["output_asset_id"] is None


def test_cancel_of_finished_job_is_refused(setup):
    client, home, pid, sid, rev, _ = setup
    j = wait_terminal(client, generate(client, pid, sid, rev)["id"])
    assert client.post(f"/api/jobs/{j['id']}/cancel").status_code == 409


# ---------------------------------------------------------------- guards and recovery

def test_generate_refused_for_workflow_without_a_generator(setup):
    client, home, pid, sid, rev, _ = setup
    rev = client.patch(f"/api/projects/{pid}/shots/{sid}", json={"base_revision": rev,
                       "fields": {"workflow_id": "img-qwen-image-2.1"}}).json()["revision"]
    r = client.post(f"/api/projects/{pid}/shots/{sid}/generate", json={"base_revision": rev})
    assert r.status_code == 400 and "no generator" in r.json()["detail"]


def test_generate_refused_on_stale_revision(setup):
    client, home, pid, sid, rev, _ = setup
    r = client.post(f"/api/projects/{pid}/shots/{sid}/generate", json={"base_revision": rev - 1})
    assert r.status_code == 409


def test_generate_refused_for_unsupported_size(setup):
    client, home, pid, sid, rev, _ = setup
    r = client.post(f"/api/projects/{pid}/shots/{sid}/generate", json={"base_revision": rev, "width": 1024})
    assert r.status_code == 400


def test_restart_marks_active_jobs_interrupted_and_never_reruns(setup, tmp_path):
    client, home, pid, sid, rev, _ = setup
    store = JobStore(home)
    job = {"id": "a" * 32, "kind": "image", "state": "RUNNING", "project_id": pid, "shot_id": sid, "take_id": "b" * 32,
           "workflow_id": "img-z-image-turbo", "workflow_version": "0.1.0", "params": {"seed": 1, "width": 512, "height": 512, "steps": 8, "cfg": 1.0},
           "created_at": "t", "started_at": "t", "finished_at": None, "updated_at": "t", "models": [], "comfy_prompt_id": None,
           "comfy_url": "http://fake", "error": None, "output_asset_id": None, "progress": {"mode": "indeterminate"}, "notes": [],
           "app_version": "x", "history": []}
    store.save(job)
    fresh = JobEngine(store=JobStore(home), projects_fn=lambda: ProjectStore(home / "projects"),
                      engine_url=lambda: "http://fake", resolver=lambda wid: resolved_args(),
                      build_graph=build_graph, compose=compose, runner_factory=FakeRunner)
    changed = fresh.recover()
    assert "a" * 32 in changed
    assert store.load("a" * 32)["state"] == "INTERRUPTED"
    assert FakeRunner.shared["submitted"] == []  # nothing was re-run


def test_retry_creates_a_new_take_and_keeps_the_old_one(setup):
    client, home, pid, sid, rev, _ = setup
    FakeRunner.shared["script"] = "error"
    first = wait_terminal(client, generate(client, pid, sid, rev)["id"])
    assert first["state"] == "FAILED"
    FakeRunner.shared["script"] = "success"
    rev = client.get(f"/api/projects/{pid}/director").json()["revision"]
    r = client.post(f"/api/jobs/{first['id']}/retry")
    assert r.status_code == 202
    second = wait_terminal(client, r.json()["id"])
    assert second["state"] == "COMPLETED"
    takes = takes_of(client, pid, sid)
    assert len(takes) == 2 and takes[0]["state"] == "failed" and takes[1]["state"] == "generated"


def test_approved_take_is_never_overwritten_by_a_later_job(setup):
    client, home, pid, sid, rev, _ = setup
    j1 = wait_terminal(client, generate(client, pid, sid, rev)["id"])
    assert j1["state"] == "COMPLETED"
    take_id = takes_of(client, pid, sid)[0]["id"]
    # approve the first take directly in the project file (approval UI comes later)
    from openfield.projects.store import ProjectStore as PS
    store = PS(home / "projects")
    store.mutate(pid, lambda m: [setattr(t, "state", "approved") for s in m.shots for t in s.takes if t.id == take_id])
    rev = client.get(f"/api/projects/{pid}/director").json()["revision"]
    FakeRunner.shared["script"] = "error"
    j2 = wait_terminal(client, generate(client, pid, sid, rev, seed=8)["id"])
    assert j2["state"] == "FAILED"
    still = next(t for t in takes_of(client, pid, sid) if t["id"] == take_id)
    assert still["state"] == "approved" and still["asset_id"] == j1["output_asset_id"]


def test_generation_status_reports_engine_and_worker(setup):
    client, home, pid, sid, rev, _ = setup
    s = client.get("/api/generation/status").json()
    assert "img-z-image-turbo" in s["generatable_workflows"] and s["worker_alive"] is True


# ---------------------------------------------------------------- unit: prompt, names, graph

def test_comfy_name_is_relative_to_the_folder_type(tmp_path):
    p = str(Path("D:/models/diffusion_models/z-Image-Turbo/z_image_turbo_bf16.safetensors"))
    assert comfy_name(p, ["diffusion_models", "unet"]) == str(Path("z-Image-Turbo/z_image_turbo_bf16.safetensors"))
    with pytest.raises(ValueError):
        comfy_name(str(Path("D:/elsewhere/file.safetensors")), ["diffusion_models"])


def test_zimage_graph_rejects_bad_sizes_and_steps():
    with pytest.raises(ValueError):
        build_zimage("p", "n", 500, 512, 1, unet="u", text_encoder="t", vae="v")
    with pytest.raises(ValueError):
        build_zimage("p", "n", 512, 512, 1, unet="u", text_encoder="t", vae="v", steps=0)


def test_prompt_says_camera_is_guidance_and_drops_repeats():
    from openfield.projects.schema import ProjectMeta, Shot, new_id
    m = ProjectMeta(id=new_id(), title="t", created_at="x", updated_at="x")
    s = Shot(id=new_id(), number=1, scene_id="a" * 16, camera_move="dolly", created_at="x", updated_at="x",
             texts={"beat": "She waits.", "action": "She waits."})
    pos, neg, notes = compose(m, s)
    assert pos.count("She waits") == 1 and "(guidance only)" in pos
    assert "watermark" in neg
