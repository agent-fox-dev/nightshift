"""Tests for root permission_mode pre-flight check in the nightshift daemon.

Issue #11: bypassPermissions fails when running as root.

TS-NS-2: Daemon startup emits a CRITICAL-level log and exits with a clear
error when permission_mode='bypassPermissions' is configured and the process
UID is 0.

Requirements: NS-REQ-2, NS-REQ-4
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import patch

import pytest


def _make_config(permission_mode: str = "bypassPermissions") -> SimpleNamespace:
    """Return a minimal config-like object with a security.permission_mode attribute."""
    return SimpleNamespace(
        security=SimpleNamespace(permission_mode=permission_mode),
    )


class TestCheckRootPermissionMode:
    """Verify the daemon pre-flight guard for root + bypassPermissions."""

    def test_root_bypass_exits(self) -> None:
        """UID 0 + bypassPermissions → sys.exit(1) with CRITICAL log (NS-REQ-2)."""
        from nightshift._startup import check_root_permission_mode

        config = _make_config("bypassPermissions")

        with (
            patch("nightshift._startup.os.getuid", return_value=0),
            pytest.raises(SystemExit) as exc_info,
        ):
            check_root_permission_mode(config)

        assert exc_info.value.code == 1

    def test_root_bypass_emits_critical_log(self, caplog) -> None:
        """UID 0 + bypassPermissions emits a CRITICAL log record (NS-REQ-2)."""
        from nightshift._startup import check_root_permission_mode

        config = _make_config("bypassPermissions")

        with (
            caplog.at_level(logging.CRITICAL, logger="nightshift._startup"),
            patch("nightshift._startup.os.getuid", return_value=0),
            pytest.raises(SystemExit),
        ):
            check_root_permission_mode(config)

        critical_records = [r for r in caplog.records if r.levelno >= logging.CRITICAL]
        assert len(critical_records) >= 1
        msg = critical_records[0].message
        assert "bypassPermissions" in msg
        assert "root" in msg.lower() or "UID 0" in msg

    def test_root_accept_edits_does_not_exit(self) -> None:
        """UID 0 + acceptEdits does not trigger exit (NS-REQ-1)."""
        from nightshift._startup import check_root_permission_mode

        config = _make_config("acceptEdits")

        with patch("nightshift._startup.os.getuid", return_value=0):
            # Must not raise
            check_root_permission_mode(config)

    def test_non_root_bypass_does_not_exit(self) -> None:
        """Non-root + bypassPermissions does not trigger exit (NS-REQ-4)."""
        from nightshift._startup import check_root_permission_mode

        config = _make_config("bypassPermissions")

        with patch("nightshift._startup.os.getuid", return_value=1000):
            # Must not raise
            check_root_permission_mode(config)

    def test_non_posix_does_not_exit(self) -> None:
        """On non-POSIX (no os.getuid), the check is a no-op."""
        from nightshift._startup import check_root_permission_mode

        config = _make_config("bypassPermissions")

        with patch("nightshift._startup.os.getuid", side_effect=AttributeError):
            # Must not raise
            check_root_permission_mode(config)
