"""DuckDB sink: session outcomes and tool signals (always-on).

Requirements: 11-REQ-5.1, 11-REQ-5.2, 11-REQ-5.3, 11-REQ-5.4, 11-REQ-5.E1,
              38-REQ-3.1, 40-REQ-5.1, 40-REQ-5.2
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import duckdb  # noqa: F401
from afaudit.events import AuditEvent
from afaudit.sink import SessionOutcome, ToolCall, ToolError

logger = logging.getLogger("afcore.knowledge.duckdb_sink")

# ---------------------------------------------------------------------------
# Shared INSERT for session_outcomes — single source of truth for the 18-
# column set.  Both DuckDBSink.record_session_outcome and
# afcore.engine.state.record_session call insert_session_outcome_row() so
# the column list cannot diverge.
# ---------------------------------------------------------------------------

_SESSION_OUTCOMES_INSERT = """
INSERT INTO session_outcomes (
    id, spec_name, task_group, node_id, touched_path,
    status, input_tokens, output_tokens, duration_ms, created_at,
    run_id, attempt, cost, model, archetype,
    commit_sha, error_message, is_transport_error
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def insert_session_outcome_row(
    conn: duckdb.DuckDBPyConnection,
    values: list,
) -> None:
    """Execute the canonical 18-column INSERT into session_outcomes.

    Both ``DuckDBSink.record_session_outcome`` and
    ``afcore.engine.state.record_session`` call this helper so the column
    set cannot diverge.  Any schema change to session_outcomes must update
    ``_SESSION_OUTCOMES_INSERT`` — the single definition — and both callers
    will follow.
    """
    conn.execute(_SESSION_OUTCOMES_INSERT, values)


class DuckDBSink:
    """SessionSink implementation backed by DuckDB.

    Session outcomes and tool signals are always written unconditionally.
    DuckDB errors propagate to the caller (38-REQ-3.1).
    """

    def __init__(
        self,
        conn: duckdb.DuckDBPyConnection,
    ) -> None:
        self._conn = conn

    def record_session_outcome(self, outcome: SessionOutcome) -> None:
        """Insert a single row into session_outcomes.

        Writes all 18 columns via the shared ``insert_session_outcome_row``
        helper so the column set stays in sync with
        ``afcore.engine.state.record_session``.

        Fields not carried by :class:`SessionOutcome` (``run_id``,
        ``attempt``, ``cost``, ``model``, ``archetype``, ``commit_sha``)
        are written as ``NULL`` / default.  A future protocol extension
        that adds those fields will automatically flow through via
        ``getattr`` fallback.

        Multiple touched paths are stored as a comma-delimited string in the
        touched_path column so that each session produces exactly one row
        (fixes #457 — per-file row explosion).  If touched_paths is empty,
        touched_path is stored as NULL.
        DuckDB errors propagate to the caller (38-REQ-3.1).
        """
        touched_path: str | None = ",".join(outcome.touched_paths) if outcome.touched_paths else None
        insert_session_outcome_row(
            self._conn,
            [
                str(outcome.id),
                outcome.spec_name,
                outcome.task_group,
                outcome.node_id,
                touched_path,
                outcome.status,
                outcome.input_tokens,
                outcome.output_tokens,
                outcome.duration_ms,
                outcome.created_at,
                getattr(outcome, "run_id", None),
                getattr(outcome, "attempt", None),
                getattr(outcome, "cost", None),
                getattr(outcome, "model", None),
                getattr(outcome, "archetype", None),
                getattr(outcome, "commit_sha", None),
                outcome.error_message,
                outcome.is_transport_error,
            ],
        )

    def record_tool_call(self, call: ToolCall) -> None:
        """Insert a row into tool_calls (always-on).

        DuckDB errors propagate to the caller (38-REQ-3.1).
        """
        self._conn.execute(
            """
            INSERT INTO tool_calls
                (id, session_id, node_id, tool_name, called_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                str(call.id),
                call.session_id,
                call.node_id,
                call.tool_name,
                call.called_at,
            ],
        )

    def record_tool_error(self, error: ToolError) -> None:
        """Insert a row into tool_errors (always-on).

        DuckDB errors propagate to the caller (38-REQ-3.1).
        """
        self._conn.execute(
            """
            INSERT INTO tool_errors
                (id, session_id, node_id, tool_name, failed_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                str(error.id),
                error.session_id,
                error.node_id,
                error.tool_name,
                error.failed_at,
            ],
        )

    def emit_audit_event(self, event: AuditEvent) -> None:
        """Insert audit event into audit_events table.

        DuckDB errors propagate to the caller (38-REQ-3.1).
        Requirements: 40-REQ-5.1, 40-REQ-5.2
        """
        self._conn.execute(
            """
            INSERT INTO audit_events
                (id, timestamp, run_id, event_type, node_id, session_id,
                 archetype, severity, payload)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                str(event.id),
                event.timestamp,
                event.run_id,
                event.event_type.value,
                event.node_id,
                event.session_id,
                event.archetype,
                event.severity.value,
                json.dumps(event.payload),
            ],
        )

    def close(self) -> None:
        """No-op. Connection lifecycle is managed by KnowledgeDB."""
        pass


def enforce_audit_retention(
    audit_dir: Path,
    conn: object,
    *,
    max_runs: int = 20,
) -> None:
    """Delete audit data for runs beyond the retention limit.

    This function handles both the DuckDB row-deletion and the
    file-deletion for ``audit_*.jsonl`` files.  It was moved here from
    ``afcore.knowledge.audit`` so that the DuckDB dependency stays in
    the afcore package while the file-only retention half lives in
    ``afaudit.cleanup``.

    Args:
        audit_dir: Path to the audit directory.
        conn: A DuckDB connection.
        max_runs: Maximum number of runs to retain.
    """
    import duckdb as _duckdb

    if not isinstance(conn, _duckdb.DuckDBPyConnection):
        return

    # 1. Query distinct run_ids ordered by oldest timestamp
    rows = conn.execute(
        """
        SELECT run_id, MIN(timestamp) AS earliest
        FROM audit_events
        GROUP BY run_id
        ORDER BY earliest ASC
        """
    ).fetchall()

    if len(rows) <= max_runs:
        return

    # 2. Identify runs to delete (oldest beyond retention limit)
    runs_to_delete = [row[0] for row in rows[: len(rows) - max_runs]]

    # 3. Delete from DuckDB
    for run_id in runs_to_delete:
        conn.execute("DELETE FROM audit_events WHERE run_id = ?", [run_id])

    # 4. Delete JSONL files
    for run_id in runs_to_delete:
        jsonl_path = audit_dir / f"audit_{run_id}.jsonl"
        try:
            if jsonl_path.exists():
                jsonl_path.unlink()
        except OSError:
            logger.warning("Failed to delete audit JSONL file: %s", jsonl_path)

    logger.info(
        "Audit retention: deleted %d old run(s), kept %d",
        len(runs_to_delete),
        max_runs,
    )
