"""afhub -- Hub API client for af-hub carry-patch workspaces."""

from afhub.auth import resolve_hub_pat, resolve_hub_url
from afhub.client import HubClient
from afhub.errors import (
    HubAuthError,
    HubConflictError,
    HubConnectionError,
    HubError,
    HubForbiddenError,
    HubModeError,
    HubNoActivePatchesError,
    HubNotFoundError,
)
from afhub.models import (
    Patch,
    PatchDetail,
    PatchResult,
    PatchStatusDashboard,
    PatchSummary,
    RebuildJob,
    RebuildPreview,
    RebuildPreviewPatchResult,
    RerereEntry,
    SyncResult,
    Workspace,
)
from afhub.polling import poll_clone_ready, poll_rebuild

__all__ = [
    # Client
    "HubClient",
    # Auth helpers
    "resolve_hub_pat",
    "resolve_hub_url",
    # Polling helpers
    "poll_clone_ready",
    "poll_rebuild",
    # Error classes
    "HubAuthError",
    "HubConflictError",
    "HubConnectionError",
    "HubError",
    "HubForbiddenError",
    "HubModeError",
    "HubNoActivePatchesError",
    "HubNotFoundError",
    # Model classes
    "Patch",
    "PatchDetail",
    "PatchResult",
    "PatchStatusDashboard",
    "PatchSummary",
    "RebuildJob",
    "RebuildPreview",
    "RebuildPreviewPatchResult",
    "RerereEntry",
    "SyncResult",
    "Workspace",
]
