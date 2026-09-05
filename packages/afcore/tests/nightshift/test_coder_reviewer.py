"""Unit tests for CoderReviewerLoop return object extensions.

Verifies that ``CoderReviewerLoop.run()`` returns an object with ``response``
and ``affected_files`` fields, correctly populated on successful coder
sessions and defaulting to empty on early-exit / exhaustion paths.

Test Spec: TS-05-29, TS-05-30, TS-05-31, TS-05-39, TS-05-40
Requirements: 05-REQ-9.1, 05-REQ-9.2, 05-REQ-9.3, 05-REQ-9.E1
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from afcore.nightshift.coder_reviewer import CoderReviewerLoop
from afcore.nightshift.fix_pipeline import FixMetrics, FixReviewResult, TriageResult
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


def _make_mock_pipeline() -> MagicMock:
    """Build a MagicMock pipeline with the attributes CoderReviewerLoop.run() needs."""
    pipeline = MagicMock()
    pipeline._config.orchestrator.max_retries = 3
    pipeline._run_id = "run-test-1"
    pipeline._format_review_comment.return_value = "## Review\nPASS"
    pipeline._post_comment = AsyncMock()
    return pipeline


def _make_coder_outcome(response: str = "The fix applied cleanly.") -> MagicMock:
    """Build a mock coder session outcome with a response attribute."""
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


def _patch_model_resolution():
    """Context manager that patches model resolution imports used inside run()."""
    return (
        patch("afcore.core.models.resolve_model", return_value="claude-sonnet-4-6"),
        patch("afcore.engine.sdk_params.resolve_model_tier", return_value="standard"),
    )


# ---------------------------------------------------------------------------
# TS-05-29: Return object has response and affected_files fields
# ---------------------------------------------------------------------------


class TestCoderReviewerReturnObjectFields:
    """Verify the return object includes ``response`` and ``affected_files``.

    The return type must have ``response: str`` (default ``""``) and
    ``affected_files: list[str]`` (default ``[]``) without introducing
    a completely new return type — the existing type is extended in-place.

    Test Spec: TS-05-29
    Requirements: 05-REQ-9.1
    """

    async def test_result_has_response_attribute(self) -> None:
        """The run() return object has a 'response' attribute."""
        pipeline = _make_mock_pipeline()
        loop = CoderReviewerLoop(pipeline)

        review_result = FixReviewResult(overall_verdict="PASS")
        coder_outcome = _make_coder_outcome()

        p1, p2 = _patch_model_resolution()
        with (
            p1,
            p2,
            patch.object(loop, "_run_coder_phase", new_callable=AsyncMock, return_value=coder_outcome),
            patch.object(loop, "_run_reviewer_phase", new_callable=AsyncMock, return_value=review_result),
        ):
            result = await loop.run(
                spec=_make_spec(),
                triage=_make_triage(affected_files=["src/handler.py"]),
                metrics=FixMetrics(),
                workspace=_make_workspace(),
            )

        assert hasattr(result, "response"), f"Return object {type(result).__name__} must have a 'response' attribute"

    async def test_result_has_affected_files_attribute(self) -> None:
        """The run() return object has an 'affected_files' attribute."""
        pipeline = _make_mock_pipeline()
        loop = CoderReviewerLoop(pipeline)

        review_result = FixReviewResult(overall_verdict="PASS")
        coder_outcome = _make_coder_outcome()

        p1, p2 = _patch_model_resolution()
        with (
            p1,
            p2,
            patch.object(loop, "_run_coder_phase", new_callable=AsyncMock, return_value=coder_outcome),
            patch.object(loop, "_run_reviewer_phase", new_callable=AsyncMock, return_value=review_result),
        ):
            result = await loop.run(
                spec=_make_spec(),
                triage=_make_triage(affected_files=["src/handler.py"]),
                metrics=FixMetrics(),
                workspace=_make_workspace(),
            )

        assert hasattr(result, "affected_files"), (
            f"Return object {type(result).__name__} must have an 'affected_files' attribute"
        )

    async def test_response_default_is_empty_string(self) -> None:
        """The 'response' field defaults to '' on exhaustion (no PASS verdict)."""
        pipeline = _make_mock_pipeline()
        pipeline._config.orchestrator.max_retries = 0  # Only one attempt
        loop = CoderReviewerLoop(pipeline)

        review_result = FixReviewResult(overall_verdict="FAIL")
        coder_outcome = _make_coder_outcome(response="")

        p1, p2 = _patch_model_resolution()
        with (
            p1,
            p2,
            patch.object(loop, "_run_coder_phase", new_callable=AsyncMock, return_value=coder_outcome),
            patch.object(loop, "_run_reviewer_phase", new_callable=AsyncMock, return_value=review_result),
        ):
            result = await loop.run(
                spec=_make_spec(),
                triage=_make_triage(),
                metrics=FixMetrics(),
                workspace=_make_workspace(),
            )

        assert result.response == ""

    async def test_affected_files_default_is_empty_list(self) -> None:
        """The 'affected_files' field defaults to [] on exhaustion."""
        pipeline = _make_mock_pipeline()
        pipeline._config.orchestrator.max_retries = 0
        loop = CoderReviewerLoop(pipeline)

        review_result = FixReviewResult(overall_verdict="FAIL")
        coder_outcome = _make_coder_outcome(response="")

        p1, p2 = _patch_model_resolution()
        with (
            p1,
            p2,
            patch.object(loop, "_run_coder_phase", new_callable=AsyncMock, return_value=coder_outcome),
            patch.object(loop, "_run_reviewer_phase", new_callable=AsyncMock, return_value=review_result),
        ):
            result = await loop.run(
                spec=_make_spec(),
                triage=_make_triage(),
                metrics=FixMetrics(),
                workspace=_make_workspace(),
            )

        assert result.affected_files == []


# ---------------------------------------------------------------------------
# TS-05-30, TS-05-39: Successful path populates response and affected_files
# ---------------------------------------------------------------------------


class TestCoderReviewerSuccessfulPath:
    """Verify fields after a successful coder session (reviewer PASS).

    When a coder session completes and the reviewer gives PASS verdict,
    ``response`` should contain the last assistant message text and
    ``affected_files`` should contain the triage file paths.

    Test Spec: TS-05-30, TS-05-39
    Requirements: 05-REQ-9.2, 05-REQ-12.1
    """

    async def test_response_set_to_coder_message_text(self) -> None:
        """response equals the last assistant message text from coder outcome."""
        pipeline = _make_mock_pipeline()
        loop = CoderReviewerLoop(pipeline)

        coder_outcome = _make_coder_outcome(response="The fix applied cleanly.")
        review_result = FixReviewResult(overall_verdict="PASS")

        p1, p2 = _patch_model_resolution()
        with (
            p1,
            p2,
            patch.object(loop, "_run_coder_phase", new_callable=AsyncMock, return_value=coder_outcome),
            patch.object(loop, "_run_reviewer_phase", new_callable=AsyncMock, return_value=review_result),
        ):
            result = await loop.run(
                spec=_make_spec(),
                triage=_make_triage(affected_files=["src/handler.py"]),
                metrics=FixMetrics(),
                workspace=_make_workspace(),
            )

        assert result.response == "The fix applied cleanly."

    async def test_affected_files_set_to_triage_file_paths(self) -> None:
        """affected_files equals the triage output's affected_files list."""
        pipeline = _make_mock_pipeline()
        loop = CoderReviewerLoop(pipeline)

        coder_outcome = _make_coder_outcome(response="Fixed successfully.")
        review_result = FixReviewResult(overall_verdict="PASS")
        triage = _make_triage(affected_files=["src/handler.py", "src/utils.py"])

        p1, p2 = _patch_model_resolution()
        with (
            p1,
            p2,
            patch.object(loop, "_run_coder_phase", new_callable=AsyncMock, return_value=coder_outcome),
            patch.object(loop, "_run_reviewer_phase", new_callable=AsyncMock, return_value=review_result),
        ):
            result = await loop.run(
                spec=_make_spec(),
                triage=triage,
                metrics=FixMetrics(),
                workspace=_make_workspace(),
            )

        assert result.affected_files == ["src/handler.py", "src/utils.py"]

    async def test_both_fields_populated_together(self) -> None:
        """Both response and affected_files are populated on a single successful run."""
        pipeline = _make_mock_pipeline()
        loop = CoderReviewerLoop(pipeline)

        coder_outcome = _make_coder_outcome(response="Fixed successfully.")
        review_result = FixReviewResult(overall_verdict="PASS")
        triage = _make_triage(affected_files=["src/main.py"])

        p1, p2 = _patch_model_resolution()
        with (
            p1,
            p2,
            patch.object(loop, "_run_coder_phase", new_callable=AsyncMock, return_value=coder_outcome),
            patch.object(loop, "_run_reviewer_phase", new_callable=AsyncMock, return_value=review_result),
        ):
            result = await loop.run(
                spec=_make_spec(),
                triage=triage,
                metrics=FixMetrics(),
                workspace=_make_workspace(),
            )

        assert hasattr(result, "response")
        assert hasattr(result, "affected_files")
        assert result.response == "Fixed successfully."
        assert result.affected_files == ["src/main.py"]


