import hashlib
import json

import pytest

from openfield.forge import graphedits
from openfield.forge.object_info import InputSpec, NodeDef


def ksampler():
    return NodeDef(name="KSampler", inputs=[
        InputSpec("model", "MODEL", True, {}),
        InputSpec("seed", "INT", True, {"default": 7, "min": 0}),
        InputSpec("sampler_name", "COMBO", True, {}, ["euler", "dpmpp_2m"]),
        InputSpec("denoise", "FLOAT", False, {}),
        InputSpec("sharp", "BOOLEAN", False, {}),
    ], outputs=["LATENT"], output_names=["LATENT"], output_node=False, category="sampling")


def test_defaults_come_from_the_node_definition():
    params = {p["name"]: p["value"] for p in graphedits.default_params(ksampler())}
    assert params == {"seed": 7, "sampler_name": "euler", "denoise": 0.0, "sharp": False}


def test_adding_writes_an_overlay_and_leaves_the_source_alone(tmp_path):
    source = tmp_path / "source.json"
    source.write_text('{"nodes": []}', encoding="utf-8")
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    overlay = tmp_path / "graph_edits.json"

    node = graphedits.add(overlay, ksampler(), [120, 340])

    assert node["added"] is True and node["id"] == "+1" and node["pos"] == [120.0, 340.0]
    assert json.loads(overlay.read_text(encoding="utf-8"))["added"][0]["type"] == "KSampler"
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    assert graphedits.load(overlay)[0]["id"] == "+1"


def test_ids_keep_counting_and_the_list_is_capped(tmp_path):
    overlay = tmp_path / "graph_edits.json"
    for i in range(graphedits.MAX_ADDED):
        graphedits.add(overlay, ksampler(), [i, 0])
    with pytest.raises(graphedits.EditError):
        graphedits.add(overlay, ksampler(), [0, 0])


def test_a_position_must_be_two_sensible_numbers(tmp_path):
    with pytest.raises(graphedits.EditError):
        graphedits.add(tmp_path / "e.json", ksampler(), [1])
    with pytest.raises(graphedits.EditError):
        graphedits.add(tmp_path / "e.json", ksampler(), [1, 1e9])


def test_changes_apply_in_order_and_a_delete_takes_its_links(tmp_path):
    overlay = tmp_path / "graph_edits.json"
    graphedits.add_op(overlay, {"op": "state", "node": "1", "state": "bypassed"}, {"1", "2"})
    graphedits.add_op(overlay, {"op": "title", "node": "1", "title": "Load"}, {"1", "2"})
    graphedits.add_op(overlay, {"op": "delete", "node": "2"}, {"1", "2"})
    nodes = [{"id": "1", "title": "x", "state": "active"}, {"id": "2", "title": "y", "state": "active"}]
    links = [{"id": "l", "from": "1", "to": "2"}]
    out_nodes, out_links = graphedits.apply_ops(nodes, links, graphedits.load_ops(overlay))
    assert out_nodes == [{"id": "1", "title": "Load", "state": "bypassed"}]
    assert out_links == []


def test_a_change_to_an_unknown_node_or_state_is_refused(tmp_path):
    overlay = tmp_path / "graph_edits.json"
    with pytest.raises(graphedits.EditError):
        graphedits.add_op(overlay, {"op": "delete", "node": "99"}, {"1"})
    with pytest.raises(graphedits.EditError):
        graphedits.add_op(overlay, {"op": "state", "node": "1", "state": "exploded"}, {"1"})
    with pytest.raises(graphedits.EditError):
        graphedits.add_op(overlay, {"op": "rewrite", "node": "1"}, {"1"})


def test_undo_takes_back_the_last_change_only(tmp_path):
    overlay = tmp_path / "graph_edits.json"
    graphedits.add_op(overlay, {"op": "state", "node": "1", "state": "muted"}, {"1"})
    graphedits.add_op(overlay, {"op": "delete", "node": "1"}, {"1"})
    assert graphedits.undo(overlay)["op"] == "delete"
    assert [o["op"] for o in graphedits.load_ops(overlay)] == ["state"]
    assert graphedits.undo(overlay)["op"] == "state"
    assert graphedits.undo(overlay) is None


def test_adding_a_node_keeps_the_changes(tmp_path):
    overlay = tmp_path / "graph_edits.json"
    graphedits.add_op(overlay, {"op": "state", "node": "1", "state": "muted"}, {"1"})
    graphedits.add(overlay, ksampler(), [0, 0])
    assert len(graphedits.load_ops(overlay)) == 1 and len(graphedits.load(overlay)) == 1


def test_a_damaged_overlay_reads_as_empty(tmp_path):
    overlay = tmp_path / "graph_edits.json"
    overlay.write_text("not json", encoding="utf-8")
    assert graphedits.load(overlay) == []
