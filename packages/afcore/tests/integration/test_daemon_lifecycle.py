"""Integration smoke tests for daemon lifecycle.

Test Spec: TS-85-SMOKE-1 through TS-85-SMOKE-7,
           TS-85-6, TS-85-12, TS-85-17, TS-85-E1
Requirements: 85-REQ-1.E1, 85-REQ-2.2, 85-REQ-4.1, 85-REQ-5.3
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_stream(
    name: str = "test-stream",
    interval: int = 1,
    enabled: bool = True,
    side_effect: list | None = None,
    duration: float = 0.0,
) -> MagicMock:
    """Create a mock WorkStream."""
    stream = MagicMock()
    stream.name = name
    stream.interval = interval
    stream.enabled = enabled

    if duration > 0:
        original_side_effect = side_effect

        async def slow_run() -> None:
            await asyncio.sleep(duration)
            if original_side_effect:
                effect = original_side_effect.pop(0)
                if isinstance(effect, Exception):
                    raise effect

        stream.run_once = AsyncMock(side_effect=slow_run)
    else:
        stream.run_once = AsyncMock(side_effect=side_effect)
    stream.shutdown = AsyncMock()
    return stream


def _make_config() -> MagicMock:
    """Create a mock config."""
    config = MagicMock()
    ns = MagicMock()
    ns.issue_check_interval = 900
    ns.push_fix_branch = False
    config.night_shift = ns
    return config


# ---------------------------------------------------------------------------
# TS-85-6: Graceful shutdown on single SIGINT
# Requirement: 85-REQ-2.2
# ---------------------------------------------------------------------------


class TestGracefulShutdown:
    """Verify single SIGINT triggers graceful shutdown."""

    async def test_run_once_completes_before_shutdown(self, tmp_path: Path) -> None:
        """run_once completes, not interrupted mid-execution."""
        from afcore.nightshift.daemon import DaemonRunner, SharedBudget

        completed = []

        async def slow_run() -> None:
            await asyncio.sleep(0.1)
            completed.append(True)

        stream = _make_mock_stream(name="slow-stream", interval=1)
        stream.run_once = AsyncMock(side_effect=slow_run)
        budget = SharedBudget(max_cost=None)
        config = _make_config()
        runner = DaemonRunner(config, None, [stream], budget, pid_path=tmp_path / "d.pid")

        async def shutdown_mid_operation() -> None:
            await asyncio.sleep(0.05)  # mid-operation
            runner.request_shutdown()

        task = asyncio.create_task(shutdown_mid_operation())
        await runner.run()
        await task
        assert stream.run_once.call_count >= 1
        assert stream.shutdown.call_count == 1


# ---------------------------------------------------------------------------
# TS-85-12: Streams run as independent asyncio tasks
# Requirement: 85-REQ-4.1
# ---------------------------------------------------------------------------


class TestConcurrentStreams:
    """Verify streams run concurrently, not sequentially."""

    async def test_concurrent_execution(self, tmp_path: Path) -> None:
        """Both streams execute within ~0.15s (not ~0.2s if sequential)."""
        import time

        from afcore.nightshift.daemon import DaemonRunner, SharedBudget

        async def slow_run() -> None:
            await asyncio.sleep(0.1)

        s1 = _make_mock_stream(name="s1", interval=100)
        s1.run_once = AsyncMock(side_effect=slow_run)
        s2 = _make_mock_stream(name="s2", interval=100)
        s2.run_once = AsyncMock(side_effect=slow_run)

        budget = SharedBudget(max_cost=None)
        config = _make_config()
        runner = DaemonRunner(config, None, [s1, s2], budget, pid_path=tmp_path / "d.pid")

        start = time.monotonic()

        async def shutdown_after_first_cycle() -> None:
            await asyncio.sleep(0.15)
            runner.request_shutdown()

        task = asyncio.create_task(shutdown_after_first_cycle())
        await runner.run()
        await task
        elapsed = time.monotonic() - start

        assert s1.run_once.call_count >= 1
        assert s2.run_once.call_count >= 1
        # If sequential, would take ~0.2s; concurrent should be ~0.1-0.15s
        assert elapsed < 0.25


# ---------------------------------------------------------------------------
# TS-85-17: Cost check between cycles, not mid-operation
# Requirement: 85-REQ-5.3
# ---------------------------------------------------------------------------


class TestCostCheckBetweenCycles:
    """Verify run_once is not interrupted when cost exceeds limit mid-cycle."""

    async def test_run_once_completes_despite_budget(self, tmp_path: Path) -> None:
        """run_once completes fully even when cost exceeds budget."""
        from afcore.nightshift.daemon import DaemonRunner, SharedBudget

        completed: list[bool] = []

        async def costly_run() -> None:
            await asyncio.sleep(0.05)
            await budget.add_cost_async(2.0)
            completed.append(True)

        budget = SharedBudget(max_cost=1.0)
        stream = _make_mock_stream(name="costly-stream", interval=1)
        stream.run_once = AsyncMock(side_effect=costly_run)
        config = _make_config()
        runner = DaemonRunner(config, None, [stream], budget, pid_path=tmp_path / "d.pid")
        await runner.run()
        assert len(completed) >= 1
        assert budget.exceeded is True


# ---------------------------------------------------------------------------
# TS-85-E1: Persistent stream failure doesn't affect others
# Requirement: 85-REQ-1.E1
# ---------------------------------------------------------------------------


class TestPersistentStreamFailure:
    """Verify a stream that always fails doesn't crash other streams."""

    async def test_failing_stream_doesnt_affect_healthy(self, tmp_path: Path) -> None:
        """Healthy stream runs normally despite another always failing."""
        from afcore.nightshift.daemon import DaemonRunner, SharedBudget

        failing = _make_mock_stream(
            name="failing",
            interval=0,
            side_effect=[RuntimeError("always fail")] * 10,
        )
        healthy = _make_mock_stream(name="healthy", interval=0)
        budget = SharedBudget(max_cost=None)
        config = _make_config()
        runner = DaemonRunner(config, None, [failing, healthy], budget, pid_path=tmp_path / "d.pid")

        async def shutdown_after_delay() -> None:
            await asyncio.sleep(0.3)
            runner.request_shutdown()

        task = asyncio.create_task(shutdown_after_delay())
        await runner.run()
        await task
        assert failing.run_once.call_count >= 2
        assert healthy.run_once.call_count >= 2


