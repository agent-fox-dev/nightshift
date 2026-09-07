"""Tests for cross-run attempt ceiling (issue #37).

Verifies that:
- count_prior_runs returns the correct count and fails open on error
- Issues at the attempt ceiling are not dispatched
- af:failed issues are excluded from the poll
- af:failed label is assigned on exhaustion
- CarryPatchMonitor retries survive a daemon restart

Test Spec: TS-NS-1 through TS-NS-5
Requirements: NS-REQ-1, NS-REQ-2, NS-REQ-3, NS-REQ-4, NS-REQ-5
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Generator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import duckdb
import pytest
from afcore.nightshift.fix_pipeline import LABEL_FAILED
from afcore.nightshift.prior_attempts import count_prior_runs
from afcore.workspace import WorkspaceInfo
from afissues.protocol import IssueResult

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_conn() -> Generator[duckdb.DuckDBPyConnection, None, None]:
    """In-memory DuckDB with session_outcomes table."""
    from afcore.knowledge.migrations import apply_pending_migrations

    from tests.unit.knowledge.conftest import SCHEMA_DDL

    conn = duckdb.connect(":memory:")
    conn.execute(SCHEMA_DDL)
    apply_pending_migrations(conn)
    yield conn
    conn.close()


def _insert_session(
    conn: duckdb.DuckDBPyConnection,
    *,
    spec_name: str = "fix-issue-42",
    run_id: str = "run_A",
    archetype: str = "coder",
    status: str = "failed",
    error_message: str | None = None,
    model: str | None = "claude-sonnet-4-5-20250514",
    created_at: str = "2026-05-28 10:00:00",
) -> None:
    """Insert a single session_outcomes row for testing."""
    session_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO session_outcomes
            (id, spec_name, task_group, node_id, touched_path,
             status, input_tokens, output_tokens, duration_ms,
             created_at, run_id, attempt, cost, model, archetype,
             commit_sha, error_message, is_transport_error)
        VALUES (?, ?, '0', ?, '', ?, 100, 50, 1000, ?, ?, 1, 0.01, ?, ?, '', ?, FALSE)
        """,
        [
            session_id,
            spec_name,
            f"{spec_name}:0:{archetype}",
            status,
            created_at,
            run_id,
            model,
            archetype,
            error_message,
        ],
    )


def _make_issue(number: int, title: str = "Test issue", labels: list[str] | None = None) -> IssueResult:
    return IssueResult(
        number=number,
        title=title,
        html_url=f"https://github.com/example/repo/issues/{number}",
        body="Issue body",
        labels=labels or ["af:fix"],
    )


def _mock_workspace() -> WorkspaceInfo:
    return WorkspaceInfo(
        path=Path("/tmp/mock-worktree"),
        branch="fix/test-branch",
        spec_name="fix-issue-42",
        task_group=0,
    )


# ---------------------------------------------------------------------------
# TS-NS-1: Issue at attempt ceiling is skipped during dispatch
# Requirement: NS-REQ-1
# ---------------------------------------------------------------------------


