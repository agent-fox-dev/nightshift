"""Unit tests for issue #19: cost model consistency.

Verifies that per-session audit events, DB rows, and per-issue cost
calculations all use the correct model ID (including mode overrides)
and that FixMetrics accumulates costs per-session.

Test Spec: TS-NS-1 through TS-NS-5
Requirements: NS-REQ-1, NS-REQ-2, NS-REQ-3, NS-REQ-4, NS-REQ-5
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config() -> MagicMock:
    """Build a mock AgentFoxConfig with minimum required attributes."""
    config = MagicMock()
    config.platform.type = "github"
    config.orchestrator.max_cost = None
    config.orchestrator.max_sessions = None
    config.orchestrator.max_retries = 3
    config.orchestrator.max_budget_usd = 0.0
    config.archetypes = MagicMock()
    config.archetypes.overrides = {}
    config.models = MagicMock()
    config.models.coding = "STANDARD"
    config.pricing = MagicMock()
    config.pricing.models = {}
    config.theme = None
    return config


def _make_real_config() -> MagicMock:
    """Build a config using real ArchetypesConfig so mode resolution works."""
    from afcore.core.config import ArchetypesConfig, PricingConfig

    config = MagicMock()
    config.platform.type = "github"
    config.orchestrator.max_cost = None
    config.orchestrator.max_sessions = None
    config.orchestrator.max_retries = 3
    config.orchestrator.max_budget_usd = 0.0
    config.archetypes = ArchetypesConfig()
    config.models = None  # Use built-in tier_defaults
    config.pricing = PricingConfig()
    config.theme = None
    config.workspace.integration_branch = "develop"
    config.workspace.merge_strategy = "direct"
    config.night_shift.push_fix_branch = False
    return config


def _make_workspace():
    from afcore.workspace import WorkspaceInfo

    return WorkspaceInfo(
        path=Path("/tmp/mock-worktree"),
        branch="fix/test-branch",
        spec_name="fix-issue-42",
        task_group=0,
    )


def _make_spec():
    from afcore.nightshift.spec_builder import InMemorySpec

    spec = MagicMock(spec=InMemorySpec)
    spec.system_context = "test context"
    spec.task_prompt = "fix the bug"
    spec.issue_number = 42
    spec.title = "test issue title"
    spec.branch_name = "fix/issue-42"
    return spec


def _mock_session_outcome(
    input_tokens: int = 1000,
    output_tokens: int = 200,
    cache_read_input_tokens: int = 0,
    cache_creation_input_tokens: int = 0,
    duration_ms: int = 5000,
    status: str = "completed",
    response: str = "",
    error_message: str | None = None,
) -> MagicMock:
    outcome = MagicMock()
    outcome.input_tokens = input_tokens
    outcome.output_tokens = output_tokens
    outcome.cache_read_input_tokens = cache_read_input_tokens
    outcome.cache_creation_input_tokens = cache_creation_input_tokens
    outcome.duration_ms = duration_ms
    outcome.status = status
    outcome.response = response
    outcome.error_message = error_message
    return outcome


# ---------------------------------------------------------------------------
# TS-NS-1: Reviewer session audit events carry the ADVANCED-tier model ID
# Requirement: NS-REQ-1
# ---------------------------------------------------------------------------


class TestReviewerSessionModelId:
    """TS-NS-1: Reviewer in fix-review mode uses ADVANCED model in events."""

    def test_emit_session_event_reviewer_uses_advanced_model(self) -> None:
        """_emit_session_event with mode='fix-review' resolves ADVANCED model.

        The reviewer archetype's fix-review mode maps to model_tier=ADVANCED.
        The session.complete payload and _record_session_to_db must carry the
        ADVANCED model ID (claude-opus-4-6 by default), not STANDARD.
        """
        from afcore.core.models import resolve_model
        from afcore.nightshift.fix_pipeline import FixPipeline

        config = _make_real_config()
        pipeline = FixPipeline(config, AsyncMock())
        pipeline._run_id = "test-run"

        outcome = _mock_session_outcome(input_tokens=500, output_tokens=100)

        advanced_model = resolve_model("ADVANCED")
        standard_model = resolve_model("STANDARD")

        with patch(
            "afcore.nightshift.fix_pipeline.emit_audit_event",
        ) as mock_emit:
            cost = pipeline._emit_session_event(
                outcome,
                "reviewer",
                "test-run",
                node_id="fix-issue-42:0:reviewer",
                mode="fix-review",
            )

        # Verify the model_id in the payload is ADVANCED, not STANDARD
        complete_calls = [c for c in mock_emit.call_args_list if "SESSION_COMPLETE" in str(c)]
        assert len(complete_calls) >= 1, "Expected session.complete event"
        payload = complete_calls[0].kwargs.get("payload", {})
        assert payload["model_id"] == advanced_model, (
            f"Expected model_id={advanced_model} (ADVANCED) but got {payload['model_id']}"
        )
        assert payload["model_id"] != standard_model, "Reviewer in fix-review mode must NOT use STANDARD model"
        assert cost >= 0.0, "Cost must be non-negative"

    def test_record_session_to_db_reviewer_uses_advanced_model(self) -> None:
        """_record_session_to_db receives the ADVANCED model_id from _emit_session_event."""
        from afcore.core.models import resolve_model
        from afcore.nightshift.fix_pipeline import FixPipeline

        config = _make_real_config()
        pipeline = FixPipeline(config, AsyncMock())
        pipeline._run_id = "test-run"

        outcome = _mock_session_outcome(input_tokens=500, output_tokens=100)

        advanced_model = resolve_model("ADVANCED")

        with (
            patch("afcore.nightshift.fix_pipeline.emit_audit_event"),
            patch.object(pipeline, "_record_session_to_db") as mock_record,
        ):
            pipeline._emit_session_event(
                outcome,
                "reviewer",
                "test-run",
                node_id="fix-issue-42:0:reviewer",
                mode="fix-review",
            )

        mock_record.assert_called_once()
        call_kwargs = mock_record.call_args.kwargs
        assert call_kwargs["model_id"] == advanced_model, (
            f"Expected model_id={advanced_model} in DB record, got {call_kwargs.get('model_id')}"
        )

    def test_coder_session_uses_standard_model(self) -> None:
        """_emit_session_event with mode='fix' resolves STANDARD model for coder."""
        from afcore.core.models import resolve_model
        from afcore.nightshift.fix_pipeline import FixPipeline

        config = _make_real_config()
        pipeline = FixPipeline(config, AsyncMock())
        pipeline._run_id = "test-run"

        outcome = _mock_session_outcome()

        standard_model = resolve_model("STANDARD")

        with patch("afcore.nightshift.fix_pipeline.emit_audit_event") as mock_emit:
            pipeline._emit_session_event(
                outcome,
                "coder",
                "test-run",
                node_id="fix-issue-42:0:coder",
                mode="fix",
            )

        complete_calls = [c for c in mock_emit.call_args_list if "SESSION_COMPLETE" in str(c)]
        assert len(complete_calls) >= 1
        payload = complete_calls[0].kwargs.get("payload", {})
        assert payload["model_id"] == standard_model

    def test_emit_session_event_returns_cost(self) -> None:
        """_emit_session_event returns the computed cost as a float."""
        from afcore.nightshift.fix_pipeline import FixPipeline

        config = _make_real_config()
        pipeline = FixPipeline(config, AsyncMock())
        pipeline._run_id = "test-run"

        outcome = _mock_session_outcome(input_tokens=1000, output_tokens=200)

        with patch("afcore.nightshift.fix_pipeline.emit_audit_event"):
            cost = pipeline._emit_session_event(
                outcome,
                "coder",
                "test-run",
                node_id="fix-issue-42:0:coder",
                mode="fix",
            )

        assert isinstance(cost, float)
        assert cost >= 0.0


# ---------------------------------------------------------------------------
# TS-NS-2: IssueOutcome.cost_usd uses accumulated per-session costs
# Requirement: NS-REQ-2
# ---------------------------------------------------------------------------


class TestCalculateFixCostUsesAccumulatedCost:
    """TS-NS-2: _calculate_fix_cost returns FixMetrics.cost_usd."""

    def test_calculate_fix_cost_returns_accumulated_cost(self) -> None:
        """_calculate_fix_cost returns metrics.cost_usd, not a re-calculated value."""
        from afcore.nightshift.engine import NightShiftEngine
        from afcore.nightshift.fix_pipeline import FixMetrics

        config = _make_config()
        engine = NightShiftEngine(config, MagicMock())

        metrics = FixMetrics(
            input_tokens=5000,
            output_tokens=1000,
            sessions_run=3,
            cost_usd=0.42,
        )

        result = engine._calculate_fix_cost(metrics)
        assert result == 0.42, f"Expected _calculate_fix_cost to return accumulated cost_usd=0.42, got {result}"

    def test_calculate_fix_cost_no_longer_reprices_at_advanced(self) -> None:
        """_calculate_fix_cost does NOT call resolve_model or calculate_cost."""
        from afcore.nightshift.engine import NightShiftEngine
        from afcore.nightshift.fix_pipeline import FixMetrics

        config = _make_config()
        engine = NightShiftEngine(config, MagicMock())

        metrics = FixMetrics(cost_usd=1.23)

        with (
            patch("afcore.core.models.resolve_model") as mock_resolve,
            patch("afcore.core.models.calculate_cost") as mock_calc,
        ):
            result = engine._calculate_fix_cost(metrics)

        mock_resolve.assert_not_called()
        mock_calc.assert_not_called()
        assert result == 1.23

    def test_fix_metrics_has_cost_usd_field(self) -> None:
        """FixMetrics includes a cost_usd field defaulting to 0.0."""
        from afcore.nightshift.fix_pipeline import FixMetrics

        metrics = FixMetrics()
        assert hasattr(metrics, "cost_usd")
        assert metrics.cost_usd == 0.0

    def test_accumulated_cost_lower_than_all_advanced_pricing(self) -> None:
        """With mixed tiers the accumulated cost is lower than pricing everything at ADVANCED.

        Simulates a pipeline with triage (STANDARD) + coder (STANDARD) + reviewer (ADVANCED).
        The accumulated per-session cost should be less than pricing all tokens at ADVANCED.
        """
        from afcore.core.config import PricingConfig
        from afcore.core.models import calculate_cost, resolve_model
        from afcore.nightshift.fix_pipeline import FixMetrics

        pricing = PricingConfig()
        standard_model = resolve_model("STANDARD")
        advanced_model = resolve_model("ADVANCED")

        # Triage: 1000 input, 200 output at STANDARD
        triage_cost = calculate_cost(1000, 200, standard_model, pricing)
        # Coder: 3000 input, 500 output at STANDARD
        coder_cost = calculate_cost(3000, 500, standard_model, pricing)
        # Reviewer: 2000 input, 300 output at ADVANCED
        reviewer_cost = calculate_cost(2000, 300, advanced_model, pricing)

        accumulated = triage_cost + coder_cost + reviewer_cost

        # Compare to pricing everything at ADVANCED (the old broken behavior)
        all_input = 1000 + 3000 + 2000
        all_output = 200 + 500 + 300
        all_advanced = calculate_cost(all_input, all_output, advanced_model, pricing)

        metrics = FixMetrics(
            input_tokens=all_input,
            output_tokens=all_output,
            sessions_run=3,
            cost_usd=accumulated,
        )

        assert metrics.cost_usd < all_advanced, (
            f"Accumulated per-session cost ({accumulated}) should be lower than all-ADVANCED pricing ({all_advanced})"
        )


# ---------------------------------------------------------------------------
# TS-NS-3: Triage session tokens included in FixMetrics
# Requirement: NS-REQ-3
# ---------------------------------------------------------------------------


class TestTriageTokensInFixMetrics:
    """TS-NS-3: Triage tokens are accumulated into FixMetrics."""

    @pytest.mark.asyncio
    async def test_triage_tokens_accumulated_in_metrics(self) -> None:
        """When triage produces output, FixMetrics includes triage tokens."""
        from afcore.nightshift.fix_pipeline import FixPipeline, TriageResult
        from afissues.protocol import IssueResult

        config = _make_config()
        pipeline = FixPipeline(config, AsyncMock())

        triage_result = TriageResult(
            summary="Found the bug",
            criteria=[],
        )

        # Set up triage token state (simulating what _run_triage stores)
        pipeline._last_triage_input_tokens = 500
        pipeline._last_triage_output_tokens = 100
        pipeline._last_triage_cost = 0.05

        issue = IssueResult(number=42, title="Test", html_url="http://example.com/42")

        with (
            patch.object(pipeline, "_setup_workspace", AsyncMock(return_value=_make_workspace())),
            patch.object(pipeline, "_cleanup_workspace", AsyncMock()),
            patch.object(pipeline, "_run_triage", AsyncMock(return_value=triage_result)),
            patch.object(pipeline, "_coder_review_loop", AsyncMock(return_value=False)),
        ):
            metrics = await pipeline.process_issue(issue, issue_body="fix bug")

        assert metrics.input_tokens >= 500, (
            f"FixMetrics.input_tokens ({metrics.input_tokens}) must include triage tokens (500)"
        )
        assert metrics.output_tokens >= 100, (
            f"FixMetrics.output_tokens ({metrics.output_tokens}) must include triage tokens (100)"
        )
        assert metrics.sessions_run >= 1, f"FixMetrics.sessions_run ({metrics.sessions_run}) must count triage session"
        assert metrics.cost_usd >= 0.05, f"FixMetrics.cost_usd ({metrics.cost_usd}) must include triage cost (0.05)"

    @pytest.mark.asyncio
    async def test_empty_triage_not_accumulated(self) -> None:
        """When triage produces no output, FixMetrics excludes triage tokens."""
        from afcore.nightshift.fix_pipeline import FixPipeline, TriageResult
        from afissues.protocol import IssueResult

        config = _make_config()
        pipeline = FixPipeline(config, AsyncMock())

        # Empty triage — no criteria or summary
        triage_result = TriageResult()

        issue = IssueResult(number=42, title="Test", html_url="http://example.com/42")

        with (
            patch.object(pipeline, "_setup_workspace", AsyncMock(return_value=_make_workspace())),
            patch.object(pipeline, "_cleanup_workspace", AsyncMock()),
            patch.object(pipeline, "_run_triage", AsyncMock(return_value=triage_result)),
            patch.object(pipeline, "_coder_review_loop", AsyncMock(return_value=False)),
        ):
            metrics = await pipeline.process_issue(issue, issue_body="fix bug")

        assert metrics.sessions_run == 0, f"Empty triage should not increment sessions_run, got {metrics.sessions_run}"
        assert metrics.cost_usd == 0.0


# ---------------------------------------------------------------------------
# TS-NS-4: Hardcoded claude-sonnet-4-6 fallback eliminated
# Requirement: NS-REQ-4
# ---------------------------------------------------------------------------


class TestNoHardcodedFallback:
    """TS-NS-4: _get_model_id uses resolve_model instead of hardcoded string."""

    def test_get_model_id_no_hardcoded_fallback_in_source(self) -> None:
        """_get_model_id source code does not contain hardcoded 'claude-sonnet-4-6'."""
        import inspect

        from afcore.nightshift.fix_pipeline import FixPipeline

        source = inspect.getsource(FixPipeline._get_model_id)
        assert "claude-sonnet-4-6" not in source, (
            "_get_model_id must not contain hardcoded 'claude-sonnet-4-6' fallback"
        )

    def test_get_model_id_with_mode_resolves_correctly(self) -> None:
        """_get_model_id with mode='fix-review' resolves ADVANCED model."""
        from afcore.core.models import resolve_model
        from afcore.nightshift.fix_pipeline import FixPipeline

        config = _make_real_config()
        pipeline = FixPipeline(config, AsyncMock())

        model_id = pipeline._get_model_id("reviewer", mode="fix-review")
        expected = resolve_model("ADVANCED")
        assert model_id == expected, f"Expected ADVANCED model {expected}, got {model_id}"

    def test_get_model_id_without_mode_resolves_base_tier(self) -> None:
        """_get_model_id without mode uses the archetype's base tier."""
        from afcore.core.models import resolve_model
        from afcore.nightshift.fix_pipeline import FixPipeline

        config = _make_real_config()
        pipeline = FixPipeline(config, AsyncMock())

        model_id = pipeline._get_model_id("reviewer")
        expected = resolve_model("STANDARD")
        assert model_id == expected, f"Expected STANDARD model {expected}, got {model_id}"

    def test_get_model_id_respects_tier_defaults_config(self) -> None:
        """When tier_defaults remaps STANDARD, _get_model_id returns the remapped model."""
        from afcore.core.config import ArchetypesConfig, ModelsConfig
        from afcore.nightshift.fix_pipeline import FixPipeline

        config = MagicMock()
        config.archetypes = ArchetypesConfig()
        # Remap STANDARD to a custom model
        config.models = ModelsConfig(
            tier_defaults={"STANDARD": "claude-haiku-4-5"},
        )
        config.pricing = MagicMock()
        config.pricing.models = {}

        pipeline = FixPipeline(config, AsyncMock())

        model_id = pipeline._get_model_id("coder", mode="fix")
        assert model_id == "claude-haiku-4-5", f"Expected remapped model 'claude-haiku-4-5', got {model_id}"

    def test_session_fail_payload_uses_mode_in_coder_reviewer(self) -> None:
        """SESSION_FAIL payloads in coder_reviewer.py pass mode to _get_model_id."""
        import inspect

        from afcore.nightshift.coder_reviewer import CoderReviewerLoop

        # Check reviewer SESSION_FAIL in _run_single_reviewer
        source = inspect.getsource(CoderReviewerLoop._run_single_reviewer)
        assert 'mode="fix-review"' in source, (
            "_run_single_reviewer SESSION_FAIL must pass mode='fix-review' to _get_model_id"
        )

        # Check coder SESSION_FAIL in _run_coder_phase
        source = inspect.getsource(CoderReviewerLoop._run_coder_phase)
        assert 'mode="fix"' in source, "_run_coder_phase SESSION_FAIL must pass mode='fix' to _get_model_id"

        # Check reviewer retry SESSION_FAIL
        source = inspect.getsource(CoderReviewerLoop._retry_reviewer_on_parse_failure)
        assert 'mode="fix-review"' in source, (
            "_retry_reviewer_on_parse_failure SESSION_FAIL must pass mode='fix-review'"
        )


