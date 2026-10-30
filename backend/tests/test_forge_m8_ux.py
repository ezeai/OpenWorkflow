"""Forge UX improvements: run history, image roles in receipts, and serving image inputs back. Mocked ComfyUI."""
import copy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openfield import api_forge
from openfield.app import create_app
from openfield.forge import object_info, uispec, uploads

FIX = Path(__file__).parent / "fixtures" / "forge"
ZIMAGE = (FIX / "image_z_image_turbo.json").read_bytes()


@pytest.fixture
def c(tmp_path, monkeypatch):
    monkeypatch.setattr(api_forge, "_info", lambda request: object_info.load(Path("__no_such_home__")))
    return TestClient(create_app(tmp_path / "home"))


def test_upload_is_served_back_by_its_name(c, tmp_path, monkeypatch):
    import io
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (8, 8), (1, 2, 3)).save(b, format="PNG")
    info = uploads.check(b.getvalue())
    uploads.keep_copy(tmp_path / "home" / "forge" / "uploads", b.getvalue(), info)
    name = uploads.comfy_name(info)
    r = c.get(f"/api/forge/uploads/{name}")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content == b.getvalue()
    r = c.get("/api/forge/uploads/..%2F..%2Fsecret.png")          # never reaches the file route: the app shell answers instead
    assert r.headers["content-type"].startswith("text/html") and b"secret" not in r.content
    assert c.get("/api/forge/uploads/openfield_0000000000000000.png").status_code == 404


def test_run_history_lists_this_apps_runs_newest_first(c, monkeypatch):
    wid = c.post("/api/forge/workflows?name=z.json", content=ZIMAGE).json()["workflow"]["id"]
    other = c.post("/api/forge/workflows?name=other.json&copy=true", content=ZIMAGE).json()["workflow"]["id"]   # a copy: the same file is caught as a duplicate otherwise
    from openfield.generation.jobs import JobStore
    store = JobStore(Path(c.app.state.services.home))
    for jid, wf, at, state in (("1" * 32, wid, "2026-10-27T01:00:00+00:00", "COMPLETED"),
                               ("2" * 32, wid, "2026-10-27T02:00:00+00:00", "FAILED"),
                               ("3" * 32, other, "2026-10-27T03:00:00+00:00", "COMPLETED")):
        store.save({"id": jid, "kind": "app", "provider": "local", "workflow_id": wf, "created_at": at, "state": state,
                    "output_file": {"name": "output.png"} if state == "COMPLETED" else None, "error": None})
    runs = c.get(f"/api/forge/workflows/{wid}/runs").json()["runs"]
    assert [r["id"] for r in runs] == ["2" * 32, "1" * 32]          # newest first, other app excluded
    assert runs[1]["receipt_url"] == f"/api/forge/runs/{'1' * 32}/receipt" and runs[0]["receipt_url"] is None
    assert c.get("/api/forge/workflows/" + "0" * 32 + "/runs").status_code == 404


def test_image_role_is_stored_and_validated():
    spec = uispec.generate({"id": "a" * 32, "sha256": "b" * 64, "name": "x"},
                           {"controls": [], "outputs": [], "task": {"family": "image-to-image"}, "readiness": "ready"})
    comp = uispec.Component(id="x", type="image_upload", label="Pose", binding=uispec.Binding(node="1", input="image"), role="layout")
    assert comp.role == "layout"
    with pytest.raises(ValueError):
        uispec.Component(id="x", type="image_upload", label="Pose", binding=uispec.Binding(node="1", input="image"), role="shell")
    assert spec.components == []
