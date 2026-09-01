"""Polling helpers for async hub operations.

Implements 01-REQ-8: poll_rebuild and poll_clone_ready wait for
asynchronous hub operations to reach a terminal state, sleeping
``interval`` seconds between polls and raising ``TimeoutError`` if
the deadline elapses.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from afhub.errors import HubError

if TYPE_CHECKING:
    from afhub.client import HubClient
    from afhub.models import RebuildJob, Workspace

_TERMINAL_REBUILD_STATUSES = frozenset(
    {"completed", "failed", "dead_letter", "cancelled"}
)


async def poll_rebuild(
    client: HubClient,
    slug: str,
    rebuild_id: str,
    *,
    timeout: float = 600.0,
    interval: float = 5.0,
) -> RebuildJob:
    """Poll *get_rebuild* until the job reaches a terminal status.

    The first call to ``client.get_rebuild`` is made immediately (no
    preceding sleep).  After each non-terminal response the helper
    sleeps *interval* seconds before retrying.

    Terminal statuses: ``completed``, ``failed``, ``dead_letter``,
    ``cancelled``.

    Raises:
        TimeoutError: If *timeout* seconds elapse before a terminal
            status is observed.  Timeout is checked after each poll
            and before each sleep (01-REQ-8.E6).
        HubConnectionError: Propagated immediately from
            ``get_rebuild`` without further retries (01-REQ-8.E3).
    """
    start = time.monotonic()
    while True:
        job = await client.get_rebuild(slug, rebuild_id)
        if job.status in _TERMINAL_REBUILD_STATUSES:
            return job
        if time.monotonic() - start >= timeout:
            raise TimeoutError(
                f"poll_rebuild timed out after {timeout}s waiting for "
                f"rebuild {rebuild_id!r} in workspace {slug!r}"
            )
        await asyncio.sleep(interval)


async def poll_clone_ready(
    client: HubClient,
    slug: str,
    *,
    timeout: float = 300.0,
    interval: float = 5.0,
) -> Workspace:
    """Poll *get_workspace* until *clone_status* is ``'ready'``.

    Sleeps *interval* seconds between polls.

    Raises:
        HubError: If *clone_status* is ``'failed'``, with
            ``status_code=0``, ``error_type='clone_failed'``, and
            ``message`` set to ``workspace.clone_error`` (or
            ``'Workspace clone failed'`` if absent).
        TimeoutError: If *timeout* seconds elapse before clone_status
            reaches ``'ready'`` or ``'failed'`` (01-REQ-8.E2).
        HubConnectionError: Propagated immediately from
            ``get_workspace`` without further retries (01-REQ-8.E4).
    """
    start = time.monotonic()
    while True:
        ws = await client.get_workspace(slug)
        if ws.clone_status == "ready":
            return ws
        if ws.clone_status == "failed":
            message = (
                ws.clone_error
                if ws.clone_error
                else "Workspace clone failed"
            )
            raise HubError(
                status_code=0,
                error_type="clone_failed",
                message=message,
            )
        if time.monotonic() - start >= timeout:
            raise TimeoutError(
                f"poll_clone_ready timed out after {timeout}s waiting for "
                f"workspace {slug!r} clone to become ready"
            )
        await asyncio.sleep(interval)
