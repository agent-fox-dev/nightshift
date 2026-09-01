"""HubClient -- async client for the af-hub carry-patch REST API.

Implements retry logic (group 12), error handling (group 9),
workspace/patch response parsing (group 13), and rebuild/rerere/
variable/secret response parsing (group 14).
"""

from __future__ import annotations

from typing import Any

import httpx

from afhub._http import DEFAULT_TIMEOUT, request_with_retry
from afhub.errors import HubError, _raise_for_status
from afhub.models import (
    Patch,
    PatchStatusDashboard,
    RebuildJob,
    RebuildPreview,
    RerereEntry,
    SyncResult,
    Workspace,
)


class HubClient:
    """Async client for the af-hub carry-patch REST API.

    Wraps an ``httpx.AsyncClient`` with bearer-token auth, API-versioned
    URLs, structured error dispatch, and transient-error retry with
    exponential backoff.
    """

    _http_client: httpx.AsyncClient

    def __init__(self, endpoint_url: str, pat: str) -> None:
        if not endpoint_url:
            raise ValueError("endpoint_url must not be empty")
        if not pat:
            raise ValueError("pat must not be empty")
        self._http_client = httpx.AsyncClient(
            base_url=endpoint_url.rstrip("/"),
            headers={"Authorization": f"Bearer {pat}"},
            timeout=DEFAULT_TIMEOUT,
        )

    async def __aenter__(self) -> HubClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the underlying HTTP client and release resources."""
        await self._http_client.aclose()

    # -- Workspace operations ------------------------------------------------

    async def get_workspace(self, slug: str) -> Workspace:
        resp = await request_with_retry(
            self._http_client.get, f"/api/v1/workspaces/{slug}"
        )
        _raise_for_status(resp)
        return Workspace(**resp.json())

    async def sync_workspace(self, slug: str, *, reset_to_upstream: bool = False) -> SyncResult:
        body: dict[str, Any] = {"reset_to_upstream": reset_to_upstream}
        resp = await request_with_retry(
            self._http_client.post,
            f"/api/v1/workspaces/{slug}/sync",
            json=body,
        )
        _raise_for_status(resp)
        return SyncResult(**resp.json())

    async def get_patch_status(self, slug: str) -> PatchStatusDashboard:
        resp = await request_with_retry(
            self._http_client.get, f"/api/v1/workspaces/{slug}/patch-status"
        )
        _raise_for_status(resp)
        return PatchStatusDashboard(**resp.json())

    async def reclone_workspace(self, slug: str) -> Workspace:
        resp = await request_with_retry(
            self._http_client.post, f"/api/v1/workspaces/{slug}/reclone"
        )
        _raise_for_status(resp)
        return Workspace(**resp.json())

    # -- Patch operations ----------------------------------------------------

    async def list_patches(self, slug: str) -> list[Patch]:
        resp = await request_with_retry(
            self._http_client.get, f"/api/v1/workspaces/{slug}/patches"
        )
        _raise_for_status(resp)
        return [Patch(**p) for p in resp.json()]

    async def add_patch(
        self,
        slug: str,
        branch_name: str,
        *,
        position: int | None = None,
        description: str | None = None,
        upstream_pr_url: str | None = None,
        if_not_exists: bool = False,
        skip_branch_check: bool = False,
    ) -> Patch:
        body: dict[str, Any] = {"branch_name": branch_name}
        if position is not None:
            body["position"] = position
        if description is not None:
            body["description"] = description
        if upstream_pr_url is not None:
            body["upstream_pr_url"] = upstream_pr_url
        if if_not_exists:
            body["if_not_exists"] = True
        if skip_branch_check:
            body["skip_branch_check"] = True
        resp = await request_with_retry(
            self._http_client.post,
            f"/api/v1/workspaces/{slug}/patches",
            json=body,
        )
        _raise_for_status(resp)
        return Patch(**resp.json())

    async def add_patches_batch(self, slug: str, patches: list[dict[str, Any]]) -> list[Patch]:
        resp = await request_with_retry(
            self._http_client.post,
            f"/api/v1/workspaces/{slug}/patches",
            json=patches,
        )
        _raise_for_status(resp)
        return [Patch(**p) for p in resp.json()]

    async def update_patch(self, slug: str, patch_id: str, **kwargs: Any) -> Patch:
        resp = await request_with_retry(
            self._http_client.patch,
            f"/api/v1/workspaces/{slug}/patches/{patch_id}",
            json=kwargs,
        )
        _raise_for_status(resp)
        return Patch(**resp.json())

    async def remove_patch(self, slug: str, patch_id: str) -> None:
        resp = await request_with_retry(
            self._http_client.delete,
            f"/api/v1/workspaces/{slug}/patches/{patch_id}",
        )
        _raise_for_status(resp)

    async def restore_patch(self, slug: str, patch_id: str) -> Patch:
        resp = await request_with_retry(
            self._http_client.post,
            f"/api/v1/workspaces/{slug}/patches/{patch_id}/restore",
        )
        _raise_for_status(resp)
        return Patch(**resp.json())

    async def reorder_patches(self, slug: str, ordered_ids: list[str]) -> list[Patch]:
        resp = await request_with_retry(
            self._http_client.post,
            f"/api/v1/workspaces/{slug}/patches/reorder",
            json={"patch_ids": ordered_ids},
        )
        _raise_for_status(resp)
        return [Patch(**p) for p in resp.json()]

    # -- Rebuild operations --------------------------------------------------

    async def submit_rebuild(
        self,
        slug: str,
        *,
        strategy: str | None = None,
        fail_mode: str | None = None,
    ) -> RebuildJob:
        body: dict[str, Any] = {}
        if strategy is not None:
            body["strategy"] = strategy
        if fail_mode is not None:
            body["fail_mode"] = fail_mode
        resp = await request_with_retry(
            self._http_client.post,
            f"/api/v1/workspaces/{slug}/rebuild",
            json=body,
        )
        _raise_for_status(resp)
        return RebuildJob(**resp.json())

    async def get_rebuild(self, slug: str, job_id: str) -> RebuildJob:
        resp = await request_with_retry(
            self._http_client.get,
            f"/api/v1/workspaces/{slug}/rebuilds/{job_id}",
        )
        _raise_for_status(resp)
        return RebuildJob(**resp.json())

    async def list_rebuilds(self, slug: str) -> list[RebuildJob]:
        resp = await request_with_retry(
            self._http_client.get, f"/api/v1/workspaces/{slug}/rebuilds"
        )
        _raise_for_status(resp)
        return [RebuildJob(**j) for j in resp.json()["jobs"]]

    async def cancel_rebuild(self, slug: str, job_id: str) -> RebuildJob:
        resp = await request_with_retry(
            self._http_client.delete,
            f"/api/v1/workspaces/{slug}/rebuilds/{job_id}",
        )
        _raise_for_status(resp)
        return RebuildJob(**resp.json())

    async def requeue_rebuild(self, slug: str, job_id: str) -> RebuildJob:
        resp = await request_with_retry(
            self._http_client.post,
            f"/api/v1/workspaces/{slug}/rebuilds/{job_id}/requeue",
        )
        _raise_for_status(resp)
        return RebuildJob(**resp.json())

    async def rollback_rebuild(self, slug: str, job_id: str) -> str:
        resp = await request_with_retry(
            self._http_client.post,
            f"/api/v1/workspaces/{slug}/rebuilds/{job_id}/rollback",
        )
        _raise_for_status(resp)
        data = resp.json()
        key = "previous_integration_head_sha"
        if key not in data:
            raise HubError(
                status_code=200,
                error_type="missing_field",
                message=f"Response missing expected key: {key}",
            )
        return data[key]

    async def get_rebuild_preview(self, slug: str) -> RebuildPreview:
        resp = await request_with_retry(
            self._http_client.get,
            f"/api/v1/workspaces/{slug}/rebuild-preview",
        )
        _raise_for_status(resp)
        return RebuildPreview(**resp.json())

    # -- Rerere operations ---------------------------------------------------

    async def list_rerere(self, slug: str) -> list[RerereEntry]:
        resp = await request_with_retry(
            self._http_client.get, f"/api/v1/workspaces/{slug}/rerere"
        )
        _raise_for_status(resp)
        return [RerereEntry(**e) for e in resp.json()["resolutions"]]

    async def forget_rerere(self, slug: str, pathspec: str) -> None:
        resp = await request_with_retry(
            self._http_client.delete,
            f"/api/v1/workspaces/{slug}/rerere/{pathspec}",
        )
        _raise_for_status(resp)

    # -- Variable operations -------------------------------------------------

    async def create_variable(self, slug: str, key: str, value: str) -> None:
        resp = await request_with_retry(
            self._http_client.post,
            f"/api/v1/workspaces/{slug}/vars",
            json={"key": key, "value": value},
        )
        _raise_for_status(resp)

    async def update_variable(self, slug: str, key: str, value: str) -> None:
        resp = await request_with_retry(
            self._http_client.patch,
            f"/api/v1/workspaces/{slug}/vars/{key}",
            json={"value": value},
        )
        _raise_for_status(resp)

    async def set_variable(self, slug: str, key: str, value: str) -> None:
        from afhub.errors import HubNotFoundError

        try:
            return await self.update_variable(slug, key, value)
        except HubNotFoundError:
            return await self.create_variable(slug, key, value)

    async def delete_variable(self, slug: str, key: str) -> None:
        resp = await request_with_retry(
            self._http_client.delete, f"/api/v1/workspaces/{slug}/vars/{key}"
        )
        _raise_for_status(resp)

    async def get_resolved_variables(self, slug: str) -> dict[str, str]:
        resp = await request_with_retry(
            self._http_client.get,
            f"/api/v1/workspaces/{slug}/vars/resolved",
        )
        _raise_for_status(resp)
        return resp.json()

    # -- Secret operations ---------------------------------------------------

    async def create_secret(self, slug: str, key: str, value: str) -> None:
        resp = await request_with_retry(
            self._http_client.post,
            f"/api/v1/workspaces/{slug}/secrets",
            json={"key": key, "value": value},
        )
        _raise_for_status(resp)
