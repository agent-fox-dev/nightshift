"""Shared HTTP session configuration for HubClient.

Stub — implementation pending (spec 01, group 12).
"""

from __future__ import annotations

import httpx

#: Default per-request timeout used by HubClient.
DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=5.0)
