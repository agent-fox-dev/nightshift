"""Tests for cache_policy advisory startup warning.

Issue #40: cache_policy is validated, cascaded, and then ignored by the
claude backend.

TS-NS-1: Startup warning emitted when cache_policy is non-default and
         the active backend cannot honour it.
TS-NS-2: No startup warning emitted when cache_policy is the default value.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest


def _make_config(
    cache_policy: str = "DEFAULT",
    backend_provider: str = "claude",
) -> SimpleNamespace:
    """Return a minimal config-like object with caching and backend attributes."""
    from afcore.core.config import CachePolicy

    policy = CachePolicy(cache_policy)
    return SimpleNamespace(
        caching=SimpleNamespace(cache_policy=policy),
        backend=SimpleNamespace(provider=backend_provider),
    )


class TestCheckCachePolicyAdvisory:
    """Verify the daemon pre-flight warning for advisory cache_policy."""

    def test_extended_on_claude_emits_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """EXTENDED + claude backend emits exactly one WARNING (TS-NS-1)."""
        from nightshift._startup import check_cache_policy_advisory

        config = _make_config("EXTENDED", "claude")

        with caplog.at_level(logging.WARNING, logger="nightshift._startup"):
            check_cache_policy_advisory(config)

        warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING and "cache_policy" in r.message]
        assert len(warning_records) == 1
        msg = warning_records[0].message
        assert "EXTENDED" in msg
        assert "claude" in msg
        assert "advisory" in msg

    def test_none_on_claude_emits_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """NONE + claude backend emits exactly one WARNING (TS-NS-1)."""
        from nightshift._startup import check_cache_policy_advisory

        config = _make_config("NONE", "claude")

        with caplog.at_level(logging.WARNING, logger="nightshift._startup"):
            check_cache_policy_advisory(config)

        warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING and "cache_policy" in r.message]
        assert len(warning_records) == 1
        assert "NONE" in warning_records[0].message

    def test_default_on_claude_no_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """DEFAULT + claude backend emits no warning (TS-NS-2)."""
        from nightshift._startup import check_cache_policy_advisory

        config = _make_config("DEFAULT", "claude")

        with caplog.at_level(logging.WARNING, logger="nightshift._startup"):
            check_cache_policy_advisory(config)

        warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING and "cache_policy" in r.message]
        assert len(warning_records) == 0

    def test_extended_on_non_claude_no_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """EXTENDED on a non-claude backend emits no warning."""
        from nightshift._startup import check_cache_policy_advisory

        config = _make_config("EXTENDED", "google")

        with caplog.at_level(logging.WARNING, logger="nightshift._startup"):
            check_cache_policy_advisory(config)

        warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING and "cache_policy" in r.message]
        assert len(warning_records) == 0

    def test_missing_caching_config_no_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """Config without caching section doesn't crash or warn."""
        from nightshift._startup import check_cache_policy_advisory

        config = SimpleNamespace(
            backend=SimpleNamespace(provider="claude"),
        )

        with caplog.at_level(logging.WARNING, logger="nightshift._startup"):
            check_cache_policy_advisory(config)

        warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING and "cache_policy" in r.message]
        assert len(warning_records) == 0