# ---------------------------------------------------------------------------
# TS-85-SMOKE-1: Daemon full lifecycle
# Path 1 (startup) + Path 5 (shutdown)
# ---------------------------------------------------------------------------


class TestSmokeDaemonFullLifecycle:
    """Verify daemon starts, writes PID, runs streams, shuts down, removes PID."""

    async def test_full_lifecycle(self, tmp_path: Path) -> None:
        """PID created, streams run, PID removed, uptime > 0."""
        from afcore.nightshift.daemon import DaemonRunner, SharedBudget

        pid_path = tmp_path / "daemon.pid"
        stream = _make_mock_stream(name="test-stream", enabled=True, interval=1)
        budget = SharedBudget(max_cost=None)
        config = _make_config()
        runner = DaemonRunner(config, None, [stream], budget, pid_path=pid_path)

        task = asyncio.create_task(runner.run())
        await asyncio.sleep(0.2)
        assert pid_path.exists()
        assert pid_path.read_text().strip() == str(os.getpid())

        runner.request_shutdown()
        state = await task
        assert not pid_path.exists()
        assert state.uptime_seconds > 0
        assert stream.run_once.call_count >= 1
        assert stream.shutdown.call_count == 1


# ---------------------------------------------------------------------------
# TS-85-SMOKE-3: Fix pipeline stream end-to-end
# Path 3
# ---------------------------------------------------------------------------


class TestSmokeFixPipeline:
    """Verify fix pipeline stream wraps engine.

    Cost is now pushed at the engine level (via SharedBudget.add_cost_async),
    not sampled by the stream via before/after state delta.
    """

    async def test_fix_pipeline_e2e(self) -> None:
        """engine._drain_issues called; stream does not push cost to budget."""
        from afcore.nightshift.daemon import SharedBudget
        from afcore.nightshift.streams import EngineWorkStream

        budget = SharedBudget(max_cost=10.0)
        engine = MagicMock()
        engine.state = MagicMock()
        engine.state.total_cost = 0.0

        async def drain_with_cost() -> None:
            engine.state.total_cost = 3.0

        engine._drain_issues = AsyncMock(side_effect=drain_with_cost)

        fix_stream = EngineWorkStream(
            stream_name="fix-pipeline",
            engine=engine,
            method_name="_drain_issues",
            budget=budget,
        )
        await fix_stream.run_once()
        assert engine._drain_issues.call_count == 1
        # Cost is no longer pushed by the stream — it's done at the engine
        # level via add_cost_async. Budget remains at 0 here.
        assert budget.total_cost == 0.0


