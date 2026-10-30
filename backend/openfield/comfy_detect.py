"""Find an existing ComfyUI installation. Read-only; runs only when the user asks.

A root is valid when it contains ComfyUI/nodes.py and ComfyUI/main.py. The
portable Windows layout is <root>/ComfyUI/..., which this supports.
Nothing is written here, and ComfyUI's own configuration is never changed.
"""
from __future__ import annotations

import os
import time
from pathlib import Path


def is_comfy_root(p: Path) -> bool:
    src = p / "ComfyUI"
    return (src / "nodes.py").is_file() and (src / "main.py").is_file()


def resolve_comfy_root(configured: str) -> Path | None:
    if not configured:
        return None
    p = Path(configured)
    return p if is_comfy_root(p) else None


def _candidate_bases() -> list[Path]:
    # drive roots first: portable installs usually sit directly on a drive
    bases = [Path(d) for d in ("C:\\", "D:\\", "E:\\") if Path(d).exists()]
    home = Path.home()
    bases += [home, home / "Documents", home / "Desktop", home / "Downloads"]
    return bases


def detect(budget_seconds: float = 8.0) -> list[dict]:
    """Search common locations. Descends only into folders whose names contain 'comfy', within a time budget."""
    found: list[Path] = []
    start = time.monotonic()
    env = os.environ.get("OPENFIELD_COMFY_ROOT")
    if env and is_comfy_root(Path(env)):
        found.append(Path(env))
    for base in _candidate_bases():
        for level1 in _children(base):
            if time.monotonic() - start > budget_seconds:
                break
            if "comfy" not in level1.name.lower():
                continue
            if is_comfy_root(level1):
                found.append(level1)
            for level2 in _children(level1):
                if "comfy" in level2.name.lower() and is_comfy_root(level2):
                    found.append(level2)
    seen: set[str] = set()
    out = []
    for p in found:
        key = os.path.normcase(str(p.resolve()))
        if key in seen:
            continue
        seen.add(key)
        out.append({"path": str(p), "models_dir": str(p / "ComfyUI" / "models")})
    return out


def _children(p: Path) -> list[Path]:
    try:
        return [c for c in p.iterdir() if c.is_dir() and not c.name.startswith(".")]
    except OSError:
        return []
