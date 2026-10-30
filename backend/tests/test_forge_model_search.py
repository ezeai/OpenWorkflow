"""Searching ComfyUI's model lists: folders, the text filter, and marking what a workflow needs."""
from pathlib import Path

from fastapi.testclient import TestClient

from openfield import api_forge, app as appmod
from openfield.forge import object_info as oi

FIX = Path(__file__).parent / "fixtures" / "forge"


def _info_with(lists: dict):
    defs = {}
    for cls, (inp, choices) in lists.items():
        defs[cls] = oi.NodeDef(cls, [oi.InputSpec(inp, "COMBO", True, {}, choices)], [], [], False)
    return type("Info", (), {"defs": defs, "source": "live"})()


def test_search_lists_files_by_folder_and_text(tmp_path, monkeypatch):
    monkeypatch.setattr(api_forge, "_info", lambda request: _info_with({
        "LoraLoader": ("lora_name", ["style/soft_light.safetensors", "character/anna.safetensors"]),
        "VAELoader": ("vae_name", ["ae.safetensors"]),
    }))
    c = TestClient(appmod.create_app(tmp_path))
    all_rows = c.get("/api/forge/models").json()
    assert all_rows["count"] == 3
    loras = c.get("/api/forge/models", params={"folder": "loras"}).json()["items"]
    assert {r["file"] for r in loras} == {"style/soft_light.safetensors", "character/anna.safetensors"}
    found = c.get("/api/forge/models", params={"q": "ANNA"}).json()["items"]
    assert [r["file"] for r in found] == ["character/anna.safetensors"]
    assert found[0]["loaded_by"] == ["LoraLoader"]


def test_a_workflow_marks_the_files_it_needs(tmp_path, monkeypatch):
    c = TestClient(appmod.create_app(tmp_path))
    wid = c.post("/api/forge/workflows?name=zimage.json", content=(FIX / "image_z_image_turbo.json").read_bytes()).json()["workflow"]["id"]
    real = oi.load(tmp_path, None)   # the bundled node definitions the analysis needs
    lists = _info_with({"UNETLoader": ("unet_name", ["z_image_turbo_bf16.safetensors", "other.safetensors"])})
    real.defs.update(lists.defs)
    monkeypatch.setattr(api_forge, "_info", lambda request: real)
    rows = c.get("/api/forge/models", params={"workflow": wid}).json()["items"]
    needed = {r["file"]: r["needed"] for r in rows}
    assert needed.get("z_image_turbo_bf16.safetensors") is True
    assert needed.get("other.safetensors") is False


def test_unknown_workflow_is_404(tmp_path):
    c = TestClient(appmod.create_app(tmp_path))
    assert c.get("/api/forge/models", params={"workflow": "f" * 32}).status_code == 404
