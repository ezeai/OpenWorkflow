"""Forge Milestone 2: UI spec schema, deterministic generation, binding validation, value application, routes."""
import copy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openfield.forge import analysis, convert, object_info, uispec, workflow

FIX = Path(__file__).parent / "fixtures" / "forge"
REC = {"id": "a" * 32, "sha256": "b" * 64, "name": "Test app"}


@pytest.fixture(scope="module")
def info():
    return object_info.load(Path("__no_such_home__"))


def analyse_fixture(info, name):
    wf = workflow.parse_ui(json.loads((FIX / name).read_text(encoding="utf-8")))
    conv = convert.convert(wf, info)
    return analysis.analyse(wf, conv, info), conv.prompt


@pytest.fixture(scope="module")
def zimage(info):
    return analyse_fixture(info, "image_z_image_turbo.json")


def test_generation_is_deterministic_and_valid(zimage):
    a, _ = zimage
    s1, s2 = uispec.generate(REC, a), uispec.generate(REC, a)
    assert s1 == s2
    v = uispec.validate(s1, REC, a)
    assert v["status"] == "valid" and v["counts"]["invalid"] == 0
    by_id = {c.binding.id: c for c in s1.components}
    assert by_id["57:27.text"].type == "prompt" and by_id["57:27.text"].group == "main"
    assert by_id["57:3.seed"].type == "seed" and by_id["57:13.width"].type == "number"
    assert by_id["57:3.cfg"].type == "slider" and by_id["57:3.cfg"].group == "advanced"
    assert by_id["57:3.sampler_name"].type == "select" and "euler" in by_id["57:3.sampler_name"].props.choices
    assert "9.filename_prefix" not in by_id            # hidden inputs are not offered
    assert s1.layout == "simple_generator" and s1.outputs[0].media == "image"
    assert not any("safetensors" in str(c.props.default) for c in s1.components)   # model files are not controls


def test_image_input_workflow_uses_reference_layout(info):
    a, _ = analyse_fixture(info, "utility_z_image_turbo_2k_upscaler.app.json")
    s = uispec.generate(REC, a)
    assert s.layout == "reference_guided" and s.components[0].type == "image_upload"
    v = uispec.validate(s, REC, a)
    img = next(r for r in v["components"] if r["component"] == "77.image")
    assert img["status"] == "verified"             # image inputs are checked by their node definition; the file is checked on upload
    assert v["status"] == "valid"


def test_spec_round_trips_through_json(zimage):
    s = uispec.generate(REC, zimage[0])
    assert uispec.parse(json.loads(s.model_dump_json())) == s


def test_newer_schema_and_bad_shapes_refused(zimage):
    d = uispec.generate(REC, zimage[0]).model_dump(mode="json")
    with pytest.raises(ValueError, match="not supported"):
        uispec.parse({**d, "schema_version": 99})
    with pytest.raises(ValueError):
        uispec.parse({**d, "components": [{**d["components"][0], "type": "run_shell"}]})
    with pytest.raises(ValueError, match="used twice"):
        uispec.parse({**d, "components": [d["components"][0], d["components"][0]]})
    with pytest.raises(ValueError):
        uispec.parse({**d, "components": [{**d["components"][0], "id": "bad id!"}]})
    with pytest.raises(ValueError):
        uispec.parse([1])


def test_binding_to_a_node_that_does_not_exist_is_invalid(zimage):
    a, _ = zimage
    s = uispec.generate(REC, a)
    s.components[0].binding = uispec.Binding(node="999", input="text")
    v = uispec.validate(s, REC, a)
    assert v["status"] == "invalid" and "no editable input" in v["components"][0]["reasons"][0]


def test_binding_to_a_model_file_input_is_invalid(zimage):
    """A model loader's file name is not an editable control, so a spec cannot expose or change it."""
    a, _ = zimage
    s = uispec.generate(REC, a)
    s.components[0].binding = uispec.Binding(node="57:28", input="unet_name")
    assert uispec.validate(s, REC, a)["components"][0]["status"] == "invalid"


def test_type_mismatch_and_widened_limits_are_invalid(zimage):
    a, _ = zimage
    s = uispec.generate(REC, a)
    seed = next(c for c in s.components if c.type == "seed")
    seed.type = "toggle"
    steps = next(c for c in s.components if c.binding.id == "57:3.steps")
    steps.props.max = 1_000_000
    v = {r["component"]: r for r in uispec.validate(s, REC, a)["components"]}
    assert v[seed.id]["status"] == "invalid" and "cannot set a INT" in v[seed.id]["reasons"][0]
    assert v[steps.id]["status"] == "invalid" and "outside" in v[steps.id]["reasons"][0]


def test_seed_limit_survives_a_browser_round_trip(zimage):
    """JavaScript cannot hold 2^64-1; the same limit sent back as a float must still validate."""
    a, _ = zimage
    s = uispec.generate(REC, a)
    seed = next(c for c in s.components if c.type == "seed")
    seed.props.max = float(seed.props.max)
    assert uispec.validate(s, REC, a)["status"] == "valid"
    seed.props.max = seed.props.max * 4                     # a real widening is still caught
    assert uispec.validate(s, REC, a)["status"] == "invalid"


def test_select_offering_unknown_values_is_invalid(zimage):
    a, _ = zimage
    s = uispec.generate(REC, a)
    sel = next(c for c in s.components if c.binding.id == "57:3.scheduler")
    sel.props.choices = [*sel.props.choices, "not_a_scheduler"]
    assert uispec.validate(s, REC, a)["status"] == "invalid"


