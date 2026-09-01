"""Polling helpers for async hub operations.

Stub — implementation pending (spec 01, group 15).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from afhub.client import HubClient
    from afhub.models import RebuildJob, Workspace


async def poll_rebuild(
    client: HubClient,
    slug: str,
    job_id: str,
    *,
    poll_interval: float = 5.0,
    timeout: float = 300.0,
) -> RebuildJob:
    """Poll get_rebuild until the job reaches a terminal status.

    Stub — raises NotImplementedError until implemented.
    """
    raise NotImplementedError


async def poll_clone_ready(
    client: HubClient,
    slug: str,
    *,
    poll_interval: float = 5.0,
    timeout: float = 300.0,
) -> Workspace:
    """Poll get_workspace until clone_status is 'ready'.

    Stub — raises NotImplementedError until implemented.
    """
    raise NotImplementedError
