"""The app's own work (canvas changes, added nodes, LoRA stack, cover) survives export, import and duplicate,
and a file that is already imported is not stored twice unless asked for."""
import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from openfield import api_forge
from openfield.app import create_app
from openfield.forge import object_info, package

FIX = Path(__file__).parent / "fixtures" / "forge"
ZIMAGE = (FIX / "image_z_image_turbo.json").read_bytes()


@pytest.fixture(autouse=True)
def bundled_info(monkeypatch):
    monkeypatch.setattr(api_forge, "_info", lambda request: object_info.load(Path("__no_such_home__")))
    # LoRA names are checked against a live ComfyUI when one is reachable; these tests must not depend on it.
    monkeypatch.setattr(api_forge, "_known_loras", lambda request: None)


def client_for(home: Path) -> TestClient:
    return TestClient(create_app(home))


def png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), (40, 90, 160)).save(buf, format="PNG")
    return buf.getvalue()


def app_with_work(c) -> tuple[str, str]:
    """An app with a saved spec, one canvas change, one LoRA and a cover. Returns (workflow id, first node id)."""
    wid = c.post("/api/forge/workflows?name=z.json", content=ZIMAGE).json()["workflow"]["id"]
    spec = c.get(f"/api/forge/workflows/{wid}/spec").json()["spec"]
    assert c.put(f"/api/forge/workflows/{wid}/spec", json={"spec": spec, "base_revision": 0}).status_code == 200
    node = c.get(f"/api/forge/workflows/{wid}/graph").json()["nodes"][0]["id"]
    assert c.post(f"/api/forge/workflows/{wid}/graph/ops", json={"op": "state", "node": node, "state": "muted"}).status_code == 200
    assert c.put(f"/api/forge/workflows/{wid}/loras", json={"items": [{"name": "style.safetensors", "strength": 0.5}]}).status_code == 200
    assert c.put(f"/api/forge/workflows/{wid}/cover", content=png(), headers={"Content-Type": "image/png"}).status_code == 200
    return wid, node


def states(c, wid) -> dict:
    return {n["id"]: n["state"] for n in c.get(f"/api/forge/workflows/{wid}/graph").json()["nodes"]}


def test_package_carries_canvas_changes_loras_and_cover(tmp_path):
    a = client_for(tmp_path / "a")
    wid, node = app_with_work(a)
    data = a.get(f"/api/forge/workflows/{wid}/export").content
    names = set(__import__("zipfile").ZipFile(io.BytesIO(data)).namelist())
    assert {"workspace/canvas.json", "workspace/loras.json", "workspace/cover.png"} <= names

    b = client_for(tmp_path / "b")
    got = b.post("/api/forge/packages/import", content=data).json()
    new = got["workflow"]["id"]
    assert set(got["carried"]) == {"workspace/canvas.json", "workspace/loras.json", "workspace/cover.png"}
    assert states(b, new)[node] == "muted"
    assert b.get(f"/api/forge/workflows/{new}/loras").json()["items"][0]["name"] == "style.safetensors"
    assert b.get(f"/api/forge/workflows/{new}/cover").status_code == 200


def test_duplicate_copies_the_work_and_leaves_the_original_alone(tmp_path):
    c = client_for(tmp_path / "a")
    wid, node = app_with_work(c)
    dup = c.post(f"/api/forge/workflows/{wid}/duplicate").json()["workflow"]["id"]
    assert states(c, dup)[node] == "muted"
    assert c.get(f"/api/forge/workflows/{dup}/loras").json()["items"]
    c.post(f"/api/forge/workflows/{dup}/graph/ops/undo")
    assert states(c, wid)[node] == "muted"   # the original kept its change; undo on the copy did not touch it


def test_a_package_with_a_damaged_canvas_file_is_refused(tmp_path):
    a = client_for(tmp_path / "a")
    wid, _ = app_with_work(a)
    data = a.get(f"/api/forge/workflows/{wid}/export").content
    parts = {n: __import__("zipfile").ZipFile(io.BytesIO(data)).read(n) for n in __import__("zipfile").ZipFile(io.BytesIO(data)).namelist()}
    parts["workspace/canvas.json"] = b"not json"
    with pytest.raises(package.PackageError):
        package.read(_zip(parts))


def _zip(parts: dict[str, bytes]) -> bytes:
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for n, d in parts.items():
            z.writestr(n, d)
    return buf.getvalue()


def test_importing_the_same_file_again_is_not_stored_twice(tmp_path):
    c = client_for(tmp_path / "a")
    first = c.post("/api/forge/workflows?name=z.json", content=ZIMAGE).json()
    again = c.post("/api/forge/workflows?name=z.json", content=ZIMAGE).json()
    assert first["duplicate"] is False and again["duplicate"] is True
    assert again["workflow"]["id"] == first["workflow"]["id"]
    copy = c.post("/api/forge/workflows?name=z.json&copy=true", content=ZIMAGE).json()
    assert copy["duplicate"] is False and copy["workflow"]["id"] != first["workflow"]["id"]


def test_preview_describes_a_file_without_storing_it(tmp_path):
    c = client_for(tmp_path / "a")
    p = c.post("/api/forge/workflows/preview?name=z.json", content=ZIMAGE).json()
    assert p["format"] == "ui" and p["nodes"] > 0 and p["already_imported"] == []
    assert c.get("/api/forge/workflows").json()["workflows"] == []
    c.post("/api/forge/workflows?name=z.json", content=ZIMAGE)
    again = c.post("/api/forge/workflows/preview?name=z.json", content=ZIMAGE).json()
    assert len(again["already_imported"]) == 1


def test_preview_refuses_what_is_not_a_workflow(tmp_path):
    c = client_for(tmp_path / "a")
    assert c.post("/api/forge/workflows/preview", content=b"{not json").status_code == 422
    assert c.post("/api/forge/workflows/preview", content=b'{"hello": 1}').status_code == 422
