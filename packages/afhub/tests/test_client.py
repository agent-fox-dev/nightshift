"""Tests for HubClient workspace operations and lifecycle.

Covers: TS-01-1 through TS-01-7 (spec 01, group 1).
Requirements: 01-REQ-1.1 through 01-REQ-1.7.

These tests are written against the stub implementation and will FAIL until
groups 13–14 provide the real implementation.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from afhub.client import HubClient
from afhub.models import (
    PatchStatusDashboard,
    PatchSummary,
    SyncResult,
    Workspace,
)

# ---------------------------------------------------------------------------
# TS-01-1: HubClient constructor creates shared httpx.AsyncClient
# ---------------------------------------------------------------------------


class TestHubClientInit:
    """TS-01-1 — HubClient.__init__ creates a single shared httpx.AsyncClient
    configured with base_url and Authorization: Bearer header.

    Requirements: 01-REQ-1.1
    """

    async def test_creates_http_client_with_base_url(self) -> None:
        """Constructor stores the endpoint_url as the httpx.AsyncClient base_url."""
        client = HubClient("https://hub.example.com", "test-pat-123")
        assert client._http_client.base_url == "https://hub.example.com"
        await client.aclose()

    async def test_creates_http_client_with_auth_header(self) -> None:
        """Constructor configures 'Authorization: Bearer <pat>' as a default header."""
        client = HubClient("https://hub.example.com", "test-pat-123")
        assert client._http_client.headers["authorization"] == "Bearer test-pat-123"
        await client.aclose()

    async def test_raises_value_error_for_empty_endpoint_url(self) -> None:
        """ValueError raised (no httpx client created) when endpoint_url is empty."""
        with pytest.raises(ValueError):
            HubClient("", "test-pat-123")

    async def test_raises_value_error_for_empty_pat(self) -> None:
        """ValueError raised (no httpx client created) when pat is empty."""
        with pytest.raises(ValueError):
            HubClient("https://hub.example.com", "")


# ---------------------------------------------------------------------------
# TS-01-2: get_workspace returns a Workspace model
# ---------------------------------------------------------------------------


class TestGetWorkspace:
    """TS-01-2 — get_workspace sends GET /api/v1/workspaces/:slug and returns
    a parsed Workspace instance on HTTP 200.

    Requirements: 01-REQ-1.2
    """

    async def test_get_workspace_returns_workspace_instance(self) -> None:
        """get_workspace returns a Workspace when the hub responds with HTTP 200."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(
            status_code=200,
            json=lambda: {
                "slug": "my-workspace",
                "git_url": "https://git.example.com/repo.git",
                "workspace_mode": "carry",
                "status": "active",
                "clone_status": "ready",
                "sync_status": "ok",
            },
        )
        client._http_client.get = AsyncMock(return_value=mock_response)
        result = await client.get_workspace("my-workspace")
        assert isinstance(result, Workspace)
        assert result.slug == "my-workspace"

    async def test_get_workspace_calls_correct_path(self) -> None:
        """get_workspace issues a GET to /api/v1/workspaces/<slug>."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(
            status_code=200,
            json=lambda: {
                "slug": "my-workspace",
                "git_url": "https://git.example.com/repo.git",
                "workspace_mode": "carry",
                "status": "active",
                "clone_status": "ready",
                "sync_status": "ok",
            },
        )
        client._http_client.get = AsyncMock(return_value=mock_response)
        await client.get_workspace("my-workspace")
        call_args = str(client._http_client.get.call_args)
        assert "/api/v1/workspaces/my-workspace" in call_args


# ---------------------------------------------------------------------------
# TS-01-3: sync_workspace sends POST with correct body and returns SyncResult
# ---------------------------------------------------------------------------


class TestSyncWorkspace:
    """TS-01-3 — sync_workspace sends POST /api/v1/workspaces/:slug/sync with
    reset_to_upstream in the JSON body and returns a SyncResult on HTTP 200.

    Requirements: 01-REQ-1.3

    Note (errata 01_patches_merged_type): The test_spec pseudocode asserts
    ``result.patches_merged == 2`` (integer), which conflicts with the
    authoritative API reference (docs/proposals/carry_patch_support.md) that
    defines ``patches_merged`` as ``list[str]``.  These tests use the proposal
    definition.  See docs/errata/01_patches_merged_type.md.
    """

    async def test_sync_workspace_returns_sync_result(self) -> None:
        """sync_workspace returns a SyncResult on HTTP 200."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(
            status_code=200,
            json=lambda: {
                "patches_merged": ["feature/already-merged"],
                "rebuild_triggered": True,
                "rebuild_job_id": "job-1",
                "force_push_detected": False,
            },
        )
        client._http_client.post = AsyncMock(return_value=mock_response)
        result = await client.sync_workspace("my-workspace", reset_to_upstream=False)
        assert isinstance(result, SyncResult)

    async def test_sync_workspace_patches_merged_is_list(self) -> None:
        """SyncResult.patches_merged is a list[str] per the hub API spec."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(
            status_code=200,
            json=lambda: {
                "patches_merged": ["feature/already-merged"],
                "rebuild_triggered": True,
                "rebuild_job_id": "job-1",
                "force_push_detected": False,
            },
        )
        client._http_client.post = AsyncMock(return_value=mock_response)
        result = await client.sync_workspace("my-workspace", reset_to_upstream=False)
        assert isinstance(result.patches_merged, list)
        assert "feature/already-merged" in result.patches_merged

    async def test_sync_workspace_posts_to_correct_path(self) -> None:
        """sync_workspace POSTs to /api/v1/workspaces/:slug/sync."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(
            status_code=200,
            json=lambda: {
                "patches_merged": [],
                "rebuild_triggered": False,
                "rebuild_job_id": None,
                "force_push_detected": False,
            },
        )
        client._http_client.post = AsyncMock(return_value=mock_response)
        await client.sync_workspace("my-workspace", reset_to_upstream=False)
        call_kwargs = client._http_client.post.call_args
        assert "/api/v1/workspaces/my-workspace/sync" in str(call_kwargs)

    async def test_sync_workspace_sends_reset_to_upstream_false(self) -> None:
        """sync_workspace includes reset_to_upstream=False in the JSON body."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(
            status_code=200,
            json=lambda: {
                "patches_merged": [],
                "rebuild_triggered": False,
                "rebuild_job_id": None,
                "force_push_detected": False,
            },
        )
        client._http_client.post = AsyncMock(return_value=mock_response)
        await client.sync_workspace("my-workspace", reset_to_upstream=False)
        call_kwargs = client._http_client.post.call_args
        assert call_kwargs.kwargs["json"]["reset_to_upstream"] is False

    async def test_sync_workspace_sends_reset_to_upstream_true(self) -> None:
        """sync_workspace includes reset_to_upstream=True in the JSON body when flagged."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(
            status_code=200,
            json=lambda: {
                "patches_merged": [],
                "rebuild_triggered": True,
                "rebuild_job_id": "job-2",
                "force_push_detected": True,
            },
        )
        client._http_client.post = AsyncMock(return_value=mock_response)
        await client.sync_workspace("my-workspace", reset_to_upstream=True)
        call_kwargs = client._http_client.post.call_args
        assert call_kwargs.kwargs["json"]["reset_to_upstream"] is True


