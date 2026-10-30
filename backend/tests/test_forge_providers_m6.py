"""Forge providers: local (default) and Comfy Cloud (explicit, consented). MOCKED SDK and MOCKED ComfyUI.

These tests prove OPENFIELD's own logic: consent, provider binding, no silent fallback, key handling, and the
runner interface. They do NOT prove that Comfy Cloud accepts or runs a workflow. No key was available.
"""
import copy
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openfield import api_forge
from openfield.app import create_app
from openfield.forge import object_info, providers
from openfield.generation import jobs as J
from openfield.generation.comfy import EngineError
from openfield.generation.jobs import JobEngine, JobStore

FIX = Path(__file__).parent / "fixtures" / "forge"
ZIMAGE = (FIX / "image_z_image_turbo.json").read_bytes()
# Dummy test fixture, not a real credential. Used only to check that secrets are never echoed back.
SECRET = "sk-test-SECRET-VALUE-123"  # noqa: S105 (dummy)


def png():
    import io
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (8, 8), (10, 200, 10)).save(b, format="PNG")
    return b.getvalue()


# ---------- fakes ----------

class FakeLocal:
    calls = 0
    fail = False

    def __init__(self, url):
        pass

    def reachable(self, wait=0.0):
        return True

    def submit(self, graph):
        FakeLocal.calls += 1
        return "local-prompt"

    def history(self, pid):
        return {"status": {"status_str": "success", "completed": True},
                "outputs": {"9": {"images": [{"filename": "local_1.png", "subfolder": "", "type": "output"}]}}}

    def queue(self):
        return {"queue_running": [], "queue_pending": []}

    def interrupt(self):
        pass

    def delete_pending(self, pid):
        pass

    def fetch(self, filename, subfolder, kind):
        return png()


class FakeOutput:
    def __init__(self, data):
        self.data = data

    def to_file(self, path):
        Path(path).write_bytes(self.data)


class FakeJob:
    def get_outputs(self, node_id):
        return [FakeOutput(png())]


class FakeSDKClient:
    """Shaped like the documented SDK: workflows.from_file, run, job.get_outputs, output.to_file."""
    submitted = []
    raise_with = None

    def __init__(self):
        self.workflows = self

    def from_file(self, path):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        FakeSDKClient.submitted.append(data)
        return data

    def run(self, wf):
        if FakeSDKClient.raise_with is not None:
            raise FakeSDKClient.raise_with
        return FakeJob()


@pytest.fixture
def world(tmp_path, monkeypatch):
    FakeLocal.calls = 0
    FakeLocal.fail = False
    FakeSDKClient.submitted = []
    FakeSDKClient.raise_with = None
    monkeypatch.setattr(api_forge, "_info", lambda request: object_info.load(Path("__no_such_home__")))
    monkeypatch.setattr(J, "POLL_S", 0.05)
    home = tmp_path / "home"
    app = create_app(home)
    engine = JobEngine(store=JobStore(home), projects_fn=app.state.services.projects, engine_url=lambda: "http://fake",
                       resolver=lambda wid: None, build_graph=None, compose=None, runner_factory=FakeLocal,
                       app_output_dir=lambda jid: home / "forge" / "runs" / jid,
                       cloud_runner_factory=lambda: providers.CloudRunner(client_factory=FakeSDKClient))
    engine.start()
    app.state.jobs = engine
    c = TestClient(app)
    return c, home


def ready(monkeypatch, key=SECRET):
    monkeypatch.setenv(providers.KEY_ENV, key)
    monkeypatch.setattr(providers.importlib.util, "find_spec", lambda name: object() if name == "comfy_sdk" else None)


def saved_app(c, wid_name="z.json"):
    wid = c.post(f"/api/forge/workflows?name={wid_name}", content=ZIMAGE).json()["workflow"]["id"]
    spec = c.get(f"/api/forge/workflows/{wid}/spec").json()["spec"]
    assert c.put(f"/api/forge/workflows/{wid}/spec", json={"spec": spec, "base_revision": 0}).status_code == 200
    return wid


