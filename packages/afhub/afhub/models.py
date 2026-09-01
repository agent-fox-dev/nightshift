"""Pydantic data models for afhub.

Stub — implementation pending (spec 01, group 10).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Workspace(BaseModel):
    """Hub workspace resource."""

    model_config = ConfigDict(extra="ignore")

    slug: str = ""
    git_url: str = ""
    workspace_mode: str = ""
    status: str = ""
    clone_status: str = ""
    sync_status: str = ""


class SyncResult(BaseModel):
    """Result returned by POST /workspaces/:slug/sync."""

    model_config = ConfigDict(extra="ignore")

    patches_merged: list[str] = []
    rebuild_triggered: bool = False
    rebuild_job_id: str | None = None
    force_push_detected: bool = False


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

    patches: list[object] = []
    summary: PatchSummary = PatchSummary()


class Patch(BaseModel):
    """A registered patch (branch) in a workspace."""

    model_config = ConfigDict(extra="ignore")

    id: str = ""
    workspace_slug: str = ""
    branch_name: str = ""
    position: int = 0
    status: str = ""
    added_at: str = ""
    updated_at: str = ""
    description: str | None = None
    upstream_pr_url: str | None = None
    skipped_reason: str | None = None
    new_head_sha: str | None = None
    conflict_files: list[str] = []


class PatchResult(BaseModel):
    """Per-patch result within a RebuildJob."""

    model_config = ConfigDict(extra="ignore")

    patch_id: str = ""
    branch_name: str = ""
    position: int = 0
    status: str = ""
    skipped_reason: str | None = None
    new_head_sha: str | None = None
    conflict_files: list[str] = []


class RebuildJob(BaseModel):
    """An asynchronous rebuild job."""

    model_config = ConfigDict(extra="ignore")

    id: str = ""
    status: str = ""
    created_at: str = ""
    completed_at: str | None = None
    strategy: str | None = None
    patch_results: list[PatchResult] = []


class RebuildPreviewPatchResult(BaseModel):
    """Per-patch result within a RebuildPreview."""

    model_config = ConfigDict(extra="ignore")

    patch_id: str = ""
    branch_name: str = ""
    position: int = 0
    status: str = ""
    tree_sha: str | None = None
    conflict_files: list[str] | None = None


class RebuildPreview(BaseModel):
    """Preview of what a rebuild would produce without executing it."""

    model_config = ConfigDict(extra="ignore")

    patch_results: list[RebuildPreviewPatchResult] = []


class RerereEntry(BaseModel):
    """A recorded rerere resolution."""

    model_config = ConfigDict(extra="ignore")

    path: str = ""
    recorded_at: str = ""


class PatchDetail(BaseModel):
    """Per-patch detail row within a PatchStatusDashboard."""

    model_config = ConfigDict(extra="ignore")

    id: str = ""
    branch_name: str = ""
    position: int = 0
    status: str = ""
