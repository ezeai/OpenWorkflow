"""Forge Milestone 4: local-model layout proposals.

FAKE MODEL: chat_json and status are replaced. These tests prove the checks around the model: shape,
allowed controls, catalogue types, validation, refusals, and what is kept from the model's view.
Whether a real model proposes good layouts is measured separately (see docs/FORGE.md).
"""
import copy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from openfield import api_forge
from openfield.app import create_app
from openfield.config import Settings
from openfield.forge import llm, object_info, propose, uispec

FIX = Path(__file__).parent / "fixtures" / "forge"


def live_info():
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


class Fake:
    def __init__(self):
        self.reply = {}
        self.error = None
        self.seen = []
        self.state = "ok"

    def status(self, url):
        if self.state != "ok":
            return {"state": self.state, "url": url, "models": [], "reason": "Ollama is not running on this computer."}
        return {"state": "ok", "url": url, "models": ["qwen3:4b-instruct-2507-q8_0"], "reason": None}

    def chat_json(self, url, model, system, user):
        self.seen.append((model, system, user))
        if self.error:
            raise self.error
        return copy.deepcopy(self.reply)


@pytest.fixture
def fake(monkeypatch):
    f = Fake()
    monkeypatch.setattr(llm, "status", f.status)
    monkeypatch.setattr(llm, "chat_json", f.chat_json)
    return f


@pytest.fixture
def client(tmp_path, monkeypatch, fake):
    monkeypatch.setattr(api_forge, "_info", lambda request: live_info())
    c = TestClient(create_app(tmp_path / "home"))
    wid = c.post("/api/forge/workflows?name=z.json", content=(FIX / "image_z_image_turbo.json").read_bytes()).json()["workflow"]["id"]
    return c, wid


def spec_of(c, wid):
    return c.get(f"/api/forge/workflows/{wid}/spec").json()["spec"]


def good_reply(spec):
    ids = {c["binding"]["node"] + "." + c["binding"]["input"]: c for c in spec["components"]}
    return {
        "name": "Portrait studio",
        "description": "Describe a portrait and get an image.",
        "layout": "simple_generator",
        "controls": [
            {"id": "57:27.text", "label": "Describe the portrait", "type": "prompt", "group": "main", "hidden": False, "help": "Be specific about light and mood."},
            {"id": "57:3.steps", "label": "Quality", "type": "slider", "group": "advanced", "hidden": False, "help": "More steps take longer."},
            {"id": "57:3.cfg", "label": "Prompt strength", "type": "slider", "group": "advanced", "hidden": True, "help": "Set for this model."},
        ],
    }


def test_settings_refuse_remote_model_servers():
    assert Settings().llm_url == "http://127.0.0.1:11434"
    with pytest.raises(ValidationError):
        Settings(llm_url="http://10.0.0.5:11434")


def test_real_status_reports_a_closed_port_as_unreachable():
    st = llm.status("http://127.0.0.1:9")
    assert st["state"] == "unreachable" and st["models"] == []


def test_status_route_is_an_answer_not_an_error(client, fake, monkeypatch):
    c, _ = client
    fake.state = "unreachable"
    r = c.get("/api/forge/llm/status")
    assert r.status_code == 200 and r.json()["state"] == "unreachable" and r.json()["default_model"] is None


