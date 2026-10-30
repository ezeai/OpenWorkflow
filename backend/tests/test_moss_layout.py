"""MOSS-Image bounding-box prompts: the centred pixel maths, edge reach, text boxes, and refusals."""
import json

import pytest

from openfield.forge import moss_layout as ml


def test_no_boxes_leaves_the_prompt_unchanged():
    assert ml.layout_prompt("a red bicycle", None, 1024, 1024) == "a red bicycle"
    assert ml.layout_prompt("a red bicycle", [], 1024, 1024) == "a red bicycle"


def test_a_full_canvas_box_reaches_both_edges_exactly():
    out = json.loads(ml.layout_prompt("poster", [{"type": "obj", "x": 0, "y": 0, "w": 1, "h": 1, "desc": "background"}], 1024, 1024))
    assert out["items"][0]["bbox"] == [-512, -512, 512, 512]
    assert out["bbox_coord_space"] == "abs_centralized" and out["bbox_format"] == "yxyx"


def test_a_centred_box_is_centred_on_the_origin():
    box = {"type": "obj", "x": 0.25, "y": 0.25, "w": 0.5, "h": 0.5, "desc": "product"}
    assert ml.bbox(ml.normalize_boxes([box])[0], 1000, 800) == [-200, -250, 200, 250]


def test_text_box_keeps_the_exact_wording_and_is_labelled_text():
    box = {"type": "text", "x": 0.1, "y": 0.8, "w": 0.8, "h": 0.1, "desc": "bold headline", "text": "  SUMMER  SALE  "}
    out = json.loads(ml.layout_prompt("poster", [box], 1024, 1024))
    item = out["items"][0]
    assert item["label"] == "text"
    assert item["text"] == "SUMMER SALE"
    assert item["caption"].startswith("bold headline, ") and ml.TEXT_LOOK in item["caption"]


def test_character_and_object_labels():
    boxes = [{"type": "character", "x": 0, "y": 0, "w": 0.5, "h": 1, "desc": "model"},
             {"type": "obj", "x": 0.5, "y": 0, "w": 0.5, "h": 1, "desc": "bottle"}]
    labels = [i["label"] for i in json.loads(ml.layout_prompt("p", boxes, 512, 512))["items"]]
    assert labels == ["character", "object"]


def test_the_summary_caption_is_the_prompt():
    out = json.loads(ml.layout_prompt("  a coffee ad  ", [{"type": "obj", "x": 0, "y": 0, "w": 1, "h": 1}], 512, 512))
    assert out["summary"]["caption"] == "a coffee ad"


@pytest.mark.parametrize("box, message", [
    ({"type": "obj", "x": 0.9, "y": 0, "w": 0.2, "h": 0.5}, "runs past the canvas edge"),
    ({"type": "obj", "x": -0.1, "y": 0, "w": 0.2, "h": 0.5}, "must lie on the canvas"),
    ({"type": "obj", "x": 0, "y": 0, "w": 0, "h": 0.5}, "must lie on the canvas"),
    ({"type": "poster", "x": 0, "y": 0, "w": 0.5, "h": 0.5}, "unknown type"),
    ({"type": "text", "x": 0, "y": 0, "w": 0.5, "h": 0.5, "text": "   "}, "no words to render"),
    ({"type": "obj", "x": "left", "y": 0, "w": 0.5, "h": 0.5}, "needs numeric"),
    ("not a box", "is not an object"),
])
def test_bad_boxes_are_refused_with_the_box_number(box, message):
    with pytest.raises(ml.LayoutError) as ei:
        ml.normalize_boxes([{"type": "obj", "x": 0, "y": 0, "w": 0.1, "h": 0.1}, box])
    assert "box 2" in str(ei.value) and message in str(ei.value)


def test_too_many_boxes_are_refused():
    with pytest.raises(ml.LayoutError, match="at most"):
        ml.normalize_boxes([{"type": "obj", "x": 0, "y": 0, "w": 0.1, "h": 0.1}] * (ml.MAX_BOXES + 1))


def test_long_text_is_cut_to_the_limit_not_rejected():
    box = {"type": "text", "x": 0, "y": 0, "w": 1, "h": 0.2, "text": "word " * 100}
    assert len(ml.normalize_boxes([box])[0]["text"]) == ml.MAX_TEXT


def test_a_text_caption_that_names_its_look_gets_no_extra_wording():
    box = {"type": "text", "x": 0, "y": 0, "w": 1, "h": 0.2, "desc": "bold sans-serif headline", "text": "SALE"}
    item = json.loads(ml.layout_prompt("p", [box], 512, 512))["items"][0]
    assert item["caption"] == "bold sans-serif headline"


def test_wrapping_quotes_are_removed_from_the_wording():
    box = {"type": "text", "x": 0, "y": 0, "w": 1, "h": 0.2, "desc": "headline", "text": '"NEW SCENT"'}
    assert ml.normalize_boxes([box])[0]["text"] == "NEW SCENT"


def test_a_too_small_text_box_grows_around_its_centre_and_keeps_its_wording():
    box = {"type": "text", "x": 0.4, "y": 0.4, "w": 0.05, "h": 0.02, "desc": "headline", "text": "SUMMER SALE"}
    out = ml.normalize_boxes([box])[0]
    assert out["w"] >= ml.TEXT_FLOOR["w"] and out["h"] >= ml.TEXT_FLOOR["h"]
    assert abs((out["x"] + out["w"] / 2) - 0.425) < 0.001 and abs((out["y"] + out["h"] / 2) - 0.41) < 0.001
    assert out["text"] == "SUMMER SALE"


def test_an_object_box_is_never_grown():
    box = {"type": "obj", "x": 0.4, "y": 0.4, "w": 0.05, "h": 0.02, "desc": "bottle"}
    out = ml.normalize_boxes([box])[0]
    assert (out["w"], out["h"]) == (0.05, 0.02)


def test_warnings_flag_long_wording_and_overlapping_text():
    boxes = ml.normalize_boxes([
        {"type": "text", "x": 0.0, "y": 0.0, "w": 0.5, "h": 0.2, "desc": "a", "text": "ONE TWO THREE FOUR FIVE SIX"},
        {"type": "text", "x": 0.3, "y": 0.1, "w": 0.5, "h": 0.2, "desc": "b", "text": "SHORT"},
        {"type": "obj", "x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0, "desc": "background"},
    ])
    w = ml.warnings(boxes)
    assert any("more than 5 words" in m for m in w[0])
    assert any("overlaps text box 2" in m for m in w[0])
    assert any("overlaps text box 1" in m for m in w[1])
    assert w[2] == []


def test_no_warnings_for_a_clean_layout():
    boxes = ml.normalize_boxes([
        {"type": "obj", "x": 0.2, "y": 0.1, "w": 0.6, "h": 0.6, "desc": "product"},
        {"type": "text", "x": 0.1, "y": 0.8, "w": 0.8, "h": 0.14, "desc": "headline", "text": "SUMMER SALE"},
    ])
    assert ml.warnings(boxes) == [[], []]