# ---------------------------------------------------------------------------
# TS-05-31, TS-05-40: Early-exit paths return defaults
# ---------------------------------------------------------------------------


class TestCoderReviewerEarlyExit:
    """Verify fields on early-exit paths (loop exhaustion, aborted).

    When the coder-reviewer loop exhausts all retries (all verdicts FAIL)
    or encounters an early exit, the result should have ``response=""``
    and ``affected_files=[]`` with no uninitialized fields.

    Test Spec: TS-05-31, TS-05-40
    Requirements: 05-REQ-9.3, 05-REQ-9.E1, 05-REQ-12.2
    """

    async def test_exhausted_loop_has_empty_response(self) -> None:
        """response is '' when all retries are exhausted (all FAIL verdicts)."""
        pipeline = _make_mock_pipeline()
        pipeline._config.orchestrator.max_retries = 0  # Only one attempt
        loop = CoderReviewerLoop(pipeline)

        coder_outcome = _make_coder_outcome(response="Attempted fix.")
        review_result = FixReviewResult(overall_verdict="FAIL", summary="Tests still fail")

        p1, p2 = _patch_model_resolution()
        with (
            p1,
            p2,
            patch.object(loop, "_run_coder_phase", new_callable=AsyncMock, return_value=coder_outcome),
            patch.object(loop, "_run_reviewer_phase", new_callable=AsyncMock, return_value=review_result),
        ):
            result = await loop.run(
                spec=_make_spec(),
                triage=_make_triage(affected_files=["src/handler.py"]),
                metrics=FixMetrics(),
                workspace=_make_workspace(),
            )

        assert result.response == ""

    async def test_exhausted_loop_has_empty_affected_files(self) -> None:
        """affected_files is [] when all retries are exhausted."""
        pipeline = _make_mock_pipeline()
        pipeline._config.orchestrator.max_retries = 0
        loop = CoderReviewerLoop(pipeline)

        coder_outcome = _make_coder_outcome(response="Attempted fix.")
        review_result = FixReviewResult(overall_verdict="FAIL")

        p1, p2 = _patch_model_resolution()
        with (
            p1,
            p2,
            patch.object(loop, "_run_coder_phase", new_callable=AsyncMock, return_value=coder_outcome),
            patch.object(loop, "_run_reviewer_phase", new_callable=AsyncMock, return_value=review_result),
        ):
            result = await loop.run(
                spec=_make_spec(),
                triage=_make_triage(affected_files=["src/handler.py"]),
                metrics=FixMetrics(),
                workspace=_make_workspace(),
            )

        assert result.affected_files == []

    async def test_no_attribute_error_on_field_access(self) -> None:
        """Accessing response and affected_files never raises AttributeError."""
        pipeline = _make_mock_pipeline()
        pipeline._config.orchestrator.max_retries = 0
        loop = CoderReviewerLoop(pipeline)

        coder_outcome = _make_coder_outcome(response="")
        review_result = FixReviewResult(overall_verdict="FAIL")

        p1, p2 = _patch_model_resolution()
        with (
            p1,
            p2,
            patch.object(loop, "_run_coder_phase", new_callable=AsyncMock, return_value=coder_outcome),
            patch.object(loop, "_run_reviewer_phase", new_callable=AsyncMock, return_value=review_result),
        ):
            result = await loop.run(
                spec=_make_spec(),
                triage=_make_triage(),
                metrics=FixMetrics(),
                workspace=_make_workspace(),
            )

        # These accesses must not raise AttributeError
        _ = result.response
        _ = result.affected_files

    async def test_exhausted_loop_with_multiple_retries(self) -> None:
        """response='' and affected_files=[] after multiple failed retries."""
        pipeline = _make_mock_pipeline()
        pipeline._config.orchestrator.max_retries = 2  # 3 total attempts
        loop = CoderReviewerLoop(pipeline)

        coder_outcome = _make_coder_outcome(response="Retry attempt.")
        review_result = FixReviewResult(overall_verdict="FAIL")

        p1, p2 = _patch_model_resolution()
        with (
            p1,
            p2,
            patch.object(loop, "_run_coder_phase", new_callable=AsyncMock, return_value=coder_outcome),
            patch.object(loop, "_run_reviewer_phase", new_callable=AsyncMock, return_value=review_result),
        ):
            result = await loop.run(
                spec=_make_spec(),
                triage=_make_triage(affected_files=["src/main.py"]),
                metrics=FixMetrics(),
                workspace=_make_workspace(),
            )

        assert result.response == ""
        assert result.affected_files == []

    async def test_empty_triage_affected_files_returns_empty_list(self) -> None:
        """affected_files=[] when triage has no affected_files."""
        pipeline = _make_mock_pipeline()
        loop = CoderReviewerLoop(pipeline)

        coder_outcome = _make_coder_outcome(response="Fixed it.")
        review_result = FixReviewResult(overall_verdict="PASS")
        triage = _make_triage(affected_files=[])

        p1, p2 = _patch_model_resolution()
        with (
            p1,
            p2,
            patch.object(loop, "_run_coder_phase", new_callable=AsyncMock, return_value=coder_outcome),
            patch.object(loop, "_run_reviewer_phase", new_callable=AsyncMock, return_value=review_result),
        ):
            result = await loop.run(
                spec=_make_spec(),
                triage=triage,
                metrics=FixMetrics(),
                workspace=_make_workspace(),
            )

        assert result.affected_files == []


