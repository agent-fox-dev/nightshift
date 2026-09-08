"""Regression tests for manual branch merge strategy handoff."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from afcore.core.config import AgentFoxConfig, WorkspaceConfig
from afcore.nightshift.fix_pipeline import FixPipeline, TriageResult
from afcore.nightshift.spec_builder import InMemorySpec
from afcore.workspace import WorkspaceInfo
from afissues.labels import LABEL_FIXED
from afissues.protocol import IssueResult


def _make_pipeline(platform: MagicMock) -> FixPipeline:
    config = AgentFoxConfig(
        workspace=WorkspaceConfig(
            merge_strategy="branch",
            integration_branch="main",
        ),
    )
    return FixPipeline(config=config, platform=platform)


def _make_issue() -> IssueResult:
    return IssueResult(
        number=65,
        title="Retain manual-review branch",
        html_url="https://github.com/test/repo/issues/65",
    )


def _make_spec() -> InMemorySpec:
    return InMemorySpec(
        issue_number=65,
        title="Retain manual-review branch",
        task_prompt="Retain the branch",
        system_context="A manual reviewer must merge this fix.",
        branch_name="fix/65-retain-manual-review-branch",
    )


def _make_workspace() -> WorkspaceInfo:
    return WorkspaceInfo(
        path=Path("/tmp/test-worktree"),
        branch="fix/65-retain-manual-review-branch",
        spec_name="fix-issue-65",
        task_group=0,
    )


class TestBranchMergeStrategy:
    """Branch mode hands completed work to a human without closing the issue."""

    @pytest.mark.asyncio
    async def test_integration_returns_branch_only_and_posts_best_effort_comment(self) -> None:
        platform = MagicMock()
        platform.add_issue_comment = AsyncMock(side_effect=RuntimeError("temporary API error"))
        pipeline = _make_pipeline(platform)
        pipeline._auto_commit_pending_changes = AsyncMock()  # type: ignore[method-assign]

        with patch(
            "afcore.nightshift.fix_pipeline._workspace_git.get_changed_files",
            new_callable=AsyncMock,
            return_value=["src/fix.py"],
        ):
            status, changed_files = await pipeline._integrate_fix(
                _make_issue(),
                _make_spec(),
                _make_workspace(),
            )

        assert status == "branch_only"
        assert changed_files == ["src/fix.py"]
        platform.add_issue_comment.assert_awaited_once()
        comment = platform.add_issue_comment.await_args.args[1]
        assert "review and merge manually" in comment
        assert "merged into" not in comment

    @pytest.mark.asyncio
    async def test_branch_only_result_leaves_issue_open_without_completion_comment(self) -> None:
        platform = MagicMock()
        platform.add_issue_comment = AsyncMock()
        platform.assign_label = AsyncMock()
        platform.close_issue = AsyncMock()
        pipeline = _make_pipeline(platform)

        await pipeline._handle_result(_make_issue(), _make_spec(), "branch_only")

        platform.close_issue.assert_not_awaited()
        assigned_labels = [call.args[1] for call in platform.assign_label.call_args_list]
        assert LABEL_FIXED not in assigned_labels
        platform.add_issue_comment.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_process_issue_keeps_branch_for_branch_only_result(self) -> None:
        platform = MagicMock()
        pipeline = _make_pipeline(platform)
        workspace = _make_workspace()
        pipeline._setup_workspace = AsyncMock(return_value=workspace)  # type: ignore[method-assign]
        pipeline._post_comment = AsyncMock()  # type: ignore[method-assign]
        pipeline._run_triage = AsyncMock(return_value=TriageResult())  # type: ignore[method-assign]
        pipeline._gather_context = MagicMock(return_value=("", ""))  # type: ignore[method-assign]
        pipeline._coder_review_loop = AsyncMock(return_value=True)  # type: ignore[method-assign]
        pipeline._ingest_knowledge = MagicMock()  # type: ignore[method-assign]
        pipeline._integrate_fix = AsyncMock(return_value=("branch_only", []))  # type: ignore[method-assign]
        pipeline._handle_result = AsyncMock()  # type: ignore[method-assign]
        pipeline._cleanup_workspace = AsyncMock()  # type: ignore[method-assign]

        metrics = await pipeline.process_issue(_make_issue(), issue_body="Issue details")

        pipeline._cleanup_workspace.assert_awaited_once_with(workspace, keep_branch=True)
        assert metrics.outcome == "branch_only"
