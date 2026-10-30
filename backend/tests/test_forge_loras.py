"""The LoRA stack: checks, storage, and how the loaders are placed into a prompt."""
import copy

import pytest

from openfield.forge import loras


def _prompt():
    return {
        "ck": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "base.safetensors"}},
        "pos": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["ck", 1], "text": "a mug"}},
        "neg": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["ck", 1], "text": ""}},
        "k": {"class_type": "KSampler", "inputs": {"model": ["ck", 0], "positive": ["pos", 0], "negative": ["neg", 0], "seed": 1}},
        "v": {"class_type": "VAEDecode", "inputs": {"samples": ["k", 0], "vae": ["ck", 2]}},
    }


def test_checks_refuse_unknown_files_bad_strengths_and_too_many():
    known = {"style.safetensors"}
    assert loras.clean([{"name": "style.safetensors", "strength": 0.7}], known) == [{"name": "style.safetensors", "strength": 0.7, "enabled": True}]
    with pytest.raises(loras.LoraError, match="not in ComfyUI"):
        loras.clean([{"name": "missing.safetensors"}], known)
    with pytest.raises(loras.LoraError, match="between"):
        loras.clean([{"name": "style.safetensors", "strength": 5}], known)
    with pytest.raises(loras.LoraError, match="at most"):
        loras.clean([{"name": "style.safetensors"}] * (loras.MAX_LORAS + 1), known)
    assert loras.clean([{"name": "any.safetensors"}], None)[0]["name"] == "any.safetensors"  # ComfyUI not reachable: not checked


def test_the_model_and_clip_are_chained_and_every_user_is_rewired():
    items = [{"name": "a.safetensors", "strength": 0.8}, {"name": "b.safetensors", "strength": 0.5}]
    out, applied = loras.apply(_prompt(), items)
    assert [a["name"] for a in applied] == ["a.safetensors", "b.safetensors"]
    assert out["forge_lora_m0_0"]["inputs"]["model"] == ["ck", 0]
    assert out["forge_lora_m0_1"]["inputs"]["model"] == ["forge_lora_m0_0", 0]
    assert out["k"]["inputs"]["model"] == ["forge_lora_m0_1", 0]
    assert out["forge_lora_c0_1"]["inputs"]["clip"] == ["forge_lora_c0_0", 1]
    assert out["pos"]["inputs"]["clip"] == ["forge_lora_c0_1", 1]
    assert out["neg"]["inputs"]["clip"] == ["forge_lora_c0_1", 1]
    assert out["v"]["inputs"]["vae"] == ["ck", 2]   # the VAE is not a LoRA target


def test_the_original_prompt_is_not_changed_and_disabled_loras_are_skipped():
    original = _prompt()
    before = copy.deepcopy(original)
    out, applied = loras.apply(original, [{"name": "a.safetensors", "strength": 1, "enabled": False}])
    assert out == original and applied == [] and original == before
    out2, _ = loras.apply(original, [{"name": "a.safetensors", "strength": 1}])
    assert original == before and out2 != original


def test_a_prompt_with_no_model_input_is_refused():
    with pytest.raises(loras.LoraError, match="no model input"):
        loras.apply({"x": {"class_type": "SaveImage", "inputs": {"images": ["y", 0]}}}, [{"name": "a.safetensors", "strength": 1}])


def test_the_store_round_trips_and_a_broken_file_reads_as_empty(tmp_path):
    s = loras.Store(tmp_path)
    assert s.load("w") == []
    s.save("w", [{"name": "a.safetensors", "strength": 0.6, "enabled": True}])
    assert s.load("w") == [{"name": "a.safetensors", "strength": 0.6, "enabled": True}]
    (tmp_path / "w" / "loras.json").write_text("{not json", encoding="utf-8")
    assert s.load("w") == []


def test_a_shift_node_after_the_unet_gets_the_lora_once_not_twice():
    prompt = {
        "unet": {"class_type": "UNETLoader", "inputs": {"unet_name": "m.safetensors"}},
        "shift": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["unet", 0], "shift": 3}},
        "k": {"class_type": "KSampler", "inputs": {"model": ["shift", 0], "seed": 1}},
    }
    out, _ = loras.apply(prompt, [{"name": "a.safetensors", "strength": 1}])
    loaders = [n for n in out.values() if n["class_type"] == "LoraLoaderModelOnly"]
    assert len(loaders) == 1
    assert out["shift"]["inputs"]["model"] == [next(k for k, v in out.items() if v is loaders[0]), 0]
    assert loaders[0]["inputs"]["model"] == ["unet", 0]
