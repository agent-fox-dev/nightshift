"""Pydantic data models for afhub.

Implements 01-REQ-5: typed response models for all hub API endpoints.
All models use ``ConfigDict(extra='ignore')`` so that unknown fields in
hub responses are silently discarded, enabling forward compatibility.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Workspace
# ---------------------------------------------------------------------------


class Workspace(BaseModel):
    """Hub workspace resource."""

    model_config = ConfigDict(extra="ignore")

    # Required fields
    slug: str
    git_url: str
    workspace_mode: str
    status: str
    clone_status: str
    sync_status: str

    # Optional fields — default to None
    clone_error: str | None = None
    sync_error: str | None = None
    sync_mode: str | None = None
    upstream_url: str | None = None
    upstream_head_sha: str | None = None
    head_sha: str | None = None
    integration_branch: str | None = None
    last_sync_at: str | None = None


# ---------------------------------------------------------------------------
# Patch
# ---------------------------------------------------------------------------


class Patch(BaseModel):
    """A registered patch (branch) in a workspace."""

    model_config = ConfigDict(extra="ignore")

    # Required fields
    id: str
    workspace_slug: str
    branch_name: str
    position: int
    status: str
    added_at: str
    updated_at: str

    # Optional fields — default to None
    conflict_files: list[str] | None = None
    upstream_pr_url: str | None = None
    description: str | None = None
    deleted_at: str | None = None


# ---------------------------------------------------------------------------
# PatchResult (per-patch outcome within a RebuildJob)
# ---------------------------------------------------------------------------


class PatchResult(BaseModel):
    """Per-patch result within a RebuildJob."""

    model_config = ConfigDict(extra="ignore")

    patch_id: str
    branch_name: str
    position: int
    status: str
    skipped_reason: str | None = None
    new_head_sha: str | None = None
    conflict_files: list[str] = []


# ---------------------------------------------------------------------------
# RebuildJob
# ---------------------------------------------------------------------------


class RebuildJob(BaseModel):
    """An asynchronous rebuild job."""

    model_config = ConfigDict(extra="ignore")

    # Required fields — id uses strict=True to reject non-string values
    # (01-REQ-5.E4).
    id: str = Field(strict=True)
    status: str
    created_at: str

    # Optional fields — default to None
    strategy: str | None = None
    error: str | None = None
    patch_results: list[PatchResult] | None = None
    integration_head_sha: str | None = None
    previous_integration_head_sha: str | None = None
    completed_at: str | None = None


# ---------------------------------------------------------------------------
# SyncResult
# ---------------------------------------------------------------------------


class SyncResult(BaseModel):
    """Result returned by POST /workspaces/:slug/sync."""

    model_config = ConfigDict(extra="ignore")

    patches_merged: list[str] = []
    rebuild_triggered: bool = False
    rebuild_job_id: str | None = None
    force_push_detected: bool = False


# ---------------------------------------------------------------------------
# RerereEntry
# ---------------------------------------------------------------------------


class RerereEntry(BaseModel):
    """A recorded rerere conflict resolution."""

    model_config = ConfigDict(extra="ignore")

    path: str
    recorded_at: str


# ---------------------------------------------------------------------------
# PatchStatusDashboard (with nested PatchDetail and PatchSummary)
# ---------------------------------------------------------------------------


class PatchDetail(BaseModel):
    """Per-patch detail row within a PatchStatusDashboard."""

    model_config = ConfigDict(extra="ignore")

    # Required fields
    id: str
    branch_name: str
    position: int
    status: str

    # Optional fields — default to None
    last_rebuild_result: str | None = None
    conflict_files: list[str] | None = None
    description: str | None = None


class PatchSummary(BaseModel):
    """Aggregate counts within a PatchStatusDashboard."""

    model_config = ConfigDict(extra="ignore")

    total_patches: int = 0
    active: int = 0
    merged_upstream: int = 0
    conflict: int = 0
    disabled: int = 0
    total_rerere_resolutions: int = 0


class PatchStatusDashboard(BaseModel):
    """Response from GET /workspaces/:slug/patch-status."""

    model_config = ConfigDict(extra="ignore")

    patches: list[PatchDetail] = []
    summary: PatchSummary = PatchSummary()


# ---------------------------------------------------------------------------
# RebuildPreview (with nested RebuildPreviewPatchResult)
# ---------------------------------------------------------------------------


class RebuildPreviewPatchResult(BaseModel):
    """Per-patch result within a RebuildPreview."""

    model_config = ConfigDict(extra="ignore")

    patch_id: str
    branch_name: str
    position: int
    status: str
    tree_sha: str | None = None
    conflict_files: list[str] | None = None


class RebuildPreview(BaseModel):
    """Preview of what a rebuild would produce without executing it."""

    model_config = ConfigDict(extra="ignore")

    patch_results: list[RebuildPreviewPatchResult] = []
