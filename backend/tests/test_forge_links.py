"""Paste-link intake: URL and trim checks, argument building, and the clip flow with yt-dlp and ffmpeg mocked.

These tests prove OpenField's own rules. They do not contact TikTok, Instagram or X, and they do not run yt-dlp or ffmpeg.
"""
import subprocess
from pathlib import Path

import pytest

from openfield.forge import links


@pytest.mark.parametrize("url", [
    "https://www.tiktok.com/@a/video/1",
    "https://instagram.com/reel/abc",
    "https://x.com/user/status/1",
    "https://twitter.com/user/status/1",
])
def test_accepts_the_allowlisted_hosts(url):
    assert links.validate_url(url) == url


@pytest.mark.parametrize("url", [
    "http://x.com/a",                      # not https
    "https://evil.example/x",              # not allowlisted
    "https://x.com.evil.example/x",        # allowlisted name only as a prefix
    "--exec=rm -rf /",                     # option injection
    "",
    "not a url",
])
def test_refuses_other_schemes_hosts_and_option_like_input(url):
    with pytest.raises(links.LinkError):
        links.validate_url(url)


def test_trim_must_be_forward_and_at_most_fifteen_seconds():
    assert links.validate_trim(1, 10).end == 10
    with pytest.raises(links.LinkError):
        links.validate_trim(5, 5)
    with pytest.raises(links.LinkError):
        links.validate_trim(0, 16)


def test_ytdlp_puts_the_url_after_a_separator_and_caps_size():
    args = links.ytdlp_args("https://x.com/a/status/1", Path("/tmp/d"))
    assert args[-2:] == ["--", "https://x.com/a/status/1"]
    assert "--no-playlist" in args and "--max-filesize" in args
    assert "--cookies" not in args and "--cookies-from-browser" not in args


def test_ffmpeg_trims_to_the_requested_length_and_normalises_the_format():
    args = links.ffmpeg_args(Path("in.mp4"), Path("out.mp4"), links.Trim(2.0, 7.5))
    assert args[args.index("-t") + 1] == "5.500"
    assert "libx264" in args and "aac" in args and "yuv420p" in args


def test_reports_missing_tools_by_name(monkeypatch):
    monkeypatch.setattr(links.shutil, "which", lambda name: None)
    assert links.missing_tools() == ["yt-dlp", "ffmpeg"]
    assert links.missing_tools("/bin/yt", "/bin/ff") == []


def test_fetch_clip_refuses_before_running_anything_when_a_tool_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(links, "missing_tools", lambda *a: ["yt-dlp"])
    called = []
    monkeypatch.setattr(links, "_run", lambda cmd: called.append(cmd))
    with pytest.raises(links.LinkError, match="yt-dlp"):
        links.fetch_clip("https://x.com/a/status/1", 0, 5, tmp_path)
    assert called == []


def test_fetch_clip_runs_both_tools_and_stores_a_named_checksum_copy(tmp_path, monkeypatch):
    def fake_run(cmd):
        if cmd[0] == "yt-dlp":
            out = Path(cmd[cmd.index("-o") + 1].replace("%(ext)s", "mp4"))
            out.write_bytes(b"source")
        else:
            Path(cmd[-1]).write_bytes(b"clip-bytes")

    monkeypatch.setattr(links, "_run", fake_run)
    clip = links.fetch_clip("https://x.com/a/status/1", 0, 5, tmp_path / "store", "yt-dlp", "ffmpeg")
    assert clip["name"].startswith("openfield_") and clip["name"].endswith(".mp4")
    assert (tmp_path / "store" / clip["name"]).read_bytes() == b"clip-bytes"
    assert clip["rights_notice"] == links.RIGHTS_NOTICE
    assert list((tmp_path / "store").iterdir()) == [tmp_path / "store" / clip["name"]]


def test_send_video_posts_the_clip_to_comfys_input_folder(tmp_path, monkeypatch):
    from openfield.forge import uploads

    clip = tmp_path / "openfield_abc.mp4"
    clip.write_bytes(b"mp4")
    seen = {}

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"name": "openfield_abc.mp4"}

    def fake_post(url, files, data, timeout):
        seen.update(url=url, data=data, mime=files["image"][2], name=files["image"][0])
        return Resp()

    monkeypatch.setattr(uploads.httpx, "post", fake_post)
    assert uploads.send_video_to_comfy("http://127.0.0.1:8188/", clip, "openfield_abc.mp4") == "openfield_abc.mp4"
    assert seen == {"url": "http://127.0.0.1:8188/upload/image", "data": {"type": "input", "overwrite": "true"},
                    "mime": "video/mp4", "name": "openfield_abc.mp4"}


def test_tool_failure_is_reported_as_a_safe_message(monkeypatch):
    def boom(cmd):
        raise links.LinkError("the tool could not process this link: ERROR: private video")
    monkeypatch.setattr(links, "_run", boom)
    with pytest.raises(links.LinkError, match="private video"):
        links.fetch_clip("https://x.com/a/status/1", 0, 5, Path("."), "yt-dlp", "ffmpeg")


def test_run_maps_subprocess_failure_without_exposing_the_command(monkeypatch):
    def fail(*a, **k):
        raise subprocess.CalledProcessError(1, ["yt-dlp", "secret-arg"], stderr=b"ERROR: something\n")
    monkeypatch.setattr(links.subprocess, "run", fail)
    with pytest.raises(links.LinkError) as e:
        links._run(["yt-dlp", "secret-arg"])
    assert "ERROR: something" in str(e.value) and "secret-arg" not in str(e.value)
