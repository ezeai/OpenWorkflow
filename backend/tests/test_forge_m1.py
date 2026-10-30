"""Forge Milestone 1: workflow import, UI-to-API conversion and analysis. No ComfyUI needed."""
import copy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openfield.forge import analysis, convert, object_info, workflow
from openfield.forge.store import ForgeError, WorkflowStore

FIX = Path(__file__).parent / "fixtures" / "forge"
ZIMAGE = FIX / "image_z_image_turbo.json"
UPSCALER = FIX / "utility_z_image_turbo_2k_upscaler.app.json"


@pytest.fixture(scope="module")
def info():
    i = object_info.load(Path("__no_such_home__"))
    assert i.source == "bundled" and "KSampler" in i.defs
    return i


def live_like(info, choices: dict[tuple[str, str], list[str]]):
    """A copy of the definitions with combo choices filled in, as a running ComfyUI would report them."""
    i = copy.deepcopy(info)
    i.source = "live"
    for (cls, name), values in choices.items():
        spec = i.defs[cls].input(name)
        spec.choices, spec.options = values, {k: v for k, v in spec.options.items() if k != "file_choices_removed"}
    return i


def ui(nodes, links, **extra):
    return {"nodes": nodes, "links": links, "version": 0.4, **extra}


def n(id, type, inputs=(), outputs=(), widgets=None, mode=0, **kw):
    return {"id": id, "type": type, "mode": mode, "inputs": list(inputs), "outputs": list(outputs), "widgets_values": widgets, **kw}


def link_in(name, type, link, widget=None):
    d = {"name": name, "type": type, "link": link}
    if widget:
        d["widget"] = {"name": widget}
    return d


def out(name, type, links):
    return {"name": name, "type": type, "links": links}


# ---------- format detection and parsing ----------

def test_detect_formats():
    assert workflow.detect_format(json.loads(ZIMAGE.read_text(encoding="utf-8"))) == "ui"
    assert workflow.detect_format({"1": {"class_type": "SaveImage", "inputs": {}}}) == "api"
    assert workflow.detect_format({"prompt": {"1": {"class_type": "X", "inputs": {}}}}) == "api_wrapped"
    assert workflow.detect_format([1, 2]) == "unsupported"
    assert workflow.detect_format({"hello": "world"}) == "unsupported"


def test_parse_keeps_ids_links_and_subgraphs():
    wf = workflow.parse_ui(json.loads(ZIMAGE.read_text(encoding="utf-8")))
    assert set(wf.root.nodes) >= {"9", "57"}
    assert wf.root.links["62"].origin_id == "57" and wf.root.links["62"].target_id == "9"
    (sub,) = wf.subgraphs.values()
    assert sub.name == "Text to Image (Z-Image-Turbo)" and "3" in sub.nodes
    assert [p.name for p in sub.inputs][:3] == ["text", "width", "height"]
    assert wf.problems == []


def test_missing_link_target_is_reported():
    wf = workflow.parse_ui(ui([n(1, "SaveImage", [link_in("images", "IMAGE", 5)], widgets=["x"])],
                              [[5, 99, 0, 1, 0, "IMAGE"]]))
    assert any("node 99, which does not exist" in p for p in wf.problems)


def test_link_to_unknown_link_id_is_reported():
    wf = workflow.parse_ui(ui([n(1, "SaveImage", [link_in("images", "IMAGE", 7)], widgets=["x"])], []))
    assert any("link 7, which does not exist" in p for p in wf.problems)


def test_malformed_node_rejected():
    with pytest.raises(workflow.WorkflowError):
        workflow.parse_ui(ui([{"type": "SaveImage"}], []))


def test_api_prompt_link_to_missing_node_rejected():
    with pytest.raises(workflow.WorkflowError):
        workflow.parse_api({"1": {"class_type": "SaveImage", "inputs": {"images": ["2", 0]}}})


# ---------- conversion ----------

