"""Error hierarchy for afhub.

Stub — implementation pending (spec 01, groups 9+).
"""

from __future__ import annotations


class HubError(Exception):
    """Base error raised by HubClient for all hub API failures."""

    status_code: int
    message: str
    error_type: str

    def __init__(self, *, status_code: int, message: str, error_type: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.error_type = error_type


class HubAuthError(HubError):
    """Raised on HTTP 401."""


class HubForbiddenError(HubError):
    """Raised on HTTP 403."""


class HubNotFoundError(HubError):
    """Raised on HTTP 404."""


class HubConflictError(HubError):
    """Raised on HTTP 409."""


class HubModeError(HubError):
    """Raised on HTTP 400 with error_type='workspace_mode_error'."""


class HubNoActivePatchesError(HubError):
    """Raised on HTTP 400 with error_type='no_active_patches'."""


class HubConnectionError(HubError):
    """Raised when all retry attempts are exhausted on a network error."""
