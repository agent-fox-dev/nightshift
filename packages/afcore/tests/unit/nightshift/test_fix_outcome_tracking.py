"""Unit tests for fix-outcome tracking (issue #50).

``FixPipeline.process_issue()`` used to return an indistinguishable
``FixMetrics`` on every terminal path -- including the ones where the fix
demonstrably failed (retries exhausted, no changes, an internal
exception, or an empty issue body). ``NightShiftEngine._process_fix()``
inferred success from "the call did not raise", so a failed fix was
recorded as ``issues_fixed += 1``, ``FIX_COMPLETE`` was emitted, and the
post-fix staleness sweep ran and could close other, unrelated issues.

``FixMetrics`` now carries an explicit ``outcome`` field (default
``"failed"``, fails safe) that ``process_issue()`` sets on every return
path, and ``_process_fix()``/``_run_one()`` use that value instead of
"did not raise".

Test Spec: issue #50
Requirements: NS-REQ-50 (issue #50, AC-1 through AC-5)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from afaudit.events import AuditEventType
from afcore.nightshift.engine import NightShiftEngine
from afcore.nightshift.fix_pipeline import FixMetrics
from afissues.protocol import IssueResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config() -> MagicMock:
    config = MagicMock()
    config.orchestrator.max_cost = None
    config.orchestrator.max_sessions = None
    config.night_shift.issue_check_interval = 900
    config.archetypes.overrides.get.return_value = None
    return config


def _make_issue(number: int, title: str = "Test issue") -> IssueResult:
    return IssueResult(
        number=number,
        title=title,
        html_url=f"https://github.com/example/repo/issues/{number}",
        body="Issue body",
    )


def _mock_metrics(outcome: str, **overrides: object) -> MagicMock:
    defaults = dict(
        sessions_run=1,
        input_tokens=10,
        output_tokens=5,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
        cost_usd=0.5,
        outcome=outcome,
    )
    defaults.update(overrides)
    return MagicMock(**defaults)


async def _run_process_fix(engine: NightShiftEngine, issue: IssueResult, metrics: object) -> bool:
    """Drive engine._process_fix with a stubbed FixPipeline.process_issue."""
    with patch("afcore.nightshift.engine.FixPipeline") as mock_cls:
        mock_pipeline = MagicMock()
        mock_pipeline.process_issue = AsyncMock(return_value=metrics)
        mock_cls.return_value = mock_pipeline
        return await engine._process_fix(issue, issue_body="details")


# ---------------------------------------------------------------------------
# FixMetrics.outcome defaults
# ---------------------------------------------------------------------------


class TestFixMetricsOutcomeDefault:
    """A FixMetrics that forgets to set outcome fails safe."""

    def test_default_outcome_is_failed(self) -> None:
        assert FixMetrics().outcome == "failed"


# ---------------------------------------------------------------------------
# AC-2, AC-3: _process_fix gates issues_fixed / IssueOutcome / audit event
# on the real outcome, not "did not raise".
# ---------------------------------------------------------------------------


class TestProcessFixGatesOnOutcome:
    """Only outcome == 'fixed' is treated as a success by the engine."""

    @pytest.mark.asyncio
    async def test_fixed_outcome_increments_issues_fixed(self) -> None:
        engine = NightShiftEngine(config=_make_config(), platform=AsyncMock())
        issue = _make_issue(1)

        result = await _run_process_fix(engine, issue, _mock_metrics("fixed"))

        assert result is True
        assert engine.state.issues_fixed == 1
        assert engine.state.issue_outcomes[-1].outcome == "fixed"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("outcome", ["failed", "no_changes", "pr_created", "skipped"])
    async def test_non_fixed_outcomes_do_not_increment_issues_fixed(self, outcome: str) -> None:
        """AC-2: a fix that was not integrated does not count as fixed.

        Covers retries-exhausted ("failed"), no commits produced
        ("no_changes"), a PR opened but not yet merged ("pr_created"), and
        an empty issue body ("skipped") -- none of these landed a fix.
        """
        engine = NightShiftEngine(config=_make_config(), platform=AsyncMock())
        issue = _make_issue(2)

        result = await _run_process_fix(engine, issue, _mock_metrics(outcome))

        assert result is False
        assert engine.state.issues_fixed == 0
        assert engine.state.issue_outcomes[-1].outcome == "failed"

    @pytest.mark.asyncio
    async def test_fixed_outcome_emits_fix_complete(self) -> None:
        """AC-3 (positive case): a genuine fix emits FIX_COMPLETE."""
        engine = NightShiftEngine(config=_make_config(), platform=AsyncMock())
        issue = _make_issue(3)

        with patch("afcore.nightshift.engine._emit_audit_event") as mock_emit:
            await _run_process_fix(engine, issue, _mock_metrics("fixed"))

        emitted = [call.args[2] for call in mock_emit.call_args_list]
        assert AuditEventType.FIX_COMPLETE in emitted
        assert AuditEventType.FIX_FAILED not in emitted

    @pytest.mark.asyncio
    async def test_retries_exhausted_emits_fix_failed_not_fix_complete(self) -> None:
        """AC-3: a fix that was not integrated emits FIX_FAILED, not FIX_COMPLETE."""
        engine = NightShiftEngine(config=_make_config(), platform=AsyncMock())
        issue = _make_issue(4)

        with patch("afcore.nightshift.engine._emit_audit_event") as mock_emit:
            await _run_process_fix(engine, issue, _mock_metrics("failed"))

        emitted = [call.args[2] for call in mock_emit.call_args_list]
        assert AuditEventType.FIX_FAILED in emitted
        assert AuditEventType.FIX_COMPLETE not in emitted

    @pytest.mark.asyncio
    async def test_pipeline_raising_is_reported_as_not_fixed(self) -> None:
        """The pre-existing raise path still reports failure correctly."""
        engine = NightShiftEngine(config=_make_config(), platform=AsyncMock())
        issue = _make_issue(5)

        with patch("afcore.nightshift.engine.FixPipeline") as mock_cls:
            mock_pipeline = MagicMock()
            mock_pipeline.process_issue = AsyncMock(side_effect=RuntimeError("boom"))
            mock_cls.return_value = mock_pipeline
            result = await engine._process_fix(issue, issue_body="details")

        assert result is False
        assert engine.state.issues_fixed == 0


# ---------------------------------------------------------------------------
# AC-1, AC-4: _run_one / staleness sweep gated on the real outcome
# ---------------------------------------------------------------------------


class TestDispatchGatesStalenessOnRealOutcome:
    """Driving dispatch end-to-end through _run_issue_check."""

    async def _run_dispatch(self, engine, platform, issues, outcomes_by_number):
        async def fake_process_issue(iss, **_kwargs):
            outcome = outcomes_by_number[iss.number]
            if outcome == "raise":
                raise RuntimeError("boom")
            return _mock_metrics(outcome)

        with (
            patch("afcore.nightshift.engine.parse_text_references", return_value=[]),
            patch("afcore.nightshift.engine.fetch_github_relationships", new=AsyncMock(return_value=[])),
            patch("afcore.nightshift.engine.build_graph", return_value=list(outcomes_by_number)),
            patch("afcore.nightshift.engine.FixPipeline") as mock_cls,
        ):
            mock_pipeline = MagicMock()
            mock_pipeline.process_issue = AsyncMock(side_effect=fake_process_issue)
            mock_cls.return_value = mock_pipeline
            platform.list_issues_by_label = AsyncMock(return_value=issues)
            await engine._run_issue_check()

    @pytest.mark.asyncio
    async def test_raising_pipeline_runs_no_staleness_check(self) -> None:
        """AC-1: pipeline raises -> _run_one reports failure, no staleness check."""
        config = _make_config()
        config.night_shift.max_parallel = 1
        platform = AsyncMock()
        engine = NightShiftEngine(config=config, platform=platform)
        issue = _make_issue(10)

        with patch("afcore.nightshift.engine.check_staleness", new=AsyncMock()) as mock_staleness:
            await self._run_dispatch(engine, platform, [issue], {10: "raise"})

        mock_staleness.assert_not_awaited()
        assert engine.state.issues_fixed == 0

    @pytest.mark.asyncio
    async def test_first_issue_failing_never_closes_the_second(self) -> None:
        """AC-4: two af:fix issues where the first fails -> close_issue never
        called for the second."""
        config = _make_config()
        config.night_shift.max_parallel = 1
        platform = AsyncMock()
        platform.close_issue = AsyncMock()
        engine = NightShiftEngine(config=config, platform=platform)
        issue_a = _make_issue(20)
        issue_b = _make_issue(21)

        # Even if a staleness check somehow ran, force it to say #21 is
        # obsolete -- the guard under test is that it must never be asked.
        with patch(
            "afcore.nightshift.engine.check_staleness",
            new=AsyncMock(return_value=MagicMock(obsolete_issues=[21], rationale={})),
        ) as mock_staleness:
            await self._run_dispatch(engine, platform, [issue_a, issue_b], {20: "failed", 21: "fixed"})

        mock_staleness.assert_not_awaited()
        platform.close_issue.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_genuine_fix_runs_staleness_sweep_and_counts_as_fixed(self) -> None:
        """AC-5: a genuinely integrated fix keeps existing success-path behavior."""
        config = _make_config()
        config.night_shift.max_parallel = 1
        platform = AsyncMock()
        platform.close_issue = AsyncMock()
        engine = NightShiftEngine(config=config, platform=platform)
        issue_a = _make_issue(30)
        issue_b = _make_issue(31)

        with patch(
            "afcore.nightshift.engine.check_staleness",
            new=AsyncMock(return_value=MagicMock(obsolete_issues=[], rationale={})),
        ) as mock_staleness:
            await self._run_dispatch(engine, platform, [issue_a, issue_b], {30: "fixed", 31: "fixed"})

        assert mock_staleness.await_count >= 1
        assert engine.state.issues_fixed == 2
