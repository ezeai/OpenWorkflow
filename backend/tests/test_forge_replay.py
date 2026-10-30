"""Replaying a run uses its exact receipt values; comparing two runs shows what differs and whether each can repeat."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openfield import api_forge
from openfield.app import create_app
from openfield.forge import object_info
from openfield.generation.jobs import JobStore

FIX = Path(__file__).parent / "fixtures" / "forge"
ZIMAGE = (FIX / "image_z_image_turbo.json").read_bytes()


@pytest.fixture(autouse=True)
def bundled_info(monkeypatch):
    monkeypatch.setattr(api_forge, "_info", lambda request: object_info.load(Path("__no_such_home__")))


def make_run(c, wid, jid, values, workflow_sha=None, spec_revision=0):
    """Records a finished run with a receipt, as a real run would, without calling ComfyUI."""
    home = Path(c.app.state.services.home)
    JobStore(home).save({"id": jid, "kind": "app", "provider": "local", "workflow_id": wid, "created_at": "2026-10-29T01:00:00+00:00",
                         "state": "COMPLETED", "output_file": {"name": "output.png"}, "error": None})
    run_dir = home / "forge" / "runs" / jid
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "run.json").write_text(__import__("json").dumps({
        "workflow_id": wid, "workflow_sha256": workflow_sha or c.get(f"/api/forge/workflows/{wid}").json()["workflow"]["sha256"],
        "spec_revision": spec_revision, "values": values, "loras": [], "provider": "local"}), encoding="utf-8")


def app_id(c):
    return c.post("/api/forge/workflows?name=z.json", content=ZIMAGE).json()["workflow"]["id"]


def test_compare_shows_what_differs_and_whether_each_run_can_repeat(tmp_path):
    c = TestClient(create_app(tmp_path / "a"))
    wid = app_id(c)
    make_run(c, wid, "1" * 32, {"prompt": "a fox", "seed": 42})
    make_run(c, wid, "2" * 32, {"prompt": "a owl", "seed": 42})
    out = c.get(f"/api/forge/runs/{'1' * 32}/compare/{'2' * 32}").json()
    assert out["same_app"] and out["same_workflow_file"]
    assert out["values"] == [{"name": "prompt", "a": "a fox", "b": "a owl"}]
    assert out["repeatable"]["a"]["repeatable"] is True


def test_a_random_seed_makes_a_run_not_repeatable(tmp_path):
    c = TestClient(create_app(tmp_path / "a"))
    wid = app_id(c)
    make_run(c, wid, "3" * 32, {"prompt": "x", "seed": "randomize"})
    rep = c.get(f"/api/forge/runs/{'3' * 32}/compare/{'3' * 32}").json()["repeatable"]["a"]
    assert rep["repeatable"] is False and len(rep["why"]) == 2


def test_replay_is_refused_when_the_workflow_file_changed(tmp_path):
    c = TestClient(create_app(tmp_path / "a"))
    wid = app_id(c)
    make_run(c, wid, "4" * 32, {"prompt": "x", "seed": 1}, workflow_sha="0" * 64)
    r = c.post(f"/api/forge/runs/{'4' * 32}/replay")
    assert r.status_code == 409 and "changed" in r.json()["detail"]


def test_replay_of_a_run_without_a_receipt_is_not_found(tmp_path):
    c = TestClient(create_app(tmp_path / "a"))
    assert c.post(f"/api/forge/runs/{'9' * 32}/replay").status_code == 404