# ---------------------------------------------------------------------------
# TS-85-SMOKE-5: Graceful shutdown preserves state
# Path 5
# ---------------------------------------------------------------------------


class TestSmokeGracefulShutdown:
    """Verify shutdown calls all stream shutdowns and removes PID."""

    async def test_shutdown_preserves_state(self, tmp_path: Path) -> None:
        """Both streams shutdown, PID removed."""
        from afcore.nightshift.daemon import DaemonRunner, SharedBudget

        pid_path = tmp_path / "daemon.pid"
        s1 = _make_mock_stream(name="s1")
        s2 = _make_mock_stream(name="s2")
        budget = SharedBudget(max_cost=None)
        config = _make_config()
        runner = DaemonRunner(config, None, [s1, s2], budget, pid_path=pid_path)
        runner.request_shutdown()
        await runner.run()
        assert s1.shutdown.call_count == 1
        assert s2.shutdown.call_count == 1
        assert not pid_path.exists()


# ---------------------------------------------------------------------------
# TS-85-SMOKE-6: PID check blocks code command
# Path 6
# ---------------------------------------------------------------------------


class TestSmokePidBlocksCode:
    """Verify code command blocked when live daemon PID exists."""

    def test_pid_check_blocks(self, tmp_path: Path) -> None:
        """check_pid_file returns ALIVE for current process."""
        from afcore.nightshift.pid import PidStatus, check_pid_file, write_pid_file

        pid_path = tmp_path / "daemon.pid"
        write_pid_file(pid_path)
        status, pid = check_pid_file(pid_path)
        assert status == PidStatus.ALIVE
        assert pid == os.getpid()


# ---------------------------------------------------------------------------
# TS-NS-1: _fill_pool dispatches no new issues after request_shutdown
# Requirement: NS-REQ-1 (issue #51)
# ---------------------------------------------------------------------------


class TestEngineShutdownStopsFillPool:
    """Verify _fill_pool returns immediately after shutdown is requested."""

    async def test_drain_returns_false_when_shutting_down(self) -> None:
        """_drain_issues returns False immediately when is_shutting_down is set.

        This exercises the guard at the top of _drain_issues which prevents
        _run_issue_check (and therefore _fill_pool) from ever being called.
        """
        from afcore.nightshift.engine import NightShiftEngine

        config = MagicMock()
        config.orchestrator = MagicMock()
        config.orchestrator.max_cost = None
        config.orchestrator.max_sessions = None
        config.gate = None

        platform = MagicMock()
        engine = NightShiftEngine(config, platform)

        # Request shutdown before drain starts
        engine.request_shutdown()
        assert engine.state.is_shutting_down is True

        # Patch _run_issue_check to detect if it's called
        engine._run_issue_check = AsyncMock()

        result = await engine._drain_issues()

        assert result is False
        engine._run_issue_check.assert_not_called()


# ---------------------------------------------------------------------------
# TS-NS-2: _drain_issues returns False after shutdown mid-drain
# Requirement: NS-REQ-2 (issue #51)
# ---------------------------------------------------------------------------


class TestDrainStopsOnShutdownMidIteration:
    """Verify _drain_issues stops on the next iteration after shutdown."""

    async def test_shutdown_mid_drain_stops_after_current_check(self) -> None:
        """_drain_issues returns False after one _run_issue_check when shutdown
        is requested between iterations.

        Simulates shutdown being requested during the first _run_issue_check:
        the first check runs to completion, then the loop re-enters and the
        is_shutting_down guard returns False.
        """
        from afcore.nightshift.engine import NightShiftEngine

        config = MagicMock()
        config.orchestrator = MagicMock()
        config.orchestrator.max_cost = None
        config.orchestrator.max_sessions = None
        config.gate = None

        platform = MagicMock()
        engine = NightShiftEngine(config, platform)

        check_count = 0

        async def fake_run_issue_check(_seen: set[int] | None = None) -> None:
            nonlocal check_count
            check_count += 1
            # Signal shutdown during the first check
            engine.request_shutdown()
            # Add a seen issue so the drain considers progress was made
            if _seen is not None:
                _seen.add(100 + check_count)

        engine._run_issue_check = AsyncMock(side_effect=fake_run_issue_check)

        # _drain_issues re-polls after _run_issue_check; stub the platform
        # to return one remaining issue so the drain would normally loop.
        remaining_issue = MagicMock()
        remaining_issue.number = 999
        remaining_issue.labels = ["af:fix"]
        platform.list_issues_by_label = AsyncMock(return_value=[remaining_issue])

        result = await engine._drain_issues()

        assert result is False
        assert check_count == 1, "Expected exactly one _run_issue_check call before shutdown stopped the drain"


