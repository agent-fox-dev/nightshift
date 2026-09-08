"""Verify dead code removal from issue #86.

Tests that:
- review_persistence.py is no longer importable (NS-REQ-1)
- insert_drift_findings is no longer defined (NS-REQ-1)
- drift supersession functions are no longer defined (NS-REQ-1)
- generate_archetype_summary is no longer re-exported from fox_provider (NS-REQ-5)
- drift_findings and review_findings tables still exist after migration (NS-REQ-4)
- [DRIFT] and [CROSS-SPEC] tags are no longer emitted by retrieve() (NS-REQ-1)

Requirements: NS-REQ-1, NS-REQ-4, NS-REQ-5
"""

from __future__ import annotations

import importlib

import duckdb
import pytest
from afcore.core.config import KnowledgeProviderConfig
from afcore.knowledge.fox_provider import FoxKnowledgeProvider
from afcore.knowledge.migrations import run_migrations

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def schema_conn() -> duckdb.DuckDBPyConnection:
    """In-memory DuckDB with schema applied."""
    conn = duckdb.connect(":memory:")
    run_migrations(conn)
    return conn


@pytest.fixture()
def provider(schema_conn: duckdb.DuckDBPyConnection) -> FoxKnowledgeProvider:
    """FoxKnowledgeProvider backed by in-memory DuckDB."""

    class _FakeKnowledgeDB:
        @property
        def connection(self) -> duckdb.DuckDBPyConnection:
            return schema_conn

    config = KnowledgeProviderConfig()
    return FoxKnowledgeProvider(_FakeKnowledgeDB(), config)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# NS-REQ-1: review_persistence.py is deleted
# ---------------------------------------------------------------------------


class TestReviewPersistenceDeleted:
    """Verify the dead review_persistence module no longer exists."""

    def test_module_not_importable(self) -> None:
        """review_persistence.py cannot be imported."""
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("afcore.engine.review_persistence")

    def test_persist_review_findings_not_importable(self) -> None:
        """persist_review_findings is not importable from any module."""
        with pytest.raises(ImportError):
            from afcore.engine.review_persistence import persist_review_findings  # noqa: F401


# ---------------------------------------------------------------------------
# NS-REQ-1: insert_drift_findings and drift supersession removed
# ---------------------------------------------------------------------------


class TestDriftFunctionsRemoved:
    """Verify drift write/query/supersession functions are removed."""

    def test_insert_drift_findings_removed(self) -> None:
        """insert_drift_findings is no longer defined in review_store."""
        from afcore.knowledge import review_store

        assert not hasattr(review_store, "insert_drift_findings"), (
            "insert_drift_findings should have been removed from review_store"
        )

    def test_supersede_drift_findings_by_files_removed(self) -> None:
        """supersede_drift_findings_by_files is no longer defined."""
        from afcore.knowledge import review_store

        assert not hasattr(review_store, "supersede_drift_findings_by_files"), (
            "supersede_drift_findings_by_files should have been removed"
        )

    def test_supersede_stale_pre_code_findings_removed(self) -> None:
        """supersede_stale_pre_code_findings is no longer defined."""
        from afcore.knowledge import review_store

        assert not hasattr(review_store, "supersede_stale_pre_code_findings"), (
            "supersede_stale_pre_code_findings should have been removed"
        )

    def test_query_active_drift_findings_removed(self) -> None:
        """query_active_drift_findings is no longer defined."""
        from afcore.knowledge import review_store

        assert not hasattr(review_store, "query_active_drift_findings"), (
            "query_active_drift_findings should have been removed"
        )

    def test_query_cross_spec_drift_findings_removed(self) -> None:
        """query_cross_spec_drift_findings is no longer defined."""
        from afcore.knowledge import review_store

        assert not hasattr(review_store, "query_cross_spec_drift_findings"), (
            "query_cross_spec_drift_findings should have been removed"
        )

    def test_drift_finding_dataclass_retained(self) -> None:
        """DriftFinding dataclass is retained for review_parser compatibility."""
        from afcore.knowledge.review_store import DriftFinding

        assert DriftFinding is not None


# ---------------------------------------------------------------------------
# NS-REQ-1: [DRIFT] and [CROSS-SPEC] are no longer emitted by retrieve()
# ---------------------------------------------------------------------------


