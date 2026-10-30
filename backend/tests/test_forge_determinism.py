"""The app builder is deterministic: the same workflow always gives the same app, every control says why it exists,
and the rule version is recorded with the spec."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openfield import api_forge
from openfield.app import create_app
from openfield.forge import object_info, uispec

FIX = Path(__file__).parent / "fixtures" / "forge"
ZIMAGE = (FIX / "image_z_image_turbo.json").read_bytes()


@pytest.fixture(autouse=True)
def bundled_info(monkeypatch):
    monkeypatch.setattr(api_forge, "_info", lambda request: object_info.load(Path("__no_such_home__")))


def client_for(home: Path) -> TestClient:
    return TestClient(create_app(home))


def test_regenerating_the_same_workflow_gives_the_same_spec(tmp_path):
    c = client_for(tmp_path / "a")
    wid = c.post("/api/forge/workflows?name=z.json", content=ZIMAGE).json()["workflow"]["id"]
    first = c.post(f"/api/forge/workflows/{wid}/spec/regenerate").json()["spec"]
    second = c.post(f"/api/forge/workflows/{wid}/spec/regenerate").json()["spec"]
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_a_separate_home_gives_the_same_spec(tmp_path):
    specs = []
    for home in ("a", "b"):
        c = client_for(tmp_path / home)
        wid = c.post("/api/forge/workflows?name=z.json", content=ZIMAGE).json()["workflow"]["id"]
        spec = c.post(f"/api/forge/workflows/{wid}/spec/regenerate").json()["spec"]
        spec.pop("workflow_id")   # the id is random per home; everything else must match
        specs.append(json.dumps(spec, sort_keys=True))
    assert specs[0] == specs[1]


def test_adding_reasons_to_an_older_spec_keeps_the_user_edits(tmp_path):
    c = client_for(tmp_path / "a")
    wid = c.post("/api/forge/workflows?name=z.json", content=ZIMAGE).json()["workflow"]["id"]
    spec = c.post(f"/api/forge/workflows/{wid}/spec/regenerate").json()["spec"]
    # An older saved spec: no reasons, no rule version, and a label the user changed.
    for comp in spec["components"]:
        comp["reason"] = None
    spec["rules"] = None
    spec["components"][0]["label"] = "My own label"
    assert c.put(f"/api/forge/workflows/{wid}/spec", json={"spec": spec, "base_revision": 0}).status_code == 200

    first = c.post(f"/api/forge/workflows/{wid}/spec/add-reasons").json()
    assert set(first["changed"]) == {comp["id"] for comp in spec["components"]}
    saved = c.get(f"/api/forge/workflows/{wid}/spec").json()["spec"]
    assert saved["rules"] == uispec.RULES_VERSION
    assert saved["components"][0]["label"] == "My own label"
    assert all(comp["reason"] for comp in saved["components"])
    assert c.post(f"/api/forge/workflows/{wid}/spec/add-reasons").json()["changed"] == []


def test_every_control_says_why_it_exists_and_the_rules_are_recorded(tmp_path):
    c = client_for(tmp_path / "a")
    wid = c.post("/api/forge/workflows?name=z.json", content=ZIMAGE).json()["workflow"]["id"]
    spec = c.post(f"/api/forge/workflows/{wid}/spec/regenerate").json()["spec"]
    assert spec["rules"] == uispec.RULES_VERSION
    assert spec["components"], "the fixture should give at least one control"
    for comp in spec["components"]:
        assert comp["reason"] and comp["reason"].endswith("."), comp
