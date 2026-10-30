"""Forge image inputs: checking uploads, sending them to ComfyUI, and using them in a run. MOCKED ComfyUI."""
import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from openfield import api_forge
from openfield.app import create_app
from openfield.forge import object_info, uploads, uispec

FIX = Path(__file__).parent / "fixtures" / "forge"


def img(fmt="PNG", size=(16, 16)) -> bytes:
    b = io.BytesIO()
    Image.new("RGB", size, (120, 40, 40)).save(b, format=fmt)
    return b.getvalue()


def test_accepts_png_jpeg_and_webp_and_records_a_checksum():
    for fmt, ext in (("PNG", "png"), ("JPEG", "jpg"), ("WEBP", "webp")):
        info = uploads.check(img(fmt))
        assert info["format"] == ext and info["width"] == 16 and len(info["sha256"]) == 64


def test_refuses_non_images_svg_and_unsafe_formats():
    with pytest.raises(uploads.UploadError, match="not a readable image"):
        uploads.check(b"<svg xmlns='http://www.w3.org/2000/svg'></svg>")
    with pytest.raises(uploads.UploadError, match="not a readable image"):
        uploads.check(b"hello")
    with pytest.raises(uploads.UploadError, match="empty"):
        uploads.check(b"")
    b = io.BytesIO()
    Image.new("RGB", (4, 4)).save(b, format="GIF")
    with pytest.raises(uploads.UploadError, match="not accepted"):
        uploads.check(b.getvalue())


def test_refuses_oversize_and_huge_dimensions(monkeypatch):
    monkeypatch.setattr(uploads, "MAX_SIDE", 32)
    with pytest.raises(uploads.UploadError, match="at most 32"):
        uploads.check(img(size=(64, 8)))


def test_stored_name_is_derived_from_the_checksum_not_the_user_file(tmp_path):
    info = uploads.check(img())
    assert uploads.comfy_name(info) == f"openfield_{info['sha256'][:16]}.png"
    p = uploads.keep_copy(tmp_path, img(), info)
    assert p.name == f"{info['sha256']}.png" and p.read_bytes() == img()


def test_comfy_refusal_is_reported_clearly(monkeypatch):
    import httpx

    def boom(*a, **k):
        raise httpx.ConnectError("refused")
    monkeypatch.setattr(httpx, "post", boom)
    info = uploads.check(img())
    with pytest.raises(uploads.UploadError, match="Is ComfyUI running"):
        uploads.send_to_comfy("http://127.0.0.1:8188", img(), info)


def test_upload_route_returns_the_stored_name(tmp_path, monkeypatch):
    monkeypatch.setattr(uploads, "send_to_comfy", lambda url, data, info: "openfield_test.png")
    c = TestClient(create_app(tmp_path / "home"))
    r = c.post("/api/forge/uploads?name=dress.png", content=img())
    assert r.status_code == 201 and r.json()["name"] == "openfield_test.png" and r.json()["width"] == 16
    assert any((tmp_path / "home" / "forge" / "uploads").iterdir())
    bad = c.post("/api/forge/uploads?name=x.svg", content=b"<svg/>")
    assert bad.status_code == 422


def test_image_input_is_verified_and_a_wrong_kind_is_not(tmp_path):
    from openfield.forge import analysis, convert, workflow
    info = object_info.load(Path("__none__"))
    wf = workflow.parse_ui(json.loads((FIX / "utility_z_image_turbo_2k_upscaler.app.json").read_text(encoding="utf-8")))
    a = analysis.analyse(wf, convert.convert(wf, info), info)
    rec = {"id": "a" * 32, "sha256": "b" * 64, "name": "x"}
    s = uispec.generate(rec, a)
    v = uispec.validate(s, rec, a)
    assert next(r for r in v["components"] if r["component"] == "77.image")["status"] == "verified"
    s.components[0].binding = uispec.Binding(node="87:71", input="text")   # a text input bound as an image
    s.components[0].type = "image_upload"
    v2 = uispec.validate(s, rec, a)
    assert v2["components"][0]["status"] == "invalid"