def test_valid_proposal_is_applied_to_a_copy_and_not_saved(client, fake):
    c, wid = client
    before = spec_of(c, wid)
    fake.reply = good_reply(before)
    r = c.post(f"/api/forge/workflows/{wid}/spec/propose", json={"request": "make it a portrait tool"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["spec"]["name"] == "Portrait studio" and body["spec"]["generated_by"] == "model"
    labels = {c["binding"]["node"] + "." + c["binding"]["input"]: c["label"] for c in body["spec"]["components"]}
    assert labels["57:27.text"] == "Describe the portrait"
    assert body["validation"]["status"] == "valid" and body["warnings"] == []
    assert set(body["changed"]) == {"57:27.text", "57:3.steps", "57:3.cfg"}
    assert spec_of(c, wid) == before                       # nothing was saved


def test_unknown_control_ids_are_ignored_with_a_warning(client, fake):
    c, wid = client
    reply = good_reply(spec_of(c, wid))
    reply["controls"].append({"id": "99:1.secret", "label": "Hidden", "type": "text", "group": "main", "hidden": False, "help": ""})
    fake.reply = reply
    body = c.post(f"/api/forge/workflows/{wid}/spec/propose", json={"request": "x"}).json()
    assert any("not in this workflow" in w for w in body["warnings"])
    assert all(c["binding"]["node"] != "99" for c in body["spec"]["components"])


def test_component_types_outside_the_catalogue_are_ignored(client, fake):
    c, wid = client
    reply = good_reply(spec_of(c, wid))
    reply["controls"][0]["type"] = "run_shell"
    fake.reply = reply
    body = c.post(f"/api/forge/workflows/{wid}/spec/propose", json={"request": "x"}).json()
    assert any("unknown component type" in w for w in body["warnings"])
    prompt = next(c for c in body["spec"]["components"] if c["binding"]["node"] == "57:27")
    assert prompt["type"] == "prompt"                      # unchanged, the bad change was dropped


def test_unknown_layout_is_refused(client, fake):
    c, wid = client
    reply = good_reply(spec_of(c, wid))
    reply["layout"] = "video_editor"
    fake.reply = reply
    r = c.post(f"/api/forge/workflows/{wid}/spec/propose", json={"request": "x"})
    assert r.status_code == 422 and "layout" in r.json()["detail"]


def test_non_object_reply_is_refused(client, fake):
    c, wid = client
    fake.reply = []
    assert c.post(f"/api/forge/workflows/{wid}/spec/propose", json={"request": "x"}).status_code == 422


def test_invalid_json_from_the_model_is_a_clean_failure(client, fake):
    c, wid = client
    fake.error = llm.LLMError("the model did not return valid JSON")
    r = c.post(f"/api/forge/workflows/{wid}/spec/propose", json={"request": "x"})
    assert r.status_code == 503 and "valid JSON" in r.json()["detail"]
    assert c.get(f"/api/forge/workflows/{wid}/spec").status_code == 200   # the deterministic spec is still there


def test_model_not_running_leaves_everything_working(client, fake):
    c, wid = client
    fake.state = "unreachable"
    r = c.post(f"/api/forge/workflows/{wid}/spec/propose", json={"request": "x"})
    assert r.status_code == 503 and "not running" in r.json()["detail"]
    assert c.get(f"/api/forge/workflows/{wid}/spec").json()["source"] == "generated"


def test_type_change_that_breaks_a_binding_is_reported_and_cannot_be_saved(client, fake):
    c, wid = client
    spec = spec_of(c, wid)
    reply = good_reply(spec)
    reply["controls"] = [{"id": "57:3.steps", "label": "Steps", "type": "toggle", "group": "main", "hidden": False, "help": ""}]
    fake.reply = reply
    body = c.post(f"/api/forge/workflows/{wid}/spec/propose", json={"request": "x"}).json()
    assert body["validation"]["status"] == "invalid"
    r = c.put(f"/api/forge/workflows/{wid}/spec", json={"spec": body["spec"]})
    assert r.status_code == 422


def test_proposal_built_on_a_client_spec_is_checked_against_the_workflow(client, fake):
    c, wid = client
    spec = spec_of(c, wid)
    spec["components"][0]["binding"] = {"node": "57:28", "input": "unet_name"}   # a model file: invalid
    fake.reply = good_reply(spec)
    body = c.post(f"/api/forge/workflows/{wid}/spec/propose", json={"request": "x", "current_spec": spec}).json()
    assert body["validation"]["status"] == "invalid"


def test_nothing_about_model_files_or_prompt_text_is_sent(client, fake):
    c, wid = client
    fake.reply = good_reply(spec_of(c, wid))
    c.post(f"/api/forge/workflows/{wid}/spec/propose", json={"request": "make it simpler"})
    model, system, user = fake.seen[-1]
    text = system + user
    for secret in ("z_image_turbo_bf16", "qwen_3_4b", "ae.safetensors", "Latina", "safetensors", "\\", "C:"):
        assert secret not in text, secret
    assert "make it simpler" in user and "57:27.text" in user


def test_request_text_is_bounded_and_stripped_of_control_characters(client, fake):
    c, wid = client
    fake.reply = good_reply(spec_of(c, wid))
    c.post(f"/api/forge/workflows/{wid}/spec/propose", json={"request": "a\x00b" + "x" * 900})
    _, _, user = fake.seen[-1]
    assert "\x00" not in user
    assert len(json.loads(user.split("request: ", 1)[1])) <= propose.MAX_REQUEST


def test_parse_is_strict_about_shape_and_tolerant_about_content():
    ids = {"57:27.text"}
    with pytest.raises(propose.ProposalError):
        propose.parse_proposal({"layout": "nope", "controls": []}, ids)
    with pytest.raises(propose.ProposalError):
        propose.parse_proposal({"layout": "simple_generator", "controls": "x"}, ids)
    prop, warnings = propose.parse_proposal({"layout": "simple_generator", "controls": [
        {"id": "57:27.text", "label": "A" * 200, "type": "prompt", "group": "main"},
        {"id": "57:27.text", "label": "again", "type": "prompt", "group": "main"},
        "junk",
    ]}, ids)
    assert len(prop["controls"]["57:27.text"]["label"]) == 80
    assert any("twice" in w for w in warnings) and any("not an object" in w for w in warnings)


def test_default_model_prefers_a_small_instruct_model():
    models = ["qwen3.5:9b", "qwen3:4b-thinking-2507-q8_0", "qwen3:4b-instruct-2507-q8_0", "llama3.1:latest"]
    assert llm.default_model(models, "") == "qwen3:4b-instruct-2507-q8_0"
    assert llm.default_model(models, "llama3.1:latest") == "llama3.1:latest"      # an explicit choice wins
    assert llm.default_model(["qwen3.5:9b"], "") == "qwen3.5:9b"
    assert llm.default_model([], "") is None
