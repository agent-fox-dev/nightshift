"""Unit tests for the cost-limit fix (issue #33).

``NightShiftEngine._check_cost_limit()`` used to stop dispatching new work
once 50% of ``orchestrator.max_cost`` was spent, silently halving the
daemon's configured throughput.  It now compares against the full limit,
matching ``SharedBudget.exceeded``, and a separate reservation guard in
``_fill_pool()`` refuses to start an issue whose worst-case cost would not
fit in what remains -- so the full limit is used without starting work
that cannot be paid for.

Test Spec: issue #33
Requirements: 61-REQ-1.E2, 61-REQ-9.3, NS-REQ-33 (issue #33, AC-1 through AC-4)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from afcore.core.config import AgentFoxConfig
from afcore.nightshift.daemon import SharedBudget
from afcore.nightshift.engine import NightShiftEngine
from afissues.protocol import IssueResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_real_engine(
    *,
    max_cost: float | None = None,
    max_retries: int = 2,
    max_budget_usd: float = 20.0,
    max_parallel: int = 1,
) -> tuple[NightShiftEngine, MagicMock]:
    """Build a NightShiftEngine against a real AgentFoxConfig.

    Uses a real config (rather than a bare MagicMock) so that
    ``resolve_max_budget()`` resolves actual floats when computing the
    per-issue cost estimate.
    """
    config = AgentFoxConfig()
    config.orchestrator.max_cost = max_cost
    config.orchestrator.max_retries = max_retries
    config.orchestrator.max_budget_usd = max_budget_usd
    config.night_shift.max_parallel = max_parallel

    platform = AsyncMock()
    platform.list_issues_by_label = AsyncMock(return_value=[])
    platform.fetch_github_relationships = AsyncMock(return_value=[])

    engine = NightShiftEngine(config=config, platform=platform)
    return engine, platform


def _make_issue(number: int, title: str = "Test issue") -> IssueResult:
    return IssueResult(
        number=number,
        title=title,
        html_url=f"https://github.com/example/repo/issues/{number}",
        body="Issue body",
    )


# ---------------------------------------------------------------------------
# AC-1, AC-2, AC-3: _check_cost_limit uses the full configured ceiling
# ---------------------------------------------------------------------------


class TestCheckCostLimitFullThreshold:
    """The 50% haircut is gone; the check matches SharedBudget.exceeded."""

    def test_not_triggered_below_max(self) -> None:
        """AC-1: max_cost=20.0, total_cost=12.0 -> False."""
        engine, _ = _make_real_engine(max_cost=20.0)
        engine.state.total_cost = 12.0
        assert engine._check_cost_limit() is False

    def test_not_triggered_well_past_the_old_50_percent_threshold(self) -> None:
        """At 95% spent the daemon must still be able to dispatch further work."""
        engine, _ = _make_real_engine(max_cost=20.0)
        engine.state.total_cost = 19.0
        assert engine._check_cost_limit() is False

    def test_triggered_at_max(self) -> None:
        """AC-2: max_cost=20.0, total_cost=20.0 -> True."""
        engine, _ = _make_real_engine(max_cost=20.0)
        engine.state.total_cost = 20.0
        assert engine._check_cost_limit() is True

    def test_triggered_above_max(self) -> None:
        engine, _ = _make_real_engine(max_cost=20.0)
        engine.state.total_cost = 25.0
        assert engine._check_cost_limit() is True

    def test_not_triggered_when_unset(self) -> None:
        """AC-3: max_cost=None -> always False regardless of total_cost."""
        engine, _ = _make_real_engine(max_cost=None)
        engine.state.total_cost = 999.0
        assert engine._check_cost_limit() is False

    def test_non_numeric_max_cost_does_not_raise(self) -> None:
        """A non-numeric config stand-in is treated like 'unset'."""
        engine, _ = _make_real_engine(max_cost=None)
        engine._config.orchestrator.max_cost = MagicMock()
        assert engine._check_cost_limit() is False

    @pytest.mark.parametrize(
        ("max_cost", "total_cost", "expected"),
        [
            (20.0, 19.99, False),
            (20.0, 20.0, True),
            (20.0, 25.0, True),
            (10.0, 0.0, False),
            (10.0, 10.0, True),
        ],
    )
    def test_consistent_with_shared_budget(self, max_cost: float, total_cost: float, expected: bool) -> None:
        """_check_cost_limit and SharedBudget.exceeded never disagree.

        Requirements: NS-REQ-33 (issue #33) -- the daemon must have a
        single definition of "budget exhausted".
        """
        engine, _ = _make_real_engine(max_cost=max_cost)
        engine.state.total_cost = total_cost
        budget = SharedBudget(max_cost=max_cost)
        budget._total_cost = total_cost

        assert engine._check_cost_limit() is expected
        assert budget.exceeded is expected


# ---------------------------------------------------------------------------
# _estimate_issue_cost
# ---------------------------------------------------------------------------


class TestEstimateIssueCost:
    """Worst-case per-issue estimate used by the AC-4 dispatch guard."""

    def test_sums_triage_coder_reviewer_across_retry_rounds(self) -> None:
        """triage + (1 + max_retries) * (coder + reviewer)."""
        engine, _ = _make_real_engine(max_retries=1, max_budget_usd=5.0)
        # 1 triage session + 2 rounds (1 + max_retries) of (coder + reviewer)
        # = 5.0 + 2 * (5.0 + 5.0) = 25.0
        assert engine._estimate_issue_cost() == pytest.approx(25.0)

    def test_zero_retries_is_one_round(self) -> None:
        engine, _ = _make_real_engine(max_retries=0, max_budget_usd=10.0)
        # 10.0 + 1 * (10.0 + 10.0) = 30.0
        assert engine._estimate_issue_cost() == pytest.approx(30.0)

    def test_returns_zero_when_global_budget_is_unlimited(self) -> None:
        """max_budget_usd=0 means unlimited -- no usable ceiling exists."""
        engine, _ = _make_real_engine(max_retries=2, max_budget_usd=0.0)
        assert engine._estimate_issue_cost() == 0.0

    def test_returns_zero_when_one_archetype_override_is_unlimited(self) -> None:
        """A per-archetype override to unlimited also removes the ceiling."""
        from afcore.core.config import PerArchetypeConfig

        engine, _ = _make_real_engine(max_retries=1, max_budget_usd=5.0)
        engine._config.archetypes.overrides["coder"] = PerArchetypeConfig(max_budget_usd=0.0)
        assert engine._estimate_issue_cost() == 0.0

    def test_non_numeric_config_returns_zero_without_raising(self) -> None:
        """A bare MagicMock config resolves to mock 'budgets' -- treated as unbounded."""
        config = MagicMock()
        config.archetypes.overrides.get.return_value = None
        platform = MagicMock()
        engine = NightShiftEngine(config=config, platform=platform)
        assert engine._estimate_issue_cost() == 0.0


# ---------------------------------------------------------------------------
# AC-4: reservation guard in _fill_pool via _reserve_budget_for_issue /
# _release_reserved_cost
# ---------------------------------------------------------------------------


class TestReserveBudgetForIssue:
    """Reservation lifecycle backing the AC-4 dispatch guard."""

    def test_true_when_max_cost_unset(self) -> None:
        engine, _ = _make_real_engine(max_cost=None, max_budget_usd=1_000_000.0)
        assert engine._reserve_budget_for_issue() is True
        assert engine._reserved_cost == 0.0

    def test_true_when_estimate_is_zero(self) -> None:
        engine, _ = _make_real_engine(max_cost=20.0, max_budget_usd=0.0)
        assert engine._reserve_budget_for_issue() is True
        assert engine._reserved_cost == 0.0

    def test_false_when_remaining_below_estimate(self) -> None:
        """AC-4: a remaining budget smaller than the estimate refuses reservation."""
        engine, _ = _make_real_engine(max_cost=20.0, max_retries=1, max_budget_usd=5.0)
        engine.state.total_cost = 10.0
        # estimate = 5 + 2*(5+5) = 25; remaining = 20 - 10 = 10 < 25
        assert engine._reserve_budget_for_issue() is False
        assert engine._reserved_cost == 0.0

    def test_true_and_accumulates_when_it_fits(self) -> None:
        engine, _ = _make_real_engine(max_cost=100.0, max_retries=1, max_budget_usd=5.0)
        # estimate = 25 per issue
        assert engine._reserve_budget_for_issue() is True
        assert engine._reserved_cost == pytest.approx(25.0)
        assert engine._reserve_budget_for_issue() is True
        assert engine._reserved_cost == pytest.approx(50.0)

    def test_second_reservation_refused_once_in_flight_exhausts_budget(self) -> None:
        """Concurrently in-flight reservations count against remaining budget."""
        engine, _ = _make_real_engine(max_cost=30.0, max_retries=1, max_budget_usd=5.0)
        # estimate = 25 per issue; first reservation fits (25 <= 30).
        assert engine._reserve_budget_for_issue() is True
        # Second would need another 25, but only 5 remains (30 - 25).
        assert engine._reserve_budget_for_issue() is False
        assert engine._reserved_cost == pytest.approx(25.0)

    def test_release_restores_reserved_cost(self) -> None:
        engine, _ = _make_real_engine(max_cost=100.0, max_retries=1, max_budget_usd=5.0)
        engine._reserve_budget_for_issue()
        assert engine._reserved_cost == pytest.approx(25.0)
        engine._release_reserved_cost()
        assert engine._reserved_cost == pytest.approx(0.0)

    def test_release_is_a_noop_when_nothing_was_reserved(self) -> None:
        engine, _ = _make_real_engine(max_cost=None)
        engine._release_reserved_cost()
        assert engine._reserved_cost == 0.0

    def test_release_never_goes_negative(self) -> None:
        """Releasing without a matching reservation clamps at zero."""
        engine, _ = _make_real_engine(max_cost=100.0, max_retries=1, max_budget_usd=5.0)
        engine._release_reserved_cost()
        assert engine._reserved_cost == 0.0


# ---------------------------------------------------------------------------
# AC-4, end-to-end: _fill_pool does not dispatch an issue that cannot be
# paid for, and releases the reservation once the issue completes.
# ---------------------------------------------------------------------------


class TestDispatchGuardEndToEnd:
    """Drives dispatch through _run_issue_check, as test_parallel_dispatch.py does."""

    async def test_issue_not_dispatched_when_estimate_exceeds_remaining_budget(self) -> None:
        """AC-4: the issue is never turned into a task when it cannot fit."""
        # estimate = 5 + 2*(5+5) = 25; remaining budget is only 10.
        engine, platform = _make_real_engine(max_cost=10.0, max_retries=1, max_budget_usd=5.0, max_parallel=3)
        issues = [_make_issue(1)]
        dispatched: list[int] = []

        async def fake_process_fix(iss, **_kwargs) -> None:
            dispatched.append(iss.number)

        with (
            patch("afcore.nightshift.engine.parse_text_references", return_value=[]),
            patch("afcore.nightshift.engine.fetch_github_relationships", new=AsyncMock(return_value=[])),
            patch("afcore.nightshift.engine.build_graph", return_value=[1]),
            patch.object(engine, "_process_fix", side_effect=fake_process_fix),
        ):
            platform.list_issues_by_label = AsyncMock(return_value=issues)
            await engine._run_issue_check()

        assert dispatched == []
        assert engine._reserved_cost == 0.0

    async def test_issue_dispatched_and_reservation_released_on_completion(self) -> None:
        """An issue that fits is dispatched, and its reservation is released."""
        engine, platform = _make_real_engine(max_cost=100.0, max_retries=1, max_budget_usd=5.0, max_parallel=1)
        issues = [_make_issue(1)]
        dispatched: list[int] = []
        reserved_during_run: list[float] = []

        async def fake_process_fix(iss, **_kwargs) -> None:
            dispatched.append(iss.number)
            reserved_during_run.append(engine._reserved_cost)

        with (
            patch("afcore.nightshift.engine.parse_text_references", return_value=[]),
            patch("afcore.nightshift.engine.fetch_github_relationships", new=AsyncMock(return_value=[])),
            patch("afcore.nightshift.engine.build_graph", return_value=[1]),
            patch.object(engine, "_process_fix", side_effect=fake_process_fix),
        ):
            platform.list_issues_by_label = AsyncMock(return_value=issues)
            await engine._run_issue_check()

        assert dispatched == [1]
        # While in flight, the estimate (25.0) was reserved.
        assert reserved_during_run == [pytest.approx(25.0)]
        # Released once the issue completed.
        assert engine._reserved_cost == 0.0

    async def test_reservation_released_when_process_fix_raises(self) -> None:
        """A failing fix still releases its reservation (no leak)."""
        engine, platform = _make_real_engine(max_cost=100.0, max_retries=1, max_budget_usd=5.0, max_parallel=1)
        issues = [_make_issue(1)]

        async def failing_process_fix(iss, **_kwargs) -> None:
            raise RuntimeError("boom")

        with (
            patch("afcore.nightshift.engine.parse_text_references", return_value=[]),
            patch("afcore.nightshift.engine.fetch_github_relationships", new=AsyncMock(return_value=[])),
            patch("afcore.nightshift.engine.build_graph", return_value=[1]),
            patch.object(engine, "_process_fix", side_effect=failing_process_fix),
        ):
            platform.list_issues_by_label = AsyncMock(return_value=issues)
            await engine._run_issue_check()

        assert engine._reserved_cost == 0.0

    async def test_dispatch_allowed_past_the_old_50_percent_threshold(self) -> None:
        """An issue is still dispatched at 60% spend when it fits in the rest.

        Regression guard for the original bug: the daemon must be able to
        use the full configured budget, not stop at half.
        """
        engine, platform = _make_real_engine(max_cost=20.0, max_retries=0, max_budget_usd=2.0, max_parallel=1)
        engine.state.total_cost = 12.0  # 60% spent
        issues = [_make_issue(1)]
        dispatched: list[int] = []

        async def fake_process_fix(iss, **_kwargs) -> None:
            dispatched.append(iss.number)

        with (
            patch("afcore.nightshift.engine.parse_text_references", return_value=[]),
            patch("afcore.nightshift.engine.fetch_github_relationships", new=AsyncMock(return_value=[])),
            patch("afcore.nightshift.engine.build_graph", return_value=[1]),
            patch.object(engine, "_process_fix", side_effect=fake_process_fix),
        ):
            platform.list_issues_by_label = AsyncMock(return_value=issues)
            await engine._run_issue_check()

        # estimate = 2 + 1*(2+2) = 4; remaining = 20 - 12 = 8 >= 4 -> dispatched.
        assert dispatched == [1]