class TestAttemptCeilingDispatch:
    """Verify issues at the attempt ceiling are not dispatched."""

    def test_count_prior_runs_returns_correct_count(
        self,
        db_conn: duckdb.DuckDBPyConnection,
    ) -> None:
        """count_prior_runs returns the number of distinct prior run_ids."""
        _insert_session(db_conn, run_id="run_A", created_at="2026-05-25 10:00:00")
        _insert_session(db_conn, run_id="run_B", created_at="2026-05-26 10:00:00")
        _insert_session(db_conn, run_id="run_C", created_at="2026-05-27 10:00:00")

        count = count_prior_runs(db_conn, "fix-issue-42")
        assert count == 3

    def test_count_prior_runs_excludes_current_run(
        self,
        db_conn: duckdb.DuckDBPyConnection,
    ) -> None:
        """count_prior_runs excludes the current run_id when provided."""
        _insert_session(db_conn, run_id="run_A", created_at="2026-05-25 10:00:00")
        _insert_session(db_conn, run_id="run_B", created_at="2026-05-26 10:00:00")
        _insert_session(db_conn, run_id="current_run", created_at="2026-05-27 10:00:00")

        count = count_prior_runs(db_conn, "fix-issue-42", current_run_id="current_run")
        assert count == 2

    def test_count_prior_runs_empty_table(
        self,
        db_conn: duckdb.DuckDBPyConnection,
    ) -> None:
        """count_prior_runs returns 0 for an empty table."""
        count = count_prior_runs(db_conn, "fix-issue-42")
        assert count == 0

    def test_count_prior_runs_only_counts_coder_sessions(
        self,
        db_conn: duckdb.DuckDBPyConnection,
    ) -> None:
        """count_prior_runs only counts coder archetype sessions."""
        _insert_session(db_conn, run_id="run_A", archetype="coder")
        _insert_session(db_conn, run_id="run_B", archetype="reviewer")
        _insert_session(db_conn, run_id="run_C", archetype="maintainer")

        count = count_prior_runs(db_conn, "fix-issue-42")
        assert count == 1  # Only coder session counted

    @pytest.mark.asyncio
    async def test_issue_at_ceiling_not_dispatched(self) -> None:
        """TS-NS-1: Issue at max_attempts_per_issue is skipped."""
        from afcore.nightshift.engine import NightShiftEngine

        config = MagicMock()
        config.orchestrator.max_cost = None
        config.orchestrator.max_sessions = None
        config.night_shift.similarity_threshold = 0.85
        config.night_shift.max_attempts_per_issue = 3

        platform = AsyncMock()
        platform.list_issues_by_label = AsyncMock(return_value=[])

        conn = MagicMock()

        engine = NightShiftEngine(config=config, platform=platform, conn=conn)

        # Mock count_prior_runs to return 3 (at ceiling)
        with patch(
            "afcore.nightshift.prior_attempts.count_prior_runs",
            return_value=3,
        ):
            result = engine._exceeds_attempt_ceiling(42)

        assert result is True

    @pytest.mark.asyncio
    async def test_issue_below_ceiling_dispatched(self) -> None:
        """Issue below max_attempts_per_issue is dispatched."""
        from afcore.nightshift.engine import NightShiftEngine

        config = MagicMock()
        config.orchestrator.max_cost = None
        config.orchestrator.max_sessions = None
        config.night_shift.similarity_threshold = 0.85
        config.night_shift.max_attempts_per_issue = 3

        platform = AsyncMock()
        conn = MagicMock()

        engine = NightShiftEngine(config=config, platform=platform, conn=conn)

        with patch(
            "afcore.nightshift.prior_attempts.count_prior_runs",
            return_value=2,
        ):
            result = engine._exceeds_attempt_ceiling(42)

        assert result is False

    @pytest.mark.asyncio
    async def test_ceiling_skips_in_fill_pool(self) -> None:
        """TS-NS-1: Issue at ceiling is skipped in _fill_pool via _dispatch_parallel."""
        from afcore.nightshift.engine import NightShiftEngine

        config = MagicMock()
        config.orchestrator.max_cost = None
        config.orchestrator.max_sessions = None
        config.night_shift.similarity_threshold = 0.85
        config.night_shift.max_attempts_per_issue = 3
        config.night_shift.max_parallel = 1

        platform = AsyncMock()
        platform.list_issues_by_label = AsyncMock(return_value=[_make_issue(42)])
        platform.get_issue = AsyncMock(return_value=_make_issue(42))

        conn = MagicMock()
        engine = NightShiftEngine(config=config, platform=platform, conn=conn)

        process_fix_calls: list[int] = []

        async def fake_process_fix(iss, **_kwargs) -> None:
            process_fix_calls.append(iss.number)

        with (
            patch("afcore.nightshift.engine.parse_text_references", return_value=[]),
            patch(
                "afcore.nightshift.engine.fetch_github_relationships",
                new=AsyncMock(return_value=[]),
            ),
            patch("afcore.nightshift.engine.build_graph", return_value=[42]),
            patch.object(engine, "_process_fix", side_effect=fake_process_fix),
            patch.object(engine, "_exceeds_attempt_ceiling", return_value=True),
        ):
            await engine._run_issue_check()

        # _process_fix should NOT have been called
        assert process_fix_calls == []


# ---------------------------------------------------------------------------
# TS-NS-2: Exhaustion assigns af:failed and posts attempt summary
# Requirement: NS-REQ-2
# ---------------------------------------------------------------------------