def wait(c, jid, limit=20.0):
    t0 = time.time()
    while time.time() - t0 < limit:
        j = c.get(f"/api/jobs/{jid}").json()
        if j["state"] in ("COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED", "RECOVERY_REQUIRED"):
            return j
        time.sleep(0.05)
    raise AssertionError(f"stuck in {j['state']}")


# ---------- status and key handling ----------

def test_cloud_is_not_configured_without_a_key(monkeypatch):
    monkeypatch.delenv(providers.KEY_ENV, raising=False)
    st = providers.cloud_status()
    assert st["state"] == "not_configured" and st["key_set"] is False and st["verified_live"] is False


def test_sdk_missing_is_reported_and_not_installed_by_us(monkeypatch):
    monkeypatch.setenv(providers.KEY_ENV, SECRET)
    monkeypatch.setattr(providers.importlib.util, "find_spec", lambda name: None)
    st = providers.cloud_status()
    assert st["state"] == "sdk_missing" and "needs your approval" in st["message"]


def test_status_never_contains_the_key(monkeypatch):
    ready(monkeypatch)
    st = providers.cloud_status()
    assert st["state"] == "ready" and SECRET not in json.dumps(st)


def test_base_url_defaults_to_the_documented_cloud_host(monkeypatch):
    monkeypatch.delenv("COMFY_BASE_URL", raising=False)
    assert providers.cloud_base_url() == "https://cloud.comfy.org"
    monkeypatch.setenv("COMFY_BASE_URL", "https://example.test")
    assert providers.cloud_base_url() == "https://example.test"


# ---------- preview and consent digest ----------

def test_preview_lists_what_leaves_and_binds_a_digest():
    prompt = {"1": {"class_type": "CLIPTextEncode", "inputs": {"text": "a red fox"}},
              "2": {"class_type": "SaveImage", "inputs": {"filename_prefix": "x", "images": ["1", 0]}}}
    p = providers.preview(prompt)
    assert p["host"] == "cloud.comfy.org" and "local file paths" in p["does_not_send"] and "your API key" in p["does_not_send"]
    assert any("prompt" in s["detail"] or "text" in s["item"] for s in p["sends"])
    assert p["digest"] == providers.preview(copy.deepcopy(prompt))["digest"]
    changed = copy.deepcopy(prompt)
    changed["1"]["inputs"]["text"] = "a blue fox"
    assert p["digest"] != providers.preview(changed)["digest"]
    assert "C:" not in json.dumps(p) and "/home" not in json.dumps(p)


# ---------- the runner over the SDK ----------

def test_cloud_runner_uses_the_documented_calls_and_returns_outputs(tmp_path):
    graph = {"9": {"class_type": "SaveImage", "inputs": {"images": ["8", 0], "filename_prefix": "x"}},
             "8": {"class_type": "VAEDecode", "inputs": {}}}
    r = providers.CloudRunner(client_factory=FakeSDKClient)
    pid = r.submit(graph)
    assert FakeSDKClient.submitted and FakeSDKClient.submitted[0]["9"]["class_type"] == "SaveImage"
    h = r.history(pid)
    assert h["status"]["completed"] and "9" in h["outputs"]
    name = h["outputs"]["9"]["images"][0]["filename"]
    assert r.fetch(name, "", "output") == png()


def test_cloud_runner_cancel_is_refused_not_faked():
    r = providers.CloudRunner(client_factory=FakeSDKClient)
    with pytest.raises(EngineError, match="not supported"):
        r.interrupt()


def test_sdk_errors_do_not_leak_the_message_or_the_key(monkeypatch):
    FakeSDKClient.raise_with = RuntimeError(f"auth failed for key {SECRET} at https://x/y")
    r = providers.CloudRunner(client_factory=FakeSDKClient)
    with pytest.raises(EngineError) as e:
        r.submit({"9": {"class_type": "SaveImage", "inputs": {}}})
    assert SECRET not in str(e.value) and "RuntimeError" in str(e.value)


