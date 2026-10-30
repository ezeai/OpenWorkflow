"""The MOSS-Image text-to-image graph: node shape, layout wiring, and refusals. No GPU is needed."""
import json

import pytest

from openfield.forge import moss_graph as mg


def _boxes():
    return [{"type": "obj", "x": 0.2, "y": 0.1, "w": 0.6, "h": 0.6, "desc": "mug"},
            {"type": "text", "x": 0.1, "y": 0.8, "w": 0.8, "h": 0.12, "desc": "headline", "text": "SUMMER SALE"}]


def test_graph_uses_the_moss_clip_type_and_the_turbo_settings():
    g = mg.build_t2i("a mug", seed=3, width=512, height=512)
    assert g["clip"]["inputs"]["type"] == "moss_image"
    assert g["lora"]["inputs"]["lora_name"] == mg.LORA_TURBO
    s = g["sample"]["inputs"]
    assert (s["steps"], s["cfg"], s["sampler_name"], s["scheduler"], s["seed"]) == (4, 1.0, "euler", "sgm_uniform", 3)


def test_no_model_sampling_patch_is_added():
    classes = {n["class_type"] for n in mg.build_t2i("a mug", seed=0).values()}
    assert "ModelSamplingSD3" not in classes
    assert "SkipLayerGuidanceDiTSimple" not in classes  # the turbo path has no layer skip


def test_boxes_go_into_the_positive_prompt_as_layout_json():
    g = mg.build_t2i("poster", seed=1, width=512, height=512, boxes=_boxes())
    layout = json.loads(g["pos"]["inputs"]["text"])
    assert [i["label"] for i in layout["items"]] == ["object", "text"]
    assert layout["items"][1]["text"] == "SUMMER SALE"


def test_a_headline_switches_to_the_negative_that_allows_lettering():
    with_text = mg.build_t2i("poster", seed=1, boxes=_boxes())
    without = mg.build_t2i("poster", seed=1)
    assert with_text["neg"]["inputs"]["text"] == mg.NEGATIVE_LAYOUT
    assert without["neg"]["inputs"]["text"] == mg.NEGATIVE


def test_the_plain_prompt_is_used_when_there_are_no_boxes():
    assert json.loads(json.dumps(mg.build_t2i("a mug", seed=1)))["pos"]["inputs"]["text"] == "a mug"


@pytest.mark.parametrize("kwargs, message", [
    (dict(prompt="   ", seed=1), "prompt is empty"),
    (dict(prompt="a", seed=1, width=500), "multiples of 16"),
    (dict(prompt="a", seed=1, width=128, height=512), "between"),
    (dict(prompt="a", seed=-1), "whole number"),
    (dict(prompt="a", seed=True), "whole number"),
])
def test_bad_requests_are_refused(kwargs, message):
    with pytest.raises(mg.GraphError, match=message):
        mg.build_t2i(**kwargs)


def test_save_node_writes_under_the_openfield_prefix():
    assert mg.build_t2i("a", seed=1)["save"]["inputs"]["filename_prefix"] == mg.OUTPUT_PREFIX


@pytest.mark.parametrize("aspect, expected", [
    ("square", (1024, 1024)), ("portrait", (1024, 1280)), ("story", (1024, 1824)),
    ("banner", (1824, 1024)), ("poster", (1024, 1456)),
])
def test_each_poster_type_has_the_right_shape_at_the_final_base(aspect, expected):
    assert mg.size_for(aspect, 1024) == expected


def test_every_side_is_a_multiple_of_16_and_within_limits():
    for aspect in mg.ASPECTS:
        for base in mg.BASES:
            w, h = mg.size_for(aspect, base)
            assert w % 16 == 0 and h % 16 == 0
            assert mg.MIN_SIDE <= w <= mg.MAX_SIDE and mg.MIN_SIDE <= h <= mg.MAX_SIDE


def test_unknown_type_or_base_is_refused():
    with pytest.raises(mg.GraphError, match="unknown poster type"):
        mg.size_for("billboard", 512)
    with pytest.raises(mg.GraphError, match="base must be"):
        mg.size_for("square", 700)
