"""Unit tests for summary ordering in FoxKnowledgeProvider.

Test Spec: TS-NS-1, TS-NS-2, TS-NS-4, TS-NS-5
Requirements: NS-REQ-1, NS-REQ-2, NS-REQ-4, NS-REQ-5

Covers:
- TS-NS-1 / NS-REQ-1: Relevance branch removed (delete option); verified
  by TestRelevanceBranchRemoved.
- TS-NS-2 / NS-REQ-2: ``_spec_dir`` / ``set_spec_dir`` removed; verified
  by TestSpecDirRemoved.
- TS-NS-4 / NS-REQ-4: Fallback to ascending task-group order when no
  footprint; verified by TestFallbackOrdering.
- TS-NS-5 / NS-REQ-5: ``query_limit = 1000`` removed with relevance
  branch; verified by TestRelevanceBranchRemoved.
"""

from __future__ import annotations

import inspect
import uuid

import duckdb
import pytest
from afcore.knowledge.fox_provider import FoxKnowledgeProvider
from afcore.knowledge.migrations import run_migrations
from afcore.knowledge.summary_store import (
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
    from afcore.knowledge.db import KnowledgeDB

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


def _make_provider(provider_db, run_id=None, max_summary_items=20):
    from afcore.core.config import KnowledgeProviderConfig
    from afcore.knowledge.fox_provider import FoxKnowledgeProvider

    config = KnowledgeProviderConfig(max_summary_items=max_summary_items)
    provider = FoxKnowledgeProvider(provider_db, config)
    if run_id is not None:
        provider._run_id = run_id
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

    def test_nonempty_footprint_preserves_ascending_order(self, provider_db, provider_conn):
        """file_footprint with values still uses ascending order (relevance branch removed)."""
        _insert_groups(provider_conn, [1, 2, 3])

        provider = _make_provider(provider_db, run_id="run-1")

        items = provider.retrieve(
            "spec_a",
            "task description",
            task_group="4",
            file_footprint=["src/main.py", "src/utils.py"],
        )

        context_items = [i for i in items if i.startswith("[CONTEXT]")]
        groups = _extract_group_numbers(context_items)

        assert groups == [1, 2, 3], f"Expected ascending order [1, 2, 3] with non-empty footprint; got {groups}"


# TS-NS-1 / TS-NS-5: Relevance branch removed (delete option)
class TestRelevanceBranchRemoved:
    """NS-REQ-1, NS-REQ-5: Dead relevance branch and query_limit=1000 removed."""

    def test_group_impacts_not_in_source(self):
        """group_impacts variable no longer exists in _query_same_spec_summaries source."""
        source = inspect.getsource(FoxKnowledgeProvider._query_same_spec_summaries)
        assert "group_impacts" not in source, "group_impacts should have been removed with the relevance branch"

    def test_query_limit_1000_not_in_source(self):
        """query_limit = 1000 no longer exists in _query_same_spec_summaries source."""
        source = inspect.getsource(FoxKnowledgeProvider._query_same_spec_summaries)
        assert "1000" not in source, "query_limit = 1000 should have been removed with the relevance branch"


# TS-NS-2: _spec_dir / set_spec_dir removed
class TestSpecDirRemoved:
    """NS-REQ-2: _spec_dir and set_spec_dir deleted from FoxKnowledgeProvider."""

    def test_no_spec_dir_attribute(self):
        """FoxKnowledgeProvider has no _spec_dir attribute after deletion."""
        assert not hasattr(FoxKnowledgeProvider, "set_spec_dir"), "set_spec_dir method should have been removed"

    def test_no_spec_dir_in_source(self):
        """_spec_dir does not appear in FoxKnowledgeProvider source."""
        source = inspect.getsource(FoxKnowledgeProvider)
        assert "_spec_dir" not in source, "_spec_dir should have been removed from FoxKnowledgeProvider"
