"""Push sites force-push with a lease now that remote refs survive (issue #34).

``create_worktree`` no longer deletes the remote fix branch, so a
``fix/N`` ref left on the remote by an earlier attempt would reject a
plain push as a non-fast-forward.  The PR-mode and carry-patch pushes in
``_integrate_fix`` therefore pass ``force=True`` (``--force-with-lease``),
which updates the PR head / hub patch branch in place while still refusing
to overwrite commits the fetched tracking ref does not know about.

Requirements: NS-REQ-34 (issue #34, AC-2), 02-REQ-4.2, 03-REQ-1.1
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from afcore.core.config import AgentFoxConfig, WorkspaceConfig
from afcore.nightshift.fix_pipeline import FixPipeline
from afcore.nightshift.spec_builder import InMemorySpec
from afcore.workspace import WorkspaceInfo
from afissues.protocol import IssueResult, PrResult


def _issue(number: int = 42) -> IssueResult:
    return IssueResult(number=number, title="Broken thing", html_url=f"https://example.test/issues/{number}")


def _spec(number: int = 42) -> InMemorySpec:
    return InMemorySpec(
        issue_number=number,
        title="Broken thing",
        task_prompt="Fix it",
        system_context="ctx",
        branch_name=f"fix/{number}-broken-thing",
    )


def _workspace(branch: str = "fix/42-broken-thing") -> WorkspaceInfo:
    return WorkspaceInfo(path=Path("/tmp/wt"), branch=branch, spec_name="fix-issue-42", task_group=0)


def _platform() -> MagicMock:
    platform = MagicMock()
    platform._owner = "owner"
    platform._repo = "repo"
    platform.create_pr = AsyncMock(return_value=PrResult(html_url="https://example.test/pull/7", number=7))
    platform.add_issue_comment = AsyncMock()
    return platform


class TestPrModePushUsesLease:
    async def test_pr_mode_push_is_forced_with_lease(self) -> None:
        """AC-2: re-processing under ``pr`` updates the PR head in place."""
        platform = _platform()
        config = AgentFoxConfig(workspace=WorkspaceConfig(merge_strategy="pr", integration_branch="main"))
        pipeline = FixPipeline(config=config, platform=platform)

        with (
            patch("afcore.nightshift.platform_factory.create_platform_safe", return_value=platform),
            patch.object(pipeline, "_auto_commit_pending_changes", AsyncMock()),
            patch(
                "afcore.nightshift.fix_pipeline._workspace_git.push_to_remote",
                new_callable=AsyncMock,
                return_value=True,
            ) as push,
            patch(
                "afcore.nightshift.fix_pipeline._workspace_git.get_changed_files",
                new_callable=AsyncMock,
                return_value=["file.py"],
            ),
        ):
            status, _ = await pipeline._integrate_fix(_issue(), _spec(), _workspace())

        assert status == "pr_created"
        push.assert_awaited_once()
        assert push.await_args.kwargs.get("force") is True
        assert push.await_args.args[1] == "fix/42-broken-thing"


class TestCarryPatchPushUsesLease:
    async def test_carry_patch_push_is_forced_with_lease(self) -> None:
        """The hub patch branch is updated in place, not rejected as non-ff."""
        config = AgentFoxConfig(workspace=WorkspaceConfig(merge_strategy="direct", integration_branch="main"))
        config.carry_patch.enabled = True
        config.carry_patch.workspace = "ws-1"
        hub_client = MagicMock()
        hub_client.add_patch = AsyncMock()
        hub_client.submit_rebuild = AsyncMock(return_value=MagicMock(id="job-1"))
        pipeline = FixPipeline(config=config, platform=_platform(), hub_client=hub_client, workspace_slug="ws-1")

        with (
            patch.object(pipeline, "_auto_commit_pending_changes", AsyncMock()),
            patch.object(pipeline, "_harvest_and_push", AsyncMock()),
            patch(
                "afcore.nightshift.fix_pipeline._workspace_git.push_to_remote",
                new_callable=AsyncMock,
                return_value=True,
            ) as push,
            patch(
                "afcore.nightshift.fix_pipeline.poll_rebuild",
                new_callable=AsyncMock,
                return_value=MagicMock(id="job-1", status="completed"),
            ),
        ):
            await pipeline._integrate_fix(_issue(), _spec(), _workspace())

        push.assert_awaited_once()
        assert push.await_args.kwargs.get("force") is True
