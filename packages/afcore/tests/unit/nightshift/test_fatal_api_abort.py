"""Nightshift aborts on non-recoverable API errors instead of degrading.

When the Anthropic API rejects a call for a reason retrying cannot fix —
an exhausted credit balance, a rejected API key — every AI-backed step
would fail the same way.  The daemon must stop and say why rather than
logging a warning per issue and continuing with silently degraded
behaviour.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from afcore.core.errors import FatalAPIError
from afissues.protocol import IssueResult

FATAL = FatalAPIError("Anthropic API credit balance is too low — no further model calls can succeed.")

# A representative non-empty diff so check_staleness reaches the AI call
# path rather than short-circuiting (issue #53 guard).
_SAMPLE_DIFF = "diff --git a/foo.py b/foo.py\n-old\n+new\n"


def _make_issue(number: int, title: str = "Test issue") -> IssueResult:
    return IssueResult(
        number=number,
        title=title,
        html_url=f"https://github.com/example/repo/issues/{number}",
        body="Issue body",
    )


# ---------------------------------------------------------------------------
# staleness
# ---------------------------------------------------------------------------


class TestStalenessPropagatesFatal:
    """check_staleness must not absorb a fatal error into its fallback."""

    @pytest.mark.asyncio
    async def test_fatal_propagates_without_github_fallback(self) -> None:
        from afcore.nightshift.staleness import check_staleness

        platform = AsyncMock()
        platform.list_issues_by_label = AsyncMock(return_value=[])

        with patch(
            "afcore.nightshift.staleness._run_ai_staleness",
            AsyncMock(side_effect=FATAL),
        ):
            with pytest.raises(FatalAPIError):
                await check_staleness(
                    _make_issue(1),
                    [_make_issue(2)],
                    _SAMPLE_DIFF,
                    MagicMock(),
                    platform,
                )

        # The GitHub-only fallback never ran.
        assert platform.list_issues_by_label.await_count == 0

    @pytest.mark.asyncio
    async def test_ordinary_ai_failure_still_falls_back(self) -> None:
        """Regression guard: non-fatal AI errors keep the GitHub fallback."""
        from afcore.nightshift.staleness import check_staleness

        platform = AsyncMock()
        platform.list_issues_by_label = AsyncMock(return_value=[_make_issue(2)])

        with patch(
            "afcore.nightshift.staleness._run_ai_staleness",
            AsyncMock(side_effect=RuntimeError("no text content")),
        ):
            result = await check_staleness(
                _make_issue(1),
                [_make_issue(2)],
                _SAMPLE_DIFF,
                MagicMock(),
                platform,
            )

        assert result.obsolete_issues == []
        assert platform.list_issues_by_label.await_count == 1


# ---------------------------------------------------------------------------
# triage
# ---------------------------------------------------------------------------


class TestTriagePropagatesFatal:
    """run_batch_triage must not wrap a fatal error as a TriageError."""

    @pytest.mark.asyncio
    async def test_fatal_is_not_wrapped(self) -> None:
        from afcore.nightshift.triage import run_batch_triage

        with patch(
            "afcore.nightshift.triage._run_ai_triage",
            AsyncMock(side_effect=FATAL),
        ):
            with pytest.raises(FatalAPIError):
                await run_batch_triage([_make_issue(1)], [], MagicMock())

    @pytest.mark.asyncio
    async def test_ordinary_failure_still_becomes_triage_error(self) -> None:
        from afcore.nightshift.triage import TriageError, run_batch_triage

        with patch(
            "afcore.nightshift.triage._run_ai_triage",
            AsyncMock(side_effect=RuntimeError("boom")),
        ):
            with pytest.raises(TriageError):
                await run_batch_triage([_make_issue(1)], [], MagicMock())


# ---------------------------------------------------------------------------
# engine
# ---------------------------------------------------------------------------


def _make_engine(max_parallel: int = 1):
    from afcore.nightshift.engine import NightShiftEngine

    config = MagicMock()
    config.orchestrator.max_cost = None
    config.orchestrator.max_sessions = None
    config.night_shift.similarity_threshold = 0.85
    config.night_shift.max_parallel = max_parallel

    platform = AsyncMock()
    platform.list_issues_by_label = AsyncMock(return_value=[])
    return NightShiftEngine(config=config, platform=platform), platform


class TestEngineAbortsOnFatal:
    """A fatal error during issue processing aborts the whole check."""

    @pytest.mark.asyncio
    async def test_staleness_fatal_aborts_issue_check(self) -> None:
        engine, platform = _make_engine()
        issues = [_make_issue(1, "A"), _make_issue(2, "B")]
        platform.list_issues_by_label = AsyncMock(return_value=issues)

        with (
            patch("afcore.nightshift.engine.parse_text_references", return_value=[]),
            patch("afcore.nightshift.engine.build_graph", return_value=[1, 2]),
            patch.object(engine, "_process_fix", new=AsyncMock(return_value=True)),
            patch(
                "afcore.nightshift.engine.check_staleness",
                new=AsyncMock(side_effect=FATAL),
            ),
        ):
            with pytest.raises(FatalAPIError):
                await engine._run_issue_check()

    @pytest.mark.asyncio
    async def test_triage_fatal_aborts_issue_check(self) -> None:
        engine, platform = _make_engine()
        issues = [_make_issue(n) for n in (1, 2, 3)]
        platform.list_issues_by_label = AsyncMock(return_value=issues)

        with (
            patch("afcore.nightshift.engine.parse_text_references", return_value=[]),
            patch(
                "afcore.nightshift.engine.run_batch_triage",
                new=AsyncMock(side_effect=FATAL),
            ),
            patch.object(engine, "_process_fix", new=AsyncMock(return_value=None)),
        ):
            with pytest.raises(FatalAPIError):
                await engine._run_issue_check()

    @pytest.mark.asyncio
    async def test_ordinary_staleness_failure_does_not_abort(self) -> None:
        """Regression guard: a transient staleness failure is still absorbed."""
        engine, platform = _make_engine()
        issues = [_make_issue(1, "A"), _make_issue(2, "B")]
        platform.list_issues_by_label = AsyncMock(return_value=issues)

        with (
            patch("afcore.nightshift.engine.parse_text_references", return_value=[]),
            patch("afcore.nightshift.engine.build_graph", return_value=[1, 2]),
            patch.object(engine, "_process_fix", new=AsyncMock(return_value=True)),
            patch(
                "afcore.nightshift.engine.check_staleness",
                new=AsyncMock(side_effect=RuntimeError("transient")),
            ),
        ):
            await engine._run_issue_check()


# ---------------------------------------------------------------------------
# work stream
# ---------------------------------------------------------------------------


class TestStreamDoesNotBackOffOnFatal:
    """A fatal error skips the failure/backoff bookkeeping."""

    @pytest.mark.asyncio
    async def test_interval_unchanged(self) -> None:
        from afcore.nightshift.streams import EngineWorkStream

        engine = MagicMock()
        engine.run = AsyncMock(side_effect=FATAL)
        stream = EngineWorkStream("fix-pipeline", engine, "run", interval=900)

        with pytest.raises(FatalAPIError):
            await stream.run_once()

        assert stream.consecutive_failures == 0
        assert stream.interval == 900


# ---------------------------------------------------------------------------
# daemon
# ---------------------------------------------------------------------------


class TestDaemonAbortsOnFatal:
    """DaemonRunner stops every stream and re-raises the fatal error."""

    @pytest.mark.asyncio
    async def test_run_raises_and_shuts_down(self) -> None:
        from afcore.nightshift.daemon import DaemonRunner, SharedBudget

        stream = MagicMock()
        stream.name = "fix-pipeline"
        stream.interval = 1
        stream.enabled = True
        stream.run_once = AsyncMock(side_effect=FATAL)
        stream.shutdown = AsyncMock()

        config = MagicMock()

        runner = DaemonRunner(
            config=config,
            platform=None,
            streams=[stream],
            budget=SharedBudget(max_cost=None),
        )

        with pytest.raises(FatalAPIError):
            await runner.run()

        assert runner.fatal_error is FATAL
        assert runner.is_shutting_down is True
        # Streams are still shut down cleanly before the abort surfaces.
        assert stream.shutdown.await_count == 1
        # The cycle ran exactly once — no retry on the next interval.
        assert stream.run_once.await_count == 1
