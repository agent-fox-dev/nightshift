"""Tests for audit retention wiring in nightshift startup.

Issue #85: enforce_audit_retention has no production caller, so audit_events
grows without bound.

Requirements: NS-REQ-1, NS-REQ-2, NS-REQ-4, NS-REQ-5
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


def _make_config(*, audit_max_runs: int = 20) -> SimpleNamespace:
    """Return a minimal config-like object with knowledge.audit_max_runs."""
    return SimpleNamespace(
        knowledge=SimpleNamespace(
            store_path=":memory:",
            audit_max_runs=audit_max_runs,
            provider=SimpleNamespace(),
        ),
    )


def _make_kdb_mock():
    """Return a mock KnowledgeDB with a connection attribute."""
    kdb = MagicMock()
    kdb.connection = MagicMock()
    return kdb


class TestAuditRetentionStartup:
    """Verify that init_knowledge calls enforce_audit_retention."""

    def test_retention_called_on_startup(self) -> None:
        """NS-REQ-1: enforce_audit_retention is called from init_knowledge."""
        from nightshift._startup import init_knowledge

        config = _make_config(audit_max_runs=10)
        kdb = _make_kdb_mock()

        with (
            patch("afcore.knowledge.db.open_knowledge_store", return_value=kdb),
            patch("afaudit.sink.SinkDispatcher"),
            patch("afcore.knowledge.fox_provider.FoxKnowledgeProvider"),
            patch("afcore.core.config.resolve_spec_root", return_value="/tmp/specs"),
            patch("afcore.engine.migrations.run_startup_migrations"),
            patch("afcore.knowledge.duckdb_sink.enforce_audit_retention") as mock_retention,
        ):
            result = init_knowledge(config, "/tmp/project")

        mock_retention.assert_called_once()
        # Verify max_runs was passed from config
        _, kwargs = mock_retention.call_args
        assert kwargs["max_runs"] == 10
        # init_knowledge should return a valid triple
        assert result[0] is kdb

    def test_retention_uses_config_default(self) -> None:
        """NS-REQ-2: audit_max_runs defaults to 20 from KnowledgeConfig."""
        from afcore.core.config import KnowledgeConfig

        kc = KnowledgeConfig()
        assert kc.audit_max_runs == 20

    def test_retention_failure_does_not_block_startup(self, caplog) -> None:
        """NS-REQ-4: Retention failure is logged but does not abort startup."""
        from nightshift._startup import init_knowledge

        config = _make_config()
        kdb = _make_kdb_mock()

        with (
            patch("afcore.knowledge.db.open_knowledge_store", return_value=kdb),
            patch("afaudit.sink.SinkDispatcher"),
            patch("afcore.knowledge.fox_provider.FoxKnowledgeProvider"),
            patch("afcore.core.config.resolve_spec_root", return_value="/tmp/specs"),
            patch("afcore.engine.migrations.run_startup_migrations"),
            patch(
                "afcore.knowledge.duckdb_sink.enforce_audit_retention",
                side_effect=RuntimeError("simulated DuckDB I/O error"),
            ),
            caplog.at_level(logging.WARNING),
        ):
            result = init_knowledge(config, "/tmp/project")

        # Daemon should still return a valid triple
        assert result[0] is kdb
        assert result[1] is not None
        assert result[2] is not None

        # Warning should have been logged
        warning_records = [
            r for r in caplog.records if r.levelno == logging.WARNING and "retention" in r.message.lower()
        ]
        assert len(warning_records) >= 1

    def test_retention_called_with_audit_dir(self) -> None:
        """NS-REQ-1: enforce_audit_retention receives the AUDIT_DIR path."""
        from nightshift._startup import init_knowledge

        config = _make_config()
        kdb = _make_kdb_mock()

        with (
            patch("afcore.knowledge.db.open_knowledge_store", return_value=kdb),
            patch("afaudit.sink.SinkDispatcher"),
            patch("afcore.knowledge.fox_provider.FoxKnowledgeProvider"),
            patch("afcore.core.config.resolve_spec_root", return_value="/tmp/specs"),
            patch("afcore.engine.migrations.run_startup_migrations"),
            patch("afcore.knowledge.duckdb_sink.enforce_audit_retention") as mock_retention,
            patch("afaudit.constants.AUDIT_DIR", "/tmp/audit"),
        ):
            init_knowledge(config, "/tmp/project")

        args, _ = mock_retention.call_args
        assert args[0] == "/tmp/audit"


class TestAuditRetentionLogging:
    """NS-REQ-5: Verify the INFO-level log message from enforce_audit_retention."""

    def test_deletion_logs_counts(
        self,
        knowledge_conn,
        tmp_path,
        caplog,
    ) -> None:
        """NS-REQ-5: After retention, an INFO log reports deleted and kept counts."""
        import json
        from datetime import UTC, datetime, timedelta
        from uuid import uuid4

        from afcore.knowledge.duckdb_sink import enforce_audit_retention

        audit_dir = tmp_path / "audit"
        audit_dir.mkdir()

        base_time = datetime(2026, 1, 1, tzinfo=UTC)
        for i in range(25):
            run_id = f"20260101_{i:06d}_abc{i:03d}"
            for j in range(2):
                ts = base_time + timedelta(hours=i, seconds=j)
                knowledge_conn.execute(
                    """
                    INSERT INTO audit_events
                        (id, timestamp, run_id, event_type, node_id, session_id,
                         archetype, severity, payload)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        str(uuid4()),
                        ts,
                        run_id,
                        "run.start" if j == 0 else "run.complete",
                        "",
                        "",
                        "",
                        "info",
                        json.dumps({}),
                    ],
                )
            jsonl_path = audit_dir / f"audit_{run_id}.jsonl"
            jsonl_path.write_text(json.dumps({"run_id": run_id}) + "\n")

        with caplog.at_level(logging.INFO, logger="afcore.knowledge.duckdb_sink"):
            enforce_audit_retention(audit_dir, knowledge_conn, max_runs=20)

        info_records = [r for r in caplog.records if r.levelno == logging.INFO and "retention" in r.message.lower()]
        assert len(info_records) >= 1
        msg = info_records[0].message
        assert "deleted 5" in msg
        assert "kept 20" in msg


# Use knowledge_conn fixture from afcore tests
@pytest.fixture
def knowledge_conn():
    """In-memory DuckDB with production schema for retention tests."""
    import duckdb
    from afcore.knowledge.migrations import run_migrations

    conn = duckdb.connect(":memory:")
    run_migrations(conn)
    yield conn
    try:
        conn.close()
    except Exception:
        pass
