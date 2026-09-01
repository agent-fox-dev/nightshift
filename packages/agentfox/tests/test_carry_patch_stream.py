"""Tests for carry-patch stream registration, daemon labels, and engine method.

All tests in this file are *intentionally failing* pending the implementation
in task group 9.  They are collected by pytest without import errors but fail
because:
- ``build_streams()`` does not yet accept a ``hub_client`` parameter or
  register a carry-patch stream.
- ``_STREAM_DISPLAY_NAMES`` / ``_STREAM_ACTIVE_LABELS`` do not yet contain
  the ``carry-patch`` key.
- ``NightShiftEngine`` does not yet have ``_run_carry_patch_monitor()``.

Dependencies and forward stubs
-------------------------------
- ``afhub`` package (Spec 01) is not yet available; HubClient is mocked.
- ``agentfox.core.config.CarryPatchConfig`` (Spec 02) may not yet expose all
  fields; config is built with ``MagicMock`` to avoid import errors.
- ``CarryPatchMonitor`` (group 5) is a stub with ``run_cycle()`` raising
  ``NotImplementedError``; tests that reach ``run_cycle()`` will fail on that.

Specification: 03_carry_patch_pipeline_monitor
Requirements: 03-REQ-7
Test IDs: TS-03-19, TS-03-20, TS-03-21, TS-03-22
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from agentfox.nightshift.carry_patch_monitor import (
    CarryPatchMonitor,
    MonitorCycleResult,
)
from agentfox.nightshift.daemon import _STREAM_ACTIVE_LABELS, _STREAM_DISPLAY_NAMES
from agentfox.nightshift.streams import build_streams

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(
    *,
    carry_patch_enabled: bool = True,
    check_interval: int = 60,
    auto_resolve: bool = True,
    max_resolve_retries: int = 3,
    rebuild_timeout: int = 600,
    rebuild_poll_interval: int = 5,
) -> MagicMock:
    """Build a MagicMock config with carry_patch fields set."""
    config = MagicMock()
    config.carry_patch = MagicMock()
    config.carry_patch.enabled = carry_patch_enabled
    config.carry_patch.check_interval = check_interval
    config.carry_patch.auto_resolve = auto_resolve
    config.carry_patch.max_resolve_retries = max_resolve_retries
    config.carry_patch.rebuild_timeout = rebuild_timeout
    config.carry_patch.rebuild_poll_interval = rebuild_poll_interval
    config.carry_patch.hub_git_remote = "hub"
    # Platform and night_shift fields used by build_streams
    config.platform = MagicMock()
    config.platform.type = "github"
    config.night_shift = MagicMock()
    config.night_shift.issue_check_interval = 900
    config.night_shift.pr_check_interval = 900
    config.workspace = MagicMock()
    config.workspace.merge_strategy = "direct"
    return config


def _make_hub_client() -> MagicMock:
    """Build a mock HubClient."""
    client = MagicMock()
    client.get_patch_status = AsyncMock()
    client.list_rerere = AsyncMock(return_value=[])
    client.submit_rebuild = AsyncMock()
    client.list_rebuilds = AsyncMock(return_value=[])
    client.add_patch = AsyncMock()
    return client


# ---------------------------------------------------------------------------
# 3.4 — TS-03-19: build_streams registers carry-patch when enabled
# ---------------------------------------------------------------------------


class TestBuildStreamsCarryPatchEnabled:
    """TS-03-19: carry-patch stream present when enabled + HubClient available.

    Requirements: 03-REQ-7.1
    Test ID: TS-03-19
    """

    def test_carry_patch_stream_in_list_when_enabled(self) -> None:
        """build_streams includes carry-patch stream when carry_patch.enabled=True.

        Requirements: 03-REQ-7.1
        Test ID: TS-03-19
        Fails: build_streams does not accept hub_client param (group 9 pending)
        """
        config = _make_config(carry_patch_enabled=True, check_interval=60)
        hub_client = _make_hub_client()

        # FAILS: build_streams does not yet accept hub_client keyword
        streams = build_streams(config, hub_client=hub_client)

        carry_patch_streams = [s for s in streams if s.name == "carry-patch"]
        assert len(carry_patch_streams) == 1, "Exactly one carry-patch stream must be registered when enabled"

    def test_carry_patch_stream_has_correct_name(self) -> None:
        """carry-patch stream has name='carry-patch'.

        Requirements: 03-REQ-7.1
        Test ID: TS-03-19
        Fails: build_streams does not accept hub_client param (group 9 pending)
        """
        config = _make_config(carry_patch_enabled=True)
        hub_client = _make_hub_client()

        # FAILS: hub_client param not yet accepted
        streams = build_streams(config, hub_client=hub_client)

        carry_patch_streams = [s for s in streams if s.name == "carry-patch"]
        assert len(carry_patch_streams) == 1
        assert carry_patch_streams[0].name == "carry-patch"

    def test_carry_patch_stream_has_correct_interval(self) -> None:
        """carry-patch stream interval matches config.carry_patch.check_interval.

        Requirements: 03-REQ-7.1
        Test ID: TS-03-19
        Fails: build_streams does not accept hub_client param (group 9 pending)
        """
        config = _make_config(carry_patch_enabled=True, check_interval=120)
        hub_client = _make_hub_client()

        # FAILS: hub_client param not yet accepted
        streams = build_streams(config, hub_client=hub_client)

        carry_patch_streams = [s for s in streams if s.name == "carry-patch"]
        assert len(carry_patch_streams) == 1
        stream = carry_patch_streams[0]
        assert stream.interval == 120, f"carry-patch stream interval must be 120, got {stream.interval}"

    def test_carry_patch_stream_is_enabled(self) -> None:
        """carry-patch stream has enabled=True when carry_patch.enabled=True.

        Requirements: 03-REQ-7.1
        Test ID: TS-03-19
        Fails: build_streams does not accept hub_client param (group 9 pending)
        """
        config = _make_config(carry_patch_enabled=True)
        hub_client = _make_hub_client()

        # FAILS: hub_client param not yet accepted
        streams = build_streams(config, hub_client=hub_client)

        carry_patch_streams = [s for s in streams if s.name == "carry-patch"]
        assert len(carry_patch_streams) == 1
        assert carry_patch_streams[0].enabled is True


# ---------------------------------------------------------------------------
# 3.4 — TS-03-20: build_streams omits carry-patch when disabled / no hub
# ---------------------------------------------------------------------------


class TestBuildStreamsCarryPatchDisabled:
    """TS-03-20: carry-patch stream absent when disabled or no HubClient.

    Requirements: 03-REQ-7.2
    Test ID: TS-03-20
    """

    def test_no_carry_patch_stream_when_disabled(self) -> None:
        """build_streams omits carry-patch stream when carry_patch.enabled=False.

        Requirements: 03-REQ-7.2
        Test ID: TS-03-20
        Fails: build_streams does not accept hub_client param (group 9 pending)
        """
        config = _make_config(carry_patch_enabled=False)
        hub_client = _make_hub_client()

        # FAILS: hub_client param not yet accepted
        streams = build_streams(config, hub_client=hub_client)

        carry_patch_streams = [s for s in streams if s.name == "carry-patch"]
        assert len(carry_patch_streams) == 0, "carry-patch stream must NOT be registered when carry_patch.enabled=False"

    def test_no_carry_patch_stream_when_hub_client_none(self) -> None:
        """build_streams omits carry-patch stream when hub_client=None.

        Requirements: 03-REQ-7.2
        Test ID: TS-03-20
        Fails: build_streams does not accept hub_client param (group 9 pending)
        """
        config = _make_config(carry_patch_enabled=True)

        # FAILS: hub_client param not yet accepted
        streams = build_streams(config, hub_client=None)

        carry_patch_streams = [s for s in streams if s.name == "carry-patch"]
        assert len(carry_patch_streams) == 0, "carry-patch stream must NOT be registered when hub_client=None"

    def test_no_carry_patch_stream_when_disabled_and_no_hub(self) -> None:
        """build_streams omits carry-patch when both disabled and no hub_client.

        Requirements: 03-REQ-7.2
        Test ID: TS-03-20
        Fails: build_streams does not accept hub_client param (group 9 pending)
        """
        config = _make_config(carry_patch_enabled=False)

        # FAILS: hub_client param not yet accepted
        streams = build_streams(config, hub_client=None)

        carry_patch_streams = [s for s in streams if s.name == "carry-patch"]
        assert len(carry_patch_streams) == 0


# ---------------------------------------------------------------------------
# 3.4 — TS-03-19 edge case: invalid check_interval (03-REQ-7.E1)
# ---------------------------------------------------------------------------


class TestBuildStreamsInvalidCheckInterval:
    """TS-03-19: ValueError raised for zero or negative check_interval.

    Requirements: 03-REQ-7.E1
    Test ID: TS-03-19
    """

    def test_zero_check_interval_raises_value_error(self) -> None:
        """build_streams raises ValueError when check_interval is 0.

        Requirements: 03-REQ-7.E1
        Test ID: TS-03-19
        Fails: build_streams does not accept hub_client param (group 9 pending)
        """
        config = _make_config(carry_patch_enabled=True, check_interval=0)
        hub_client = _make_hub_client()

        with pytest.raises(ValueError, match="check_interval"):
            build_streams(config, hub_client=hub_client)

    def test_negative_check_interval_raises_value_error(self) -> None:
        """build_streams raises ValueError when check_interval is negative.

        Requirements: 03-REQ-7.E1
        Test ID: TS-03-19
        Fails: build_streams does not accept hub_client param (group 9 pending)
        """
        config = _make_config(carry_patch_enabled=True, check_interval=-10)
        hub_client = _make_hub_client()

        with pytest.raises(ValueError, match="check_interval"):
            build_streams(config, hub_client=hub_client)


# ---------------------------------------------------------------------------
# 3.5 — TS-03-21: Daemon display names and active labels
# ---------------------------------------------------------------------------


class TestDaemonDisplayRegistry:
    """TS-03-21: carry-patch key in _STREAM_DISPLAY_NAMES and _STREAM_ACTIVE_LABELS.

    Requirements: 03-REQ-7.3
    Test ID: TS-03-21
    """

    def test_carry_patch_in_display_names(self) -> None:
        """'carry-patch' is a key in _STREAM_DISPLAY_NAMES.

        Requirements: 03-REQ-7.3
        Test ID: TS-03-21
        Fails: 'carry-patch' not yet added to display names (group 9 pending)
        """
        assert "carry-patch" in _STREAM_DISPLAY_NAMES, "'carry-patch' must be in _STREAM_DISPLAY_NAMES dict"

    def test_carry_patch_display_name_non_empty(self) -> None:
        """'carry-patch' display name is a non-empty string.

        Requirements: 03-REQ-7.3
        Test ID: TS-03-21
        Fails: 'carry-patch' not yet added to display names (group 9 pending)
        """
        assert "carry-patch" in _STREAM_DISPLAY_NAMES
        display_name = _STREAM_DISPLAY_NAMES["carry-patch"]
        assert isinstance(display_name, str)
        assert len(display_name) > 0, "'carry-patch' display name must be a non-empty string"

    def test_carry_patch_in_active_labels(self) -> None:
        """'carry-patch' is a key in _STREAM_ACTIVE_LABELS.

        Requirements: 03-REQ-7.3
        Test ID: TS-03-21
        Fails: 'carry-patch' not yet added to active labels (group 9 pending)
        """
        assert "carry-patch" in _STREAM_ACTIVE_LABELS, "'carry-patch' must be in _STREAM_ACTIVE_LABELS dict"

    def test_carry_patch_active_label_non_empty(self) -> None:
        """'carry-patch' active label is a non-empty string.

        Requirements: 03-REQ-7.3
        Test ID: TS-03-21
        Fails: 'carry-patch' not yet added to active labels (group 9 pending)
        """
        assert "carry-patch" in _STREAM_ACTIVE_LABELS
        label = _STREAM_ACTIVE_LABELS["carry-patch"]
        assert isinstance(label, str)
        assert len(label) > 0, "'carry-patch' active label must be a non-empty string"


# ---------------------------------------------------------------------------
# 3.5 — TS-03-22: NightShiftEngine._run_carry_patch_monitor delegation
# ---------------------------------------------------------------------------


class TestEngineRunCarryPatchMonitor:
    """TS-03-22: _run_carry_patch_monitor delegates to CarryPatchMonitor.run_cycle.

    Requirements: 03-REQ-7.4
    Test ID: TS-03-22
    """

    async def test_engine_has_run_carry_patch_monitor_method(self) -> None:
        """NightShiftEngine has _run_carry_patch_monitor method.

        Requirements: 03-REQ-7.4
        Test ID: TS-03-22
        Fails: method not yet added to engine (group 9 pending)
        """
        from agentfox.nightshift.engine import NightShiftEngine  # noqa: PLC0415

        assert hasattr(NightShiftEngine, "_run_carry_patch_monitor"), (
            "NightShiftEngine must have _run_carry_patch_monitor method"
        )

    async def test_run_carry_patch_monitor_delegates_to_monitor(self) -> None:
        """_run_carry_patch_monitor delegates to CarryPatchMonitor.run_cycle.

        Requirements: 03-REQ-7.4
        Test ID: TS-03-22
        Fails: method not yet added to engine (group 9 pending)
        """
        from agentfox.nightshift.engine import NightShiftEngine  # noqa: PLC0415

        # Build a minimal engine (mocked to avoid heavy __init__ deps)
        engine = MagicMock(spec=NightShiftEngine)

        # Create a mock monitor whose run_cycle returns a known result
        mock_monitor = MagicMock(spec=CarryPatchMonitor)
        mock_result = MagicMock(spec=MonitorCycleResult)
        mock_result.conflicts_detected = 1
        mock_result.conflicts_resolved = 1
        mock_result.conflicts_failed = 0
        mock_result.patches_merged = 0
        mock_result.rebuild_triggered = True
        mock_monitor.run_cycle = AsyncMock(return_value=mock_result)
        engine._carry_patch_monitor = mock_monitor

        # Call the real method (not the mock method)
        result = await NightShiftEngine._run_carry_patch_monitor(engine, slug="ws-1")

        # After implementation:
        assert result is mock_result, (
            "_run_carry_patch_monitor must return the MonitorCycleResult from CarryPatchMonitor.run_cycle()"
        )
        mock_monitor.run_cycle.assert_called_once()

    async def test_run_carry_patch_monitor_reuses_same_monitor_instance(
        self,
    ) -> None:
        """Same CarryPatchMonitor instance is reused across calls.

        Requirements: 03-REQ-7.4 (reuse to preserve retry counter)
        Test ID: TS-03-22
        Fails: method not yet added to engine (group 9 pending)
        """
        from agentfox.nightshift.engine import NightShiftEngine  # noqa: PLC0415

        engine = MagicMock(spec=NightShiftEngine)
        mock_monitor = MagicMock(spec=CarryPatchMonitor)
        mock_monitor.run_cycle = AsyncMock(return_value=MagicMock(spec=MonitorCycleResult))
        engine._carry_patch_monitor = mock_monitor

        # Call twice
        await NightShiftEngine._run_carry_patch_monitor(engine, slug="ws-1")
        await NightShiftEngine._run_carry_patch_monitor(engine, slug="ws-1")

        # Same monitor must be used both times — 2 calls total
        assert mock_monitor.run_cycle.call_count == 2, (
            "CarryPatchMonitor.run_cycle() must be called on the same instance (reuse), not a fresh instance per call"
        )

    async def test_run_carry_patch_monitor_propagates_exception(self) -> None:
        """Exceptions from CarryPatchMonitor propagate to caller (03-REQ-7.E2).

        Requirements: 03-REQ-7.E2
        Test ID: TS-03-22
        Fails: method not yet added to engine (group 9 pending)
        """
        from agentfox.nightshift.engine import NightShiftEngine  # noqa: PLC0415

        engine = MagicMock(spec=NightShiftEngine)
        mock_monitor = MagicMock(spec=CarryPatchMonitor)
        mock_monitor.run_cycle = AsyncMock(side_effect=RuntimeError("unexpected monitor failure"))
        engine._carry_patch_monitor = mock_monitor

        with pytest.raises(RuntimeError, match="unexpected monitor failure"):
            await NightShiftEngine._run_carry_patch_monitor(engine, slug="ws-1")
