"""AI Influencer characters: the store, the identity score, and the routes. ComfyUI is faked except in the
one live test, which runs the face script under the ComfyUI install's own Python if it is present."""
import io
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from openfield import app as appmod
from openfield.forge import characters, identity, uploads

COMFY_ROOT = Path(r"D:\ComfyUI_windows_portable_nvidia_cu128_py310\ComfyUI_windows_portable")


def _png(color=(120, 90, 60), size=(64, 64)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


# ---- the store

def test_create_and_read_back(tmp_path):
    st = characters.CharacterStore(tmp_path)
    rec = st.create("  Maya  ", "warm tone")
    assert rec["name"] == "Maya" and rec["anchor"] is None and rec["history"] == []
    assert st.get(rec["id"])["notes"] == "warm tone"
    assert [r["name"] for r in st.list()] == ["Maya"]


@pytest.mark.parametrize("name", ["", "   ", "x" * (characters.MAX_NAME + 1)])
def test_bad_names_are_refused(tmp_path, name):
    with pytest.raises(characters.CharacterError, match="name"):
        characters.CharacterStore(tmp_path).create(name)


def test_unknown_or_malformed_ids_are_refused(tmp_path):
    st = characters.CharacterStore(tmp_path)
    with pytest.raises(characters.CharacterError, match="unknown"):
        st.get("0" * 32)
    with pytest.raises(characters.CharacterError, match="unknown"):
        st.get("../../etc")


def test_history_keeps_every_check_in_order(tmp_path):
    st = characters.CharacterStore(tmp_path)
    cid = st.create("A")["id"]
    st.add_check(cid, {"score": 0.5, "passed": True})
    st.add_check(cid, {"score": 0.1, "passed": False})
    assert [h["score"] for h in st.get(cid)["history"]] == [0.5, 0.1]


# ---- the identity score (the subprocess is faked)

def _fake_run(stdout, returncode=0):
    def run(*a, **k):
        return subprocess.CompletedProcess(a[0], returncode, stdout=stdout, stderr="")
    return run


def _comfy(tmp_path):
    (tmp_path / "python_embeded").mkdir()
    (tmp_path / "python_embeded" / "python.exe").write_bytes(b"x")
    (tmp_path / "ComfyUI" / "models" / "insightface").mkdir(parents=True)
    return str(tmp_path)


def test_a_score_above_threshold_passes(tmp_path, monkeypatch):
    monkeypatch.setattr(identity.subprocess, "run", _fake_run("similarity 0.5120\n"))
    out = identity.score(_comfy(tmp_path), Path("a.png"), Path("b.png"))
    assert out == {"score": 0.512, "threshold": identity.THRESHOLD, "passed": True, "face_found": True}


def test_a_score_below_threshold_does_not_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(identity.subprocess, "run", _fake_run("similarity 0.2410\n"))
    assert identity.score(_comfy(tmp_path), Path("a.png"), Path("b.png"))["passed"] is False


def test_no_face_in_the_output_is_a_result_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(identity.subprocess, "run", _fake_run("noface candidate\n"))
    assert identity.score(_comfy(tmp_path), Path("a.png"), Path("b.png")) == {
        "score": None, "threshold": identity.THRESHOLD, "passed": False, "face_found": False}


def test_no_face_in_the_anchor_is_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(identity.subprocess, "run", _fake_run("noface reference\n"))
    with pytest.raises(identity.IdentityError, match="anchor"):
        identity.score(_comfy(tmp_path), Path("a.png"), Path("b.png"))


def test_a_missing_comfy_install_says_what_to_set(tmp_path):
    with pytest.raises(identity.IdentityError, match="ComfyUI folder"):
        identity.score(None, Path("a.png"), Path("b.png"))
    with pytest.raises(identity.IdentityError, match="python_embeded"):
        identity.score(str(tmp_path), Path("a.png"), Path("b.png"))


def test_the_live_face_script_matches_a_face_with_itself():
    py = COMFY_ROOT / "python_embeded" / "python.exe"
    ref = COMFY_ROOT / "ComfyUI" / "input" / "Maya_00756_.png"
    if not (py.is_file() and ref.is_file()):
        pytest.skip("the ComfyUI install is not present on this machine")
    out = identity.score(str(COMFY_ROOT), ref, ref)
    assert out["face_found"] is True and out["score"] > 0.9


# ---- the routes

@pytest.fixture()
def client(tmp_path, monkeypatch):
    c = TestClient(appmod.create_app(tmp_path))
    monkeypatch.setattr(uploads, "send_to_comfy", lambda url, data, info: "openfield_" + info["sha256"][:16] + ".png")
    return c


def test_create_list_and_get(client):
    r = client.post("/api/forge/characters", json={"name": "Maya", "notes": "warm"})
    assert r.status_code == 201
    cid = r.json()["character"]["id"]
    assert client.get("/api/forge/characters").json()["characters"][0]["id"] == cid
    assert client.get(f"/api/forge/characters/{cid}").json()["character"]["name"] == "Maya"


def test_a_blank_name_is_a_clear_refusal(client):
    r = client.post("/api/forge/characters", json={"name": " "})
    assert r.status_code == 422 and "name" in r.json()["detail"]


def test_anchor_must_be_an_image_and_is_kept(client):
    cid = client.post("/api/forge/characters", json={"name": "Maya"}).json()["character"]["id"]
    bad = client.post(f"/api/forge/characters/{cid}/anchor", content=b"not an image", headers={"content-type": "application/octet-stream"})
    assert bad.status_code == 422
    good = client.post(f"/api/forge/characters/{cid}/anchor?name=face.png", content=_png(), headers={"content-type": "application/octet-stream"})
    assert good.status_code == 201
    anchor = good.json()["character"]["anchor"]
    assert anchor["comfy_name"].startswith("openfield_") and Path(anchor["path"]).is_file()


def test_check_needs_an_anchor_first(client):
    cid = client.post("/api/forge/characters", json={"name": "Maya"}).json()["character"]["id"]
    r = client.post(f"/api/forge/characters/{cid}/check", json={"job_id": "a" * 32})
    assert r.status_code == 422 and "anchor" in r.json()["detail"]


def test_check_scores_a_finished_run_and_records_it(client, monkeypatch, tmp_path):
    cid = client.post("/api/forge/characters", json={"name": "Maya"}).json()["character"]["id"]
    client.post(f"/api/forge/characters/{cid}/anchor", content=_png(), headers={"content-type": "application/octet-stream"})
    jid = "b" * 32
    home = client.app.state.services.home
    (home / "forge" / "runs" / jid).mkdir(parents=True)
    (home / "forge" / "runs" / jid / "out.png").write_bytes(_png((10, 10, 10)))
    job = {"id": jid, "state": "COMPLETED", "output_file": {"name": "out.png"}}
    monkeypatch.setattr(client.app.state.jobs.store, "load", lambda j: job)
    seen = {}

    def fake_score(root, ref, cand):
        seen["ref"], seen["cand"] = ref, cand
        return {"score": 0.61, "threshold": identity.THRESHOLD, "passed": True, "face_found": True}
    monkeypatch.setattr(identity, "score", fake_score)
    r = client.post(f"/api/forge/characters/{cid}/check", json={"job_id": jid})
    assert r.status_code == 200 and r.json()["check"]["passed"] is True
    assert seen["cand"] == home / "forge" / "runs" / jid / "out.png"
    assert client.get(f"/api/forge/characters/{cid}").json()["character"]["history"][0]["job_id"] == jid


def test_check_refuses_a_run_that_did_not_finish(client, monkeypatch):
    cid = client.post("/api/forge/characters", json={"name": "Maya"}).json()["character"]["id"]
    client.post(f"/api/forge/characters/{cid}/anchor", content=_png(), headers={"content-type": "application/octet-stream"})
    monkeypatch.setattr(client.app.state.jobs.store, "load", lambda j: {"id": j, "state": "RUNNING", "output_file": None})
    r = client.post(f"/api/forge/characters/{cid}/check", json={"job_id": "c" * 32})
    assert r.status_code == 422 and "finished" in r.json()["detail"]
