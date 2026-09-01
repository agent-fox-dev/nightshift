"""Tests for the afhub error hierarchy.

Covers: TS-01-37 through TS-01-45 (spec 01, group 6).
Requirements: 01-REQ-6 (01-REQ-6.1 through 01-REQ-6.9, edge cases E1-E3).
Correctness property: 01-PROP-6.

These tests are written against the stub implementation and will FAIL until
groups 9 and 13-14 provide the real implementation.  Tests that exercise
HubClient error-raising behavior fail because HubClient.__init__ raises
NotImplementedError in the stub.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

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

# ---------------------------------------------------------------------------
# TS-01-37: HubError base class carries status_code, message, and error_type
# ---------------------------------------------------------------------------


class TestHubErrorBase:
    """TS-01-37 — HubError base class carries status_code, message, and
    error_type attributes.

    Requirements: 01-REQ-6.1
    """

    def test_hub_error_exposes_status_code(self) -> None:
        """HubError instance exposes .status_code attribute."""
        err = HubError(status_code=500, message="internal error", error_type="server_error")
        assert err.status_code == 500

    def test_hub_error_exposes_message(self) -> None:
        """HubError instance exposes .message attribute."""
        err = HubError(status_code=500, message="internal error", error_type="server_error")
        assert err.message == "internal error"

    def test_hub_error_exposes_error_type(self) -> None:
        """HubError instance exposes .error_type attribute."""
        err = HubError(status_code=500, message="internal error", error_type="server_error")
        assert err.error_type == "server_error"

    def test_hub_error_is_exception(self) -> None:
        """HubError is a subclass of Exception."""
        err = HubError(status_code=500, message="internal error", error_type="server_error")
        assert isinstance(err, Exception)

    def test_hub_error_str_contains_message(self) -> None:
        """HubError string representation includes the message."""
        err = HubError(status_code=500, message="internal error", error_type="server_error")
        assert "internal error" in str(err)


# ---------------------------------------------------------------------------
# TS-01-38: HubClient raises HubAuthError when the hub returns HTTP 401
# ---------------------------------------------------------------------------


class TestHubAuthErrorRaised:
    """TS-01-38 — HubClient raises HubAuthError when the hub returns HTTP 401.

    Requirements: 01-REQ-6.2
    """

    async def test_401_raises_hub_auth_error(self) -> None:
        """HubClient raises HubAuthError with status_code=401 on HTTP 401."""
        from afhub.client import HubClient

        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(
            status_code=401,
            json=lambda: {"error": {"code": 401, "message": "unauthorized", "error_type": "auth_error"}},
            is_success=False,
        )
        client._http_client.get = AsyncMock(return_value=mock_response)
        with pytest.raises(HubAuthError) as exc_info:
            await client.get_workspace("ws1")
        assert exc_info.value.status_code == 401

    async def test_401_hub_auth_error_carries_message(self) -> None:
        """HubAuthError raised on 401 carries the message from the error envelope."""
        from afhub.client import HubClient

        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(
            status_code=401,
            json=lambda: {"error": {"code": 401, "message": "unauthorized", "error_type": "auth_error"}},
            is_success=False,
        )
        client._http_client.get = AsyncMock(return_value=mock_response)
        with pytest.raises(HubAuthError) as exc_info:
            await client.get_workspace("ws1")
        assert exc_info.value.message == "unauthorized"

    async def test_401_hub_auth_error_carries_error_type(self) -> None:
        """HubAuthError raised on 401 carries the error_type from the error envelope."""
        from afhub.client import HubClient

        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(
            status_code=401,
            json=lambda: {"error": {"code": 401, "message": "unauthorized", "error_type": "auth_error"}},
            is_success=False,
        )
        client._http_client.get = AsyncMock(return_value=mock_response)
        with pytest.raises(HubAuthError) as exc_info:
            await client.get_workspace("ws1")
        assert exc_info.value.error_type == "auth_error"


# ---------------------------------------------------------------------------
# TS-01-39: HubClient raises HubForbiddenError when the hub returns HTTP 403
# ---------------------------------------------------------------------------


class TestHubForbiddenErrorRaised:
    """TS-01-39 — HubClient raises HubForbiddenError when the hub returns HTTP 403.

    Requirements: 01-REQ-6.3
    """

    async def test_403_raises_hub_forbidden_error(self) -> None:
        """HubClient raises HubForbiddenError with status_code=403 on HTTP 403."""
        from afhub.client import HubClient

        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(
            status_code=403,
            json=lambda: {"error": {"code": 403, "message": "forbidden", "error_type": "forbidden"}},
            is_success=False,
        )
        client._http_client.get = AsyncMock(return_value=mock_response)
        with pytest.raises(HubForbiddenError) as exc_info:
            await client.get_workspace("ws1")
        assert exc_info.value.status_code == 403

    async def test_403_hub_forbidden_error_carries_message(self) -> None:
        """HubForbiddenError raised on 403 carries the message from the error envelope."""
        from afhub.client import HubClient

        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(
            status_code=403,
            json=lambda: {"error": {"code": 403, "message": "forbidden", "error_type": "forbidden"}},
            is_success=False,
        )
        client._http_client.get = AsyncMock(return_value=mock_response)
        with pytest.raises(HubForbiddenError) as exc_info:
            await client.get_workspace("ws1")
        assert exc_info.value.message == "forbidden"


# ---------------------------------------------------------------------------
# TS-01-40: HubClient raises HubNotFoundError when the hub returns HTTP 404
# ---------------------------------------------------------------------------


class TestHubNotFoundErrorRaised:
    """TS-01-40 — HubClient raises HubNotFoundError when the hub returns HTTP 404.

    Requirements: 01-REQ-6.4
    """

    async def test_404_raises_hub_not_found_error(self) -> None:
        """HubClient raises HubNotFoundError with status_code=404 on HTTP 404."""
        from afhub.client import HubClient

        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(
            status_code=404,
            json=lambda: {"error": {"code": 404, "message": "not found", "error_type": "not_found"}},
            is_success=False,
        )
        client._http_client.get = AsyncMock(return_value=mock_response)
        with pytest.raises(HubNotFoundError) as exc_info:
            await client.get_workspace("ws1")
        assert exc_info.value.status_code == 404

    async def test_404_hub_not_found_error_carries_message(self) -> None:
        """HubNotFoundError raised on 404 carries the message from the error envelope."""
        from afhub.client import HubClient

        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(
            status_code=404,
            json=lambda: {"error": {"code": 404, "message": "not found", "error_type": "not_found"}},
            is_success=False,
        )
        client._http_client.get = AsyncMock(return_value=mock_response)
        with pytest.raises(HubNotFoundError) as exc_info:
            await client.get_workspace("ws1")
        assert exc_info.value.message == "not found"


# ---------------------------------------------------------------------------
# TS-01-41: HubClient raises HubConflictError with error_type stored on the
#           exception when the hub returns HTTP 409
# ---------------------------------------------------------------------------


class TestHubConflictErrorRaised:
    """TS-01-41 — HubClient raises HubConflictError with error_type stored on
    the exception when the hub returns HTTP 409.

    Requirements: 01-REQ-6.5
    """

    async def test_409_raises_hub_conflict_error(self) -> None:
        """HubClient raises HubConflictError with status_code=409 on HTTP 409."""
        from afhub.client import HubClient

        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(
            status_code=409,
            json=lambda: {"error": {"code": 409, "message": "conflict", "error_type": "duplicate_patch"}},
            is_success=False,
        )
        client._http_client.post = AsyncMock(return_value=mock_response)
        with pytest.raises(HubConflictError) as exc_info:
            await client.add_patch("ws1", "feat/x")
        assert exc_info.value.status_code == 409

    async def test_409_hub_conflict_error_stores_error_type(self) -> None:
        """HubConflictError.error_type is set to the value from the envelope."""
        from afhub.client import HubClient

        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(
            status_code=409,
            json=lambda: {"error": {"code": 409, "message": "conflict", "error_type": "duplicate_patch"}},
            is_success=False,
        )
        client._http_client.post = AsyncMock(return_value=mock_response)
        with pytest.raises(HubConflictError) as exc_info:
            await client.add_patch("ws1", "feat/x")
        assert exc_info.value.error_type == "duplicate_patch"

    async def test_409_hub_conflict_error_type_accessible_to_callers(self) -> None:
        """Callers can inspect .error_type on the caught HubConflictError."""
        from afhub.client import HubClient

        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(
            status_code=409,
            json=lambda: {"error": {"code": 409, "message": "conflict", "error_type": "duplicate_patch"}},
            is_success=False,
        )
        client._http_client.post = AsyncMock(return_value=mock_response)
        caught = False
        try:
            await client.add_patch("ws1", "feat/x")
        except HubConflictError as e:
            caught = True
            assert hasattr(e, "error_type")
            assert e.error_type == "duplicate_patch"
        assert caught, "HubConflictError was not raised"


# ---------------------------------------------------------------------------
# TS-01-42: HubClient raises HubModeError when the hub returns HTTP 400
#           with error_type 'workspace_mode_mismatch'
# ---------------------------------------------------------------------------


class TestHubModeErrorRaised:
    """TS-01-42 — HubClient raises HubModeError when the hub returns HTTP 400
    with error_type 'workspace_mode_mismatch'.

    Requirements: 01-REQ-6.6
    """

    async def test_400_workspace_mode_mismatch_raises_hub_mode_error(self) -> None:
        """HubClient raises HubModeError on 400 with error_type='workspace_mode_mismatch'."""
        from afhub.client import HubClient

        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(
            status_code=400,
            json=lambda: {"error": {"code": 400, "message": "mode mismatch", "error_type": "workspace_mode_mismatch"}},
            is_success=False,
        )
        client._http_client.post = AsyncMock(return_value=mock_response)
        with pytest.raises(HubModeError) as exc_info:
            await client.add_patch("ws1", "feat/x")
        assert exc_info.value.status_code == 400

    async def test_400_workspace_mode_mismatch_error_type(self) -> None:
        """HubModeError carries error_type='workspace_mode_mismatch'."""
        from afhub.client import HubClient

        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(
            status_code=400,
            json=lambda: {"error": {"code": 400, "message": "mode mismatch", "error_type": "workspace_mode_mismatch"}},
            is_success=False,
        )
        client._http_client.post = AsyncMock(return_value=mock_response)
        with pytest.raises(HubModeError) as exc_info:
            await client.add_patch("ws1", "feat/x")
        assert exc_info.value.error_type == "workspace_mode_mismatch"


# ---------------------------------------------------------------------------
# TS-01-43: HubClient raises HubNoActivePatchesError when the hub returns
#           HTTP 400 with error_type 'no_active_patches'
# ---------------------------------------------------------------------------


class TestHubNoActivePatchesErrorRaised:
    """TS-01-43 — HubClient raises HubNoActivePatchesError when the hub returns
    HTTP 400 with error_type 'no_active_patches'.

    Requirements: 01-REQ-6.7
    """

    async def test_400_no_active_patches_raises_hub_no_active_patches_error(self) -> None:
        """HubClient raises HubNoActivePatchesError on 400 with error_type='no_active_patches'."""
        from afhub.client import HubClient

        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(
            status_code=400,
            json=lambda: {"error": {"code": 400, "message": "no active patches", "error_type": "no_active_patches"}},
            is_success=False,
        )
        client._http_client.post = AsyncMock(return_value=mock_response)
        with pytest.raises(HubNoActivePatchesError) as exc_info:
            await client.submit_rebuild("ws1")
        assert exc_info.value.status_code == 400

    async def test_400_no_active_patches_error_type(self) -> None:
        """HubNoActivePatchesError carries error_type='no_active_patches'."""
        from afhub.client import HubClient

        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(
            status_code=400,
            json=lambda: {"error": {"code": 400, "message": "no active patches", "error_type": "no_active_patches"}},
            is_success=False,
        )
        client._http_client.post = AsyncMock(return_value=mock_response)
        with pytest.raises(HubNoActivePatchesError) as exc_info:
            await client.submit_rebuild("ws1")
        assert exc_info.value.error_type == "no_active_patches"


# ---------------------------------------------------------------------------
# TS-01-44: HubClient raises base HubError when the hub returns HTTP 400
#           with an unrecognized error_type
# ---------------------------------------------------------------------------


class TestHubErrorUnrecognized400:
    """TS-01-44 — HubClient raises base HubError (not a subclass) when the hub
    returns HTTP 400 with an unrecognized error_type.

    Requirements: 01-REQ-6.8
    """

    async def test_400_unrecognized_error_type_raises_base_hub_error(self) -> None:
        """HubClient raises HubError (exact type, not a subclass) on 400 with unknown error_type."""
        from afhub.client import HubClient

        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(
            status_code=400,
            json=lambda: {"error": {"code": 400, "message": "bad request", "error_type": "some_unknown_error"}},
            is_success=False,
        )
        client._http_client.post = AsyncMock(return_value=mock_response)
        with pytest.raises(HubError) as exc_info:
            await client.add_patch("ws1", "feat/x")
        assert type(exc_info.value) is HubError

    async def test_400_unrecognized_error_type_carries_status_code(self) -> None:
        """HubError raised for unrecognized 400 carries status_code=400."""
        from afhub.client import HubClient

        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(
            status_code=400,
            json=lambda: {"error": {"code": 400, "message": "bad request", "error_type": "some_unknown_error"}},
            is_success=False,
        )
        client._http_client.post = AsyncMock(return_value=mock_response)
        with pytest.raises(HubError) as exc_info:
            await client.add_patch("ws1", "feat/x")
        assert exc_info.value.status_code == 400

    async def test_400_unrecognized_error_type_carries_error_type(self) -> None:
        """HubError raised for unrecognized 400 carries the unrecognized error_type value."""
        from afhub.client import HubClient

        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(
            status_code=400,
            json=lambda: {"error": {"code": 400, "message": "bad request", "error_type": "some_unknown_error"}},
            is_success=False,
        )
        client._http_client.post = AsyncMock(return_value=mock_response)
        with pytest.raises(HubError) as exc_info:
            await client.add_patch("ws1", "feat/x")
        assert exc_info.value.error_type == "some_unknown_error"


# ---------------------------------------------------------------------------
# TS-01-45: All error classes are importable from the afhub top-level namespace
# ---------------------------------------------------------------------------


class TestErrorExports:
    """TS-01-45 — All error classes are importable directly from the afhub
    top-level namespace.

    Requirements: 01-REQ-6.9
    """

    def test_all_error_classes_importable_from_afhub(self) -> None:
        """All listed error classes can be imported from afhub without ImportError."""
        from afhub import (
            HubAuthError,
            HubConflictError,
            HubConnectionError,
            HubError,
            HubForbiddenError,
            HubModeError,
            HubNoActivePatchesError,
            HubNotFoundError,
        )

        assert all(
            cls is not None
            for cls in [
                HubError,
                HubAuthError,
                HubForbiddenError,
                HubNotFoundError,
                HubConflictError,
                HubConnectionError,
                HubModeError,
                HubNoActivePatchesError,
            ]
        )

    def test_top_level_hub_error_is_errors_hub_error(self) -> None:
        """afhub.HubError is the same class as afhub.errors.HubError."""
        from afhub import HubError as TopLevel
        from afhub.errors import HubError as Module

        assert TopLevel is Module

    def test_top_level_hub_auth_error_is_errors_hub_auth_error(self) -> None:
        """afhub.HubAuthError is the same class as afhub.errors.HubAuthError."""
        from afhub import HubAuthError as TopLevel
        from afhub.errors import HubAuthError as Module

        assert TopLevel is Module

    def test_top_level_hub_connection_error_is_errors_hub_connection_error(self) -> None:
        """afhub.HubConnectionError is the same class as afhub.errors.HubConnectionError."""
        from afhub import HubConnectionError as TopLevel
        from afhub.errors import HubConnectionError as Module

        assert TopLevel is Module


# ---------------------------------------------------------------------------
# 01-PROP-6 / 01-REQ-6.E3: HubError subclass hierarchy — all subclasses are
#                           catchable via 'except HubError'
# ---------------------------------------------------------------------------


_ALL_SUBCLASS_TYPES = [
    HubAuthError,
    HubForbiddenError,
    HubNotFoundError,
    HubConflictError,
    HubConnectionError,
    HubModeError,
    HubNoActivePatchesError,
]


class TestHubErrorSubclassHierarchy:
    """01-PROP-6 / 01-REQ-6.E3 — All HubError subclasses inherit from HubError,
    so every subclass instance passes isinstance(instance, HubError).

    Requirements: 01-REQ-6.1, 01-REQ-6.E3
    Correctness Property: 01-PROP-6
    """

    @pytest.mark.parametrize("error_cls", _ALL_SUBCLASS_TYPES, ids=lambda c: c.__name__)
    def test_subclass_is_instance_of_hub_error(self, error_cls: type) -> None:
        """isinstance(subclass_instance, HubError) is True for every subclass."""
        instance = error_cls(status_code=999, message="test", error_type="test_type")
        assert isinstance(instance, HubError)

    @pytest.mark.parametrize("error_cls", _ALL_SUBCLASS_TYPES, ids=lambda c: c.__name__)
    def test_subclass_is_catchable_as_hub_error(self, error_cls: type) -> None:
        """Every subclass can be caught via 'except HubError'."""
        caught = False
        try:
            raise error_cls(status_code=999, message="test", error_type="test_type")
        except HubError:
            caught = True
        assert caught

    @pytest.mark.parametrize("error_cls", _ALL_SUBCLASS_TYPES, ids=lambda c: c.__name__)
    def test_subclass_inherits_attributes(self, error_cls: type) -> None:
        """Every subclass instance exposes status_code, message, and error_type."""
        instance = error_cls(status_code=418, message="teapot", error_type="im_a_teapot")
        assert instance.status_code == 418
        assert instance.message == "teapot"
        assert instance.error_type == "im_a_teapot"


# ---------------------------------------------------------------------------
# 01-REQ-6.E1: Non-JSON error response — HubError with raw status code and
#              message set to response.text[:200]
# ---------------------------------------------------------------------------


class TestNonJsonErrorResponse:
    """01-REQ-6.E1 — When the hub returns an error status code but the response
    body is not valid JSON, HubClient raises HubError with the raw status code
    and message set to response.text[:200].

    Requirements: 01-REQ-6.E1
    """

    async def test_non_json_error_raises_hub_error(self) -> None:
        """HubClient raises HubError (not an unhandled JSON parse error) on non-JSON body."""
        from afhub.client import HubClient

        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(
            status_code=502,
            text="<html>Bad Gateway</html>",
            is_success=False,
        )
        mock_response.json = MagicMock(side_effect=ValueError("No JSON"))
        client._http_client.get = AsyncMock(return_value=mock_response)
        with pytest.raises(HubError) as exc_info:
            await client.get_workspace("ws1")
        assert exc_info.value.status_code == 502

    async def test_non_json_error_message_is_truncated_body(self) -> None:
        """HubError.message is set to response.text[:200] when JSON parsing fails."""
        from afhub.client import HubClient

        client = HubClient("https://hub.example.com", "pat")
        long_body = "x" * 300
        mock_response = MagicMock(
            status_code=500,
            text=long_body,
            is_success=False,
        )
        mock_response.json = MagicMock(side_effect=ValueError("No JSON"))
        client._http_client.get = AsyncMock(return_value=mock_response)
        with pytest.raises(HubError) as exc_info:
            await client.get_workspace("ws1")
        assert len(exc_info.value.message) <= 200
        assert exc_info.value.message == long_body[:200]

    async def test_non_json_error_empty_body(self) -> None:
        """HubError.message is empty string when body is unreadable/empty."""
        from afhub.client import HubClient

        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(
            status_code=500,
            text="",
            is_success=False,
        )
        mock_response.json = MagicMock(side_effect=ValueError("No JSON"))
        client._http_client.get = AsyncMock(return_value=mock_response)
        with pytest.raises(HubError) as exc_info:
            await client.get_workspace("ws1")
        assert exc_info.value.message == ""


# ---------------------------------------------------------------------------
# 01-REQ-6.E2: 5xx error response — HubError with the 5xx status code
# ---------------------------------------------------------------------------


class Test5xxErrorResponse:
    """01-REQ-6.E2 — When the hub returns an unexpected 5xx status code,
    HubClient raises HubError with the 5xx status code and parsed or raw message.

    Requirements: 01-REQ-6.E2
    """

    async def test_500_raises_hub_error(self) -> None:
        """HubClient raises HubError with status_code=500 on HTTP 500."""
        from afhub.client import HubClient

        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(
            status_code=500,
            json=lambda: {"error": {"code": 500, "message": "internal server error", "error_type": "server_error"}},
            is_success=False,
        )
        client._http_client.get = AsyncMock(return_value=mock_response)
        with pytest.raises(HubError) as exc_info:
            await client.get_workspace("ws1")
        assert exc_info.value.status_code == 500

    async def test_503_raises_hub_error(self) -> None:
        """HubClient raises HubError with status_code=503 on HTTP 503."""
        from afhub.client import HubClient

        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(
            status_code=503,
            json=lambda: {
                "error": {"code": 503, "message": "service unavailable", "error_type": "service_unavailable"}
            },
            is_success=False,
        )
        client._http_client.get = AsyncMock(return_value=mock_response)
        with pytest.raises(HubError) as exc_info:
            await client.get_workspace("ws1")
        assert exc_info.value.status_code == 503

    async def test_5xx_hub_error_carries_parsed_message(self) -> None:
        """HubError raised on 5xx carries the parsed message from the error envelope."""
        from afhub.client import HubClient

        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(
            status_code=500,
            json=lambda: {"error": {"code": 500, "message": "internal server error", "error_type": "server_error"}},
            is_success=False,
        )
        client._http_client.get = AsyncMock(return_value=mock_response)
        with pytest.raises(HubError) as exc_info:
            await client.get_workspace("ws1")
        assert exc_info.value.message == "internal server error"


# ---------------------------------------------------------------------------
# HubConnectionError — basic construction and subclass checks
# ---------------------------------------------------------------------------


class TestHubConnectionError:
    """HubConnectionError — raised when all retry attempts are exhausted on
    a network error.

    Requirements: 01-REQ-6.1 (HubConnectionError as a subclass of HubError)
    """

    def test_hub_connection_error_is_hub_error_subclass(self) -> None:
        """HubConnectionError is a subclass of HubError."""
        assert issubclass(HubConnectionError, HubError)

    def test_hub_connection_error_exposes_attributes(self) -> None:
        """HubConnectionError carries status_code, message, and error_type."""
        err = HubConnectionError(status_code=0, message="connection refused", error_type="connection_error")
        assert err.status_code == 0
        assert err.message == "connection refused"
        assert err.error_type == "connection_error"

    def test_hub_connection_error_catchable_as_hub_error(self) -> None:
        """HubConnectionError can be caught via 'except HubError'."""
        with pytest.raises(HubError):
            raise HubConnectionError(status_code=0, message="timeout", error_type="timeout")
