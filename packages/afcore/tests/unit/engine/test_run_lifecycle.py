"""Run lifecycle and stale run detection tests.

Test Spec: TS-118-13 (stale run detection on startup),
           TS-118-E8 (multiple stale runs cleaned)
Requirements: 118-REQ-6.1, 118-REQ-6.E2
"""

from __future__ import annotations

import duckdb
import pytest
from afcore.engine.state import cleanup_stale_runs


class TestStaleRunDetection:
    """TS-118-13: stale 'running' runs are detected and transitioned to 'stalled'.

    Requirements: 118-REQ-6.1
    """

    @pytest.fixture
    def runs_db(self) -> duckdb.DuckDBPyConnection:
        """Create an in-memory DuckDB with a runs table."""
        conn = duckdb.connect(":memory:")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS runs (
                id                  VARCHAR PRIMARY KEY,
                plan_content_hash   VARCHAR NOT NULL,
                started_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                completed_at        TIMESTAMP,
                status              VARCHAR NOT NULL DEFAULT 'running',
                total_input_tokens  BIGINT NOT NULL DEFAULT 0,
                total_output_tokens BIGINT NOT NULL DEFAULT 0,
                total_cost          DOUBLE NOT NULL DEFAULT 0.0,
                total_sessions      INTEGER NOT NULL DEFAULT 0
            )
        """)
        return conn

    def test_stale_run_detected_and_transitioned(
        self,
        runs_db: duckdb.DuckDBPyConnection,
    ) -> None:
        """Stale 'running' run from prior process is transitioned to 'stalled'."""
        # Insert a stale run (from prior process) and a completed run
        runs_db.execute(
            "INSERT INTO runs (id, plan_content_hash, status) VALUES (?, ?, ?)",
            ["stale_run", "hash1", "running"],
        )
        runs_db.execute(
            "INSERT INTO runs (id, plan_content_hash, status, completed_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
            ["completed_run", "hash2", "completed"],
        )

        # cleanup_stale_runs transitions stale runs;
        # spec requires status='stalled' (not 'interrupted')
        stale_count = cleanup_stale_runs(runs_db, "current_run")

        assert stale_count == 1

        # The stale run should now be 'stalled'
        row = runs_db.execute("SELECT status FROM runs WHERE id = ?", ["stale_run"]).fetchone()
        assert row is not None
        assert row[0] == "stalled"

        # The completed run should be unchanged
        row2 = runs_db.execute("SELECT status FROM runs WHERE id = ?", ["completed_run"]).fetchone()
        assert row2 is not None
        assert row2[0] == "completed"


class TestMultipleStaleRunsCleaned:
    """TS-118-E8: all stale runs are cleaned on startup.

    Requirements: 118-REQ-6.E2
    """

    def test_all_stale_runs_transitioned(self) -> None:
        """When 3 stale runs exist, all should be transitioned to 'stalled'."""
        conn = duckdb.connect(":memory:")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS runs (
                id                  VARCHAR PRIMARY KEY,
                plan_content_hash   VARCHAR NOT NULL,
                started_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                completed_at        TIMESTAMP,
                status              VARCHAR NOT NULL DEFAULT 'running',
                total_input_tokens  BIGINT NOT NULL DEFAULT 0,
                total_output_tokens BIGINT NOT NULL DEFAULT 0,
                total_cost          DOUBLE NOT NULL DEFAULT 0.0,
                total_sessions      INTEGER NOT NULL DEFAULT 0
            )
        """)

        # Insert 3 stale runs
        for rid in ["r1", "r2", "r3"]:
            conn.execute(
                "INSERT INTO runs (id, plan_content_hash, status) VALUES (?, ?, ?)",
                [rid, "hash", "running"],
            )

        count = cleanup_stale_runs(conn, "current_run")
        assert count == 3

        for rid in ["r1", "r2", "r3"]:
            row = conn.execute("SELECT status FROM runs WHERE id = ?", [rid]).fetchone()
            assert row is not None
            assert row[0] == "stalled"

        conn.close()


class TestCleanupStaleRunsAtShutdown:
    """Shutdown cleanup transitions all running rows when current_run_id is empty.

    Validates the pattern used by cleanup_runs_on_shutdown() in _startup.py.
    """

    def test_empty_run_id_transitions_all(self) -> None:
        """cleanup_stale_runs('') transitions every running row."""
        conn = duckdb.connect(":memory:")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS runs (
                id                  VARCHAR PRIMARY KEY,
                plan_content_hash   VARCHAR NOT NULL,
                started_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                completed_at        TIMESTAMP,
                status              VARCHAR NOT NULL DEFAULT 'running',
                total_input_tokens  BIGINT NOT NULL DEFAULT 0,
                total_output_tokens BIGINT NOT NULL DEFAULT 0,
                total_cost          DOUBLE NOT NULL DEFAULT 0.0,
                total_sessions      INTEGER NOT NULL DEFAULT 0
            )
        """)

        conn.execute(
            "INSERT INTO runs (id, plan_content_hash, status) VALUES (?, ?, ?)",
            ["active_run", "hash1", "running"],
        )

        # Passing "" means no run is excluded — all running rows transition
        count = cleanup_stale_runs(conn, "")
        assert count == 1

        row = conn.execute("SELECT status FROM runs WHERE id = ?", ["active_run"]).fetchone()
        assert row is not None
        assert row[0] == "stalled"

        conn.close()
