"""Optional-install models: guidance, detection of user-placed files, and the rule that nothing is downloaded.

Synthetic placeholder files only. No model weights are fetched or created by these tests.
"""
import struct
from pathlib import Path

import pytest

from openfield.models.catalog import load_catalog
from openfield.models.discovery import Root, scan_roots
from openfield.models.status import model_status
from openfield.models.store import RegistryData
from openfield.workflows.registry import CoreNodes, evaluate, load_workflows


def placeholder(path: Path, size: int = 4096) -> Path:
    header = b'{"__metadata__":{}}'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(struct.pack("<Q", len(header)) + header + b"\0" * max(0, size - 8 - len(header)))
    return path


@pytest.fixture
def comfy(tmp_path):
    root = tmp_path / "ComfyUI" / "models"
    for d in ("diffusion_models", "text_encoders", "vae", "clip_vision"):
        (root / d).mkdir(parents=True)
    return root


def test_optional_models_have_guidance_and_no_download_source():
    cat = load_catalog()
    for mid in ("ltx-2.5-i2v", "minimax-h3", "wan-2.1-i2v-14b-480p"):
        m = cat.models[mid]
        assert m.install_guide and "nothing is downloaded" in m.install_guide.lower()
        assert all(v.download is None for v in m.variants), f"{mid} must not offer a download"


def test_ltx_and_minimax_are_not_open_weight_eligible():
    cat = load_catalog()
    scan = scan_roots([])
    for mid in ("ltx-2.5-i2v", "minimax-h3"):
        st = model_status(cat.models[mid], scan, RegistryData())
        assert st["open_weight_eligibility"] == "not_eligible"
        assert st["license"]["status"] == "restricted"


def test_ltx_files_placed_in_subfolders_are_discovered(comfy):
    placeholder(comfy / "diffusion_models" / "ltx 2.5" / "LTX25-distilled-DiT-comfy-nvfp4.safetensors")
    placeholder(comfy / "text_encoders" / "ltx 2.5" / "gemma4-12b-ltx25-comfy-w4a8.safetensors")
    placeholder(comfy / "vae" / "ltx 2.5" / "ltx-2.5-video-vae-bf16.safetensors")
    scan = scan_roots([Root(comfy, "external", "comfy")])
    st = model_status(load_catalog().models["ltx-2.5-i2v"], scan, RegistryData())
    assert st["discovered"] is True and st["category"] == "discovered"
    assert all(c["state"] == "found" for c in st["variants"][0]["components"])
    assert st["identified"] is False  # finding is not identifying


def test_wan_partial_shards_do_not_count_as_present(comfy):
    for i in range(1, 4):  # 3 of 7 shards
        placeholder(comfy / "diffusion_models" / "Wan2.1" / f"diffusion_pytorch_model-{i:05d}-of-00007.safetensors")
    scan = scan_roots([Root(comfy, "external", "comfy")])
    st = model_status(load_catalog().models["wan-2.1-i2v-14b-480p"], scan, RegistryData())
    publisher = next(v for v in st["variants"] if v["id"] == "publisher-safetensors")
    diffusion = publisher["components"][0]
    assert diffusion["state"] == "missing" and diffusion["partial"] is True
    assert publisher["complete"] is False


def test_wan_all_shards_present_counts_as_present(comfy):
    for i in range(1, 8):
        placeholder(comfy / "diffusion_models" / "Wan2.1" / f"diffusion_pytorch_model-{i:05d}-of-00007.safetensors")
    scan = scan_roots([Root(comfy, "external", "comfy")])
    st = model_status(load_catalog().models["wan-2.1-i2v-14b-480p"], scan, RegistryData())
    diffusion = next(v for v in st["variants"] if v["id"] == "publisher-safetensors")["components"][0]
    assert diffusion["state"] == "found" and diffusion["partial"] is False


def test_minimax_workflow_reports_missing_pack_even_when_files_exist(comfy):
    placeholder(comfy / "diffusion_models" / "minimax h3" / "minimax_h3_fl2va_pruned_w4a8_mixed.safetensors")
    placeholder(comfy / "text_encoders" / "minimax h3" / "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors")
    placeholder(comfy / "vae" / "minimax_h3_video_vae_fp16.safetensors")
    cat = load_catalog()
    scan = scan_roots([Root(comfy, "external", "comfy")])
    st = {m.id: model_status(m, scan, RegistryData()) for m in cat.models.values()}
    wf = load_workflows().items["i2v-minimax-h3"]
    ev = evaluate(wf, st, CoreNodes(""), custom_packs_present=set(), validated_ids=set())
    assert ev["status"] == "license_blocked" or ev["status"] == "missing_packs"
    assert ev["missing_packs"] == ["ComfyUI-MiniMaxH3Mod"]
    assert ev["smoke_tested"] is False


def test_no_optional_workflow_claims_a_generator_yet():
    for wid in ("i2v-ltx-2.5-single-stage", "i2v-wan-2.1-14b-480p-safetensors", "i2v-minimax-h3"):
        wf = load_workflows().items[wid]
        assert wf.workflow_file is None
        assert any("No generator" in x for x in wf.limitations)
