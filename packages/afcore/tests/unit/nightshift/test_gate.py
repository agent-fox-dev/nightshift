"""Unit tests for the mechanical verification gate (issue #35).

Verifies that:
- A non-zero gate exit code blocks integration even when the reviewer
  returns PASS (AC-1, TS-NS-1).
- A failing gate skips the reviewer session for that attempt (AC-2, TS-NS-2).
- Captured gate output is injected into the next coder attempt prompt
  (AC-3, TS-NS-3).
- Absence of a configured gate command leaves existing behaviour unchanged
  and logs once at startup (AC-4, TS-NS-4).

Requirements: NS-REQ-1, NS-REQ-2, NS-REQ-3, NS-REQ-4
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from afcore.nightshift.coder_reviewer import CoderReviewerLoop
from afcore.nightshift.fix_pipeline import FixMetrics, FixReviewResult, TriageResult
from afcore.nightshift.gate import GateResult, format_gate_feedback, log_ungated_warning
from afcore.nightshift.spec_builder import InMemorySpec
from afcore.workspace import WorkspaceInfo

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_spec(issue_number: int = 42) -> InMemorySpec:
    return InMemorySpec(
        issue_number=issue_number,
        title="Fix the flaky test",
        task_prompt="Fix the issue: Fix the flaky test",
        system_context="Repository context here.",
        branch_name=f"fix/{issue_number}-fix-the-flaky-test",
    )


def _make_triage(
    affected_files: list[str] | None = None,
    summary: str = "The test is flaky due to race condition",
) -> TriageResult:
    return TriageResult(
        summary=summary,
        affected_files=affected_files or [],
    )


def _make_workspace() -> WorkspaceInfo:
    return WorkspaceInfo(
        path=Path("/tmp/mock-worktree"),
        branch="fix/42-fix-the-flaky-test",
        spec_name="fix-issue-42",
        task_group=0,
    )


def _make_mock_pipeline(*, gate_command: str = "make check", gate_timeout: int = 600) -> MagicMock:
    """Build a MagicMock pipeline with gate config."""
    pipeline = MagicMock()
    pipeline._config.orchestrator.max_retries = 3
    pipeline._config.gate.command = gate_command
    pipeline._config.gate.timeout = gate_timeout
    pipeline._run_id = "run-test-gate"
    pipeline._format_review_comment.return_value = "## Review\nPASS"
    pipeline._post_comment = AsyncMock()
    return pipeline


def _make_coder_outcome(response: str = "The fix applied cleanly.") -> MagicMock:
    outcome = MagicMock()
    outcome.response = response
    outcome.input_tokens = 100
    outcome.output_tokens = 50
    outcome.cache_read_input_tokens = 0
    outcome.cache_creation_input_tokens = 0
    outcome.duration_ms = 2000
    outcome.status = "completed"
    outcome.error_message = None
    return outcome


def _make_gate_result(*, passed: bool, exit_code: int = 0, output: str = "") -> GateResult:
    return GateResult(
        passed=passed,
        exit_code=exit_code,
        output=output,
        command="make check",
    )


def _patch_model_resolution():
    return (
        patch("afcore.core.models.resolve_model", return_value="claude-sonnet-4-6"),
        patch("afcore.engine.sdk_params.resolve_model_tier", return_value="standard"),
    )


# ---------------------------------------------------------------------------
# TS-NS-1: Non-zero gate exit code blocks integration even with reviewer PASS
# Requirement: NS-REQ-1
# ---------------------------------------------------------------------------


class TestGateBlocksIntegration:
    """AC-1: A non-zero gate exit code blocks integration.

    Test Spec: TS-NS-1
    Requirements: NS-REQ-1
    """

    async def test_gate_fail_blocks_even_with_reviewer_pass(self) -> None:
        """Gate fails → result.success is False, reviewer never called."""
        pipeline = _make_mock_pipeline()
        pipeline._config.orchestrator.max_retries = 0  # One attempt only
        loop = CoderReviewerLoop(pipeline)

        coder_outcome = _make_coder_outcome()
        gate_fail = _make_gate_result(passed=False, exit_code=1, output="FAIL: test_foo")

        reviewer_calls: list[int] = []

        async def mock_reviewer(*args: object, **kwargs: object) -> FixReviewResult:
            reviewer_calls.append(1)
            return FixReviewResult(overall_verdict="PASS")

        p1, p2 = _patch_model_resolution()
        with (
            p1,
            p2,
            patch.object(loop, "_run_coder_phase", new_callable=AsyncMock, return_value=coder_outcome),
            patch.object(loop, "_run_gate", new_callable=AsyncMock, return_value=gate_fail),
            patch.object(loop, "_run_reviewer_phase", side_effect=mock_reviewer),
        ):
            result = await loop.run(
                spec=_make_spec(),
                triage=_make_triage(affected_files=["src/handler.py"]),
                metrics=FixMetrics(),
                workspace=_make_workspace(),
            )

        assert result.success is False, "Gate failure must block integration"
        assert len(reviewer_calls) == 0, "Reviewer must not be called when gate fails"

    async def test_gate_pass_allows_reviewer_pass_to_succeed(self) -> None:
        """Gate passes + reviewer PASS → result.success is True."""
        pipeline = _make_mock_pipeline()
        loop = CoderReviewerLoop(pipeline)

        coder_outcome = _make_coder_outcome()
        gate_pass = _make_gate_result(passed=True, exit_code=0)
        review_pass = FixReviewResult(overall_verdict="PASS")

        p1, p2 = _patch_model_resolution()
        with (
            p1,
            p2,
            patch.object(loop, "_run_coder_phase", new_callable=AsyncMock, return_value=coder_outcome),
            patch.object(loop, "_run_gate", new_callable=AsyncMock, return_value=gate_pass),
            patch.object(loop, "_run_reviewer_phase", new_callable=AsyncMock, return_value=review_pass),
        ):
            result = await loop.run(
                spec=_make_spec(),
                triage=_make_triage(affected_files=["src/handler.py"]),
                metrics=FixMetrics(),
                workspace=_make_workspace(),
            )

        assert result.success is True


# ---------------------------------------------------------------------------
# TS-NS-2: A failing gate skips the reviewer session
# Requirement: NS-REQ-2
# ---------------------------------------------------------------------------


class TestGateSkipsReviewer:
    """AC-2: A failing gate skips the reviewer session.

    Test Spec: TS-NS-2
    Requirements: NS-REQ-2
    """

    async def test_reviewer_not_invoked_on_gate_failure(self) -> None:
        """_run_reviewer_phase is never called when gate exits non-zero."""
        pipeline = _make_mock_pipeline()
        pipeline._config.orchestrator.max_retries = 0
        loop = CoderReviewerLoop(pipeline)

        coder_outcome = _make_coder_outcome()
        gate_fail = _make_gate_result(passed=False, exit_code=2, output="lint errors")

        reviewer_mock = AsyncMock(return_value=FixReviewResult(overall_verdict="PASS"))

        p1, p2 = _patch_model_resolution()
        with (
            p1,
            p2,
            patch.object(loop, "_run_coder_phase", new_callable=AsyncMock, return_value=coder_outcome),
            patch.object(loop, "_run_gate", new_callable=AsyncMock, return_value=gate_fail),
            patch.object(loop, "_run_reviewer_phase", reviewer_mock),
        ):
            await loop.run(
                spec=_make_spec(),
                triage=_make_triage(),
                metrics=FixMetrics(),
                workspace=_make_workspace(),
            )

        reviewer_mock.assert_not_called()

    async def test_gate_failure_posts_comment(self) -> None:
        """A gate failure posts a gate-failure comment on the issue."""
        pipeline = _make_mock_pipeline()
        pipeline._config.orchestrator.max_retries = 0
        loop = CoderReviewerLoop(pipeline)

        coder_outcome = _make_coder_outcome()
        gate_fail = _make_gate_result(passed=False, exit_code=1, output="tests fail")

        p1, p2 = _patch_model_resolution()
        with (
            p1,
            p2,
            patch.object(loop, "_run_coder_phase", new_callable=AsyncMock, return_value=coder_outcome),
            patch.object(loop, "_run_gate", new_callable=AsyncMock, return_value=gate_fail),
            patch.object(loop, "_run_reviewer_phase", new_callable=AsyncMock),
        ):
            await loop.run(
                spec=_make_spec(),
                triage=_make_triage(),
                metrics=FixMetrics(),
                workspace=_make_workspace(),
            )

        posted = [str(call) for call in pipeline._post_comment.call_args_list]
        assert any("gate failed" in c.lower() for c in posted), f"Expected 'Gate Failed' in comment, got: {posted}"


# ---------------------------------------------------------------------------
# TS-NS-3: Gate output is injected into the next coder attempt prompt
# Requirement: NS-REQ-3
# ---------------------------------------------------------------------------


class TestGateOutputInjection:
    """AC-3: Gate output is injected into the next coder attempt prompt.

    Test Spec: TS-NS-3
    Requirements: NS-REQ-3
    """

    async def test_gate_output_in_next_coder_prompt(self) -> None:
        """After gate failure, the next coder prompt contains the gate output."""
        pipeline = _make_mock_pipeline()
        pipeline._config.orchestrator.max_retries = 1  # Allow one retry
        loop = CoderReviewerLoop(pipeline)

        coder_outcome = _make_coder_outcome()
        gate_fail = _make_gate_result(
            passed=False,
            exit_code=1,
            output="FAILED test_foo.py::test_bar - AssertionError",
        )
        gate_pass = _make_gate_result(passed=True, exit_code=0)
        review_pass = FixReviewResult(overall_verdict="PASS")

        coder_phase_calls: list[dict] = []
        gate_call_count = 0

        async def mock_coder(*args: object, **kwargs: object) -> object:
            # Capture prior_context (kwarg or positional)
            prior = kwargs.get("prior_context", "")
            if not prior and len(args) > 8:
                prior = args[8]
            coder_phase_calls.append({"prior_context": prior})
            return coder_outcome

        async def mock_gate(workspace: object) -> GateResult:
            nonlocal gate_call_count
            gate_call_count += 1
            if gate_call_count == 1:
                return gate_fail
            return gate_pass

        p1, p2 = _patch_model_resolution()
        with (
            p1,
            p2,
            patch.object(loop, "_run_coder_phase", side_effect=mock_coder),
            patch.object(loop, "_run_gate", side_effect=mock_gate),
            patch.object(loop, "_run_reviewer_phase", new_callable=AsyncMock, return_value=review_pass),
        ):
            result = await loop.run(
                spec=_make_spec(),
                triage=_make_triage(affected_files=["src/handler.py"]),
                metrics=FixMetrics(),
                workspace=_make_workspace(),
            )

        assert result.success is True
        assert len(coder_phase_calls) == 2
        # Second coder call should have the gate output in prior_context
        second_prior = coder_phase_calls[1]["prior_context"]
        assert "Previous Gate Output" in second_prior
        assert "FAILED test_foo.py::test_bar" in second_prior

    def test_format_gate_feedback_contains_output(self) -> None:
        """format_gate_feedback produces a markdown section with the output."""
        gate_result = _make_gate_result(
            passed=False,
            exit_code=1,
            output="error: compilation failed\nsrc/main.rs:42: expected ';'",
        )
        feedback = format_gate_feedback(gate_result)

        assert "## Previous Gate Output" in feedback
        assert "make check" in feedback
        assert "exit code 1" in feedback
        assert "compilation failed" in feedback
        assert "expected ';'" in feedback


# ---------------------------------------------------------------------------
# TS-NS-4: No gate command leaves behaviour unchanged and logs at startup
# Requirement: NS-REQ-4
# ---------------------------------------------------------------------------


class TestNoGateConfigUnchanged:
    """AC-4: No gate command → unchanged behaviour, startup warning.

    Test Spec: TS-NS-4
    Requirements: NS-REQ-4
    """

    async def test_no_gate_config_passes_through_to_reviewer(self) -> None:
        """When gate command is empty, reviewer is invoked and PASS → success."""
        pipeline = _make_mock_pipeline(gate_command="")  # No gate command
        loop = CoderReviewerLoop(pipeline)

        coder_outcome = _make_coder_outcome()
        review_pass = FixReviewResult(overall_verdict="PASS")

        reviewer_calls: list[int] = []

        async def mock_reviewer(*args: object, **kwargs: object) -> FixReviewResult:
            reviewer_calls.append(1)
            return review_pass

        p1, p2 = _patch_model_resolution()
        with (
            p1,
            p2,
            patch.object(loop, "_run_coder_phase", new_callable=AsyncMock, return_value=coder_outcome),
            patch.object(loop, "_run_reviewer_phase", side_effect=mock_reviewer),
        ):
            result = await loop.run(
                spec=_make_spec(),
                triage=_make_triage(affected_files=["src/handler.py"]),
                metrics=FixMetrics(),
                workspace=_make_workspace(),
            )

        assert result.success is True
        assert len(reviewer_calls) == 1, "Reviewer must be called when no gate configured"

    async def test_no_gate_config_no_subprocess(self) -> None:
        """When gate command is empty, _run_gate returns None (no subprocess)."""
        pipeline = _make_mock_pipeline(gate_command="")
        loop = CoderReviewerLoop(pipeline)

        result = await loop._run_gate(_make_workspace())
        assert result is None, "_run_gate must return None when no gate command configured"

    async def test_gate_none_config_no_subprocess(self) -> None:
        """When gate config attribute is missing entirely, _run_gate returns None."""
        pipeline = _make_mock_pipeline(gate_command="")
        # Remove the gate attribute entirely
        del pipeline._config.gate
        loop = CoderReviewerLoop(pipeline)

        result = await loop._run_gate(_make_workspace())
        assert result is None

    def test_ungated_warning_logged_at_startup(self, caplog: pytest.LogCaptureFixture) -> None:
        """log_ungated_warning emits exactly one WARNING about ungated mode."""
        with caplog.at_level(logging.WARNING, logger="afcore.nightshift.gate"):
            log_ungated_warning()

        gate_warnings = [r for r in caplog.records if "gate" in r.message.lower() and r.levelno == logging.WARNING]
        assert len(gate_warnings) == 1, f"Expected exactly one gate warning, got {len(gate_warnings)}"
        assert "no gate command configured" in gate_warnings[0].message.lower()

    def test_engine_logs_ungated_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """NightShiftEngine.__init__ logs ungated warning when no gate command."""
        from afcore.core.config import AgentFoxConfig

        config = AgentFoxConfig()  # Default: no gate command

        with caplog.at_level(logging.WARNING, logger="afcore.nightshift.gate"):
            from afcore.nightshift.engine import NightShiftEngine

            NightShiftEngine(config, platform=MagicMock())

        gate_warnings = [r for r in caplog.records if "gate" in r.message.lower() and r.levelno == logging.WARNING]
        assert len(gate_warnings) >= 1, "Expected at least one ungated warning at startup"


# ---------------------------------------------------------------------------
# GateResult dataclass
# ---------------------------------------------------------------------------


class TestGateResult:
    """Basic GateResult attribute tests."""

    def test_passed_gate(self) -> None:
        r = GateResult(passed=True, exit_code=0, output="OK", command="make check")
        assert r.passed is True
        assert r.exit_code == 0

    def test_failed_gate(self) -> None:
        r = GateResult(passed=False, exit_code=1, output="FAIL", command="make check")
        assert r.passed is False
        assert r.exit_code == 1
        assert r.output == "FAIL"

    def test_timeout_gate(self) -> None:
        r = GateResult(passed=False, exit_code=-1, output="timeout", command="make check")
        assert r.passed is False
        assert r.exit_code == -1
