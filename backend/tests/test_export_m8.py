"""Milestone 8 tests: export contents, round trip into a fresh home, refusal of tampered or hostile archives, diagnostics.

No model weights are created or read. Placeholder project content only.
"""
import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from openfield.app import create_app


def png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), (40, 90, 160)).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def home(tmp_path):
    return tmp_path / "home"


@pytest.fixture
def client(home):
    return TestClient(create_app(home))


def project_with_asset(c):
    p = c.post("/api/projects", json={"title": "Export me"}).json()
    pid, rev = p["id"], p["revision"]
    r = c.post(f"/api/projects/{pid}/entities", json={"base_revision": rev, "kind": "character", "name": "Mira"}).json()
    c.patch(f"/api/projects/{pid}/entities/{r['entity_id']}", json={"base_revision": 1, "fields": {"description": "short dark hair"}})
    up = c.post(f"/api/projects/{pid}/assets?label=ref.png", content=png()).json()["asset"]
    return pid, up


def zip_names(data: bytes):
    return zipfile.ZipFile(io.BytesIO(data)).namelist()


def test_export_contains_project_manifests_and_no_weights(client):
    pid, asset = project_with_asset(client)
    r = client.get(f"/api/projects/{pid}/export")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    names = zip_names(r.content)
    assert "project/project.json" in names and "project/assets.json" in names
    assert f"project/{asset['rel_path']}" in names
    for m in ("manifests/models.json", "manifests/dependencies.json", "manifests/reproducibility.json", "README.txt"):
        assert m in names
    assert not any(n.endswith((".safetensors", ".gguf", ".ckpt", ".pth")) for n in names)


def test_export_without_assets_leaves_images_out(client):
    pid, asset = project_with_asset(client)
    names = zip_names(client.get(f"/api/projects/{pid}/export?include_assets=false").content)
    assert f"project/{asset['rel_path']}" not in names


def test_export_has_no_absolute_paths(client, home):
    pid, _ = project_with_asset(client)
    z = zipfile.ZipFile(io.BytesIO(client.get(f"/api/projects/{pid}/export").content))
    blob = b"".join(z.read(n) for n in z.namelist() if n.endswith((".json", ".txt")))
    assert str(home).encode() not in blob
    assert b"C:\\" not in blob and b"D:\\" not in blob


def test_round_trip_into_a_fresh_home_restores_the_project(client, tmp_path):
    pid, asset = project_with_asset(client)
    data = client.get(f"/api/projects/{pid}/export").content
    fresh_home = tmp_path / "other-machine"
    fresh = TestClient(create_app(fresh_home))
    r = fresh.post("/api/projects/import", content=data)
    assert r.status_code == 201, r.text
    new_id = r.json()["project_id"]
    assert new_id != pid
    got = fresh.get(f"/api/projects/{new_id}").json()
    assert got["title"] == "Export me"
    assert [e["name"] for e in got["entities"]] == ["Mira"]
    a = next(x for x in got["assets"] if x["label"] == "ref.png")
    assert a["status"] == "ok"
    assert fresh.get(f"/api/projects/{new_id}/assets/{a['id']}/file").content == png()


def test_tampered_asset_is_refused(client, tmp_path):
    pid, asset = project_with_asset(client)
    buf = io.BytesIO()
    src = zipfile.ZipFile(io.BytesIO(client.get(f"/api/projects/{pid}/export").content))
    with zipfile.ZipFile(buf, "w") as out:
        for n in src.namelist():
            data = src.read(n)
            if n.startswith("project/assets/images/"):
                data = data[:-4] + b"\x00\x00\x00\x00"  # same size, different bytes
            out.writestr(n, data)
    fresh = TestClient(create_app(tmp_path / "x"))
    r = fresh.post("/api/projects/import", content=buf.getvalue())
    assert r.status_code == 400 and "checksum" in r.json()["detail"]


def test_path_traversal_in_archive_is_refused(client, tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as out:
        out.writestr("project/project.json", json.dumps({"schema_version": 3, "id": "x"}))
        out.writestr("../../escape.txt", "nope")
    r = TestClient(create_app(tmp_path / "y")).post("/api/projects/import", content=buf.getvalue())
    assert r.status_code == 400 and "unsafe path" in r.json()["detail"]


def test_asset_outside_image_folder_is_refused(client, tmp_path):
    buf = io.BytesIO()
    manifest = {"schema_version": 3, "id": "x", "title": "t", "created_at": "t", "updated_at": "t", "revision": 1}
    assets = {"schema_version": 3, "assets": [{"id": "a" * 32, "rel_path": "project.json", "mime": "image/png",
                                              "size": 2, "sha256": "0" * 64, "created_at": "t"}]}
    with zipfile.ZipFile(buf, "w") as out:
        out.writestr("project/project.json", json.dumps(manifest))
        out.writestr("project/assets.json", json.dumps(assets))
    r = TestClient(create_app(tmp_path / "z")).post("/api/projects/import", content=buf.getvalue())
    assert r.status_code == 400 and "outside" in r.json()["detail"]


def test_newer_schema_is_refused_on_import(client, tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as out:
        out.writestr("project/project.json", json.dumps({"schema_version": 99, "id": "x", "title": "t"}))
    r = TestClient(create_app(tmp_path / "w")).post("/api/projects/import", content=buf.getvalue())
    assert r.status_code == 400 and "newer Open Workflow" in r.json()["detail"]


def test_not_a_zip_is_refused(client, tmp_path):
    r = TestClient(create_app(tmp_path / "v")).post("/api/projects/import", content=b"hello")
    assert r.status_code == 400


def test_diagnostics_bundle_redacts_secrets(client, home):
    log = home / "logs"
    log.mkdir(parents=True, exist_ok=True)
    (log / "openworkflow.log").write_text("ok line\napi_key=sk-SECRET123 token: abc999\n", encoding="utf-8")
    r = client.get("/api/diagnostics")
    assert r.status_code == 200
    z = zipfile.ZipFile(io.BytesIO(r.content))
    tail = z.read("log-tail.txt").decode("utf-8")
    assert "SECRET123" not in tail and "abc999" not in tail and "[REDACTED]" in tail
    info = json.loads(z.read("info.json"))
    assert "Nothing in this file is sent anywhere" in info["note"]
