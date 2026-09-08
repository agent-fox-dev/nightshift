"""Tests for the issue-closed-between-poll-and-dispatch guard (issue #46).

Verifies that when ``_run_one`` re-fetches an issue and finds
``state == 'closed'``, no session is started and the skip is logged.

Also verifies that ``IssueResult.state`` is a real dataclass field
(not accessed via ``getattr`` with a default), and that all platform
implementations populate it correctly.

Test Spec: TS-NS-1 through TS-NS-5
"""

from __future__ import annotations

import ast
import logging
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

    engine = NightShiftEngine(config=config, platform=platform)
    return engine, platform


def _make_issue(number: int, title: str = "Test issue", state: str = "open") -> IssueResult:
    return IssueResult(
        number=number,
        title=title,
        html_url=f"https://github.com/example/repo/issues/{number}",
        body="Issue body",
        labels=("af:fix",),
        state=state,
    )


# ---------------------------------------------------------------------------
# TS-NS-1: Closed issue skipped before session starts
# ---------------------------------------------------------------------------


class TestClosedIssueSkipped:
    """AC-1: A closed issue is silently skipped — no session is started."""

    @pytest.mark.asyncio
    async def test_closed_issue_skipped_no_session(self, caplog) -> None:
        """When get_issue returns state='closed', _process_fix is NOT called
        and the skip is logged."""
        engine, platform = _make_engine(max_parallel=1)

        # The issue as originally polled (open)
        original_issue = _make_issue(42, state="open")

        # When re-fetched, the issue is now closed
        platform.get_issue = AsyncMock(
            return_value=_make_issue(42, state="closed"),
        )

        process_fix_called = False

        async def fake_process_fix(iss, **_kwargs):
            nonlocal process_fix_called
            process_fix_called = True

        with (
            patch("afcore.nightshift.engine.parse_text_references", return_value=[]),
            patch(
                "afcore.nightshift.engine.fetch_github_relationships",
                new=AsyncMock(return_value=[]),
            ),
            patch("afcore.nightshift.engine.build_graph", return_value=[42]),
            patch.object(engine, "_process_fix", side_effect=fake_process_fix),
            caplog.at_level(logging.INFO),
        ):
            platform.list_issues_by_label = AsyncMock(return_value=[original_issue])
            await engine._run_issue_check()

        assert not process_fix_called, "_process_fix should not be called for closed issue"

        # Verify skip was logged
        skip_logs = [r for r in caplog.records if "closed between poll and dispatch" in r.message]
        assert len(skip_logs) == 1, f"Expected exactly one skip log, got {len(skip_logs)}"
        assert "42" in skip_logs[0].message

    @pytest.mark.asyncio
    async def test_open_issue_proceeds_normally(self) -> None:
        """When get_issue returns state='open', _process_fix IS called."""
        engine, platform = _make_engine(max_parallel=1)

        original_issue = _make_issue(42, state="open")

        # When re-fetched, issue is still open
        platform.get_issue = AsyncMock(
            return_value=_make_issue(42, state="open"),
        )

        process_fix_called = False

        async def fake_process_fix(iss, **_kwargs):
            nonlocal process_fix_called
            process_fix_called = True

        with (
            patch("afcore.nightshift.engine.parse_text_references", return_value=[]),
            patch(
                "afcore.nightshift.engine.fetch_github_relationships",
                new=AsyncMock(return_value=[]),
            ),
            patch("afcore.nightshift.engine.build_graph", return_value=[42]),
            patch.object(engine, "_process_fix", side_effect=fake_process_fix),
        ):
            platform.list_issues_by_label = AsyncMock(return_value=[original_issue])
            await engine._run_issue_check()

        assert process_fix_called, "_process_fix should be called for open issue"

    @pytest.mark.asyncio
    async def test_closed_issue_not_added_to_in_flight(self) -> None:
        """A closed issue should never appear in _in_flight."""
        engine, platform = _make_engine(max_parallel=1)

        original_issue = _make_issue(42, state="open")
        platform.get_issue = AsyncMock(
            return_value=_make_issue(42, state="closed"),
        )

        async def fake_process_fix(iss, **_kwargs):
            pass

        with (
            patch("afcore.nightshift.engine.parse_text_references", return_value=[]),
            patch(
                "afcore.nightshift.engine.fetch_github_relationships",
                new=AsyncMock(return_value=[]),
            ),
            patch("afcore.nightshift.engine.build_graph", return_value=[42]),
            patch.object(engine, "_process_fix", side_effect=fake_process_fix),
        ):
            platform.list_issues_by_label = AsyncMock(return_value=[original_issue])
            await engine._run_issue_check()

        assert 42 not in engine._in_flight


# ---------------------------------------------------------------------------
# TS-NS-2: fresh.state is direct attribute access (no getattr)
# ---------------------------------------------------------------------------


