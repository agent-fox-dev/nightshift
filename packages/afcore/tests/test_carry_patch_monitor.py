"""Tests for CarryPatchMonitor class and MonitorCycleResult dataclass.

All tests in this file are *intentionally failing* pending the implementation
in task groups 5, 6, and 7.  They will be collected by pytest without import
errors but will fail at execution time with ``NotImplementedError`` (from
``CarryPatchMonitor.run_cycle()``) or ``AssertionError`` (from direct
structural checks that fail on the stub types).

Dependencies and forward stubs
-------------------------------
- ``afhub`` package (Spec 01) is not yet available; all afhub types are
  stubbed inline (``_PatchDetail``, ``_PatchStatusDashboard``,
  ``_RebuildJob``, ``_RerereEntry``).
- ``afcore.core.config.CarryPatchConfig`` (Spec 02) may not yet expose
  all fields; config is built with ``MagicMock`` to avoid import errors.
- ``afaudit.events.AuditEventType`` carry-patch constants are not yet
  added (group 4.1 pending); they are accessed inside test functions
  (never at module import time) so collection succeeds.

Specification: 03_carry_patch_pipeline_monitor
Requirements: 03-REQ-2, 03-REQ-3
Test IDs: TS-03-7, TS-03-8, TS-03-9, TS-03-10, TS-03-11, TS-03-12,
          TS-03-13, TS-03-14
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from afcore.nightshift.carry_patch_monitor import CarryPatchMonitor, MonitorCycleResult
from afcore.nightshift.engine import NightShiftEngine

# ---------------------------------------------------------------------------
# Stub types for afhub (Spec 01 — not yet implemented)
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class _PatchDetail:
    """Stub for afhub.PatchDetail."""

    id: str
    status: str
    branch_name: str = ""
    description: str = ""
    conflict_files: list[str] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class _PatchStatusDashboard:
    """Stub for afhub.PatchStatusDashboard."""

    patches: list[_PatchDetail] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class _RebuildJob:
    """Stub for afhub.RebuildJob."""

    id: str
    status: str


@dataclasses.dataclass
class _RerereEntry:
    """Stub for afhub.RerereEntry."""

    path: str
    recorded_at: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(
    *,
    auto_resolve: bool = True,
    max_resolve_retries: int = 3,
    check_interval: int = 60,
    rebuild_timeout: int = 600,
    poll_interval: int = 5,
) -> MagicMock:
    """Return a MagicMock config with carry_patch fields populated."""
    config = MagicMock()
    config.carry_patch = MagicMock()
    config.carry_patch.auto_resolve = auto_resolve
    config.carry_patch.max_resolve_retries = max_resolve_retries
    config.carry_patch.check_interval = check_interval
    config.carry_patch.rebuild_timeout = rebuild_timeout
    config.carry_patch.poll_interval = poll_interval
    config.carry_patch.hub_git_remote = "hub"
    return config


def _make_hub_client(
    *,
    patches: list[_PatchDetail] | None = None,
    get_status_raises: Exception | None = None,
    rerere_entries: list[_RerereEntry] | None = None,
    submit_rebuild_return: _RebuildJob | None = None,
    list_rebuilds_return: list[_RebuildJob] | None = None,
) -> MagicMock:
    """Return a MagicMock HubClient with async methods."""
    client = MagicMock()
    if get_status_raises is not None:
        client.get_patch_status = AsyncMock(side_effect=get_status_raises)
    else:
        dashboard = _PatchStatusDashboard(patches=patches or [])
        client.get_patch_status = AsyncMock(return_value=dashboard)
    client.list_rerere = AsyncMock(return_value=rerere_entries or [])
    client.submit_rebuild = AsyncMock(return_value=(submit_rebuild_return or _RebuildJob("job-1", "queued")))
    client.list_rebuilds = AsyncMock(return_value=list_rebuilds_return or [])
    return client


def _make_engine(
    *,
    coder_session_raises: Exception | None = None,
    coder_session_returns: object = None,
) -> MagicMock:
    """Return a MagicMock NightShiftEngine with async _run_coder_session.

    Uses ``spec=NightShiftEngine`` so that accessing attributes not present
    on the real class raises ``AttributeError`` — ensuring the mock stays
    honest about the engine's public and private API (AC-2, NS-REQ-1).
    """
    engine = MagicMock(spec=NightShiftEngine)
    if coder_session_raises is not None:
        engine._run_coder_session = AsyncMock(side_effect=coder_session_raises)
    else:
        engine._run_coder_session = AsyncMock(return_value=coder_session_returns)
    return engine


def _make_monitor(
    *,
    hub_client: MagicMock | None = None,
    config: MagicMock | None = None,
    workspace_slug: str = "ws-1",
    engine: MagicMock | None = None,
) -> CarryPatchMonitor:
    """Construct a CarryPatchMonitor with sensible defaults."""
    return CarryPatchMonitor(
        hub_client=hub_client or _make_hub_client(),
        workspace_slug=workspace_slug,
        config=config or _make_config(),
        engine=engine or _make_engine(),
    )


# ---------------------------------------------------------------------------
# 2.1 — TS-03-7, TS-03-8: Instantiation and MonitorCycleResult structure
# ---------------------------------------------------------------------------


class TestCarryPatchMonitorInstantiation:
    """TS-03-7, TS-03-8: Instantiation and MonitorCycleResult dataclass.

    Requirements: 03-REQ-2.1, 03-REQ-2.2
    Test IDs: TS-03-7, TS-03-8
    """

    async def test_instantiation_accepts_four_required_params(self) -> None:
        """CarryPatchMonitor accepts hub_client, workspace_slug, config, engine.

        Requirements: 03-REQ-2.1
        Test ID: TS-03-7
        Fails: run_cycle() raises NotImplementedError (groups 5–7 pending)
        """
        hub_client = _make_hub_client()
        config = _make_config()
        engine = _make_engine()
        monitor = CarryPatchMonitor(
            hub_client=hub_client,
            workspace_slug="test-workspace",
            config=config,
            engine=engine,
        )
        assert isinstance(monitor, CarryPatchMonitor)

        # run_cycle() must be a coroutine function.
        assert asyncio.iscoroutinefunction(monitor.run_cycle), "CarryPatchMonitor.run_cycle() must be an async method"

        # FAILS: run_cycle raises NotImplementedError (implementation pending)
        result = await monitor.run_cycle()

        # Assertions after implementation:
        assert isinstance(result, MonitorCycleResult), "run_cycle() must return a MonitorCycleResult instance"

    def test_monitor_cycle_result_is_dataclass(self) -> None:
        """MonitorCycleResult is a dataclasses.dataclass.

        Requirements: 03-REQ-2.2
        Test ID: TS-03-8
        Fails: stub MonitorCycleResult is a plain class, not a dataclass
        """
        # FAILS: MonitorCycleResult is not yet a dataclass (group 5.1 pending)
        assert dataclasses.is_dataclass(MonitorCycleResult), (
            "MonitorCycleResult must be decorated with @dataclasses.dataclass"
        )

    def test_monitor_cycle_result_has_five_required_fields(self) -> None:
        """MonitorCycleResult has the 5 fields with correct types and defaults.

        Requirements: 03-REQ-2.2
        Test ID: TS-03-8
        Fails: stub MonitorCycleResult is not a dataclass (group 5.1 pending)
        """
        # FAILS: MonitorCycleResult is not yet a dataclass
        assert dataclasses.is_dataclass(MonitorCycleResult), "MonitorCycleResult must be a dataclass"
        r = MonitorCycleResult(
            conflicts_detected=3,
            conflicts_resolved=2,
            conflicts_failed=1,
            patches_merged=0,
            rebuild_triggered=True,
        )
        assert r.conflicts_detected == 3
        assert r.conflicts_resolved == 2
        assert r.conflicts_failed == 1
        assert r.patches_merged == 0
        assert r.rebuild_triggered is True

    def test_monitor_cycle_result_defaults_to_all_zero(self) -> None:
        """MonitorCycleResult() with no args defaults to all-zero / False values.

        Requirements: 03-REQ-2.2
        Test ID: TS-03-8
        Fails: stub MonitorCycleResult is not a dataclass (group 5.1 pending)
        """
        # FAILS: MonitorCycleResult is not yet a dataclass
        assert dataclasses.is_dataclass(MonitorCycleResult), "MonitorCycleResult must be a dataclass with zero defaults"
        r = MonitorCycleResult()
        assert r.conflicts_detected == 0
        assert r.conflicts_resolved == 0
        assert r.conflicts_failed == 0
        assert r.patches_merged == 0
        assert r.rebuild_triggered is False

    async def test_monitor_has_retry_counter_and_run_cycle_returns_result(
        self,
    ) -> None:
        """CarryPatchMonitor has _retry_counter dict and run_cycle returns MonitorCycleResult.

        Requirements: 03-REQ-2, 03-REQ-3.4
        Test ID: TS-03-7 (structural + behavioral)
        Fails: run_cycle raises NotImplementedError (groups 5–7 pending)
        """
        monitor = _make_monitor()
        assert hasattr(monitor, "_retry_counter"), "CarryPatchMonitor must have a _retry_counter dict attribute"
        assert isinstance(monitor._retry_counter, dict), "_retry_counter must be a dict"

        # FAILS: run_cycle raises NotImplementedError — the retry counter must
        # be properly wired to run_cycle logic for this assertion to hold.
        result = await monitor.run_cycle()
        assert isinstance(result, MonitorCycleResult), (
            "run_cycle() must return MonitorCycleResult so retry counter tracking is observable through result fields"
        )


# ---------------------------------------------------------------------------
# 2.2 — TS-03-9: Hub error returns empty MonitorCycleResult (fail-open)
# ---------------------------------------------------------------------------


class TestRunCycleHubError:
    """TS-03-9: get_patch_status() error → fail-open empty MonitorCycleResult.

    Requirements: 03-REQ-3.1, 03-REQ-3.E1
    Test ID: TS-03-9
    """

    async def test_get_patch_status_hub_error_returns_empty_result(self) -> None:
        """When get_patch_status raises, run_cycle returns empty MonitorCycleResult.

        Requirements: 03-REQ-3.1 (fail-open)
        Test ID: TS-03-9
        Fails: run_cycle raises NotImplementedError (groups 5–7 pending)
        """
        hub_client = _make_hub_client(get_status_raises=ConnectionError("hub unreachable"))
        monitor = _make_monitor(hub_client=hub_client)

        # FAILS: run_cycle raises NotImplementedError
        result = await monitor.run_cycle()

        # Assertions after implementation:
        assert isinstance(result, MonitorCycleResult)
        assert result.conflicts_detected == 0, "fail-open: conflicts_detected must be 0"
        assert result.conflicts_resolved == 0, "fail-open: conflicts_resolved must be 0"
        assert result.conflicts_failed == 0, "fail-open: conflicts_failed must be 0"
        assert result.patches_merged == 0, "fail-open: patches_merged must be 0"
        assert result.rebuild_triggered is False, "fail-open: rebuild_triggered must be False"

    async def test_get_patch_status_runtime_error_returns_empty_result(self) -> None:
        """RuntimeError from get_patch_status also produces fail-open result.

        Requirements: 03-REQ-3.E1 (any hub error)
        Test ID: TS-03-9
        Fails: run_cycle raises NotImplementedError (groups 5–7 pending)
        """
        hub_client = _make_hub_client(get_status_raises=RuntimeError("internal server error"))
        monitor = _make_monitor(hub_client=hub_client)

        # FAILS: run_cycle raises NotImplementedError
        result = await monitor.run_cycle()

        assert isinstance(result, MonitorCycleResult)
        assert result.conflicts_detected == 0
        assert result.rebuild_triggered is False

    async def test_get_patch_status_error_does_not_propagate(self) -> None:
        """Hub errors must not propagate out of run_cycle (fail-open contract).

        Requirements: 03-REQ-3.E1
        Test ID: TS-03-9
        Fails: run_cycle raises NotImplementedError, not a wrapped hub error
        """
        hub_client = _make_hub_client(get_status_raises=TimeoutError("timed out"))
        monitor = _make_monitor(hub_client=hub_client)

        # FAILS: run_cycle raises NotImplementedError instead of returning
        # a MonitorCycleResult.  After implementation the TimeoutError must
        # be caught and a zero-result returned instead of propagated.
        result = await monitor.run_cycle()
        assert isinstance(result, MonitorCycleResult)


# ---------------------------------------------------------------------------
# 2.3 — TS-03-10: merged_upstream logged; TS-03-11: auto_resolve=False guard
# ---------------------------------------------------------------------------


class TestMergedUpstreamAndAutoResolve:
    """TS-03-10, TS-03-11: merged_upstream logging and auto_resolve=False guard.

    Requirements: 03-REQ-3.1 (merged detection), 03-REQ-3.2 (auto_resolve gate)
    Test IDs: TS-03-10, TS-03-11
    """

    async def test_merged_upstream_patches_increment_patches_merged(self, caplog: pytest.LogCaptureFixture) -> None:
        """run_cycle increments patches_merged for merged_upstream patches.

        Requirements: 03-REQ-3.1
        Test ID: TS-03-10
        Fails: run_cycle raises NotImplementedError (groups 5–7 pending)
        """
        patches = [
            _PatchDetail(id="p1", status="merged_upstream", branch_name="fix/p1"),
            _PatchDetail(id="p2", status="pending"),
        ]
        hub_client = _make_hub_client(patches=patches)
        monitor = _make_monitor(hub_client=hub_client)

        with caplog.at_level(logging.INFO):
            # FAILS: run_cycle raises NotImplementedError
            result = await monitor.run_cycle()

        assert isinstance(result, MonitorCycleResult)
        assert result.patches_merged == 1, "patches_merged must equal the number of merged_upstream patches"
        # merged_upstream patches must be logged at INFO level
        assert any(
            "merged_upstream" in record.message.lower() or "p1" in record.message for record in caplog.records
        ), "merged_upstream patch must be logged"

    async def test_all_merged_upstream_patches_counted(self) -> None:
        """All merged_upstream patches are counted in patches_merged.

        Requirements: 03-REQ-3.1
        Test ID: TS-03-10
        Fails: run_cycle raises NotImplementedError (groups 5–7 pending)
        """
        patches = [
            _PatchDetail(id="p1", status="merged_upstream"),
            _PatchDetail(id="p2", status="merged_upstream"),
            _PatchDetail(id="p3", status="conflict"),
        ]
        hub_client = _make_hub_client(patches=patches)
        config = _make_config(auto_resolve=True, max_resolve_retries=3)
        engine = _make_engine()
        monitor = _make_monitor(hub_client=hub_client, config=config, engine=engine)

        # FAILS: run_cycle raises NotImplementedError
        result = await monitor.run_cycle()

        assert result.patches_merged == 2, "patches_merged must count both merged_upstream patches"

    async def test_auto_resolve_false_does_not_invoke_coder_session(self) -> None:
        """When auto_resolve=False, run_cycle does not invoke the coder session.

        Requirements: 03-REQ-3.2
        Test ID: TS-03-11
        Fails: run_cycle raises NotImplementedError (groups 5–7 pending)
        """
        patches = [
            _PatchDetail(id="p1", status="conflict", branch_name="fix/p1"),
            _PatchDetail(id="p2", status="conflict", branch_name="fix/p2"),
        ]
        hub_client = _make_hub_client(patches=patches)
        config = _make_config(auto_resolve=False)
        engine = _make_engine()
        monitor = _make_monitor(hub_client=hub_client, config=config, engine=engine)

        # FAILS: run_cycle raises NotImplementedError
        result = await monitor.run_cycle()

        # After implementation:
        assert result.conflicts_detected == 2, "conflicts_detected must be set even when auto_resolve=False"
        assert result.conflicts_resolved == 0, "conflicts_resolved must be 0 when auto_resolve=False"
        assert result.conflicts_failed == 0, "conflicts_failed must be 0 when auto_resolve=False"
        assert result.rebuild_triggered is False, "rebuild_triggered must be False when auto_resolve=False"
        engine._run_coder_session.assert_not_called()

    async def test_auto_resolve_false_logs_conflict_count(self, caplog: pytest.LogCaptureFixture) -> None:
        """When auto_resolve=False, conflict count is logged at INFO.

        Requirements: 03-REQ-3.2
        Test ID: TS-03-11
        Fails: run_cycle raises NotImplementedError (groups 5–7 pending)
        """
        patches = [
            _PatchDetail(id="p1", status="conflict"),
            _PatchDetail(id="p2", status="conflict"),
        ]
        hub_client = _make_hub_client(patches=patches)
        config = _make_config(auto_resolve=False)
        monitor = _make_monitor(hub_client=hub_client, config=config)

        with caplog.at_level(logging.INFO):
            # FAILS: run_cycle raises NotImplementedError
            await monitor.run_cycle()

        # After implementation, conflict count logged at INFO
        assert any("2" in record.message or "conflict" in record.message.lower() for record in caplog.records), (
            "conflict count must be logged when auto_resolve=False"
        )


# ---------------------------------------------------------------------------
# 2.4 — TS-03-12: max_resolve_retries exceeded skips patch with warning
# ---------------------------------------------------------------------------


class TestMaxResolveRetriesExceeded:
    """TS-03-12: Patch at max retry count is skipped with a WARNING.

    Requirements: 03-REQ-3.4
    Test ID: TS-03-12
    """

    async def test_patch_at_max_retries_is_skipped(self, caplog: pytest.LogCaptureFixture) -> None:
        """Patch with retry count == max_resolve_retries is skipped.

        Requirements: 03-REQ-3.4
        Test ID: TS-03-12
        Fails: run_cycle raises NotImplementedError (groups 5–7 pending)
        """
        patches = [
            _PatchDetail(id="p1", status="conflict", branch_name="fix/p1"),
        ]
        hub_client = _make_hub_client(patches=patches)
        config = _make_config(auto_resolve=True, max_resolve_retries=3)
        engine = _make_engine()
        monitor = _make_monitor(hub_client=hub_client, config=config, engine=engine)

        # Seed the retry counter to the maximum allowed count.
        monitor._retry_counter[("ws-1", "p1")] = 3

        with caplog.at_level(logging.WARNING):
            # FAILS: run_cycle raises NotImplementedError
            result = await monitor.run_cycle()

        # After implementation:
        engine._run_coder_session.assert_not_called()
        assert result.conflicts_detected == 1, "conflicts_detected must still be incremented for skipped patches"
        assert result.conflicts_failed == 1, "conflicts_failed must count patches skipped due to retry exhaustion"
        assert result.conflicts_resolved == 0

        # Warning logged containing patch id and branch name
        warning_messages = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("p1" in msg for msg in warning_messages), "WARNING must include patch id 'p1'"
        assert any("fix/p1" in msg for msg in warning_messages), "WARNING must include branch name 'fix/p1'"

    async def test_patch_below_max_retries_is_not_skipped(self) -> None:
        """Patch with retry count < max_resolve_retries proceeds to resolution.

        Requirements: 03-REQ-3.4 (counter check boundary)
        Test ID: TS-03-12 (negative boundary)
        Fails: run_cycle raises NotImplementedError (groups 5–7 pending)
        """
        patches = [
            _PatchDetail(id="p1", status="conflict", branch_name="fix/p1"),
        ]
        hub_client = _make_hub_client(patches=patches)
        config = _make_config(auto_resolve=True, max_resolve_retries=3)
        engine = _make_engine()
        monitor = _make_monitor(hub_client=hub_client, config=config, engine=engine)

        # Retry count below max — patch should proceed to resolution attempt.
        monitor._retry_counter[("ws-1", "p1")] = 2

        with (
            patch("afcore.workspace.git.fetch_remote", AsyncMock()),
            patch("afcore.workspace.git.checkout_branch", AsyncMock()),
            patch("afcore.workspace.git.push_to_remote", AsyncMock()),
        ):
            await monitor.run_cycle()

        # After implementation, coder session must be invoked:
        engine._run_coder_session.assert_called_once()


# ---------------------------------------------------------------------------
# 2.5 — TS-03-13: Successful conflict resolution
# ---------------------------------------------------------------------------


class TestSuccessfulConflictResolution:
    """TS-03-13: Successful resolution → fetch, checkout, coder session, push, rebuild.

    Requirements: 03-REQ-3.3, 03-REQ-3.5
    Test ID: TS-03-13
    """

    async def test_successful_resolution_calls_fetch_checkout_coder_push_rebuild(
        self,
    ) -> None:
        """Full resolution sequence is executed on a conflict patch.

        Requirements: 03-REQ-3.3 (steps b–d)
        Test ID: TS-03-13
        Fails: run_cycle raises NotImplementedError (groups 5–7 pending)
        """
        patches = [
            _PatchDetail(
                id="p1",
                status="conflict",
                branch_name="fix/p1",
                description="Fix auth bug",
                conflict_files=["auth.py"],
            )
        ]
        hub_client = _make_hub_client(
            patches=patches,
            submit_rebuild_return=_RebuildJob("job-1", "queued"),
        )
        config = _make_config(auto_resolve=True, max_resolve_retries=3)
        engine = _make_engine(coder_session_returns=None)  # success: returns None
        monitor = _make_monitor(hub_client=hub_client, config=config, engine=engine)

        with (
            patch("afcore.workspace.git.fetch_remote", AsyncMock()) as mock_fetch,
            patch("afcore.workspace.git.checkout_branch", AsyncMock()) as mock_checkout,
            patch("afcore.workspace.git.push_to_remote", AsyncMock()) as mock_push,
        ):
            # FAILS: run_cycle raises NotImplementedError
            result = await monitor.run_cycle()

        # After implementation, all steps must be invoked:
        mock_fetch.assert_called()
        mock_checkout.assert_called()
        engine._run_coder_session.assert_called_once()
        mock_push.assert_called()
        hub_client.submit_rebuild.assert_called()

        assert result.conflicts_detected == 1
        assert result.conflicts_resolved == 1
        assert result.conflicts_failed == 0
        assert result.rebuild_triggered is True

    async def test_successful_resolution_invokes_coder_with_carry_patch_mode(
        self,
    ) -> None:
        """Coder session is invoked with archetype='coder' and mode='carry-patch'.

        Requirements: 03-REQ-3.3 step c
        Test ID: TS-03-13
        Fails: run_cycle raises NotImplementedError (groups 5–7 pending)
        """
        patches = [_PatchDetail(id="p1", status="conflict", branch_name="fix/p1")]
        hub_client = _make_hub_client(patches=patches)
        config = _make_config(auto_resolve=True, max_resolve_retries=3)
        engine = _make_engine()
        monitor = _make_monitor(hub_client=hub_client, config=config, engine=engine)

        with (
            patch("afcore.workspace.git.fetch_remote", AsyncMock()),
            patch("afcore.workspace.git.checkout_branch", AsyncMock()),
            patch("afcore.workspace.git.push_to_remote", AsyncMock()),
        ):
            # FAILS: run_cycle raises NotImplementedError
            await monitor.run_cycle()

        # After implementation, coder session must have been called with the
        # correct archetype and mode:
        engine._run_coder_session.assert_called_once()
        call_kwargs = engine._run_coder_session.call_args
        assert call_kwargs is not None
        # archetype='coder' and mode='carry-patch' must appear in args/kwargs
        all_args = list(call_kwargs.args) + list(call_kwargs.kwargs.values())
        assert any(v == "coder" for v in all_args if isinstance(v, str)), (
            "coder session must be invoked with archetype='coder'"
        )
        assert any(v == "carry-patch" for v in all_args if isinstance(v, str)), (
            "coder session must be invoked with mode='carry-patch'"
        )

    async def test_successful_resolution_emits_conflict_resolved_audit_event(
        self,
    ) -> None:
        """CARRY_PATCH_CONFLICT_RESOLVED audit event is emitted on success.

        Requirements: 03-REQ-3.5, 03-REQ-8
        Test ID: TS-03-13
        Fails: run_cycle raises NotImplementedError (groups 5–7 pending)
        """
        patches = [_PatchDetail(id="p1", status="conflict", branch_name="fix/p1")]
        hub_client = _make_hub_client(patches=patches)
        config = _make_config(auto_resolve=True)
        engine = _make_engine()
        monitor = _make_monitor(hub_client=hub_client, config=config, engine=engine)

        emitted_types: list[str] = []

        def capture_emit(sink: object, run_id: str, event_type: object, **kwargs: object) -> None:
            emitted_types.append(str(event_type))

        with (
            patch("afaudit.emit.emit_audit_event", side_effect=capture_emit),
            patch("afcore.workspace.git.fetch_remote", AsyncMock()),
            patch("afcore.workspace.git.checkout_branch", AsyncMock()),
            patch("afcore.workspace.git.push_to_remote", AsyncMock()),
        ):
            # FAILS: run_cycle raises NotImplementedError
            await monitor.run_cycle()

        # After implementation:
        from afaudit.events import AuditEventType  # noqa: PLC0415

        assert str(AuditEventType.CARRY_PATCH_CONFLICT_RESOLVED) in emitted_types, (
            "CARRY_PATCH_CONFLICT_RESOLVED audit event must be emitted on success"
        )


# ---------------------------------------------------------------------------
# 2.6 — TS-03-14: Failed conflict resolution increments retry counter
# ---------------------------------------------------------------------------


class TestFailedConflictResolution:
    """TS-03-14: Coder session failure increments retry counter, emits audit event.

    Requirements: 03-REQ-3.6
    Test ID: TS-03-14
    """

    async def test_failed_resolution_increments_retry_counter(self) -> None:
        """Session retry counter is incremented when coder session fails.

        Requirements: 03-REQ-3.6
        Test ID: TS-03-14
        Fails: run_cycle raises NotImplementedError (groups 5–7 pending)
        """
        patches = [_PatchDetail(id="p1", status="conflict", branch_name="fix/p1")]
        hub_client = _make_hub_client(patches=patches)
        config = _make_config(auto_resolve=True, max_resolve_retries=3)
        engine = _make_engine(coder_session_raises=RuntimeError("coder session failed"))
        monitor = _make_monitor(hub_client=hub_client, config=config, engine=engine)

        initial_count = monitor._retry_counter.get(("ws-1", "p1"), 0)
        assert initial_count == 0

        with (
            patch("afcore.workspace.git.fetch_remote", AsyncMock()),
            patch("afcore.workspace.git.checkout_branch", AsyncMock()),
        ):
            # FAILS: run_cycle raises NotImplementedError
            result = await monitor.run_cycle()

        # After implementation:
        new_count = monitor._retry_counter.get(("ws-1", "p1"), 0)
        assert new_count == initial_count + 1, "retry counter must be incremented by 1 on coder session failure"
        assert result.conflicts_failed == 1
        assert result.conflicts_resolved == 0

    async def test_failed_resolution_emits_conflict_failed_audit_event(
        self,
    ) -> None:
        """CARRY_PATCH_CONFLICT_FAILED audit event emitted on resolution failure.

        Requirements: 03-REQ-3.6, 03-REQ-8
        Test ID: TS-03-14
        Fails: run_cycle raises NotImplementedError (groups 5–7 pending)
        """
        patches = [_PatchDetail(id="p1", status="conflict", branch_name="fix/p1")]
        hub_client = _make_hub_client(patches=patches)
        config = _make_config(auto_resolve=True, max_resolve_retries=3)
        engine = _make_engine(coder_session_raises=RuntimeError("coder failed"))
        monitor = _make_monitor(hub_client=hub_client, config=config, engine=engine)

        emitted_types: list[str] = []

        def capture_emit(sink: object, run_id: str, event_type: object, **kwargs: object) -> None:
            emitted_types.append(str(event_type))

        with (
            patch("afaudit.emit.emit_audit_event", side_effect=capture_emit),
            patch("afcore.workspace.git.fetch_remote", AsyncMock()),
            patch("afcore.workspace.git.checkout_branch", AsyncMock()),
        ):
            # FAILS: run_cycle raises NotImplementedError
            await monitor.run_cycle()

        # After implementation:
        from afaudit.events import AuditEventType  # noqa: PLC0415

        assert str(AuditEventType.CARRY_PATCH_CONFLICT_FAILED) in emitted_types, (
            "CARRY_PATCH_CONFLICT_FAILED audit event must be emitted on failure"
        )

    async def test_failed_resolution_does_not_propagate_exception(self) -> None:
        """Coder session failure must not propagate out of run_cycle.

        Requirements: 03-REQ-3.6 (continue to next patch)
        Test ID: TS-03-14
        Fails: run_cycle raises NotImplementedError (groups 5–7 pending)
        """
        patches = [
            _PatchDetail(id="p1", status="conflict", branch_name="fix/p1"),
            _PatchDetail(id="p2", status="conflict", branch_name="fix/p2"),
        ]
        hub_client = _make_hub_client(patches=patches)
        config = _make_config(auto_resolve=True, max_resolve_retries=3)
        engine = _make_engine(coder_session_raises=RuntimeError("coder failed"))
        monitor = _make_monitor(hub_client=hub_client, config=config, engine=engine)

        with (
            patch("afcore.workspace.git.fetch_remote", AsyncMock()),
            patch("afcore.workspace.git.checkout_branch", AsyncMock()),
        ):
            # FAILS: run_cycle raises NotImplementedError rather than returning.
            # After implementation, the RuntimeError from coder must be caught
            # and the second conflict patch must also be attempted.
            result = await monitor.run_cycle()

        # Both patches attempted (neither resolution succeeded):
        assert result.conflicts_failed == 2
        assert result.conflicts_resolved == 0
        assert result.rebuild_triggered is False

    async def test_failed_resolution_retry_counter_starts_at_zero(self) -> None:
        """First failure increments the retry counter from 0 to 1.

        Requirements: 03-REQ-3.6
        Test ID: TS-03-14
        Fails: run_cycle raises NotImplementedError (groups 5–7 pending)
        """
        patches = [_PatchDetail(id="p1", status="conflict", branch_name="fix/p1")]
        hub_client = _make_hub_client(patches=patches)
        config = _make_config(auto_resolve=True, max_resolve_retries=3)
        engine = _make_engine(coder_session_raises=RuntimeError("coder failed"))
        monitor = _make_monitor(hub_client=hub_client, config=config, engine=engine)

        # Retry counter starts empty for this patch.
        assert ("ws-1", "p1") not in monitor._retry_counter

        with (
            patch("afcore.workspace.git.fetch_remote", AsyncMock()),
            patch("afcore.workspace.git.checkout_branch", AsyncMock()),
        ):
            # FAILS: run_cycle raises NotImplementedError
            await monitor.run_cycle()

        # After implementation, counter must be 1:
        assert monitor._retry_counter.get(("ws-1", "p1"), 0) == 1, (
            "retry counter for ('ws-1', 'p1') must be 1 after first failure"
        )


# ---------------------------------------------------------------------------
# 3.1 — TS-03-15: Conflict resolution context assembly
# ---------------------------------------------------------------------------


class TestConflictResolutionContext:
    """TS-03-15: Context dict passed to coder session during conflict resolution.

    Requirements: 03-REQ-4.1
    Test ID: TS-03-15
    """

    async def test_context_dict_has_all_required_keys(self) -> None:
        """Context dict contains patch_description, conflict_files,
        upstream_context, and rerere_resolutions.

        Requirements: 03-REQ-4.1
        Test ID: TS-03-15
        Fails: run_cycle raises NotImplementedError (groups 5–7 pending)
        """
        patches = [
            _PatchDetail(
                id="p1",
                status="conflict",
                branch_name="fix/p1",
                description="Fix auth bug",
                conflict_files=["auth.py"],
            )
        ]
        hub_client = _make_hub_client(
            patches=patches,
            rerere_entries=[_RerereEntry(path="auth.py", recorded_at="2026-01-01")],
        )
        config = _make_config(auto_resolve=True)
        engine = _make_engine()
        monitor = _make_monitor(hub_client=hub_client, config=config, engine=engine)

        with (
            patch("afcore.workspace.git.fetch_remote", AsyncMock()),
            patch("afcore.workspace.git.checkout_branch", AsyncMock()),
            patch("afcore.workspace.git.push_to_remote", AsyncMock()),
            patch(
                "afcore.workspace.git.run_git",
                AsyncMock(return_value=(0, "diff --git a/auth.py ...", "")),
            ),
        ):
            # FAILS: run_cycle raises NotImplementedError
            await monitor.run_cycle()

        # After implementation, coder session must receive the context dict:
        engine._run_coder_session.assert_called_once()
        call_kwargs = engine._run_coder_session.call_args
        assert call_kwargs is not None

        # Extract context dict from call args or kwargs
        all_values = list(call_kwargs.args) + list(call_kwargs.kwargs.values())
        ctx_candidates = [v for v in all_values if isinstance(v, dict)]
        assert ctx_candidates, "coder session must receive a context dict argument"
        ctx = ctx_candidates[0]

        assert ctx["patch_description"] == "Fix auth bug", "patch_description must come from PatchDetail.description"
        assert ctx["conflict_files"] == ["auth.py"], "conflict_files must come from PatchDetail.conflict_files"
        assert "diff" in ctx["upstream_context"], "upstream_context must contain git diff output"
        assert ctx["rerere_resolutions"] == ["auth.py"], (
            "rerere_resolutions must be path strings extracted from RerereEntry"
        )

    async def test_rerere_resolutions_are_path_strings_not_objects(self) -> None:
        """rerere_resolutions contains plain path strings, not RerereEntry objects.

        Requirements: 03-REQ-4.1
        Test ID: TS-03-15
        Fails: run_cycle raises NotImplementedError (groups 5–7 pending)
        """
        patches = [
            _PatchDetail(
                id="p1",
                status="conflict",
                branch_name="fix/p1",
                description="Desc",
                conflict_files=["a.py"],
            )
        ]
        entries = [
            _RerereEntry(path="a.py", recorded_at="2026-01-01"),
            _RerereEntry(path="b.py", recorded_at="2026-01-02"),
        ]
        hub_client = _make_hub_client(patches=patches, rerere_entries=entries)
        config = _make_config(auto_resolve=True)
        engine = _make_engine()
        monitor = _make_monitor(hub_client=hub_client, config=config, engine=engine)

        with (
            patch("afcore.workspace.git.fetch_remote", AsyncMock()),
            patch("afcore.workspace.git.checkout_branch", AsyncMock()),
            patch("afcore.workspace.git.push_to_remote", AsyncMock()),
            patch(
                "afcore.workspace.git.run_git",
                AsyncMock(return_value=(0, "", "")),
            ),
        ):
            # FAILS: run_cycle raises NotImplementedError
            await monitor.run_cycle()

        engine._run_coder_session.assert_called_once()
        call_kwargs = engine._run_coder_session.call_args
        all_values = list(call_kwargs.args) + list(call_kwargs.kwargs.values())
        ctx = next(v for v in all_values if isinstance(v, dict))

        # Path strings, NOT RerereEntry objects
        assert ctx["rerere_resolutions"] == ["a.py", "b.py"]
        for item in ctx["rerere_resolutions"]:
            assert isinstance(item, str), f"rerere_resolutions items must be str, got {type(item)}"

    async def test_patch_description_defaults_to_empty_string_when_none(
        self,
    ) -> None:
        """When PatchDetail.description is None, patch_description is empty string.

        Requirements: 03-REQ-4.E3
        Test ID: TS-03-15
        Fails: run_cycle raises NotImplementedError (groups 5–7 pending)
        """
        patches = [
            _PatchDetail(
                id="p1",
                status="conflict",
                branch_name="fix/p1",
                description="",
                conflict_files=[],
            )
        ]
        # Override description to None to test the None → "" conversion
        patches[0].description = None  # type: ignore[assignment]
        hub_client = _make_hub_client(patches=patches)
        config = _make_config(auto_resolve=True)
        engine = _make_engine()
        monitor = _make_monitor(hub_client=hub_client, config=config, engine=engine)

        with (
            patch("afcore.workspace.git.fetch_remote", AsyncMock()),
            patch("afcore.workspace.git.checkout_branch", AsyncMock()),
            patch("afcore.workspace.git.push_to_remote", AsyncMock()),
            patch(
                "afcore.workspace.git.run_git",
                AsyncMock(return_value=(0, "", "")),
            ),
        ):
            # FAILS: run_cycle raises NotImplementedError
            await monitor.run_cycle()

        engine._run_coder_session.assert_called_once()
        call_kwargs = engine._run_coder_session.call_args
        all_values = list(call_kwargs.args) + list(call_kwargs.kwargs.values())
        ctx = next(v for v in all_values if isinstance(v, dict))
        assert ctx["patch_description"] == "", "patch_description must be '' when description is None"

    async def test_list_rerere_exception_passes_empty_list(self, caplog: pytest.LogCaptureFixture) -> None:
        """When list_rerere raises, rerere_resolutions is [] and coder proceeds.

        Requirements: 03-REQ-4.E1
        Test ID: TS-03-15
        Fails: run_cycle raises NotImplementedError (groups 5–7 pending)
        """
        patches = [
            _PatchDetail(
                id="p1",
                status="conflict",
                branch_name="fix/p1",
                description="Desc",
                conflict_files=["a.py"],
            )
        ]
        hub_client = _make_hub_client(patches=patches)
        hub_client.list_rerere = AsyncMock(side_effect=ConnectionError("rerere service down"))
        config = _make_config(auto_resolve=True)
        engine = _make_engine()
        monitor = _make_monitor(hub_client=hub_client, config=config, engine=engine)

        with (
            caplog.at_level(logging.WARNING),
            patch("afcore.workspace.git.fetch_remote", AsyncMock()),
            patch("afcore.workspace.git.checkout_branch", AsyncMock()),
            patch("afcore.workspace.git.push_to_remote", AsyncMock()),
            patch(
                "afcore.workspace.git.run_git",
                AsyncMock(return_value=(0, "some diff", "")),
            ),
        ):
            # FAILS: run_cycle raises NotImplementedError
            await monitor.run_cycle()

        # Coder session must still be invoked (not aborted)
        engine._run_coder_session.assert_called_once()
        call_kwargs = engine._run_coder_session.call_args
        all_values = list(call_kwargs.args) + list(call_kwargs.kwargs.values())
        ctx = next(v for v in all_values if isinstance(v, dict))
        assert ctx["rerere_resolutions"] == [], "rerere_resolutions must be [] when list_rerere raises"

        # Warning logged
        assert any("rerere" in r.message.lower() for r in caplog.records if r.levelno >= logging.WARNING), (
            "warning about rerere failure must be logged"
        )

    async def test_git_diff_failure_passes_empty_upstream_context(self, caplog: pytest.LogCaptureFixture) -> None:
        """When git diff fails, upstream_context is '' and coder proceeds.

        Requirements: 03-REQ-4.E2
        Test ID: TS-03-15
        Fails: run_cycle raises NotImplementedError (groups 5–7 pending)
        """
        patches = [
            _PatchDetail(
                id="p1",
                status="conflict",
                branch_name="fix/p1",
                description="Desc",
                conflict_files=["a.py"],
            )
        ]
        hub_client = _make_hub_client(
            patches=patches,
            rerere_entries=[_RerereEntry(path="a.py")],
        )
        config = _make_config(auto_resolve=True)
        engine = _make_engine()
        monitor = _make_monitor(hub_client=hub_client, config=config, engine=engine)

        with (
            caplog.at_level(logging.WARNING),
            patch("afcore.workspace.git.fetch_remote", AsyncMock()),
            patch("afcore.workspace.git.checkout_branch", AsyncMock()),
            patch("afcore.workspace.git.push_to_remote", AsyncMock()),
            patch(
                "afcore.workspace.git.run_git",
                AsyncMock(return_value=(1, "", "fatal: bad ref")),
            ),
        ):
            # FAILS: run_cycle raises NotImplementedError
            await monitor.run_cycle()

        engine._run_coder_session.assert_called_once()
        call_kwargs = engine._run_coder_session.call_args
        all_values = list(call_kwargs.args) + list(call_kwargs.kwargs.values())
        ctx = next(v for v in all_values if isinstance(v, dict))
        assert ctx["upstream_context"] == "", "upstream_context must be '' when git diff fails"


# ---------------------------------------------------------------------------
# 3.1 — TS-03-16: Rerere state is never modified (read-only)
# ---------------------------------------------------------------------------


class TestRerereReadOnly:
    """TS-03-16: run_cycle only reads rerere state; no write/mutation calls.

    Requirements: 03-REQ-4.2
    Test ID: TS-03-16
    """

    async def test_only_list_rerere_called_no_write_methods(self) -> None:
        """hub_client.list_rerere is called; no write_rerere or mutation call.

        Requirements: 03-REQ-4.2
        Test ID: TS-03-16
        Fails: run_cycle raises NotImplementedError (groups 5–7 pending)
        """
        patches = [
            _PatchDetail(
                id="p1",
                status="conflict",
                branch_name="fix/p1",
                conflict_files=["a.py"],
            )
        ]
        hub_client = _make_hub_client(patches=patches, rerere_entries=[])
        # Add hypothetical write methods to verify they're never called
        hub_client.write_rerere = AsyncMock()
        hub_client.delete_rerere = AsyncMock()
        hub_client.update_rerere = AsyncMock()

        config = _make_config(auto_resolve=True)
        engine = _make_engine()
        monitor = _make_monitor(hub_client=hub_client, config=config, engine=engine)

        with (
            patch("afcore.workspace.git.fetch_remote", AsyncMock()),
            patch("afcore.workspace.git.checkout_branch", AsyncMock()),
            patch("afcore.workspace.git.push_to_remote", AsyncMock()),
            patch(
                "afcore.workspace.git.run_git",
                AsyncMock(return_value=(0, "", "")),
            ),
        ):
            # FAILS: run_cycle raises NotImplementedError
            await monitor.run_cycle()

        # list_rerere must be called (read-only)
        hub_client.list_rerere.assert_called()

        # No write/mutation rerere calls
        hub_client.write_rerere.assert_not_called()
        hub_client.delete_rerere.assert_not_called()
        hub_client.update_rerere.assert_not_called()


# ---------------------------------------------------------------------------
# 3.6 — TS-03-24: CONFLICT_DETECTED and MERGED_DETECTED audit events
# ---------------------------------------------------------------------------


class TestConflictDetectedAndMergedDetectedAuditEvents:
    """TS-03-24: Audit events for conflict detection and merged patch detection.

    Requirements: 03-REQ-8.2
    Test ID: TS-03-24
    """

    async def test_conflict_detected_audit_event_emitted(self) -> None:
        """CARRY_PATCH_CONFLICT_DETECTED emitted when a conflict patch is found.

        Requirements: 03-REQ-8.2
        Test ID: TS-03-24
        Fails: run_cycle raises NotImplementedError (groups 5–7 pending)
        """
        patches = [_PatchDetail(id="p1", status="conflict", branch_name="fix/p1")]
        hub_client = _make_hub_client(patches=patches)
        config = _make_config(auto_resolve=True)
        engine = _make_engine()
        monitor = _make_monitor(hub_client=hub_client, config=config, engine=engine)

        emitted_types: list[str] = []

        def capture_emit(sink: object, run_id: str, event_type: object, **kwargs: object) -> None:
            emitted_types.append(str(event_type))

        with (
            patch("afaudit.emit.emit_audit_event", side_effect=capture_emit),
            patch("afcore.workspace.git.fetch_remote", AsyncMock()),
            patch("afcore.workspace.git.checkout_branch", AsyncMock()),
            patch("afcore.workspace.git.push_to_remote", AsyncMock()),
            patch(
                "afcore.workspace.git.run_git",
                AsyncMock(return_value=(0, "", "")),
            ),
        ):
            # FAILS: run_cycle raises NotImplementedError
            await monitor.run_cycle()

        from afaudit.events import AuditEventType  # noqa: PLC0415

        assert str(AuditEventType.CARRY_PATCH_CONFLICT_DETECTED) in emitted_types, (
            "CARRY_PATCH_CONFLICT_DETECTED audit event must be emitted when a conflict patch is found"
        )

    async def test_merged_detected_audit_event_emitted(self) -> None:
        """CARRY_PATCH_MERGED_DETECTED emitted for merged_upstream patches.

        Requirements: 03-REQ-8.2
        Test ID: TS-03-24
        Fails: run_cycle raises NotImplementedError (groups 5–7 pending)
        """
        patches = [_PatchDetail(id="p1", status="merged_upstream", branch_name="fix/p1")]
        hub_client = _make_hub_client(patches=patches)
        config = _make_config()
        monitor = _make_monitor(hub_client=hub_client, config=config)

        emitted_types: list[str] = []

        def capture_emit(sink: object, run_id: str, event_type: object, **kwargs: object) -> None:
            emitted_types.append(str(event_type))

        with patch("afaudit.emit.emit_audit_event", side_effect=capture_emit):
            # FAILS: run_cycle raises NotImplementedError
            await monitor.run_cycle()

        from afaudit.events import AuditEventType  # noqa: PLC0415

        assert str(AuditEventType.CARRY_PATCH_MERGED_DETECTED) in emitted_types, (
            "CARRY_PATCH_MERGED_DETECTED audit event must be emitted for merged_upstream patches"
        )

    async def test_audit_event_emission_failure_does_not_abort_cycle(
        self,
    ) -> None:
        """emit_audit_event failure does not abort the monitor cycle.

        Requirements: 03-REQ-8.E1
        Test ID: TS-03-24
        Fails: run_cycle raises NotImplementedError (groups 5–7 pending)
        """
        patches = [_PatchDetail(id="p1", status="conflict", branch_name="fix/p1")]
        hub_client = _make_hub_client(patches=patches)
        config = _make_config(auto_resolve=True)
        engine = _make_engine()
        monitor = _make_monitor(hub_client=hub_client, config=config, engine=engine)

        with (
            patch(
                "afaudit.emit.emit_audit_event",
                side_effect=RuntimeError("audit sink down"),
            ),
            patch("afcore.workspace.git.fetch_remote", AsyncMock()),
            patch("afcore.workspace.git.checkout_branch", AsyncMock()),
            patch("afcore.workspace.git.push_to_remote", AsyncMock()),
            patch(
                "afcore.workspace.git.run_git",
                AsyncMock(return_value=(0, "", "")),
            ),
        ):
            # FAILS: run_cycle raises NotImplementedError
            # After implementation, this must return normally even though
            # emit_audit_event raises.
            result = await monitor.run_cycle()

        assert isinstance(result, MonitorCycleResult), (
            "run_cycle must return MonitorCycleResult even when audit emission fails"
        )


# ---------------------------------------------------------------------------
# 4.1 — AC-1/AC-2/AC-3/AC-4/AC-5: Rendered prompt and spec-bound mock
# ---------------------------------------------------------------------------


def _mock_session_params() -> MagicMock:
    """Return a mock ResolvedSessionParams with sensible defaults."""
    params = MagicMock()
    params.max_turns = 200
    params.thinking = None
    params.max_budget_usd = None
    params.effort = "high"
    params.compaction = False
    params.cache_policy = "NONE"
    return params


class TestSpecBoundEngine:
    """AC-2: MagicMock(spec=NightShiftEngine) works for _run_coder_session.

    Requirements: NS-REQ-1
    """

    def test_spec_bound_mock_has_run_coder_session(self) -> None:
        """Accessing _run_coder_session on a spec-bound mock succeeds.

        Requirements: NS-REQ-1.1
        """
        engine = MagicMock(spec=NightShiftEngine)
        # This must NOT raise AttributeError — proving the method
        # exists on the real NightShiftEngine class.
        _ = engine._run_coder_session
        assert hasattr(engine, "_run_coder_session")


class TestRunCoderSessionRendering:
    """Tests for NightShiftEngine._run_coder_session prompt rendering.

    Verifies that the carry-patch profile template is loaded and rendered
    with actual context values — no {{ ... }} placeholders survive.

    Requirements: NS-REQ-2, NS-REQ-3, NS-REQ-4
    Test Specs: TS-NS-2, TS-NS-3, TS-NS-4
    """

    async def test_system_prompt_contains_patch_description_and_conflict_files(
        self,
    ) -> None:
        """run_session receives a prompt with patch description and conflict files.

        Requirements: NS-REQ-2.1
        Test Spec: TS-NS-2
        """
        config = MagicMock()
        config.models = MagicMock()
        engine = NightShiftEngine(config=config, platform=MagicMock())

        context: dict[str, object] = {
            "patch_description": "Fix authentication bypass in login flow",
            "conflict_files": ["auth.py", "login.py"],
            "upstream_context": "diff --git a/auth.py b/auth.py",
            "rerere_resolutions": [],
            "branch": "fix/auth",
            "repo_root": "/tmp/test-repo",
        }

        with (
            patch("afcore.session.session.run_session", new_callable=AsyncMock) as mock_run,
            patch("afcore.core.models.resolve_model", return_value="claude-sonnet-4-20250514"),
            patch("afcore.engine.sdk_params.resolve_model_tier", return_value="STANDARD"),
            patch("afcore.engine.sdk_params.resolve_session_params", return_value=_mock_session_params()),
            patch("afcore.engine.sdk_params.resolve_security_config", return_value=None),
        ):
            await engine._run_coder_session(
                archetype="coder",
                mode="carry-patch",
                context=context,
            )

        mock_run.assert_called_once()
        call_kwargs = mock_run.call_args.kwargs
        system_prompt = call_kwargs["system_prompt"]

        assert "Fix authentication bypass in login flow" in system_prompt, (
            "system_prompt must contain the literal patch_description value"
        )
        assert "auth.py" in system_prompt, "system_prompt must contain conflict file names"
        assert "login.py" in system_prompt, "system_prompt must contain all conflict file names"

    async def test_no_template_placeholders_remain(self) -> None:
        """No {{ ... }} placeholders remain in the rendered system prompt.

        Requirements: NS-REQ-2.1
        Test Spec: TS-NS-2
        """
        import re

        config = MagicMock()
        config.models = MagicMock()
        engine = NightShiftEngine(config=config, platform=MagicMock())

        context: dict[str, object] = {
            "patch_description": "Fix bug",
            "conflict_files": ["file.py"],
            "upstream_context": "some diff",
            "rerere_resolutions": ["path/to/rerere"],
            "branch": "fix/bug",
            "repo_root": "/tmp/repo",
        }

        with (
            patch("afcore.session.session.run_session", new_callable=AsyncMock) as mock_run,
            patch("afcore.core.models.resolve_model", return_value="claude-sonnet-4-20250514"),
            patch("afcore.engine.sdk_params.resolve_model_tier", return_value="STANDARD"),
            patch("afcore.engine.sdk_params.resolve_session_params", return_value=_mock_session_params()),
            patch("afcore.engine.sdk_params.resolve_security_config", return_value=None),
        ):
            await engine._run_coder_session(
                archetype="coder",
                mode="carry-patch",
                context=context,
            )

        system_prompt = mock_run.call_args.kwargs["system_prompt"]
        assert not re.search(r"\{\{\s*\w+\s*\}\}", system_prompt), (
            f"system_prompt must not contain {{ ... }} placeholders, got: {system_prompt[:200]}"
        )

    async def test_rerere_resolutions_in_prompt(self) -> None:
        """System prompt contains rerere resolution paths.

        Requirements: NS-REQ-4.1
        Test Spec: TS-NS-4
        """
        config = MagicMock()
        config.models = MagicMock()
        engine = NightShiftEngine(config=config, platform=MagicMock())

        context: dict[str, object] = {
            "patch_description": "Fix bug",
            "conflict_files": ["file.py"],
            "upstream_context": "",
            "rerere_resolutions": ["path/to/rerere", "another/rerere"],
            "branch": "fix/bug",
            "repo_root": "/tmp/repo",
        }

        with (
            patch("afcore.session.session.run_session", new_callable=AsyncMock) as mock_run,
            patch("afcore.core.models.resolve_model", return_value="claude-sonnet-4-20250514"),
            patch("afcore.engine.sdk_params.resolve_model_tier", return_value="STANDARD"),
            patch("afcore.engine.sdk_params.resolve_session_params", return_value=_mock_session_params()),
            patch("afcore.engine.sdk_params.resolve_security_config", return_value=None),
        ):
            await engine._run_coder_session(
                archetype="coder",
                mode="carry-patch",
                context=context,
            )

        system_prompt = mock_run.call_args.kwargs["system_prompt"]
        assert "path/to/rerere" in system_prompt, "system_prompt must contain rerere resolution paths"
        assert "another/rerere" in system_prompt, "system_prompt must contain all rerere resolution paths"

    async def test_carry_patch_mode_passed_to_session_params(self) -> None:
        """SDK parameters are resolved with mode='carry-patch'.

        Requirements: NS-REQ-3.1
        Test Spec: TS-NS-3
        """
        config = MagicMock()
        config.models = MagicMock()
        engine = NightShiftEngine(config=config, platform=MagicMock())

        context: dict[str, object] = {
            "patch_description": "Fix bug",
            "conflict_files": ["file.py"],
            "upstream_context": "",
            "rerere_resolutions": [],
            "branch": "fix/bug",
            "repo_root": "/tmp/repo",
        }

        with (
            patch("afcore.session.session.run_session", new_callable=AsyncMock) as mock_run,
            patch("afcore.core.models.resolve_model", return_value="claude-sonnet-4-20250514"),
            patch("afcore.engine.sdk_params.resolve_model_tier", return_value="STANDARD") as mock_tier,
            patch(
                "afcore.engine.sdk_params.resolve_session_params",
                return_value=_mock_session_params(),
            ) as mock_params,
            patch("afcore.engine.sdk_params.resolve_security_config", return_value=None),
        ):
            await engine._run_coder_session(
                archetype="coder",
                mode="carry-patch",
                context=context,
            )

        # Verify mode='carry-patch' was passed to param resolution
        mock_tier.assert_called_once_with(config, "coder", mode="carry-patch")
        mock_params.assert_called_once_with(config, "coder", mode="carry-patch")

        # Verify archetype='coder' was passed to run_session
        call_kwargs = mock_run.call_args.kwargs
        assert call_kwargs["archetype"] == "coder"

    async def test_workspace_info_constructed_from_context(self) -> None:
        """WorkspaceInfo is built with repo_root and branch from context.

        Requirements: NS-REQ-5.1
        Test Spec: TS-NS-5
        """
        config = MagicMock()
        config.models = MagicMock()
        engine = NightShiftEngine(config=config, platform=MagicMock())

        context: dict[str, object] = {
            "patch_description": "Fix bug",
            "conflict_files": [],
            "upstream_context": "",
            "rerere_resolutions": [],
            "branch": "fix/my-branch",
            "repo_root": "/tmp/my-repo",
        }

        with (
            patch("afcore.session.session.run_session", new_callable=AsyncMock) as mock_run,
            patch("afcore.core.models.resolve_model", return_value="claude-sonnet-4-20250514"),
            patch("afcore.engine.sdk_params.resolve_model_tier", return_value="STANDARD"),
            patch("afcore.engine.sdk_params.resolve_session_params", return_value=_mock_session_params()),
            patch("afcore.engine.sdk_params.resolve_security_config", return_value=None),
        ):
            await engine._run_coder_session(
                archetype="coder",
                mode="carry-patch",
                context=context,
            )

        from pathlib import Path  # noqa: PLC0415

        workspace = mock_run.call_args.kwargs["workspace"]
        assert workspace.path == Path("/tmp/my-repo")
        assert workspace.branch == "fix/my-branch"
        assert workspace.mode == "carry-patch"


class TestFullCycleResolution:
    """AC-5: Full run_cycle with a real engine (patched internals) resolves successfully.

    Verifies that conflicts_resolved increments by 1 and conflicts_failed
    stays at 0 when the coder session succeeds.

    Requirements: NS-REQ-5
    Test Spec: TS-NS-5
    """

    async def test_successful_resolution_with_mocked_engine(self) -> None:
        """Successful cycle increments conflicts_resolved, not conflicts_failed.

        Requirements: NS-REQ-5.1
        Test Spec: TS-NS-5
        """
        patches = [
            _PatchDetail(
                id="p1",
                status="conflict",
                branch_name="fix/p1",
                description="Fix auth bug",
                conflict_files=["auth.py"],
            )
        ]
        hub_client = _make_hub_client(
            patches=patches,
            rerere_entries=[_RerereEntry(path="auth.py")],
            submit_rebuild_return=_RebuildJob("job-1", "queued"),
        )
        config = _make_config(auto_resolve=True, max_resolve_retries=3)
        engine = _make_engine(coder_session_returns=None)
        monitor = _make_monitor(hub_client=hub_client, config=config, engine=engine)

        with (
            patch("afcore.workspace.git.fetch_remote", AsyncMock()),
            patch("afcore.workspace.git.checkout_branch", AsyncMock()),
            patch("afcore.workspace.git.push_to_remote", AsyncMock()),
            patch(
                "afcore.workspace.git.run_git",
                AsyncMock(return_value=(0, "diff output", "")),
            ),
        ):
            result = await monitor.run_cycle()

        assert result.conflicts_resolved == 1, "conflicts_resolved must be 1 after successful resolution"
        assert result.conflicts_failed == 0, "conflicts_failed must be 0 after successful resolution"
