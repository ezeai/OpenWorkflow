from openfield.forge import graphview

SUB = "f2fd"


def workflow():
    """Outer: LoadImage(1) -> placeholder(5) -> SaveImage(9). Inside the placeholder: Resize(2) between its edges."""
    return {
        "nodes": [{"id": 1, "type": "LoadImage", "mode": 0}, {"id": 5, "type": SUB, "mode": 0}, {"id": 9, "type": "SaveImage", "mode": 0}],
        "links": [[7, 1, 0, 5, 0, "IMAGE"], [8, 5, 0, 9, 0, "IMAGE"]],
        "definitions": {"subgraphs": [{
            "id": SUB, "name": "Resize block",
            "nodes": [{"id": 2, "type": "ImageScale", "mode": 0}, {"id": 3, "type": "MarkdownNote", "mode": 0}],
            "links": [
                {"id": 1, "origin_id": -10, "origin_slot": 0, "target_id": 2, "target_slot": 0, "type": "IMAGE"},
                {"id": 2, "origin_id": 2, "origin_slot": 0, "target_id": -20, "target_slot": 0, "type": "IMAGE"},
            ],
        }]},
    }


def test_subgraph_nodes_appear_and_the_box_does_not():
    g = graphview.flatten(workflow())
    ids = {n["id"] for n in g["nodes"]}
    assert "5" not in ids
    assert {"1", "9", "5:2", "5:3"} <= ids
    inner = next(n for n in g["nodes"] if n["id"] == "5:2")
    assert inner["group"] == "Resize block" and inner["type"] == "ImageScale"
    assert next(n for n in g["nodes"] if n["id"] == "5:3")["ui_only"] is True


def test_links_join_across_the_box_edge():
    links = {(l["from"], l["to"]) for l in graphview.flatten(workflow())["links"]}
    assert ("1", "5:2") in links    # outer input now goes straight to the inner node
    assert ("5:2", "9") in links    # inner output now goes straight to the outer result
    assert not any("5" == x for pair in links for x in pair)


def test_dict_and_list_links_both_read():
    data = workflow()
    data["links"] = [{"id": 7, "origin_id": 1, "origin_slot": 0, "target_id": 5, "target_slot": 0, "type": "IMAGE"}]
    assert ("1", "5:2") in {(l["from"], l["to"]) for l in graphview.flatten(data)["links"]}


def test_settings_pair_names_with_values():
    data = {"nodes": [{"id": 1, "type": "KSampler", "widgets_values": [42, "euler", 1.0],
                       "inputs": [{"name": "model"}, {"name": "seed", "widget": {"name": "seed"}},
                                  {"name": "sampler_name", "widget": {"name": "sampler_name"}}]}],
            "links": []}
    params = graphview.flatten(data)["nodes"][0]["params"]
    assert params == [{"name": "seed", "value": 42}, {"name": "sampler_name", "value": "euler"}, {"name": "value 3", "value": 1.0}]


def test_long_text_is_shortened_for_the_canvas():
    data = {"nodes": [{"id": 1, "type": "CLIPTextEncode", "widgets_values": ["x" * 1000],
                       "inputs": [{"name": "text", "widget": {"name": "text"}}]}], "links": []}
    value = graphview.flatten(data)["nodes"][0]["params"][0]["value"]
    assert len(value) == graphview.SHOWN + 1 and value.endswith("…")


def known_info():
    from openfield.forge import object_info
    raw = {"KSampler": {"input": {"required": {
        "model": ["MODEL"],
        "seed": ["INT", {"default": 0, "min": 0, "max": 99, "step": 1, "control_after_generate": True}],
        "sampler_name": [["euler", "dpmpp_2m"], {}],
        "denoise": ["FLOAT", {"default": 1.0, "min": 0.0, "max": 1.0, "step": 0.01}],
    }}, "output": ["LATENT"], "category": "sampling"}}
    return object_info.from_raw(raw, "bundled", None, None)


def test_known_nodes_get_the_exact_settings_with_types_and_limits():
    data = {"nodes": [{"id": 3, "type": "KSampler", "widgets_values": [42, "randomize", "euler", 0.5]}], "links": []}
    node = graphview.flatten(data, known_info())["nodes"][0]
    assert node["known"] is True
    assert [p["name"] for p in node["params"]] == ["seed", "seed control", "sampler_name", "denoise"]
    seed = node["params"][0]
    assert (seed["type"], seed["value"], seed["min"], seed["max"], seed["step"]) == ("INT", 42, 0, 99, 1)
    assert node["params"][2]["choices"] == ["euler", "dpmpp_2m"]
    assert node["params"][3]["value"] == 0.5


def test_sockets_keep_the_editor_names_and_order():
    raw = {"id": 3, "type": "KSampler", "widgets_values": [1],
           "inputs": [{"name": "model", "type": "MODEL", "link": 4},
                      {"name": "seed", "type": "INT", "widget": {"name": "seed"}, "link": None}],
           "outputs": [{"name": "LATENT", "type": "LATENT", "links": [9]}]}
    node = graphview.flatten({"nodes": [raw], "links": []}, known_info())["nodes"][0]
    assert node["inputs"] == [{"name": "model", "type": "MODEL", "widget": False},
                              {"name": "seed", "type": "INT", "widget": True}]
    assert node["outputs"] == [{"name": "LATENT", "type": "LATENT", "widget": False}]


def test_unknown_nodes_fall_back_to_the_saved_names():
    data = {"nodes": [{"id": 3, "type": "NotInstalled", "widgets_values": [1]}], "links": []}
    node = graphview.flatten(data, known_info())["nodes"][0]
    assert node["known"] is False and node["params"] == [{"name": "value 1", "value": 1}]


def test_a_subgraph_inside_a_subgraph_opens_too():
    inner = {"id": "in-id", "name": "Inner", "nodes": [{"id": 7, "type": "VAEDecode", "mode": 0}], "links": []}
    outer = {"id": "out-id", "name": "Outer", "nodes": [{"id": 5, "type": "in-id", "mode": 0}], "links": []}
    data = {"nodes": [{"id": 1, "type": "out-id", "mode": 0}], "links": [],
            "definitions": {"subgraphs": [inner, outer]}}
    g = graphview.flatten(data)
    ids = {n["id"] for n in g["nodes"]}
    assert ids == {"1:5:7"}
    assert g["nodes"][0]["group"] == "Outer › Inner"
    assert {s["name"] for s in g["subgraphs"]} == {"Inner", "Outer"}
    assert g["nodes"][0]["scope"] == "1:5"
    boxes = {b["scope"]: b for b in g["boxes"]}
    assert boxes["1"]["parent"] is None and boxes["1:5"]["parent"] == "1"
    assert boxes["1:5"]["label"] == "Outer › Inner"


def test_a_workflow_without_subgraphs_is_unchanged():
    data = {"nodes": [{"id": 1, "type": "A"}, {"id": 2, "type": "B"}], "links": [[3, 1, 0, 2, 0, "X"]]}
    g = graphview.flatten(data)
    assert [n["id"] for n in g["nodes"]] == ["1", "2"]
    assert g["links"] == [{"id": 3, "from": "1", "from_slot": 0, "to": "2", "to_slot": 0, "type": "X"}]
    assert g["subgraphs"] == []
