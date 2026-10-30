"""Forge Milestone 5: the app library, duplicate, and .forgeapp packages (export, safe import)."""
import copy
import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openfield import api_forge
from openfield.app import create_app
from openfield.forge import object_info, package

FIX = Path(__file__).parent / "fixtures" / "forge"
ZIMAGE = (FIX / "image_z_image_turbo.json").read_bytes()


@pytest.fixture(autouse=True)
def bundled_info(monkeypatch):
    monkeypatch.setattr(api_forge, "_info", lambda request: object_info.load(Path("__no_such_home__")))


def client_for(home: Path) -> TestClient:
    return TestClient(create_app(home))


def new_app(c, name="z.json"):
    wid = c.post(f"/api/forge/workflows?name={name}", content=ZIMAGE).json()["workflow"]["id"]
    spec = c.get(f"/api/forge/workflows/{wid}/spec").json()["spec"]
    spec["components"][0]["label"] = "Describe the scene"
    assert c.put(f"/api/forge/workflows/{wid}/spec", json={"spec": spec, "base_revision": 0}).status_code == 200
    return wid


def export(c, wid) -> bytes:
    r = c.get(f"/api/forge/workflows/{wid}/export")
    assert r.status_code == 200, r.text
    return r.content


def zipped(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for n, d in entries.items():
            z.writestr(n, d)
    return buf.getvalue()


def parts_of(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return {n: z.read(n) for n in z.namelist()}


def edited_package(data: bytes, **changes) -> bytes:
    """A package with some parts replaced. Checksums are NOT updated, so the reader must catch the change."""
    p = parts_of(data)
    for k, v in changes.items():
        p[k.replace("__", "/").replace("_json", ".json")] = v
    return zipped(p)


def test_export_requires_a_saved_app(tmp_path):
    c = client_for(tmp_path / "a")
    wid = c.post("/api/forge/workflows?name=z.json", content=ZIMAGE).json()["workflow"]["id"]
    r = c.get(f"/api/forge/workflows/{wid}/export")
    assert r.status_code == 409 and "save the app" in r.json()["detail"]


def test_package_contents_are_fixed_and_contain_no_paths_or_weights(tmp_path):
    c = client_for(tmp_path / "a")
    wid = new_app(c)
    data = export(c, wid)
    p = parts_of(data)
    assert set(p) == set(package.ENTRIES)
    m = json.loads(p["manifest.json"])
    assert m["format"] == "forgeapp" and m["schema_version"] == 1 and m["name"] == "z"
    assert m["workflow_sha256"] == __import__("hashlib").sha256(ZIMAGE).hexdigest()
    deps = json.loads(p["dependencies.json"])
    assert {d["file"] for d in deps["model_files"]} >= {"z_image_turbo_bf16.safetensors"}
    everything = b"".join(p.values())
    for forbidden in (b"D:\\", b"C:\\", b"/home/", b"api_prompt", b"output.png", b"run.json"):
        assert forbidden not in everything, forbidden


def test_round_trip_into_a_fresh_home_restores_the_app(tmp_path):
    a = client_for(tmp_path / "a")
    wid = new_app(a)
    original_spec = a.get(f"/api/forge/workflows/{wid}/spec").json()["spec"]
    data = export(a, wid)
    b = client_for(tmp_path / "b")
    r = b.post("/api/forge/packages/import?name=x.forgeapp", content=data)
    assert r.status_code == 201, r.text
    body = r.json()
    new_id = body["workflow"]["id"]
    assert new_id != wid and body["workflow"]["name"] == "z" and body["spec_revision"] == 1
    got = b.get(f"/api/forge/workflows/{new_id}/spec").json()
    assert got["source"] == "saved" and got["spec"]["workflow_id"] == new_id
    assert {**got["spec"], "workflow_id": None} == {**original_spec, "workflow_id": None}
    assert got["validation"]["status"] in ("valid", "needs_attention")
    src = b.get(f"/api/forge/workflows/{new_id}/analysis").status_code
    assert src == 200


def test_import_never_overwrites_it_adds_a_numbered_copy(tmp_path):
    c = client_for(tmp_path / "a")
    wid = new_app(c)
    data = export(c, wid)
    first = c.post("/api/forge/packages/import", content=data).json()["workflow"]
    second = c.post("/api/forge/packages/import", content=data).json()["workflow"]
    assert len({wid, first["id"], second["id"]}) == 3
    assert {first["name"], second["name"]} == {"z (2)", "z (3)"}
    assert c.get(f"/api/forge/workflows/{wid}/spec").json()["source"] == "saved"


def test_duplicate_is_independent_of_the_original(tmp_path):
    c = client_for(tmp_path / "a")
    wid = new_app(c)
    before = c.get(f"/api/forge/workflows/{wid}/spec").json()["spec"]
    dup = c.post(f"/api/forge/workflows/{wid}/duplicate").json()["workflow"]
    assert dup["id"] != wid and dup["name"] == "z copy"
    spec = c.get(f"/api/forge/workflows/{dup['id']}/spec").json()["spec"]
    assert spec["components"][0]["label"] == "Describe the scene" and spec["workflow_id"] == dup["id"]
    spec["components"][0]["label"] = "Only in the copy"
    assert c.put(f"/api/forge/workflows/{dup['id']}/spec", json={"spec": spec, "base_revision": 1}).status_code == 200
    assert c.get(f"/api/forge/workflows/{wid}/spec").json()["spec"] == before


def test_duplicate_of_an_unsaved_app_is_refused(tmp_path):
    c = client_for(tmp_path / "a")
    wid = c.post("/api/forge/workflows?name=z.json", content=ZIMAGE).json()["workflow"]["id"]
    assert c.post(f"/api/forge/workflows/{wid}/duplicate").status_code == 409


def refused(c, data: bytes, fragment: str):
    r = c.post("/api/forge/packages/import", content=data)
    assert r.status_code == 422, (r.status_code, r.text)
    assert fragment in r.json()["detail"], r.json()["detail"]


@pytest.fixture
def good(tmp_path):
    c = client_for(tmp_path / "a")
    wid = new_app(c)
    return c, export(c, wid)


def test_refuses_path_traversal_and_absolute_paths(good, tmp_path):
    c, data = good
    p = parts_of(data)
    refused(c, zipped({**p, "../evil.txt": b"x"}), "unsafe path")
    refused(c, zipped({**p, "/abs.txt": b"x"}), "unsafe path")
    refused(c, zipped({**p, "C:evil.txt": b"x"}), "unsafe path")
    r = c.post("/api/forge/packages/import", content=zipped({**p, "sub\\evil.txt": b"x"}))
    assert r.status_code == 422      # zip writers turn backslashes into slashes; the entry is then refused as an extra file


def test_refuses_any_extra_file(good):
    c, data = good
    refused(c, zipped({**parts_of(data), "run.exe": b"MZ"}), "not part of the format")
    refused(c, zipped({**parts_of(data), "output.png": b"x"}), "not part of the format")


def test_refuses_folder_entries(good):
    c, data = good
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n, d in parts_of(data).items():
            z.writestr(n, d)
        z.writestr("workflow/", b"")
    refused(c, buf.getvalue(), "folder entry")


def test_refuses_a_newer_format_version(good):
    c, data = good
    m = json.loads(parts_of(data)["manifest.json"])
    m["schema_version"] = 2
    refused(c, zipped({**parts_of(data), "manifest.json": json.dumps(m).encode()}), "format version")


def test_refuses_a_changed_part(good):
    c, data = good
    spec = json.loads(parts_of(data)["ui-spec.json"])
    spec["components"][0]["label"] = "tampered"
    refused(c, zipped({**parts_of(data), "ui-spec.json": json.dumps(spec).encode()}), "checksum")


def test_refuses_a_workflow_that_does_not_match_its_checksum(good):
    c, data = good
    m = json.loads(parts_of(data)["manifest.json"])
    m["workflow_sha256"] = "0" * 64
    refused(c, zipped({**parts_of(data), "manifest.json": json.dumps(m).encode()}), "checksum")


def test_refuses_controls_that_are_not_valid(good):
    c, data = good
    spec = json.loads(parts_of(data)["ui-spec.json"])
    spec["components"][0]["type"] = "run_shell"
    m = json.loads(parts_of(data)["manifest.json"])
    m["entries"]["ui-spec.json"] = __import__("hashlib").sha256(json.dumps(spec, indent=2).encode()).hexdigest()
    refused(c, zipped({**parts_of(data), "ui-spec.json": json.dumps(spec, indent=2).encode(), "manifest.json": json.dumps(m).encode()}),
            "controls are not valid")


def test_refuses_a_missing_part(good):
    c, data = good
    p = parts_of(data)
    del p["README.md"]
    refused(c, zipped(p), "missing README.md")


def test_refuses_not_a_zip_and_not_a_package(good):
    c, _ = good
    refused(c, b"hello", "not a valid .forgeapp")
    m = {"format": "something-else", "schema_version": 1}
    refused(c, zipped({"manifest.json": json.dumps(m).encode()}), "missing")


def test_refuses_an_archive_that_expands_too_much(good):
    c, data = good
    big = zipped({**parts_of(data), "README.md": b"0" * (5 * 1024 * 1024)})
    refused(c, big, "unreasonable size")


def test_refuses_a_package_over_the_size_limit(good, monkeypatch):
    c, data = good
    monkeypatch.setattr(package, "MAX_PACKAGE_BYTES", 100)
    r = c.post("/api/forge/packages/import", content=data)
    assert r.status_code == 413


def test_a_refused_import_creates_nothing(good, tmp_path):
    c, data = good
    before = len(c.get("/api/forge/workflows").json()["workflows"])
    c.post("/api/forge/packages/import", content=zipped({**parts_of(data), "run.exe": b"x"}))
    assert len(c.get("/api/forge/workflows").json()["workflows"]) == before


def test_unique_names_helper():
    assert package._unique("a", []) == "a"
    assert package._unique("a", ["A"]) == "a (2)"
    assert package._unique("a", ["a", "a (2)"]) == "a (3)"
    assert package._unique("a (2)", ["a (2)"]) == "a (3)"


def test_safe_filenames():
    name = package.safe_filename("My App: v2/../x")
    assert name.endswith(".forgeapp") and ".." not in name and "/" not in name and " " not in name
    assert package.safe_filename("...") == "app.forgeapp"
