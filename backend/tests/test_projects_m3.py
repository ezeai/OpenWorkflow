"""Milestone 3 tests: projects, entities, versions, assets, recovery, restart survival.

All tests use real files on a temporary disk and real Pillow image decoding. Nothing is mocked.
"""
import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from openfield.app import create_app
from openfield.projects.assets import AssetRegistry
from openfield.projects.schema import ProjectMeta, new_id
from openfield.projects.store import ProjectStore, Unreadable


def png_bytes(size=(64, 48), color=(200, 40, 40)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def home(tmp_path):
    return tmp_path / "home"


@pytest.fixture
def client(home):
    return TestClient(create_app(home))


def new_client(home):
    """A brand-new application instance on the same storage: simulates an app restart."""
    return TestClient(create_app(home))


def make_project(c, title="Night Market"):
    r = c.post("/api/projects", json={"title": title, "description": "short film"})
    assert r.status_code == 201, r.text
    return r.json()


def make_entity(c, pid, rev, kind, name):
    r = c.post(f"/api/projects/{pid}/entities", json={"base_revision": rev, "kind": kind, "name": name})
    assert r.status_code == 201, r.text
    return r.json()["entity_id"], r.json()["project"]


def upload(c, pid, data: bytes, label="ref.png"):
    return c.post(f"/api/projects/{pid}/assets?label={label}", content=data,
                  headers={"Content-Type": "application/octet-stream"})


# ---------------------------------------------------------------- acceptance: restart survival

def test_project_with_characters_worlds_and_references_survives_restart(home, client):
    p = make_project(client)
    pid, rev = p["id"], p["revision"]
    cid, p = make_entity(client, pid, rev, "character", "Mira")
    rev = p["revision"]
    r = client.patch(f"/api/projects/{pid}/entities/{cid}", json={
        "base_revision": next(e for e in p["entities"] if e["id"] == cid)["revision"],
        "fields": {"hair": "short silver hair", "identity_lock": "strong"}})
    assert r.status_code == 200, r.text
    wid, p = make_entity(client, pid, r.json()["revision"], "world", "Rain street")
    up = upload(client, pid, png_bytes(), label="mira-face.png")
    assert up.status_code == 201, up.text
    aid = up.json()["asset"]["id"]
    ch = next(e for e in r.json()["entities"] if e["id"] == cid)
    r2 = client.patch(f"/api/projects/{pid}/entities/{cid}", json={
        "base_revision": ch["revision"], "refs": [{"asset_id": aid, "role": "face"}]})
    assert r2.status_code == 200, r2.text

    restarted = new_client(home)
    got = restarted.get(f"/api/projects/{pid}")
    assert got.status_code == 200
    body = got.json()
    char = next(e for e in body["entities"] if e["id"] == cid)
    assert char["fields"]["hair"] == "short silver hair" and char["fields"]["identity_lock"] == "strong"
    assert char["refs"] == [{"asset_id": aid, "role": "face"}]
    assert any(e["id"] == wid and e["kind"] == "world" for e in body["entities"])
    asset = next(a for a in body["assets"] if a["id"] == aid)
    assert asset["status"] == "ok"
    served = restarted.get(f"/api/projects/{pid}/assets/{aid}/file")
    assert served.status_code == 200 and served.headers["content-type"] == "image/png"
    assert served.content == png_bytes()


def test_project_list_survives_restart(home, client):
    make_project(client, "One")
    make_project(client, "Two")
    titles = {p["title"] for p in new_client(home).get("/api/projects").json()["projects"]}
    assert titles == {"One", "Two"}


# ---------------------------------------------------------------- schema and validation

def test_unknown_field_is_rejected(client):
    p = make_project(client)
    cid, p = make_entity(client, p["id"], p["revision"], "character", "A")
    r = client.patch(f"/api/projects/{p['id']}/entities/{cid}",
                     json={"base_revision": 1, "fields": {"secret_backdoor": "x"}})
    assert r.status_code == 400 and "unknown field" in r.json()["detail"]


def test_identity_lock_must_be_a_known_mode(client):
    p = make_project(client)
    cid, p = make_entity(client, p["id"], p["revision"], "character", "A")
    r = client.patch(f"/api/projects/{p['id']}/entities/{cid}",
                     json={"base_revision": 1, "fields": {"identity_lock": "guaranteed"}})
    assert r.status_code == 400


def test_overlong_field_rejected(client):
    p = make_project(client)
    cid, p = make_entity(client, p["id"], p["revision"], "world", "W")
    r = client.patch(f"/api/projects/{p['id']}/entities/{cid}",
                     json={"base_revision": 1, "fields": {"description": "x" * 4001}})
    assert r.status_code == 400


def test_role_not_valid_for_kind_rejected(client):
    p = make_project(client)
    cid, p = make_entity(client, p["id"], p["revision"], "world", "W")
    aid = upload(client, p["id"], png_bytes()).json()["asset"]["id"]
    r = client.patch(f"/api/projects/{p['id']}/entities/{cid}",
                     json={"base_revision": 1, "refs": [{"asset_id": aid, "role": "face"}]})
    assert r.status_code == 400  # 'face' is a character role only


def test_reference_to_unknown_asset_rejected(client):
    p = make_project(client)
    cid, p = make_entity(client, p["id"], p["revision"], "character", "A")
    r = client.patch(f"/api/projects/{p['id']}/entities/{cid}",
                     json={"base_revision": 1, "refs": [{"asset_id": new_id(), "role": "face"}]})
    assert r.status_code == 400


def test_references_cannot_cross_projects(client):
    a = make_project(client, "A")
    b = make_project(client, "B")
    aid = upload(client, a["id"], png_bytes()).json()["asset"]["id"]
    cid, pb = make_entity(client, b["id"], b["revision"], "character", "Other")
    r = client.patch(f"/api/projects/{b['id']}/entities/{cid}",
                     json={"base_revision": 1, "refs": [{"asset_id": aid, "role": "face"}]})
    assert r.status_code == 400


def test_bad_ids_never_reach_the_filesystem(client):
    p = make_project(client)
    # ids that reach the API route (other paths fall through to the SPA page, which is not a file read)
    for bad in ("ABC", "x" * 40, "..secret", "%2E%2E"):
        r = client.get(f"/api/projects/{p['id']}/assets/{bad}/file")
        assert r.status_code in (400, 404), (bad, r.status_code)
    assert client.get("/api/projects/not-an-id").status_code in (400, 404)


# ---------------------------------------------------------------- images

def test_non_image_bytes_rejected_even_with_image_name(client):
    p = make_project(client)
    r = upload(client, p["id"], b"<script>alert(1)</script>", label="evil.png")
    assert r.status_code == 400 and "not a readable image" in r.json()["detail"]


def test_svg_is_rejected(client):
    p = make_project(client)
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>x</script></svg>'
    assert upload(client, p["id"], svg, label="a.svg").status_code == 400


def test_empty_upload_rejected(client):
    p = make_project(client)
    assert upload(client, p["id"], b"").status_code == 400


def test_oversize_upload_rejected(client):
    p = make_project(client)
    big = b"\0" * (25 * 1024 * 1024 + 1)
    assert upload(client, p["id"], big).status_code == 413


def test_upload_never_overwrites_and_keeps_original_name_out_of_path(client, home):
    p = make_project(client)
    a1 = upload(client, p["id"], png_bytes(), label="../../escape.png").json()["asset"]
    a2 = upload(client, p["id"], png_bytes(), label="../../escape.png").json()["asset"]
    assert a1["id"] != a2["id"] and a1["rel_path"] != a2["rel_path"]
    assert "escape" not in a1["rel_path"] and a1["label"] == "../../escape.png"
    assert (home / "projects" / p["id"] / a1["rel_path"]).is_file()


# ---------------------------------------------------------------- integrity

def test_missing_file_is_reported_not_hidden(home, client):
    p = make_project(client)
    a = upload(client, p["id"], png_bytes()).json()["asset"]
    (home / "projects" / p["id"] / a["rel_path"]).unlink()
    got = client.get(f"/api/projects/{p['id']}").json()
    assert next(x for x in got["assets"] if x["id"] == a["id"])["status"] == "missing"
    assert client.get(f"/api/projects/{p['id']}/assets/{a['id']}/file").status_code == 400


def test_changed_contents_detected_only_by_deep_verify(home, client):
    p = make_project(client)
    a = upload(client, p["id"], png_bytes(color=(1, 2, 3))).json()["asset"]
    path = home / "projects" / p["id"] / a["rel_path"]
    same_size = bytearray(path.read_bytes())
    same_size[-5] ^= 0xFF  # flip one byte, keep the size
    path.write_bytes(bytes(same_size))
    shallow = client.post(f"/api/projects/{p['id']}/assets/{a['id']}/verify", json={"deep": False}).json()
    deep = client.post(f"/api/projects/{p['id']}/assets/{a['id']}/verify", json={"deep": True}).json()
    assert shallow["status"] == "ok"
    assert deep["status"] == "hash_mismatch"


def test_size_change_detected_on_listing(home, client):
    p = make_project(client)
    a = upload(client, p["id"], png_bytes()).json()["asset"]
    path = home / "projects" / p["id"] / a["rel_path"]
    path.write_bytes(path.read_bytes() + b"tail")
    got = client.get(f"/api/projects/{p['id']}").json()
    assert next(x for x in got["assets"] if x["id"] == a["id"])["status"] == "size_mismatch"


# ---------------------------------------------------------------- concurrency and versions

def test_stale_entity_revision_is_refused_and_nothing_changes(client):
    p = make_project(client)
    cid, p = make_entity(client, p["id"], p["revision"], "character", "A")
    first = client.patch(f"/api/projects/{p['id']}/entities/{cid}", json={"base_revision": 1, "fields": {"hair": "red"}})
    assert first.status_code == 200
    stale = client.patch(f"/api/projects/{p['id']}/entities/{cid}", json={"base_revision": 1, "fields": {"hair": "blue"}})
    assert stale.status_code == 409
    got = client.get(f"/api/projects/{p['id']}").json()
    assert next(e for e in got["entities"] if e["id"] == cid)["fields"]["hair"] == "red"


def test_stale_project_revision_refused_on_title_change(client):
    p = make_project(client)
    ok = client.patch(f"/api/projects/{p['id']}", json={"base_revision": p["revision"], "title": "Renamed"})
    assert ok.status_code == 200
    stale = client.patch(f"/api/projects/{p['id']}", json={"base_revision": p["revision"], "title": "Lost update"})
    assert stale.status_code == 409 and client.get(f"/api/projects/{p['id']}").json()["title"] == "Renamed"


def test_only_real_content_changes_create_versions(client):
    p = make_project(client)
    cid, p = make_entity(client, p["id"], p["revision"], "character", "A")
    e = next(x for x in p["entities"] if x["id"] == cid)
    r = client.patch(f"/api/projects/{p['id']}/entities/{cid}", json={"base_revision": e["revision"], "fields": {"hair": "red"}})
    e = next(x for x in r.json()["entities"] if x["id"] == cid)
    noop = client.patch(f"/api/projects/{p['id']}/entities/{cid}", json={"base_revision": e["revision"], "fields": {"hair": "red"}})
    e2 = next(x for x in noop.json()["entities"] if x["id"] == cid)
    assert len(e2["versions"]) == 2  # created + one real edit; the no-op added nothing
    assert e2["current_version"] == 2


def test_restore_creates_new_version_and_keeps_history(client):
    p = make_project(client)
    cid, p = make_entity(client, p["id"], p["revision"], "character", "A")
    r1 = client.patch(f"/api/projects/{p['id']}/entities/{cid}", json={"base_revision": 1, "fields": {"hair": "red"}})
    e = next(x for x in r1.json()["entities"] if x["id"] == cid)
    r2 = client.patch(f"/api/projects/{p['id']}/entities/{cid}", json={"base_revision": e["revision"], "fields": {"hair": "blue"}})
    e = next(x for x in r2.json()["entities"] if x["id"] == cid)
    rest = client.post(f"/api/projects/{p['id']}/entities/{cid}/restore", json={"base_revision": e["revision"], "version": 2})
    assert rest.status_code == 200
    got = next(x for x in rest.json()["entities"] if x["id"] == cid)
    assert got["fields"]["hair"] == "red"
    assert got["current_version"] == 4 and len(got["versions"]) == 4
    hist = client.get(f"/api/projects/{p['id']}/entities/{cid}/versions").json()["versions"]
    assert [v["fields"].get("hair") for v in hist] == ["red", "blue", "red", None]  # newest first


def test_archive_keeps_entity_and_its_references(client):
    p = make_project(client)
    cid, p = make_entity(client, p["id"], p["revision"], "prop", "Lantern")
    r = client.patch(f"/api/projects/{p['id']}/entities/{cid}", json={"base_revision": 1, "archived": True})
    e = next(x for x in r.json()["entities"] if x["id"] == cid)
    assert e["archived"] is True and e["name"] == "Lantern"


# ---------------------------------------------------------------- recovery

def test_corrupt_manifest_recovers_from_backup_and_keeps_backup_valid(home, client):
    p = make_project(client)
    pid = p["id"]
    cid, p = make_entity(client, pid, p["revision"], "character", "Kept")  # writes a backup of the previous state
    manifest = home / "projects" / pid / "project.json"
    manifest.write_text("{ this is damaged")
    got = new_client(home).get(f"/api/projects/{pid}")
    assert got.status_code == 200
    body = got.json()
    assert body["recovery_note"] and "restored from the previous saved copy" in body["recovery_note"]
    # a write after damage must not overwrite the good backup with the damaged file
    r = new_client(home).patch(f"/api/projects/{pid}", json={"base_revision": body["revision"], "title": "After damage"})
    assert r.status_code == 200
    bak = json.loads((home / "projects" / pid / "project.json.bak").read_text(encoding="utf-8"))
    assert bak["title"] != "After damage"  # backup still holds a good earlier state


def test_both_copies_corrupt_is_reported_not_crashing(home, client):
    p = make_project(client)
    d = home / "projects" / p["id"]
    (d / "project.json").write_text("{bad")
    (d / "project.json.bak").write_text("{also bad")
    fresh = new_client(home)
    assert fresh.get(f"/api/projects/{p['id']}").status_code == 500
    listed = fresh.get("/api/projects").json()["projects"]
    assert any(x["id"] == p["id"] and x["readable"] is False for x in listed)


def test_manifest_from_newer_schema_is_refused(home, client):
    p = make_project(client)
    path = home / "projects" / p["id"] / "project.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["schema_version"] = 99
    path.write_text(json.dumps(raw))
    (home / "projects" / p["id"] / "project.json.bak").unlink(missing_ok=True)
    r = new_client(home).get(f"/api/projects/{p['id']}")
    assert r.status_code == 500 and "newer Open Workflow" in r.json()["detail"]


def test_leftover_temp_file_does_not_affect_the_project(home, client):
    p = make_project(client)
    (home / "projects" / p["id"] / "tmpabc.tmp").write_text("partial write")
    assert new_client(home).get(f"/api/projects/{p['id']}").json()["title"] == "Night Market"


def test_delete_requires_exact_title_then_removes_folder(home, client):
    p = make_project(client)
    assert client.post(f"/api/projects/{p['id']}/delete", json={"confirm_title": "wrong"}).status_code == 400
    assert (home / "projects" / p["id"]).exists()
    ok = client.post(f"/api/projects/{p['id']}/delete", json={"confirm_title": "Night Market"})
    assert ok.status_code == 200 and not (home / "projects" / p["id"]).exists()


def test_store_refuses_paths_outside_root(tmp_path):
    store = ProjectStore(tmp_path / "root")
    with pytest.raises(Exception):
        store.dir("../outside")


def test_asset_registry_rejects_path_traversal_in_manifest(tmp_path):
    proj = tmp_path / "p"
    proj.mkdir()
    (proj / "assets.json").write_text(json.dumps({"schema_version": 1, "assets": [{
        "id": new_id(), "rel_path": "../../outside.png", "mime": "image/png", "size": 1,
        "sha256": "0" * 64, "created_at": "t"}]}))
    reg = AssetRegistry(proj)
    with pytest.raises(Exception):
        reg.load()  # pattern rejects the entry as invalid