# ---------------------------------------------------------------------------
# TS-05-29 additional: backward compatibility with bool semantics
# ---------------------------------------------------------------------------


class TestCoderReviewerBoolCompatibility:
    """Verify the return object preserves bool-like truthiness semantics.

    The existing call site in fix_pipeline.py uses ``if not success:``
    where ``success = await self._coder_review_loop(...)``. The extended
    return type must be truthy on PASS and falsy on exhaustion to avoid
    breaking existing control flow.

    Requirement: 05-REQ-9.1 (no new return type — extend existing)
    """

    async def test_truthy_on_pass(self) -> None:
        """Return object is truthy when reviewer verdict is PASS."""
        pipeline = _make_mock_pipeline()
        loop = CoderReviewerLoop(pipeline)

        review_result = FixReviewResult(overall_verdict="PASS")
        coder_outcome = _make_coder_outcome()

        p1, p2 = _patch_model_resolution()
        with (
            p1,
            p2,
            patch.object(loop, "_run_coder_phase", new_callable=AsyncMock, return_value=coder_outcome),
            patch.object(loop, "_run_reviewer_phase", new_callable=AsyncMock, return_value=review_result),
        ):
            result = await loop.run(
                spec=_make_spec(),
                triage=_make_triage(),
                metrics=FixMetrics(),
                workspace=_make_workspace(),
            )

        assert result, "Return object should be truthy on PASS verdict"

    async def test_falsy_on_exhaustion(self) -> None:
        """Return object is falsy when loop exhausts all retries."""
        pipeline = _make_mock_pipeline()
        pipeline._config.orchestrator.max_retries = 0
        loop = CoderReviewerLoop(pipeline)

        review_result = FixReviewResult(overall_verdict="FAIL")
        coder_outcome = _make_coder_outcome()

        p1, p2 = _patch_model_resolution()
        with (
            p1,
            p2,
            patch.object(loop, "_run_coder_phase", new_callable=AsyncMock, return_value=coder_outcome),
            patch.object(loop, "_run_reviewer_phase", new_callable=AsyncMock, return_value=review_result),
        ):
            result = await loop.run(
                spec=_make_spec(),
                triage=_make_triage(),
                metrics=FixMetrics(),
                workspace=_make_workspace(),
            )

        assert not result, "Return object should be falsy on exhaustion"


