"""Forge Milestone 3: running a saved app through the job engine.

MOCKED ComfyUI: FakeRunner scripts the server's answers. These tests prove OPENFIELD's own logic:
refusals, value application, job lifecycle, output saving and provenance. The real run is recorded in
docs/evidence/forge-m3.
"""
import copy
import io
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from openfield import api_forge
from openfield.app import create_app
from openfield.forge import object_info, uispec
from openfield.generation import jobs as J
from openfield.generation.jobs import JobEngine, JobStore

FIX = Path(__file__).parent / "fixtures" / "forge"


def png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), (30, 90, 30)).save(buf, format="PNG")
    return buf.getvalue()


class FakeRunner:
    shared = {"script": "success", "submitted": []}

    def __init__(self, url):
        self.url = url

    def reachable(self, wait=0.0):
        return True

    def submit(self, graph):
        self.shared["submitted"].append(graph)
        return "fake-prompt-app"

    def history(self, pid):
        if self.shared["script"] == "error":
            return {"status": {"status_str": "error", "completed": False, "messages": [["execution_error", {}]]}, "outputs": {}}
        return {"status": {"status_str": "success", "completed": True},
                "outputs": {"9": {"images": [{"filename": "forge_00001_.png", "subfolder": "", "type": "output"}]}}}

    def queue(self):
        return {"queue_running": [], "queue_pending": []}

    def interrupt(self):
        pass

    def delete_pending(self, pid):
        pass

    def fetch(self, filename, subfolder, kind):
        return png()


def live_info():
    """Node definitions with the installed model files, as a running ComfyUI would report them."""
    info = object_info.load(Path("__no_such_home__"))
    i = copy.deepcopy(info)
    i.source = "live"
    for cls, name, choices in (
        ("UNETLoader", "unet_name", ["z-Image-Turbo\\z_image_turbo_bf16.safetensors"]),
        ("CLIPLoader", "clip_name", ["qwe3 vl gguf\\qwen_3_4b.safetensors"]),
        ("VAELoader", "vae_name", ["ae.safetensors"]),
    ):
        spec = i.defs[cls].input(name)
        spec.choices, spec.options = choices, {k: v for k, v in spec.options.items() if k != "file_choices_removed"}
    return i


@pytest.fixture
def client(tmp_path, monkeypatch):
    FakeRunner.shared.update({"script": "success", "submitted": []})
    monkeypatch.setattr(J, "POLL_S", 0.05)
    monkeypatch.setattr(api_forge, "_info", lambda request: live_info())
    home = tmp_path / "home"
    app = create_app(home)
    engine = JobEngine(store=JobStore(home), projects_fn=app.state.services.projects, engine_url=lambda: "http://fake",
                       resolver=lambda wid: None, build_graph=None, compose=None, runner_factory=FakeRunner,
                       app_output_dir=lambda jid: home / "forge" / "runs" / jid)
    engine.start()
    app.state.jobs = engine
    return TestClient(app)


def saved_app(c, name="image_z_image_turbo.json"):
    wid = c.post(f"/api/forge/workflows?name={name}", content=(FIX / "image_z_image_turbo.json").read_bytes()).json()["workflow"]["id"]
    spec = c.get(f"/api/forge/workflows/{wid}/spec").json()["spec"]
    r = c.put(f"/api/forge/workflows/{wid}/spec", json={"spec": spec, "base_revision": 0})
    assert r.status_code == 200, r.text
    return wid, spec


def ids_by_binding(spec):
    return {f"{c['binding']['node']}.{c['binding']['input']}": c["id"] for c in spec["components"]}


def wait_terminal(c, jid, limit=20.0):
    t0 = time.time()
    while time.time() - t0 < limit:
        j = c.get(f"/api/jobs/{jid}").json()
        if j["state"] in ("COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED", "RECOVERY_REQUIRED"):
            return j
        time.sleep(0.05)
    raise AssertionError(f"job stuck in {j['state']}")


def test_run_completes_and_saves_the_output_with_provenance(client, tmp_path):
    wid, spec = saved_app(client)
    b = ids_by_binding(spec)
    r = client.post(f"/api/forge/workflows/{wid}/run", json={"values": {b["57:27.text"]: "a red fox", b["57:13.width"]: 512,
                                                                         b["57:13.height"]: 512, b["57:3.seed"]: 123}})
    assert r.status_code == 202, r.text
    jid = r.json()["job"]["id"]
    j = wait_terminal(client, jid)
    assert j["state"] == "COMPLETED" and j["kind"] == "app" and j["take_id"] is None and j["output_asset_id"] is None
    sent = FakeRunner.shared["submitted"][-1]
    assert sent["57:27"]["inputs"]["text"] == "a red fox" and sent["57:3"]["inputs"]["seed"] == 123
    assert sent["57:28"]["inputs"]["unet_name"] == "z-Image-Turbo\\z_image_turbo_bf16.safetensors"   # the installed name, not the file name
    assert all("_meta" not in n for n in sent.values())
    out = client.get(f"/api/forge/runs/{jid}/output")
    assert out.status_code == 200 and out.headers["content-type"] == "image/png" and out.content == png()
    run_dir = tmp_path / "home" / "forge" / "runs" / jid
    prov = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert prov["spec_revision"] == 1 and prov["values"][b["57:3.seed"]] == 123 and prov["output_sha256"] == j["output_file"]["sha256"]
    assert prov["workflow_sha256"] and {m["file"] for m in prov["models"]} >= {"z_image_turbo_bf16.safetensors"}


