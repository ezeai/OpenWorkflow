"""Schema-driven fill: proves OpenField's own logic (match by name and type, clamp, report what is missing).

Uses hand-written /object_info-shaped dicts. It does not contact ComfyUI.
"""
import pytest

from openfield.generation import catalog

KSAMPLER_LIKE = {
    "input": {
        "required": {
            "seed": ["INT", {"default": 0, "min": 0, "max": 1000}],
            "steps": ["INT", {"default": 8, "min": 1, "max": 50}],
            "cfg": ["FLOAT", {"default": 1.0, "min": 0.0, "max": 10.0}],
            "sampler": [["euler", "dpmpp_2m"], {"default": "euler"}],
            "width": ["INT", {"default": 512}],
            "text": ["STRING", {"multiline": True}],
        },
        "optional": {
            "negative": ["STRING", {"default": ""}],
            "strength": ["FLOAT", {"default": 0.5}],
        },
    }
}


def test_parse_reads_inputs_with_types_defaults_and_options():
    specs = {s.name: s for s in catalog.parse_node(KSAMPLER_LIKE)}
    assert specs["seed"].type == "INT" and specs["seed"].required is True
    assert specs["sampler"].type == "COMBO" and specs["sampler"].options == ("euler", "dpmpp_2m")
    assert specs["negative"].required is False
    assert specs["cfg"].minimum == 0.0 and specs["cfg"].maximum == 10.0


def test_fill_matches_prompt_negative_and_size_by_name_and_type():
    specs = catalog.parse_node(KSAMPLER_LIKE)
    result = catalog.fill(specs, {"prompt": "a cat", "negative": "blurry", "width": 832, "seed": 7, "steps": 4, "cfg": 1.5})
    assert result.inputs["text"] == "a cat"
    assert result.inputs["negative"] == "blurry"
    assert result.inputs["width"] == 832
    assert result.inputs["seed"] == 7 and result.inputs["cfg"] == 1.5
    assert result.unfilled == []


def test_defaults_fill_inputs_that_have_no_matching_value():
    specs = catalog.parse_node(KSAMPLER_LIKE)
    result = catalog.fill(specs, {"prompt": "x"})
    # Every required input here has a default, so only the prompt is needed.
    assert result.unfilled == []
    assert result.inputs["seed"] == 0 and result.inputs["width"] == 512


def test_required_input_without_default_is_listed_as_unfilled():
    node = {"input": {"required": {"reference_image": ["IMAGE", {}], "text": ["STRING", {}]}}}
    result = catalog.fill(catalog.parse_node(node), {"prompt": "hi"})
    assert result.unfilled == ["reference_image"]
    assert result.inputs == {"text": "hi"}


def test_numbers_are_clamped_to_the_nodes_own_range():
    specs = catalog.parse_node(KSAMPLER_LIKE)
    result = catalog.fill(specs, {"steps": 500, "cfg": -3.0})
    assert result.inputs["steps"] == 50
    assert result.inputs["cfg"] == 0.0


def test_combo_only_takes_a_value_it_offers_and_falls_back_to_default():
    specs = catalog.parse_node(KSAMPLER_LIKE)
    assert catalog.fill(specs, {"sampler": "dpmpp_2m"}).inputs["sampler"] == "dpmpp_2m"
    assert catalog.fill(specs, {"sampler": "not_a_sampler"}).inputs["sampler"] == "euler"


def test_build_catalog_node_returns_a_graph_or_names_what_is_missing():
    from openfield.generation.builders import build_catalog_node

    graph = build_catalog_node("KSamplerLike", KSAMPLER_LIKE, {"prompt": "a cat", "seed": 3}, node_id="8")
    assert graph == {"8": {"class_type": "KSamplerLike", "inputs": {
        "seed": 3, "steps": 8, "cfg": 1.0, "sampler": "euler", "width": 512, "text": "a cat",
        "negative": "", "strength": 0.5}}}

    linked = {"input": {"required": {"image": ["IMAGE", {}], "text": ["STRING", {}]}}}
    with pytest.raises(ValueError, match="image"):
        build_catalog_node("NeedsImage", linked, {"prompt": "x"})


def test_optional_inputs_take_their_default_and_are_never_reported_unfilled():
    specs = catalog.parse_node(KSAMPLER_LIKE)
    result = catalog.fill(specs, {})
    assert result.inputs["strength"] == 0.5 and result.inputs["negative"] == ""
    assert "strength" not in result.unfilled
    assert result.unfilled == ["text"]  # the required prompt input has no default and no value