# ---------------------------------------------------------------------------
# TS-NS-1: Transport-error coder sessions do not trigger reviewer
# Requirement: NS-REQ-1
# ---------------------------------------------------------------------------


class TestCoderTransportError:
    """Verify transport-error coder sessions skip reviewer and don't consume attempt.

    Test Spec: TS-NS-1
    Requirements: NS-REQ-1
    """

    async def test_transport_error_skips_reviewer_does_not_consume_attempt(self) -> None:
        """AC-1: Transport error → no reviewer, attempt not incremented, retries coder."""
        pipeline = _make_mock_pipeline()
        pipeline._config.orchestrator.max_retries = 1
        loop = CoderReviewerLoop(pipeline)

        transport_outcome = _make_coder_outcome(response="")
        transport_outcome.status = "failed"
        transport_outcome.is_transport_error = True

        success_outcome = _make_coder_outcome(response="Fixed it.")
        success_outcome.status = "completed"
        success_outcome.is_transport_error = False

        review_result = FixReviewResult(overall_verdict="PASS")

        coder_calls: list[int] = []
        reviewer_calls: list[int] = []

        async def mock_coder(*args: object, **kwargs: object) -> object:
            attempt = args[7] if len(args) > 7 else kwargs.get("attempt", 0)
            coder_calls.append(attempt)
            if len(coder_calls) == 1:
                return transport_outcome
            return success_outcome

        async def mock_reviewer(*args: object, **kwargs: object) -> FixReviewResult:
            reviewer_calls.append(1)
            return review_result

        p1, p2 = _patch_model_resolution()
        with (
            p1,
            p2,
            patch.object(loop, "_run_coder_phase", side_effect=mock_coder),
            patch.object(loop, "_run_reviewer_phase", side_effect=mock_reviewer),
        ):
            result = await loop.run(
                spec=_make_spec(),
                triage=_make_triage(affected_files=["src/handler.py"]),
                metrics=FixMetrics(),
                workspace=_make_workspace(),
            )

        assert result.success is True
        assert len(coder_calls) == 2, "Coder should be called twice (transport retry + success)"
        assert len(reviewer_calls) == 1, "Reviewer should be called only once (after success)"
        # Both coder calls should have attempt=0 (transport error doesn't increment)
        assert coder_calls[0] == 0
        assert coder_calls[1] == 0

    async def test_transport_error_bounded_by_max_retries(self) -> None:
        """AC-1: Transport retries are bounded by MAX_TRANSPORT_RETRIES."""
        pipeline = _make_mock_pipeline()
        pipeline._config.orchestrator.max_retries = 3
        loop = CoderReviewerLoop(pipeline)

        transport_outcome = _make_coder_outcome(response="")
        transport_outcome.status = "failed"
        transport_outcome.is_transport_error = True

        coder_calls: list[int] = []

        async def mock_coder(*args: object, **kwargs: object) -> object:
            coder_calls.append(1)
            return transport_outcome

        p1, p2 = _patch_model_resolution()
        with (
            p1,
            p2,
            patch.object(loop, "_run_coder_phase", side_effect=mock_coder),
        ):
            result = await loop.run(
                spec=_make_spec(),
                triage=_make_triage(),
                metrics=FixMetrics(),
                workspace=_make_workspace(),
            )

        assert result.success is False
        # Should be called MAX_TRANSPORT_RETRIES + 1 times (initial + retries)
        assert len(coder_calls) == CoderReviewerLoop.MAX_TRANSPORT_RETRIES + 1

    async def test_transport_error_posts_transport_comment(self) -> None:
        """AC-1: When transport retries exhausted, comment names transport error."""
        pipeline = _make_mock_pipeline()
        pipeline._config.orchestrator.max_retries = 3
        loop = CoderReviewerLoop(pipeline)

        transport_outcome = _make_coder_outcome(response="")
        transport_outcome.status = "failed"
        transport_outcome.is_transport_error = True

        async def mock_coder(*args: object, **kwargs: object) -> object:
            return transport_outcome

        p1, p2 = _patch_model_resolution()
        with (
            p1,
            p2,
            patch.object(loop, "_run_coder_phase", side_effect=mock_coder),
        ):
            await loop.run(
                spec=_make_spec(),
                triage=_make_triage(),
                metrics=FixMetrics(),
                workspace=_make_workspace(),
            )

        # Check that the posted comment names transport errors
        posted = [str(call) for call in pipeline._post_comment.call_args_list]
        assert any("transport" in c.lower() for c in posted), f"Expected transport error comment, got: {posted}"