# ---------------------------------------------------------------------------
# TS-01-4: get_patch_status returns PatchStatusDashboard
# ---------------------------------------------------------------------------


class TestGetPatchStatus:
    """TS-01-4 — get_patch_status sends GET /api/v1/workspaces/:slug/patch-status
    and returns a PatchStatusDashboard on HTTP 200.

    Requirements: 01-REQ-1.4
    """

    async def test_get_patch_status_returns_dashboard(self) -> None:
        """get_patch_status returns a PatchStatusDashboard instance."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(
            status_code=200,
            json=lambda: {
                "patches": [],
                "summary": {
                    "total_patches": 0,
                    "active": 0,
                    "merged_upstream": 0,
                    "conflict": 0,
                    "disabled": 0,
                    "total_rerere_resolutions": 0,
                },
            },
        )
        client._http_client.get = AsyncMock(return_value=mock_response)
        result = await client.get_patch_status("my-workspace")
        assert isinstance(result, PatchStatusDashboard)

    async def test_get_patch_status_summary_is_patch_summary(self) -> None:
        """PatchStatusDashboard.summary is a PatchSummary instance."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(
            status_code=200,
            json=lambda: {
                "patches": [],
                "summary": {
                    "total_patches": 0,
                    "active": 0,
                    "merged_upstream": 0,
                    "conflict": 0,
                    "disabled": 0,
                    "total_rerere_resolutions": 0,
                },
            },
        )
        client._http_client.get = AsyncMock(return_value=mock_response)
        result = await client.get_patch_status("my-workspace")
        assert isinstance(result.summary, PatchSummary)

    async def test_get_patch_status_calls_correct_path(self) -> None:
        """get_patch_status GETs /api/v1/workspaces/:slug/patch-status."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(
            status_code=200,
            json=lambda: {
                "patches": [],
                "summary": {
                    "total_patches": 0,
                    "active": 0,
                    "merged_upstream": 0,
                    "conflict": 0,
                    "disabled": 0,
                    "total_rerere_resolutions": 0,
                },
            },
        )
        client._http_client.get = AsyncMock(return_value=mock_response)
        await client.get_patch_status("my-workspace")
        call_args = str(client._http_client.get.call_args)
        assert "/api/v1/workspaces/my-workspace/patch-status" in call_args


# ---------------------------------------------------------------------------
# TS-01-5: reclone_workspace sends POST and returns Workspace
# ---------------------------------------------------------------------------


class TestRecloneWorkspace:
    """TS-01-5 — reclone_workspace sends POST /api/v1/workspaces/:slug/reclone
    and returns a Workspace on HTTP 200.

    Requirements: 01-REQ-1.5
    """

    async def test_reclone_workspace_returns_workspace(self) -> None:
        """reclone_workspace returns a Workspace instance."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(
            status_code=200,
            json=lambda: {
                "slug": "my-workspace",
                "git_url": "https://git.example.com/repo.git",
                "workspace_mode": "carry",
                "status": "active",
                "clone_status": "pending",
                "sync_status": "ok",
            },
        )
        client._http_client.post = AsyncMock(return_value=mock_response)
        result = await client.reclone_workspace("my-workspace")
        assert isinstance(result, Workspace)

    async def test_reclone_workspace_posts_to_correct_path(self) -> None:
        """reclone_workspace POSTs to /api/v1/workspaces/:slug/reclone."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(
            status_code=200,
            json=lambda: {
                "slug": "my-workspace",
                "git_url": "https://git.example.com/repo.git",
                "workspace_mode": "carry",
                "status": "active",
                "clone_status": "pending",
                "sync_status": "ok",
            },
        )
        client._http_client.post = AsyncMock(return_value=mock_response)
        await client.reclone_workspace("my-workspace")
        call_args = str(client._http_client.post.call_args)
        assert "/api/v1/workspaces/my-workspace/reclone" in call_args


# ---------------------------------------------------------------------------
# TS-01-6: aclose() closes the httpx.AsyncClient
# ---------------------------------------------------------------------------


class TestAclose:
    """TS-01-6 — aclose() closes the underlying httpx.AsyncClient and releases
    connection pool resources; subsequent requests raise an error.

    Requirements: 01-REQ-1.6
    """

    async def test_aclose_returns_none(self) -> None:
        """aclose() returns None."""
        client = HubClient("https://hub.example.com", "pat")
        client._http_client.aclose = AsyncMock()
        result = await client.aclose()
        assert result is None

    async def test_aclose_calls_underlying_http_client_aclose(self) -> None:
        """aclose() calls the underlying httpx.AsyncClient.aclose() exactly once."""
        client = HubClient("https://hub.example.com", "pat")
        client._http_client.aclose = AsyncMock()
        await client.aclose()
        client._http_client.aclose.assert_called_once()

    async def test_subsequent_request_raises_after_aclose(self) -> None:
        """get_workspace raises an error after aclose() has been called."""
        client = HubClient("https://hub.example.com", "pat")
        client._http_client.aclose = AsyncMock()
        await client.aclose()
        with pytest.raises(Exception):
            await client.get_workspace("ws1")


# ---------------------------------------------------------------------------
# TS-01-7: HubClient as async context manager
# ---------------------------------------------------------------------------


class TestAsyncContextManager:
    """TS-01-7 — HubClient used as async context manager yields the instance
    and calls aclose() on exit.

    Requirements: 01-REQ-1.7
    """

    async def test_context_manager_yields_hub_client_instance(self) -> None:
        """The context variable 'c' is the same HubClient instance."""
        client = HubClient("https://hub.example.com", "pat")
        client._http_client.aclose = AsyncMock()
        async with client as c:
            assert c is client

    async def test_context_manager_calls_aclose_on_exit(self) -> None:
        """aclose() is called exactly once when the 'async with' block exits."""
        client = HubClient("https://hub.example.com", "pat")
        client._http_client.aclose = AsyncMock()
        async with client as _:
            pass
        client._http_client.aclose.assert_called_once()

    async def test_context_manager_calls_aclose_on_exception(self) -> None:
        """aclose() is still called even when an exception is raised inside the block."""
        client = HubClient("https://hub.example.com", "pat")
        client._http_client.aclose = AsyncMock()
        with pytest.raises(RuntimeError):
            async with client:
                raise RuntimeError("simulated error")
        client._http_client.aclose.assert_called_once()
