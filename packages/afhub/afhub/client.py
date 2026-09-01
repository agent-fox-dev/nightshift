"""HubClient — async client for the af-hub carry-patch REST API.

Stub — implementation pending (spec 01, groups 13–14).
"""

from __future__ import annotations

from typing import Any


class HubClient:
    """Async client for the af-hub carry-patch REST API.

    Stub implementation: constructor and context-manager protocol are not
    implemented.  All method bodies raise ``NotImplementedError`` so tests
    written against this stub will fail correctly in group 1.
    """

    def __init__(self, endpoint_url: str, pat: str) -> None:
        raise NotImplementedError

    async def __aenter__(self) -> HubClient:
        raise NotImplementedError

    async def __aexit__(self, *args: Any) -> None:
        raise NotImplementedError

    async def aclose(self) -> None:
        raise NotImplementedError

    # -- Workspace operations ------------------------------------------------

    async def get_workspace(self, slug: str) -> Any:
        raise NotImplementedError

    async def sync_workspace(self, slug: str, *, reset_to_upstream: bool = False) -> Any:
        raise NotImplementedError

    async def get_patch_status(self, slug: str) -> Any:
        raise NotImplementedError

    async def reclone_workspace(self, slug: str) -> Any:
        raise NotImplementedError

    # -- Patch operations ----------------------------------------------------

    async def list_patches(self, slug: str) -> Any:
        raise NotImplementedError

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
    ) -> Any:
        raise NotImplementedError

    async def add_patches_batch(self, slug: str, patches: list[dict[str, Any]]) -> Any:
        raise NotImplementedError

    async def update_patch(self, slug: str, patch_id: str, **kwargs: Any) -> Any:
        raise NotImplementedError

    async def remove_patch(self, slug: str, patch_id: str) -> None:
        raise NotImplementedError

    async def restore_patch(self, slug: str, patch_id: str) -> Any:
        raise NotImplementedError

    async def reorder_patches(self, slug: str, ordered_ids: list[str]) -> Any:
        raise NotImplementedError

    # -- Rebuild operations --------------------------------------------------

    async def submit_rebuild(
        self,
        slug: str,
        *,
        strategy: str | None = None,
        fail_fast: bool = False,
    ) -> Any:
        raise NotImplementedError

    async def get_rebuild(self, slug: str, job_id: str) -> Any:
        raise NotImplementedError

    async def list_rebuilds(self, slug: str) -> Any:
        raise NotImplementedError

    async def cancel_rebuild(self, slug: str, job_id: str) -> Any:
        raise NotImplementedError

    async def requeue_rebuild(self, slug: str, job_id: str) -> Any:
        raise NotImplementedError

    async def rollback_rebuild(self, slug: str, job_id: str) -> Any:
        raise NotImplementedError

    async def get_rebuild_preview(self, slug: str) -> Any:
        raise NotImplementedError

    # -- Rerere operations ---------------------------------------------------

    async def list_rerere(self, slug: str) -> Any:
        raise NotImplementedError

    async def forget_rerere(self, slug: str, pathspec: str) -> None:
        raise NotImplementedError

    # -- Variable operations -------------------------------------------------

    async def create_variable(self, slug: str, key: str, value: str) -> Any:
        raise NotImplementedError

    async def update_variable(self, slug: str, key: str, value: str) -> Any:
        raise NotImplementedError

    async def set_variable(self, slug: str, key: str, value: str) -> Any:
        raise NotImplementedError

    async def delete_variable(self, slug: str, key: str) -> None:
        raise NotImplementedError

    async def get_resolved_variables(self, slug: str) -> Any:
        raise NotImplementedError

    # -- Secret operations ---------------------------------------------------

    async def create_secret(self, slug: str, key: str, value: str) -> None:
        raise NotImplementedError