class TestExhaustionAssignsFailedLabel:
    """Verify af:failed is assigned when the pipeline exhausts retries."""

    @pytest.mark.asyncio
    async def test_mark_issue_failed_assigns_label(self) -> None:
        """TS-NS-2: platform.assign_label is called with LABEL_FAILED."""
        from afcore.nightshift.fix_pipeline import FixPipeline

        config = MagicMock()
        config.orchestrator.max_retries = 0
        config.archetypes.overrides.get.return_value = None
        mock_platform = AsyncMock()
        mock_platform.assign_label = AsyncMock()
        mock_platform.add_issue_comment = AsyncMock()

        pipeline = FixPipeline(config=config, platform=mock_platform)
        pipeline._run_id = "test-run-id"

        issue = IssueResult(
            number=42,
            title="Test issue",
            html_url="https://github.com/test/repo/issues/42",
        )
        from afcore.nightshift.spec_builder import build_in_memory_spec

        spec = build_in_memory_spec(issue, "Test body")

        await pipeline._mark_issue_failed(issue, spec)

        # Assert assign_label was called with LABEL_FAILED
        mock_platform.assign_label.assert_called_once_with(42, LABEL_FAILED)

    @pytest.mark.asyncio
    async def test_mark_issue_failed_posts_comment(self) -> None:
        """TS-NS-2: a comment is posted mentioning af:failed."""
        from afcore.nightshift.fix_pipeline import FixPipeline

        config = MagicMock()
        config.orchestrator.max_retries = 0
        config.archetypes.overrides.get.return_value = None
        mock_platform = AsyncMock()
        mock_platform.assign_label = AsyncMock()
        mock_platform.add_issue_comment = AsyncMock()

        pipeline = FixPipeline(config=config, platform=mock_platform)
        pipeline._run_id = "test-run-id"

        issue = IssueResult(
            number=42,
            title="Test issue",
            html_url="https://github.com/test/repo/issues/42",
        )
        from afcore.nightshift.spec_builder import build_in_memory_spec

        spec = build_in_memory_spec(issue, "Test body")

        await pipeline._mark_issue_failed(issue, spec)

        # Assert add_issue_comment was called
        assert mock_platform.add_issue_comment.called
        comment_text = mock_platform.add_issue_comment.call_args[0][1]
        assert "af:failed" in comment_text
        assert "exhausted" in comment_text.lower()

    @pytest.mark.asyncio
    async def test_mark_issue_failed_with_prior_attempts(self) -> None:
        """TS-NS-2: attempt summary includes prior run_ids."""
        from afcore.nightshift.fix_pipeline import FixPipeline
        from afcore.nightshift.prior_attempts import PriorAttempt

        config = MagicMock()
        config.orchestrator.max_retries = 0
        config.archetypes.overrides.get.return_value = None
        mock_platform = AsyncMock()
        mock_platform.assign_label = AsyncMock()
        mock_platform.add_issue_comment = AsyncMock()

        conn = MagicMock()
        pipeline = FixPipeline(config=config, platform=mock_platform, conn=conn)
        pipeline._run_id = "test-run-id"

        issue = IssueResult(
            number=42,
            title="Test issue",
            html_url="https://github.com/test/repo/issues/42",
        )
        from afcore.nightshift.spec_builder import build_in_memory_spec

        spec = build_in_memory_spec(issue, "Test body")

        prior = [
            PriorAttempt(
                run_id="run_A",
                created_at="2026-05-25 10:00:00",
                status="failed",
                error_message="Test error",
                model="claude-sonnet-4-5",
            ),
            PriorAttempt(
                run_id="run_B",
                created_at="2026-05-26 10:00:00",
                status="failed",
                error_message=None,
                model=None,
            ),
        ]

        with patch(
            "afcore.nightshift.fix_pipeline.query_prior_attempts",
            return_value=prior,
        ):
            await pipeline._mark_issue_failed(issue, spec)

        comment_text = mock_platform.add_issue_comment.call_args[0][1]
        assert "run_A" in comment_text
        assert "run_B" in comment_text
        assert "Fix Attempt Summary" in comment_text

    @pytest.mark.asyncio
    async def test_process_issue_calls_mark_failed_on_exhaustion(self) -> None:
        """TS-NS-2: process_issue assigns af:failed when coder loop is exhausted."""
        from afcore.nightshift.coder_reviewer import CoderReviewerResult
        from afcore.nightshift.fix_pipeline import FixPipeline

        config = MagicMock()
        config.orchestrator.max_retries = 0
        config.archetypes.overrides.get.return_value = None
        config.workspace.integration_branch = "main"
        mock_platform = AsyncMock()
        mock_platform.assign_label = AsyncMock()
        mock_platform.add_issue_comment = AsyncMock()

        pipeline = FixPipeline(config=config, platform=mock_platform)
        pipeline._setup_workspace = AsyncMock(return_value=_mock_workspace())
        pipeline._cleanup_workspace = AsyncMock()
        pipeline._run_triage = AsyncMock(return_value=MagicMock(criteria=[], summary="test", affected_files=[]))
        pipeline._coder_review_loop = AsyncMock(return_value=CoderReviewerResult(success=False))
        pipeline._mark_issue_failed = AsyncMock()

        issue = IssueResult(
            number=42,
            title="Test issue",
            html_url="https://github.com/test/repo/issues/42",
        )

        await pipeline.process_issue(issue, issue_body="Something is broken.")

        pipeline._mark_issue_failed.assert_called_once()
        call_args = pipeline._mark_issue_failed.call_args
        assert call_args[0][0].number == 42


