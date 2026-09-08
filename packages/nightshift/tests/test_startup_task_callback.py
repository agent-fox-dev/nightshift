"""Tests for wrap_task_callback — the JSONL bridge in _startup.py.

Validates that the callback correctly forwards TaskEvent.duration_s,
pairs every terminal event with a task_started, and always invokes
the UI callback exactly once.

Requirements: NS-REQ-1, NS-REQ-2, NS-REQ-3, NS-REQ-4, NS-REQ-5
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock


@dataclass(frozen=True, slots=True)
class _FakeTaskEvent:
    """Minimal stand-in for ``afcore.ui.progress.TaskEvent``."""

    node_id: str
    status: str
    duration_s: float = 0.0
    error_message: str | None = None


def _make_callback():
    """Build a wrap_task_callback wired to mock collaborators.

    Returns ``(cb, om, ui_cb)`` where *cb* is the inner ``_cb``,
    *om* is the mock OutputManager, and *ui_cb* is the mock UI
    callback.
    """
    from nightshift._startup import wrap_task_callback

    om = MagicMock()
    om.json_mode = True

    progress = MagicMock()
    ui_cb = MagicMock()
    progress.task_callback = ui_cb

    cb = wrap_task_callback(progress, om)
    return cb, om, ui_cb


def _emitted_events(om: MagicMock) -> list[dict]:
    """Extract all progress events emitted through OutputManager."""
    return [call[0][0] for call in om.emit_progress.call_args_list]


# ── NS-REQ-1 / AC-1: duration_s is forwarded verbatim ──────────────


class TestCompletedDurationForwarded:
    """TS-NS-1: task_completed carries the real duration_s from TaskEvent."""

    def test_duration_matches_event(self) -> None:
        cb, om, _ = _make_callback()
        ev = _FakeTaskEvent(node_id="fix-issue-X:1:coder", status="completed", duration_s=421.6)
        cb(ev)

        events = _emitted_events(om)
        completed = [e for e in events if e["event"] == "task_completed"]
        assert len(completed) == 1
        assert completed[0]["duration_s"] == 421.6

    def test_zero_duration_forwarded(self) -> None:
        """A legitimate zero duration is forwarded, not replaced."""
        cb, om, _ = _make_callback()
        ev = _FakeTaskEvent(node_id="n", status="completed", duration_s=0.0)
        cb(ev)

        events = _emitted_events(om)
        completed = [e for e in events if e["event"] == "task_completed"]
        assert completed[0]["duration_s"] == 0.0


# ── NS-REQ-2 / AC-2: task_failed is preceded by task_started ───────


class TestFailedPrecededByStarted:
    """TS-NS-2: every task_failed has a preceding task_started for same node_id."""

    def test_failed_has_started(self) -> None:
        cb, om, _ = _make_callback()
        ev = _FakeTaskEvent(
            node_id="fix-issue-X:1:coder",
            status="failed",
            duration_s=12.0,
            error_message="timeout",
        )
        cb(ev)

        events = _emitted_events(om)
        started_ids = [e["node_id"] for e in events if e["event"] == "task_started"]
        failed_ids = [e["node_id"] for e in events if e["event"] == "task_failed"]
        assert "fix-issue-X:1:coder" in started_ids
        assert "fix-issue-X:1:coder" in failed_ids

        # started must come before failed in the sequence
        started_idx = next(i for i, e in enumerate(events) if e["event"] == "task_started")
        failed_idx = next(i for i, e in enumerate(events) if e["event"] == "task_failed")
        assert started_idx < failed_idx

    def test_failed_carries_error(self) -> None:
        cb, om, _ = _make_callback()
        ev = _FakeTaskEvent(node_id="n", status="failed", error_message="something broke")
        cb(ev)

        events = _emitted_events(om)
        failed = [e for e in events if e["event"] == "task_failed"]
        assert failed[0]["error"] == "something broke"


# ── NS-REQ-3 / AC-3: ui_cb invoked exactly once ────────────────────


class TestUiCallbackInvokedOnce:
    """TS-NS-3: ui_cb is called exactly once per TaskEvent."""

    def test_completed_calls_ui_once(self) -> None:
        cb, _, ui_cb = _make_callback()
        ev = _FakeTaskEvent(node_id="n", status="completed", duration_s=1.0)
        cb(ev)
        assert ui_cb.call_count == 1

    def test_failed_calls_ui_once(self) -> None:
        cb, _, ui_cb = _make_callback()
        ev = _FakeTaskEvent(node_id="n", status="failed", error_message="err")
        cb(ev)
        assert ui_cb.call_count == 1

    def test_other_status_calls_ui_once(self) -> None:
        cb, _, ui_cb = _make_callback()
        ev = _FakeTaskEvent(node_id="n", status="blocked")
        cb(ev)
        assert ui_cb.call_count == 1


# ── NS-REQ-5: ProgressDisplay optional duration_s parameter ────────


class TestProgressDisplayDurationParam:
    """TS-NS-5: task_completed/task_failed accept optional duration_s."""

    def test_task_completed_with_explicit_duration(self) -> None:
        from afcore.io.progress import ProgressDisplay

        om = MagicMock()
        pd = ProgressDisplay(output_manager=om, json_mode=True)
        pd.task_completed(node_id="n", duration_s=99.0)

        call = om.emit_progress.call_args
        event_data = call[0][0]
        assert event_data["duration_s"] == 99.0

    def test_task_completed_without_duration_falls_back(self) -> None:
        from afcore.io.progress import ProgressDisplay

        om = MagicMock()
        pd = ProgressDisplay(output_manager=om, json_mode=True)
        pd.task_started(node_id="n")
        pd.task_completed(node_id="n")

        events = [c[0][0] for c in om.emit_progress.call_args_list]
        completed = [e for e in events if e["event"] == "task_completed"]
        # Computed duration should be a non-negative float (near zero)
        assert isinstance(completed[0]["duration_s"], float)
        assert completed[0]["duration_s"] >= 0

    def test_task_failed_with_explicit_duration(self) -> None:
        from afcore.io.progress import ProgressDisplay

        om = MagicMock()
        pd = ProgressDisplay(output_manager=om, json_mode=True)
        pd.task_failed(node_id="n", error="err", duration_s=55.5)

        call = om.emit_progress.call_args
        event_data = call[0][0]
        assert event_data["duration_s"] == 55.5

    def test_task_failed_without_duration_omits_key(self) -> None:
        from afcore.io.progress import ProgressDisplay

        om = MagicMock()
        pd = ProgressDisplay(output_manager=om, json_mode=True)
        pd.task_failed(node_id="n", error="err")

        call = om.emit_progress.call_args
        event_data = call[0][0]
        assert "duration_s" not in event_data


# ── Edge: started only emitted once per node_id ─────────────────────


class TestStartedEmittedOncePerNode:
    """Ensure task_started is emitted only once per node_id, not duplicated."""

    def test_completed_after_other_status(self) -> None:
        """A node seen first via 'blocked', then 'completed', gets one task_started."""
        cb, om, _ = _make_callback()
        cb(_FakeTaskEvent(node_id="n", status="blocked"))
        cb(_FakeTaskEvent(node_id="n", status="completed", duration_s=5.0))

        events = _emitted_events(om)
        started = [e for e in events if e["event"] == "task_started" and e["node_id"] == "n"]
        assert len(started) == 1

    def test_different_nodes_get_separate_starts(self) -> None:
        cb, om, _ = _make_callback()
        cb(_FakeTaskEvent(node_id="a", status="completed", duration_s=1.0))
        cb(_FakeTaskEvent(node_id="b", status="completed", duration_s=2.0))

        events = _emitted_events(om)
        started = [e for e in events if e["event"] == "task_started"]
        assert len(started) == 2
        assert {e["node_id"] for e in started} == {"a", "b"}


# ── Edge: non-json mode returns raw task_callback ───────────────────


class TestNonJsonModePassthrough:
    """When json_mode is False, wrap_task_callback returns the raw callback."""

    def test_returns_raw_callback(self) -> None:
        from nightshift._startup import wrap_task_callback

        om = MagicMock()
        om.json_mode = False
        progress = MagicMock()
        sentinel = object()
        progress.task_callback = sentinel

        result = wrap_task_callback(progress, om)
        assert result is sentinel