def test_app_runs_do_not_create_takes_or_smoke_tests(client, tmp_path):
    wid, _ = saved_app(client)
    jid = client.post(f"/api/forge/workflows/{wid}/run", json={"values": {}}).json()["job"]["id"]
    j = wait_terminal(client, jid)
    assert j["state"] == "COMPLETED" and j["take_id"] is None and j["project_id"] is None
    smoke = tmp_path / "home" / "models" / "smoke_tests.json"
    assert not smoke.exists() or json.loads(smoke.read_text(encoding="utf-8")).get("smoke_tests", []) == []


def test_unsaved_app_is_refused(client):
    wid = client.post("/api/forge/workflows?name=z.json", content=(FIX / "image_z_image_turbo.json").read_bytes()).json()["workflow"]["id"]
    r = client.post(f"/api/forge/workflows/{wid}/run", json={"values": {}})
    assert r.status_code == 409 and "save the app" in r.json()["detail"]


def test_missing_model_file_is_refused(client, monkeypatch):
    wid, _ = saved_app(client)
    info = live_info()
    info.defs["CLIPLoader"].input("clip_name").choices = ["other.safetensors"]
    monkeypatch.setattr(api_forge, "_info", lambda request: info)
    r = client.post(f"/api/forge/workflows/{wid}/run", json={"values": {}})
    assert r.status_code == 422 and "is not in ComfyUI" in r.json()["detail"]


def test_ambiguous_model_file_is_refused(client, monkeypatch):
    wid, _ = saved_app(client)
    info = live_info()
    info.defs["VAELoader"].input("vae_name").choices = ["a\\ae.safetensors", "b\\ae.safetensors"]
    monkeypatch.setattr(api_forge, "_info", lambda request: info)
    r = client.post(f"/api/forge/workflows/{wid}/run", json={"values": {}})
    assert r.status_code == 422 and "matches several" in r.json()["detail"]


def test_unchecked_model_files_are_refused(client, monkeypatch):
    """With only the bundled node list, model files cannot be checked, so nothing runs."""
    wid, _ = saved_app(client)
    monkeypatch.setattr(api_forge, "_info", lambda request: object_info.load(Path("__no_such_home__")))
    r = client.post(f"/api/forge/workflows/{wid}/run", json={"values": {}})
    assert r.status_code == 422 and "could not be checked" in r.json()["detail"]


def test_out_of_range_value_is_refused_and_nothing_is_queued(client):
    wid, spec = saved_app(client)
    b = ids_by_binding(spec)
    before = len(FakeRunner.shared["submitted"])
    r = client.post(f"/api/forge/workflows/{wid}/run", json={"values": {b["57:13.width"]: 10**6}})
    assert r.status_code == 422 and "outside" in r.json()["detail"]
    assert len(FakeRunner.shared["submitted"]) == before


def test_unknown_control_is_refused(client):
    wid, _ = saved_app(client)
    r = client.post(f"/api/forge/workflows/{wid}/run", json={"values": {"not-a-control": 1}})
    assert r.status_code == 422 and "unknown component" in r.json()["detail"]


def test_a_spec_with_an_unverified_control_cannot_run(client):
    wid, spec = saved_app(client)
    spec["components"][0]["binding"] = {"node": "57:28", "input": "unet_name"}   # a model file: invalid
    assert client.put(f"/api/forge/workflows/{wid}/spec", json={"spec": spec}).status_code == 422
    # the saved spec is still the valid one, so the run uses it and succeeds
    assert client.post(f"/api/forge/workflows/{wid}/run", json={"values": {}}).status_code == 202


def test_comfy_error_fails_the_run_and_saves_no_output(client):
    FakeRunner.shared["script"] = "error"
    wid, _ = saved_app(client)
    jid = client.post(f"/api/forge/workflows/{wid}/run", json={"values": {}}).json()["job"]["id"]
    j = wait_terminal(client, jid)
    assert j["state"] == "FAILED" and j["output_file"] is None
    assert client.get(f"/api/forge/runs/{jid}/output").status_code == 404


def test_output_route_only_serves_app_runs(client):
    assert client.get("/api/forge/runs/" + "0" * 32 + "/output").status_code == 404
    assert client.get("/api/forge/runs/not-hex/output").status_code == 404


def test_app_runs_cannot_be_retried_as_shots(client):
    FakeRunner.shared["script"] = "error"
    wid, _ = saved_app(client)
    jid = client.post(f"/api/forge/workflows/{wid}/run", json={"values": {}}).json()["job"]["id"]
    assert wait_terminal(client, jid)["state"] == "FAILED"
    r = client.post(f"/api/jobs/{jid}/retry")
    assert r.status_code == 409 and "run the app again" in r.json()["detail"]