class TestDriftRetrievalRemoved:
    """Drift and cross-spec retrieval paths removed from fox_provider."""

    def test_no_drift_tag_in_retrieve(
        self,
        schema_conn: duckdb.DuckDBPyConnection,
        provider: FoxKnowledgeProvider,
    ) -> None:
        """retrieve() does not emit [DRIFT] tags even when drift_findings has data."""
        # Insert a drift finding directly via SQL (insert_drift_findings is removed)
        schema_conn.execute(
            "INSERT INTO drift_findings "
            "(id, severity, description, spec_ref, artifact_ref, spec_name, task_group, session_id, created_at) "
            "VALUES (gen_random_uuid(), ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
            ["critical", "test drift", "REQ-1", "src/foo.py", "test-spec", "1", "s1"],
        )

        result = provider.retrieve(
            "test-spec",
            "some task",
            task_group="1",
            file_footprint=["src/foo.py"],
        )
        drift_items = [r for r in result if "[DRIFT]" in r]
        assert drift_items == [], "retrieve() should not emit [DRIFT] items"

    def test_no_cross_spec_tag_in_retrieve(
        self,
        schema_conn: duckdb.DuckDBPyConnection,
        provider: FoxKnowledgeProvider,
    ) -> None:
        """retrieve() does not emit [CROSS-SPEC] tags."""
        # Insert a drift finding directly via SQL
        schema_conn.execute(
            "INSERT INTO drift_findings "
            "(id, severity, description, spec_ref, artifact_ref, spec_name, task_group, session_id, created_at) "
            "VALUES (gen_random_uuid(), ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
            ["critical", "cross spec drift", "REQ-1", "src/bar.py", "other-spec", "1", "s1"],
        )

        result = provider.retrieve(
            "test-spec",
            "some task",
            task_group="1",
            file_footprint=["src/bar.py"],
        )
        cross_spec_items = [r for r in result if "[CROSS-SPEC]" in r]
        assert cross_spec_items == [], "retrieve() should not emit [CROSS-SPEC] items"


# ---------------------------------------------------------------------------
# NS-REQ-1: drift supersession not called from fox_provider.ingest()
# ---------------------------------------------------------------------------


class TestDriftSupersessionNotCalledFromIngest:
    """Verify ingest() no longer calls drift supersession functions."""

    def test_ingest_completes_without_drift_supersession(
        self,
        schema_conn: duckdb.DuckDBPyConnection,
        provider: FoxKnowledgeProvider,
    ) -> None:
        """ingest() for a completed coder session succeeds without drift code."""
        # Insert a drift finding via SQL to ensure it's NOT superseded
        schema_conn.execute(
            "INSERT INTO drift_findings "
            "(id, severity, description, spec_ref, artifact_ref, spec_name, task_group, session_id, created_at) "
            "VALUES (gen_random_uuid(), ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
            ["critical", "test drift", "REQ-1", "src/foo.py", "test-spec", "1", "s0"],
        )

        # ingest() should succeed without calling any drift functions
        provider.ingest(
            session_id="s1",
            spec_name="test-spec",
            context={
                "session_status": "completed",
                "touched_files": ["src/foo.py"],
                "archetype": "coder",
            },
        )

        # Drift finding should still be active (not superseded) since
        # drift supersession is no longer wired
        row = schema_conn.execute("SELECT superseded_by FROM drift_findings WHERE spec_name = 'test-spec'").fetchone()
        assert row is not None
        assert row[0] is None, "Drift finding should NOT be superseded (drift supersession removed)"


# ---------------------------------------------------------------------------
# NS-REQ-4: Tables still exist after migration
# ---------------------------------------------------------------------------


class TestTablesStillExist:
    """Verify review_findings and drift_findings tables exist after migration."""

    def test_review_findings_table_exists(self, schema_conn: duckdb.DuckDBPyConnection) -> None:
        """review_findings table can be queried without error."""
        result = schema_conn.execute("SELECT * FROM review_findings LIMIT 1").fetchall()
        assert result == []

    def test_drift_findings_table_exists(self, schema_conn: duckdb.DuckDBPyConnection) -> None:
        """drift_findings table can be queried without error."""
        result = schema_conn.execute("SELECT * FROM drift_findings LIMIT 1").fetchall()
        assert result == []


# ---------------------------------------------------------------------------
# NS-REQ-5: generate_archetype_summary removed from fox_provider re-exports
# ---------------------------------------------------------------------------


class TestArchetypeSummaryReexport:
    """Verify generate_archetype_summary is no longer re-exported from fox_provider."""

    def test_not_in_fox_provider_all(self) -> None:
        """generate_archetype_summary is not in fox_provider.__all__."""
        from afcore.knowledge import fox_provider

        assert "generate_archetype_summary" not in fox_provider.__all__

    def test_still_importable_from_formatting(self) -> None:
        """generate_archetype_summary is still available in formatting module."""
        from afcore.knowledge.formatting import generate_archetype_summary

        assert callable(generate_archetype_summary)