# ---------------------------------------------------------------------------
# TS-NS-3: Poll query excludes af:failed issues
# Requirement: NS-REQ-3
# ---------------------------------------------------------------------------


class TestPollExcludesFailedIssues:
    """Verify af:failed issues are filtered out before dispatch."""

    @pytest.mark.asyncio
    async def test_af_failed_issue_excluded_from_run_issue_check(self) -> None:
        """TS-NS-3: Issue with af:failed label is not processed."""
        from afcore.nightshift.engine import NightShiftEngine

        config = MagicMock()
        config.orchestrator.max_cost = None
        config.orchestrator.max_sessions = None
        config.night_shift.similarity_threshold = 0.85
        config.night_shift.max_attempts_per_issue = 3

        platform = AsyncMock()

        # Return an issue that has both af:fix and af:failed
        failed_issue = _make_issue(42, labels=["af:fix", "af:failed"])
        platform.list_issues_by_label = AsyncMock(return_value=[failed_issue])

        engine = NightShiftEngine(config=config, platform=platform)

        process_fix_calls: list[int] = []

        async def fake_process_fix(iss, **_kwargs) -> None:
            process_fix_calls.append(iss.number)

        with (
            patch("afcore.nightshift.engine.parse_text_references", return_value=[]),
            patch(
                "afcore.nightshift.engine.fetch_github_relationships",
                new=AsyncMock(return_value=[]),
            ),
            patch("afcore.nightshift.engine.build_graph", return_value=[42]),
            patch.object(engine, "_process_fix", side_effect=fake_process_fix),
        ):
            await engine._run_issue_check()

        assert process_fix_calls == []

    @pytest.mark.asyncio
    async def test_non_failed_issue_still_processed(self) -> None:
        """Issue without af:failed is still processed."""
        from afcore.nightshift.engine import NightShiftEngine

        config = MagicMock()
        config.orchestrator.max_cost = None
        config.orchestrator.max_sessions = None
        config.night_shift.similarity_threshold = 0.85
        config.night_shift.max_attempts_per_issue = 100

        platform = AsyncMock()

        good_issue = _make_issue(42, labels=["af:fix"])
        platform.list_issues_by_label = AsyncMock(return_value=[good_issue])
        platform.get_issue = AsyncMock(return_value=good_issue)

        engine = NightShiftEngine(config=config, platform=platform)

        process_fix_calls: list[int] = []

        async def fake_process_fix(iss, **_kwargs) -> None:
            process_fix_calls.append(iss.number)

        with (
            patch("afcore.nightshift.engine.parse_text_references", return_value=[]),
            patch(
                "afcore.nightshift.engine.fetch_github_relationships",
                new=AsyncMock(return_value=[]),
            ),
            patch("afcore.nightshift.engine.build_graph", return_value=[42]),
            patch.object(engine, "_process_fix", side_effect=fake_process_fix),
            patch.object(engine, "_exceeds_attempt_ceiling", return_value=False),
        ):
            await engine._run_issue_check()

        assert 42 in process_fix_calls


# ---------------------------------------------------------------------------
# TS-NS-4: Fail-open when DuckDB is unavailable
# Requirement: NS-REQ-4
# ---------------------------------------------------------------------------


