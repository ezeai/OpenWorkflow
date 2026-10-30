from openfield.generation.progress import PromptProgress, node_titles

GRAPH = {
    "3": {"class_type": "KSampler", "_meta": {"title": "Sampler"}, "inputs": {}},
    "8": {"class_type": "VAEDecode", "inputs": {}},
}


def _progress():
    return PromptProgress("p1", node_titles(GRAPH))


def test_titles_fall_back_to_class_type():
    assert node_titles(GRAPH) == {"3": "Sampler", "8": "VAEDecode"}


def test_step_progress_is_determinate_with_node_title():
    p = _progress()
    assert p.feed({"type": "executing", "data": {"node": "3", "prompt_id": "p1"}})
    assert p.feed({"type": "progress", "data": {"value": 12, "max": 20, "node": "3", "prompt_id": "p1"}})
    record = p.record()
    assert record["mode"] == "determinate"
    assert (record["node"], record["step"], record["steps"]) == ("Sampler", 12, 20)


def test_moving_to_next_node_counts_finished_and_clears_steps():
    p = _progress()
    p.feed({"type": "executing", "data": {"node": "3", "prompt_id": "p1"}})
    p.feed({"type": "progress", "data": {"value": 20, "max": 20, "node": "3", "prompt_id": "p1"}})
    p.feed({"type": "executing", "data": {"node": "8", "prompt_id": "p1"}})
    record = p.record()
    assert record["mode"] == "indeterminate"
    assert record["node"] == "VAEDecode"
    assert record["nodes_finished"] == 1


def test_null_node_means_the_prompt_finished():
    p = _progress()
    p.feed({"type": "executing", "data": {"node": "8", "prompt_id": "p1"}})
    assert p.feed({"type": "executing", "data": {"node": None, "prompt_id": "p1"}})
    assert p.record()["node"] is None


def test_other_prompts_are_ignored():
    p = _progress()
    assert not p.feed({"type": "executing", "data": {"node": "3", "prompt_id": "someone-else"}})
    assert p.record()["node"] is None


def test_unrelated_messages_do_not_change_the_record():
    p = _progress()
    assert not p.feed({"type": "status", "data": {"status": {"exec_info": {"queue_remaining": 1}}}})
