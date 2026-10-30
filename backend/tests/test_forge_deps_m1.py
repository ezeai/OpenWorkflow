"""Forge dependency analysis, milestone 1: which node classes a workflow needs, what ComfyUI registers, and
candidate packs from Manager's mapping. No installation, no network, no node code is run here."""
import copy
import dataclasses
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openfield import api_forge
from openfield.app import create_app
from openfield.forge import analysis, convert, deps, object_info, workflow

FIX = Path(__file__).parent / "fixtures" / "forge"
ZIMAGE = FIX / "image_z_image_turbo.json"
MANAGER_ROOT = Path("D:/ComfyUI_windows_portable_nvidia_cu128_py310/ComfyUI_windows_portable")


def bundled():
    return object_info.load(Path("__no_such_home__"))


def live(extra: dict | None = None):
    i = copy.deepcopy(bundled())
    i.source = "live"
    for cls, base in (extra or {}).items():
        i.defs[cls] = dataclasses.replace(i.defs[base], name=cls)
    return i


def analyse(path: Path, info, index=None):
    wf = workflow.parse_ui(json.loads(path.read_text(encoding="utf-8")))
    conv = convert.convert(wf, info)
    return analysis.analyse(wf, conv, info, {"pack_index": index, "custom_nodes_folders": 0})


def write_manager_map(root: Path, data: dict) -> Path:
    p = root / "ComfyUI" / "custom_nodes" / "comfyui-manager"
    p.mkdir(parents=True)
    (p / "extension-node-map.json").write_text(json.dumps(data), encoding="utf-8")
    return root


# ---------- which nodes a run needs ----------

def test_required_nodes_expand_subgraphs_and_skip_muted_bypassed_and_ui_only():
    wf = workflow.parse_ui(json.loads(ZIMAGE.read_text(encoding="utf-8")))
    req = deps.required_nodes(wf, None)
    classes = {r["class"] for r in req}
    assert "KSampler" in classes and "SaveImage" in classes
    assert not any(c.startswith("f2fdebf6") for c in classes)         # the subgraph wrapper is not a class
    assert not (classes & deps.UI_ONLY)
    inner = next(r for r in req if r["class"] == "KSampler")
    assert inner["node"] == "57:3" and inner["scope"] != "graph"       # reached through the subgraph


def test_muted_and_bypassed_nodes_are_not_required():
    nodes = [
        {"id": 1, "type": "LoadImage", "mode": 0, "inputs": [], "outputs": [], "widgets_values": ["a.png", "image"]},
        {"id": 2, "type": "SomeMuted", "mode": 2, "inputs": [], "outputs": [], "widgets_values": []},
        {"id": 3, "type": "SomeBypassed", "mode": 4, "inputs": [], "outputs": [], "widgets_values": []},
    ]
    wf = workflow.parse_ui({"nodes": nodes, "links": []})
    assert [r["class"] for r in deps.required_nodes(wf, None)] == ["LoadImage"]


def test_api_prompt_classes_are_used_when_there_is_no_editor_file():
    prompt = {"1": {"class_type": "KSampler", "inputs": {}}, "2": {"class_type": "AddLabel", "inputs": {}}}
    assert {r["class"] for r in deps.required_nodes(None, prompt)} == {"KSampler", "AddLabel"}


# ---------- availability ----------

def test_core_class_is_available_from_the_snapshot():
    a = analyse(ZIMAGE, bundled())
    d = a["dependencies"]
    ks = next(r for r in d["requirements"] if r["class"] == "KSampler")
    assert ks["available"] is True and ks["status"] == "core" and ks["availability_source"] == "snapshot"
    assert ks["installed"] is None and ks["verification"] == "not verified"


def test_unknown_class_is_unchecked_when_comfyui_is_not_running():
    nodes = [{"id": 1, "type": "AddLabel", "mode": 0, "inputs": [], "outputs": [], "widgets_values": []},
             {"id": 2, "type": "SaveImage", "mode": 0, "inputs": [], "outputs": [], "widgets_values": ["x"]}]
    wf = workflow.parse_ui({"nodes": nodes, "links": []})
    info = bundled()
    d = deps.build(deps.required_nodes(wf, None), info, None, None)
    row = next(r for r in d["requirements"] if r["class"] == "AddLabel")
    assert row["available"] is None and row["status"] == "unchecked"
    assert "not running" in d["headline"]


def test_unregistered_class_is_missing_when_comfyui_is_running():
    nodes = [{"id": 1, "type": "AddLabel", "mode": 0, "inputs": [], "outputs": [], "widgets_values": []}]
    wf = workflow.parse_ui({"nodes": nodes, "links": []})
    d = deps.build(deps.required_nodes(wf, None), live(), None, None)
    row = d["requirements"][0]
    assert row["available"] is False and row["status"] == "missing" and row["confidence"] == "unknown"


