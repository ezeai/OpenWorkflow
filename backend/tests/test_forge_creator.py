"""Creator path: prompt writing (with and without a reference photo), the Krea-2 graph edit, and the routes.
The model server is faked; no ComfyUI job is started."""
import copy

import pytest
from fastapi.testclient import TestClient

from openfield import app as appmod
from openfield.forge import creator, llm_providers


KREA_GRAPH = {
    "30:6": {"class_type": "CLIPTextEncode", "inputs": {"text": ["30:28", 0], "clip": ["30:11", 0]}, "_meta": {"title": "x"}},
    "30:3": {"class_type": "KSampler", "inputs": {"seed": 1, "steps": 8}},
}


def test_writer_without_reference_is_text_only():
    msgs = creator.writer_messages("a calm nurse in her 30s", None)
    assert msgs[0]["role"] == "system" and "images" not in msgs[1]
    assert "a calm nurse" in msgs[1]["content"]


def test_writer_with_reference_attaches_the_photo_and_asks_for_look_only():
    msgs = creator.writer_messages("", "AAAA")
    assert msgs[1]["images"] == ["AAAA"]
    assert "Describe its visible look" in msgs[1]["content"] and "do not name anyone" in msgs[1]["content"]


def test_nothing_to_describe_is_refused():
    with pytest.raises(creator.CreatorError, match="describe"):
        creator.writer_messages("  ", None)


def test_an_overlong_idea_is_refused():
    with pytest.raises(creator.CreatorError, match="longer"):
        creator.writer_messages("x" * (creator.MAX_IDEA + 1), None)


def test_clean_prompt_collapses_whitespace_and_refuses_empty():
    assert creator.clean_prompt("  A  portrait\n of  a woman. ") == "A portrait of a woman."
    with pytest.raises(creator.CreatorError, match="empty"):
        creator.clean_prompt("   ")


def test_krea_graph_sets_the_text_and_seed_without_touching_the_original():
    original = copy.deepcopy(KREA_GRAPH)
    g = creator.krea_graph(KREA_GRAPH, "a portrait", 77)
    assert g["30:6"]["inputs"]["text"] == "a portrait" and g["30:3"]["inputs"]["seed"] == 77
    assert "_meta" not in g["30:6"]
    assert KREA_GRAPH == original


def test_a_different_graph_is_refused():
    with pytest.raises(creator.CreatorError, match="Krea-2"):
        creator.krea_graph({"1": {"class_type": "X", "inputs": {}}}, "a", 1)


@pytest.fixture()
def client(tmp_path):
    return TestClient(appmod.create_app(tmp_path))


def test_prompt_route_returns_the_model_text(client, monkeypatch):
    seen = {}

    def fake_chat(ep, model, messages, timeout=0):
        seen["messages"] = messages
        return "  A soft portrait of a woman in her 30s, warm light.  "
    monkeypatch.setattr(llm_providers, "chat", fake_chat)
    r = client.post("/api/forge/creator/prompt", json={"idea": "a nurse", "endpoint": "ollama", "model": "qwen3.5:4b"})
    assert r.status_code == 200
    assert r.json() == {"prompt": "A soft portrait of a woman in her 30s, warm light.", "model": "qwen3.5:4b", "reference_used": False}
    assert seen["messages"][1]["content"].endswith("a nurse")


def test_prompt_route_passes_the_reference_to_the_model(client, monkeypatch):
    seen = {}

    def fake_chat(ep, model, messages, timeout=0):
        seen["images"] = messages[1].get("images")
        return "A portrait."
    monkeypatch.setattr(llm_providers, "chat", fake_chat)
    r = client.post("/api/forge/creator/prompt", json={"reference_b64": "BBBB", "endpoint": "ollama", "model": "m"})
    assert r.status_code == 200 and r.json()["reference_used"] is True
    assert seen["images"] == ["BBBB"]


def test_prompt_route_reports_an_unreachable_server(client, monkeypatch):
    def down(ep, model, messages, timeout=0):
        raise llm_providers.ProviderError("Ollama did not answer (ConnectError)")
    monkeypatch.setattr(llm_providers, "chat", down)
    r = client.post("/api/forge/creator/prompt", json={"idea": "x", "endpoint": "ollama", "model": "m"})
    assert r.status_code == 503 and "did not answer" in r.json()["detail"]


def test_prompt_route_refuses_a_remote_custom_server(client):
    r = client.post("/api/forge/creator/prompt", json={"idea": "x", "endpoint": "custom", "base": "http://10.0.0.5/v1", "model": "m"})
    assert r.status_code == 503 and "on this computer" in r.json()["detail"]


def test_anchor_from_run_refuses_a_render_that_is_not_finished(client, monkeypatch):
    cid = client.post("/api/forge/characters", json={"name": "Maya"}).json()["character"]["id"]
    monkeypatch.setattr(client.app.state.jobs.store, "load", lambda j: {"id": j, "state": "RUNNING", "output_file": None})
    r = client.post(f"/api/forge/characters/{cid}/anchor-from-run", json={"job_id": "d" * 32})
    assert r.status_code == 422 and "finished" in r.json()["detail"]
