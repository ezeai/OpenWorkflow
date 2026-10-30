"""Shared services for one backend process. Passed explicitly; no module-level mutable state."""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .comfy_detect import resolve_comfy_root
from .config import Settings, SettingsStore
from .models.catalog import LoadedCatalog, load_catalog
from .models.discovery import Root, ScanResult, scan_roots
from .models.downloads import DownloadManager
from .models.status import model_status
from .models.store import ModelRegistryStore, RegistryData, now_iso
from .projects import ProjectStore
from .wizard import WizardStore
from .workflows.registry import LoadedWorkflows, core_node_index, load_workflows, evaluate

SCAN_TTL_SECONDS = 60.0


@dataclass
class ScanCache:
    result: ScanResult | None = None
    at: float = 0.0


@dataclass
class Services:
    home: Path
    settings_store: SettingsStore
    registry: ModelRegistryStore
    catalog: LoadedCatalog
    workflows: LoadedWorkflows
    wizard: WizardStore
    downloads: DownloadManager
    scan_cache: ScanCache = field(default_factory=ScanCache)
    completed_downloads: list[str] = field(default_factory=list)
    _lock: threading.RLock = field(default_factory=threading.RLock)

    @property
    def managed_dir(self) -> Path:
        return self.home / "models" / "managed"

    def settings(self) -> Settings:
        return self.settings_store.load()

    def projects(self) -> ProjectStore:
        """Project store at the configured projects folder (created on first use)."""
        s = self.settings()
        return ProjectStore(Path(s.projects_dir) if s.projects_dir else self.home / "projects")

    def roots(self, s: Settings | None = None) -> list[Root]:
        s = s or self.settings()
        roots: list[Root] = []
        seen: set[str] = set()

        def add(p: Path, ownership: str, label: str) -> None:
            key = os.path.normcase(str(p.resolve())) if p.exists() else os.path.normcase(str(p))
            if key in seen:
                return
            seen.add(key)
            roots.append(Root(p, ownership, label))  # type: ignore[arg-type]

        comfy = resolve_comfy_root(s.comfy_root)
        if comfy is not None:
            add(comfy / "ComfyUI" / "models", "external", "ComfyUI models")
        for d in s.model_dirs:
            add(Path(d), "user_shared", f"Shared folder: {Path(d).name or d}")
        self.managed_dir.mkdir(parents=True, exist_ok=True)  # Open Workflow's own folder; never a 'missing' problem
        add(self.managed_dir, "managed", "Open Workflow managed")
        return roots

    def scan(self, force: bool = False) -> ScanResult:
        """Filesystem scan with a short cache. Never hashes; never writes model files."""
        with self._lock:
            fresh = self.scan_cache.result is not None and time.monotonic() - self.scan_cache.at < SCAN_TTL_SECONDS
            if fresh and not force:
                return self.scan_cache.result
            result = scan_roots(self.roots())
            self.scan_cache = ScanCache(result, time.monotonic())
            data = self.registry.load()
            data.last_scan_at = now_iso()
            self.registry.save(data)
            return result

    def invalidate_scan(self) -> None:
        with self._lock:
            self.scan_cache = ScanCache()

    def model_statuses(self) -> tuple[list[dict], RegistryData, ScanResult]:
        scan = self.scan()
        reg = self.registry.load()
        models = [model_status(m, scan, reg) for m in self.catalog.models.values()]
        return models, reg, scan

    def evaluations(self) -> list[dict]:
        models, reg, scan = self.model_statuses()
        by_id = {m["id"]: m for m in models}
        comfy = resolve_comfy_root(self.settings().comfy_root)
        nodes = core_node_index(comfy)
        packs = set()
        if comfy is not None and (comfy / "ComfyUI" / "custom_nodes").is_dir():
            packs = {p.name for p in (comfy / "ComfyUI" / "custom_nodes").iterdir() if p.is_dir()}
        validated = {wid for wid, rec in reg.workflow_validations.items() if isinstance(rec, dict) and rec}
        smoke = {r.get("workflow_id") for r in reg.smoke_tests if isinstance(r, dict)}
        return [evaluate(w, by_id, nodes, packs, validated, smoke) for w in self.workflows.items.values()]
