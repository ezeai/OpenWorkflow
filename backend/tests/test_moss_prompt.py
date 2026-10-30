"""The prompt helper runs on the MOSS text encoder: usable answers are used, anything else falls back to the template."""
import pytest
from fastapi.testclient import TestClient

from openfield import api_moss, app as appmod
from openfield.forge import moss_enhancer, moss_graph, moss_prompt


class FakeWorker:
    up = True
    text = None          # None = still writing; dict = result
    submitted = []

    def __init__(self, url=None):
        pass

    def status(self):
        return {"state": "ready" if FakeWorker.up else "not_running", "url": "x", "message": ""}

    def submit(self, graph, client_id):
        FakeWorker.submitted.append(graph)
        return "p1"

    def text_result(self, prompt_id):
        return FakeWorker.text


@pytest.fixture()
def client(tmp_path, monkeypatch):
    FakeWorker.up, FakeWorker.text, FakeWorker.submitted = True, None, []
    monkeypatch.setattr(api_moss, "MossWorker", FakeWorker)
    return TestClient(appmod.create_app(tmp_path))


GOOD = ("A glass perfume bottle with a brushed gold cap stands on polished black stone, lit by a single warm rim light "
        "that catches the glass, against a dark background with soft haze.")


def test_the_text_encoder_is_asked_for_the_prompt(client):
    r = client.post("/api/moss/enhance", json={"engine": "encoder", "idea": "perfume bottle, luxury"})
    assert r.status_code == 202
    graph = FakeWorker.submitted[0]
    assert graph["gen"]["class_type"] == "TextGenerate"
    assert graph["clip"]["inputs"]["type"] == "moss_image"
    assert "perfume bottle, luxury" in graph["gen"]["inputs"]["prompt"]


def test_a_usable_answer_is_returned_as_the_model_prompt(client):
    rid = client.post("/api/moss/enhance", json={"engine": "encoder", "idea": "perfume bottle"}).json()["request"]["id"]
    assert client.get(f"/api/moss/enhance/{rid}").json()["request"]["state"] == "running"
    FakeWorker.text = {"ok": True, "text": f"  {GOOD}  ", "messages": []}
    req = client.get(f"/api/moss/enhance/{rid}").json()["request"]
    assert req["state"] == "done" and req["source"] == "moss_text_encoder"
    assert req["prompt"] == GOOD and req["note"] is None


def test_an_unusable_answer_falls_back_to_the_template_with_a_note(client):
    rid = client.post("/api/moss/enhance", json={"engine": "encoder", "idea": "perfume bottle"}).json()["request"]["id"]
    FakeWorker.text = {"ok": True, "text": "ok", "messages": []}
    req = client.get(f"/api/moss/enhance/{rid}").json()["request"]
    assert req["source"] == "template" and "not a usable prompt" in req["note"]
    assert req["prompt"].startswith("perfume bottle")


def test_a_failed_text_job_falls_back_with_a_note(client):
    rid = client.post("/api/moss/enhance", json={"engine": "encoder", "idea": "perfume bottle"}).json()["request"]["id"]
    FakeWorker.text = {"ok": False, "text": None, "messages": [["execution_error", {}]]}
    req = client.get(f"/api/moss/enhance/{rid}").json()["request"]
    assert req["source"] == "template" and "did not return text" in req["note"]


def test_when_the_moss_server_is_down_the_template_is_immediate(client):
    FakeWorker.up = False
    req = client.post("/api/moss/enhance", json={"engine": "encoder", "idea": "perfume bottle"}).json()["request"]
    assert req["state"] == "done" and req["source"] == "template"
    assert "MOSS server is not running" in req["note"]
    assert FakeWorker.submitted == []


def test_an_empty_idea_is_refused_without_a_job(client):
    req = client.post("/api/moss/enhance", json={"engine": "encoder", "idea": "   "}).json()["request"]
    assert req["state"] == "done" and req["source"] == "none" and FakeWorker.submitted == []


