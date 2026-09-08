"""Integration tests for session summary storage and cross-run retrieval.

Exercises the real FoxKnowledgeProvider (not a MagicMock) against an
in-memory DuckDB to verify the end-to-end flow of storing summaries
during post-harvest ingestion and retrieving them in subsequent runs.

Issue: #83 — session summaries were never stored, and the retrieval
query could never match.  These tests verify the fix.

Test Spec: TS-NS-1, TS-NS-3, TS-NS-4, TS-NS-5
Requirements: NS-REQ-1, NS-REQ-3, NS-REQ-4, NS-REQ-5
"""

from __future__ import annotations

import uuid

import duckdb
from afcore.core.config import KnowledgeProviderConfig
from afcore.knowledge.db import KnowledgeDB
from afcore.knowledge.fox_provider import FoxKnowledgeProvider
from afcore.knowledge.migrations import run_migrations
from afcore.knowledge.summary_store import (
    SummaryRecord,
    insert_summary,
    query_same_spec_summaries,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_conn() -> duckdb.DuckDBPyConnection:
    """Create an in-memory DuckDB with all migrations applied."""
    conn = duckdb.connect(":memory:")
    run_migrations(conn)
    return conn


def _make_provider(
    conn: duckdb.DuckDBPyConnection,
    run_id: str | None = None,
) -> FoxKnowledgeProvider:
    """Build a real FoxKnowledgeProvider backed by *conn*."""
    db = KnowledgeDB.__new__(KnowledgeDB)
    db._conn = conn
    provider = FoxKnowledgeProvider(db, KnowledgeProviderConfig())
    if run_id is not None:
        provider.set_run_id(run_id)
    return provider


def _make_summary(
    *,
    spec_name: str = "fix-issue-42",
    task_group: str = "0",
    run_id: str = "run-A",
    archetype: str = "coder",
    attempt: int = 1,
    summary: str = "Fixed the widget. Watch out: fragile serialization.",
) -> SummaryRecord:
    return SummaryRecord(
        id=str(uuid.uuid4()),
        node_id=f"{spec_name}:0:coder",
        run_id=run_id,
        spec_name=spec_name,
        task_group=task_group,
        archetype=archetype,
        attempt=attempt,
        summary=summary,
        created_at="2026-09-07T10:00:00",
    )


# ===========================================================================
# TS-NS-1: Post-harvest ingest stores a summary row in session_summaries
# ===========================================================================
# Requirements: NS-REQ-1


class TestPostHarvestIngestStoresSummary:
    """Verify that ingest() with session_status='completed' and a non-empty
    summary stores a row in session_summaries.

    Test Spec: TS-NS-1
    """

    def test_summary_stored_with_session_status_completed(self) -> None:
        conn = _make_conn()
        try:
            provider = _make_provider(conn, run_id="run-1")

            context: dict = {
                "session_status": "completed",
                "touched_files": ["src/foo.py", "src/bar.py"],
                "project_root": "/tmp/repo",
                "sink": None,
                "run_id": "run-1",
                "archetype": "coder",
                "task_group": "0",
                "attempt": 1,
                "summary": "The fix is complete.",
                "rejected_approaches": [{"approach": "A", "reason": "too slow"}],
                "gotchas": ["fragile serialization"],
                "assumptions": ["Python 3.12+"],
            }

            provider.ingest("fix-issue-42", "fix-issue-42", context)

            # Verify one row in session_summaries
            count = conn.execute("SELECT COUNT(*) FROM session_summaries WHERE spec_name = 'fix-issue-42'").fetchone()[
                0
            ]
            assert count == 1

            # Verify enriched summary text
            summary = conn.execute("SELECT summary FROM session_summaries WHERE spec_name = 'fix-issue-42'").fetchone()[
                0
            ]
            assert "The fix is complete." in summary
            assert "Watch out:" in summary
            assert "Assumes:" in summary
        finally:
            conn.close()


# ===========================================================================
# TS-NS-3: Cross-run retrieval returns prior summaries
# ===========================================================================
# Requirements: NS-REQ-3


class TestCrossRunRetrievalReturnsPriorSummaries:
    """Verify that retrieve() on a second run (different run_id) returns
    summaries stored during a prior run.

    Test Spec: TS-NS-3
    """

    def test_retrieve_returns_prior_run_summary(self) -> None:
        conn = _make_conn()
        try:
            # Store a summary from run-A
            insert_summary(
                conn,
                _make_summary(
                    spec_name="fix-issue-42",
                    task_group="0",
                    run_id="run-A",
                    summary="Fixed the widget. Watch out: fragile serialization.",
                ),
            )

            # Retrieve on run-B (different run_id)
            provider = _make_provider(conn, run_id="run-B")
            result = provider.retrieve(
                "fix-issue-42",
                "fix the widget",
                task_group="0",
            )

            context_items = [i for i in result if "[CONTEXT]" in i]
            assert len(context_items) >= 1, "Expected at least one [CONTEXT] item from prior run"
            assert "Fixed the widget" in context_items[0]
        finally:
            conn.close()

    def test_retrieve_returns_prior_run_summary_without_set_run_id(self) -> None:
        """Cross-run retrieval works even if set_run_id was not called."""
        conn = _make_conn()
        try:
            insert_summary(
                conn,
                _make_summary(
                    spec_name="fix-issue-42",
                    task_group="0",
                    run_id="run-A",
                ),
            )

            # Don't call set_run_id
            provider = _make_provider(conn)
            result = provider.retrieve(
                "fix-issue-42",
                "fix the widget",
                task_group="0",
            )

            context_items = [i for i in result if "[CONTEXT]" in i]
            assert len(context_items) >= 1
        finally:
            conn.close()


# ===========================================================================
# TS-NS-4: Real FoxKnowledgeProvider fails without session_status
# ===========================================================================
# Requirements: NS-REQ-4


class TestIngestWithoutSessionStatusStoresNothing:
    """Verify that when session_status is absent from context, the real
    FoxKnowledgeProvider does NOT store a summary — confirming the gate
    is tested with the real implementation.

    Test Spec: TS-NS-4
    """

    def test_no_summary_stored_without_session_status(self) -> None:
        conn = _make_conn()
        try:
            provider = _make_provider(conn, run_id="run-1")

            context: dict = {
                # session_status deliberately omitted
                "touched_files": ["src/foo.py"],
                "project_root": "/tmp/repo",
                "sink": None,
                "run_id": "run-1",
                "archetype": "coder",
                "task_group": "0",
                "attempt": 1,
                "summary": "The fix is complete.",
            }

            provider.ingest("fix-issue-42", "fix-issue-42", context)

            count = conn.execute("SELECT COUNT(*) FROM session_summaries").fetchone()[0]
            assert count == 0, "Summary should NOT be stored when session_status is absent"
        finally:
            conn.close()

    def test_no_summary_stored_with_non_completed_status(self) -> None:
        conn = _make_conn()
        try:
            provider = _make_provider(conn, run_id="run-1")

            context: dict = {
                "session_status": "failed",
                "touched_files": ["src/foo.py"],
                "project_root": "/tmp/repo",
                "sink": None,
                "run_id": "run-1",
                "archetype": "coder",
                "task_group": "0",
                "attempt": 1,
                "summary": "The fix is complete.",
            }

            provider.ingest("fix-issue-42", "fix-issue-42", context)

            count = conn.execute("SELECT COUNT(*) FROM session_summaries").fetchone()[0]
            assert count == 0
        finally:
            conn.close()


# ===========================================================================
# TS-NS-5: query_same_spec_summaries cross-run retrieval
# ===========================================================================
# Requirements: NS-REQ-5


class TestQuerySameSpecSummariesCrossRun:
    """Verify that query_same_spec_summaries returns rows from different
    run IDs when run_id=None (cross-run mode).

    Test Spec: TS-NS-5
    """

    def test_cross_run_retrieval_with_run_id_none(self) -> None:
        conn = _make_conn()
        try:
            insert_summary(
                conn,
                _make_summary(
                    spec_name="fix-issue-42",
                    task_group="0",
                    run_id="run-A",
                ),
            )

            # Query with run_id=None and matching task_group
            results = query_same_spec_summaries(
                conn,
                "fix-issue-42",
                task_group="0",
                run_id=None,
            )
            assert len(results) == 1
            assert results[0].run_id == "run-A"
            assert results[0].spec_name == "fix-issue-42"
        finally:
            conn.close()

    def test_intra_run_retrieval_with_run_id_still_works(self) -> None:
        """Intra-run mode (with run_id) still uses strict < comparison."""
        conn = _make_conn()
        try:
            insert_summary(
                conn,
                _make_summary(
                    spec_name="spec_a",
                    task_group="2",
                    run_id="run-1",
                ),
            )

            # Same run_id, task_group="3" > "2": should return the row
            results = query_same_spec_summaries(
                conn,
                "spec_a",
                task_group="3",
                run_id="run-1",
            )
            assert len(results) == 1

            # Same run_id, task_group="2" = "2": strict < means no match
            results = query_same_spec_summaries(
                conn,
                "spec_a",
                task_group="2",
                run_id="run-1",
            )
            assert len(results) == 0

            # Different run_id: no match (intra-run mode scopes by run_id)
            results = query_same_spec_summaries(
                conn,
                "spec_a",
                task_group="3",
                run_id="run-2",
            )
            assert len(results) == 0
        finally:
            conn.close()

    def test_cross_run_same_task_group_matches(self) -> None:
        """Cross-run mode uses <= so task_group='0' matches stored '0'."""
        conn = _make_conn()
        try:
            insert_summary(
                conn,
                _make_summary(
                    spec_name="fix-issue-99",
                    task_group="0",
                    run_id="run-A",
                ),
            )

            # Cross-run mode: task_group="0" matches stored "0" via <=
            results = query_same_spec_summaries(
                conn,
                "fix-issue-99",
                task_group="0",
                run_id=None,
            )
            assert len(results) == 1
        finally:
            conn.close()
