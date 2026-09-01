"""afhub -- Hub API client for af-hub carry-patch workspaces."""

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

__all__ = [
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