# ---------------------------------------------------------------------------
# TS-NS-2: Timeout coder sessions skip reviewer
# Requirement: NS-REQ-2
# ---------------------------------------------------------------------------


class TestCoderTimeout:
    """Verify timeout coder sessions skip reviewer and post timeout comment.

    Test Spec: TS-NS-2
    Requirements: NS-REQ-2
    """

    async def test_timeout_skips_reviewer_and_posts_timeout_comment(self) -> None:
        """AC-2: Timeout → reviewer not called, comment names 'timeout'."""
        pipeline = _make_mock_pipeline()
        pipeline._config.orchestrator.max_retries = 0  # One attempt only
        loop = CoderReviewerLoop(pipeline)

        timeout_outcome = _make_coder_outcome(response="")
        timeout_outcome.status = "timeout"
        timeout_outcome.is_transport_error = False

        reviewer_calls: list[int] = []

        async def mock_reviewer(*args: object, **kwargs: object) -> FixReviewResult:
            reviewer_calls.append(1)
            return FixReviewResult(overall_verdict="FAIL")

        p1, p2 = _patch_model_resolution()
        with (
            p1,
            p2,
            patch.object(loop, "_run_coder_phase", new_callable=AsyncMock, return_value=timeout_outcome),
            patch.object(loop, "_run_reviewer_phase", side_effect=mock_reviewer),
        ):
            result = await loop.run(
                spec=_make_spec(),
                triage=_make_triage(),
                metrics=FixMetrics(),
                workspace=_make_workspace(),
            )

        assert result.success is False
        assert len(reviewer_calls) == 0, "Reviewer must not be called on timeout"

        # Check comment names timeout
        posted = [str(call) for call in pipeline._post_comment.call_args_list]
        assert any("timed out" in c.lower() for c in posted), f"Expected 'timed out' in comment, got: {posted}"
        # Comment should NOT contain "Overall verdict: FAIL"
        assert not any("overall verdict: fail" in c.lower() for c in posted), (
            f"Comment should not contain 'Overall verdict: FAIL', got: {posted}"
        )

    async def test_failed_coder_skips_reviewer(self) -> None:
        """Non-transport failure also skips reviewer and posts failure comment."""
        pipeline = _make_mock_pipeline()
        pipeline._config.orchestrator.max_retries = 0
        loop = CoderReviewerLoop(pipeline)

        failed_outcome = _make_coder_outcome(response="")
        failed_outcome.status = "failed"
        failed_outcome.is_transport_error = False

        reviewer_calls: list[int] = []

        async def mock_reviewer(*args: object, **kwargs: object) -> FixReviewResult:
            reviewer_calls.append(1)
            return FixReviewResult(overall_verdict="FAIL")

        p1, p2 = _patch_model_resolution()
        with (
            p1,
            p2,
            patch.object(loop, "_run_coder_phase", new_callable=AsyncMock, return_value=failed_outcome),
            patch.object(loop, "_run_reviewer_phase", side_effect=mock_reviewer),
        ):
            result = await loop.run(
                spec=_make_spec(),
                triage=_make_triage(),
                metrics=FixMetrics(),
                workspace=_make_workspace(),
            )

        assert result.success is False
        assert len(reviewer_calls) == 0, "Reviewer must not be called on coder failure"

        # Check comment mentions failure
        posted = [str(call) for call in pipeline._post_comment.call_args_list]
        assert any("failed" in c.lower() for c in posted), f"Expected 'failed' in comment, got: {posted}"


