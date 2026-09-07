"""Unit tests for issue #39: _drain_issues must not report platform outage as empty queue.

Test Spec: TS-NS-1 through TS-NS-5
Requirements: NS-REQ-1, NS-REQ-2, NS-REQ-3, NS-REQ-4, NS-REQ-5
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_engine(
    *,
    platform_raises: bool = False,
    issues: list | None = None,
    raise_on_repoll: bool = False,
):
    """Build a NightShiftEngine with a mock platform.

    Args:
        platform_raises: If True, list_issues_by_label always raises.
        issues: Issues to return from list_issues_by_label (initial poll).
        raise_on_repoll: If True, first call succeeds (returns *issues*),
                         second call (re-poll) raises.
    """
    from afcore.nightshift.engine import NightShiftEngine

    config = MagicMock()
    config.platform.type = "github"
    config.orchestrator.max_cost = None
    config.orchestrator.max_sessions = None
    config.night_shift.max_parallel = 1
    config.gate = None

    platform = MagicMock()

    if platform_raises:
        platform.list_issues_by_label = AsyncMock(
            side_effect=RuntimeError("forge unavailable"),
        )
    elif raise_on_repoll:
        # First call returns issues, second call raises
        call_count = 0

        async def _list_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return issues or []
            raise RuntimeError("forge unavailable on re-poll")

        platform.list_issues_by_label = AsyncMock(side_effect=_list_side_effect)
    else:
        platform.list_issues_by_label = AsyncMock(return_value=issues or [])

    engine = NightShiftEngine(config, platform)
    return engine


def _make_issue(number: int = 1, title: str = "test issue"):
    """Build a minimal IssueResult-like mock."""
    from afissues.protocol import IssueResult

    return IssueResult(
        number=number,
        title=title,
        html_url=f"https://github.com/test/repo/issues/{number}",
        labels=["af:fix"],
    )


# ---------------------------------------------------------------------------
# TS-NS-1: Platform exception during drain re-poll must not return True
# Requirement: NS-REQ-1
# ---------------------------------------------------------------------------


class TestDrainRepollPlatformError:
    """Verify _drain_issues does not return True when the platform raises."""

    @pytest.mark.asyncio
    async def test_drain_raises_on_repoll_error(self) -> None:
        """When list_issues_by_label raises on the re-poll,
        _drain_issues must propagate the exception (not return True)."""
        issue = _make_issue(1)
        engine = _make_engine(raise_on_repoll=True, issues=[issue])

        # Patch _run_issue_check to be a no-op so we isolate re-poll behavior
        with patch.object(engine, "_run_issue_check", new_callable=AsyncMock):
            with pytest.raises(RuntimeError, match="forge unavailable on re-poll"):
                await engine._drain_issues()

    @pytest.mark.asyncio
    async def test_drain_raises_on_initial_poll_error(self) -> None:
        """When list_issues_by_label raises in _run_issue_check (initial poll),
        _drain_issues propagates instead of silently succeeding."""
        engine = _make_engine(platform_raises=True)

        with pytest.raises(RuntimeError, match="forge unavailable"):
            await engine._drain_issues()

    @pytest.mark.asyncio
    async def test_drain_never_returns_true_on_error(self) -> None:
        """Exhaustive check: _drain_issues never returns True when platform is down."""
        engine = _make_engine(platform_raises=True)

        # Should raise, but we also verify it doesn't return True
        try:
            result = await engine._drain_issues()
            # If it somehow doesn't raise, it must NOT be True
            assert result is not True, "_drain_issues returned True despite platform error"
        except RuntimeError:
            pass  # Expected: propagation is the correct behavior


# ---------------------------------------------------------------------------
# TS-NS-2: _run_issue_check must not increment issue_checks_completed on failure
# Requirement: NS-REQ-2
# ---------------------------------------------------------------------------


class TestIssueCheckCounterOnFailure:
    """Verify issue_checks_completed is unchanged when platform poll fails."""

    @pytest.mark.asyncio
    async def test_counter_unchanged_on_platform_error(self) -> None:
        """issue_checks_completed must not change when list_issues_by_label raises."""
        engine = _make_engine(platform_raises=True)
        before = engine.state.issue_checks_completed

        with pytest.raises(RuntimeError):
            await engine._run_issue_check()

        assert engine.state.issue_checks_completed == before, (
            "issue_checks_completed was incremented despite platform error"
        )

    @pytest.mark.asyncio
    async def test_counter_increments_on_success(self) -> None:
        """issue_checks_completed increments normally when platform returns empty."""
        engine = _make_engine(issues=[])
        before = engine.state.issue_checks_completed

        await engine._run_issue_check()

        assert engine.state.issue_checks_completed == before + 1


# ---------------------------------------------------------------------------
# TS-NS-3: Platform failure during drain must produce ERROR-level log
# Requirement: NS-REQ-3
# ---------------------------------------------------------------------------


class TestDrainErrorLogging:
    """Verify platform failure is logged at ERROR, not just WARNING."""

    @pytest.mark.asyncio
    async def test_error_logged_when_drain_propagates(self, caplog) -> None:
        """When _drain_issues raises, _run_stream_loop logs at ERROR level.

        We simulate this by calling run_once on the EngineWorkStream and
        verifying that the exception propagates (which _run_stream_loop
        catches with logger.exception, producing an ERROR log).
        """
        from afcore.nightshift.daemon import SharedBudget
        from afcore.nightshift.streams import EngineWorkStream

        engine = _make_engine(platform_raises=True)
        budget = SharedBudget(max_cost=None)
        stream = EngineWorkStream(
            stream_name="fix-pipeline",
            engine=engine,
            method_name="_drain_issues",
            budget=budget,
            enabled=True,
            interval=900,
        )

        # The stream must re-raise the exception so _run_stream_loop
        # can log it at ERROR via logger.exception
        with pytest.raises(RuntimeError, match="forge unavailable"):
            await stream.run_once()

    @pytest.mark.asyncio
    async def test_no_false_success_on_error(self, caplog) -> None:
        """No 'drain succeeded' message when platform is down."""
        engine = _make_engine(platform_raises=True)

        with caplog.at_level(logging.DEBUG):
            with pytest.raises(RuntimeError):
                await engine._drain_issues()

        # Should NOT log any message implying drain succeeded
        for record in caplog.records:
            assert "drain succeeded" not in record.message.lower()


# ---------------------------------------------------------------------------
# TS-NS-4: Consecutive forge failures produce backed-off retry interval
# Requirement: NS-REQ-4
# ---------------------------------------------------------------------------


class TestStreamBackoff:
    """Verify EngineWorkStream backs off on consecutive failures."""

    @pytest.mark.asyncio
    async def test_interval_increases_on_failure(self) -> None:
        """After a failure, interval is greater than the base interval."""
        from afcore.nightshift.streams import EngineWorkStream

        mock_engine = MagicMock()
        mock_engine._drain_issues = AsyncMock(
            side_effect=RuntimeError("forge down"),
        )

        stream = EngineWorkStream(
            stream_name="fix-pipeline",
            engine=mock_engine,
            method_name="_drain_issues",
            enabled=True,
            interval=900,
        )

        base_interval = stream.interval
        assert base_interval == 900

        # First failure
        with pytest.raises(RuntimeError):
            await stream.run_once()

        assert stream.interval > base_interval
        assert stream.consecutive_failures == 1

    @pytest.mark.asyncio
    async def test_consecutive_failures_increase_backoff(self) -> None:
        """Each consecutive failure doubles the interval (up to cap)."""
        from afcore.nightshift.streams import EngineWorkStream

        mock_engine = MagicMock()
        mock_engine._drain_issues = AsyncMock(
            side_effect=RuntimeError("forge down"),
        )

        stream = EngineWorkStream(
            stream_name="fix-pipeline",
            engine=mock_engine,
            method_name="_drain_issues",
            enabled=True,
            interval=100,
        )

        intervals = []
        for _ in range(5):
            with pytest.raises(RuntimeError):
                await stream.run_once()
            intervals.append(stream.interval)

        # Each interval should be >= previous (monotonically increasing up to cap)
        for i in range(1, len(intervals)):
            assert intervals[i] >= intervals[i - 1]

        # After 5 failures: multipliers are 2, 4, 8, 8, 8 (cap at 8)
        assert intervals[0] == 200  # 2^1 * 100
        assert intervals[1] == 400  # 2^2 * 100
        assert intervals[2] == 800  # 2^3 * 100 = cap
        assert intervals[3] == 800  # capped
        assert intervals[4] == 800  # capped

    @pytest.mark.asyncio
    async def test_backoff_bounded(self) -> None:
        """Backoff interval does not exceed _MAX_BACKOFF_MULTIPLIER * base."""
        from afcore.nightshift.streams import EngineWorkStream

        mock_engine = MagicMock()
        mock_engine._drain_issues = AsyncMock(
            side_effect=RuntimeError("forge down"),
        )

        stream = EngineWorkStream(
            stream_name="fix-pipeline",
            engine=mock_engine,
            method_name="_drain_issues",
            enabled=True,
            interval=900,
        )

        max_expected = 900 * EngineWorkStream._MAX_BACKOFF_MULTIPLIER

        for _ in range(20):
            with pytest.raises(RuntimeError):
                await stream.run_once()

        assert stream.interval <= max_expected
        assert stream.interval == max_expected

    @pytest.mark.asyncio
    async def test_backoff_resets_on_success(self) -> None:
        """After a successful run, interval resets to base."""
        from afcore.nightshift.streams import EngineWorkStream

        call_count = 0

        async def _side_effect():
            nonlocal call_count
            call_count += 1
            if call_count <= 3:
                raise RuntimeError("forge down")
            # 4th call succeeds

        mock_engine = MagicMock()
        mock_engine._drain_issues = AsyncMock(side_effect=_side_effect)

        stream = EngineWorkStream(
            stream_name="fix-pipeline",
            engine=mock_engine,
            method_name="_drain_issues",
            enabled=True,
            interval=100,
        )

        # Accumulate some failures
        for _ in range(3):
            with pytest.raises(RuntimeError):
                await stream.run_once()

        assert stream.interval > 100
        assert stream.consecutive_failures == 3

        # Successful run
        await stream.run_once()

        assert stream.interval == 100
        assert stream.consecutive_failures == 0


# ---------------------------------------------------------------------------
# TS-NS-5: _drain_issues must not proceed after failed _run_issue_check
# Requirement: NS-REQ-5
# ---------------------------------------------------------------------------


class TestDrainStopsOnIssueCheckFailure:
    """Verify _drain_issues does not advance to re-poll after _run_issue_check fails."""

    @pytest.mark.asyncio
    async def test_repoll_not_called_after_check_failure(self) -> None:
        """When _run_issue_check raises, _drain_issues must not call
        list_issues_by_label for the re-poll step."""
        engine = _make_engine(issues=[])

        # Make _run_issue_check raise
        with patch.object(
            engine,
            "_run_issue_check",
            new_callable=AsyncMock,
            side_effect=RuntimeError("poll failed"),
        ):
            with pytest.raises(RuntimeError, match="poll failed"):
                await engine._drain_issues()

        # list_issues_by_label should NOT have been called for re-poll
        # (it was only configured on the mock, not invoked since
        # _run_issue_check was patched to raise before we got there)
        engine._platform.list_issues_by_label.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_drain_does_not_return_true_on_check_failure(self) -> None:
        """_drain_issues must not return True when _run_issue_check fails."""
        engine = _make_engine(issues=[])

        with patch.object(
            engine,
            "_run_issue_check",
            new_callable=AsyncMock,
            side_effect=RuntimeError("poll failed"),
        ):
            try:
                result = await engine._drain_issues()
                assert result is not True
            except RuntimeError:
                pass  # Propagation is the expected/correct behavior
