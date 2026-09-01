"""Shared HTTP session configuration and retry logic for HubClient.

Implements 01-REQ-9: transient error retry with exponential backoff.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from afhub.errors import HubConnectionError

#: Default per-request timeout used by HubClient.
DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=5.0)

#: httpx exceptions that trigger a retry (specific named subclasses only).
#: Base TimeoutException, WriteTimeout, PoolTimeout, and all other httpx
#: exceptions propagate immediately without retry.
_RETRYABLE_EXCEPTIONS: tuple[type[Exception], ...] = (
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.ConnectError,
)

#: Maximum number of retries after the initial attempt.
_MAX_RETRIES: int = 3

#: Base backoff interval in seconds.
_BACKOFF_BASE: float = 1.0

#: Multiplicative backoff factor (delay = base * factor^attempt).
_BACKOFF_FACTOR: int = 2

#: Maximum backoff interval in seconds per retry wait.
_BACKOFF_CAP: float = 30.0


async def request_with_retry(
    fn: Any,
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Execute an async HTTP request with retry on transient network errors.

    Calls ``fn(*args, **kwargs)`` and retries up to ``_MAX_RETRIES`` times
    when the call raises one of the whitelisted transient exceptions
    (``ConnectTimeout``, ``ReadTimeout``, ``ConnectError``).

    Backoff between retries follows an exponential schedule:
    ``min(base * factor^attempt, cap)`` -- yielding delays of 1 s, 2 s, 4 s
    for the default configuration.

    All other exceptions (including ``WriteTimeout``, ``PoolTimeout``, and
    the base ``TimeoutException``) propagate immediately without any retry
    or sleep.

    Raises :class:`~afhub.errors.HubConnectionError` after all retry
    attempts are exhausted.
    """
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES + 1):  # 0..3 -> 4 total attempts
        try:
            return await fn(*args, **kwargs)
        except _RETRYABLE_EXCEPTIONS as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES:
                delay = min(
                    _BACKOFF_BASE * (_BACKOFF_FACTOR**attempt),
                    _BACKOFF_CAP,
                )
                await asyncio.sleep(delay)
    # All retries exhausted -- raise HubConnectionError.
    raise HubConnectionError(
        status_code=0,
        message=str(last_exc),
    )
