"""scripts/check_environment.py: each check reports a truthful state. Nothing here starts a server or installs anything."""
import importlib.util
import json
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("check_environment", ROOT / "scripts" / "check_environment.py")
ce = importlib.util.module_from_spec(spec)
sys.modules["check_environment"] = ce
spec.loader.exec_module(ce)


def test_python_version_gate():
    assert ce.check_python((3, 12, 3)).state == ce.READY
    old = ce.check_python((3, 9, 1))
    assert old.state == ce.ATTENTION and old.required and "3.11" in old.message


def test_requirement_names_skip_comments_and_version_pins(tmp_path):
    req = tmp_path / "requirements.txt"
    req.write_text("# a comment\n\nfastapi>=0.115\nuvicorn>=0.30\n", encoding="utf-8")
    assert ce.requirement_names(req) == ["fastapi", "uvicorn"]


def test_missing_package_is_named_and_blocks(tmp_path):
    req = tmp_path / "requirements.txt"
    req.write_text("definitely-not-a-real-package-xyz>=1\n", encoding="utf-8")
    c = ce.check_dependencies(req)
    assert c.state == ce.ATTENTION and "definitely-not-a-real-package-xyz" in c.message and c.required


def test_frontend_needs_built_index_and_assets(tmp_path):
    assert ce.check_frontend(tmp_path / "missing").state == ce.ATTENTION
    (tmp_path / "dist" / "assets").mkdir(parents=True)
    (tmp_path / "dist" / "index.html").write_text("<!doctype html>", encoding="utf-8")
    assert ce.check_frontend(tmp_path / "dist").state == ce.READY


def test_comfy_url_comes_from_settings_with_default(tmp_path):
    assert ce.configured_comfy_url(tmp_path / "none.json") == ce.DEFAULT_COMFY_URL
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"comfy_url": "http://127.0.0.1:8199/"}), encoding="utf-8")
    assert ce.configured_comfy_url(cfg) == "http://127.0.0.1:8199"
    cfg.write_text("not json", encoding="utf-8")
    assert ce.configured_comfy_url(cfg) == ce.DEFAULT_COMFY_URL


def _closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_comfy_unreachable_is_not_connected_not_an_error():
    c = ce.probe_comfy(f"http://127.0.0.1:{_closed_port()}", timeout=1)
    assert c.state == ce.NOT_CONNECTED
    assert c.required is False  # ComfyUI does not block opening the app


def test_port_held_by_something_else_is_reported_without_being_touched():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = s.getsockname()[1]
        c = ce.check_port(port)
    assert c.state == ce.ATTENTION and str(port) in c.message

