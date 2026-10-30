"""Environment check for Open Workflow on Windows.

Reports what is ready and what needs attention. It changes nothing: no installs, no downloads,
no writes outside the standard library's own lookups. ComfyUI is probed read-only at the URL
configured in data/config.json (default http://127.0.0.1:8188), and a missing ComfyUI is reported
as "not connected", not as an error in Open Workflow itself.

Run from the repository root:  python scripts/check_environment.py
Exit code 0 when the application can start; 1 when a required part is missing.
"""
from __future__ import annotations

import importlib.metadata as metadata
import json
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MIN_PYTHON = (3, 11)
DEFAULT_COMFY_URL = "http://127.0.0.1:8188"
APP_PORT = 8765

READY = "Ready"
ATTENTION = "Needs attention"
NOT_CONNECTED = "Not connected"
UNKNOWN = "Unknown"


@dataclass
class Check:
    name: str
    state: str
    message: str
    required: bool = False


def check_python(version: tuple[int, ...] = sys.version_info[:3]) -> Check:
    have = ".".join(map(str, version))
    need = ".".join(map(str, MIN_PYTHON))
    if tuple(version[:2]) >= MIN_PYTHON:
        return Check("Python", READY, f"Python {have}", required=True)
    return Check("Python", ATTENTION,
                 f"Python {have} is too old. Install Python {need} or newer from python.org, then run setup again.",
                 required=True)


def requirement_names(requirements_file: Path) -> list[str]:
    names = []
    for line in requirements_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r"([A-Za-z0-9_.\-]+)", line)
        if match:
            names.append(match.group(1))
    return names


def check_dependencies(requirements_file: Path = REPO_ROOT / "backend" / "requirements.txt") -> Check:
    missing = []
    for name in requirement_names(requirements_file):
        try:
            metadata.version(name)
        except metadata.PackageNotFoundError:
            missing.append(name)
    if not missing:
        return Check("Python packages", READY, "All packages in backend/requirements.txt are installed.", required=True)
    return Check("Python packages", ATTENTION,
                 f"Missing: {', '.join(missing)}. Run scripts\\setup_windows.ps1 once, or: pip install -r backend\\requirements.txt",
                 required=True)


def check_frontend(dist: Path = REPO_ROOT / "frontend" / "dist") -> Check:
    if (dist / "index.html").is_file() and (dist / "assets").is_dir():
        return Check("Interface files", READY, "The built interface is present.", required=True)
    return Check("Interface files", ATTENTION,
                 "frontend\\dist is missing. It ships with the repository; restore it from git, or rebuild with Node.js: cd frontend, npm install, npm run build.",
                 required=True)


def configured_comfy_url(config_file: Path = REPO_ROOT / "data" / "config.json") -> str:
    try:
        url = json.loads(config_file.read_text(encoding="utf-8")).get("comfy_url") or DEFAULT_COMFY_URL
    except (OSError, ValueError, AttributeError):
        return DEFAULT_COMFY_URL
    return str(url).rstrip("/")


def probe_comfy(url: str, timeout: float = 3.0) -> Check:
    """Read-only GET of ComfyUI's own system_stats endpoint. Any failure means 'not connected'."""
    try:
        with urllib.request.urlopen(f"{url}/system_stats", timeout=timeout) as resp:
            if resp.status != 200:
                return Check("ComfyUI", UNKNOWN, f"{url} answered with HTTP {resp.status}. Check that the address is ComfyUI.")
            stats = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", e)
        return Check("ComfyUI", NOT_CONNECTED,
                     f"No ComfyUI answered at {url} ({reason}). Start ComfyUI separately, then connect it in Open Workflow.")
    except (OSError, ValueError):
        return Check("ComfyUI", UNKNOWN, f"{url} answered, but not with ComfyUI's status. Check the address.")
    version = (stats.get("system") or {}).get("comfyui_version", "unknown version")
    return Check("ComfyUI", READY, f"Connected at {url} (ComfyUI {version}).")


def check_port(port: int = APP_PORT) -> Check:
    """Is the default port free, or already held by something? Never kills a process."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        if s.connect_ex(("127.0.0.1", port)) != 0:
            return Check("Port", READY, f"Port {port} is free.")
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2) as resp:
            body = json.loads(resp.read().decode("utf-8", "replace"))
        if body.get("app") == "openfield":
            return Check("Port", READY, f"Open Workflow is already running at http://127.0.0.1:{port}. Open it in the browser; no second copy is needed.")
    except (OSError, ValueError):
        pass
    return Check("Port", ATTENTION,
                 f"Port {port} is used by another program. Close it, or the start script will use the next free port.")


def run_checks(comfy_url: str | None = None) -> list[Check]:
    return [
        check_python(),
        check_dependencies(),
        check_frontend(),
        check_port(),
        probe_comfy(comfy_url or configured_comfy_url()),
    ]


def main() -> int:
    checks = run_checks()
    width = max(len(c.name) for c in checks)
    for c in checks:
        print(f"{c.name.ljust(width)}  {c.state:<15}  {c.message}")
    blocking = [c for c in checks if c.required and c.state != READY]
    print()
    if blocking:
        print("Open Workflow cannot start yet. Fix the items marked 'Needs attention' above, then run this check again.")
        return 1
    print("Open Workflow can start. ComfyUI is optional for opening the app, but generation needs it connected.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
