import json

import pytest
from fastapi.testclient import TestClient

from openfield.app import create_app
from openfield.config import Settings, SettingsStore
from openfield.logging_setup import RedactingFilter
import logging


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(tmp_path))


def test_health_reports_unreachable_engine_honestly(client):
    s = client.put("/api/settings", json={"comfy_url": "http://127.0.0.1:9"}).json()
    assert s["comfy_url"].endswith(":9")
    h = client.get("/api/health").json()
    assert h["engine"]["state"] == "unreachable" and h["open_weights_path"] is True


def test_remote_engine_url_rejected(client):
    r = client.put("/api/settings", json={"comfy_url": "http://example.com:8188"})
    assert r.status_code == 422 and r.json()["detail"][0]["field"] == "comfy_url"


def test_settings_persist_and_corrupt_file_is_kept(tmp_path):
    st = SettingsStore(tmp_path)
    st.save(Settings(first_run_complete=True, projects_dir=str(tmp_path / "p")))
    assert SettingsStore(tmp_path).load().first_run_complete is True
    st.path.write_text("{not json")
    assert SettingsStore(tmp_path).load().first_run_complete is False
    assert (tmp_path / "config.json.corrupt").exists()


def test_hardware_snapshot_shape(client):
    h = client.get("/api/hardware").json()
    assert h["ram_total_gb"] > 0 and isinstance(h["gpus"], list) and "ffmpeg" in h


def test_secrets_redacted():
    rec = logging.LogRecord("x", logging.INFO, "", 0, "api_key=abc123 token: zzz ok", (), None)
    RedactingFilter().filter(rec)
    assert "abc123" not in rec.msg and "zzz" not in rec.msg and "ok" in rec.msg


def test_built_ui_assets_have_correct_mime(tmp_path):
    from openfield import app as appmod
    if not appmod.FRONTEND_DIST.is_dir():
        pytest.skip("frontend not built")
    c = TestClient(appmod.create_app(tmp_path))
    js = next((appmod.FRONTEND_DIST / "assets").glob("*.js")).name
    assert c.get(f"/assets/{js}").headers["content-type"].startswith("text/javascript")
    assert c.get("/some/spa/route").headers["content-type"].startswith("text/html")
