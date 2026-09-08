"""Unit tests for repository-root threading (issue #43).

The repository root is resolved once at startup and passed explicitly to
the engine, the fix pipeline and the carry-patch monitor instead of being
re-derived from ``Path.cwd()`` at each git call site.

Requirements: NS-REQ-6 (issue #43)
Acceptance criteria: AC-1, AC-2, AC-3, AC-4
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from afcore.nightshift.carry_patch_monitor import CarryPatchMonitor
from afcore.nightshift.engine import NightShiftEngine
from afcore.nightshift.fix_pipeline import FixPipeline
from afcore.nightshift.spec_builder import InMemorySpec
from afcore.workspace import WorkspaceInfo
from afcore.workspace.repo_root import resolve_repo_root


def _init_repo(path: Path) -> Path:
    """Initialise a git repository at *path* and return it."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    return path


def _make_spec(issue_number: int = 42) -> InMemorySpec:
    return InMemorySpec(
        issue_number=issue_number,
        title="Broken thing",
        task_prompt="Fix the broken thing",
        system_context="context",
        branch_name=f"fix/{issue_number}-broken-thing",
    )


def _make_config() -> MagicMock:
    config = MagicMock()
    config.workspace = MagicMock()
    config.workspace.integration_branch = "develop"
    return config


# ---------------------------------------------------------------------------
# resolve_repo_root
# ---------------------------------------------------------------------------