# ---------------------------------------------------------------------------
# TS-NS-3: Double parse failure posts distinct comment, no empty feedback
# Requirement: NS-REQ-3
# ---------------------------------------------------------------------------


class TestDoubleParseFailure:
    """Verify double parse failure posts parse-error comment, no empty feedback.

    Test Spec: TS-NS-3
    Requirements: NS-REQ-3
    """

    async def test_parse_failure_does_not_inject_empty_feedback(self) -> None:
        """AC-3: Parse failure result is not used as review_feedback for next attempt."""
        pipeline = _make_mock_pipeline()
        pipeline._config.orchestrator.max_retries = 1  # Two attempts total
        loop = CoderReviewerLoop(pipeline)

        coder_outcome = _make_coder_outcome(response="Attempted fix.")
        coder_outcome.status = "completed"
        coder_outcome.is_transport_error = False

        parse_failure = FixReviewResult(
            is_parse_failure=True,
            overall_verdict="FAIL",
            verdicts=[],
            summary="",
        )

        coder_phase_calls: list[object] = []

        async def mock_coder(*args: object, **kwargs: object) -> object:
            # Capture the review_feedback argument (positional arg 5)
            feedback = args[5] if len(args) > 5 else kwargs.get("review_feedback")
            coder_phase_calls.append(feedback)
            return coder_outcome

        p1, p2 = _patch_model_resolution()
        with (
            p1,
            p2,
            patch.object(loop, "_run_coder_phase", side_effect=mock_coder),
            patch.object(loop, "_run_reviewer_phase", new_callable=AsyncMock, return_value=parse_failure),
        ):
            result = await loop.run(
                spec=_make_spec(),
                triage=_make_triage(),
                metrics=FixMetrics(),
                workspace=_make_workspace(),
            )

        assert result.success is False
        # First coder call should have None feedback, second should ALSO have None
        # (parse failure should not be injected)
        assert len(coder_phase_calls) == 2
        assert coder_phase_calls[0] is None, "First attempt should have no feedback"
        assert coder_phase_calls[1] is None, "Second attempt should have no feedback (parse failure not injected)"

    async def test_parse_failure_posts_parse_error_comment(self) -> None:
        """AC-3: Comment states review could not be parsed, not 'Overall verdict: FAIL'."""
        pipeline = _make_mock_pipeline()
        pipeline._config.orchestrator.max_retries = 0  # One attempt only
        loop = CoderReviewerLoop(pipeline)

        coder_outcome = _make_coder_outcome(response="")
        coder_outcome.status = "completed"
        coder_outcome.is_transport_error = False

        parse_failure = FixReviewResult(
            is_parse_failure=True,
            overall_verdict="FAIL",
            verdicts=[],
            summary="",
        )

        # Use the real _format_review_comment from FixPipeline
        from afcore.nightshift.fix_pipeline import FixPipeline

        pipeline._format_review_comment = FixPipeline._format_review_comment.__get__(pipeline, type(pipeline))

        p1, p2 = _patch_model_resolution()
        with (
            p1,
            p2,
            patch.object(loop, "_run_coder_phase", new_callable=AsyncMock, return_value=coder_outcome),
            patch.object(loop, "_run_reviewer_phase", new_callable=AsyncMock, return_value=parse_failure),
        ):
            await loop.run(
                spec=_make_spec(),
                triage=_make_triage(),
                metrics=FixMetrics(),
                workspace=_make_workspace(),
            )

        posted = [str(call) for call in pipeline._post_comment.call_args_list]
        # Should contain parse failure indicator
        assert any("could not be parsed" in c.lower() for c in posted), (
            f"Expected 'could not be parsed' in comment, got: {posted}"
        )


