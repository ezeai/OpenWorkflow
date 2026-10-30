"""Typed contracts for the model registry.

Statuses are kept separate on purpose. A file existing is not the same as
identity being confirmed, a license being verified, or inference having run.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

LicenseStatus = Literal["verified", "restricted", "unverified"]
Role = Literal["diffusion", "text_encoder", "vae", "clip_vision"]
Eligibility = Literal["eligible", "review", "not_eligible"]
Ownership = Literal["external", "user_shared", "managed"]
ComponentState = Literal["missing", "found", "identified"]

MODEL_ID = r"^[a-z0-9][a-z0-9.\-]*$"


class License(BaseModel):
    id: str
    status: LicenseStatus
    evidence_url: str | None = None
    verified_on: str | None = None
    commercial_restriction: str | None = None
    notes: str = ""


class Component(BaseModel):
    role: Role
    folders: list[str] = Field(min_length=1)
    filenames: list[str] = Field(min_length=1)  # exact names only; no fuzzy matching
    license_status: LicenseStatus = "unverified"
    license_note: str = ""
    require_all: bool = False  # True: every listed file must be present (sharded weights)


class DownloadFile(BaseModel):
    relpath: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._\-/]*$")  # no traversal, no absolute paths
    url: str = Field(pattern=r"^https://huggingface\.co/")
    size_bytes: int = Field(gt=0)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    component_role: Role


class DownloadSpec(BaseModel):
    """A verified file manifest. Only publisher-backed entries may carry one (see docs/MODEL_LICENSE_REVIEW.md)."""
    source_repo: str = Field(pattern=r"^https://huggingface\.co/")
    revision: str | None = None
    checksum_authority: Literal["publisher", "none"]
    files: list[DownloadFile] = Field(min_length=1)


class Variant(BaseModel):
    id: str
    name: str
    publisher_files: bool
    components: list[Component] = Field(min_length=1)
    download: DownloadSpec | None = None


class CatalogModel(BaseModel):
    id: str = Field(pattern=MODEL_ID)
    name: str
    family: str
    tasks: list[str] = Field(min_length=1)
    publisher: str
    source_url: str
    license: License
    open_weight_policy: Eligibility
    variants: list[Variant] = Field(min_length=1)
    limitations: list[str] = []
    # Optional-install guidance. Open Workflow never downloads these for you; the guide tells you what to place where.
    obtain_from: str = ""
    approx_size_gb: float | None = None
    install_guide: str = ""
