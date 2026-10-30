"""Milestone 1: canonical workflow description. Real fixtures for both formats, plus malformed input."""
import json
from pathlib import Path

from openfield.forge import document

FIX = Path(__file__).parent / "fixtures" / "forge"
API = Path(__file__).parents[2] / "docs" / "evidence" / "forge-m1" / "zimage-template-api-prompt.json"


def _record(data: bytes, **extra) -> dict:
    import hashlib
    rec = {"id": "a" * 32, "name": "t", "origin": "test", "imported_at": "2026-10-27T00:00:00Z",
           "sha256": hashlib.sha256(data).hexdigest()}
    rec.update(extra)
    return rec


def _describe(data: bytes) -> dict:
    return document.describe(data, _record(data))


def test_native_ui_fixture_is_detected_and_valid():
    data = (FIX / "image_z_image_turbo.json").read_bytes()
    d = _describe(data)
    assert d["source"]["format"] == "ui"
    assert d["source"]["matches_record"] is True
    assert d["validation"]["state"] == "valid", d["validation"]
    assert d["counts"]["nodes"] == len(d["nodes"]) > 0
    assert all(n["scope"] == "graph" for n in d["nodes"]) or d["counts"]["subgraphs"] > 0


def test_subgraph_fixture_reports_subgraph_scopes():
    data = (FIX / "utility_z_image_turbo_2k_upscaler.app.json").read_bytes()
    d = _describe(data)
    assert d["source"]["format"] == "ui"
    assert d["validation"]["state"] == "valid", d["validation"]
    assert d["counts"]["subgraphs"] >= 1
    assert any(n["scope"].startswith("subgraph ") for n in d["nodes"])


def test_api_prompt_is_detected_and_described():
    data = API.read_bytes()
    d = _describe(data)
    assert d["source"]["format"] == "api"
    assert d["validation"]["state"] == "valid", d["validation"]
    assert d["counts"]["nodes"] == len(json.loads(data))
    assert all(n["state"] == "active" for n in d["nodes"])
    assert all(n["scope"] == "graph" for n in d["nodes"])


def test_api_wrapped_prompt_is_detected():
    inner = json.loads(API.read_bytes())
    data = json.dumps({"prompt": inner}).encode()
    d = _describe(data)
    assert d["source"]["format"] == "api_wrapped"
    assert d["validation"]["state"] == "valid", d["validation"]
    assert d["counts"]["nodes"] == len(inner)


def test_muted_and_bypassed_nodes_are_counted():
    wf = {"nodes": [
        {"id": 1, "type": "KSampler", "mode": 0, "inputs": [], "outputs": []},
        {"id": 2, "type": "SaveImage", "mode": 2, "inputs": [], "outputs": []},
        {"id": 3, "type": "VAEDecode", "mode": 4, "inputs": [], "outputs": []},
        {"id": 4, "type": "Note", "mode": 0, "inputs": [], "outputs": []},
    ], "links": []}
    d = _describe(json.dumps(wf).encode())
    assert d["counts"] == {"nodes": 4, "links": 0, "subgraphs": 0, "muted": 1, "bypassed": 1, "ui_only": 1}
    states = {n["id"]: n["state"] for n in d["nodes"]}
    assert states == {"1": "active", "2": "muted", "3": "bypassed", "4": "active"}


def test_dangling_link_is_reported_as_invalid():
    wf = {"nodes": [{"id": 1, "type": "KSampler", "mode": 0,
                     "inputs": [{"name": "model", "type": "MODEL", "link": 99}], "outputs": []}],
          "links": []}
    d = _describe(json.dumps(wf).encode())
    assert d["validation"]["state"] == "invalid"
    assert any("link 99" in p for p in d["validation"]["problems"])


def test_link_to_missing_node_is_reported():
    wf = {"nodes": [{"id": 1, "type": "KSampler", "mode": 0, "inputs": [], "outputs": []}],
          "links": [[5, 7, 0, 1, 0, "MODEL"]]}
    d = _describe(json.dumps(wf).encode())
    assert d["validation"]["state"] == "invalid"
    assert any("node 7" in p for p in d["validation"]["problems"])


def test_api_link_to_missing_node_is_invalid_not_a_crash():
    prompt = {"1": {"class_type": "KSampler", "inputs": {"model": ["9", 0]}}}
    d = _describe(json.dumps(prompt).encode())
    assert d["validation"]["state"] == "invalid"
    assert d["validation"]["problems"]


def test_unknown_top_level_keys_are_listed_not_dropped():
    wf = {"nodes": [], "links": [], "custom_vendor_block": {"x": 1}}
    d = _describe(json.dumps(wf).encode())
    assert d["editor_metadata"]["preserved_top_level_keys"] == ["custom_vendor_block"]


def test_app_builder_selection_and_version_are_reported():
    wf = {"nodes": [], "links": [], "version": 0.4, "extra": {"linearData": {"inputs": []}}}
    d = _describe(json.dumps(wf).encode())
    assert d["editor_metadata"]["version"] == "0.4"
    assert d["editor_metadata"]["has_app_builder_selection"] is True


def test_checksum_mismatch_is_flagged():
    data = (FIX / "image_z_image_turbo.json").read_bytes()
    d = document.describe(data, _record(data, sha256="0" * 64))
    assert d["source"]["matches_record"] is False


def test_document_route_returns_summary_and_404s_unknown(tmp_path):
    from fastapi.testclient import TestClient
    from openfield import app as appmod
    c = TestClient(appmod.create_app(tmp_path))
    r = c.post("/api/forge/workflows?name=zimage.json", content=(FIX / "image_z_image_turbo.json").read_bytes())
    assert r.status_code == 200, r.text
    wid = r.json()["workflow"]["id"]
    d = c.get(f"/api/forge/workflows/{wid}/document")
    assert d.status_code == 200, d.text
    body = d.json()
    assert body["source"]["format"] == "ui" and body["source"]["matches_record"] is True
    assert body["validation"]["state"] == "valid"
    assert c.get("/api/forge/workflows/" + "f" * 32 + "/document").status_code == 404


def test_hostile_inputs_are_invalid_and_do_not_raise():
    cases = [
        b"not json at all",
        b"\xff\xfe\x00 binary",
        b"[1, 2, 3]",
        b'{"hello": "world"}',
        b'{"nodes": "not a list"}',
        b'{"1": {"inputs": {}}}',
        b'{"1": {"class_type": "A", "inputs": [1,2]}}',
        b'{"nodes": [{"type": "X"}], "links": []}',
    ]
    for data in cases:
        d = _describe(data)
        assert d["validation"]["state"] == "invalid", data
        assert d["validation"]["problems"], data