# ---------------------------------------------------------------------------
# TS-NS-4: _format_review_comment renders is_parse_failure explicitly
# Requirement: NS-REQ-4
# ---------------------------------------------------------------------------


class TestFormatReviewCommentParseFailure:
    """Verify _format_review_comment renders parse failure explicitly.

    Test Spec: TS-NS-4
    Requirements: NS-REQ-4
    """

    def test_parse_failure_renders_parse_error_indicator(self) -> None:
        """AC-4: Output includes parse-failure indicator, not bare 'Overall verdict: FAIL'."""
        from afcore.nightshift.fix_pipeline import FixPipeline

        review = FixReviewResult(
            is_parse_failure=True,
            overall_verdict="FAIL",
            verdicts=[],
            summary="",
        )
        # Use a minimal mock pipeline to call the static-like method
        pipeline = MagicMock(spec=FixPipeline)
        output = FixPipeline._format_review_comment(pipeline, review)

        assert "parse" in output.lower(), f"Expected 'parse' in output, got: {output!r}"
        assert "could not be parsed" in output.lower(), f"Expected 'could not be parsed' in output, got: {output!r}"
        # Should NOT render "Overall verdict: FAIL" as the main content
        assert "**overall verdict:** fail" not in output.lower(), (
            f"Parse failure should not render as 'Overall verdict: FAIL', got: {output!r}"
        )

    def test_normal_fail_still_renders_verdict(self) -> None:
        """AC-5: Normal FAIL (not parse failure) still renders verdict as before."""
        from afcore.nightshift.fix_pipeline import FixPipeline, FixReviewVerdict

        review = FixReviewResult(
            is_parse_failure=False,
            overall_verdict="FAIL",
            verdicts=[FixReviewVerdict(criterion_id="AC-1", verdict="FAIL", evidence="broken")],
            summary="Tests still fail",
        )
        pipeline = MagicMock(spec=FixPipeline)
        # Need to use the real _render_verdict_section
        pipeline._render_verdict_section = FixPipeline._render_verdict_section
        output = FixPipeline._format_review_comment(pipeline, review)

        assert "**Overall verdict:** FAIL" in output
        assert "AC-1" in output
        assert "Tests still fail" in output

    def test_normal_pass_renders_verdict(self) -> None:
        """AC-5: Normal PASS renders verdict as before."""
        from afcore.nightshift.fix_pipeline import FixPipeline, FixReviewVerdict

        review = FixReviewResult(
            is_parse_failure=False,
            overall_verdict="PASS",
            verdicts=[FixReviewVerdict(criterion_id="AC-1", verdict="PASS", evidence="ok")],
            summary="All criteria met",
        )
        pipeline = MagicMock(spec=FixPipeline)
        pipeline._render_verdict_section = FixPipeline._render_verdict_section
        output = FixPipeline._format_review_comment(pipeline, review)

        assert "**Overall verdict:** PASS" in output
        assert "AC-1" in output
        assert "All criteria met" in output


# ---------------------------------------------------------------------------
# TS-NS-5: Normal PASS/FAIL flow unchanged
# Requirement: NS-REQ-5
# ---------------------------------------------------------------------------


