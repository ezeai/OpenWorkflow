"""Paste a TikTok, Instagram or X link: download the video with yt-dlp, then trim and re-encode it with ffmpeg.

Safety rules, all enforced here before any external program runs:
- Only https links to the allowlisted hosts are accepted. A link that starts with '-' is refused.
- The link is passed to yt-dlp after '--', so it can never be read as an option.
- Downloads go to a fresh temporary folder, are capped in size, and are removed afterwards.
- Cookies are never passed unless the operator sets them in configuration.

If yt-dlp or ffmpeg is not installed, the feature reports which tool is missing and does nothing else.
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

ALLOWED_HOSTS = {"tiktok.com", "instagram.com", "x.com", "twitter.com"}
MAX_CLIP_SECONDS = 15
MAX_DOWNLOAD_BYTES = 500 * 1024 * 1024
TIMEOUT_S = 300
RIGHTS_NOTICE = ("Only use videos you have the right to use. Downloading a video may breach the platform's terms "
                 "or the creator's rights.")


class LinkError(ValueError):
    """The link or the trim is not acceptable. The message is safe to show."""


@dataclass(frozen=True)
class Trim:
    start: float
    end: float


def validate_url(url: str) -> str:
    url = (url or "").strip()
    if not url or url.startswith("-"):
        raise LinkError("paste a link that starts with https://")
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise LinkError("only https links are accepted")
    host = parts.hostname.lower()
    if not any(host == h or host.endswith("." + h) for h in ALLOWED_HOSTS):
        raise LinkError("only TikTok, Instagram and X (Twitter) links are accepted")
    return url


def validate_trim(start: float, end: float) -> Trim:
    if start < 0 or end <= start:
        raise LinkError("the end must come after the start")
    if end - start > MAX_CLIP_SECONDS:
        raise LinkError(f"a clip can be at most {MAX_CLIP_SECONDS} seconds")
    return Trim(float(start), float(end))


def missing_tools(ytdlp: str | None = None, ffmpeg: str | None = None) -> list[str]:
    missing = []
    if not (ytdlp or shutil.which("yt-dlp")):
        missing.append("yt-dlp")
    if not (ffmpeg or shutil.which("ffmpeg")):
        missing.append("ffmpeg")
    return missing


def ytdlp_args(url: str, out_dir: Path) -> list[str]:
    """yt-dlp arguments. The URL comes last, after '--', so it cannot be read as an option."""
    return ["--no-playlist", "--no-progress", "--no-warnings", "--restrict-filenames",
            "-f", "b[ext=mp4]/b", "--max-filesize", str(MAX_DOWNLOAD_BYTES),
            "-o", str(out_dir / "source.%(ext)s"), "--", url]


def ffmpeg_args(src: Path, dst: Path, trim: Trim) -> list[str]:
    """Trim and re-encode to H.264 MP4 with AAC audio, so every stored clip has the same format."""
    return ["-y", "-v", "error", "-ss", f"{trim.start:.3f}", "-i", str(src), "-t", f"{trim.end - trim.start:.3f}",
            "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-movflags", "+faststart", str(dst)]


def _run(cmd: list[str]) -> None:
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise LinkError("the download or conversion took too long")
    except subprocess.CalledProcessError as e:
        detail = (e.stderr or b"").decode("utf-8", "replace").strip().splitlines()[-1:] or ["unknown error"]
        raise LinkError(f"the tool could not process this link: {detail[0][:200]}")


def fetch_clip(url: str, start: float, end: float, store: Path, ytdlp: str | None = None,
               ffmpeg: str | None = None) -> dict:
    """Download, trim and store one clip. Returns its stored name and checksum. Temporary files are always removed."""
    url = validate_url(url)
    trim = validate_trim(start, end)
    gone = missing_tools(ytdlp, ffmpeg)
    if gone:
        raise LinkError(f"{' and '.join(gone)} is not installed, so links cannot be used yet")
    store.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="openfield-link-") as tmp:
        tmp_dir = Path(tmp)
        _run([ytdlp or "yt-dlp", *ytdlp_args(url, tmp_dir)])
        sources = [p for p in tmp_dir.glob("source.*") if p.suffix != ".part"]
        if not sources:
            raise LinkError("no video was found at that link")
        out = tmp_dir / "clip.mp4"
        _run([ffmpeg or "ffmpeg", *ffmpeg_args(sources[0], out, trim)])
        data = out.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    name = f"openfield_{digest[:16]}.mp4"
    target = store / name
    if not target.exists():
        target.write_bytes(data)
    return {"name": name, "sha256": digest, "bytes": len(data), "seconds": trim.end - trim.start,
            "rights_notice": RIGHTS_NOTICE}