def test_zimage_template_converts_to_the_m5_pipeline(info):
    wf = workflow.parse_ui(json.loads(ZIMAGE.read_text(encoding="utf-8")))
    c = convert.convert(wf, info)
    assert c.status == "ok", [p.as_dict() for p in c.problems]
    classes = sorted(v["class_type"] for v in c.prompt.values())
    assert classes == sorted(["SaveImage", "CLIPLoader", "VAELoader", "ConditioningZeroOut", "VAEDecode", "UNETLoader",
                              "CLIPTextEncode", "EmptySD3LatentImage", "ModelSamplingAuraFlow", "KSampler"])
    ks = c.prompt["57:3"]["inputs"]
    assert ks["seed"] == 0 and ks["steps"] == 8 and ks["cfg"] == 1 and ks["sampler_name"] == "res_multistep" and ks["denoise"] == 1
    assert ks["model"] == ["57:11", 0] and ks["positive"] == ["57:27", 0] and ks["negative"] == ["57:33", 0]
    assert c.prompt["9"]["inputs"]["images"] == ["57:8", 0]
    assert c.prompt["57:30"]["inputs"] == {"clip_name": "qwen_3_4b.safetensors", "type": "lumina2", "device": "default"}
    assert c.prompt["57:11"]["inputs"]["shift"] == 3          # the optional 'sampling' input was added later; it is left out


def test_widget_seed_control_value_is_skipped(info):
    wf = workflow.parse_ui(ui([n(1, "KSampler", widgets=[42, "fixed", 20, 7.5, "euler", "normal", 0.5])], []))
    c = convert.convert(wf, info)
    i = c.prompt["1"]["inputs"]
    assert (i["seed"], i["steps"], i["cfg"], i["sampler_name"], i["scheduler"], i["denoise"]) == (42, 20, 7.5, "euler", "normal", 0.5)


def test_muted_node_is_dropped_and_its_consumer_reports_the_gap(info):
    nodes = [n(1, "EmptyLatentImage", outputs=[out("LATENT", "LATENT", [1])], widgets=[512, 512, 1], mode=2),
             n(2, "VAEDecode", [link_in("samples", "LATENT", 1), link_in("vae", "VAE", None)], [out("IMAGE", "IMAGE", [2])]),
             n(3, "SaveImage", [link_in("images", "IMAGE", 2)], widgets=["x"])]
    c = convert.convert(workflow.parse_ui(ui(nodes, [[1, 1, 0, 2, 0, "LATENT"], [2, 2, 0, 3, 0, "IMAGE"]])), info)
    assert "1" not in c.prompt and "samples" not in c.prompt["2"]["inputs"]
    assert c.status == "blocked" and any("'samples'" in p.message for p in c.problems)


def test_bypassed_node_passes_its_matching_input_through(info):
    nodes = [n(1, "LoadImage", outputs=[out("IMAGE", "IMAGE", [1])], widgets=["a.png", "image"]),
             n(2, "ImageInvert", [link_in("image", "IMAGE", 1)], [out("IMAGE", "IMAGE", [2])], mode=4),
             n(3, "SaveImage", [link_in("images", "IMAGE", 2)], widgets=["x"])]
    c = convert.convert(workflow.parse_ui(ui(nodes, [[1, 1, 0, 2, 0, "IMAGE"], [2, 2, 0, 3, 0, "IMAGE"]])), info)
    assert "2" not in c.prompt and c.prompt["3"]["inputs"]["images"] == ["1", 0]
    assert c.prompt["1"]["inputs"] == {"image": "a.png"}   # the upload button value is not an input


def test_reroute_and_primitive_are_resolved(info):
    nodes = [n(1, "LoadImage", outputs=[out("IMAGE", "IMAGE", [1])], widgets=["a.png", "image"]),
             n(2, "Reroute", [link_in("", "*", 1)], [out("", "IMAGE", [2])]),
             n(4, "PrimitiveNode", outputs=[out("STRING", "STRING", [3])], widgets=["final"]),
             n(3, "SaveImage", [link_in("images", "IMAGE", 2), link_in("filename_prefix", "STRING", 3, widget="filename_prefix")], widgets=["x"])]
    links = [[1, 1, 0, 2, 0, "IMAGE"], [2, 2, 0, 3, 0, "IMAGE"], [3, 4, 0, 3, 1, "STRING"]]
    c = convert.convert(workflow.parse_ui(ui(nodes, links)), info)
    assert set(c.prompt) == {"1", "3"}
    assert c.prompt["3"]["inputs"] == {"images": ["1", 0], "filename_prefix": "final"}


