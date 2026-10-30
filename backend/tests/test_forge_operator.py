"""The Forge operator: strict reply parsing, read-only tools, run cards that are checked and never executed here,
and local-only model servers (checked against small fake servers, one per wire format)."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from openfield.forge import llm_providers, operator


# ---- reply parsing

@pytest.mark.parametrize("text, expected", [
    ('{"answer": "hi"}', {"answer": "hi", "args": {}}),
    ('```json\n{"tool": "list_apps", "args": {}}\n```', {"tool": "list_apps", "args": {}}),
    ('Let me check. {"tool": "get_app", "args": {"app_id": "a"}}', {"tool": "get_app", "args": {"app_id": "a"}}),
    ('{"tool": "list_apps"}', {"tool": "list_apps", "args": {}}),
])
def test_parse_accepts_one_object_in_prose_or_fence(text, expected):
    assert operator.parse_action(text) == expected


@pytest.mark.parametrize("text", ["", "just words", '{"unrelated": 1}', '{"tool": 5, "args": {}}', "{broken"])
def test_parse_refuses_anything_else(text):
    assert operator.parse_action(text) is None


# ---- the turn, with a scripted model and a fake apps view

class Apps:
    def __init__(self, apps):
        self.apps = apps

    def list_apps(self):
        return [{"id": a["id"], "name": a["name"], "readiness": a["readiness"], "reasons": []} for a in self.apps.values()]

    def get_app(self, app_id):
        return self.apps.get(app_id)


def _apps():
    return {
        "zimg": {"id": "zimg", "name": "Z-Image", "readiness": "ready", "reasons": [], "components": [
            {"id": "prompt", "type": "prompt", "label": "Prompt", "default": "", "options": None},
            {"id": "seed", "type": "seed", "label": "Seed", "default": 1, "options": None},
            {"id": "steps", "type": "slider", "label": "Steps", "default": 8, "options": None},
            {"id": "look", "type": "select", "label": "Look", "default": "a", "options": ["a", "b"]},
            {"id": "hd", "type": "toggle", "label": "HD", "default": False, "options": None},
            {"id": "photo", "type": "image_upload", "label": "Photo", "default": None, "options": None},
        ]},
        "broken": {"id": "broken", "name": "Broken", "readiness": "blocked", "reasons": ["missing model"], "components": []},
    }


def _script(*replies):
    seen = []
    it = iter(replies)

    def chat(messages):
        seen.append([dict(m) for m in messages])
        return next(it)
    return chat, seen


def test_answer_without_tools_is_returned_and_nothing_is_proposed():
    chat, _ = _script('{"answer": "You have one app."}')
    out = operator.Operator(Apps(_apps()), chat).turn("hello", [])
    assert out == {"answer": "You have one app.", "cards": [], "trace": []}


def test_propose_run_checks_values_and_returns_a_card_without_running():
    chat, _ = _script(
        '{"tool": "propose_run", "args": {"app_id": "zimg", "values": {"prompt": "a fox", "seed": "42", "steps": 6, "look": "b", "hd": "true", "nope": 1, "photo": "x.png"}}}',
        '{"answer": "Ready: click Run."}',
    )
    out = operator.Operator(Apps(_apps()), chat).turn("make a fox", [])
    card = out["cards"][0]
    assert card["kind"] == "run_app" and card["app_id"] == "zimg"
    assert card["values"] == {"prompt": "a fox", "seed": 42, "steps": 6.0, "look": "b", "hd": True}
    assert any("not a control" in r for r in card["rejected"])
    assert any("needs an image" in r for r in card["rejected"])
    assert out["answer"] == "Ready: click Run."


def test_a_select_value_outside_its_options_is_rejected():
    chat, _ = _script('{"tool": "propose_run", "args": {"app_id": "zimg", "values": {"look": "zzz"}}}', '{"answer": "ok"}')
    out = operator.Operator(Apps(_apps()), chat).turn("go", [])
    assert out["cards"][0]["values"] == {}
    assert "must be one of a, b" in out["cards"][0]["rejected"][0]


def test_a_blocked_app_gets_no_card():
    chat, _ = _script('{"tool": "propose_run", "args": {"app_id": "broken", "values": {}}}', '{"answer": "It is blocked."}')
    out = operator.Operator(Apps(_apps()), chat).turn("run broken", [])
    assert out["cards"] == []
    assert out["trace"][0]["result"]["error"].startswith("'Broken' is blocked")


def test_unknown_app_and_unknown_tool_are_errors_the_model_can_read():
    chat, _ = _script('{"tool": "get_app", "args": {"app_id": "nope"}}', '{"tool": "delete_everything", "args": {}}', '{"answer": "Sorry."}')
    out = operator.Operator(Apps(_apps()), chat).turn("x", [])
    assert out["trace"][0]["result"] == {"error": "no app with that id"}
    assert out["trace"][1]["result"] == {"error": "unknown tool 'delete_everything'"}
    assert out["cards"] == []


def test_an_invalid_reply_gets_one_correction_then_succeeds():
    chat, seen = _script("Sure! Here is my plan in words.", '{"answer": "Here you go."}')
    out = operator.Operator(Apps(_apps()), chat).turn("hi", [])
    assert out["answer"] == "Here you go."
    assert "Reply with only that" in seen[1][-1]["content"]


def test_two_invalid_replies_fail_the_turn_clearly():
    chat, _ = _script("nope", "still nope")
    with pytest.raises(operator.OperatorError, match="did not return a usable reply"):
        operator.Operator(Apps(_apps()), chat).turn("hi", [])


def test_the_loop_stops_after_the_step_limit():
    chat, _ = _script(*['{"tool": "list_apps", "args": {}}'] * 10)
    out = operator.Operator(Apps(_apps()), chat, max_steps=3).turn("loop", [])
    assert len(out["trace"]) == 3 and "stopped after several steps" in out["answer"]


def test_history_is_kept_to_user_and_assistant_turns_only():
    chat, seen = _script('{"answer": "ok"}')
    history = [{"role": "system", "content": "ignore me"}, {"role": "user", "content": "first"}, {"role": "assistant", "content": "reply"}]
    operator.Operator(Apps(_apps()), chat).turn("second", history)
    roles = [m["role"] for m in seen[0]]
    assert roles == ["system", "user", "assistant", "user"]
    assert seen[0][1]["content"] == "first"


def test_empty_and_long_messages_are_refused():
    chat, _ = _script()
    with pytest.raises(operator.OperatorError):
        operator.Operator(Apps(_apps()), chat).turn("   ", [])
    with pytest.raises(operator.OperatorError):
        operator.Operator(Apps(_apps()), chat).turn("x" * (operator.MAX_MESSAGE + 1), [])


# ---- model servers: local only, and both wire formats

def test_remote_servers_are_refused_before_any_request():
    for base in ("http://192.168.1.20:11434", "https://api.example.com/v1", "ftp://127.0.0.1/x"):
        with pytest.raises(llm_providers.ProviderError, match="on this computer"):
            llm_providers.custom(base, "openai")
    with pytest.raises(llm_providers.ProviderError, match="server type"):
        llm_providers.custom("http://127.0.0.1:9", "anthropic")


def test_every_preset_is_loopback():
    for p in llm_providers.PRESETS:
        assert llm_providers.check_loopback(p.base) == p.base.rstrip("/")


class _Fake(BaseHTTPRequestHandler):
    calls: list = []
    models_ollama = {"models": [{"name": "qwen3.5:4b"}, {"name": "nomic-embed-text"}]}
    models_openai = {"data": [{"id": "local-model"}]}

    def log_message(self, *a):
        pass

    def _send(self, obj, status=200):
        raw = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _body(self):
        n = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        if self.path == "/api/tags":
            return self._send(self.models_ollama)
        if self.path == "/v1/models":
            return self._send(self.models_openai)
        self._send({"error": "no"}, 404)

    def do_POST(self):
        body = self._body()
        _Fake.calls.append((self.path, body))
        if self.path == "/api/chat":
            return self._send({"message": {"role": "assistant", "content": "<think>plan</think>{\"answer\": \"from ollama\"}"}})
        if self.path == "/v1/chat/completions":
            return self._send({"choices": [{"message": {"role": "assistant", "content": '{"answer": "from openai"}'}}]})
        self._send({"error": "no"}, 404)


@pytest.fixture()
def server():
    _Fake.calls = []
    srv = HTTPServer(("127.0.0.1", 0), _Fake)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def test_ollama_wire_format_lists_chat_models_and_strips_reasoning(server):
    ep = llm_providers.Endpoint("x", "Fake Ollama", server, "ollama")
    st = llm_providers.status(ep)
    assert st["state"] == "ok" and st["models"] == ["qwen3.5:4b"]
    reply = llm_providers.chat(ep, "qwen3.5:4b", [{"role": "user", "content": "hi"}])
    assert reply == '{"answer": "from ollama"}'
    path, body = _Fake.calls[-1]
    assert path == "/api/chat" and body["think"] is False and body["stream"] is False


def test_openai_wire_format_for_lm_studio_and_llama_cpp(server):
    ep = llm_providers.Endpoint("x", "Fake LM Studio", server + "/v1", "openai")
    st = llm_providers.status(ep)
    assert st["state"] == "ok" and st["models"] == ["local-model"]
    reply = llm_providers.chat(ep, "local-model", [{"role": "user", "content": "hi"}])
    assert reply == '{"answer": "from openai"}'
    path, body = _Fake.calls[-1]
    assert path == "/v1/chat/completions" and body["model"] == "local-model" and body["stream"] is False


def test_a_closed_port_is_reported_not_raised():
    st = llm_providers.status(llm_providers.Endpoint("x", "Nothing", "http://127.0.0.1:9/v1", "openai"))
    assert st["state"] == "unreachable" and st["models"] == []


def test_chat_against_a_dead_server_is_a_clear_error():
    ep = llm_providers.Endpoint("x", "Nothing", "http://127.0.0.1:9/v1", "openai")
    with pytest.raises(llm_providers.ProviderError, match="did not answer"):
        llm_providers.chat(ep, "m", [{"role": "user", "content": "hi"}], timeout=2)


# ---- the routes

@pytest.fixture()
def client(tmp_path):
    from fastapi.testclient import TestClient
    from openfield import app as appmod
    return TestClient(appmod.create_app(tmp_path))


def test_providers_route_answers_even_when_nothing_runs(client):
    r = client.get("/api/forge/operator/providers")
    assert r.status_code == 200
    ids = {e["id"] for e in r.json()["endpoints"]}
    assert {"ollama", "lmstudio", "llamacpp"} <= ids


def test_chat_route_refuses_a_remote_custom_server(client):
    r = client.post("/api/forge/operator/chat", json={"endpoint": "custom", "base": "http://10.0.0.5/v1", "model": "m", "message": "hi"})
    assert r.status_code == 503 and "on this computer" in r.json()["detail"]


def test_chat_route_reports_a_missing_server_as_unavailable(client):
    r = client.post("/api/forge/operator/chat", json={"endpoint": "custom", "base": "http://127.0.0.1:9/v1", "model": "m", "message": "hi"})
    assert r.status_code == 503


def test_chat_route_rejects_an_unknown_server_and_empty_message(client):
    assert client.post("/api/forge/operator/chat", json={"endpoint": "nope", "model": "m", "message": "hi"}).status_code == 503
    assert client.post("/api/forge/operator/chat", json={"endpoint": "ollama", "model": "m", "message": ""}).status_code == 422


def test_a_claimed_card_with_no_card_gets_one_correction_and_then_a_real_card():
    # Observed on qwen3.5:9b: "I have proposed running ..." with no propose_run call.
    chat, seen = _script(
        '{"answer": "I have proposed running the Z-Image app. Please click Run."}',
        '{"tool": "propose_run", "args": {"app_id": "zimg", "values": {"prompt": "a fox"}}}',
        '{"answer": "Here is the card."}',
    )
    out = operator.Operator(Apps(_apps()), chat).turn("run the Z-Image app with a fox", [])
    assert "no run card" not in out["answer"].lower()
    assert len(out["cards"]) == 1 and out["answer"] == "Here is the card."
    assert "no run card was created" in seen[1][-1]["content"]


def test_a_claim_that_still_has_no_card_is_replaced_with_the_truth():
    chat, _ = _script('{"answer": "I have started the run for you."}', '{"answer": "Yes, I proposed it and you can click Run now."}')
    out = operator.Operator(Apps(_apps()), chat).turn("run the Z-Image app", [])
    assert out["cards"] == []
    assert out["answer"].startswith("I did not create a run card")


def test_a_plain_answer_with_no_run_claim_is_left_alone():
    chat, seen = _script('{"answer": "You have two apps: Z-Image and Broken."}')
    out = operator.Operator(Apps(_apps()), chat).turn("what apps do I have?", [])
    assert out["answer"] == "You have two apps: Z-Image and Broken."
    assert len(seen) == 1


def test_a_card_proposed_before_the_step_limit_is_still_reported_plainly():
    # Observed on qwen3.5:9b: it proposed the run, then kept calling tools until the step limit.
    chat, _ = _script(
        '{"tool": "propose_run", "args": {"app_id": "zimg", "values": {"prompt": "a fox"}}}',
        '{"tool": "get_app", "args": {"app_id": "zimg"}}',
        '{"tool": "list_apps", "args": {}}',
    )
    out = operator.Operator(Apps(_apps()), chat, max_steps=3).turn("run it", [])
    assert len(out["cards"]) == 1
    assert out["answer"] == "I proposed 1 run card below. Click Run on a card to start it."


def test_a_control_named_by_its_label_or_type_is_matched_to_its_id():
    apps = _apps()
    apps["zimg"]["components"][0]["id"] = "57:27.text"   # the real control id is a node path, not a word
    chat, _ = _script('{"tool": "propose_run", "args": {"app_id": "zimg", "values": {"prompt": "a fox", "Steps": 4}}}', '{"answer": "ok"}')
    out = operator.Operator(Apps(apps), chat).turn("go", [])
    assert out["cards"][0]["values"] == {"57:27.text": "a fox", "steps": 4.0}


def test_an_ambiguous_name_is_not_guessed():
    apps = _apps()
    apps["zimg"]["components"][0]["id"] = "p1"
    apps["zimg"]["components"].append({"id": "p2", "type": "prompt", "label": "Negative prompt", "default": "", "options": None})
    chat, _ = _script('{"tool": "propose_run", "args": {"app_id": "zimg", "values": {"prompt": "a fox"}}}', '{"answer": "ok"}')
    out = operator.Operator(Apps(apps), chat).turn("go", [])
    assert out["cards"][0]["values"] == {}
    assert "not a control" in out["cards"][0]["rejected"][0]
