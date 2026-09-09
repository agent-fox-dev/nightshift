"""Merge-conflict resolution agent."""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


MERGE_AGENT_SYSTEM_PROMPT = """\
You are a merge conflict resolution agent. Your ONLY task is to resolve \
git merge conflicts in the working tree.

Rules:
- Resolve merge conflicts only. Do NOT refactor code, fix test failures, \
or make feature changes.
- Open each conflicted file, understand both sides of the conflict, and \
produce a correct merged result.
- After resolving all conflicts, stage the resolved files with `git add` \
and commit with a merge commit message.
- Do not modify any files that are not part of the merge conflict.
- Do not run tests or make any changes beyond what is needed to resolve \
the conflicts.
"""


async def run_merge_agent(
    worktree_path: Path,
    conflict_output: str,
    model_id: str,
) -> bool:
    """Spawn a merge agent to resolve git conflicts.

    Args:
        worktree_path: Path to the git worktree with unresolved conflicts.
        conflict_output: Git conflict/diff output to include in the prompt.
        model_id: Model ID to use (resolved from ADVANCED tier).

    Returns:
        True if conflicts were resolved and committed, False otherwise.

    Requirements: 45-REQ-4.1, 45-REQ-4.2, 45-REQ-4.3, 45-REQ-4.4,
                  45-REQ-4.5, 45-REQ-4.E1, 45-REQ-4.E2
    """
    task_prompt = (
        "Resolve the following git merge conflicts in the working tree.\n\n"
        "Git conflict output:\n"
        f"```\n{conflict_output}\n```\n\n"
        "Resolve all conflicts, stage the files, and commit."
    )

    try:
        session_ok = await _run_agent_session(
            worktree_path=worktree_path,
            system_prompt=MERGE_AGENT_SYSTEM_PROMPT,
            task_prompt=task_prompt,
            model_id=model_id,
        )
    except Exception:
        logger.exception(
            "Merge agent session failed with exception (worktree=%s)",
            worktree_path,
        )
        return False

    if not session_ok:
        logger.error(
            "Merge agent session returned failure (worktree=%s)",
            worktree_path,
        )
        return False

    # Verify conflicts are actually resolved
    resolved = await _check_conflicts_resolved(worktree_path)
    if not resolved:
        logger.error(
            "Merge agent did not resolve all conflicts (worktree=%s)",
            worktree_path,
        )
        return False

    logger.info("Merge agent resolved all conflicts (worktree=%s)", worktree_path)
    return True


async def _run_agent_session(
    worktree_path: Path,
    system_prompt: str,
    task_prompt: str,
    model_id: str,
) -> bool:
    """Run a coding agent session for conflict resolution.

    This is the internal integration point with the session runner.
    Separated for testability -- tests mock this function to avoid
    spawning real agent sessions.

    Returns:
        True if the session completed successfully, False otherwise.
    """
    from afcore.core.config import load_config
    from afcore.session.session import run_session
    from afcore.workspace import WorkspaceInfo

    config = load_config()

    workspace = WorkspaceInfo(
        path=worktree_path,
        branch="merge-resolution",
        spec_name="merge-agent",
        task_group=0,
    )

    try:
        # Note: effort, compaction, and cache_policy are intentionally omitted.
        # The merge agent is a narrow, single-purpose tool for conflict
        # resolution — it does not use resolve_session_params or the
        # archetype config cascade.  The caller supplies model_id directly
        # (resolved from ADVANCED tier) and SDK defaults suffice for the
        # remaining parameters.  See issue #20.
        outcome = await run_session(
            workspace=workspace,
            node_id="merge-agent",
            system_prompt=system_prompt,
            task_prompt=task_prompt,
            config=config,
            model_id=model_id,
        )
        return outcome.status == "completed"
    except Exception:
        logger.exception("Agent session raised an exception")
        return False


async def _check_conflicts_resolved(worktree_path: Path) -> bool:
    """Check if all merge conflicts have been resolved **and committed**.

    Performs three checks:

    1. ``git ls-files -u`` — must be empty (no unmerged index entries).
    2. ``git diff --check`` — no leftover conflict markers in the working
       tree.
    3. ``git diff --cached --quiet`` — nothing staged but uncommitted.
       A staged-but-uncommitted resolution would be wiped by the
       ``finally`` cleanup in ``_harvest_under_lock``.

    All three must pass for the function to return ``True``.

    Returns:
        True if conflicts are resolved and committed, False otherwise.
    """
    from afcore.workspace import run_git

    # (1) No unmerged index entries
    rc, stdout, _stderr = await run_git(
        ["ls-files", "-u"],
        cwd=worktree_path,
        check=False,
    )
    if rc != 0 or stdout.strip():
        logger.debug(
            "Unmerged index entries remain (worktree=%s): %s",
            worktree_path,
            stdout.strip()[:200],
        )
        return False

    # (2) No conflict markers in working tree
    rc, _stdout, _stderr = await run_git(
        ["diff", "--check"],
        cwd=worktree_path,
        check=False,
    )
    if rc != 0:
        logger.debug(
            "Conflict markers remain in working tree (worktree=%s)",
            worktree_path,
        )
        return False

    # (3) Nothing staged but uncommitted — the resolution must be
    #     committed, not just staged, or the finally-block cleanup
    #     will discard it.
    rc, _stdout, _stderr = await run_git(
        ["diff", "--cached", "--quiet"],
        cwd=worktree_path,
        check=False,
    )
    if rc != 0:
        logger.debug(
            "Staged but uncommitted changes remain (worktree=%s); merge agent may have staged without committing",
            worktree_path,
        )
        return False

    return True
