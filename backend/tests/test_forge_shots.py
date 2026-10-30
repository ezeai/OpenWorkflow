"""Shot presets and the character dataset: presets are data, a finished render becomes a dataset item with a caption,
and an unfinished render is refused."""
import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from openfield import app as appmod
from openfield.forge import shots, uploads


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), (90, 60, 40)).save(buf, format="PNG")
    return buf.getvalue()


def test_presets_have_unique_ids_and_a_video_headless_front():
    ids = [p["id"] for p in shots.PRESETS]
    assert len(ids) == len(set(ids))
    assert any(p["id"] == "headless_front" and p["headless"] for p in shots.PRESETS)
    assert all(p["prompt"] for p in shots.PRESETS)


def test_an_unknown_preset_is_refused():
    with pytest.raises(shots.ShotError, match="unknown"):
        shots.preset("nope")


def test_caption_names_the_character_and_keeps_the_framing():
    c = shots.caption(shots.preset("back_view"), "Maya")
    assert c == "Maya, " + shots.preset("back_view")["prompt"]


@pytest.fixture()
def client(tmp_path, monkeypatch):
    c = TestClient(appmod.create_app(tmp_path))
    monkeypatch.setattr(uploads, "send_to_comfy", lambda url, data, info: "openfield_" + info["sha256"][:16] + ".png")
    return c


def test_presets_route(client):
    r = client.get("/api/forge/shots/presets")
    assert r.status_code == 200 and len(r.json()["presets"]) == len(shots.PRESETS)


def _finished_job(client, monkeypatch, jid: str, state: str = "COMPLETED"):
    home = client.app.state.services.home / "forge"
    (home / "runs" / jid).mkdir(parents=True, exist_ok=True)
    (home / "runs" / jid / "output.png").write_bytes(_png())
    job = {"id": jid, "state": state, "output_file": {"name": "output.png"} if state == "COMPLETED" else None,
           "provenance": {"prompt": "a portrait"}}
    monkeypatch.setattr(client.app.state.jobs.store, "load", lambda j: job)


def test_a_finished_render_becomes_a_dataset_item_with_a_caption(client, monkeypatch):
    cid = client.post("/api/forge/characters", json={"name": "Maya"}).json()["character"]["id"]
    jid = "e" * 32
    _finished_job(client, monkeypatch, jid)
    r = client.post(f"/api/forge/characters/{cid}/dataset", json={"job_id": jid, "preset_id": "full_body"})
    assert r.status_code == 201, r.text
    item = r.json()["item"]
    assert item["caption"] == "Maya, " + shots.preset("full_body")["prompt"]
    folder = client.app.state.services.home / "forge" / "characters" / "dataset" / cid
    assert (folder / item["file"]).is_file() and (folder / f"{item['id']}.txt").read_text(encoding="utf-8") == item["caption"]
    listed = client.get(f"/api/forge/characters/{cid}/dataset").json()["dataset"]
    assert [i["id"] for i in listed] == [item["id"]]


def test_an_unfinished_render_is_not_added(client, monkeypatch):
    cid = client.post("/api/forge/characters", json={"name": "Maya"}).json()["character"]["id"]
    _finished_job(client, monkeypatch, "f" * 32, state="RUNNING")
    r = client.post(f"/api/forge/characters/{cid}/dataset", json={"job_id": "f" * 32})
    assert r.status_code == 422 and "finished" in r.json()["detail"]


def test_an_unknown_preset_on_add_is_refused(client, monkeypatch):
    cid = client.post("/api/forge/characters", json={"name": "Maya"}).json()["character"]["id"]
    _finished_job(client, monkeypatch, "d" * 32)
    r = client.post(f"/api/forge/characters/{cid}/dataset", json={"job_id": "d" * 32, "preset_id": "bogus"})
    assert r.status_code == 422
