"""Read-only model discovery. Never moves, copies, renames or writes model files.

Staged verification, cheapest first:
  1. stat: existence, size, mtime, symlink target, permissions.
  2. header: safetensors JSON header length/parse, GGUF magic. Reads a few KB.
  3. sha256: only when explicitly requested, cached by (path, size, mtime).

Matching is by exact filename inside the folders a component declares.
Filename similarity is never treated as identity, and two same-size files are
reported as 'same size, not compared' until hashed.
"""
from __future__ import annotations

import hashlib
import json
import os
import struct
from dataclasses import dataclass, field
from pathlib import Path

from .schema import Ownership

MAX_SCAN_DEPTH = 3
MAX_SAFETENSORS_HEADER = 100 * 1024 * 1024


@dataclass(frozen=True)
class Root:
    path: Path
    ownership: Ownership
    label: str


@dataclass
class FileEntry:
    path: str
    name: str
    size: int
    mtime_ns: int
    ownership: Ownership
    root_label: str
    is_symlink: bool
    link_target: str | None
    format: str                 # safetensors | gguf | unknown
    header_ok: bool
    header_error: str | None = None


@dataclass
class Problem:
    kind: str                   # missing_dir | permission | broken_link | stat_error | header_error
    path: str
    detail: str


@dataclass
class ScanResult:
    files: list[FileEntry] = field(default_factory=list)
    problems: list[Problem] = field(default_factory=list)
    roots_checked: list[dict] = field(default_factory=list)

    def by_name(self) -> dict[str, list[FileEntry]]:
        out: dict[str, list[FileEntry]] = {}
        for f in self.files:
            out.setdefault(f.name.lower(), []).append(f)
        return out


def read_header(path: str) -> tuple[str, bool, str | None]:
    """Format and header check. Never reads the weights."""
    try:
        with open(path, "rb") as f:
            magic = f.read(4)
            if magic == b"GGUF":
                return "gguf", True, None
            f.seek(0)
            head = f.read(8)
            if len(head) < 8:
                return "unknown", False, "file too small to be a model"
            (n,) = struct.unpack("<Q", head)
            if n <= 0 or n > MAX_SAFETENSORS_HEADER:
                return "unknown", False, "not a safetensors header"
            body = f.read(n)
            if len(body) != n:
                return "safetensors", False, "truncated header"
            json.loads(body.decode("utf-8"))
            return "safetensors", True, None
    except PermissionError:
        raise
    except (OSError, ValueError, UnicodeDecodeError, struct.error) as e:
        return "unknown", False, f"{type(e).__name__}: {e}"


def scan_roots(roots: list[Root], problems_out: list[Problem] | None = None) -> ScanResult:
    result = ScanResult()
    for root in roots:
        base = Path(root.path)
        info = {"path": str(base), "ownership": root.ownership, "label": root.label, "exists": base.is_dir()}
        result.roots_checked.append(info)
        if not base.is_dir():
            result.problems.append(Problem("missing_dir", str(base), "directory does not exist"))
            continue
        _walk(base, root, result)
    if problems_out is not None:
        problems_out.extend(result.problems)
    return result


def _walk(base: Path, root: Root, result: ScanResult) -> None:
    base_depth = len(base.parts)
    try:
        for dirpath, dirnames, filenames in os.walk(base, followlinks=False, onerror=lambda e: result.problems.append(
                Problem("permission", str(e.filename or base), f"cannot list directory: {e.strerror}"))):
            if len(Path(dirpath).parts) - base_depth >= MAX_SCAN_DEPTH:
                dirnames[:] = []
            for name in filenames:
                _add_file(Path(dirpath) / name, root, result)
    except OSError as e:
        result.problems.append(Problem("permission", str(base), f"cannot scan: {e.strerror or e}"))


def _add_file(p: Path, root: Root, result: ScanResult) -> None:
    is_link = p.is_symlink()
    target = None
    if is_link:
        try:
            target = os.readlink(p)
        except OSError:
            target = None
    try:
        st = p.stat()          # follows symlinks
    except FileNotFoundError:
        result.problems.append(Problem("broken_link", str(p), f"symbolic link target missing ({target})"))
        return
    except PermissionError:
        result.problems.append(Problem("permission", str(p), "access denied"))
        return
    except OSError as e:
        result.problems.append(Problem("stat_error", str(p), f"{type(e).__name__}: {e}"))
        return
    if not os.path.isfile(p):
        return
    fmt, ok, err = ("unknown", False, None)
    if p.suffix.lower() in (".safetensors", ".gguf", ".ckpt", ".pt", ".pth", ".bin"):
        try:
            fmt, ok, err = read_header(str(p))
        except PermissionError:
            result.problems.append(Problem("permission", str(p), "cannot read header"))
            return
        if not ok and fmt != "unknown":
            result.problems.append(Problem("header_error", str(p), err or "header invalid"))
    result.files.append(FileEntry(
        path=str(p), name=p.name, size=st.st_size, mtime_ns=st.st_mtime_ns, ownership=root.ownership,
        root_label=root.label, is_symlink=is_link, link_target=target, format=fmt, header_ok=ok, header_error=err))


# ---------- staged hashing with cache ----------

def sha256_cached(entry: FileEntry, cache: dict, chunk: int = 8 * 1024 * 1024) -> tuple[str, bool]:
    """Return (sha256, from_cache). Cache entry is valid only while size and mtime are unchanged."""
    cached = cache.get(entry.path)
    if isinstance(cached, dict) and cached.get("size") == entry.size and cached.get("mtime_ns") == entry.mtime_ns \
            and isinstance(cached.get("sha256"), str):
        return cached["sha256"], True
    h = hashlib.sha256()
    with open(entry.path, "rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    digest = h.hexdigest()
    cache[entry.path] = {"size": entry.size, "mtime_ns": entry.mtime_ns, "sha256": digest}
    return digest, False


def same_size_groups(files: list[FileEntry]) -> list[list[str]]:
    """Groups of paths that share a size. A size match is NOT evidence of identity."""
    by_size: dict[int, list[str]] = {}
    for f in files:
        by_size.setdefault(f.size, []).append(f.path)
    return [sorted(v) for v in by_size.values() if len(v) > 1]