class TestFailOpenOnDuckDBError:
    """Verify the ceiling check fails open when DuckDB is unavailable."""

    def test_count_prior_runs_fails_open_on_exception(self, caplog: pytest.LogCaptureFixture) -> None:
        """TS-NS-4: count_prior_runs returns 0 and logs warning on error."""
        broken_conn = MagicMock()
        broken_conn.execute = MagicMock(side_effect=RuntimeError("DB error"))

        with caplog.at_level(logging.WARNING, logger="afcore.nightshift.prior_attempts"):
            count = count_prior_runs(broken_conn, "fix-issue-42")

        assert count == 0
        assert any("fail-open" in r.getMessage().lower() for r in caplog.records)

    def test_exceeds_attempt_ceiling_returns_false_when_conn_is_none(self) -> None:
        """TS-NS-4: _exceeds_attempt_ceiling returns False when conn is None."""
        from afcore.nightshift.engine import NightShiftEngine

        config = MagicMock()
        config.orchestrator.max_cost = None
        config.orchestrator.max_sessions = None
        config.night_shift.max_attempts_per_issue = 3

        platform = AsyncMock()

        engine = NightShiftEngine(config=config, platform=platform, conn=None)

        result = engine._exceeds_attempt_ceiling(42)
        assert result is False

    def test_exceeds_attempt_ceiling_fails_open_on_exception(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """TS-NS-4: _exceeds_attempt_ceiling returns False on exception."""
        from afcore.nightshift.engine import NightShiftEngine

        config = MagicMock()
        config.orchestrator.max_cost = None
        config.orchestrator.max_sessions = None
        config.night_shift.max_attempts_per_issue = 3

        platform = AsyncMock()
        conn = MagicMock()

        engine = NightShiftEngine(config=config, platform=platform, conn=conn)

        with (
            caplog.at_level(logging.WARNING, logger="afcore.nightshift.engine"),
            patch(
                "afcore.nightshift.prior_attempts.count_prior_runs",
                side_effect=RuntimeError("DB unavailable"),
            ),
        ):
            result = engine._exceeds_attempt_ceiling(42)

        assert result is False


# ---------------------------------------------------------------------------
# TS-NS-5: CarryPatchMonitor retries survive daemon restart
# Requirement: NS-REQ-5
# ---------------------------------------------------------------------------


class TestCarryPatchRetryPersistence:
    """Verify patch retry counters are persisted across daemon restarts."""

    @pytest.fixture
    def carry_db_conn(self) -> Generator[duckdb.DuckDBPyConnection, None, None]:
        """In-memory DuckDB for carry patch tests."""
        conn = duckdb.connect(":memory:")
        yield conn
        conn.close()

    def test_retry_counter_persisted_to_duckdb(
        self,
        carry_db_conn: duckdb.DuckDBPyConnection,
    ) -> None:
        """Retry counter writes are persisted to DuckDB."""
        from afcore.nightshift.carry_patch_monitor import CarryPatchMonitor

        hub_client = MagicMock()
        hub_client.get_patch_status = AsyncMock()
        engine = MagicMock()
        config = MagicMock()
        config.carry_patch.auto_resolve = True
        config.carry_patch.max_resolve_retries = 3

        monitor = CarryPatchMonitor(
            hub_client=hub_client,
            workspace_slug="ws-1",
            config=config,
            engine=engine,
            conn=carry_db_conn,
        )

        # Persist a retry count
        monitor._persist_retry_count("ws-1", "patch-1", 2)

        # Verify it's in the DB
        row = carry_db_conn.execute(
            "SELECT retry_count FROM carry_patch_retries WHERE slug = ? AND patch_id = ?",
            ["ws-1", "patch-1"],
        ).fetchone()
        assert row is not None
        assert row[0] == 2

    def test_retry_counter_loaded_on_construction(
        self,
        carry_db_conn: duckdb.DuckDBPyConnection,
    ) -> None:
        """TS-NS-5: On restart, the monitor reads persisted counters."""
        from afcore.nightshift.carry_patch_monitor import CarryPatchMonitor

        hub_client = MagicMock()
        hub_client.get_patch_status = AsyncMock()
        engine = MagicMock()
        config = MagicMock()
        config.carry_patch.auto_resolve = True
        config.carry_patch.max_resolve_retries = 3

        # First monitor session — persist retries
        monitor1 = CarryPatchMonitor(
            hub_client=hub_client,
            workspace_slug="ws-1",
            config=config,
            engine=engine,
            conn=carry_db_conn,
        )
        monitor1._persist_retry_count("ws-1", "patch-1", 3)

        # "Restart" — construct a new monitor with the same conn
        monitor2 = CarryPatchMonitor(
            hub_client=hub_client,
            workspace_slug="ws-1",
            config=config,
            engine=engine,
            conn=carry_db_conn,
        )

        # The new monitor should have loaded the persisted counter
        assert monitor2._retry_counter[("ws-1", "patch-1")] == 3

    @pytest.mark.asyncio
    async def test_exhausted_patch_skipped_after_restart(
        self,
        carry_db_conn: duckdb.DuckDBPyConnection,
    ) -> None:
        """TS-NS-5: Patch at retry limit is skipped after restart."""
        import dataclasses

        from afcore.nightshift.carry_patch_monitor import CarryPatchMonitor

        @dataclasses.dataclass
        class _PatchDetail:
            id: str
            status: str
            branch_name: str = ""

        @dataclasses.dataclass
        class _PatchStatusDashboard:
            patches: list[_PatchDetail] = dataclasses.field(default_factory=list)

        hub_client = MagicMock()
        engine = MagicMock()
        config = MagicMock()
        config.carry_patch.auto_resolve = True
        config.carry_patch.max_resolve_retries = 2

        # Pre-populate with max retries exhausted
        monitor1 = CarryPatchMonitor(
            hub_client=hub_client,
            workspace_slug="ws-1",
            config=config,
            engine=engine,
            conn=carry_db_conn,
        )
        monitor1._persist_retry_count("ws-1", "patch-1", 2)

        # "Restart" — new monitor loads from DB
        hub_client.get_patch_status = AsyncMock(
            return_value=_PatchStatusDashboard(
                patches=[_PatchDetail(id="patch-1", status="conflict", branch_name="fix/test")]
            )
        )

        monitor2 = CarryPatchMonitor(
            hub_client=hub_client,
            workspace_slug="ws-1",
            config=config,
            engine=engine,
            conn=carry_db_conn,
        )

        result = await monitor2.run_cycle()

        # Patch should be skipped (conflicts_failed=1) and not resolved
        assert result.conflicts_failed == 1
        assert result.conflicts_resolved == 0

    def test_monitor_works_without_conn(self) -> None:
        """Monitor operates normally when conn is None (no persistence)."""
        from afcore.nightshift.carry_patch_monitor import CarryPatchMonitor

        hub_client = MagicMock()
        engine = MagicMock()
        config = MagicMock()
        config.carry_patch.auto_resolve = True
        config.carry_patch.max_resolve_retries = 3

        # Should not raise
        monitor = CarryPatchMonitor(
            hub_client=hub_client,
            workspace_slug="ws-1",
            config=config,
            engine=engine,
            conn=None,
        )
        assert monitor._retry_counter == {}


# ---------------------------------------------------------------------------
# Config: max_attempts_per_issue default
# ---------------------------------------------------------------------------


class TestMaxAttemptsConfig:
    """Verify NightShiftConfig.max_attempts_per_issue defaults correctly."""

    def test_default_value_is_3(self) -> None:
        """max_attempts_per_issue defaults to 3."""
        from afcore.core.config import NightShiftConfig

        config = NightShiftConfig()
        assert config.max_attempts_per_issue == 3

    def test_clamped_to_minimum_1(self) -> None:
        """max_attempts_per_issue is clamped to minimum of 1."""
        from afcore.core.config import NightShiftConfig

        config = NightShiftConfig(max_attempts_per_issue=0)
        assert config.max_attempts_per_issue == 1

    def test_custom_value(self) -> None:
        """max_attempts_per_issue accepts custom values in range."""
        from afcore.core.config import NightShiftConfig

        config = NightShiftConfig(max_attempts_per_issue=5)
        assert config.max_attempts_per_issue == 5


# ---------------------------------------------------------------------------
# LABEL_FAILED constant
# ---------------------------------------------------------------------------


class TestLabelFailedConstant:
    """Verify the LABEL_FAILED constant is defined correctly."""

    def test_label_value(self) -> None:
        assert LABEL_FAILED == "af:failed"