def test_duplicate_binding_and_wrong_workflow_are_reported(zimage):
    a, _ = zimage
    s = uispec.generate(REC, a)
    dup = s.components[0].model_copy(update={"id": "dup"})
    s.components.append(dup)
    assert any("more than one component" in p for p in uispec.validate(s, REC, a)["problems"])
    other = {**REC, "sha256": "c" * 64}
    assert any("different version" in p for p in uispec.validate(uispec.generate(REC, a), other, a)["problems"])


def test_apply_values_sets_only_bound_inputs(zimage):
    a, prompt = zimage
    s = uispec.generate(REC, a)
    v = uispec.validate(s, REC, a)
    ids = {c.binding.id: c.id for c in s.components}
    before = copy.deepcopy(prompt)
    out = uispec.apply_values(s, v, a, prompt, {ids["57:27.text"]: "a red fox", ids["57:13.width"]: 640, ids["57:3.cfg"]: 2.5, ids["57:3.seed"]: 123})
    assert out["57:27"]["inputs"]["text"] == "a red fox" and out["57:13"]["inputs"]["width"] == 640
    assert out["57:3"]["inputs"]["cfg"] == 2.5 and out["57:3"]["inputs"]["seed"] == 123
    assert prompt == before                                                     # the original is untouched
    changed = {(n, k) for n in out for k in out[n]["inputs"] if out[n]["inputs"][k] != before[n]["inputs"][k]}
    assert changed == {("57:27", "text"), ("57:13", "width"), ("57:3", "cfg"), ("57:3", "seed")}


@pytest.mark.parametrize("cid, value, message", [
    ("57:13.width", "wide", "whole number"),
    ("57:13.width", 10**9, "outside"),
    ("57:3.cfg", "high", "number"),
    ("57:3.cfg", 1000.0, "outside"),
    ("57:3.sampler_name", "no_such_sampler", "not one of"),
    ("57:27.text", 5, "text"),
    ("57:27.text", "x" * 20001, "too long"),
    ("57:3.seed", True, "whole number"),
])
def test_apply_values_rejects_bad_values(zimage, cid, value, message):
    a, prompt = zimage
    s = uispec.generate(REC, a)
    v = uispec.validate(s, REC, a)
    comp = next(c for c in s.components if c.binding.id == cid)
    with pytest.raises(uispec.ApplyError, match=message):
        uispec.apply_values(s, v, a, prompt, {comp.id: value})


def test_apply_values_rejects_unknown_and_invalid_components(zimage):
    a, prompt = zimage
    s = uispec.generate(REC, a)
    with pytest.raises(uispec.ApplyError, match="unknown component"):
        uispec.apply_values(s, uispec.validate(s, REC, a), a, prompt, {"57:28.unet_name": "x"})
    s.components[0].binding = uispec.Binding(node="999", input="text")
    with pytest.raises(uispec.ApplyError, match="invalid"):
        uispec.apply_values(s, uispec.validate(s, REC, a), a, prompt, {s.components[0].id: "x"})


def _client(tmp_path):
    from openfield import app as appmod
    c = TestClient(appmod.create_app(tmp_path))
    wid = c.post("/api/forge/workflows?name=z.json", content=(FIX / "image_z_image_turbo.json").read_bytes()).json()["workflow"]["id"]
    return c, wid


def test_routes_generate_save_and_reload(tmp_path):
    c, wid = _client(tmp_path)
    g = c.get(f"/api/forge/workflows/{wid}/spec").json()
    assert g["source"] == "generated" and g["revision"] == 0 and g["validation"]["status"] in {"valid", "needs_attention"}
    assert set(g["catalog"]) == set(uispec.CATALOG) and "run_shell" not in g["catalog"]
    spec = g["spec"]
    spec["components"][0]["label"] = "Describe the image"
    r = c.put(f"/api/forge/workflows/{wid}/spec", json={"spec": spec, "base_revision": 0})
    assert r.status_code == 200 and r.json()["revision"] == 1
    again = c.get(f"/api/forge/workflows/{wid}/spec").json()
    assert again["source"] == "saved" and again["spec"]["components"][0]["label"] == "Describe the image" and again["spec"]["generated_by"] == "user"
    assert c.put(f"/api/forge/workflows/{wid}/spec", json={"spec": spec, "base_revision": 0}).status_code == 409   # stale save
    reg = c.post(f"/api/forge/workflows/{wid}/spec/regenerate").json()
    assert reg["spec"]["components"][0]["label"] == "Prompt"
    assert c.get(f"/api/forge/workflows/{wid}/spec").json()["spec"]["components"][0]["label"] == "Describe the image"   # regenerate does not save


def test_route_refuses_to_save_invalid_bindings(tmp_path):
    c, wid = _client(tmp_path)
    spec = c.get(f"/api/forge/workflows/{wid}/spec").json()["spec"]
    spec["components"][0]["binding"] = {"node": "57:28", "input": "unet_name"}
    r = c.put(f"/api/forge/workflows/{wid}/spec", json={"spec": spec})
    assert r.status_code == 422 and "invalid bindings" in r.json()["detail"]
    assert c.get(f"/api/forge/workflows/{wid}/spec").json()["source"] == "generated"       # nothing was saved
    v = c.post(f"/api/forge/workflows/{wid}/spec/validate", json={"spec": spec}).json()["validation"]
    assert v["status"] == "invalid"
    assert c.put(f"/api/forge/workflows/{wid}/spec", json={"spec": {**spec, "schema_version": 9}}).status_code == 422
    assert c.get("/api/forge/workflows/" + "0" * 32 + "/spec").status_code == 404