# ---------------------------------------------------------------------------
# TS-NS-5: pr_feedback.py no longer reads night_shift.model_id
# Requirement: NS-REQ-5
# ---------------------------------------------------------------------------


class TestPrFeedbackNoModelId:
    """TS-NS-5: pr_feedback.py does not read non-existent night_shift.model_id."""

    def test_no_night_shift_model_id_reference(self) -> None:
        """pr_feedback.py source does not reference night_shift.model_id."""
        source = Path("packages/afcore/afcore/nightshift/pr_feedback.py").read_text(
            encoding="utf-8",
        )
        assert "night_shift.model_id" not in source, "pr_feedback.py must not reference night_shift.model_id"
        assert 'model_id", None' not in source or "night_shift" not in source, (
            "pr_feedback.py must not use getattr to read model_id from night_shift config"
        )

    def test_run_coder_session_called_without_model_id(self) -> None:
        """_run_coder_session in feedback path is called without model_id override."""
        source = Path("packages/afcore/afcore/nightshift/pr_feedback.py").read_text(
            encoding="utf-8",
        )
        # The old code had: model_id = getattr(config.night_shift, "model_id", None)
        # followed by: await pipeline._run_coder_session(..., model_id=model_id)
        # After the fix, the getattr line is removed and model_id is not passed
        # (or defaults to None via the function signature).
        assert 'getattr(config.night_shift, "model_id"' not in source, (
            "pr_feedback.py must not use getattr to look up night_shift.model_id"
        )
