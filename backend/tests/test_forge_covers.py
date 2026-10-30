"""App pictures: a user cover, the fallback to the latest completed run, and refusals."""
import io
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from openfield import app as appmod

FIX = Path(__file__).parent / "fixtures" / "forge"


def _png(size=(16, 16)):
    buf = io.BytesIO()
    Image.new("RGB", size, (200, 80, 40)).save(buf, format="PNG")
    return buf.getvalue()


def _import(c):
    r = c.post("/api/forge/workflows?name=zimage.json", content=(FIX / "image_z_image_turbo.json").read_bytes())
    assert r.status_code == 200, r.text
    return r.json()["workflow"]["id"]


def test_no_picture_is_404_until_one_is_set(tmp_path):
    c = TestClient(appmod.create_app(tmp_path))
    wid = _import(c)
    assert c.get(f"/api/forge/workflows/{wid}/cover").status_code == 404


def test_a_set_cover_is_served_and_can_be_replaced_and_cleared(tmp_path):
    c = TestClient(appmod.create_app(tmp_path))
    wid = _import(c)
    r = c.put(f"/api/forge/workflows/{wid}/cover", content=_png((16, 16)))
    assert r.status_code == 200, r.text
    got = c.get(f"/api/forge/workflows/{wid}/cover")
    assert got.status_code == 200 and got.headers["content-type"] == "image/png" and got.content == _png((16, 16))
    c.put(f"/api/forge/workflows/{wid}/cover", content=_png((32, 32)))
    assert c.get(f"/api/forge/workflows/{wid}/cover").content == _png((32, 32))
    assert len(list((tmp_path / "forge" / "covers").glob(f"{wid}.*"))) == 1
    assert c.delete(f"/api/forge/workflows/{wid}/cover").status_code == 200
    assert c.get(f"/api/forge/workflows/{wid}/cover").status_code == 404


def test_the_latest_completed_run_is_the_fallback(tmp_path):
    c = TestClient(appmod.create_app(tmp_path))
    wid = _import(c)
    jobs = c.app.state.jobs
    base = tmp_path / "forge" / "runs"
    for jid, when in (("a" * 32, "2026-10-27T10:00:00+00:00"), ("b" * 32, "2026-10-28T10:00:00+00:00")):
        (base / jid).mkdir(parents=True)
        (base / jid / "output.png").write_bytes(_png((8, 8) if jid[0] == "a" else (24, 24)))
        jobs.store.save({"id": jid, "kind": "app", "workflow_id": wid, "state": "COMPLETED", "finished_at": when,
                         "output_file": {"name": "output.png"}, "history": []})
    assert c.get(f"/api/forge/workflows/{wid}/cover").content == _png((24, 24))


def test_refused_pictures_and_unknown_apps(tmp_path):
    c = TestClient(appmod.create_app(tmp_path))
    wid = _import(c)
    assert c.put(f"/api/forge/workflows/{wid}/cover", content=b"<svg xmlns='http://www.w3.org/2000/svg'/>").status_code == 422
    assert c.put(f"/api/forge/workflows/{wid}/cover", content=b"").status_code == 422
    assert c.get("/api/forge/workflows/" + "f" * 32 + "/cover").status_code == 404
