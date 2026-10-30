"""Take approval tests: choose the generated take a shot ships with.

Real project storage on a temporary disk; ComfyUI is MOCKED with FakeRunner exactly as in
test_generation_m5.py, so these tests prove OPENFIELD's approval logic, not a real model run.
The real generation path (job -> take -> asset) it depends on is verified by test_generation_m5.py.
"""
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from openfield.app import create_app
from openfield.generation import jobs as J
from openfield.generation.builders import build_graph
from openfield.generation import prompt
from openfield.generation.jobs import JobEngine, JobStore
from openfield.projects.store import ProjectStore


class FakeRunner:
    """Scripted ComfyUI success; copied from the established test pattern."""
    shared = {"script": "success", "interrupted": False, "submitted": []}

    def __init__(self, url):
        self.url = url

    def reachable(self, wait=0.0):
        return self.shared["script"] != "unreachable"

    def submit(self, graph):
        self.shared["submitted"].append(graph)
        return "fake-prompt-1"

    def history(self, pid):
        return {"status": {"status_str": "success", "completed": True},
                "outputs": {"10": {"images": [{"filename": "out_00001_.png", "subfolder": "", "type": "output"}]}}}

    def queue(self):
        return {"queue_running": [], "queue_pending": []}

    def interrupt(self):
        self.shared["interrupted"] = True

    def delete_pending(self, pid):
        self.shared["interrupted"] = True

    def fetch(self, filename, subfolder, kind):
        import io
        buf = io.BytesIO()
        Image.new("RGB", (16, 16), (40, 40, 40)).save(buf, format="PNG")
        return buf.getvalue()


def resolved_args():
    from openfield.generation.jobs import Resolved
    return Resolved(graph_args={"unet": "z-Image-Turbo\\z_image_turbo_bf16.safetensors",
                                "text_encoder": "qwe3 vl gguf\\qwen_3_4b.safetensors", "vae": "ae.safetensors"},
                    models=[{"role": "diffusion", "name": "z_image_turbo_bf16.safetensors", "identity_confirmed": False}])


@pytest.fixture
def setup(tmp_path, monkeypatch):
    FakeRunner.shared.update({"script": "success", "interrupted": False, "submitted": []})
    home = tmp_path / "home"
    app = create_app(home)
    engine = J.JobEngine(store=JobStore(home), projects_fn=lambda: ProjectStore(home / "projects"),
                         engine_url=lambda: "http://fake", resolver=lambda wid: resolved_args(),
                         build_graph=build_graph, compose=prompt.compose, runner_factory=FakeRunner)
    engine.start()
    app.state.jobs = engine
    client = TestClient(app)

    def client_factory():
        """A fresh client over the same storage: proves approval survives restart of the app object."""
        return TestClient(create_app(home))

    p = client.post("/api/projects", json={"title": "Approval"}).json()
    pid, rev = p["id"], p["revision"]
    rev = client.put(f"/api/projects/{pid}/script", json={"base_revision": rev, "text": "EXT. STREET - NIGHT\nMira walks.\n"}).json()["revision"]
    rev = client.post(f"/api/projects/{pid}/plan/apply", json={"base_revision": rev, "picks": [1]}).json()["revision"]
    sid = client.get(f"/api/projects/{pid}/director").json()["shots"][0]["id"]
    rev = client.patch(f"/api/projects/{pid}/shots/{sid}",
                       json={"base_revision": rev, "fields": {"workflow_id": "img-z-image-turbo"}}).json()["revision"]

    def wait_terminal(jid, limit=20.0):
        t0 = time.time()
        while time.time() - t0 < limit:
            j = client.get(f"/api/jobs/{jid}").json()
            if j["state"] in ("COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED", "RECOVERY_REQUIRED"):
                return j
            time.sleep(0.05)
        raise AssertionError("job did not finish")

    def generate_ok(seed=7):
        j = client.post(f"/api/projects/{pid}/shots/{sid}/generate", json={"base_revision": client.get(f"/api/projects/{pid}").json()["revision"], "seed": seed})
        assert j.status_code == 202, j.text
        done = wait_terminal(j.json()["id"])
        assert done["state"] == "COMPLETED", done
        return done

    return {"client": client, "home": home, "pid": pid, "sid": sid, "rev": rev, "engine": engine,
            "generate_ok": generate_ok, "client_factory": client_factory}