def test_unknown_node_type_blocks(info):
    c = convert.convert(workflow.parse_ui(ui([n(1, "SomeCustomNode", widgets=[1])], [])), info)
    assert c.status == "blocked" and any("SomeCustomNode" in p.message for p in c.problems)


def test_no_output_node_blocks(info):
    c = convert.convert(workflow.parse_ui(ui([n(1, "EmptyLatentImage", widgets=[512, 512, 1])], [])), info)
    assert any("no output node" in p.message for p in c.problems)


def test_notes_are_not_sent(info):
    c = convert.convert(workflow.parse_ui(ui([n(1, "MarkdownNote", widgets=["hi"]), n(2, "SaveImage", widgets=["x"])], [])), info)
    assert set(c.prompt) == {"2"}


def test_dynamic_combo_values_get_dotted_names(info):
    c = convert.convert(workflow.parse_ui(ui([n(1, "SaveVideo", [link_in("video", "VIDEO", None)], widgets=["video/x", "auto", "auto"])], [])), info)
    i = c.prompt["1"]["inputs"]
    assert i["filename_prefix"] == "video/x" and i["format"] == "auto" and i["format.codec"] == "auto"


def test_extra_trailing_values_are_info_only(info):
    nodes = [n(1, "LoadImage", outputs=[out("IMAGE", "IMAGE", [1])], widgets=["a.png", "image"]),
             n(2, "SaveImage", [link_in("images", "IMAGE", 1)], widgets=["x", "editor-only"])]
    c = convert.convert(workflow.parse_ui(ui(nodes, [[1, 1, 0, 2, 0, "IMAGE"]])), info)
    assert c.status == "ok" and any(p.severity == "info" for p in c.problems)


def test_converter_never_changes_the_parsed_source(info):
    data = json.loads(ZIMAGE.read_text(encoding="utf-8"))
    before = json.dumps(data, sort_keys=True)
    convert.convert(workflow.parse_ui(data), info)
    assert json.dumps(data, sort_keys=True) == before


# ---------- analysis ----------

def test_zimage_analysis_controls_and_outputs(info):
    wf = workflow.parse_ui(json.loads(ZIMAGE.read_text(encoding="utf-8")))
    a = analysis.analyse(wf, convert.convert(wf, info), info)
    kinds = {c["id"]: c["inferred"]["kind"] for c in a["controls"]}
    assert kinds["57:27.text"] == "prompt"        # its zeroed copy feeds the negative side; that does not make it negative
    assert kinds["57:3.seed"] == "seed" and kinds["57:13.width"] == "width" and kinds["9.filename_prefix"] == "output_name"
    seed = next(c for c in a["controls"] if c["id"] == "57:3.seed")
    assert seed["fact"]["type"] == "INT" and seed["fact"]["constraints"]["min"] == 0 and seed["suggested"]["show"]
    assert a["outputs"] == [{"node": "9", "class_type": "SaveImage", "title": "SaveImage", "media": "image", "app_builder_selected": False}]
    assert a["task"] == {"family": "text-to-image", "rule": "media inputs and output node types", "inferred": True}
    assert {m["file"] for m in a["model_files"]} == {"qwen_3_4b.safetensors", "ae.safetensors", "z_image_turbo_bf16.safetensors"}
    assert all(m["status"] == "unknown" for m in a["model_files"])          # the bundled snapshot has no file lists
    assert a["readiness"] == "needs_attention"