def test_unknown_requests_are_404(client):
    assert client.get("/api/moss/enhance/" + "a" * 32).status_code == 404


def test_build_text_refuses_bad_lengths():
    with pytest.raises(moss_graph.GraphError):
        moss_graph.build_text("write", max_length=4)
    with pytest.raises(moss_graph.GraphError):
        moss_graph.build_text("   ")


def test_the_helper_uses_the_moss_enhancer_formula_and_its_sampling(client):
    from openfield.forge import moss_enhancer
    client.post("/api/moss/enhance", json={"engine": "encoder", "idea": "perfume bottle, luxury"})
    inputs = FakeWorker.submitted[0]["gen"]["inputs"]
    assert inputs["system_prompt"] == moss_enhancer.FORMULA
    assert inputs["sampling_mode"] == "on"
    assert inputs["sampling_mode.temperature"] == 0.7 and inputs["sampling_mode.top_k"] == 64
    assert inputs["max_length"] == moss_prompt.MAX_TOKENS


# ---- the fast helper: a small local chat model, with the model call replaced -------------

class FakeLLMResponse:
    def __init__(self, text):
        self._text = text

    def raise_for_status(self):
        return None

    def json(self):
        return {"message": {"content": self._text}}


def _fast_setup(monkeypatch, text=None, models=("qwen3:4b-instruct-2507-q8_0",), state="ok"):
    from openfield.forge import llm, moss_prompt as mp
    sent = {}
    monkeypatch.setattr(llm, "status", lambda url: {"state": state, "url": url, "models": list(models),
                                                    "reason": None if state == "ok" else "Ollama is not running on this computer."})

    def fake_post(url, json=None, timeout=None):
        sent["body"] = json
        if text is None:
            raise mp.httpx.ConnectError("refused")
        return FakeLLMResponse(text)
    monkeypatch.setattr(mp.httpx, "post", fake_post)
    return sent


def test_the_fast_helper_uses_the_formula_and_unloads_the_model(monkeypatch, client):
    sent = _fast_setup(monkeypatch, text=GOOD)
    r = client.post("/api/moss/enhance", json={"idea": "perfume bottle"}).json()["request"]
    assert r["state"] == "done" and r["source"] == "local_model" and r["prompt"] == GOOD
    body = sent["body"]
    assert body["keep_alive"] == 0 and body["think"] is False
    assert body["messages"][0]["content"] == moss_enhancer.FORMULA
    assert body["messages"][1]["content"] == "perfume bottle"
    assert FakeWorker.submitted == []   # the MOSS encoder is not used by the fast helper


def test_the_fast_helper_falls_back_when_ollama_is_down(monkeypatch, client):
    _fast_setup(monkeypatch, state="unreachable")
    r = client.post("/api/moss/enhance", json={"idea": "perfume bottle"}).json()["request"]
    assert r["source"] == "template" and "not running" in r["note"]


def test_the_fast_helper_falls_back_when_the_answer_is_unusable(monkeypatch, client):
    _fast_setup(monkeypatch, text="ok")
    r = client.post("/api/moss/enhance", json={"idea": "perfume bottle"}).json()["request"]
    assert r["source"] == "template" and "not a usable prompt" in r["note"]


def test_the_fast_helper_is_the_default_and_the_encoder_is_chosen_explicitly(monkeypatch, client):
    _fast_setup(monkeypatch, text=GOOD)
    fast_req = client.post("/api/moss/enhance", json={"idea": "perfume bottle"}).json()["request"]
    assert fast_req["state"] == "done" and fast_req["engine"] == "fast"
    enc_req = client.post("/api/moss/enhance", json={"engine": "encoder", "idea": "perfume bottle"}).json()["request"]
    assert enc_req["state"] == "running" and enc_req["engine"] == "encoder"
    assert client.post("/api/moss/enhance", json={"engine": "other", "idea": "x"}).status_code == 422