def test_missing_key_is_a_clean_engine_error(monkeypatch):
    monkeypatch.delenv(providers.KEY_ENV, raising=False)
    r = providers.CloudRunner()                          # real factory, no key
    with pytest.raises(EngineError, match="not configured"):
        r.submit({"9": {"class_type": "SaveImage", "inputs": {}}})


# ---------- routes and consent ----------

def test_providers_route_reports_both_without_the_key(world, monkeypatch):
    c, _ = world
    ready(monkeypatch)
    body = c.get("/api/forge/providers").json()
    assert body["default"] == "local" and body["cloud"]["state"] == "ready"
    assert SECRET not in json.dumps(body)


def test_cloud_preview_is_refused_for_apps_with_model_files(world, monkeypatch):
    c, _ = world
    ready(monkeypatch)
    wid = saved_app(c)
    r = c.post(f"/api/forge/workflows/{wid}/cloud-preview", json={"values": {}})
    assert r.status_code == 422          # refused before anything is sent; the model-file reason is shown


def test_cloud_run_needs_configuration(world, monkeypatch):
    c, _ = world
    monkeypatch.delenv(providers.KEY_ENV, raising=False)
    wid = saved_app(c)
    import openfield.forge.run as fr
    monkeypatch.setattr(fr, "prepare", lambda spec, v, a, api, values: (copy.deepcopy(api), [], []))
    r = c.post(f"/api/forge/workflows/{wid}/run", json={"values": {}, "provider": "cloud", "consent": {"acknowledged": True, "digest": "x"}})
    assert r.status_code == 409 and "not configured" in r.json()["detail"]


def test_cloud_run_needs_consent_to_the_exact_preview(world, monkeypatch):
    c, _ = world
    ready(monkeypatch)
    wid = saved_app(c)
    # The Z-Image app has model files, so cloud is refused before consent: exercise the consent gate directly.
    import openfield.forge.run as fr
    monkeypatch.setattr(fr, "prepare", lambda spec, v, a, api, values: (copy.deepcopy(api), [], []))
    r = c.post(f"/api/forge/workflows/{wid}/run", json={"values": {}, "provider": "cloud"})
    assert r.status_code == 409 and "consent is required" in r.json()["detail"]
    r = c.post(f"/api/forge/workflows/{wid}/run", json={"values": {}, "provider": "cloud", "consent": {"acknowledged": True, "digest": "wrong"}})
    assert r.status_code == 409 and "consent is required" in r.json()["detail"]
    r = c.post(f"/api/forge/workflows/{wid}/run", json={"values": {}, "provider": "cloud", "consent": {"acknowledged": False, "digest": "x"}})
    assert r.status_code == 409


def test_consented_cloud_run_completes_on_the_cloud_runner_only(world, monkeypatch):
    c, _ = world
    ready(monkeypatch)
    wid = saved_app(c)
    import openfield.forge.run as fr
    monkeypatch.setattr(fr, "prepare", lambda spec, v, a, api, values: (copy.deepcopy(api), [], []))
    # The digest the server will compare against: the same converted prompt for this app.
    pv = providers.preview(copy.deepcopy(api_forge_prompt(c, wid)))
    r = c.post(f"/api/forge/workflows/{wid}/run", json={"values": {}, "provider": "cloud",
                                                        "consent": {"acknowledged": True, "digest": pv["digest"]}})
    assert r.status_code == 202, r.text
    jid = r.json()["job"]["id"]
    j = wait(c, jid)
    assert j["state"] == "COMPLETED" and j["provider"] == "cloud"
    assert FakeLocal.calls == 0                             # the local engine was never used
    assert FakeSDKClient.submitted                           # the cloud runner was
    assert c.get(f"/api/forge/runs/{jid}/output").status_code == 200


def api_forge_prompt(c, wid):
    """The converted prompt the server prepares for the same app and values (no model files for this fixture)."""
    from openfield.forge import convert, workflow
    info = object_info.load(Path("__no_such_home__"))
    wf = workflow.parse_ui(json.loads(ZIMAGE))
    return convert.convert(wf, info).prompt


