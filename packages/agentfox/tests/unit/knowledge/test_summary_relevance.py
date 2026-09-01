"""Unit tests for relevance-based summary ranking in FoxKnowledgeProvider.

Test Spec: TS-NS-1 through TS-NS-5
Requirements: NS-REQ-1, NS-REQ-2, NS-REQ-3, NS-REQ-4, NS-REQ-5

Covers relevance filtering of session summary injection based on
file-footprint overlap between the current task group and prior groups.
"""

from __future__ import annotations

import uuid

import duckdb
import pytest
from agentfox.knowledge.migrations import run_migrations
from agentfox.knowledge.summary_store import (
    SummaryRecord,
    insert_summary,
)

_SESSION_SUMMARIES_DDL = """
CREATE TABLE IF NOT EXISTS session_summaries (
    id          UUID PRIMARY KEY,
    node_id     VARCHAR NOT NULL,
    run_id      VARCHAR NOT NULL,
    spec_name   VARCHAR NOT NULL,
    task_group  VARCHAR NOT NULL,
    archetype   VARCHAR NOT NULL,
    attempt     INTEGER NOT NULL DEFAULT 1,
    summary     TEXT NOT NULL,
    created_at  TIMESTAMP NOT NULL
);
"""


@pytest.fixture()
def provider_conn():
    conn = duckdb.connect(":memory:")
    run_migrations(conn)
    conn.execute(_SESSION_SUMMARIES_DDL)
    yield conn
    conn.close()


@pytest.fixture()
def provider_db(provider_conn):
    from agentfox.knowledge.db import KnowledgeDB

    db = KnowledgeDB.__new__(KnowledgeDB)
    db._conn = provider_conn
    return db


def _make_record(
    *,
    id=None,
    node_id="spec_a:1",
    run_id="run-1",
    spec_name="spec_a",
    task_group="1",
    archetype="coder",
    attempt=1,
    summary="Summary for group",
    created_at="2026-04-28T18:00:00",
):
    return SummaryRecord(
        id=id or str(uuid.uuid4()),
        node_id=node_id,
        run_id=run_id,
        spec_name=spec_name,
        task_group=task_group,
        archetype=archetype,
        attempt=attempt,
        summary=summary,
        created_at=created_at,
    )


def _make_provider(provider_db, run_id=None, spec_dir=None, max_summary_items=20):
    from agentfox.core.config import KnowledgeProviderConfig
    from agentfox.knowledge.fox_provider import FoxKnowledgeProvider

    config = KnowledgeProviderConfig(max_summary_items=max_summary_items)
    provider = FoxKnowledgeProvider(provider_db, config)
    if run_id is not None:
        provider._run_id = run_id
    if spec_dir is not None:
        provider.set_spec_dir(spec_dir)
    return provider


def _insert_groups(conn, groups, *, spec_name="spec_a", run_id="run-1"):
    """Insert one coder summary for each group number in *groups*."""
    for g in groups:
        insert_summary(
            conn,
            _make_record(
                spec_name=spec_name,
                run_id=run_id,
                task_group=str(g),
                node_id=f"{spec_name}:{g}",
                summary=f"Summary for group {g}",
            ),
        )


def _extract_group_numbers(items):
    """Extract group numbers from [CONTEXT] formatted items."""
    import re

    groups = []
    for item in items:
        m = re.search(r"group (\d+)", item)
        if m:
            groups.append(int(m.group(1)))
    return groups


# TS-NS-4: Fallback to original ordering when file_footprint is None/empty
class TestFallbackOrdering:
    """NS-REQ-4: Falls back to ascending task-group order when no footprint."""

    def test_none_footprint_preserves_ascending_order(self, provider_db, provider_conn):
        """file_footprint=None -> ascending group order (1, 2, 3)."""
        _insert_groups(provider_conn, [1, 2, 3])

        provider = _make_provider(provider_db, run_id="run-1")

        items = provider.retrieve(
            "spec_a",
            "task description",
            task_group="4",
            file_footprint=None,
        )

        context_items = [i for i in items if i.startswith("[CONTEXT]")]
        groups = _extract_group_numbers(context_items)

        assert groups == [1, 2, 3], f"Expected ascending order [1, 2, 3] with None footprint; got {groups}"

    def test_empty_footprint_preserves_ascending_order(self, provider_db, provider_conn):
        """file_footprint=[] -> ascending group order (1, 2, 3)."""
        _insert_groups(provider_conn, [1, 2, 3])

        provider = _make_provider(provider_db, run_id="run-1")

        items = provider.retrieve(
            "spec_a",
            "task description",
            task_group="4",
            file_footprint=[],
        )

        context_items = [i for i in items if i.startswith("[CONTEXT]")]
        groups = _extract_group_numbers(context_items)

        assert groups == [1, 2, 3], f"Expected ascending order [1, 2, 3] with empty footprint; got {groups}"
