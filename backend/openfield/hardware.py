"""Real hardware/toolchain discovery. Never guesses: unknown stays None."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import psutil


def _run(cmd: list[str], timeout: float = 5.0) -> str | None:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip() if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def gpus() -> list[dict]:
    out = _run(["nvidia-smi", "--query-gpu=name,memory.total,memory.used,driver_version", "--format=csv,noheader,nounits"])
    result = []
    for line in (out or "").splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == 4:
            try:
                result.append({"name": parts[0], "vram_total_mb": int(parts[1]), "vram_used_mb": int(parts[2]),
                               "vram_free_mb": int(parts[1]) - int(parts[2]), "driver": parts[3]})
            except ValueError:
                continue
    return result


def disk_free_gb(path: str | Path) -> float | None:
    try:
        p = Path(path)
        while not p.exists() and p != p.parent:
            p = p.parent
        return round(shutil.disk_usage(p).free / 1024**3, 1)
    except OSError:
        return None


def find_ffmpeg(configured: str = "") -> dict:
    exe = configured if configured and Path(configured).is_file() else shutil.which("ffmpeg")
    if not exe:
        return {"available": False, "path": None, "version": None}
    ver = (_run([exe, "-version"]) or "").splitlines()
    return {"available": True, "path": exe, "version": ver[0] if ver else None}


def snapshot(paths: list[str] | None = None, ffmpeg_path: str = "") -> dict:
    vm = psutil.virtual_memory()
    return {
        "os": sys.platform,
        "python": sys.version.split()[0],
        "cpu_threads": psutil.cpu_count(logical=True),
        "ram_total_gb": round(vm.total / 1024**3, 1),
        "ram_available_gb": round(vm.available / 1024**3, 1),
        "gpus": gpus(),
        "disk_free_gb": {str(p): disk_free_gb(p) for p in (paths or [])},
        "ffmpeg": find_ffmpeg(ffmpeg_path),
    }
