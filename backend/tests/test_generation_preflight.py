"""Preflight is a check that never queues: unknown projects and shots are refused before any engine call."""
import pytest
from fastapi.testclient import TestClient

from openfield.app import create_app


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(tmp_path / "home"))


def test_preflight_for_unknown_project_is_not_found(client):
    response = client.post("/api/projects/no-such-project/shots/s1/preflight", json={"base_revision": 0})
    assert response.status_code == 404


def test_preflight_rejects_steps_out_of_range_before_checking_engine(client):
    response = client.post("/api/projects/no-such-project/shots/s1/preflight", json={"base_revision": 0, "steps": 0})
    assert response.status_code == 422
