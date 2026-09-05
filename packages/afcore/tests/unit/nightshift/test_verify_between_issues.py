"""Unit tests for between-issue verification guards.

Covers issue #15: Nightshift must verify freshness of code, issues, and
daemon exclusivity between sessions.

Test Spec: TS-NS-1, TS-NS-2, TS-NS-3, TS-NS-4
Requirements: NS-REQ-1, NS-REQ-2, NS-REQ-3, NS-REQ-4
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from afissues.protocol import IssueResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_engine(max_parallel: int = 1):
    """Return a NightShiftEngine with a mocked platform and minimal config."""
    from afcore.nightshift.engine import NightShiftEngine

    config = MagicMock()
    config.orchestrator.max_cost = None
    config.orchestrator.max_sessions = None
    config.night_shift.similarity_threshold = 0.85
    config.night_shift.max_parallel = max_parallel

    platform = AsyncMock()
    platform.list_issues_by_label = AsyncMock(return_value=[])
    platform.get_issue = AsyncMock()

    engine = NightShiftEngine(config=config, platform=platform)
    return engine, platform


def _make_issue(
    number: int,
    title: str = "Test issue",
    labels: tuple[str, ...] = ("af:fix",),
) -> IssueResult:
    return IssueResult(
        number=number,
        title=title,
        html_url=f"https://github.com/example/repo/issues/{number}",
        body="Issue body",
        labels=labels,
    )


# ---------------------------------------------------------------------------
# TS-NS-1: Nightshift fetches latest code from origin before branching
# Requirement: NS-REQ-1
# ---------------------------------------------------------------------------


class TestFetchBeforeBranching:
    """ensure_integration_branch is called before create_worktree."""

    @pytest.mark.asyncio
    async def test_ensure_integration_branch_called_before_create_worktree(self) -> None:
        """_setup_workspace calls ensure_integration_branch before create_worktree.

        Instruments both calls with mocks and asserts ordering via a call log.
        """
        from afcore.nightshift.fix_pipeline import FixPipeline
        from afcore.nightshift.spec_builder import build_in_memory_spec

        config = MagicMock()
        config.workspace.integration_branch = "develop"

        pipeline = FixPipeline(config=config, platform=AsyncMock())

        issue = _make_issue(42, "Fix the bug")
        spec = build_in_memory_spec(issue, "Issue body text")

        call_log: list[str] = []

        async def fake_ensure(repo_root, branch):
            call_log.append("ensure_integration_branch")

        async def fake_create_worktree(repo_root, **kwargs):
            call_log.append("create_worktree")
            from afcore.workspace import WorkspaceInfo

            return WorkspaceInfo(
                path=Path("/tmp/fake"),
                branch="fix/42-fix-the-bug",
                spec_name="fix-issue-42",
                task_group=0,
            )

        with (
            patch(
                "afcore.workspace.ensure_integration_branch",
                side_effect=fake_ensure,
            ),
            patch(
                "afcore.workspace.create_worktree",
                side_effect=fake_create_worktree,
            ),
        ):
            await pipeline._setup_workspace(spec)

        assert call_log == ["ensure_integration_branch", "create_worktree"]

    @pytest.mark.asyncio
    async def test_ensure_integration_branch_receives_correct_branch(self) -> None:
        """ensure_integration_branch is called with the configured integration branch."""
        from afcore.nightshift.fix_pipeline import FixPipeline
        from afcore.nightshift.spec_builder import build_in_memory_spec

        config = MagicMock()
        config.workspace.integration_branch = "main"

        pipeline = FixPipeline(config=config, platform=AsyncMock())

        issue = _make_issue(7)
        spec = build_in_memory_spec(issue, "body")

        captured_branch = None

        async def fake_ensure(repo_root, branch):
            nonlocal captured_branch
            captured_branch = branch

        async def fake_create_worktree(repo_root, **kwargs):
            from afcore.workspace import WorkspaceInfo

            return WorkspaceInfo(
                path=Path("/tmp/fake"),
                branch="fix/7-test-issue",
                spec_name="fix-issue-7",
                task_group=0,
            )

        with (
            patch(
                "afcore.workspace.ensure_integration_branch",
                side_effect=fake_ensure,
            ),
            patch(
                "afcore.workspace.create_worktree",
                side_effect=fake_create_worktree,
            ),
        ):
            await pipeline._setup_workspace(spec)

        assert captured_branch == "main"


# ---------------------------------------------------------------------------
# TS-NS-2: Nightshift skips processing a closed issue
# Requirement: NS-REQ-2
# ---------------------------------------------------------------------------


class TestSkipClosedIssue:
    """Re-fetched issue with closed state or missing af:fix label is skipped."""

    @pytest.mark.asyncio
    async def test_closed_issue_skipped_no_process_fix(self) -> None:
        """When get_issue returns state='closed', _process_fix is never called.

        The issue number must NOT be added to _processed_issues.
        """
        engine, platform = _make_engine(max_parallel=1)

        issue = _make_issue(10, "Closed issue", labels=("af:fix",))

        # get_issue returns an object with state='closed'
        closed_mock = MagicMock()
        closed_mock.state = "closed"
        closed_mock.labels = ("af:fix",)
        platform.get_issue = AsyncMock(return_value=closed_mock)

        process_fix_called = False

        async def fake_process_fix(iss, **_kwargs) -> None:
            nonlocal process_fix_called
            process_fix_called = True

        with (
            patch("afcore.nightshift.engine.parse_text_references", return_value=[]),
            patch(
                "afcore.nightshift.engine.fetch_github_relationships",
                new=AsyncMock(return_value=[]),
            ),
            patch("afcore.nightshift.engine.build_graph", return_value=[10]),
            patch.object(engine, "_process_fix", side_effect=fake_process_fix),
        ):
            platform.list_issues_by_label = AsyncMock(return_value=[issue])
            await engine._run_issue_check()

        assert not process_fix_called, "_process_fix should not be called for closed issues"
        assert 10 not in engine._processed_issues, "Closed issue should not be added to _processed_issues"

    @pytest.mark.asyncio
    async def test_label_removed_issue_skipped(self) -> None:
        """When re-fetched issue no longer has af:fix label, it is skipped."""
        engine, platform = _make_engine(max_parallel=1)

        issue = _make_issue(20, "Label removed")

        # get_issue returns issue without af:fix label
        no_label_issue = _make_issue(20, "Label removed", labels=())
        platform.get_issue = AsyncMock(return_value=no_label_issue)

        process_fix_called = False

        async def fake_process_fix(iss, **_kwargs) -> None:
            nonlocal process_fix_called
            process_fix_called = True

        with (
            patch("afcore.nightshift.engine.parse_text_references", return_value=[]),
            patch(
                "afcore.nightshift.engine.fetch_github_relationships",
                new=AsyncMock(return_value=[]),
            ),
            patch("afcore.nightshift.engine.build_graph", return_value=[20]),
            patch.object(engine, "_process_fix", side_effect=fake_process_fix),
        ):
            platform.list_issues_by_label = AsyncMock(return_value=[issue])
            await engine._run_issue_check()

        assert not process_fix_called
        assert 20 not in engine._processed_issues

    @pytest.mark.asyncio
    async def test_still_open_issue_processed(self) -> None:
        """When re-fetched issue is still open with af:fix, processing proceeds."""
        engine, platform = _make_engine(max_parallel=1)

        issue = _make_issue(30, "Still open")

        # get_issue returns issue that's still open and labelled
        platform.get_issue = AsyncMock(return_value=issue)

        processed: list[int] = []

        async def fake_process_fix(iss, **_kwargs) -> None:
            processed.append(iss.number)

        with (
            patch("afcore.nightshift.engine.parse_text_references", return_value=[]),
            patch(
                "afcore.nightshift.engine.fetch_github_relationships",
                new=AsyncMock(return_value=[]),
            ),
            patch("afcore.nightshift.engine.build_graph", return_value=[30]),
            patch.object(engine, "_process_fix", side_effect=fake_process_fix),
        ):
            platform.list_issues_by_label = AsyncMock(return_value=[issue])
            await engine._run_issue_check()

        assert 30 in processed
        assert 30 in engine._processed_issues

    @pytest.mark.asyncio
    async def test_get_issue_failure_continues_processing(self) -> None:
        """When get_issue raises, the issue is still processed (fail-open)."""
        engine, platform = _make_engine(max_parallel=1)

        issue = _make_issue(40, "API error issue")
        platform.get_issue = AsyncMock(side_effect=RuntimeError("API timeout"))

        processed: list[int] = []

        async def fake_process_fix(iss, **_kwargs) -> None:
            processed.append(iss.number)

        with (
            patch("afcore.nightshift.engine.parse_text_references", return_value=[]),
            patch(
                "afcore.nightshift.engine.fetch_github_relationships",
                new=AsyncMock(return_value=[]),
            ),
            patch("afcore.nightshift.engine.build_graph", return_value=[40]),
            patch.object(engine, "_process_fix", side_effect=fake_process_fix),
        ):
            platform.list_issues_by_label = AsyncMock(return_value=[issue])
            await engine._run_issue_check()

        assert 40 in processed, "Issue should be processed despite get_issue failure"


# ---------------------------------------------------------------------------
# TS-NS-3: Nightshift refuses to start a second daemon instance
# Requirement: NS-REQ-3
# ---------------------------------------------------------------------------


class TestRefuseSecondDaemon:
    """_run_daemon exits non-zero when PID file records a live process."""

    def test_alive_pid_blocks_startup(self, tmp_path: Path) -> None:
        """Daemon exits with status 1 when another instance is alive."""
        from afcore.nightshift.pid import write_pid_file

        pid_path = tmp_path / ".nightshift" / "daemon.pid"
        pid_path.parent.mkdir(parents=True, exist_ok=True)
        write_pid_file(pid_path)  # Writes current process PID (alive)

        from nightshift.app import _run_daemon

        ctx = MagicMock()
        ctx.obj = {"quiet": False}
        om = MagicMock()
        om.json_mode = False
        om.quiet = False
        config = MagicMock()
        config.security.permission_mode = "acceptEdits"
        config.platform.type = "github"
        config.workspace.integration_branch = "develop"

        with (
            patch("nightshift._startup.check_root_permission_mode"),
            patch("afcore.nightshift.engine.validate_night_shift_prerequisites"),
            patch("afcore.core.models.validate_model_access"),
            patch("pathlib.Path.cwd", return_value=tmp_path),
        ):
            with pytest.raises(SystemExit) as exc_info:
                _run_daemon(ctx, om, config)

            assert exc_info.value.code == 1

    def test_alive_pid_prints_error_to_stderr(self, tmp_path: Path, capsys) -> None:
        """Error message is printed to stderr when blocked."""
        from afcore.nightshift.pid import write_pid_file

        pid_path = tmp_path / ".nightshift" / "daemon.pid"
        pid_path.parent.mkdir(parents=True, exist_ok=True)
        write_pid_file(pid_path)

        from nightshift.app import _run_daemon

        ctx = MagicMock()
        ctx.obj = {"quiet": False}
        om = MagicMock()
        om.json_mode = False
        om.quiet = False
        config = MagicMock()
        config.security.permission_mode = "acceptEdits"
        config.platform.type = "github"

        with (
            patch("nightshift._startup.check_root_permission_mode"),
            patch("afcore.nightshift.engine.validate_night_shift_prerequisites"),
            patch("afcore.core.models.validate_model_access"),
            patch("pathlib.Path.cwd", return_value=tmp_path),
        ):
            with pytest.raises(SystemExit):
                _run_daemon(ctx, om, config)

        captured = capsys.readouterr()
        assert "already running" in captured.err

    def test_daemon_runner_not_called_when_alive(self, tmp_path: Path) -> None:
        """DaemonRunner.run() is never called when PID shows alive."""
        from afcore.nightshift.pid import write_pid_file

        pid_path = tmp_path / ".nightshift" / "daemon.pid"
        pid_path.parent.mkdir(parents=True, exist_ok=True)
        write_pid_file(pid_path)

        from nightshift.app import _run_daemon

        ctx = MagicMock()
        ctx.obj = {"quiet": False}
        om = MagicMock()
        om.json_mode = False
        om.quiet = False
        config = MagicMock()
        config.security.permission_mode = "acceptEdits"
        config.platform.type = "github"

        runner_mock = MagicMock()

        with (
            patch("nightshift._startup.check_root_permission_mode"),
            patch("afcore.nightshift.engine.validate_night_shift_prerequisites"),
            patch("afcore.core.models.validate_model_access"),
            patch("pathlib.Path.cwd", return_value=tmp_path),
            patch("afcore.nightshift.daemon.DaemonRunner", return_value=runner_mock),
        ):
            with pytest.raises(SystemExit):
                _run_daemon(ctx, om, config)

        runner_mock.run.assert_not_called()


# ---------------------------------------------------------------------------
# TS-NS-4: Nightshift starts normally when PID file is stale or absent
# Requirement: NS-REQ-4
# ---------------------------------------------------------------------------


class TestStartNormallyWhenPidStaleOrAbsent:
    """Daemon proceeds past PID check when file is stale or absent."""

    def test_stale_pid_allows_startup(self, tmp_path: Path) -> None:
        """Daemon passes PID check when PID file has a dead process PID."""
        pid_path = tmp_path / ".nightshift" / "daemon.pid"
        pid_path.parent.mkdir(parents=True, exist_ok=True)
        pid_path.write_text("99999999")  # Dead PID

        from nightshift.app import _run_daemon

        ctx = MagicMock()
        ctx.obj = {"quiet": False}
        om = MagicMock()
        om.json_mode = False
        om.quiet = False
        om.verbose = False
        config = MagicMock()
        config.security.permission_mode = "acceptEdits"
        config.platform.type = "github"
        config.workspace.integration_branch = "develop"

        # Track whether we get past the PID check and reach platform creation
        platform_created = False

        def fake_create_platform(cfg, root):
            nonlocal platform_created
            platform_created = True
            raise RuntimeError("stop here - platform creation reached")

        with (
            patch("nightshift._startup.check_root_permission_mode"),
            patch("afcore.nightshift.engine.validate_night_shift_prerequisites"),
            patch("afcore.core.models.validate_model_access"),
            patch("pathlib.Path.cwd", return_value=tmp_path),
            patch(
                "afcore.nightshift.platform_factory.create_platform",
                side_effect=fake_create_platform,
            ),
        ):
            # Should proceed past PID check and reach create_platform
            with pytest.raises(RuntimeError, match="stop here"):
                _run_daemon(ctx, om, config)

        assert platform_created, "Daemon should proceed past stale PID check"

    def test_absent_pid_allows_startup(self, tmp_path: Path) -> None:
        """Daemon passes PID check when no PID file exists."""
        # Don't create any PID file
        nightshift_dir = tmp_path / ".nightshift"
        nightshift_dir.mkdir(parents=True, exist_ok=True)

        from nightshift.app import _run_daemon

        ctx = MagicMock()
        ctx.obj = {"quiet": False}
        om = MagicMock()
        om.json_mode = False
        om.quiet = False
        om.verbose = False
        config = MagicMock()
        config.security.permission_mode = "acceptEdits"
        config.platform.type = "github"
        config.workspace.integration_branch = "develop"

        platform_created = False

        def fake_create_platform(cfg, root):
            nonlocal platform_created
            platform_created = True
            raise RuntimeError("stop here - platform creation reached")

        with (
            patch("nightshift._startup.check_root_permission_mode"),
            patch("afcore.nightshift.engine.validate_night_shift_prerequisites"),
            patch("afcore.core.models.validate_model_access"),
            patch("pathlib.Path.cwd", return_value=tmp_path),
            patch(
                "afcore.nightshift.platform_factory.create_platform",
                side_effect=fake_create_platform,
            ),
        ):
            with pytest.raises(RuntimeError, match="stop here"):
                _run_daemon(ctx, om, config)

        assert platform_created, "Daemon should proceed past absent PID check"