def takes_of(c, pid, sid):
    return next(s for s in c.get(f"/api/projects/{pid}/director").json()["shots"] if s["id"] == sid)["takes"]


def approve(c, pid, sid, tid, rev):
    return c.post(f"/api/projects/{pid}/shots/{sid}/takes/{tid}/approve", json={"base_revision": rev})


def unapprove(c, pid, sid, tid, rev):
    return c.post(f"/api/projects/{pid}/shots/{sid}/takes/{tid}/unapprove", json={"base_revision": rev})


def test_approve_generated_take(setup):
    s = setup
    c, pid, sid = s["client"], s["pid"], s["sid"]
    s["generate_ok"](7)
    takes = takes_of(c, pid, sid)
    assert len(takes) == 1 and takes[0]["state"] == "generated" and takes[0]["asset_id"]
    r = approve(c, pid, sid, takes[0]["id"], c.get(f"/api/projects/{pid}").json()["revision"])
    assert r.status_code == 200, r.text
    assert takes_of(c, pid, sid)[0]["state"] == "approved"


def test_chosen_take_survives_app_restart(setup):
    s = setup
    c, pid, sid = s["client"], s["pid"], s["sid"]
    s["generate_ok"](3)
    tid = takes_of(c, pid, sid)[0]["id"]
    assert approve(c, pid, sid, tid, c.get(f"/api/projects/{pid}").json()["revision"]).status_code == 200
    c2 = s["client_factory"]()
    assert takes_of(c2, pid, sid)[0]["state"] == "approved"


def test_choosing_the_second_take_releases_the_first(setup):
    s = setup
    c, pid, sid = s["client"], s["pid"], s["sid"]
    s["generate_ok"](1)
    s["generate_ok"](2)
    t1, t2 = takes_of(c, pid, sid)
    rev = c.get(f"/api/projects/{pid}").json()["revision"]
    assert approve(c, pid, sid, t1["id"], rev).status_code == 200
    rev = c.get(f"/api/projects/{pid}").json()["revision"]
    r = approve(c, pid, sid, t2["id"], rev)
    assert r.status_code == 200, r.text
    states = {t["id"]: t["state"] for t in takes_of(c, pid, sid)}
    assert states[t1["id"]] == "generated" and states[t2["id"]] == "approved"


def test_only_a_generated_take_with_output_can_be_chosen(setup):
    s = setup
    c, pid, sid = s["client"], s["pid"], s["sid"]
    # a take recorded without generation: stays 'requested', has no asset
    rev = c.get(f"/api/projects/{pid}").json()["revision"]
    r = c.post(f"/api/projects/{pid}/shots/{sid}/takes", json={"base_revision": rev, "note": "manual take"})
    assert r.status_code == 201, r.text
    tid = takes_of(c, pid, sid)[0]["id"]
    rev = c.get(f"/api/projects/{pid}").json()["revision"]
    assert approve(c, pid, sid, tid, rev).status_code == 400


def test_unapprove_returns_to_generated_and_rejects_non_chosen(setup):
    s = setup
    c, pid, sid = s["client"], s["pid"], s["sid"]
    s["generate_ok"](5)
    tid = takes_of(c, pid, sid)[0]["id"]
    rev = c.get(f"/api/projects/{pid}").json()["revision"]
    rev2 = c.post(f"/api/projects/{pid}/shots/{sid}/takes/{tid}/approve", json={"base_revision": rev}).json()["revision"]
    r = unapprove(c, pid, sid, tid, rev2)
    assert r.status_code == 200, r.text
    assert takes_of(c, pid, sid)[0]["state"] == "generated"
    rev = c.get(f"/api/projects/{pid}").json()["revision"]
    assert unapprove(c, pid, sid, tid, rev).status_code == 400  # nothing to release
    assert takes_of(c, pid, sid)[0]["state"] == "generated"


def test_stale_revision_is_conflict_and_unknown_take_is_refused(setup):
    s = setup
    c, pid, sid = s["client"], s["pid"], s["sid"]
    s["generate_ok"](9)
    tid = takes_of(c, pid, sid)[0]["id"]
    rev = c.get(f"/api/projects/{pid}").json()["revision"]
    assert approve(c, pid, sid, tid, rev - 1).status_code == 409
    assert approve(c, pid, sid, "no-such-take", rev).status_code == 400
    assert approve(c, pid, "no-such-shot", tid, rev).status_code == 400
    assert takes_of(c, pid, sid)[0]["state"] == "generated"
