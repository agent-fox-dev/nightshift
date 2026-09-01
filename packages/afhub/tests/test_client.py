"""Tests for HubClient workspace, patch, rebuild, rerere, variable, and secret operations.

Covers: TS-01-1 through TS-01-30 (spec 01, groups 1-4),
        TS-01-52 through TS-01-57 (spec 01, group 7).
Requirements: 01-REQ-1 through 01-REQ-4, 01-REQ-9, 01-REQ-10.

These tests are written against the stub implementation and will FAIL until
groups 12-14 provide the real implementation.
"""

from __future__ import annotations

import os
import tomllib
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from afhub.client import HubClient
from afhub.errors import HubConnectionError
from afhub.models import (
    PatchStatusDashboard,
    PatchSummary,
    RebuildJob,
    RebuildPreview,
    RerereEntry,
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


# ---------------------------------------------------------------------------
# TS-01-8: list_patches returns list[Patch]
# ---------------------------------------------------------------------------


class TestListPatches:
    """TS-01-8 — list_patches sends GET /api/v1/workspaces/:slug/patches and
    returns a list of Patch models on HTTP 200.

    Requirements: 01-REQ-2.1
    """

    _PATCH_DATA = {
        "id": "p1",
        "workspace_slug": "my-workspace",
        "branch_name": "feat/x",
        "position": 1,
        "status": "active",
        "added_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }

    async def test_list_patches_returns_list_of_patch_instances(self) -> None:
        """list_patches returns a list containing Patch instances."""
        from afhub.models import Patch

        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(status_code=200, json=lambda: [self._PATCH_DATA])
        client._http_client.get = AsyncMock(return_value=mock_response)
        result = await client.list_patches("my-workspace")
        assert len(result) == 1
        assert isinstance(result[0], Patch)
        assert result[0].id == "p1"

    async def test_list_patches_returns_empty_list_when_no_patches(self) -> None:
        """list_patches returns [] when the hub returns an empty array."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(status_code=200, json=lambda: [])
        client._http_client.get = AsyncMock(return_value=mock_response)
        result = await client.list_patches("my-workspace")
        assert result == []

    async def test_list_patches_calls_correct_path(self) -> None:
        """list_patches GETs /api/v1/workspaces/:slug/patches."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(status_code=200, json=lambda: [])
        client._http_client.get = AsyncMock(return_value=mock_response)
        await client.list_patches("my-workspace")
        call_args = str(client._http_client.get.call_args)
        assert "/api/v1/workspaces/my-workspace/patches" in call_args


# ---------------------------------------------------------------------------
# TS-01-9: add_patch sends single-object JSON body and returns Patch (201)
# ---------------------------------------------------------------------------


class TestAddPatch:
    """TS-01-9 — add_patch sends POST /api/v1/workspaces/:slug/patches with a
    single-object JSON body and returns a Patch on HTTP 201.

    Requirements: 01-REQ-2.2
    """

    _PATCH_DATA = {
        "id": "p1",
        "workspace_slug": "my-workspace",
        "branch_name": "feat/my-branch",
        "position": 1,
        "status": "active",
        "added_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }

    async def test_add_patch_returns_patch_instance(self) -> None:
        """add_patch returns a Patch instance on HTTP 201."""
        from afhub.models import Patch

        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(status_code=201, json=lambda: self._PATCH_DATA)
        client._http_client.post = AsyncMock(return_value=mock_response)
        result = await client.add_patch("my-workspace", "feat/my-branch", position=1)
        assert isinstance(result, Patch)

    async def test_add_patch_sends_single_object_body(self) -> None:
        """add_patch sends a single JSON object (not an array) in the POST body."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(status_code=201, json=lambda: self._PATCH_DATA)
        client._http_client.post = AsyncMock(return_value=mock_response)
        await client.add_patch("my-workspace", "feat/my-branch", position=1)
        sent_body = client._http_client.post.call_args.kwargs["json"]
        assert isinstance(sent_body, dict)

    async def test_add_patch_sends_branch_name_in_body(self) -> None:
        """add_patch includes branch_name in the request body."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(status_code=201, json=lambda: self._PATCH_DATA)
        client._http_client.post = AsyncMock(return_value=mock_response)
        await client.add_patch("my-workspace", "feat/my-branch", position=1)
        sent_body = client._http_client.post.call_args.kwargs["json"]
        assert sent_body["branch_name"] == "feat/my-branch"

    async def test_add_patch_omits_none_optional_fields(self) -> None:
        """add_patch omits upstream_pr_url and description from the body when None."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(status_code=201, json=lambda: self._PATCH_DATA)
        client._http_client.post = AsyncMock(return_value=mock_response)
        await client.add_patch("my-workspace", "feat/my-branch", position=1)
        sent_body = client._http_client.post.call_args.kwargs["json"]
        assert "upstream_pr_url" not in sent_body
        assert "description" not in sent_body

    async def test_add_patch_posts_to_correct_path(self) -> None:
        """add_patch POSTs to /api/v1/workspaces/:slug/patches."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(status_code=201, json=lambda: self._PATCH_DATA)
        client._http_client.post = AsyncMock(return_value=mock_response)
        await client.add_patch("my-workspace", "feat/my-branch", position=1)
        call_args = str(client._http_client.post.call_args)
        assert "/api/v1/workspaces/my-workspace/patches" in call_args


# ---------------------------------------------------------------------------
# TS-01-10: add_patches_batch sends JSON array body and returns list[Patch]
# ---------------------------------------------------------------------------


class TestAddPatchesBatch:
    """TS-01-10 — add_patches_batch sends POST /api/v1/workspaces/:slug/patches
    with a JSON array body and returns list[Patch] on HTTP 201.

    Requirements: 01-REQ-2.3

    Note: add_patches_batch is a distinct method from add_patch — it is NOT
    achieved through runtime dispatch on the argument type.
    """

    _PATCH_DATA = [
        {
            "id": "p1",
            "workspace_slug": "my-workspace",
            "branch_name": "feat/a",
            "position": 1,
            "status": "active",
            "added_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        },
        {
            "id": "p2",
            "workspace_slug": "my-workspace",
            "branch_name": "feat/b",
            "position": 2,
            "status": "active",
            "added_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        },
    ]

    async def test_add_patches_batch_returns_list_of_patch_instances(self) -> None:
        """add_patches_batch returns a list where every element is a Patch."""
        from afhub.models import Patch

        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(status_code=201, json=lambda: self._PATCH_DATA)
        client._http_client.post = AsyncMock(return_value=mock_response)
        result = await client.add_patches_batch(
            "my-workspace",
            [{"branch_name": "feat/a", "position": 1}, {"branch_name": "feat/b", "position": 2}],
        )
        assert isinstance(result, list)
        assert all(isinstance(p, Patch) for p in result)
        assert len(result) == 2

    async def test_add_patches_batch_sends_array_body(self) -> None:
        """add_patches_batch sends a JSON array (not a dict) in the POST body."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(status_code=201, json=lambda: self._PATCH_DATA)
        client._http_client.post = AsyncMock(return_value=mock_response)
        await client.add_patches_batch(
            "my-workspace",
            [{"branch_name": "feat/a", "position": 1}],
        )
        sent_body = client._http_client.post.call_args.kwargs["json"]
        assert isinstance(sent_body, list)

    async def test_add_patch_and_add_patches_batch_are_distinct_methods(self) -> None:
        """add_patch and add_patches_batch are separate methods on HubClient."""
        assert hasattr(HubClient, "add_patch")
        assert hasattr(HubClient, "add_patches_batch")
        assert HubClient.add_patch is not HubClient.add_patches_batch


# ---------------------------------------------------------------------------
# TS-01-11: update_patch sends PATCH and returns Patch
# ---------------------------------------------------------------------------


class TestUpdatePatch:
    """TS-01-11 — update_patch sends PATCH /api/v1/workspaces/:slug/patches/:id
    with kwargs as JSON body and returns a Patch on HTTP 200.

    Requirements: 01-REQ-2.4
    """

    _PATCH_DATA = {
        "id": "p1",
        "workspace_slug": "my-workspace",
        "branch_name": "feat/x",
        "position": 1,
        "status": "active",
        "added_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-02T00:00:00Z",
        "description": "updated desc",
    }

    async def test_update_patch_returns_patch_instance(self) -> None:
        """update_patch returns a Patch instance on HTTP 200."""
        from afhub.models import Patch

        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(status_code=200, json=lambda: self._PATCH_DATA)
        client._http_client.patch = AsyncMock(return_value=mock_response)
        result = await client.update_patch("my-workspace", "p1", description="updated desc")
        assert isinstance(result, Patch)

    async def test_update_patch_returns_updated_field(self) -> None:
        """update_patch result reflects the updated description from the server."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(status_code=200, json=lambda: self._PATCH_DATA)
        client._http_client.patch = AsyncMock(return_value=mock_response)
        result = await client.update_patch("my-workspace", "p1", description="updated desc")
        assert result.description == "updated desc"

    async def test_update_patch_calls_correct_path(self) -> None:
        """update_patch PATCHes /api/v1/workspaces/:slug/patches/:id."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(status_code=200, json=lambda: self._PATCH_DATA)
        client._http_client.patch = AsyncMock(return_value=mock_response)
        await client.update_patch("my-workspace", "p1", description="updated desc")
        call_args = str(client._http_client.patch.call_args)
        assert "/api/v1/workspaces/my-workspace/patches/p1" in call_args


# ---------------------------------------------------------------------------
# TS-01-12: remove_patch sends DELETE and returns None (no body parse)
# ---------------------------------------------------------------------------


class TestRemovePatch:
    """TS-01-12 — remove_patch sends DELETE /api/v1/workspaces/:slug/patches/:id
    and returns None on HTTP 204 without parsing the response body.

    Requirements: 01-REQ-2.5
    """

    async def test_remove_patch_returns_none(self) -> None:
        """remove_patch returns None on HTTP 204."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(status_code=204)
        mock_response.json = MagicMock(side_effect=Exception("should not be called"))
        client._http_client.delete = AsyncMock(return_value=mock_response)
        result = await client.remove_patch("my-workspace", "p1")
        assert result is None

    async def test_remove_patch_does_not_parse_response_body(self) -> None:
        """remove_patch does not call response.json() on a 204 response."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(status_code=204)
        mock_response.json = MagicMock(side_effect=Exception("should not be called"))
        client._http_client.delete = AsyncMock(return_value=mock_response)
        await client.remove_patch("my-workspace", "p1")
        mock_response.json.assert_not_called()

    async def test_remove_patch_calls_correct_path(self) -> None:
        """remove_patch DELETEs /api/v1/workspaces/:slug/patches/:id."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(status_code=204)
        mock_response.json = MagicMock(side_effect=Exception("should not be called"))
        client._http_client.delete = AsyncMock(return_value=mock_response)
        await client.remove_patch("my-workspace", "p1")
        call_args = str(client._http_client.delete.call_args)
        assert "/api/v1/workspaces/my-workspace/patches/p1" in call_args


# ---------------------------------------------------------------------------
# TS-01-13: restore_patch sends POST to /restore and returns Patch
# ---------------------------------------------------------------------------


class TestRestorePatch:
    """TS-01-13 — restore_patch sends POST /api/v1/workspaces/:slug/patches/:id/restore
    and returns a Patch on HTTP 200.

    Requirements: 01-REQ-2.6
    """

    _PATCH_DATA = {
        "id": "p1",
        "workspace_slug": "my-workspace",
        "branch_name": "feat/x",
        "position": 1,
        "status": "active",
        "added_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }

    async def test_restore_patch_returns_patch_instance(self) -> None:
        """restore_patch returns a Patch instance on HTTP 200."""
        from afhub.models import Patch

        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(status_code=200, json=lambda: self._PATCH_DATA)
        client._http_client.post = AsyncMock(return_value=mock_response)
        result = await client.restore_patch("my-workspace", "p1")
        assert isinstance(result, Patch)

    async def test_restore_patch_calls_correct_path(self) -> None:
        """restore_patch POSTs to /api/v1/workspaces/:slug/patches/:id/restore."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(status_code=200, json=lambda: self._PATCH_DATA)
        client._http_client.post = AsyncMock(return_value=mock_response)
        await client.restore_patch("my-workspace", "p1")
        call_args = str(client._http_client.post.call_args)
        assert "/api/v1/workspaces/my-workspace/patches/p1/restore" in call_args


# ---------------------------------------------------------------------------
# TS-01-14: reorder_patches sends POST with patch_ids and returns list[Patch]
# ---------------------------------------------------------------------------


class TestReorderPatches:
    """TS-01-14 — reorder_patches sends POST /api/v1/workspaces/:slug/patches/reorder
    with an ordered patch_ids list in the body and returns list[Patch] on HTTP 200.

    Requirements: 01-REQ-2.7
    """

    _PATCH_DATA = [
        {
            "id": "p2",
            "workspace_slug": "my-workspace",
            "branch_name": "feat/b",
            "position": 1,
            "status": "active",
            "added_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        },
        {
            "id": "p1",
            "workspace_slug": "my-workspace",
            "branch_name": "feat/a",
            "position": 2,
            "status": "active",
            "added_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        },
    ]

    async def test_reorder_patches_returns_list_of_patch_instances(self) -> None:
        """reorder_patches returns a list of Patch instances on HTTP 200."""
        from afhub.models import Patch

        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(status_code=200, json=lambda: self._PATCH_DATA)
        client._http_client.post = AsyncMock(return_value=mock_response)
        result = await client.reorder_patches("my-workspace", ["p2", "p1"])
        assert isinstance(result, list)
        assert all(isinstance(p, Patch) for p in result)

    async def test_reorder_patches_sends_patch_ids_in_body(self) -> None:
        """reorder_patches sends {'patch_ids': [...]} in the POST body."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(status_code=200, json=lambda: self._PATCH_DATA)
        client._http_client.post = AsyncMock(return_value=mock_response)
        await client.reorder_patches("my-workspace", ["p2", "p1"])
        sent_body = client._http_client.post.call_args.kwargs["json"]
        assert sent_body["patch_ids"] == ["p2", "p1"]

    async def test_reorder_patches_calls_correct_path(self) -> None:
        """reorder_patches POSTs to /api/v1/workspaces/:slug/patches/reorder."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(status_code=200, json=lambda: self._PATCH_DATA)
        client._http_client.post = AsyncMock(return_value=mock_response)
        await client.reorder_patches("my-workspace", ["p2", "p1"])
        call_args = str(client._http_client.post.call_args)
        assert "/api/v1/workspaces/my-workspace/patches/reorder" in call_args


# ---------------------------------------------------------------------------
# TS-01-15: submit_rebuild sends POST and returns RebuildJob (202)
# ---------------------------------------------------------------------------


class TestSubmitRebuild:
    """TS-01-15 — submit_rebuild sends POST /api/v1/workspaces/:slug/rebuild
    with strategy and fail_mode in the JSON body and returns a RebuildJob on
    HTTP 202.

    Requirements: 01-REQ-3.1
    """

    _JOB_DATA = {
        "id": "job-1",
        "status": "pending",
        "created_at": "2026-01-01T00:00:00Z",
    }

    async def test_submit_rebuild_returns_rebuild_job_instance(self) -> None:
        """submit_rebuild returns a RebuildJob instance on HTTP 202."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(status_code=202, json=lambda: self._JOB_DATA)
        client._http_client.post = AsyncMock(return_value=mock_response)
        result = await client.submit_rebuild("my-workspace", strategy="merge", fail_mode="stop")
        assert isinstance(result, RebuildJob)

    async def test_submit_rebuild_returns_correct_id(self) -> None:
        """submit_rebuild result has the expected job id from the response."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(status_code=202, json=lambda: self._JOB_DATA)
        client._http_client.post = AsyncMock(return_value=mock_response)
        result = await client.submit_rebuild("my-workspace", strategy="merge", fail_mode="stop")
        assert result.id == "job-1"

    async def test_submit_rebuild_sends_strategy_in_body(self) -> None:
        """submit_rebuild includes strategy in the POST body when provided."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(status_code=202, json=lambda: self._JOB_DATA)
        client._http_client.post = AsyncMock(return_value=mock_response)
        await client.submit_rebuild("my-workspace", strategy="merge", fail_mode="stop")
        sent_body = client._http_client.post.call_args.kwargs["json"]
        assert sent_body.get("strategy") == "merge"

    async def test_submit_rebuild_sends_fail_mode_in_body(self) -> None:
        """submit_rebuild includes fail_mode in the POST body when provided."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(status_code=202, json=lambda: self._JOB_DATA)
        client._http_client.post = AsyncMock(return_value=mock_response)
        await client.submit_rebuild("my-workspace", strategy="merge", fail_mode="stop")
        sent_body = client._http_client.post.call_args.kwargs["json"]
        assert sent_body.get("fail_mode") == "stop"

    async def test_submit_rebuild_omits_none_optional_fields(self) -> None:
        """submit_rebuild omits strategy and fail_mode from the body when None."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(status_code=202, json=lambda: self._JOB_DATA)
        client._http_client.post = AsyncMock(return_value=mock_response)
        await client.submit_rebuild("my-workspace")
        sent_body = client._http_client.post.call_args.kwargs["json"]
        assert "strategy" not in sent_body
        assert "fail_mode" not in sent_body

    async def test_submit_rebuild_posts_to_correct_path(self) -> None:
        """submit_rebuild POSTs to /api/v1/workspaces/:slug/rebuild."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(status_code=202, json=lambda: self._JOB_DATA)
        client._http_client.post = AsyncMock(return_value=mock_response)
        await client.submit_rebuild("my-workspace", strategy="merge", fail_mode="stop")
        call_args = str(client._http_client.post.call_args)
        assert "/api/v1/workspaces/my-workspace/rebuild" in call_args


# ---------------------------------------------------------------------------
# TS-01-16: get_rebuild sends GET and returns RebuildJob
# ---------------------------------------------------------------------------


class TestGetRebuild:
    """TS-01-16 — get_rebuild sends GET /api/v1/workspaces/:slug/rebuilds/:id
    and returns a RebuildJob on HTTP 200.

    Requirements: 01-REQ-3.2
    """

    _JOB_DATA = {
        "id": "job-1",
        "status": "running",
        "created_at": "2026-01-01T00:00:00Z",
    }

    async def test_get_rebuild_returns_rebuild_job_instance(self) -> None:
        """get_rebuild returns a RebuildJob instance on HTTP 200."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(status_code=200, json=lambda: self._JOB_DATA)
        client._http_client.get = AsyncMock(return_value=mock_response)
        result = await client.get_rebuild("my-workspace", "job-1")
        assert isinstance(result, RebuildJob)

    async def test_get_rebuild_returns_correct_id(self) -> None:
        """get_rebuild result has id matching the queried rebuild_id."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(status_code=200, json=lambda: self._JOB_DATA)
        client._http_client.get = AsyncMock(return_value=mock_response)
        result = await client.get_rebuild("my-workspace", "job-1")
        assert result.id == "job-1"

    async def test_get_rebuild_calls_correct_path(self) -> None:
        """get_rebuild GETs /api/v1/workspaces/:slug/rebuilds/:id."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(status_code=200, json=lambda: self._JOB_DATA)
        client._http_client.get = AsyncMock(return_value=mock_response)
        await client.get_rebuild("my-workspace", "job-1")
        call_args = str(client._http_client.get.call_args)
        assert "/api/v1/workspaces/my-workspace/rebuilds/job-1" in call_args


# ---------------------------------------------------------------------------
# TS-01-17: list_rebuilds unwraps 'jobs' envelope and returns list[RebuildJob]
# ---------------------------------------------------------------------------


class TestListRebuilds:
    """TS-01-17 — list_rebuilds sends GET /api/v1/workspaces/:slug/rebuilds,
    unwraps the 'jobs' array, and returns list[RebuildJob] on HTTP 200.

    Requirements: 01-REQ-3.3
    """

    _JOB_DATA = {
        "id": "job-1",
        "status": "completed",
        "created_at": "2026-01-01T00:00:00Z",
    }

    async def test_list_rebuilds_returns_list_of_rebuild_job_instances(self) -> None:
        """list_rebuilds returns a list where every element is a RebuildJob."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(
            status_code=200, json=lambda: {"jobs": [self._JOB_DATA]}
        )
        client._http_client.get = AsyncMock(return_value=mock_response)
        result = await client.list_rebuilds("my-workspace")
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], RebuildJob)

    async def test_list_rebuilds_unwraps_jobs_envelope(self) -> None:
        """list_rebuilds unwraps {jobs: [...]} and does not return the wrapper dict."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(
            status_code=200, json=lambda: {"jobs": [self._JOB_DATA]}
        )
        client._http_client.get = AsyncMock(return_value=mock_response)
        result = await client.list_rebuilds("my-workspace")
        assert not isinstance(result, dict)
        assert result[0].id == "job-1"

    async def test_list_rebuilds_returns_empty_list_when_jobs_is_empty(self) -> None:
        """list_rebuilds returns [] when the 'jobs' array is empty."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(status_code=200, json=lambda: {"jobs": []})
        client._http_client.get = AsyncMock(return_value=mock_response)
        result = await client.list_rebuilds("my-workspace")
        assert result == []

    async def test_list_rebuilds_calls_correct_path(self) -> None:
        """list_rebuilds GETs /api/v1/workspaces/:slug/rebuilds."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(status_code=200, json=lambda: {"jobs": []})
        client._http_client.get = AsyncMock(return_value=mock_response)
        await client.list_rebuilds("my-workspace")
        call_args = str(client._http_client.get.call_args)
        assert "/api/v1/workspaces/my-workspace/rebuilds" in call_args


# ---------------------------------------------------------------------------
# TS-01-18: cancel_rebuild sends DELETE and returns RebuildJob
# ---------------------------------------------------------------------------


class TestCancelRebuild:
    """TS-01-18 — cancel_rebuild sends DELETE /api/v1/workspaces/:slug/rebuilds/:id
    and returns a RebuildJob representing the cancelled job on HTTP 200.

    Requirements: 01-REQ-3.4
    """

    _JOB_DATA = {
        "id": "job-1",
        "status": "cancelled",
        "created_at": "2026-01-01T00:00:00Z",
    }

    async def test_cancel_rebuild_returns_rebuild_job_instance(self) -> None:
        """cancel_rebuild returns a RebuildJob instance on HTTP 200."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(status_code=200, json=lambda: self._JOB_DATA)
        client._http_client.delete = AsyncMock(return_value=mock_response)
        result = await client.cancel_rebuild("my-workspace", "job-1")
        assert isinstance(result, RebuildJob)

    async def test_cancel_rebuild_returns_job_with_cancelled_status(self) -> None:
        """cancel_rebuild result has status='cancelled'."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(status_code=200, json=lambda: self._JOB_DATA)
        client._http_client.delete = AsyncMock(return_value=mock_response)
        result = await client.cancel_rebuild("my-workspace", "job-1")
        assert result.status == "cancelled"

    async def test_cancel_rebuild_calls_correct_path(self) -> None:
        """cancel_rebuild DELETEs /api/v1/workspaces/:slug/rebuilds/:id."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(status_code=200, json=lambda: self._JOB_DATA)
        client._http_client.delete = AsyncMock(return_value=mock_response)
        await client.cancel_rebuild("my-workspace", "job-1")
        call_args = str(client._http_client.delete.call_args)
        assert "/api/v1/workspaces/my-workspace/rebuilds/job-1" in call_args


# ---------------------------------------------------------------------------
# TS-01-19: requeue_rebuild sends POST to /requeue and returns RebuildJob
# ---------------------------------------------------------------------------


class TestRequeueRebuild:
    """TS-01-19 — requeue_rebuild sends POST /api/v1/workspaces/:slug/rebuilds/:id/requeue
    and returns a RebuildJob on HTTP 200.

    Requirements: 01-REQ-3.5
    """

    _JOB_DATA = {
        "id": "job-1",
        "status": "pending",
        "created_at": "2026-01-01T00:00:00Z",
    }

    async def test_requeue_rebuild_returns_rebuild_job_instance(self) -> None:
        """requeue_rebuild returns a RebuildJob instance on HTTP 200."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(status_code=200, json=lambda: self._JOB_DATA)
        client._http_client.post = AsyncMock(return_value=mock_response)
        result = await client.requeue_rebuild("my-workspace", "job-1")
        assert isinstance(result, RebuildJob)

    async def test_requeue_rebuild_calls_correct_path(self) -> None:
        """requeue_rebuild POSTs to /api/v1/workspaces/:slug/rebuilds/:id/requeue."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(status_code=200, json=lambda: self._JOB_DATA)
        client._http_client.post = AsyncMock(return_value=mock_response)
        await client.requeue_rebuild("my-workspace", "job-1")
        call_args = str(client._http_client.post.call_args)
        assert "/api/v1/workspaces/my-workspace/rebuilds/job-1/requeue" in call_args


# ---------------------------------------------------------------------------
# TS-01-20: rollback_rebuild extracts previous_integration_head_sha as str
# ---------------------------------------------------------------------------


class TestRollbackRebuild:
    """TS-01-20 — rollback_rebuild sends POST /api/v1/workspaces/:slug/rebuilds/:id/rollback
    and extracts the 'previous_integration_head_sha' value as a plain string on
    HTTP 200.

    Requirements: 01-REQ-3.6
    """

    async def test_rollback_rebuild_returns_string(self) -> None:
        """rollback_rebuild returns a str (not a dict or RebuildJob)."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(
            status_code=200,
            json=lambda: {"previous_integration_head_sha": "abc123"},
        )
        client._http_client.post = AsyncMock(return_value=mock_response)
        result = await client.rollback_rebuild("my-workspace", "job-1")
        assert isinstance(result, str)

    async def test_rollback_rebuild_returns_sha_value(self) -> None:
        """rollback_rebuild returns the extracted previous_integration_head_sha."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(
            status_code=200,
            json=lambda: {"previous_integration_head_sha": "abc123"},
        )
        client._http_client.post = AsyncMock(return_value=mock_response)
        result = await client.rollback_rebuild("my-workspace", "job-1")
        assert result == "abc123"

    async def test_rollback_rebuild_does_not_return_full_dict(self) -> None:
        """rollback_rebuild returns the sha string, not the raw response dict."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(
            status_code=200,
            json=lambda: {"previous_integration_head_sha": "abc123", "other": "ignored"},
        )
        client._http_client.post = AsyncMock(return_value=mock_response)
        result = await client.rollback_rebuild("my-workspace", "job-1")
        assert result != {"previous_integration_head_sha": "abc123", "other": "ignored"}

    async def test_rollback_rebuild_calls_correct_path(self) -> None:
        """rollback_rebuild POSTs to /api/v1/workspaces/:slug/rebuilds/:id/rollback."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(
            status_code=200,
            json=lambda: {"previous_integration_head_sha": "abc123"},
        )
        client._http_client.post = AsyncMock(return_value=mock_response)
        await client.rollback_rebuild("my-workspace", "job-1")
        call_args = str(client._http_client.post.call_args)
        assert "/api/v1/workspaces/my-workspace/rebuilds/job-1/rollback" in call_args


# ---------------------------------------------------------------------------
# TS-01-21: get_rebuild_preview returns RebuildPreview
# ---------------------------------------------------------------------------


class TestGetRebuildPreview:
    """TS-01-21 — get_rebuild_preview sends GET /api/v1/workspaces/:slug/rebuild-preview
    and returns a RebuildPreview on HTTP 200.

    Requirements: 01-REQ-3.7
    """

    _PREVIEW_DATA = {
        "patch_results": [
            {
                "patch_id": "p1",
                "branch_name": "feat/x",
                "position": 1,
                "status": "would_succeed",
                "tree_sha": None,
                "conflict_files": None,
            }
        ]
    }

    async def test_get_rebuild_preview_returns_rebuild_preview_instance(self) -> None:
        """get_rebuild_preview returns a RebuildPreview instance on HTTP 200."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(status_code=200, json=lambda: self._PREVIEW_DATA)
        client._http_client.get = AsyncMock(return_value=mock_response)
        result = await client.get_rebuild_preview("my-workspace")
        assert isinstance(result, RebuildPreview)

    async def test_get_rebuild_preview_patch_results_has_items(self) -> None:
        """get_rebuild_preview result has patch_results list with one entry."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(status_code=200, json=lambda: self._PREVIEW_DATA)
        client._http_client.get = AsyncMock(return_value=mock_response)
        result = await client.get_rebuild_preview("my-workspace")
        assert len(result.patch_results) == 1

    async def test_get_rebuild_preview_returns_empty_patch_results_when_none(self) -> None:
        """get_rebuild_preview returns RebuildPreview with empty patch_results list."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(status_code=200, json=lambda: {"patch_results": []})
        client._http_client.get = AsyncMock(return_value=mock_response)
        result = await client.get_rebuild_preview("my-workspace")
        assert result.patch_results == []

    async def test_get_rebuild_preview_calls_correct_path(self) -> None:
        """get_rebuild_preview GETs /api/v1/workspaces/:slug/rebuild-preview."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(status_code=200, json=lambda: {"patch_results": []})
        client._http_client.get = AsyncMock(return_value=mock_response)
        await client.get_rebuild_preview("my-workspace")
        call_args = str(client._http_client.get.call_args)
        assert "/api/v1/workspaces/my-workspace/rebuild-preview" in call_args


# ---------------------------------------------------------------------------
# TS-01-22: list_rerere unwraps 'resolutions' envelope and returns list[RerereEntry]
# ---------------------------------------------------------------------------


class TestListRerere:
    """TS-01-22 — list_rerere sends GET /api/v1/workspaces/:slug/rerere,
    unwraps the 'resolutions' array, and returns list[RerereEntry] on HTTP 200.

    Requirements: 01-REQ-4.1
    """

    _ENTRY_DATA = {
        "path": "src/foo.py",
        "recorded_at": "2026-01-01T00:00:00Z",
    }

    async def test_list_rerere_returns_list_of_rerere_entry_instances(self) -> None:
        """list_rerere returns a list where every element is a RerereEntry."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(
            status_code=200, json=lambda: {"resolutions": [self._ENTRY_DATA]}
        )
        client._http_client.get = AsyncMock(return_value=mock_response)
        result = await client.list_rerere("my-workspace")
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], RerereEntry)
        assert result[0].path == "src/foo.py"

    async def test_list_rerere_unwraps_resolutions_envelope(self) -> None:
        """list_rerere unwraps {'resolutions': [...]} and does not return the wrapper dict."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(
            status_code=200, json=lambda: {"resolutions": [self._ENTRY_DATA]}
        )
        client._http_client.get = AsyncMock(return_value=mock_response)
        result = await client.list_rerere("my-workspace")
        assert not isinstance(result, dict)

    async def test_list_rerere_returns_empty_list_when_resolutions_empty(self) -> None:
        """list_rerere returns [] when the 'resolutions' array is empty."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(status_code=200, json=lambda: {"resolutions": []})
        client._http_client.get = AsyncMock(return_value=mock_response)
        result = await client.list_rerere("my-workspace")
        assert result == []

    async def test_list_rerere_calls_correct_path(self) -> None:
        """list_rerere GETs /api/v1/workspaces/:slug/rerere."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(status_code=200, json=lambda: {"resolutions": []})
        client._http_client.get = AsyncMock(return_value=mock_response)
        await client.list_rerere("my-workspace")
        call_args = str(client._http_client.get.call_args)
        assert "/api/v1/workspaces/my-workspace/rerere" in call_args


# ---------------------------------------------------------------------------
# TS-01-23: forget_rerere sends DELETE to /rerere/*pathspec and returns None (204)
# ---------------------------------------------------------------------------


class TestForgetRerere:
    """TS-01-23 — forget_rerere sends DELETE /api/v1/workspaces/:slug/rerere/*pathspec
    and returns None on HTTP 204 without parsing the response body.

    Requirements: 01-REQ-4.2
    """

    async def test_forget_rerere_returns_none(self) -> None:
        """forget_rerere returns None on HTTP 204."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(status_code=204)
        mock_response.json = MagicMock(side_effect=Exception("should not be called"))
        client._http_client.delete = AsyncMock(return_value=mock_response)
        result = await client.forget_rerere("my-workspace", "src/foo.py")
        assert result is None

    async def test_forget_rerere_does_not_parse_response_body(self) -> None:
        """forget_rerere does not call response.json() on a 204 response."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(status_code=204)
        mock_response.json = MagicMock(side_effect=Exception("should not be called"))
        client._http_client.delete = AsyncMock(return_value=mock_response)
        await client.forget_rerere("my-workspace", "src/foo.py")
        mock_response.json.assert_not_called()

    async def test_forget_rerere_calls_correct_path(self) -> None:
        """forget_rerere DELETEs a URL containing the workspace slug and pathspec."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(status_code=204)
        mock_response.json = MagicMock(side_effect=Exception("should not be called"))
        client._http_client.delete = AsyncMock(return_value=mock_response)
        await client.forget_rerere("my-workspace", "src/foo.py")
        call_args = str(client._http_client.delete.call_args)
        assert "/api/v1/workspaces/my-workspace/rerere" in call_args
        assert "src/foo.py" in call_args


# ---------------------------------------------------------------------------
# TS-01-24: create_variable sends POST /vars with key/value and returns None (201)
# ---------------------------------------------------------------------------


class TestCreateVariable:
    """TS-01-24 — create_variable sends POST /api/v1/workspaces/:slug/vars
    with key and value in the JSON body and returns None on HTTP 201.

    Requirements: 01-REQ-4.3
    """

    async def test_create_variable_returns_none(self) -> None:
        """create_variable returns None on HTTP 201."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(status_code=201)
        client._http_client.post = AsyncMock(return_value=mock_response)
        result = await client.create_variable("my-workspace", "MY_KEY", "my_value")
        assert result is None

    async def test_create_variable_sends_key_in_body(self) -> None:
        """create_variable includes key in the POST body."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(status_code=201)
        client._http_client.post = AsyncMock(return_value=mock_response)
        await client.create_variable("my-workspace", "MY_KEY", "my_value")
        sent_body = client._http_client.post.call_args.kwargs["json"]
        assert sent_body["key"] == "MY_KEY"

    async def test_create_variable_sends_value_in_body(self) -> None:
        """create_variable includes value in the POST body."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(status_code=201)
        client._http_client.post = AsyncMock(return_value=mock_response)
        await client.create_variable("my-workspace", "MY_KEY", "my_value")
        sent_body = client._http_client.post.call_args.kwargs["json"]
        assert sent_body["value"] == "my_value"

    async def test_create_variable_posts_to_correct_path(self) -> None:
        """create_variable POSTs to /api/v1/workspaces/:slug/vars."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(status_code=201)
        client._http_client.post = AsyncMock(return_value=mock_response)
        await client.create_variable("my-workspace", "MY_KEY", "my_value")
        call_args = str(client._http_client.post.call_args)
        assert "/api/v1/workspaces/my-workspace/vars" in call_args


# ---------------------------------------------------------------------------
# TS-01-25: update_variable sends PATCH /vars/:key with value and returns None
# ---------------------------------------------------------------------------


class TestUpdateVariable:
    """TS-01-25 — update_variable sends PATCH /api/v1/workspaces/:slug/vars/:key
    with the new value in the JSON body and returns None on HTTP 200.

    Requirements: 01-REQ-4.4
    """

    async def test_update_variable_returns_none(self) -> None:
        """update_variable returns None on HTTP 200."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(status_code=200)
        client._http_client.patch = AsyncMock(return_value=mock_response)
        result = await client.update_variable("my-workspace", "MY_KEY", "new_value")
        assert result is None

    async def test_update_variable_sends_value_in_body(self) -> None:
        """update_variable includes value in the PATCH body."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(status_code=200)
        client._http_client.patch = AsyncMock(return_value=mock_response)
        await client.update_variable("my-workspace", "MY_KEY", "new_value")
        sent_body = client._http_client.patch.call_args.kwargs["json"]
        assert sent_body["value"] == "new_value"

    async def test_update_variable_calls_correct_path(self) -> None:
        """update_variable PATCHes /api/v1/workspaces/:slug/vars/:key."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(status_code=200)
        client._http_client.patch = AsyncMock(return_value=mock_response)
        await client.update_variable("my-workspace", "MY_KEY", "new_value")
        call_args = str(client._http_client.patch.call_args)
        assert "/api/v1/workspaces/my-workspace/vars/MY_KEY" in call_args


# ---------------------------------------------------------------------------
# TS-01-26: set_variable issues PATCH only and returns None when PATCH succeeds
# ---------------------------------------------------------------------------


class TestSetVariablePatchSuccess:
    """TS-01-26 — set_variable sends PATCH /api/v1/workspaces/:slug/vars/:key first;
    on HTTP 200 returns None without issuing a GET or POST.

    Requirements: 01-REQ-4.5
    """

    async def test_set_variable_returns_none_on_patch_success(self) -> None:
        """set_variable returns None when PATCH succeeds with HTTP 200."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(status_code=200)
        client._http_client.patch = AsyncMock(return_value=mock_response)
        client._http_client.get = AsyncMock()
        result = await client.set_variable("my-workspace", "EXISTING_KEY", "val")
        assert result is None

    async def test_set_variable_issues_only_patch_on_success(self) -> None:
        """set_variable issues exactly one PATCH and no GET when PATCH succeeds."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(status_code=200)
        client._http_client.patch = AsyncMock(return_value=mock_response)
        client._http_client.get = AsyncMock()
        await client.set_variable("my-workspace", "EXISTING_KEY", "val")
        client._http_client.patch.assert_called_once()
        client._http_client.get.assert_not_called()


# ---------------------------------------------------------------------------
# TS-01-27: set_variable falls back to POST when PATCH returns 404
# ---------------------------------------------------------------------------


class TestSetVariablePatchFallback:
    """TS-01-27 — set_variable falls back to POST /api/v1/workspaces/:slug/vars
    when PATCH returns HTTP 404, and returns None on HTTP 201 from the fallback POST.

    Requirements: 01-REQ-4.6
    """

    _PATCH_404 = {
        "error": {"code": 404, "message": "not found", "error_type": "not_found"}
    }

    async def test_set_variable_falls_back_to_post_on_404(self) -> None:
        """set_variable returns None when PATCH returns 404 and POST returns 201."""
        client = HubClient("https://hub.example.com", "pat")
        patch_404 = MagicMock(status_code=404, json=lambda: self._PATCH_404)
        post_201 = MagicMock(status_code=201)
        client._http_client.patch = AsyncMock(return_value=patch_404)
        client._http_client.post = AsyncMock(return_value=post_201)
        result = await client.set_variable("my-workspace", "NEW_KEY", "val")
        assert result is None

    async def test_set_variable_calls_patch_then_post_on_404(self) -> None:
        """set_variable calls PATCH exactly once then POST exactly once on 404."""
        client = HubClient("https://hub.example.com", "pat")
        patch_404 = MagicMock(status_code=404, json=lambda: self._PATCH_404)
        post_201 = MagicMock(status_code=201)
        client._http_client.patch = AsyncMock(return_value=patch_404)
        client._http_client.post = AsyncMock(return_value=post_201)
        await client.set_variable("my-workspace", "NEW_KEY", "val")
        client._http_client.patch.assert_called_once()
        client._http_client.post.assert_called_once()

    async def test_set_variable_fallback_post_sends_key_and_value(self) -> None:
        """set_variable fallback POST body contains key and value."""
        client = HubClient("https://hub.example.com", "pat")
        patch_404 = MagicMock(status_code=404, json=lambda: self._PATCH_404)
        post_201 = MagicMock(status_code=201)
        client._http_client.patch = AsyncMock(return_value=patch_404)
        client._http_client.post = AsyncMock(return_value=post_201)
        await client.set_variable("my-workspace", "NEW_KEY", "val")
        post_body = client._http_client.post.call_args.kwargs["json"]
        assert post_body["key"] == "NEW_KEY"
        assert post_body["value"] == "val"

    async def test_set_variable_no_get_issued_on_fallback_path(self) -> None:
        """set_variable does not issue a GET even when falling back from PATCH to POST."""
        client = HubClient("https://hub.example.com", "pat")
        patch_404 = MagicMock(status_code=404, json=lambda: self._PATCH_404)
        post_201 = MagicMock(status_code=201)
        client._http_client.patch = AsyncMock(return_value=patch_404)
        client._http_client.post = AsyncMock(return_value=post_201)
        client._http_client.get = AsyncMock()
        await client.set_variable("my-workspace", "NEW_KEY", "val")
        client._http_client.get.assert_not_called()


# ---------------------------------------------------------------------------
# TS-01-28: delete_variable sends DELETE /vars/:key and returns None (204)
# ---------------------------------------------------------------------------


class TestDeleteVariable:
    """TS-01-28 — delete_variable sends DELETE /api/v1/workspaces/:slug/vars/:key
    and returns None on HTTP 204 without parsing the response body.

    Requirements: 01-REQ-4.7
    """

    async def test_delete_variable_returns_none(self) -> None:
        """delete_variable returns None on HTTP 204."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(status_code=204)
        mock_response.json = MagicMock(side_effect=Exception("should not be called"))
        client._http_client.delete = AsyncMock(return_value=mock_response)
        result = await client.delete_variable("my-workspace", "MY_KEY")
        assert result is None

    async def test_delete_variable_does_not_parse_response_body(self) -> None:
        """delete_variable does not call response.json() on a 204 response."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(status_code=204)
        mock_response.json = MagicMock(side_effect=Exception("should not be called"))
        client._http_client.delete = AsyncMock(return_value=mock_response)
        await client.delete_variable("my-workspace", "MY_KEY")
        mock_response.json.assert_not_called()

    async def test_delete_variable_calls_correct_path(self) -> None:
        """delete_variable DELETEs /api/v1/workspaces/:slug/vars/:key."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(status_code=204)
        mock_response.json = MagicMock(side_effect=Exception("should not be called"))
        client._http_client.delete = AsyncMock(return_value=mock_response)
        await client.delete_variable("my-workspace", "MY_KEY")
        call_args = str(client._http_client.delete.call_args)
        assert "/api/v1/workspaces/my-workspace/vars/MY_KEY" in call_args


# ---------------------------------------------------------------------------
# TS-01-29: get_resolved_variables returns dict[str, str]
# ---------------------------------------------------------------------------


class TestGetResolvedVariables:
    """TS-01-29 — get_resolved_variables sends GET /api/v1/workspaces/:slug/vars/resolved
    and returns dict[str, str] on HTTP 200; returns {} for empty response.

    Requirements: 01-REQ-4.8
    """

    async def test_get_resolved_variables_returns_dict(self) -> None:
        """get_resolved_variables returns a dict."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(
            status_code=200, json=lambda: {"KEY1": "val1", "KEY2": "val2"}
        )
        client._http_client.get = AsyncMock(return_value=mock_response)
        result = await client.get_resolved_variables("my-workspace")
        assert isinstance(result, dict)

    async def test_get_resolved_variables_returns_correct_values(self) -> None:
        """get_resolved_variables returns the key-value pairs from the response."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(
            status_code=200, json=lambda: {"KEY1": "val1", "KEY2": "val2"}
        )
        client._http_client.get = AsyncMock(return_value=mock_response)
        result = await client.get_resolved_variables("my-workspace")
        assert result == {"KEY1": "val1", "KEY2": "val2"}

    async def test_get_resolved_variables_returns_empty_dict_when_no_vars(self) -> None:
        """get_resolved_variables returns {} when the hub returns an empty object."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(status_code=200, json=lambda: {})
        client._http_client.get = AsyncMock(return_value=mock_response)
        result = await client.get_resolved_variables("my-workspace")
        assert result == {}

    async def test_get_resolved_variables_calls_correct_path(self) -> None:
        """get_resolved_variables GETs /api/v1/workspaces/:slug/vars/resolved."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(status_code=200, json=lambda: {})
        client._http_client.get = AsyncMock(return_value=mock_response)
        await client.get_resolved_variables("my-workspace")
        call_args = str(client._http_client.get.call_args)
        assert "/api/v1/workspaces/my-workspace/vars/resolved" in call_args


# ---------------------------------------------------------------------------
# TS-01-30: create_secret sends POST /secrets with key/value and returns None (201)
# ---------------------------------------------------------------------------


class TestCreateSecret:
    """TS-01-30 — create_secret sends POST /api/v1/workspaces/:slug/secrets
    with key and value in the JSON body and returns None on HTTP 201.

    Requirements: 01-REQ-4.9
    """

    async def test_create_secret_returns_none(self) -> None:
        """create_secret returns None on HTTP 201."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(status_code=201)
        client._http_client.post = AsyncMock(return_value=mock_response)
        result = await client.create_secret("my-workspace", "MY_SECRET", "s3cr3t")
        assert result is None

    async def test_create_secret_sends_key_in_body(self) -> None:
        """create_secret includes key in the POST body."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(status_code=201)
        client._http_client.post = AsyncMock(return_value=mock_response)
        await client.create_secret("my-workspace", "MY_SECRET", "s3cr3t")
        sent_body = client._http_client.post.call_args.kwargs["json"]
        assert sent_body["key"] == "MY_SECRET"

    async def test_create_secret_sends_value_in_body(self) -> None:
        """create_secret includes value in the POST body."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(status_code=201)
        client._http_client.post = AsyncMock(return_value=mock_response)
        await client.create_secret("my-workspace", "MY_SECRET", "s3cr3t")
        sent_body = client._http_client.post.call_args.kwargs["json"]
        assert sent_body["value"] == "s3cr3t"

    async def test_create_secret_posts_to_correct_path(self) -> None:
        """create_secret POSTs to /api/v1/workspaces/:slug/secrets."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(status_code=201)
        client._http_client.post = AsyncMock(return_value=mock_response)
        await client.create_secret("my-workspace", "MY_SECRET", "s3cr3t")
        call_args = str(client._http_client.post.call_args)
        assert "/api/v1/workspaces/my-workspace/secrets" in call_args


# ---------------------------------------------------------------------------
# TS-01-30 (design note): no list_variables method on HubClient
# ---------------------------------------------------------------------------


class TestNoListVariables:
    """Design constraint — HubClient deliberately omits list_variables.

    Variable responses are intentionally opaque; only get_resolved_variables
    (GET /vars/resolved) is provided for reading variable state.

    Requirements: 01-REQ-4 (design note)
    """

    def test_no_list_variables_method_on_hub_client(self) -> None:
        """HubClient has no list_variables method — intentionally omitted by design."""
        assert not hasattr(HubClient, "list_variables")


# ---------------------------------------------------------------------------
# TS-01-52: HubClient retries up to 3 times with exponential backoff
# ---------------------------------------------------------------------------


class TestRetryWithBackoff:
    """TS-01-52 -- HubClient retries up to 3 times with exponential backoff
    when httpx raises ConnectTimeout, ReadTimeout, or ConnectError, then raises
    HubConnectionError.

    Requirements: 01-REQ-9.1, 01-REQ-9.E1, 01-REQ-9.E2, 01-REQ-9.E3
    Correctness properties: 01-PROP-4, 01-PROP-5
    """

    async def test_connect_timeout_retried_then_hub_connection_error(self) -> None:
        """ConnectTimeout on all attempts raises HubConnectionError after
        4 total attempts (1 original + 3 retries) (01-PROP-4).
        """
        client = HubClient("https://hub.example.com", "pat")
        client._http_client.get = AsyncMock(
            side_effect=httpx.ConnectTimeout("timeout")
        )
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            with pytest.raises(HubConnectionError):
                await client.get_workspace("ws1")
            assert client._http_client.get.call_count == 4
            sleep_calls = [call.args[0] for call in mock_sleep.call_args_list]
            assert sleep_calls == [1.0, 2.0, 4.0]

    async def test_read_timeout_retried_then_hub_connection_error(self) -> None:
        """ReadTimeout on all attempts raises HubConnectionError after
        4 total attempts.
        """
        client = HubClient("https://hub.example.com", "pat")
        client._http_client.get = AsyncMock(
            side_effect=httpx.ReadTimeout("read timeout")
        )
        with patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(HubConnectionError):
                await client.get_workspace("ws1")
            assert client._http_client.get.call_count == 4

    async def test_connect_error_retried_then_hub_connection_error(self) -> None:
        """ConnectError on all attempts raises HubConnectionError after
        4 total attempts.
        """
        client = HubClient("https://hub.example.com", "pat")
        client._http_client.get = AsyncMock(
            side_effect=httpx.ConnectError("connection refused")
        )
        with patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(HubConnectionError):
                await client.get_workspace("ws1")
            assert client._http_client.get.call_count == 4

    async def test_second_attempt_succeeds_returns_response(self) -> None:
        """When first attempt raises ConnectTimeout but second succeeds,
        returns parsed response without raising (01-REQ-9.E2).
        """
        client = HubClient("https://hub.example.com", "pat")
        success_response = MagicMock(
            status_code=200,
            json=lambda: {
                "slug": "ws1",
                "git_url": "https://git.example.com/repo.git",
                "workspace_mode": "carry",
                "status": "active",
                "clone_status": "ready",
                "sync_status": "ok",
            },
        )
        client._http_client.get = AsyncMock(
            side_effect=[httpx.ConnectTimeout("timeout"), success_response]
        )
        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await client.get_workspace("ws1")
            assert result.slug == "ws1"

    async def test_backoff_sleep_capped_at_30_seconds(self) -> None:
        """Backoff sleep is never greater than 30.0 seconds
        (01-REQ-9.E3, 01-PROP-5).
        """
        client = HubClient("https://hub.example.com", "pat")
        client._http_client.get = AsyncMock(
            side_effect=httpx.ConnectTimeout("timeout")
        )
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            with pytest.raises(HubConnectionError):
                await client.get_workspace("ws1")
            for call in mock_sleep.call_args_list:
                assert call.args[0] <= 30.0

    async def test_exponential_backoff_values(self) -> None:
        """Backoff delays follow base 1s * (factor 2 ^ attempt):
        1.0, 2.0, 4.0 for 3 retries (01-REQ-9.1).
        """
        client = HubClient("https://hub.example.com", "pat")
        client._http_client.get = AsyncMock(
            side_effect=httpx.ConnectTimeout("timeout")
        )
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            with pytest.raises(HubConnectionError):
                await client.get_workspace("ws1")
            sleep_calls = [call.args[0] for call in mock_sleep.call_args_list]
            assert sleep_calls == [1.0, 2.0, 4.0]


# ---------------------------------------------------------------------------
# TS-01-53: Non-retryable exceptions propagate immediately
# ---------------------------------------------------------------------------


class TestNonRetryableExceptions:
    """TS-01-53 -- HubClient does not retry on non-retryable httpx exceptions;
    the exception propagates immediately.

    Requirements: 01-REQ-9.2, 01-REQ-9.E4
    """

    async def test_timeout_exception_base_not_retried(self) -> None:
        """httpx.TimeoutException (base class) propagates immediately
        without retry.
        """
        client = HubClient("https://hub.example.com", "pat")
        client._http_client.get = AsyncMock(
            side_effect=httpx.TimeoutException("base timeout")
        )
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            with pytest.raises(httpx.TimeoutException):
                await client.get_workspace("ws1")
            assert client._http_client.get.call_count == 1
            mock_sleep.assert_not_called()

    async def test_pool_timeout_not_retried(self) -> None:
        """httpx.PoolTimeout propagates immediately without retry
        (01-REQ-9.E4).
        """
        client = HubClient("https://hub.example.com", "pat")
        client._http_client.get = AsyncMock(
            side_effect=httpx.PoolTimeout("pool timeout")
        )
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            with pytest.raises(httpx.PoolTimeout):
                await client.get_workspace("ws1")
            assert client._http_client.get.call_count == 1
            mock_sleep.assert_not_called()

    async def test_write_timeout_not_retried(self) -> None:
        """httpx.WriteTimeout propagates immediately without retry
        (01-REQ-9.E4).
        """
        client = HubClient("https://hub.example.com", "pat")
        client._http_client.get = AsyncMock(
            side_effect=httpx.WriteTimeout("write timeout")
        )
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            with pytest.raises(httpx.WriteTimeout):
                await client.get_workspace("ws1")
            assert client._http_client.get.call_count == 1
            mock_sleep.assert_not_called()


# ---------------------------------------------------------------------------
# TS-01-54: Every HTTP request contains /api/v1/ path prefix
# ---------------------------------------------------------------------------


class TestApiV1PathPrefix:
    """TS-01-54 -- Every HTTP request issued by HubClient contains /api/v1/
    as the path prefix.

    Requirements: 01-REQ-10.1
    Correctness property: 01-PROP-2
    """

    async def test_get_workspace_uses_api_v1_prefix(self) -> None:
        """get_workspace request URL contains '/api/v1/'."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(
            status_code=200,
            json=lambda: {
                "slug": "ws1",
                "git_url": "https://git.example.com/repo.git",
                "workspace_mode": "carry",
                "status": "active",
                "clone_status": "ready",
                "sync_status": "ok",
            },
        )
        client._http_client.get = AsyncMock(return_value=mock_response)
        await client.get_workspace("ws1")
        call_args = str(client._http_client.get.call_args)
        assert "/api/v1/" in call_args

    async def test_submit_rebuild_uses_api_v1_prefix(self) -> None:
        """submit_rebuild request URL contains '/api/v1/'."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(
            status_code=202,
            json=lambda: {
                "id": "job-1",
                "status": "pending",
                "created_at": "2026-01-01T00:00:00Z",
            },
        )
        client._http_client.post = AsyncMock(return_value=mock_response)
        await client.submit_rebuild("ws1")
        call_args = str(client._http_client.post.call_args)
        assert "/api/v1/" in call_args

    async def test_list_patches_uses_api_v1_prefix(self) -> None:
        """list_patches request URL contains '/api/v1/'."""
        client = HubClient("https://hub.example.com", "pat")
        mock_response = MagicMock(status_code=200, json=lambda: [])
        client._http_client.get = AsyncMock(return_value=mock_response)
        await client.list_patches("ws1")
        call_args = str(client._http_client.get.call_args)
        assert "/api/v1/" in call_args


# ---------------------------------------------------------------------------
# TS-01-55: pyproject.toml declares correct dependencies
# ---------------------------------------------------------------------------


class TestPyprojectTomlDependencies:
    """TS-01-55 -- afhub pyproject.toml declares correct runtime and dev
    dependencies with Python >=3.12.

    Requirements: 01-REQ-10.2
    """

    _PYPROJECT = os.path.join(os.path.dirname(__file__), "..", "pyproject.toml")

    def test_runtime_dep_httpx(self) -> None:
        """pyproject.toml declares httpx>=0.27 as a runtime dependency."""
        with open(self._PYPROJECT, "rb") as f:
            config = tomllib.load(f)
        deps = config["project"]["dependencies"]
        assert any("httpx" in d and "0.27" in d for d in deps)

    def test_runtime_dep_pydantic(self) -> None:
        """pyproject.toml declares pydantic>=2.0 as a runtime dependency."""
        with open(self._PYPROJECT, "rb") as f:
            config = tomllib.load(f)
        deps = config["project"]["dependencies"]
        assert any("pydantic" in d and "2.0" in d for d in deps)

    def test_python_requires_312(self) -> None:
        """pyproject.toml declares requires-python >= 3.12."""
        with open(self._PYPROJECT, "rb") as f:
            config = tomllib.load(f)
        assert config["project"]["requires-python"] == ">=3.12"


# ---------------------------------------------------------------------------
# TS-01-56: Package source and test files exist at specified paths
# ---------------------------------------------------------------------------


class TestPackageLayout:
    """TS-01-56 -- afhub package source files and test files exist at the
    specified paths.

    Requirements: 01-REQ-10.3
    """

    _PKG_ROOT = os.path.join(os.path.dirname(__file__), "..")

    @pytest.mark.parametrize(
        "rel_path",
        [
            "afhub/__init__.py",
            "afhub/client.py",
            "afhub/models.py",
            "afhub/errors.py",
            "afhub/auth.py",
            "afhub/polling.py",
            "afhub/_http.py",
            "tests/test_client.py",
            "tests/test_models.py",
            "tests/test_errors.py",
            "tests/test_auth.py",
            "tests/test_polling.py",
        ],
    )
    def test_file_exists(self, rel_path: str) -> None:
        """Expected file exists within the packages/afhub directory."""
        full_path = os.path.join(self._PKG_ROOT, rel_path)
        assert os.path.exists(full_path), f"Expected file not found: {full_path}"


# ---------------------------------------------------------------------------
# TS-01-57: pytest asyncio_mode=auto configured, no external mock library
# ---------------------------------------------------------------------------


class TestPytestConfig:
    """TS-01-57 -- pytest with asyncio_mode=auto runs all async test functions
    without explicit decorators; unittest.mock is used for httpx mocking with
    no external mock library.

    Requirements: 01-REQ-10.4
    """

    _PYPROJECT = os.path.join(os.path.dirname(__file__), "..", "pyproject.toml")

    def test_asyncio_mode_auto_configured(self) -> None:
        """pyproject.toml configures asyncio_mode='auto' for pytest."""
        with open(self._PYPROJECT, "rb") as f:
            config = tomllib.load(f)
        ini_opts = config.get("tool", {}).get("pytest", {}).get("ini_options", {})
        assert ini_opts.get("asyncio_mode") == "auto"

    def test_no_external_mock_library_imported(self) -> None:
        """No test file imports an external mock library like pytest-mock."""
        # Build patterns dynamically to avoid this test's own source
        # matching itself.
        ext_lib = "pytest" + "_" + "mock"
        patterns = [f"from {ext_lib}", f"import {ext_lib}"]
        test_dir = os.path.dirname(__file__)
        for fname in os.listdir(test_dir):
            if fname.startswith("test_") and fname.endswith(".py"):
                filepath = os.path.join(test_dir, fname)
                with open(filepath) as f:
                    content = f.read()
                for pat in patterns:
                    assert pat not in content, (
                        f"External mock library found in {fname}"
                    )


# ---------------------------------------------------------------------------
# TS-01-SMOKE-1: End-to-end smoke test — submit rebuild and poll to completion
# ---------------------------------------------------------------------------


class TestSmoke:
    """TS-01-SMOKE-1 — End-to-end smoke test: instantiate HubClient, submit a
    rebuild, and poll until the job reaches 'completed' status using mocked
    httpx responses.

    Real components: HubClient, poll_rebuild, RebuildJob, afhub._http retry logic.
    Mockable: httpx.AsyncClient (network I/O), asyncio.sleep.

    Requirements: 01-REQ-1, 01-REQ-3, 01-REQ-8, 01-REQ-9
    Execution path: 01-PATH-1
    """

    async def test_smoke_submit_rebuild_and_poll_to_completion(self) -> None:
        """Submit a rebuild via HubClient, then poll until 'completed'."""
        from afhub.polling import poll_rebuild

        # 1. Instantiate HubClient with real constructor (creates httpx.AsyncClient)
        client = HubClient("https://hub.example.com", "test-pat")

        # 2. Mock POST /rebuild -> 202 with pending RebuildJob
        submit_response = MagicMock(
            status_code=202,
            json=lambda: {
                "id": "job-1",
                "status": "pending",
                "created_at": "2026-01-01T00:00:00Z",
            },
        )
        client._http_client.post = AsyncMock(return_value=submit_response)

        job = await client.submit_rebuild("ws1", strategy="merge", fail_mode="stop")
        assert isinstance(job, RebuildJob)
        assert job.status == "pending"
        assert job.id == "job-1"

        # 3. Mock GET /rebuilds/:id -> running, then completed
        running_response = MagicMock(
            status_code=200,
            json=lambda: {
                "id": "job-1",
                "status": "running",
                "created_at": "2026-01-01T00:00:00Z",
            },
        )
        completed_response = MagicMock(
            status_code=200,
            json=lambda: {
                "id": "job-1",
                "status": "completed",
                "created_at": "2026-01-01T00:00:00Z",
                "completed_at": "2026-01-01T00:05:00Z",
            },
        )
        client._http_client.get = AsyncMock(
            side_effect=[running_response, completed_response]
        )

        # 4. Poll until terminal
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await poll_rebuild(
                client, "ws1", "job-1", timeout=600.0, interval=5.0
            )

        # 5. Verify outcomes
        assert isinstance(result, RebuildJob)
        assert result.status == "completed"
        assert result.id == "job-1"
        # asyncio.sleep called once with interval=5.0 between the two polls
        mock_sleep.assert_called_once_with(5.0)
        # get_rebuild called exactly twice (running -> completed)
        assert client._http_client.get.call_count == 2
