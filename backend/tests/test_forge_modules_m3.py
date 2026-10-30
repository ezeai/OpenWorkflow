"""Milestone 3: module contracts over complete Forge workflows, and reuse after the source workflow is gone."""
import copy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openfield import app as appmod
from openfield.forge import module

FIX = Path(__file__).parent / "fixtures" / "forge"
ZIMAGE = FIX / "image_z_image_turbo.json"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Isolated: no live ComfyUI node list is read and no job can reach a real queue unless a test allows it."""
    from openfield import api_forge
    from openfield.forge import object_info as oi
    bundled_load = oi.load
    # The bundled definitions carry no model file lists, so model checks come out 'unknown' offline, as they should.
    monkeypatch.setattr(oi, "load", lambda home, client_get=None: bundled_load(home, None))
    monkeypatch.setitem(api_forge._INFO, "info", None)
    c = TestClient(appmod.create_app(tmp_path))

    def no_real_queue(*a, **k):
        raise AssertionError("a test tried to submit a real job")
    monkeypatch.setattr(c.app.state.jobs, "submit_app", no_real_queue)
    return c


def _import(c: TestClient) -> str:
    r = c.post("/api/forge/workflows?name=zimage.json", content=ZIMAGE.read_bytes())
    assert r.status_code == 200, r.text
    return r.json()["workflow"]["id"]


def _ids(c: TestClient, wid: str) -> dict:
    """Find real node ids in the converted prompt. Nothing here is hard-coded."""
    prompt = c.get(f"/api/forge/workflows/{wid}/api-prompt").json()["api_prompt"]
    by = {}
    for nid, n in prompt.items():
        by.setdefault(n["class_type"], []).append(nid)
    clip = [nid for nid in by.get("CLIPTextEncode", []) if isinstance(prompt[nid]["inputs"].get("text"), str)]
    sampler = by.get("KSampler", [])
    saver = by.get("SaveImage", [])
    assert clip and sampler and saver, sorted(by)
    return {"prompt": clip[0], "sampler": sampler[0], "save": saver[0]}


def _ports(ids: dict) -> dict:
    inputs = [
        {"name": "prompt", "type": "text", "node": ids["prompt"], "field": "text", "required": True, "label": "Prompt"},
        {"name": "seed", "type": "integer", "node": ids["sampler"], "field": "seed", "default": 7},
    ]
    outputs = [{"name": "image", "node": ids["save"], "label": "Image"}]
    return {"inputs": inputs, "outputs": outputs}


def _create(c: TestClient, wid: str, ids: dict, name="Hero shot") -> dict:
    body = {"workflow_id": wid, "name": name, "description": "test", **_ports(ids)}
    r = c.post("/api/forge/modules", json=body)
    assert r.status_code == 201, r.text
    return r.json()["module"]


# ---- contract checks -------------------------------------------------------------------

def _prompt_for_checks():
    return json.loads((Path(__file__).parents[2] / "docs" / "evidence" / "forge-m1" / "zimage-template-api-prompt.json").read_text())


def _ids_from_prompt(prompt):
    by = {}
    for nid, n in prompt.items():
        by.setdefault(n["class_type"], []).append(nid)
    clip = [nid for nid in by["CLIPTextEncode"] if isinstance(prompt[nid]["inputs"].get("text"), str)]
    return {"prompt": clip[0], "sampler": by["KSampler"][0], "save": by["SaveImage"][0]}


def test_valid_ports_pass_against_a_real_prompt():
    prompt = _prompt_for_checks()
    ids = _ids_from_prompt(prompt)
    p = _ports(ids)
    ins = [module.Port(**x) for x in p["inputs"]]
    outs = [module.OutputPort(**x) for x in p["outputs"]]
    assert module.check(ins, outs, prompt) == []


@pytest.mark.parametrize("mutate, expected", [
    (lambda p: p["inputs"][0].update(node="999"), "not in the workflow"),
    (lambda p: p["inputs"][0].update(field="no_such_field"), "does not have"),
    (lambda p: p["outputs"][0].update(node="999"), "not in the workflow"),
    (lambda p: p["inputs"].append(dict(p["inputs"][0])), "used twice"),
    (lambda p: p.update(outputs=[]), "at least one output"),
    (lambda p: p["inputs"][1].update(default="seven"), "default: must be a whole number"),
    (lambda p: p["inputs"].append({"name": "pick", "type": "select", "node": p["inputs"][1]["node"], "field": "sampler_name", "options": ["a", "b"], "default": "c"}), "not one of its options"),
    (lambda p: p["inputs"].append({"name": "Bad Name", "type": "text", "node": p["inputs"][0]["node"], "field": "text"}), "lowercase"),
])
def test_bad_ports_are_refused_with_a_reason(mutate, expected):
    prompt = _prompt_for_checks()
    ids = _ids_from_prompt(prompt)
    p = copy.deepcopy(_ports(ids))
    mutate(p)
    with pytest.raises(Exception) as ei:
        ins = [module.Port(**x) for x in p["inputs"]]
        outs = [module.OutputPort(**x) for x in p["outputs"]]
        problems = module.check(ins, outs, prompt)
        if problems:
            raise module.ModuleError("; ".join(problems))
    assert expected in str(ei.value), str(ei.value)


def test_output_must_be_an_image_save_node():
    prompt = _prompt_for_checks()
    ids = _ids_from_prompt(prompt)
    outs = [module.OutputPort(name="latent", node=ids["sampler"])]
    problems = module.check([], outs, prompt)
    assert any("must be a save or preview image node" in p for p in problems)


def test_linked_input_cannot_be_a_module_input():
    prompt = _prompt_for_checks()
    ids = _ids_from_prompt(prompt)
    linked = next((nid, k) for nid, n in prompt.items() for k, v in n["inputs"].items()
                  if isinstance(v, list) and len(v) == 2)
    ins = [module.Port(name="wired", type="text", node=linked[0], field=linked[1])]
    problems = module.check(ins, [module.OutputPort(name="image", node=ids["save"])], prompt)
    assert any("connected to another node" in p for p in problems)


# ---- prepare -----------------------------------------------------------------------------

def test_prepare_applies_values_and_leaves_snapshot_alone():
    prompt = _prompt_for_checks()
    ids = _ids_from_prompt(prompt)
    ins = [module.Port(**x) for x in _ports(ids)["inputs"]]
    before = copy.deepcopy(prompt)
    out = module.prepare(ins, prompt, {"prompt": "a red bridge", "seed": 99})
    assert out[ids["prompt"]]["inputs"]["text"] == "a red bridge"
    assert out[ids["sampler"]]["inputs"]["seed"] == 99
    assert prompt == before


def test_prepare_uses_defaults_and_refuses_bad_values():
    prompt = _prompt_for_checks()
    ids = _ids_from_prompt(prompt)
    ins = [module.Port(**x) for x in _ports(ids)["inputs"]]
    out = module.prepare(ins, prompt, {"prompt": "x"})
    assert out[ids["sampler"]]["inputs"]["seed"] == 7
    with pytest.raises(module.ModuleError, match="required"):
        module.prepare(ins, prompt, {"seed": 1})
    with pytest.raises(module.ModuleError, match="unknown input"):
        module.prepare(ins, prompt, {"prompt": "x", "steps": 4})
    with pytest.raises(module.ModuleError, match="whole number"):
        module.prepare(ins, prompt, {"prompt": "x", "seed": True})
    with pytest.raises(module.ModuleError, match="must be text"):
        module.prepare(ins, prompt, {"prompt": 5})


# ---- routes: create, reuse after the source is gone, versions ------------------------

def test_module_is_reusable_after_the_source_workflow_is_deleted(client, tmp_path):
    wid = _import(client)
    ids = _ids(client, wid)
    m = _create(client, wid, ids)
    mid = m["module_id"]
    before = client.post(f"/api/forge/modules/{mid}/prepare", json={"values": {"prompt": "a lighthouse", "seed": 5}})
    assert before.status_code == 200, before.text

    # Remove the source workflow entirely. The module must still prepare the same prompt.
    from openfield.forge.store import WorkflowStore
    store = WorkflowStore(tmp_path / "forge" / "workflows")
    store.delete(wid, store.get(wid)["name"])
    assert client.get(f"/api/forge/workflows/{wid}").status_code == 404

    after = client.post(f"/api/forge/modules/{mid}/prepare", json={"values": {"prompt": "a lighthouse", "seed": 5}})
    assert after.status_code == 200, after.text
    assert after.json()["prompt"] == before.json()["prompt"]
    assert after.json()["prompt"][ids["sampler"]]["inputs"]["seed"] == 5


def test_module_list_and_detail(client):
    wid = _import(client)
    ids = _ids(client, wid)
    m = _create(client, wid, ids, name="Alpha")
    lst = client.get("/api/forge/modules").json()["modules"]
    assert [x["module_id"] for x in lst] == [m["module_id"]]
    assert lst[0]["latest_version"] == 1 and lst[0]["inputs"] == 2 and lst[0]["outputs"] == 1
    d = client.get(f"/api/forge/modules/{m['module_id']}").json()
    assert d["versions"] == [1]
    assert "prompt" not in d["module"]
    assert [p["name"] for p in d["module"]["inputs"]] == ["prompt", "seed"]


def test_new_version_keeps_old_ports_and_is_numbered(client):
    wid = _import(client)
    ids = _ids(client, wid)
    m = _create(client, wid, ids)
    mid = m["module_id"]
    body = {"name": "Hero shot v2", "inputs": [{"name": "prompt", "type": "text", "node": ids["prompt"], "field": "text", "required": True}],
            "outputs": [{"name": "image", "node": ids["save"]}]}
    r = client.post(f"/api/forge/modules/{mid}/versions", json=body)
    assert r.status_code == 201, r.text
    assert r.json()["versions"] == [1, 2]
    v1 = client.get(f"/api/forge/modules/{mid}?version=1").json()["module"]
    v2 = client.get(f"/api/forge/modules/{mid}?version=2").json()["module"]
    assert [p["name"] for p in v1["inputs"]] == ["prompt", "seed"]
    assert [p["name"] for p in v2["inputs"]] == ["prompt"]
    assert v2["name"] == "Hero shot v2"
    assert client.get(f"/api/forge/modules/{mid}?version=9").status_code == 404


def test_saved_version_cannot_be_overwritten(client, tmp_path):
    wid = _import(client)
    ids = _ids(client, wid)
    mid = _create(client, wid, ids)["module_id"]
    store = module.ModuleStore(tmp_path / "forge" / "modules")
    c = store.get(mid, 1)
    with pytest.raises(Exception) as ei:
        store.save(c)
    assert "already exists" in str(ei.value)


def test_tampered_snapshot_is_detected(client, tmp_path):
    wid = _import(client)
    ids = _ids(client, wid)
    mid = _create(client, wid, ids)["module_id"]
    f = tmp_path / "forge" / "modules" / mid / "v1.json"
    data = json.loads(f.read_text(encoding="utf-8"))
    data["prompt"][ids["sampler"]]["inputs"]["steps"] = 999
    f.write_text(json.dumps(data), encoding="utf-8")
    r = client.post(f"/api/forge/modules/{mid}/prepare", json={"values": {"prompt": "x"}})
    assert r.status_code == 409, r.text


def test_unknown_module_is_404_and_bad_body_is_422(client):
    assert client.get("/api/forge/modules/" + "e" * 32).status_code == 404
    assert client.post("/api/forge/modules/" + "e" * 32 + "/prepare", json={"values": {}}).status_code == 404
    wid = _import(client)
    r = client.post("/api/forge/modules", json={"workflow_id": wid, "name": "x", "outputs": []})
    assert r.status_code == 422


# ---- model references and the run path (Milestone 3, step 2) -------------------------------

from openfield.forge import object_info as oi


def _fake_info(choices_by_class: dict):
    """A stand-in node list with real InputSpec objects. choices=None means the list was not available."""
    defs = {}
    for cls, (inp, choices) in choices_by_class.items():
        spec = oi.InputSpec(inp, "COMBO", True, {}, choices)
        defs[cls] = oi.NodeDef(cls, [spec], [], [], False)
    return type("Info", (), {"defs": defs})()


def test_model_status_follows_the_same_rules_as_analysis():
    refs = [
        {"node": "1", "input": "ckpt", "class_type": "Loader", "file": "a/model.safetensors"},
        {"node": "2", "input": "ckpt", "class_type": "Loader", "file": "x/dup.safetensors"},
        {"node": "3", "input": "ckpt", "class_type": "Loader", "file": "gone.safetensors"},
        {"node": "4", "input": "ckpt", "class_type": "Other", "file": "any.safetensors"},
    ]
    info = _fake_info({"Loader": ("ckpt", ["a/model.safetensors", "b/dup.safetensors", "c/dup.safetensors", "moved/model.safetensors"]),
                       "Other": ("ckpt", None)})
    st = {s["node"]: s for s in module.model_status(refs, info)}
    assert st["1"]["status"] == "available"
    assert st["2"]["status"] == "ambiguous"
    assert st["3"]["status"] == "missing"
    assert st["4"]["status"] == "unknown"
    one = _fake_info({"Loader": ("ckpt", ["moved/unique.safetensors", "a/model.safetensors"])})
    single = [{"node": "1", "input": "ckpt", "class_type": "Loader", "file": "dir/unique.safetensors"}]
    assert module.model_status(single, one)[0] == {**single[0], "status": "found_elsewhere", "comfy_name": "moved/unique.safetensors"}


def test_model_refs_are_stored_and_tampering_with_them_is_detected(client, tmp_path):
    wid = _import(client)
    ids = _ids(client, wid)
    mid = _create(client, wid, ids)["module_id"]
    store = module.ModuleStore(tmp_path / "forge" / "modules")
    c = store.get(mid, 1)
    assert len(c.model_refs) >= 1, "the Z-Image fixture names model files"
    f = tmp_path / "forge" / "modules" / mid / "v1.json"
    data = json.loads(f.read_text(encoding="utf-8"))
    data["model_refs"] = []
    f.write_text(json.dumps(data), encoding="utf-8")
    r = client.post(f"/api/forge/modules/{mid}/prepare", json={"values": {"prompt": "x"}})
    assert r.status_code == 409, r.text


def test_run_refuses_when_model_files_cannot_be_checked(client):
    wid = _import(client)
    ids = _ids(client, wid)
    mid = _create(client, wid, ids)["module_id"]
    r = client.post(f"/api/forge/modules/{mid}/run", json={"values": {"prompt": "x"}})
    assert r.status_code == 422, r.text
    assert "cannot be checked" in r.json()["detail"]


def test_run_submits_the_prepared_prompt_when_models_are_available(client, monkeypatch):
    wid = _import(client)
    ids = _ids(client, wid)
    mid = _create(client, wid, ids)["module_id"]
    calls = []

    def fake_submit(*args, **kw):
        calls.append((args, kw))
        return {"id": "j" * 32, "state": "QUEUED", "kind": "app", "provider": "local"}

    client.app.state.jobs.submit_app = fake_submit
    refs = module.ModuleStore(client.app.state.services.home / "forge" / "modules").get(mid).model_refs
    monkeypatch.setattr(module, "model_status", lambda rs, info: [{**r, "status": "available", "comfy_name": r["file"]} for r in rs])
    r = client.post(f"/api/forge/modules/{mid}/run", json={"values": {"prompt": "a lighthouse", "seed": 11}})
    assert r.status_code == 202, r.text
    assert r.json()["job"]["state"] == "QUEUED"
    (args, kw) = calls[0]
    sent_prompt, models, params, provenance = args[2], args[3], args[4], args[5]
    assert sent_prompt[ids["prompt"]]["inputs"]["text"] == "a lighthouse"
    assert sent_prompt[ids["sampler"]]["inputs"]["seed"] == 11
    assert all("_meta" not in n for n in sent_prompt.values())
    assert [m["file"] for m in models] == [r_["file"] for r_ in refs]
    assert provenance["module_id"] == mid and provenance["module_version"] == 1
    assert provenance["snapshot_sha256"] and provenance["workflow_sha256"]
    assert kw["provider"] == "local" and kw["consent"] is None


def test_run_cannot_go_to_cloud(client):
    wid = _import(client)
    ids = _ids(client, wid)
    mid = _create(client, wid, ids)["module_id"]
    r = client.post(f"/api/forge/modules/{mid}/run", json={"values": {"prompt": "x"}, "provider": "cloud"})
    assert r.status_code == 422