class TestResolveRepoRoot:
    """The resolver hardens the working directory to the work-tree root."""

    def test_subdirectory_resolves_to_repo_root(self, tmp_path: Path) -> None:
        """A subdirectory of the repository resolves to the repository root.

        Acceptance criteria: AC-1
        """
        repo = _init_repo(tmp_path / "repo")
        sub = repo / "packages" / "afcore"
        sub.mkdir(parents=True)

        assert resolve_repo_root(sub) == repo.resolve()

    def test_repo_root_resolves_to_itself(self, tmp_path: Path) -> None:
        """The repository root resolves to itself."""
        repo = _init_repo(tmp_path / "repo")

        assert resolve_repo_root(repo) == repo.resolve()

    def test_outside_a_repository_falls_back_to_the_directory(self, tmp_path: Path) -> None:
        """A directory outside any work tree resolves to itself."""
        plain = tmp_path / "not-a-repo"
        plain.mkdir()

        with patch("afcore.workspace.repo_root.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess(args=[], returncode=128, stdout=b"", stderr=b"")
            assert resolve_repo_root(plain) == plain.resolve()

    def test_missing_git_binary_falls_back_to_the_directory(self, tmp_path: Path) -> None:
        """A missing git binary does not abort resolution."""
        with patch("afcore.workspace.repo_root.subprocess.run", side_effect=FileNotFoundError):
            assert resolve_repo_root(tmp_path) == tmp_path.resolve()

    def test_defaults_to_the_working_directory(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """With no argument the resolver starts from the working directory."""
        repo = _init_repo(tmp_path / "repo")
        sub = repo / "nested"
        sub.mkdir()
        monkeypatch.chdir(sub)

        assert resolve_repo_root() == repo.resolve()


# ---------------------------------------------------------------------------
# FixPipeline
# ---------------------------------------------------------------------------


class TestFixPipelineRepoRoot:
    """The pipeline operates on the root it was given, not on ``cwd``."""

    def test_constructed_against_a_temp_repo_without_chdir(self, tmp_path: Path) -> None:
        """A pipeline can be built against a temp repo without changing ``cwd``.

        Acceptance criteria: AC-4
        """
        repo = _init_repo(tmp_path / "repo")
        before = Path.cwd()

        pipeline = FixPipeline(config=_make_config(), platform=MagicMock(), repo_root=repo)

        assert pipeline.repo_root == repo
        assert Path.cwd() == before

    def test_defaults_to_the_resolved_working_directory(self, tmp_git_repo: Path) -> None:
        """Without an explicit root the pipeline resolves the work tree once."""
        pipeline = FixPipeline(config=_make_config(), platform=MagicMock())

        assert pipeline.repo_root == tmp_git_repo.resolve()

    async def test_setup_workspace_uses_the_repo_root(self, tmp_path: Path) -> None:
        """The worktree is created under the repository root, not ``cwd``.

        Acceptance criteria: AC-1
        """
        repo = _init_repo(tmp_path / "repo")
        pipeline = FixPipeline(config=_make_config(), platform=MagicMock(), repo_root=repo)

        with (
            patch("afcore.workspace.ensure_integration_branch", new=AsyncMock()) as ensure,
            patch("afcore.workspace.create_worktree", new=AsyncMock()) as create,
        ):
            create.return_value = WorkspaceInfo(
                path=repo / ".nightshift" / "worktrees" / "fix-issue-42",
                branch="fix/42-broken-thing",
                spec_name="fix-issue-42",
                task_group=0,
            )
            await pipeline._setup_workspace(_make_spec())

        assert ensure.await_args.args[0] == repo
        assert create.await_args.args[0] == repo

    async def test_cleanup_workspace_uses_the_repo_root(self, tmp_path: Path) -> None:
        """The worktree is destroyed against the repository root."""
        repo = _init_repo(tmp_path / "repo")
        pipeline = FixPipeline(config=_make_config(), platform=MagicMock(), repo_root=repo)
        workspace = WorkspaceInfo(
            path=repo / ".nightshift" / "worktrees" / "fix-issue-42",
            branch="fix/42-broken-thing",
            spec_name="fix-issue-42",
            task_group=0,
        )

        with patch("afcore.workspace.destroy_worktree", new=AsyncMock()) as destroy:
            await pipeline._cleanup_workspace(workspace)

        assert destroy.await_args.args[0] == repo

    async def test_harvest_uses_the_repo_root(self, tmp_path: Path) -> None:
        """Harvest runs against the repository root."""
        repo = _init_repo(tmp_path / "repo")
        pipeline = FixPipeline(config=_make_config(), platform=MagicMock(), repo_root=repo)
        workspace = WorkspaceInfo(
            path=repo / ".nightshift" / "worktrees" / "fix-issue-42",
            branch="fix/42-broken-thing",
            spec_name="fix-issue-42",
            task_group=0,
        )

        with patch("afcore.workspace.harvest.harvest", new=AsyncMock(return_value=[])) as harvest:
            changed = await pipeline._harvest_and_push(_make_spec(), workspace)

        assert changed == []
        assert harvest.await_args.args[0] == repo


# ---------------------------------------------------------------------------
# NightShiftEngine
# ---------------------------------------------------------------------------


class TestEngineRepoRoot:
    """The engine stores the root and threads it into the pipeline."""

    def test_engine_exposes_the_given_root(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path / "repo")
        engine = NightShiftEngine(config=_make_config(), platform=MagicMock(), repo_root=repo)

        assert engine.repo_root == repo

    def test_engine_defaults_to_the_resolved_working_directory(self, tmp_git_repo: Path) -> None:
        engine = NightShiftEngine(config=_make_config(), platform=MagicMock())

        assert engine.repo_root == tmp_git_repo.resolve()

    async def test_pr_pipeline_receives_the_engine_root(self, tmp_path: Path) -> None:
        """``_check_open_prs`` builds its pipeline with the engine's root."""
        repo = _init_repo(tmp_path / "repo")
        platform = MagicMock()
        platform.list_issues_by_label = AsyncMock(return_value=[MagicMock()])
        engine = NightShiftEngine(config=_make_config(), platform=platform, repo_root=repo)

        seen: list[FixPipeline] = []

        async def _capture(_issue, *, config, platform, pipeline):  # noqa: ARG001
            seen.append(pipeline)

        with patch("afcore.nightshift.engine.process_pr_issue", new=_capture):
            await engine._check_open_prs()

        assert seen and seen[0].repo_root == repo

    async def test_coder_session_requires_repo_root_in_context(self, tmp_path: Path) -> None:
        """A missing ``repo_root`` raises rather than defaulting to ``cwd``.

        Acceptance criteria: AC-3
        """
        repo = _init_repo(tmp_path / "repo")
        engine = NightShiftEngine(config=_make_config(), platform=MagicMock(), repo_root=repo)

        with pytest.raises(ValueError, match="repo_root"):
            await engine._run_coder_session(archetype="coder", mode="carry-patch", context={"branch": "patch/1"})


# ---------------------------------------------------------------------------
# CarryPatchMonitor
# ---------------------------------------------------------------------------


class TestCarryPatchMonitorRepoRoot:
    """The monitor checks out against the root it was given."""

    def test_monitor_accepts_an_explicit_root(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path / "repo")
        monitor = CarryPatchMonitor(
            hub_client=MagicMock(),
            workspace_slug="ws-1",
            config=_make_config(),
            engine=MagicMock(spec=NightShiftEngine),
            repo_root=repo,
        )

        assert monitor._repo_root == repo

    async def test_conflict_resolution_uses_the_given_root(self, tmp_path: Path) -> None:
        """Fetch uses the repo root; the coder context uses the worktree path.

        After issue #32, conflict resolution creates an isolated worktree
        instead of checking out in the primary tree. The fetch still
        operates on the repo root, but the coder session receives the
        worktree path as its working directory.
        """
        repo = _init_repo(tmp_path / "repo")
        engine = MagicMock(spec=NightShiftEngine)
        engine._run_coder_session = AsyncMock()
        config = _make_config()
        config.carry_patch = MagicMock()
        config.carry_patch.hub_git_remote = "hub"
        monitor = CarryPatchMonitor(
            hub_client=MagicMock(),
            workspace_slug="ws-1",
            config=config,
            engine=engine,
            repo_root=repo,
        )
        patch_detail = MagicMock()
        patch_detail.branch_name = "patch/1"
        patch_detail.id = "p1"
        result = MagicMock()

        wt_path = repo / ".nightshift" / "worktrees" / "carry-patch" / "0"
        mock_workspace = WorkspaceInfo(
            path=wt_path,
            branch="patch/1",
            spec_name="carry-patch",
            task_group=0,
        )

        with (
            patch.object(monitor, "_build_conflict_context", new=AsyncMock(return_value={})) as build_ctx,
            patch.object(monitor, "_submit_and_poll_rebuild", new=AsyncMock()),
            patch("afcore.nightshift.carry_patch_monitor._workspace_git") as git,
            patch(
                "afcore.nightshift.carry_patch_monitor.create_worktree",
                new=AsyncMock(return_value=mock_workspace),
            ) as mock_create,
            patch("afcore.nightshift.carry_patch_monitor.destroy_worktree", new=AsyncMock()) as mock_destroy,
            patch(
                "afcore.nightshift.carry_patch_monitor.MergeLock",
                return_value=MagicMock(
                    __aenter__=AsyncMock(return_value=None),
                    __aexit__=AsyncMock(return_value=None),
                ),
            ),
        ):
            git.fetch_remote = AsyncMock()
            git.push_to_remote = AsyncMock()
            await monitor._resolve_conflict(patch_detail, result)

        # Fetch uses the repo root
        assert git.fetch_remote.await_args.args[0] == repo
        # create_worktree uses the repo root
        assert mock_create.await_args.args[0] == repo
        # destroy_worktree uses the repo root
        assert mock_destroy.await_args.args[0] == repo
        # The coder context uses the worktree path, NOT repo root (issue #32)
        assert engine._run_coder_session.await_args.kwargs["context"]["repo_root"] == str(wt_path)
        # build_conflict_context receives the worktree path
        assert build_ctx.await_args.args[2] == wt_path


# ---------------------------------------------------------------------------
# Structural guard
# ---------------------------------------------------------------------------


class TestNoWorkingDirectoryDerivation:
    """No git path is derived from the working directory any more."""

    @pytest.mark.parametrize(
        "module",
        [
            "afcore/nightshift/fix_pipeline.py",
            "afcore/nightshift/engine.py",
            "afcore/nightshift/carry_patch_monitor.py",
        ],
    )
    def test_module_has_no_cwd_calls(self, module: str) -> None:
        """``Path.cwd()`` appears nowhere in the pipeline or monitor modules.

        Acceptance criteria: AC-2
        """
        source = (Path(__file__).parents[3] / module).read_text(encoding="utf-8")
        tree = ast.parse(source)
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "cwd"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "Path"
        ]
        assert not calls, f"{module} still derives a path from Path.cwd()"
