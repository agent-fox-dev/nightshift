"""Merge agent unit tests.

Test Spec: TS-45-9 through TS-45-13, TS-45-E6, TS-NS-1, TS-NS-2, TS-NS-4
Requirements: 45-REQ-4.1 through 45-REQ-4.5, 45-REQ-4.E1, 45-REQ-4.E2,
              NS-REQ-1, NS-REQ-2, NS-REQ-4
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from afcore.workspace.merge_agent import (
    MERGE_AGENT_SYSTEM_PROMPT,
    MergeAgentResult,
    _check_conflicts_resolved,
    run_merge_agent,
)

# ---------------------------------------------------------------------------
# Helpers: lightweight SessionOutcome stand-in for unit tests
# ---------------------------------------------------------------------------


@dataclass
class _FakeOutcome:
    """Minimal stand-in for ``afaudit.sink.SessionOutcome``."""

    status: str = "completed"
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    duration_ms: int = 0
    error_message: str | None = None
    response: str = ""
    is_transport_error: bool = False


def _make_outcome(*, status: str = "completed", **kwargs) -> _FakeOutcome:
    return _FakeOutcome(status=status, **kwargs)


class TestAgentSpawnedOnMergeFailure:
    """TS-45-9: Agent spawned when all deterministic strategies fail."""

    @pytest.mark.asyncio
    async def test_agent_returns_true_on_success(self, tmp_path: Path) -> None:
        """run_merge_agent returns MergeAgentResult with success=True when conflicts are resolved."""
        with (
            patch(
                "afcore.workspace.merge_agent._run_agent_session",
                new_callable=AsyncMock,
                return_value=_make_outcome(status="completed"),
            ) as mock_session,
            patch(
                "afcore.workspace.merge_agent._check_conflicts_resolved",
                new_callable=AsyncMock,
                return_value=True,
            ),
        ):
            result = await run_merge_agent(
                worktree_path=tmp_path,
                conflict_output="CONFLICT (content): Merge conflict in foo.py",
                model_id="claude-opus-4-6",
            )
            assert isinstance(result, MergeAgentResult)
            assert result.success is True
            assert result.outcome is not None
            mock_session.assert_called_once()

    @pytest.mark.asyncio
    async def test_agent_returns_false_on_failure(self, tmp_path: Path) -> None:
        """run_merge_agent returns MergeAgentResult with success=False when agent fails to resolve."""
        with patch(
            "afcore.workspace.merge_agent._run_agent_session",
            new_callable=AsyncMock,
            return_value=_make_outcome(status="failed"),
        ):
            result = await run_merge_agent(
                worktree_path=tmp_path,
                conflict_output="CONFLICT",
                model_id="claude-opus-4-6",
            )
            assert isinstance(result, MergeAgentResult)
            assert result.success is False
            # Outcome should still be present for accounting
            assert result.outcome is not None


class TestAgentUsesAdvancedModel:
    """TS-45-10: Agent uses the ADVANCED model tier."""

    @pytest.mark.asyncio
    async def test_model_id_passed_to_session(self, tmp_path: Path) -> None:
        """The model_id argument is passed through to the agent session."""
        with (
            patch(
                "afcore.workspace.merge_agent._run_agent_session",
                new_callable=AsyncMock,
                return_value=_make_outcome(status="completed"),
            ) as mock_session,
            patch(
                "afcore.workspace.merge_agent._check_conflicts_resolved",
                new_callable=AsyncMock,
                return_value=True,
            ),
        ):
            await run_merge_agent(
                worktree_path=tmp_path,
                conflict_output="CONFLICT",
                model_id="claude-opus-4-6",
            )
            # Verify model_id was passed
            call_kwargs = mock_session.call_args
            assert call_kwargs is not None
            # model_id should appear in the call args
            all_args = str(call_kwargs)
            assert "claude-opus-4-6" in all_args


class TestAgentPromptConflictOnly:
    """TS-45-11: Agent prompt restricts to conflict resolution only."""

    def test_system_prompt_mentions_merge_conflict(self) -> None:
        """System prompt contains 'merge conflict'."""
        assert "merge conflict" in MERGE_AGENT_SYSTEM_PROMPT.lower()

    def test_system_prompt_mentions_only(self) -> None:
        """System prompt indicates conflict resolution only."""
        assert "only" in MERGE_AGENT_SYSTEM_PROMPT.lower()

    def test_system_prompt_prohibits_refactoring(self) -> None:
        """System prompt prohibits refactoring."""
        prompt_lower = MERGE_AGENT_SYSTEM_PROMPT.lower()
        assert "refactor" in prompt_lower

    def test_system_prompt_prohibits_test_fixes(self) -> None:
        """System prompt prohibits test fixes."""
        prompt_lower = MERGE_AGENT_SYSTEM_PROMPT.lower()
        assert "test" in prompt_lower

    def test_system_prompt_prohibits_feature_changes(self) -> None:
        """System prompt prohibits feature changes."""
        prompt_lower = MERGE_AGENT_SYSTEM_PROMPT.lower()
        assert "feature" in prompt_lower


class TestAgentReceivesConflictOutput:
    """TS-45-12: Agent receives git conflict output as context."""

    @pytest.mark.asyncio
    async def test_conflict_output_in_prompt(self, tmp_path: Path) -> None:
        """Conflict output is included in the agent session context."""
        conflict_text = "CONFLICT (content): Merge conflict in src/main.py"
        captured_prompt: list[str] = []

        async def fake_session(
            worktree_path: Path,
            system_prompt: str,
            task_prompt: str,
            model_id: str,
            **kwargs,
        ) -> _FakeOutcome:
            captured_prompt.append(task_prompt)
            return _make_outcome(status="completed")

        with (
            patch(
                "afcore.workspace.merge_agent._run_agent_session",
                side_effect=fake_session,
            ),
            patch(
                "afcore.workspace.merge_agent._check_conflicts_resolved",
                new_callable=AsyncMock,
                return_value=True,
            ),
        ):
            await run_merge_agent(
                worktree_path=tmp_path,
                conflict_output=conflict_text,
                model_id="claude-opus-4-6",
            )

        assert len(captured_prompt) == 1
        assert conflict_text in captured_prompt[0]


class TestAgentResolutionCompletesMerge:
    """TS-45-13: After agent resolution, merge is completed."""

    @pytest.mark.asyncio
    async def test_resolution_returns_true(self, tmp_path: Path) -> None:
        """When agent resolves conflicts, run_merge_agent returns MergeAgentResult with success=True."""
        with (
            patch(
                "afcore.workspace.merge_agent._run_agent_session",
                new_callable=AsyncMock,
                return_value=_make_outcome(status="completed"),
            ),
            patch(
                "afcore.workspace.merge_agent._check_conflicts_resolved",
                new_callable=AsyncMock,
                return_value=True,
            ),
        ):
            result = await run_merge_agent(
                worktree_path=tmp_path,
                conflict_output="CONFLICT",
                model_id="claude-opus-4-6",
            )
            assert result.success is True


class TestAgentApiErrorTreatedAsFailure:
    """TS-45-E6: Agent API errors treated as failure."""

    @pytest.mark.asyncio
    async def test_api_error_returns_false(self, tmp_path: Path) -> None:
        """When agent session raises an exception, run_merge_agent returns failure with no outcome."""
        with patch(
            "afcore.workspace.merge_agent._run_agent_session",
            new_callable=AsyncMock,
            side_effect=RuntimeError("API timeout"),
        ):
            result = await run_merge_agent(
                worktree_path=tmp_path,
                conflict_output="CONFLICT",
                model_id="claude-opus-4-6",
            )
            assert result.success is False
            assert result.outcome is None

    @pytest.mark.asyncio
    async def test_timeout_error_returns_false(self, tmp_path: Path) -> None:
        """When agent session times out, run_merge_agent returns failure with no outcome."""
        with patch(
            "afcore.workspace.merge_agent._run_agent_session",
            new_callable=AsyncMock,
            side_effect=TimeoutError("session timed out"),
        ):
            result = await run_merge_agent(
                worktree_path=tmp_path,
                conflict_output="CONFLICT",
                model_id="claude-opus-4-6",
            )
            assert result.success is False
            assert result.outcome is None


# ---------------------------------------------------------------------------
# TS-NS-1, TS-NS-2, TS-NS-4: Real-repo tests for _check_conflicts_resolved
# Requirements: NS-REQ-1, NS-REQ-2, NS-REQ-4
# Issue #72: git diff --check passes on staged-but-uncommitted resolution
# ---------------------------------------------------------------------------


def _init_repo(path: Path) -> Path:
    """Create a minimal git repo with initial commit."""
    path.mkdir(exist_ok=True)
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    (path / "README.md").write_text("init\n")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    return path


def _create_merge_conflict(repo: Path) -> None:
    """Set up a merge conflict on file 'shared.py' between main and 'feature'.

    After this function, the repo is on 'main' (or default branch) with
    an active merge conflict (unmerged index entries) in 'shared.py'.
    """
    # Get current branch name
    result = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    main_branch = result.stdout.strip()

    # Create conflicting content on a feature branch
    subprocess.run(
        ["git", "checkout", "-b", "feature"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    (repo / "shared.py").write_text("feature content\n")
    subprocess.run(["git", "add", "shared.py"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "feature change"],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    # Create conflicting content on main
    subprocess.run(
        ["git", "checkout", main_branch],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    (repo / "shared.py").write_text("main content\n")
    subprocess.run(["git", "add", "shared.py"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "main change"],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    # Attempt merge — will conflict
    subprocess.run(
        ["git", "merge", "feature"],
        cwd=repo,
        capture_output=True,
    )


class TestCheckConflictsResolvedRealRepo:
    """TS-NS-4: Real-repo tests for _check_conflicts_resolved.

    Exercises the function against a real temporary git repository in
    three states without patching. Requirements: NS-REQ-1, NS-REQ-2, NS-REQ-4.
    """

    @pytest.mark.asyncio
    async def test_unresolved_conflict_returns_false(self, tmp_path: Path) -> None:
        """TS-NS-1 / AC-1: Unmerged index entries → returns False."""
        repo = _init_repo(tmp_path / "repo")
        _create_merge_conflict(repo)

        # Verify we actually have unmerged entries
        result = subprocess.run(
            ["git", "ls-files", "-u"],
            cwd=repo,
            capture_output=True,
            text=True,
        )
        assert result.stdout.strip(), "Precondition: unmerged entries must exist"

        assert await _check_conflicts_resolved(repo) is False

    @pytest.mark.asyncio
    async def test_staged_but_not_committed_returns_false(self, tmp_path: Path) -> None:
        """TS-NS-2 / AC-2: Resolution staged but not committed → returns False."""
        repo = _init_repo(tmp_path / "repo")
        _create_merge_conflict(repo)

        # Resolve the conflict by writing clean content and staging
        (repo / "shared.py").write_text("resolved content\n")
        subprocess.run(["git", "add", "shared.py"], cwd=repo, check=True, capture_output=True)

        # Verify: no unmerged entries, but staged changes exist
        result = subprocess.run(
            ["git", "ls-files", "-u"],
            cwd=repo,
            capture_output=True,
            text=True,
        )
        assert not result.stdout.strip(), "Precondition: no unmerged entries after add"

        result = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=repo,
            capture_output=True,
        )
        assert result.returncode != 0, "Precondition: staged changes exist"

        # The old check (git diff --check) would return True here.
        # The strengthened check must return False.
        assert await _check_conflicts_resolved(repo) is False

    @pytest.mark.asyncio
    async def test_resolved_and_committed_returns_true(self, tmp_path: Path) -> None:
        """TS-NS-4 state (c): Resolution committed → returns True."""
        repo = _init_repo(tmp_path / "repo")
        _create_merge_conflict(repo)

        # Resolve and commit
        (repo / "shared.py").write_text("resolved content\n")
        subprocess.run(["git", "add", "shared.py"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "resolve conflict"],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        assert await _check_conflicts_resolved(repo) is True

    @pytest.mark.asyncio
    async def test_clean_repo_no_merge_returns_true(self, tmp_path: Path) -> None:
        """A clean repo with no merge in progress returns True.

        This is the normal state after a successful non-conflicting merge
        or when no merge is happening at all.
        """
        repo = _init_repo(tmp_path / "repo")

        assert await _check_conflicts_resolved(repo) is True


# ---------------------------------------------------------------------------
# Issue #74: Merge-agent accounting, audit, and budget tests
# ---------------------------------------------------------------------------


class TestMergeAgentSessionAccounting:
    """Tests for issue #74: merge-agent session accounting.

    Verifies that run_merge_agent returns SessionOutcome data,
    passes sink_dispatcher / run_id / max_budget_usd through to
    _run_agent_session, and preserves deliberate omissions (effort,
    compaction, cache_policy).
    """

    @pytest.mark.asyncio
    async def test_outcome_returned_with_token_counts(self, tmp_path: Path) -> None:
        """AC-1: The SessionOutcome with token counts is returned."""
        outcome = _make_outcome(
            status="completed",
            input_tokens=100,
            output_tokens=50,
            cache_read_input_tokens=10,
            cache_creation_input_tokens=5,
        )
        with (
            patch(
                "afcore.workspace.merge_agent._run_agent_session",
                new_callable=AsyncMock,
                return_value=outcome,
            ),
            patch(
                "afcore.workspace.merge_agent._check_conflicts_resolved",
                new_callable=AsyncMock,
                return_value=True,
            ),
        ):
            result = await run_merge_agent(
                worktree_path=tmp_path,
                conflict_output="CONFLICT",
                model_id="claude-opus-4-6",
            )
            assert result.success is True
            assert result.outcome is outcome
            assert result.outcome.input_tokens == 100
            assert result.outcome.output_tokens == 50

    @pytest.mark.asyncio
    async def test_sink_dispatcher_and_run_id_forwarded(self, tmp_path: Path) -> None:
        """AC-2: sink_dispatcher and run_id are forwarded to _run_agent_session."""
        sentinel_sink = object()
        with (
            patch(
                "afcore.workspace.merge_agent._run_agent_session",
                new_callable=AsyncMock,
                return_value=_make_outcome(status="completed"),
            ) as mock_session,
            patch(
                "afcore.workspace.merge_agent._check_conflicts_resolved",
                new_callable=AsyncMock,
                return_value=True,
            ),
        ):
            await run_merge_agent(
                worktree_path=tmp_path,
                conflict_output="CONFLICT",
                model_id="claude-opus-4-6",
                sink_dispatcher=sentinel_sink,
                run_id="test-run-123",
            )
            call_kwargs = mock_session.call_args
            assert call_kwargs.kwargs["sink_dispatcher"] is sentinel_sink
            assert call_kwargs.kwargs["run_id"] == "test-run-123"

    @pytest.mark.asyncio
    async def test_max_budget_usd_forwarded(self, tmp_path: Path) -> None:
        """AC-3: max_budget_usd is forwarded to _run_agent_session."""
        with (
            patch(
                "afcore.workspace.merge_agent._run_agent_session",
                new_callable=AsyncMock,
                return_value=_make_outcome(status="completed"),
            ) as mock_session,
            patch(
                "afcore.workspace.merge_agent._check_conflicts_resolved",
                new_callable=AsyncMock,
                return_value=True,
            ),
        ):
            await run_merge_agent(
                worktree_path=tmp_path,
                conflict_output="CONFLICT",
                model_id="claude-opus-4-6",
                max_budget_usd=5.0,
            )
            call_kwargs = mock_session.call_args
            assert call_kwargs.kwargs["max_budget_usd"] == 5.0

    @pytest.mark.asyncio
    async def test_deliberate_omissions_preserved(self, tmp_path: Path) -> None:
        """AC-4: effort, compaction, cache_policy are NOT passed to run_session.

        The merge agent intentionally omits these per issue #20.
        This test patches run_session (not _run_agent_session) to
        verify the omission at the session-runner boundary.
        """
        with (
            patch(
                "afcore.core.config.load_config",
            ),
            patch(
                "afcore.session.session.run_session",
                new_callable=AsyncMock,
                return_value=_make_outcome(status="completed"),
            ) as mock_run_session,
        ):
            from afcore.workspace.merge_agent import _run_agent_session

            await _run_agent_session(
                worktree_path=tmp_path,
                system_prompt="test",
                task_prompt="test",
                model_id="claude-opus-4-6",
            )

            call_kwargs = mock_run_session.call_args
            # These must not be present in the call
            assert "effort" not in call_kwargs.kwargs
            assert "compaction" not in call_kwargs.kwargs
            assert "cache_policy" not in call_kwargs.kwargs

    @pytest.mark.asyncio
    async def test_run_session_receives_sink_and_budget(self, tmp_path: Path) -> None:
        """Verify that _run_agent_session passes sink_dispatcher, run_id,
        and max_budget_usd through to run_session."""
        sentinel_sink = object()
        with (
            patch(
                "afcore.core.config.load_config",
            ),
            patch(
                "afcore.session.session.run_session",
                new_callable=AsyncMock,
                return_value=_make_outcome(status="completed"),
            ) as mock_run_session,
        ):
            from afcore.workspace.merge_agent import _run_agent_session

            await _run_agent_session(
                worktree_path=tmp_path,
                system_prompt="test",
                task_prompt="test",
                model_id="claude-opus-4-6",
                sink_dispatcher=sentinel_sink,
                run_id="run-42",
                max_budget_usd=3.5,
            )

            call_kwargs = mock_run_session.call_args
            assert call_kwargs.kwargs["sink_dispatcher"] is sentinel_sink
            assert call_kwargs.kwargs["run_id"] == "run-42"
            assert call_kwargs.kwargs["max_budget_usd"] == 3.5

    @pytest.mark.asyncio
    async def test_failed_session_still_returns_outcome(self, tmp_path: Path) -> None:
        """When the session fails (non-completed status), the outcome is still
        returned so its tokens can be accounted for."""
        outcome = _make_outcome(
            status="failed",
            input_tokens=80,
            output_tokens=20,
        )
        with patch(
            "afcore.workspace.merge_agent._run_agent_session",
            new_callable=AsyncMock,
            return_value=outcome,
        ):
            result = await run_merge_agent(
                worktree_path=tmp_path,
                conflict_output="CONFLICT",
                model_id="claude-opus-4-6",
            )
            assert result.success is False
            assert result.outcome is outcome
            assert result.outcome.input_tokens == 80

    @pytest.mark.asyncio
    async def test_exception_returns_no_outcome(self, tmp_path: Path) -> None:
        """When _run_agent_session raises, outcome is None (no tokens to account for)."""
        with patch(
            "afcore.workspace.merge_agent._run_agent_session",
            new_callable=AsyncMock,
            side_effect=RuntimeError("boom"),
        ):
            result = await run_merge_agent(
                worktree_path=tmp_path,
                conflict_output="CONFLICT",
                model_id="claude-opus-4-6",
            )
            assert result.success is False
            assert result.outcome is None