def test_cloud_failure_never_falls_back_to_local(world, monkeypatch):
    c, _ = world
    ready(monkeypatch)
    wid = saved_app(c)
    import openfield.forge.run as fr
    monkeypatch.setattr(fr, "prepare", lambda spec, v, a, api, values: (copy.deepcopy(api), [], []))
    pv = providers.preview(copy.deepcopy(api_forge_prompt(c, wid)))
    FakeSDKClient.raise_with = RuntimeError("boom")
    r = c.post(f"/api/forge/workflows/{wid}/run", json={"values": {}, "provider": "cloud", "consent": {"acknowledged": True, "digest": pv["digest"]}})
    jid = r.json()["job"]["id"]
    j = wait(c, jid)
    assert j["state"] == "FAILED" and j["provider"] == "cloud"
    assert FakeLocal.calls == 0


def test_a_cloud_job_interrupted_by_a_restart_is_not_polled_or_claimed(world, monkeypatch, tmp_path):
    c, home = world
    rec = {"id": "a" * 32, "kind": "app", "provider": "cloud", "state": "RUNNING", "project_id": None, "shot_id": None,
           "take_id": None, "workflow_id": "x", "workflow_version": "x", "params": {}, "created_at": "2026-10-27T00:00:00+00:00",
           "started_at": None, "finished_at": None, "updated_at": "2026-10-27T00:00:00+00:00", "models": [], "comfy_prompt_id": "p",
           "comfy_url": "http://fake", "error": None, "output_asset_id": None, "output_file": None, "prompt": None,
           "progress": {}, "notes": [], "app_version": "0", "history": []}
    JobStore(home).save(rec)

    class NoContact(FakeLocal):
        def history(self, pid):
            raise AssertionError("a cloud job must not be polled after a restart")
    engine = c.app.state.jobs
    engine.runner_factory = NoContact
    engine.cloud_runner_factory = lambda: NoContact("x")
    changed = engine.recover()
    assert "a" * 32 in changed
    j = engine.store.load("a" * 32)
    assert j["state"] == "INTERRUPTED" and "not checked" in j["history"][-1]["note"]


def test_no_key_is_written_anywhere_on_disk(world, monkeypatch):
    c, home = world
    ready(monkeypatch)
    wid = saved_app(c)
    c.get("/api/forge/providers")
    for p in home.rglob("*"):
        if p.is_file():
            assert SECRET.encode() not in p.read_bytes(), p.name


def test_a_finished_run_has_a_receipt(world, monkeypatch):
    c, _ = world
    wid = saved_app(c)
    import openfield.forge.run as fr
    monkeypatch.setattr(fr, "prepare", lambda spec, v, a, api, values: (copy.deepcopy(api), [], []))
    jid = c.post(f"/api/forge/workflows/{wid}/run", json={"values": {}}).json()["job"]["id"]
    assert wait(c, jid)["state"] == "COMPLETED"
    rec = c.get(f"/api/forge/runs/{jid}/receipt")
    assert rec.status_code == 200
    body = rec.json()
    assert body["spec_revision"] == 1 and body["provider"] == "local" and body["output_sha256"]
    assert c.get(f"/api/forge/runs/{'0' * 32}/receipt").status_code == 404


def test_an_identical_cloud_run_in_progress_is_not_started_twice(world, monkeypatch):
    c, home = world
    ready(monkeypatch)
    wid = saved_app(c)
    import openfield.forge.run as fr
    monkeypatch.setattr(fr, "prepare", lambda spec, v, a, api, values: (copy.deepcopy(api), [], []))
    pv = providers.preview(copy.deepcopy(api_forge_prompt(c, wid)))
    from openfield.generation.jobs import JobStore
    JobStore(home).save({"id": "b" * 32, "kind": "app", "provider": "cloud", "state": "RUNNING", "consent": {"digest": pv["digest"]},
                         "workflow_id": wid, "params": {}, "models": [], "created_at": "2026-10-27T00:00:00+00:00", "history": []})
    r = c.post(f"/api/forge/workflows/{wid}/run", json={"values": {}, "provider": "cloud",
                                                        "consent": {"acknowledged": True, "digest": pv["digest"]}})
    assert r.status_code == 409 and "already in progress" in r.json()["detail"]