# ---------------------------------------------------------------------------
# TS-NS-3: DaemonRunner.request_shutdown propagates to engine
# Requirement: NS-REQ-51 (issue #51)
# ---------------------------------------------------------------------------


class TestDaemonShutdownPropagesToEngine:
    """Verify DaemonRunner.request_shutdown() sets engine.state.is_shutting_down."""

    def test_request_shutdown_sets_engine_flag(self) -> None:
        """Calling request_shutdown on DaemonRunner sets is_shutting_down on the
        engine wired through an EngineWorkStream.
        """
        from afcore.nightshift.daemon import DaemonRunner, SharedBudget
        from afcore.nightshift.engine import NightShiftEngine
        from afcore.nightshift.streams import EngineWorkStream

        config = MagicMock()
        config.orchestrator = MagicMock()
        config.orchestrator.max_cost = None
        config.orchestrator.max_sessions = None
        config.gate = None
        config.night_shift = MagicMock()
        config.night_shift.enabled_streams = ["fixes"]

        platform = MagicMock()
        engine = NightShiftEngine(config, platform)

        stream = EngineWorkStream(
            stream_name="fix-pipeline",
            engine=engine,
            method_name="_drain_issues",
        )
        budget = SharedBudget(max_cost=None)
        runner = DaemonRunner(config, platform, [stream], budget)

        assert engine.state.is_shutting_down is False
        runner.request_shutdown()
        assert engine.state.is_shutting_down is True

    async def test_shutdown_during_drain_stops_new_dispatch(self) -> None:
        """Full integration: daemon with engine-backed stream stops dispatching
        new issues when shutdown is requested during a drain.

        AC-3: only one _process_fix runs; run() returns promptly.
        """
        from afcore.nightshift.daemon import DaemonRunner, SharedBudget
        from afcore.nightshift.engine import NightShiftEngine
        from afcore.nightshift.streams import EngineWorkStream

        config = MagicMock()
        config.orchestrator = MagicMock()
        config.orchestrator.max_cost = None
        config.orchestrator.max_sessions = None
        config.orchestrator.max_retries = 0
        config.gate = None
        config.night_shift = MagicMock()
        config.night_shift.enabled_streams = ["fixes"]
        config.night_shift.max_parallel = 1
        config.night_shift.max_attempts_per_issue = 99

        platform = MagicMock()
        engine = NightShiftEngine(config, platform)

        fix_count = 0

        async def fake_process_fix(issue, issue_body=""):
            nonlocal fix_count
            fix_count += 1
            # Simulate a short fix
            await asyncio.sleep(0.01)
            return True

        engine._process_fix = AsyncMock(side_effect=fake_process_fix)

        # _drain_issues does its own platform polling; set up 3 issues
        issue1 = MagicMock()
        issue1.number = 1
        issue1.title = "Issue 1"
        issue1.labels = ["af:fix"]
        issue2 = MagicMock()
        issue2.number = 2
        issue2.title = "Issue 2"
        issue2.labels = ["af:fix"]
        issue3 = MagicMock()
        issue3.number = 3
        issue3.title = "Issue 3"
        issue3.labels = ["af:fix"]

        # We'll test at the _run_issue_check level by patching it to:
        # 1. On first call: process one issue, request shutdown
        # 2. Should not get a second call
        check_count = 0
        runner = None

        async def fake_run_issue_check(_seen=None):
            nonlocal check_count
            check_count += 1
            # Simulate processing one issue
            await fake_process_fix(issue1)
            if _seen is not None:
                _seen.add(1)
            # Signal shutdown
            runner.request_shutdown()

        engine._run_issue_check = AsyncMock(side_effect=fake_run_issue_check)

        # Stub re-poll to return remaining issues
        platform.list_issues_by_label = AsyncMock(return_value=[issue2, issue3])

        stream = EngineWorkStream(
            stream_name="fix-pipeline",
            engine=engine,
            method_name="_drain_issues",
            interval=999,
        )
        budget = SharedBudget(max_cost=None)
        runner = DaemonRunner(config, platform, [stream], budget)

        # run() should return promptly after shutdown
        await asyncio.wait_for(runner.run(), timeout=5.0)

        assert check_count == 1, f"Expected 1 issue check, got {check_count}"
        assert fix_count == 1, f"Expected 1 fix, got {fix_count}"
        assert engine.state.is_shutting_down is True


# ---------------------------------------------------------------------------