def test_model_files_matched_by_basename_against_live_choices(info):
    live = live_like(info, {("UNETLoader", "unet_name"): ["z-Image-Turbo\\z_image_turbo_bf16.safetensors"],
                            ("CLIPLoader", "clip_name"): ["qwen_3_4b.safetensors"],
                            ("VAELoader", "vae_name"): ["a\\ae.safetensors", "b\\ae.safetensors"]})
    wf = workflow.parse_ui(json.loads(ZIMAGE.read_text(encoding="utf-8")))
    a = analysis.analyse(wf, convert.convert(wf, live), live)
    st = {m["file"]: (m["status"], m["comfy_name"]) for m in a["model_files"]}
    assert st["z_image_turbo_bf16.safetensors"] == ("found_elsewhere", "z-Image-Turbo\\z_image_turbo_bf16.safetensors")
    assert st["qwen_3_4b.safetensors"] == ("available", "qwen_3_4b.safetensors")
    assert st["ae.safetensors"][0] == "ambiguous"
    assert a["readiness"] == "blocked" and any("several installed files" in r for r in a["reasons"])


def test_app_builder_selection_is_imported_as_a_hint(info):
    wf = workflow.parse_ui(json.loads(UPSCALER.read_text(encoding="utf-8")))
    a = analysis.analyse(wf, convert.convert(wf, info), info)
    assert a["app_builder"] and a["task"]["family"] == "image-to-image"
    picked = {c["id"] for c in a["controls"] if c["app_builder_selected"]}
    assert picked == {"77.image", "87:69.denoise"}
    assert any(o["media"] == "comparison" for o in a["outputs"])


# ---------- store and routes ----------

def test_store_keeps_exact_bytes_and_rejects_non_workflows(tmp_path):
    s = WorkflowStore(tmp_path)
    raw = ZIMAGE.read_bytes()
    rec = s.add(raw, "image_z_image_turbo.json", "upload")
    assert rec["name"] == "image_z_image_turbo" and rec["format"] == "ui"
    assert (tmp_path / rec["id"] / "source.json").read_bytes() == raw
    with pytest.raises(ForgeError):
        s.add(b'{"hello": 1}', "x.json", "upload")
    with pytest.raises(ForgeError):
        s.add(b"not json", "x.json", "upload")
    with pytest.raises(ForgeError):
        s.get("../../etc")


def test_store_detects_changed_source(tmp_path):
    s = WorkflowStore(tmp_path)
    rec = s.add(ZIMAGE.read_bytes(), "z", "upload")
    (tmp_path / rec["id"] / "source.json").write_bytes(b'{"nodes": []}')
    with pytest.raises(ForgeError):
        s.source(rec["id"])


def test_delete_needs_exact_name(tmp_path):
    s = WorkflowStore(tmp_path)
    rec = s.add(ZIMAGE.read_bytes(), "Z", "upload")
    with pytest.raises(ForgeError):
        s.delete(rec["id"], "z")
    s.delete(rec["id"], "Z")
    assert s.list() == []


def test_routes_import_and_analyse(tmp_path):
    from openfield import app as appmod
    c = TestClient(appmod.create_app(tmp_path))
    r = c.post("/api/forge/workflows?name=zimage.json", content=ZIMAGE.read_bytes())
    assert r.status_code == 200, r.text
    wid = r.json()["workflow"]["id"]
    assert [w["id"] for w in c.get("/api/forge/workflows").json()["workflows"]] == [wid]
    a = c.get(f"/api/forge/workflows/{wid}/analysis").json()
    assert a["analysis"]["task"]["family"] == "text-to-image"
    assert a["analysis"]["node_definitions"]["source"] in {"live", "cache", "bundled"}
    p = c.get(f"/api/forge/workflows/{wid}/api-prompt").json()["api_prompt"]
    assert p["9"]["class_type"] == "SaveImage"
    assert c.post("/api/forge/workflows", content=b"[1,2]").status_code == 400
    assert c.get("/api/forge/workflows/nothex/analysis").status_code == 404
    assert c.post("/api/forge/templates/import", json={"name": "../secrets"}).status_code == 404
