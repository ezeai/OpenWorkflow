"""Image inputs on modules: the name rules, the prepare step, and the run-time check against ComfyUI's input folder."""
import copy

import httpx
import pytest
from fastapi.testclient import TestClient

from openfield import api_forge, app as appmod
from openfield.forge import module

GOOD = "openfield_0123456789abcdef.png"

PROMPT = {
    "5": {"class_type": "LoadImage", "inputs": {"image": "old photo.jpg"}},
    "9": {"class_type": "SaveImage", "inputs": {"images": ["5", 0], "filename_prefix": "x"}},
}


def _ports(**over):
    p = {"name": "photo", "type": "image", "node": "5", "field": "image", "required": True}
    p.update(over)
    return module.Port(**p)


OUT = [module.OutputPort(name="image", node="9")]


@pytest.fixture()
def client(tmp_path, monkeypatch):
    c = TestClient(appmod.create_app(tmp_path))

    def no_real_queue(*a, **k):
        raise AssertionError("a test tried to submit a real job")
    monkeypatch.setattr(c.app.state.jobs, "submit_app", no_real_queue)
    return c


def _save_module(client) -> str:
    c = module.new_contract("b" * 32, 1, "Photo to image", "", {"id": "w" * 32, "name": "t", "sha256": "0" * 64},
                            copy.deepcopy(PROMPT), [_ports()], OUT)
    module.ModuleStore(client.app.state.services.home / "forge" / "modules").save(c)
    return c.module_id


def test_image_port_needs_no_default_and_default_is_refused():
    assert module.check([_ports()], OUT, PROMPT) == []
    problems = module.check([_ports(default=GOOD, required=False)], OUT, PROMPT)
    assert any("cannot have a default" in p for p in problems)


@pytest.mark.parametrize("value", [
    "blue dress.jpg",
    "../openfield_0123456789abcdef.png",
    "openfield_0123456789abcdef.gif",
    "openfield_xyz.png",
    5,
    None,
])
def test_prepare_refuses_anything_that_is_not_a_forge_upload_name(value):
    with pytest.raises(module.ModuleError):
        module.prepare([_ports()], PROMPT, {"photo": value})


def test_prepare_sets_the_upload_name_on_a_copy():
    out = module.prepare([_ports()], PROMPT, {"photo": GOOD})
    assert out["5"]["inputs"]["image"] == GOOD
    assert PROMPT["5"]["inputs"]["image"] == "old photo.jpg"


def test_missing_required_image_is_refused():
    with pytest.raises(module.ModuleError, match="required"):
        module.prepare([_ports()], PROMPT, {})


def test_run_refuses_when_comfy_does_not_have_the_image(client, monkeypatch):
    mid = _save_module(client)
    monkeypatch.setattr(api_forge, "_comfy_has_input", lambda url, name: False)
    r = client.post(f"/api/forge/modules/{mid}/run", json={"values": {"photo": GOOD}})
    assert r.status_code == 422, r.text
    assert "upload the image again" in r.json()["detail"]


def test_run_submits_with_the_image_name_in_the_prompt(client, monkeypatch):
    mid = _save_module(client)
    calls = []

    def fake_submit(*args, **kw):
        calls.append(args)
        return {"id": "k" * 32, "state": "QUEUED", "kind": "app", "provider": "local"}
    client.app.state.jobs.submit_app = fake_submit
    monkeypatch.setattr(api_forge, "_comfy_has_input", lambda url, name: name == GOOD)
    r = client.post(f"/api/forge/modules/{mid}/run", json={"values": {"photo": GOOD}})
    assert r.status_code == 202, r.text
    sent = calls[0][2]
    assert sent["5"]["inputs"]["image"] == GOOD
    assert "_meta" not in sent["5"]


def test_optional_image_left_out_does_not_crash_the_run(client, monkeypatch):
    c = module.new_contract("c" * 32, 1, "Optional photo", "", {"id": "w" * 32, "name": "t", "sha256": "0" * 64},
                            copy.deepcopy(PROMPT), [_ports(required=False)], OUT)
    module.ModuleStore(client.app.state.services.home / "forge" / "modules").save(c)
    calls = []
    client.app.state.jobs.submit_app = lambda *a, **k: calls.append(a) or {"id": "x", "state": "QUEUED"}
    monkeypatch.setattr(api_forge, "_comfy_has_input", lambda url, name: pytest.fail("no image was given"))
    r = client.post(f"/api/forge/modules/{c.module_id}/run", json={"values": {}})
    assert r.status_code == 202, r.text
    assert calls[0][2]["5"]["inputs"]["image"] == "old photo.jpg"


def test_unreachable_comfy_is_a_clear_refusal_not_a_pass(monkeypatch):
    def down(*a, **k):
        raise httpx.ConnectError("refused")
    monkeypatch.setattr(httpx, "get", down)
    with pytest.raises(module.ModuleError, match="not reachable"):
        api_forge._comfy_has_input("http://127.0.0.1:8188", GOOD)


def test_comfy_view_answer_decides_presence(monkeypatch):
    seen = {}

    def fake_get(url, params=None, timeout=None):
        seen.update(url=url, params=params)
        return httpx.Response(200 if params["filename"] == GOOD else 404)
    monkeypatch.setattr(httpx, "get", fake_get)
    assert api_forge._comfy_has_input("http://127.0.0.1:8188/", GOOD) is True
    assert seen == {"url": "http://127.0.0.1:8188/view", "params": {"filename": GOOD, "type": "input"}}
    assert api_forge._comfy_has_input("http://127.0.0.1:8188", "openfield_ffffffffffffffff.png") is False


def test_a_busy_comfy_is_waited_for_not_refused(monkeypatch):
    # ComfyUI stalls for a few seconds just after a job ends; the input check must ride that out.
    calls = []

    class Ok:
        status_code = 200

    def busy_then_ok(*a, **k):
        calls.append(1)
        if len(calls) < 3:
            raise httpx.ReadTimeout("busy")
        return Ok()
    monkeypatch.setattr(httpx, "get", busy_then_ok)
    monkeypatch.setattr("openfield.generation.comfy.time.sleep", lambda s: None)
    assert api_forge._comfy_has_input("http://127.0.0.1:8188", GOOD) is True
    assert len(calls) == 3


def test_a_comfy_that_stays_busy_is_refused_in_the_end(monkeypatch):
    from openfield.generation import comfy
    clock = iter(range(0, 1000, 5))
    monkeypatch.setattr(comfy.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(comfy.time, "sleep", lambda s: None)

    def always_busy(*a, **k):
        raise httpx.ReadTimeout("busy")
    monkeypatch.setattr(httpx, "get", always_busy)
    with pytest.raises(module.ModuleError, match="not reachable"):
        api_forge._comfy_has_input("http://127.0.0.1:8188", GOOD)


def test_reachable_waits_only_when_asked(monkeypatch):
    from openfield.generation import comfy
    calls = []

    def busy(*a, **k):
        calls.append(1)
        raise httpx.ReadTimeout("busy")
    monkeypatch.setattr(httpx, "get", busy)
    monkeypatch.setattr(comfy.time, "sleep", lambda s: None)
    assert comfy.ComfyRunner("http://127.0.0.1:8188").reachable() is False
    assert len(calls) == 1  # the quick check used by status pages does not retry
    clock = iter(range(0, 1000, 5))
    monkeypatch.setattr(comfy.time, "monotonic", lambda: next(clock))
    assert comfy.ComfyRunner("http://127.0.0.1:8188").reachable(wait=comfy.BUSY_WAIT) is False
    assert len(calls) > 2  # the pre-run check retried before giving up