def test_registered_class_is_registered_only_with_a_live_check():
    nodes = [{"id": 1, "type": "AddLabel", "mode": 0, "inputs": [], "outputs": [], "widgets_values": []}]
    wf = workflow.parse_ui({"nodes": nodes, "links": []})
    d = deps.build(deps.required_nodes(wf, None), live({"AddLabel": "KSampler"}), None, None)
    row = d["requirements"][0]
    assert row["status"] == "registered" and row["verification"] == "registered in the running ComfyUI"
    assert row["installed"] is None                       # registration is not an install record


# ---------- candidate packs ----------

def test_single_candidate_is_likely_not_verified(tmp_path):
    root = write_manager_map(tmp_path, {"https://github.com/a/pack": [["Y"], {"title_aux": "Pack A"}]})
    idx = deps.load_pack_index(str(root))
    d = deps.build([{"class": "Y", "node": "1", "title": None, "scope": "graph"}], live(), idx, None)
    row = d["requirements"][0]
    assert row["status"] == "candidate" and row["confidence"] == "likely"
    assert row["candidates"][0]["repository"] == "https://github.com/a/pack"
    assert row["installed"] is None


def test_several_packs_for_one_class_are_ambiguous_and_all_shown(tmp_path):
    root = write_manager_map(tmp_path, {
        "https://github.com/a/one": [["X"], {"title_aux": "One"}],
        "https://github.com/b/two": [["X", "Z"], {"title_aux": "Two"}],
    })
    idx = deps.load_pack_index(str(root))
    d = deps.build([{"class": "X", "node": "1", "title": None, "scope": "graph"}], live(), idx, None)
    row = d["requirements"][0]
    assert row["status"] == "choose" and row["confidence"] == "ambiguous" and row["candidate_count"] == 2
    assert {c["repository"] for c in row["candidates"]} == {"https://github.com/a/one", "https://github.com/b/two"}


def test_a_class_with_no_pack_has_no_candidates(tmp_path):
    root = write_manager_map(tmp_path, {"https://github.com/a/pack": [["Y"], {}]})
    idx = deps.load_pack_index(str(root))
    d = deps.build([{"class": "Nothing", "node": "1", "title": None, "scope": "graph"}], live(), idx, None)
    assert d["requirements"][0]["status"] == "missing" and d["requirements"][0]["candidates"] == []


def test_malformed_mapping_entries_are_skipped(tmp_path):
    root = write_manager_map(tmp_path, {"https://x/y": "not a list", "https://x/z": [["Ok"], {}], "https://x/w": [[1, None], {}]})
    idx = deps.load_pack_index(str(root))
    assert set(idx) == {"Ok"}


def test_missing_mapping_file_is_reported_not_guessed(tmp_path):
    assert deps.load_pack_index(str(tmp_path)) is None
    assert deps.load_pack_index(None) is None
    d = deps.build([{"class": "Y", "node": "1", "title": None, "scope": "graph"}], live(), None, None)
    assert d["requirements"][0]["candidates"] == [] and "not found" in d["pack_index"]


def test_candidate_lookup_makes_no_network_calls(tmp_path, monkeypatch):
    import httpx

    def boom(*a, **k):
        raise AssertionError("network call")
    monkeypatch.setattr(httpx, "get", boom)
    monkeypatch.setattr(httpx, "post", boom)
    root = write_manager_map(tmp_path, {"https://github.com/a/pack": [["Y"], {}]})
    idx = deps.load_pack_index(str(root))
    deps.build([{"class": "Y", "node": "1", "title": None, "scope": "graph"}], live(), idx, None)


def test_real_manager_mapping_names_a_known_class():
    idx = deps.load_pack_index(str(MANAGER_ROOT))
    if idx is None:
        pytest.skip("ComfyUI Manager mapping is not present on this machine")
    assert any(c["repository"] == "https://github.com/kijai/ComfyUI-KJNodes" for c in idx.get("AddLabel", []))


# ---------- route and analysis ----------

def test_analysis_route_reports_dependencies(tmp_path, monkeypatch):
    monkeypatch.setattr(api_forge, "_info", lambda request: bundled())
    c = TestClient(create_app(tmp_path / "home"))
    wid = c.post("/api/forge/workflows?name=z.json", content=ZIMAGE.read_bytes()).json()["workflow"]["id"]
    d = c.get(f"/api/forge/workflows/{wid}/analysis").json()["analysis"]["dependencies"]
    assert d["node_classes_needed"] >= 5 and d["availability_source"] == "bundled"
    assert d["counts"]["core"] >= 5 and d["counts"]["missing"] == 0
    assert all(r["installed"] is None for r in d["requirements"])
    assert d["notes"] and "Nothing is installed" in d["notes"][1]