class TestNormalFlowUnchanged:
    """Verify normal PASS/FAIL flow is unchanged by the new checks.

    Test Spec: TS-NS-5
    Requirements: NS-REQ-5
    """

    async def test_normal_pass_returns_success(self) -> None:
        """Normal completed coder + PASS reviewer returns success."""
        pipeline = _make_mock_pipeline()
        loop = CoderReviewerLoop(pipeline)

        coder_outcome = _make_coder_outcome(response="Fixed it.")
        coder_outcome.status = "completed"
        coder_outcome.is_transport_error = False

        review_result = FixReviewResult(overall_verdict="PASS")

        p1, p2 = _patch_model_resolution()
        with (
            p1,
            p2,
            patch.object(loop, "_run_coder_phase", new_callable=AsyncMock, return_value=coder_outcome),
            patch.object(loop, "_run_reviewer_phase", new_callable=AsyncMock, return_value=review_result),
        ):
            result = await loop.run(
                spec=_make_spec(),
                triage=_make_triage(affected_files=["src/main.py"]),
                metrics=FixMetrics(),
                workspace=_make_workspace(),
            )

        assert result.success is True
        assert result.response == "Fixed it."
        assert result.affected_files == ["src/main.py"]

    async def test_normal_fail_exhaustion_posts_manual_intervention(self) -> None:
        """Normal completed coder + FAIL reviewer exhaustion posts manual intervention."""
        pipeline = _make_mock_pipeline()
        pipeline._config.orchestrator.max_retries = 0  # One attempt
        loop = CoderReviewerLoop(pipeline)

        coder_outcome = _make_coder_outcome(response="Attempted fix.")
        coder_outcome.status = "completed"
        coder_outcome.is_transport_error = False

        review_result = FixReviewResult(
            overall_verdict="FAIL",
            summary="Tests fail",
            is_parse_failure=False,
        )

        p1, p2 = _patch_model_resolution()
        with (
            p1,
            p2,
            patch.object(loop, "_run_coder_phase", new_callable=AsyncMock, return_value=coder_outcome),
            patch.object(loop, "_run_reviewer_phase", new_callable=AsyncMock, return_value=review_result),
        ):
            result = await loop.run(
                spec=_make_spec(),
                triage=_make_triage(),
                metrics=FixMetrics(),
                workspace=_make_workspace(),
            )

        assert result.success is False
        posted = [str(call) for call in pipeline._post_comment.call_args_list]
        assert any("manual intervention" in c.lower() for c in posted), (
            f"Expected 'manual intervention' in comment, got: {posted}"
        )

    async def test_normal_fail_with_retries_updates_feedback(self) -> None:
        """FAIL with retries remaining updates review_feedback for next coder."""
        pipeline = _make_mock_pipeline()
        pipeline._config.orchestrator.max_retries = 1
        loop = CoderReviewerLoop(pipeline)

        coder_outcome = _make_coder_outcome(response="Attempted fix.")
        coder_outcome.status = "completed"
        coder_outcome.is_transport_error = False

        from afcore.nightshift.fix_pipeline import FixReviewVerdict

        fail_review = FixReviewResult(
            overall_verdict="FAIL",
            summary="Tests fail",
            verdicts=[FixReviewVerdict(criterion_id="AC-1", verdict="FAIL", evidence="broken")],
            is_parse_failure=False,
        )
        pass_review = FixReviewResult(overall_verdict="PASS")

        reviewer_call_count = 0
        coder_phase_calls: list[object] = []

        async def mock_coder(*args: object, **kwargs: object) -> object:
            feedback = args[5] if len(args) > 5 else kwargs.get("review_feedback")
            coder_phase_calls.append(feedback)
            return coder_outcome

        async def mock_reviewer(*args: object, **kwargs: object) -> FixReviewResult:
            nonlocal reviewer_call_count
            reviewer_call_count += 1
            if reviewer_call_count == 1:
                return fail_review
            return pass_review

        p1, p2 = _patch_model_resolution()
        with (
            p1,
            p2,
            patch.object(loop, "_run_coder_phase", side_effect=mock_coder),
            patch.object(loop, "_run_reviewer_phase", side_effect=mock_reviewer),
        ):
            result = await loop.run(
                spec=_make_spec(),
                triage=_make_triage(affected_files=["src/main.py"]),
                metrics=FixMetrics(),
                workspace=_make_workspace(),
            )

        assert result.success is True
        assert len(coder_phase_calls) == 2
        # First attempt has no feedback
        assert coder_phase_calls[0] is None
        # Second attempt has the FAIL review as feedback
        assert coder_phase_calls[1] is fail_review
