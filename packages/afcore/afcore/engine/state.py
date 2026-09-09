"""Execution state persistence: data models, save/load, run lifecycle.

Requirements: 04-REQ-4.1, 04-REQ-4.2, 04-REQ-4.3,
              105-REQ-2.1, 105-REQ-2.3, 105-REQ-2.E1,
              105-REQ-3.2, 105-REQ-3.E1,
              105-REQ-4.2, 105-REQ-4.3, 105-REQ-4.4, 105-REQ-4.E1
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import duckdb

logger = logging.getLogger(__name__)


@dataclass
class SessionOutcomeRecord:
    """Unified session record written directly to the session_outcomes DB table.

    Replaces the legacy SessionRecord + ExecutionState.session_history pattern.
    All fields map 1:1 to session_outcomes columns (including the extended
    columns added by the v11 migration).

    Requirements: 105-REQ-3.1, 105-REQ-3.2
    """

    id: str
    spec_name: str
    task_group: str
    node_id: str
    touched_path: str  # reserved, currently unused (always empty string)
    status: str
    input_tokens: int
    output_tokens: int
    duration_ms: int
    created_at: str  # ISO 8601
    run_id: str
    attempt: int
    cost: float
    model: str
    archetype: str
    commit_sha: str  # reserved, currently unused (always empty string)
    error_message: str | None  # SQL NULL for successful sessions (REQ-3.E1)
    is_transport_error: bool


def record_session(
    conn: duckdb.DuckDBPyConnection,
    record: SessionOutcomeRecord,
) -> None:
    """INSERT a session outcome row into session_outcomes with all extended fields.

    Uses the shared ``insert_session_outcome_row`` helper so the column set
    stays in sync with ``DuckDBSink.record_session_outcome``.

    Stores NULL for error_message when the session succeeded (not empty string).

    Requirements: 105-REQ-3.2, 105-REQ-3.E1
    """
    from afcore.knowledge.duckdb_sink import insert_session_outcome_row

    insert_session_outcome_row(
        conn,
        [
            record.id,
            record.spec_name,
            record.task_group,
            record.node_id,
            record.touched_path,
            record.status,
            record.input_tokens,
            record.output_tokens,
            record.duration_ms,
            record.created_at,
            record.run_id,
            record.attempt,
            record.cost,
            record.model,
            record.archetype,
            record.commit_sha,
            record.error_message,  # None -> SQL NULL (REQ-3.E1)
            record.is_transport_error,
        ],
    )


def create_run(
    conn: duckdb.DuckDBPyConnection,
    run_id: str,
    plan_hash: str,
) -> None:
    """INSERT a new run row with status='running'.

    started_at is set explicitly in Python using UTC so that it matches the
    UTC convention used by session_outcomes.created_at. DuckDB's
    CURRENT_TIMESTAMP resolves to server local time, which would produce
    cross-table timestamp skew (issue #480).

    Requirements: 105-REQ-4.2
    """
    now = datetime.now(UTC).isoformat()
    conn.execute(
        """
        INSERT INTO runs (id, plan_content_hash, status, started_at)
        VALUES (?, ?, 'running', ?)
        """,
        [run_id, plan_hash, now],
    )


def update_run_totals(
    conn: duckdb.DuckDBPyConnection,
    run_id: str,
    input_tokens: int,
    output_tokens: int,
    cost: float,
    *,
    is_workspace_setup_failure: bool = False,
) -> None:
    """UPDATE runs to accumulate token and cost counters.

    Requirements: 105-REQ-4.3
    """
    session_increment = 0 if is_workspace_setup_failure else 1
    conn.execute(
        """
        UPDATE runs
        SET total_input_tokens  = total_input_tokens  + ?,
            total_output_tokens = total_output_tokens + ?,
            total_cost          = total_cost          + ?,
            total_sessions      = total_sessions      + ?
        WHERE id = ?
        """,
        [input_tokens, output_tokens, cost, session_increment, run_id],
    )


def complete_run(
    conn: duckdb.DuckDBPyConnection,
    run_id: str,
    status: str,
) -> None:
    """UPDATE runs SET completed_at, status to mark a run as finished.

    completed_at is set explicitly in Python using UTC (issue #480).

    Requirements: 105-REQ-4.4
    """
    now = datetime.now(UTC).isoformat()
    conn.execute(
        """
        UPDATE runs
        SET completed_at = ?,
            status = ?
        WHERE id = ?
        """,
        [now, status, run_id],
    )


def cleanup_stale_runs(
    conn: duckdb.DuckDBPyConnection,
    current_run_id: str,
) -> int:
    """Mark stale running runs as stalled.

    Any run with status='running' and completed_at IS NULL whose id differs
    from *current_run_id* is considered an orphan left by a prior aborted
    start. They are updated to status='stalled' with the current
    timestamp so they no longer pollute reports.

    Returns the number of rows updated.

    Requirements: 118-REQ-6.1, 118-REQ-6.E2
    """
    count_row = conn.execute(
        """
        SELECT count(*)
        FROM runs
        WHERE status = 'running'
          AND completed_at IS NULL
          AND id != ?
        """,
        [current_run_id],
    ).fetchone()
    count = count_row[0] if count_row else 0

    if count:
        now = datetime.now(UTC).isoformat()
        conn.execute(
            """
            UPDATE runs
            SET status = 'stalled',
                completed_at = ?
            WHERE status = 'running'
              AND completed_at IS NULL
              AND id != ?
            """,
            [now, current_run_id],
        )

    return count
