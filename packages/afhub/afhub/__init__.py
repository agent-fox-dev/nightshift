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

__all__ = [
    "HubAuthError",
    "HubConflictError",
    "HubConnectionError",
    "HubError",
    "HubForbiddenError",
    "HubModeError",
    "HubNoActivePatchesError",
    "HubNotFoundError",
]