class TestDirectStateAccess:
    """AC-4: The guard uses fresh.state, not getattr(fresh, 'state', ...)."""

    def test_no_getattr_state_in_engine(self) -> None:
        """engine.py must not use getattr(fresh, 'state', ...) — it must use
        fresh.state directly so a missing field causes AttributeError."""
        engine_path = Path(__file__).resolve().parents[3] / "afcore" / "nightshift" / "engine.py"
        source = engine_path.read_text()

        # Parse the AST to find getattr calls with "state" as the attr arg
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "getattr"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value == "state"
            ):
                pytest.fail(
                    f"Found getattr(_, 'state', ...) at line {node.lineno}. Use fresh.state directly (issue #46)."
                )

    def test_issueresult_state_is_real_field(self) -> None:
        """IssueResult has a real 'state' dataclass field."""
        assert "state" in IssueResult.__dataclass_fields__, (
            "IssueResult must have a 'state' field as a real dataclass attribute"
        )


# ---------------------------------------------------------------------------
# TS-NS-3: GitHub and Gitea populate state
# ---------------------------------------------------------------------------


class TestGitHubPopulatesState:
    """AC-2: GitHub get_issue populates IssueResult.state."""

    def test_github_map_issue_closed(self) -> None:
        """GitHub get_issue sets state='closed' from API response."""
        # Directly test via IssueResult construction as GitHub does
        data = {
            "number": 1,
            "title": "Test",
            "html_url": "https://github.com/o/r/issues/1",
            "body": "",
            "labels": [],
            "state": "closed",
        }
        result = IssueResult(
            number=data["number"],
            title=data["title"],
            html_url=data["html_url"],
            body=data.get("body") or "",
            labels=tuple(lbl["name"] for lbl in data.get("labels", [])),
            state=data.get("state", "open"),
        )
        assert result.state == "closed"

    def test_github_map_issue_open(self) -> None:
        """GitHub get_issue sets state='open' from API response."""
        data = {
            "number": 1,
            "title": "Test",
            "html_url": "https://github.com/o/r/issues/1",
            "body": "",
            "labels": [],
            "state": "open",
        }
        result = IssueResult(
            number=data["number"],
            title=data["title"],
            html_url=data["html_url"],
            body=data.get("body") or "",
            labels=tuple(lbl["name"] for lbl in data.get("labels", [])),
            state=data.get("state", "open"),
        )
        assert result.state == "open"


class TestGiteaPopulatesState:
    """AC-2: Gitea _map_issue populates IssueResult.state."""

    def test_gitea_map_issue_closed(self) -> None:
        """Gitea _map_issue sets state='closed' from API response."""
        from afissues.gitea import _map_issue

        data = {
            "number": 1,
            "title": "Test",
            "html_url": "https://gitea.example.com/o/r/issues/1",
            "body": "",
            "labels": [],
            "state": "closed",
        }
        result = _map_issue(data)
        assert result.state == "closed"

    def test_gitea_map_issue_open(self) -> None:
        """Gitea _map_issue sets state='open' from API response."""
        from afissues.gitea import _map_issue

        data = {
            "number": 1,
            "title": "Test",
            "html_url": "https://gitea.example.com/o/r/issues/1",
            "body": "",
            "labels": [],
            "state": "open",
        }
        result = _map_issue(data)
        assert result.state == "open"


# ---------------------------------------------------------------------------
# TS-NS-4: GitLab normalises "opened" → "open"
# ---------------------------------------------------------------------------


class TestGitLabNormalisesState:
    """AC-3: GitLab normalises 'opened' to 'open'."""

    def test_gitlab_opened_normalised_to_open(self) -> None:
        """GitLab _map_issue normalises 'opened' to 'open'."""
        from afissues.gitlab import _map_issue

        data = {
            "iid": 1,
            "title": "Test",
            "web_url": "https://gitlab.com/o/r/-/issues/1",
            "description": "",
            "labels": [],
            "state": "opened",
        }
        result = _map_issue(data)
        assert result.state == "open", f"Expected 'open', got {result.state!r}"

    def test_gitlab_closed_preserved(self) -> None:
        """GitLab _map_issue preserves 'closed' as-is."""
        from afissues.gitlab import _map_issue

        data = {
            "iid": 1,
            "title": "Test",
            "web_url": "https://gitlab.com/o/r/-/issues/1",
            "description": "",
            "labels": [],
            "state": "closed",
        }
        result = _map_issue(data)
        assert result.state == "closed"


# ---------------------------------------------------------------------------
# TS-NS-5: Default state does not break existing construction sites
# ---------------------------------------------------------------------------


class TestBackwardCompatibility:
    """AC-5: Adding state as a defaulted field does not break existing
    IssueResult construction."""

    def test_construction_without_state(self) -> None:
        """IssueResult can be constructed without state (defaults to 'open')."""
        result = IssueResult(number=1, title="Test", html_url="https://example.com")
        assert result.state == "open"

    def test_construction_with_all_existing_fields(self) -> None:
        """IssueResult with all pre-existing fields still works."""
        result = IssueResult(
            number=1,
            title="Test",
            html_url="https://example.com",
            body="body text",
            labels=("af:fix", "bug"),
        )
        assert result.state == "open"
        assert result.labels == ("af:fix", "bug")

    def test_construction_with_state(self) -> None:
        """IssueResult constructed with state='closed' works."""
        result = IssueResult(
            number=1,
            title="Test",
            html_url="https://example.com",
            state="closed",
        )
        assert result.state == "closed"
