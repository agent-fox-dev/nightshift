"""Error hierarchy for afhub.

Implements 01-REQ-6: typed exception hierarchy for hub API errors.
"""

from __future__ import annotations

from typing import Any


class HubError(Exception):
    """Base error raised by HubClient for all hub API failures.

    Carries *status_code*, *message*, and *error_type* so callers can
    inspect structured error data without parsing HTTP responses.
    """

    status_code: int
    message: str
    error_type: str

    def __init__(self, *, status_code: int, message: str, error_type: str = "") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.error_type = error_type


class HubAuthError(HubError):
    """Raised when the hub returns HTTP 401 Unauthorized."""


class HubForbiddenError(HubError):
    """Raised when the hub returns HTTP 403 Forbidden."""


class HubNotFoundError(HubError):
    """Raised when the hub returns HTTP 404 Not Found."""


class HubConflictError(HubError):
    """Raised when the hub returns HTTP 409 Conflict.

    The *error_type* attribute is stored for caller inspection.
    """


class HubModeError(HubError):
    """Raised when the hub returns HTTP 400 with error_type='workspace_mode_mismatch'."""


class HubNoActivePatchesError(HubError):
    """Raised when the hub returns HTTP 400 with error_type='no_active_patches'."""


class HubConnectionError(HubError):
    """Raised when all retry attempts are exhausted on a network error."""


# -- Status-code to exception-class mapping ------------------------------------

_STATUS_MAP: dict[int, type[HubError]] = {
    401: HubAuthError,
    403: HubForbiddenError,
    404: HubNotFoundError,
    409: HubConflictError,
}

_ERROR_TYPE_400_MAP: dict[str, type[HubError]] = {
    "workspace_mode_mismatch": HubModeError,
    "no_active_patches": HubNoActivePatchesError,
}


def _raise_for_status(response: Any) -> None:
    """Parse the hub error envelope and raise the appropriate exception.

    Called after every HTTP response.  Does nothing when the response
    indicates success.  For error responses:

    1. Attempt to parse the JSON error envelope.
    2. Select the exception class from *status_code* and *error_type*.
    3. If JSON parsing fails, fall back to ``response.text[:200]``
       (01-REQ-6.E1).
    """
    if 200 <= response.status_code < 300:
        return

    status_code: int = response.status_code

    # Try to parse the structured error envelope.
    try:
        body = response.json()
        envelope = body.get("error", {}) if isinstance(body, dict) else {}
        message: str = envelope.get("message", "")
        error_type: str = envelope.get("error_type", "")
    except Exception:
        # Non-JSON or malformed body -- use raw text (01-REQ-6.E1).
        try:
            message = response.text[:200]
        except Exception:
            message = ""
        error_type = ""

    # Dispatch to the correct exception class.
    if status_code == 400:
        exc_cls = _ERROR_TYPE_400_MAP.get(error_type, HubError)
    else:
        exc_cls = _STATUS_MAP.get(status_code, HubError)

    raise exc_cls(status_code=status_code, message=message, error_type=error_type)
