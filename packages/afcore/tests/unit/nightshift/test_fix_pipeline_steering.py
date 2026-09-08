"""Pipeline-level tests for steering directive injection (issue #78).

Verifies that ``load_steering`` is called and its content appears in the
assembled system prompts for all three fix-pipeline session types: coder,
reviewer, and triage (maintainer).

Requirements: NS-REQ-1 through NS-REQ-5 (issue #78)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from afcore.nightshift.fix_pipeline import (
    _MAX_STEERING_CHARS,
    FixPipeline,
    TriageResult,
)
from afcore.nightshift.spec_builder import InMemorySpec
from afcore.workspace import WorkspaceInfo

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_STEERING_CONTENT = "Never touch packages/afhub.\nAlways add a regression test."


def _make_config() -> MagicMock:
    config = MagicMock()
    config.archetypes.overrides.get.return_value = None
    config.security = None
    return config


def _make_spec() -> InMemorySpec:
    return InMemorySpec(
        issue_number=42,
        title="test issue",
        task_prompt="Fix the issue: test issue (#42)",
        system_context="Issue body context.",
        branch_name="fix/42-test-issue",
    )


def _make_triage(*, with_criteria: bool = False) -> TriageResult:
    if with_criteria:
        from afcore.nightshift.fix_pipeline import AcceptanceCriterion

        return TriageResult(
            summary="Summary",
            criteria=[
                AcceptanceCriterion(
                    id="AC-1",
                    description="First criterion",
                    preconditions="File exists",
                    expected="File is read",
                    assertion="assert read",
                ),
            ],
        )
    return TriageResult()


def _make_pipeline(repo_root: Path | None = None) -> FixPipeline:
    config = _make_config()
    return FixPipeline(
        config=config,
        platform=MagicMock(),
        repo_root=repo_root or Path("/tmp/fake-repo"),
    )


def _make_workspace() -> WorkspaceInfo:
    return WorkspaceInfo(
        path=Path("/tmp/mock-worktree"),
        branch="fix/42-test-issue",
        spec_name="fix-issue-42",
        task_group=0,
    )


# ---------------------------------------------------------------------------
# NS-REQ-1: Steering appears in coder system prompt
# ---------------------------------------------------------------------------


class TestCoderPromptSteeringInjection:
    """_build_coder_prompt includes steering directives in the system prompt."""

    def test_steering_present_in_coder_system_prompt(self) -> None:
        """When load_steering returns content, the coder system prompt
        contains a '## Steering Directives' section with that content."""
        pipeline = _make_pipeline()
        spec = _make_spec()
        triage = _make_triage()

        with patch(
            "afcore.nightshift.fix_pipeline.load_steering",
            return_value=_STEERING_CONTENT,
        ):
            system_prompt, _ = pipeline._build_coder_prompt(spec, triage)

        assert "## Steering Directives" in system_prompt
        assert _STEERING_CONTENT in system_prompt

    def test_steering_present_in_coder_prompt_with_criteria(self) -> None:
        """Steering is injected even when triage has acceptance criteria."""
        pipeline = _make_pipeline()
        spec = _make_spec()
        triage = _make_triage(with_criteria=True)

        with patch(
            "afcore.nightshift.fix_pipeline.load_steering",
            return_value=_STEERING_CONTENT,
        ):
            system_prompt, _ = pipeline._build_coder_prompt(spec, triage)

        assert "## Steering Directives" in system_prompt
        assert _STEERING_CONTENT in system_prompt


# ---------------------------------------------------------------------------
# NS-REQ-1: Steering appears in reviewer system prompt
# ---------------------------------------------------------------------------


class TestReviewerPromptSteeringInjection:
    """_build_reviewer_prompt includes steering directives in the system prompt."""

    def test_steering_present_in_reviewer_system_prompt(self) -> None:
        """When load_steering returns content, the reviewer system prompt
        contains a '## Steering Directives' section."""
        pipeline = _make_pipeline()
        spec = _make_spec()
        triage = _make_triage()

        with patch(
            "afcore.nightshift.fix_pipeline.load_steering",
            return_value=_STEERING_CONTENT,
        ):
            system_prompt, _ = pipeline._build_reviewer_prompt(spec, triage)

        assert "## Steering Directives" in system_prompt
        assert _STEERING_CONTENT in system_prompt

    def test_steering_present_in_reviewer_prompt_with_criteria(self) -> None:
        """Steering is injected even on the afspec-rendered path."""
        pipeline = _make_pipeline()
        spec = _make_spec()
        triage = _make_triage(with_criteria=True)

        with patch(
            "afcore.nightshift.fix_pipeline.load_steering",
            return_value=_STEERING_CONTENT,
        ):
            system_prompt, _ = pipeline._build_reviewer_prompt(spec, triage)

        assert "## Steering Directives" in system_prompt
        assert _STEERING_CONTENT in system_prompt


# ---------------------------------------------------------------------------
# NS-REQ-1: Steering appears in triage system prompt
# ---------------------------------------------------------------------------


class TestTriagePromptSteeringInjection:
    """_run_triage builds a system prompt that includes steering directives."""

    @pytest.mark.asyncio
    async def test_steering_present_in_triage_system_prompt(self) -> None:
        """When load_steering returns content, the triage session receives
        a system prompt containing '## Steering Directives'."""
        pipeline = _make_pipeline()
        spec = _make_spec()
        workspace = _make_workspace()

        # Mock _run_session to capture the system_prompt keyword argument
        captured_kwargs: dict = {}

        async def fake_run_session(archetype, ws, **kwargs):
            captured_kwargs.update(kwargs)
            outcome = MagicMock()
            outcome.input_tokens = 0
            outcome.output_tokens = 0
            outcome.cache_read_input_tokens = 0
            outcome.cache_creation_input_tokens = 0
            outcome.response = ""
            outcome.status = "completed"
            outcome.duration_ms = 0
            outcome.error_message = None
            outcome.is_transport_error = False
            return outcome

        with (
            patch(
                "afcore.nightshift.fix_pipeline.load_steering",
                return_value=_STEERING_CONTENT,
            ),
            patch.object(pipeline, "_run_session", side_effect=fake_run_session),
            patch.object(pipeline, "_emit_session_event", return_value=0.0),
            patch.object(pipeline, "_post_comment"),
        ):
            pipeline._run_id = "test-run"
            await pipeline._run_triage(spec, workspace)

        assert "system_prompt" in captured_kwargs, (
            "Expected _run_triage to pass an explicit system_prompt to _run_session"
        )
        system_prompt = captured_kwargs["system_prompt"]
        assert "## Steering Directives" in system_prompt
        assert _STEERING_CONTENT in system_prompt


# ---------------------------------------------------------------------------
# NS-REQ-2: Placeholder-only steering produces no section
# ---------------------------------------------------------------------------


class TestPlaceholderSteeringOmitted:
    """When load_steering returns None, no steering section is produced."""

    def test_no_steering_section_in_coder_prompt(self) -> None:
        pipeline = _make_pipeline()
        spec = _make_spec()
        triage = _make_triage()

        with patch(
            "afcore.nightshift.fix_pipeline.load_steering",
            return_value=None,
        ):
            system_prompt, _ = pipeline._build_coder_prompt(spec, triage)

        assert "## Steering Directives" not in system_prompt

    def test_no_steering_section_in_reviewer_prompt(self) -> None:
        pipeline = _make_pipeline()
        spec = _make_spec()
        triage = _make_triage()

        with patch(
            "afcore.nightshift.fix_pipeline.load_steering",
            return_value=None,
        ):
            system_prompt, _ = pipeline._build_reviewer_prompt(spec, triage)

        assert "## Steering Directives" not in system_prompt

    @pytest.mark.asyncio
    async def test_no_steering_section_in_triage_prompt(self) -> None:
        pipeline = _make_pipeline()
        spec = _make_spec()
        workspace = _make_workspace()

        captured_kwargs: dict = {}

        async def fake_run_session(archetype, ws, **kwargs):
            captured_kwargs.update(kwargs)
            outcome = MagicMock()
            outcome.input_tokens = 0
            outcome.output_tokens = 0
            outcome.cache_read_input_tokens = 0
            outcome.cache_creation_input_tokens = 0
            outcome.response = ""
            outcome.status = "completed"
            outcome.duration_ms = 0
            outcome.error_message = None
            outcome.is_transport_error = False
            return outcome

        with (
            patch(
                "afcore.nightshift.fix_pipeline.load_steering",
                return_value=None,
            ),
            patch.object(pipeline, "_run_session", side_effect=fake_run_session),
            patch.object(pipeline, "_emit_session_event", return_value=0.0),
            patch.object(pipeline, "_post_comment"),
        ):
            pipeline._run_id = "test-run"
            await pipeline._run_triage(spec, workspace)

        system_prompt = captured_kwargs.get("system_prompt", "")
        assert "## Steering Directives" not in system_prompt


# ---------------------------------------------------------------------------
# NS-REQ-4: Oversized steering is truncated
# ---------------------------------------------------------------------------


class TestSteeringTruncation:
    """Oversized steering content is truncated to _MAX_STEERING_CHARS."""

    def test_oversized_steering_truncated_in_coder_prompt(self) -> None:
        """When steering exceeds the budget, it is truncated with a marker."""
        pipeline = _make_pipeline()
        spec = _make_spec()
        triage = _make_triage()

        oversized = "x" * (_MAX_STEERING_CHARS + 5000)

        with patch(
            "afcore.nightshift.fix_pipeline.load_steering",
            return_value=oversized,
        ):
            system_prompt, _ = pipeline._build_coder_prompt(spec, triage)

        assert "## Steering Directives" in system_prompt
        # The full oversized content must NOT appear
        assert oversized not in system_prompt
        # The truncation marker must be present
        assert "<!-- steering truncated -->" in system_prompt

    def test_truncated_content_within_budget(self) -> None:
        """The steering section in the prompt is bounded by _MAX_STEERING_CHARS."""
        pipeline = _make_pipeline()
        spec = _make_spec()
        triage = _make_triage()

        oversized = "y" * (_MAX_STEERING_CHARS * 2)

        with patch(
            "afcore.nightshift.fix_pipeline.load_steering",
            return_value=oversized,
        ):
            system_prompt, _ = pipeline._build_coder_prompt(spec, triage)

        # Extract the steering section from the prompt
        marker = "## Steering Directives\n\n"
        idx = system_prompt.index(marker)
        steering_portion = system_prompt[idx + len(marker) :]
        # The content portion (before the next section or end) should be bounded
        # It should contain the truncated content + marker, not the full oversized string
        assert len(steering_portion) < len(oversized)

    def test_within_budget_not_truncated(self) -> None:
        """Content within the budget is not truncated."""
        pipeline = _make_pipeline()
        spec = _make_spec()
        triage = _make_triage()

        small = "z" * 100

        with patch(
            "afcore.nightshift.fix_pipeline.load_steering",
            return_value=small,
        ):
            system_prompt, _ = pipeline._build_coder_prompt(spec, triage)

        assert small in system_prompt
        assert "<!-- steering truncated -->" not in system_prompt


# ---------------------------------------------------------------------------
# NS-REQ-5: _load_steering_section helper behaviour
# ---------------------------------------------------------------------------


class TestLoadSteeringSection:
    """Unit tests for the _load_steering_section helper."""

    def test_returns_empty_when_no_file(self) -> None:
        """When load_steering returns None, the helper returns ''."""
        pipeline = _make_pipeline()

        with patch(
            "afcore.nightshift.fix_pipeline.load_steering",
            return_value=None,
        ):
            result = pipeline._load_steering_section()

        assert result == ""

    def test_returns_formatted_section(self) -> None:
        """When load_steering returns content, the helper returns a formatted section."""
        pipeline = _make_pipeline()

        with patch(
            "afcore.nightshift.fix_pipeline.load_steering",
            return_value=_STEERING_CONTENT,
        ):
            result = pipeline._load_steering_section()

        assert result.startswith("## Steering Directives\n\n")
        assert _STEERING_CONTENT in result

    def test_passes_repo_root_to_load_steering(self) -> None:
        """The helper passes self._repo_root to load_steering."""
        repo_root = Path("/my/project")
        pipeline = _make_pipeline(repo_root=repo_root)

        with patch(
            "afcore.nightshift.fix_pipeline.load_steering",
            return_value=None,
        ) as mock_ls:
            pipeline._load_steering_section()

        mock_ls.assert_called_once_with(repo_root)
