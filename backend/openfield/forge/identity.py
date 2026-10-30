"""Identity check for a character: how close is an output's face to the character's anchor face.

The score comes from ArcFace (insightface) run under the ComfyUI install's own Python, because that is where
insightface lives. THRESHOLD is provisional: it was not calibrated on a labelled set. Treat "passed" as a
screening flag for a human to look at, never as proof of identity.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).with_name("face_score.py")
THRESHOLD = 0.35          # provisional, uncalibrated (see docs/AI_INFLUENCER_HYBRID.md)
TIMEOUT_S = 180.0
_SIM = re.compile(r"^similarity (-?[0-9.]+)\r?$", re.MULTILINE)  # Windows prints CRLF


class IdentityError(Exception):
    """The face check could not run. The message is safe to show."""


def python_for(comfy_root: str | None) -> Path:
    if not comfy_root:
        raise IdentityError("set the ComfyUI folder first: the face check uses the Python that ships with it")
    py = Path(comfy_root) / "python_embeded" / "python.exe"
    if not py.is_file():
        raise IdentityError("the ComfyUI folder has no python_embeded\\python.exe, so the face check cannot run")
    return py


def score(comfy_root: str | None, reference: Path, candidate: Path) -> dict:
    py = python_for(comfy_root)
    models = Path(comfy_root) / "ComfyUI" / "models" / "insightface"
    if not models.is_dir():
        raise IdentityError("the ComfyUI folder has no models\\insightface folder")
    try:
        out = subprocess.run([str(py), "-s", str(SCRIPT), str(models), str(reference), str(candidate)],
                             capture_output=True, text=True, timeout=TIMEOUT_S, check=False)
    except subprocess.TimeoutExpired as e:
        raise IdentityError("the face check took too long") from e
    text = out.stdout or ""
    m = _SIM.search(text)
    if m:
        value = float(m.group(1))
        return {"score": round(value, 4), "threshold": THRESHOLD, "passed": value >= THRESHOLD, "face_found": True}
    if "noface reference" in text:
        raise IdentityError("no face was found in the character's anchor image")
    if "noface candidate" in text:
        return {"score": None, "threshold": THRESHOLD, "passed": False, "face_found": False}
    raise IdentityError("the face check failed to run")
