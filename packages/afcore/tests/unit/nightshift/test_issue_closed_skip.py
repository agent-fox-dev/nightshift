"""Tests for the issue-closed-between-poll-and-dispatch guard (issue #46).

Verifies that when ``_run_one`` re-fetches an issue and finds
``state == 'closed'``, no session is started and the skip is logged.

The engine uses ``getattr(fresh, 'state', 'open')`` so the guard works
whether or not ``IssueResult`` has a ``state`` field.  Platform tests
are marked ``xfail`` until the upstream ``afissues`` package adds the
``state`` field to ``IssueResult``.

Test Spec: TS-NS-1 through TS-NS-5
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from afissues.protocol import IssueResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_issue(number: int, title: str = "Test issue", state: str = "open") -> SimpleNamespace:
    """Return an issue-like object with a ``state`` attribute.

    Uses ``SimpleNamespace`` rather than ``IssueResult`` because the upstream
    dataclass does not yet have a ``state`` field.
    """
    return SimpleNamespace(
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
        from afcore.nightshift.engine import NightShiftEngine

        config = MagicMock()
        config.orchestrator.max_cost = None
        config.orchestrator.max_sessions = None
        config.night_shift.similarity_threshold = 0.85
        config.night_shift.max_parallel = 1

        platform = AsyncMock()
        platform.list_issues_by_label = AsyncMock(return_value=[])
        engine = NightShiftEngine(config=config, platform=platform)

        original_issue = _make_issue(42, state="open")

        platform.get_issue = AsyncMock(
            return_value=_make_issue(42, state="closed"),
        )

        process_fix_called = False

        async def fake_process_fix(iss, **_kwargs):
            nonlocal process_fix_called
            process_fix_called = True

        with (
            patch("afcore.nightshift.engine.parse_text_references", return_value=[]),
            patch("afcore.nightshift.engine.build_graph", return_value=[42]),
            patch.object(engine, "_process_fix", side_effect=fake_process_fix),
            caplog.at_level(logging.INFO),
        ):
            platform.list_issues_by_label = AsyncMock(return_value=[original_issue])
            await engine._run_issue_check()

        assert not process_fix_called, "_process_fix should not be called for closed issue"

        skip_logs = [r for r in caplog.records if "closed between poll and dispatch" in r.message]
        assert len(skip_logs) == 1, f"Expected exactly one skip log, got {len(skip_logs)}"
        assert "42" in skip_logs[0].message

    @pytest.mark.asyncio
    async def test_open_issue_proceeds_normally(self) -> None:
        """When get_issue returns state='open', _process_fix IS called."""
        from afcore.nightshift.engine import NightShiftEngine

        config = MagicMock()
        config.orchestrator.max_cost = None
        config.orchestrator.max_sessions = None
        config.night_shift.similarity_threshold = 0.85
        config.night_shift.max_parallel = 1

        platform = AsyncMock()
        platform.list_issues_by_label = AsyncMock(return_value=[])
        engine = NightShiftEngine(config=config, platform=platform)

        original_issue = _make_issue(42, state="open")

        platform.get_issue = AsyncMock(
            return_value=_make_issue(42, state="open"),
        )

        process_fix_called = False

        async def fake_process_fix(iss, **_kwargs):
            nonlocal process_fix_called
            process_fix_called = True

        with (
            patch("afcore.nightshift.engine.parse_text_references", return_value=[]),
            patch("afcore.nightshift.engine.build_graph", return_value=[42]),
            patch.object(engine, "_process_fix", side_effect=fake_process_fix),
        ):
            platform.list_issues_by_label = AsyncMock(return_value=[original_issue])
            await engine._run_issue_check()

        assert process_fix_called, "_process_fix should be called for open issue"

    @pytest.mark.asyncio
    async def test_closed_issue_not_added_to_in_flight(self) -> None:
        """A closed issue should never appear in _in_flight."""
        from afcore.nightshift.engine import NightShiftEngine

        config = MagicMock()
        config.orchestrator.max_cost = None
        config.orchestrator.max_sessions = None
        config.night_shift.similarity_threshold = 0.85
        config.night_shift.max_parallel = 1

        platform = AsyncMock()
        platform.list_issues_by_label = AsyncMock(return_value=[])
        engine = NightShiftEngine(config=config, platform=platform)

        original_issue = _make_issue(42, state="open")
        platform.get_issue = AsyncMock(
            return_value=_make_issue(42, state="closed"),
        )

        async def fake_process_fix(iss, **_kwargs):
            pass

        with (
            patch("afcore.nightshift.engine.parse_text_references", return_value=[]),
            patch("afcore.nightshift.engine.build_graph", return_value=[42]),
            patch.object(engine, "_process_fix", side_effect=fake_process_fix),
        ):
            platform.list_issues_by_label = AsyncMock(return_value=[original_issue])
            await engine._run_issue_check()

        assert 42 not in engine._in_flight


# ---------------------------------------------------------------------------
# TS-NS-2: engine uses getattr for forward-compatible state access
# ---------------------------------------------------------------------------


class TestForwardCompatibleStateAccess:
    """AC-4: The guard uses getattr(fresh, 'state', 'open') so it works
    whether or not IssueResult has a state field."""

    def test_getattr_returns_open_without_state_field(self) -> None:
        """An IssueResult without a state field defaults to 'open'."""
        issue = IssueResult(number=1, title="Test", html_url="https://example.com")
        assert getattr(issue, "state", "open") == "open"

    def test_getattr_returns_state_when_present(self) -> None:
        """An object with a state attribute returns that value."""
        issue = _make_issue(1, state="closed")
        assert getattr(issue, "state", "open") == "closed"


# ---------------------------------------------------------------------------
# TS-NS-3: Platform state population (xfail until upstream adds state)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    reason="afissues.IssueResult does not yet have a 'state' field (upstream issue)",
    strict=True,
)
class TestGitHubPopulatesState:
    """AC-2: GitHub get_issue populates IssueResult.state."""

    def test_github_map_issue_closed(self) -> None:
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


@pytest.mark.xfail(
    reason="afissues.IssueResult does not yet have a 'state' field (upstream issue)",
    strict=True,
)
class TestGiteaPopulatesState:
    """AC-2: Gitea _map_issue populates IssueResult.state."""

    def test_gitea_map_issue_closed(self) -> None:
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
# TS-NS-4: GitLab normalises "opened" → "open" (xfail until upstream)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    reason="afissues.IssueResult does not yet have a 'state' field (upstream issue)",
    strict=True,
)
class TestGitLabNormalisesState:
    """AC-3: GitLab normalises 'opened' to 'open'."""

    def test_gitlab_opened_normalised_to_open(self) -> None:
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
        assert result.state == "open"

    def test_gitlab_closed_preserved(self) -> None:
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
# TS-NS-5: Backward compatibility (xfail until upstream)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    reason="afissues.IssueResult does not yet have a 'state' field (upstream issue)",
    strict=True,
)
class TestBackwardCompatibility:
    """AC-5: Adding state as a defaulted field does not break existing
    IssueResult construction."""

    def test_construction_without_state(self) -> None:
        result = IssueResult(number=1, title="Test", html_url="https://example.com")
        assert result.state == "open"

    def test_construction_with_all_existing_fields(self) -> None:
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
        result = IssueResult(
            number=1,
            title="Test",
            html_url="https://example.com",
            state="closed",
        )
        assert result.state == "closed"
