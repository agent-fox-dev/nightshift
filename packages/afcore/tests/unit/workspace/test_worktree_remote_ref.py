"""create_worktree must never delete remote refs (issue #34).

``create_worktree`` used to run ``git push origin --delete <branch>`` as
part of its stale-state cleanup.  Under ``merge_strategy = "pr"`` that
branch is the head ref of an open pull request, and under carry-patch it
is registered on the hub's patch stack -- re-processing the issue
destroyed that work.  Only the *local* stale branch may be removed; a
stale remote ref cannot block worktree creation, and divergence is
resolved at the push site with a leased force-push.

Requirements: 03-REQ-1.E2, NS-REQ-34 (issue #34, AC-1, AC-3)
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from afcore.workspace import create_worktree
from afcore.workspace import worktree as worktree_module

from .conftest import add_commit_to_branch, get_branch_tip, list_branches

BRANCH = "fix/42-broken-thing"


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)
    return result.stdout.strip()


@pytest.fixture
def repo_with_remote(tmp_worktree_repo: Path, tmp_path: Path) -> Path:
    """tmp_worktree_repo wired to a bare ``origin`` that already holds BRANCH.

    Simulates the state after an earlier daemon run pushed the fix branch
    (and, under ``pr`` mode, opened a pull request on it).
    """
    remote = tmp_path / "origin.git"
    _git(tmp_path, "init", "--bare", str(remote))
    _git(tmp_worktree_repo, "remote", "add", "origin", str(remote))
    _git(tmp_worktree_repo, "push", "origin", "develop")

    # An earlier attempt's fix branch, pushed and then removed locally.
    _git(tmp_worktree_repo, "checkout", "-b", BRANCH)
    add_commit_to_branch(tmp_worktree_repo, "earlier_fix.txt", "earlier attempt\n")
    _git(tmp_worktree_repo, "push", "origin", BRANCH)
    _git(tmp_worktree_repo, "checkout", "develop")
    _git(tmp_worktree_repo, "branch", "-D", BRANCH)
    return tmp_worktree_repo


def _remote_heads(repo: Path) -> list[str]:
    out = _git(repo, "ls-remote", "--heads", "origin")
    return [line.split("\t")[1].removeprefix("refs/heads/") for line in out.splitlines() if line]


class TestRemoteRefPreserved:
    """AC-1: no ``push --delete`` runs and the remote ref survives."""

    async def test_remote_branch_survives_worktree_creation(self, repo_with_remote: Path) -> None:
        assert BRANCH in _remote_heads(repo_with_remote)
        remote_tip_before = _git(repo_with_remote, "rev-parse", f"origin/{BRANCH}")

        ws = await create_worktree(
            repo_with_remote,
            spec_name="fix-issue-42",
            task_group=0,
            base_branch="develop",
            branch_name=BRANCH,
        )

        assert ws.path.is_dir()
        assert ws.branch == BRANCH
        assert BRANCH in _remote_heads(repo_with_remote), "remote fix branch was deleted"
        assert _git(repo_with_remote, "rev-parse", f"origin/{BRANCH}") == remote_tip_before

    async def test_no_push_delete_is_issued(self, repo_with_remote: Path) -> None:
        """Every git invocation is inspected; none may be a remote delete."""
        real_run_git = worktree_module.run_git
        calls: list[list[str]] = []

        async def spying_run_git(args, *a, **kw):
            calls.append(list(args))
            return await real_run_git(args, *a, **kw)

        with patch.object(worktree_module, "run_git", spying_run_git):
            await create_worktree(
                repo_with_remote,
                spec_name="fix-issue-42",
                task_group=0,
                base_branch="develop",
                branch_name=BRANCH,
            )

        assert calls, "run_git was never invoked"
        for args in calls:
            assert "--delete" not in args, f"remote delete issued: {args}"
            assert not (args[0] == "push" and any(arg.startswith(":") for arg in args)), (
                f"':<branch>' delete pattern issued: {args}"
            )

    async def test_no_push_at_all_when_remote_missing(self, tmp_worktree_repo: Path) -> None:
        """Without a remote, creation neither pushes nor fails."""
        real_run_git = worktree_module.run_git
        calls: list[list[str]] = []

        async def spying_run_git(args, *a, **kw):
            calls.append(list(args))
            return await real_run_git(args, *a, **kw)

        with patch.object(worktree_module, "run_git", spying_run_git):
            ws = await create_worktree(
                tmp_worktree_repo, "fix-issue-7", 0, base_branch="develop", branch_name="fix/7-x"
            )

        assert ws.path.is_dir()
        assert not any(args[0] == "push" for args in calls), f"unexpected push: {calls}"

    async def test_recreation_recreates_from_base_without_touching_remote(self, repo_with_remote: Path) -> None:
        """Re-processing an issue: branch rebuilt from develop, remote untouched."""
        develop_tip = get_branch_tip(repo_with_remote, "develop")

        await create_worktree(repo_with_remote, "fix-issue-42", 0, base_branch="develop", branch_name=BRANCH)

        assert get_branch_tip(repo_with_remote, BRANCH) == develop_tip
        assert BRANCH in _remote_heads(repo_with_remote)


class TestStaleLocalBranchStillRemoved:
    """AC-3: a stale local branch is still force-deleted and recreated."""

    async def test_stale_local_branch_force_deleted_and_worktree_created(self, tmp_worktree_repo: Path) -> None:
        # A stale local branch with its own commit, not checked out in any worktree.
        _git(tmp_worktree_repo, "checkout", "-b", BRANCH)
        stale_tip = add_commit_to_branch(tmp_worktree_repo, "stale.txt", "stale\n")
        _git(tmp_worktree_repo, "checkout", "develop")
        develop_tip = get_branch_tip(tmp_worktree_repo, "develop")
        assert stale_tip != develop_tip
        assert BRANCH in list_branches(tmp_worktree_repo)

        ws = await create_worktree(tmp_worktree_repo, "fix-issue-42", 0, base_branch="develop", branch_name=BRANCH)

        assert ws.path.is_dir()
        assert get_branch_tip(tmp_worktree_repo, BRANCH) == develop_tip, "stale local branch was not replaced"
        assert _git(ws.path, "rev-parse", "--abbrev-ref", "HEAD") == BRANCH

    async def test_stale_local_branch_removed_even_when_remote_holds_it(self, repo_with_remote: Path) -> None:
        """Local stale ref goes; the identically named remote ref stays."""
        _git(repo_with_remote, "checkout", "-b", BRANCH)
        add_commit_to_branch(repo_with_remote, "stale.txt", "stale\n")
        _git(repo_with_remote, "checkout", "develop")
        develop_tip = get_branch_tip(repo_with_remote, "develop")

        ws = await create_worktree(repo_with_remote, "fix-issue-42", 0, base_branch="develop", branch_name=BRANCH)

        assert ws.path.is_dir()
        assert get_branch_tip(repo_with_remote, BRANCH) == develop_tip
        assert BRANCH in _remote_heads(repo_with_remote)
