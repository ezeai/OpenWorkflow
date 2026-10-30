"""The LoRA stack through the routes: saved, checked against ComfyUI's list, and placed in the prompt a run sends."""
from pathlib import Path

from fastapi.testclient import TestClient

from openfield import api_forge, app as appmod

FIX = Path(__file__).parent / "fixtures" / "forge"


def test_a_saved_stack_is_placed_into_the_prompt_the_run_sends(tmp_path, monkeypatch):
    c = TestClient(appmod.create_app(tmp_path))
    wid = c.post("/api/forge/workflows?name=zimage.json", content=(FIX / "image_z_image_turbo.json").read_bytes()).json()["workflow"]["id"]
    monkeypatch.setattr(api_forge, "_known_loras", lambda request: {"style.safetensors"})
    r = c.put(f"/api/forge/workflows/{wid}/loras", json={"items": [{"name": "style.safetensors", "strength": 0.8}]})
    assert r.status_code == 200, r.text
    assert c.get(f"/api/forge/workflows/{wid}/loras").json()["items"][0]["name"] == "style.safetensors"
    refused = c.put(f"/api/forge/workflows/{wid}/loras", json={"items": [{"name": "missing.safetensors"}]})
    assert refused.status_code == 422 and "not in ComfyUI" in refused.json()["detail"]
    # a run needs a saved app; saving one from the generated spec keeps this test to the LoRA step only
    spec = c.get(f"/api/forge/workflows/{wid}/spec").json()
    assert c.put(f"/api/forge/workflows/{wid}/spec", json={"spec": spec["spec"], "base_revision": None}).status_code == 200
    sent = {}
    monkeypatch.setattr(c.app.state.jobs, "submit_app", lambda *a, **k: sent.update(prompt=a[2]) or {"id": "j" * 32, "state": "QUEUED", "kind": "app", "provider": "local", "notes": []})
    run = c.post(f"/api/forge/workflows/{wid}/run", json={"values": {}, "provider": "local"})
    assert run.status_code == 202, run.text
    loader = [n for n in sent["prompt"].values() if n["class_type"] == "LoraLoaderModelOnly"]
    assert len(loader) == 1 and loader[0]["inputs"]["lora_name"] == "style.safetensors"
    assert loader[0]["inputs"]["strength_model"] == 0.8


def test_the_graph_view_lists_nodes_and_links_without_editing(tmp_path):
    c = TestClient(appmod.create_app(tmp_path))
    wid = c.post("/api/forge/workflows?name=zimage.json", content=(FIX / "image_z_image_turbo.json").read_bytes()).json()["workflow"]["id"]
    g = c.get(f"/api/forge/workflows/{wid}/graph").json()
    assert g["format"] == "ui" and len(g["nodes"]) > 0 and len(g["links"]) > 0
    assert all(set(n) >= {"id", "type", "title", "state"} for n in g["nodes"])
    assert {lk["from"] for lk in g["links"]} <= {n["id"] for n in g["nodes"]} | {"-10", "-20"}
