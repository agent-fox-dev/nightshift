"""Unit tests for staleness check logic.

Covers issue #228: check_staleness must close issues that the AI says are
obsolete AND that GitHub confirms are still open — not issues that are already
closed externally.

Issue #53: check_staleness must return empty when fix_diff is empty, without
calling the AI.

Requirements: 71-REQ-5.1, 71-REQ-5.2, 71-REQ-5.E1, 71-REQ-5.E2, NS-REQ-1
"""

from __future__ import annotations

import pytest

# A representative non-empty diff used by normal-path tests.
# Tests that exercise the empty-diff guard use "" explicitly.
_SAMPLE_DIFF = "diff --git a/foo.py b/foo.py\n--- a/foo.py\n+++ b/foo.py\n@@ -1 +1 @@\n-old\n+new\n"

# ---------------------------------------------------------------------------
# Response parser (unchanged, just regression coverage)
# ---------------------------------------------------------------------------


class TestParseStatenessResponse:
    """_parse_staleness_response extracts obsolete issue numbers correctly."""

    def test_parses_valid_json(self) -> None:
        """Parses a clean JSON response."""
        from afcore.nightshift.staleness import _parse_staleness_response
        from afissues.protocol import IssueResult

        remaining = [
            IssueResult(number=10, title="A", html_url="", body=""),
            IssueResult(number=20, title="B", html_url="", body=""),
        ]
        response = '{"obsolete": [{"issue_number": 10, "rationale": "resolved"}]}'
        result = _parse_staleness_response(response, remaining)
        assert result.obsolete_issues == [10]
        assert result.rationale[10] == "resolved"

    def test_ignores_unknown_issue_numbers(self) -> None:
        """Issue numbers not in remaining list are silently dropped."""
        from afcore.nightshift.staleness import _parse_staleness_response
        from afissues.protocol import IssueResult

        remaining = [IssueResult(number=10, title="A", html_url="", body="")]
        response = '{"obsolete": [{"issue_number": 99, "rationale": "?"}]}'
        result = _parse_staleness_response(response, remaining)
        assert result.obsolete_issues == []


# ---------------------------------------------------------------------------
# Model tier used by _run_ai_staleness (updated for issue #26)
# ---------------------------------------------------------------------------


class TestRunAiStalenessModelTier:
    """_run_ai_staleness resolves tier via maintainer:hunt archetype identity."""

    @pytest.mark.asyncio
    async def test_uses_maintainer_hunt_default_tier(self) -> None:
        """nightshift_ai_call is invoked with the maintainer:hunt default tier (SIMPLE).

        Issue #26: staleness now resolves via resolve_model_tier(config,
        "maintainer", mode="hunt") instead of hardcoding "STANDARD".
        """
        from unittest.mock import AsyncMock, MagicMock, patch

        from afcore.core.config import ArchetypesConfig
        from afcore.nightshift.staleness import _run_ai_staleness
        from afissues.protocol import IssueResult

        fixed = IssueResult(number=1, title="Fixed", html_url="", body="")
        remaining = [IssueResult(number=2, title="Remaining", html_url="", body="")]
        config = MagicMock()
        config.archetypes = ArchetypesConfig(overrides={})

        mock_ai_call = AsyncMock(
            return_value=('{"obsolete": []}', MagicMock()),
        )

        with patch(
            "afcore.nightshift.cost_helpers.nightshift_ai_call",
            mock_ai_call,
        ):
            await _run_ai_staleness(fixed, remaining, "diff content", config)

        mock_ai_call.assert_called_once()
        _, kwargs = mock_ai_call.call_args
        assert kwargs["model_tier"] == "SIMPLE", (
            "staleness should use maintainer:hunt default tier (SIMPLE), not hardcoded STANDARD"
        )


# ---------------------------------------------------------------------------
# Issue #53: empty diff must short-circuit (fail closed)
# ---------------------------------------------------------------------------


class TestCheckStalenessEmptyDiffGuard:
    """check_staleness returns empty immediately when fix_diff is empty (issue #53).

    AC-1: Given fix_diff="", check_staleness returns obsolete_issues=[]
    regardless of what the model would return.  _run_ai_staleness is
    never invoked.
    """

    @pytest.mark.asyncio
    async def test_empty_diff_returns_empty_without_ai_call(self) -> None:
        """fix_diff="" → StalenessResult(obsolete_issues=[], rationale={}).

        _run_ai_staleness must NOT be called.
        """
        from unittest.mock import AsyncMock, MagicMock, patch

        from afcore.nightshift.staleness import StalenessResult, check_staleness
        from afissues.protocol import IssueResult

        fixed = IssueResult(number=1, title="Fixed", html_url="", body="")
        remaining = [IssueResult(number=2, title="Remaining", html_url="", body="")]

        mock_platform = AsyncMock()
        config = MagicMock()

        # The AI would return a non-empty nomination — but it should never be called.
        ai_result = StalenessResult(obsolete_issues=[2], rationale={2: "fixed by issue 1"})
        mock_ai = AsyncMock(return_value=ai_result)

        with patch("afcore.nightshift.staleness._run_ai_staleness", mock_ai):
            result = await check_staleness(fixed, remaining, "", config, mock_platform)

        assert result.obsolete_issues == []
        assert result.rationale == {}
        mock_ai.assert_not_called()

    @pytest.mark.asyncio
    async def test_whitespace_only_diff_returns_empty(self) -> None:
        """fix_diff containing only whitespace is treated as empty."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from afcore.nightshift.staleness import StalenessResult, check_staleness
        from afissues.protocol import IssueResult

        fixed = IssueResult(number=1, title="Fixed", html_url="", body="")
        remaining = [IssueResult(number=2, title="Remaining", html_url="", body="")]

        mock_platform = AsyncMock()
        config = MagicMock()

        ai_result = StalenessResult(obsolete_issues=[2], rationale={2: "fixed"})
        mock_ai = AsyncMock(return_value=ai_result)

        with patch("afcore.nightshift.staleness._run_ai_staleness", mock_ai):
            result = await check_staleness(fixed, remaining, "   \n  ", config, mock_platform)

        assert result.obsolete_issues == []
        mock_ai.assert_not_called()


# ---------------------------------------------------------------------------
# Issue #228: corrected gate logic in check_staleness
# ---------------------------------------------------------------------------


class TestCheckStalenessGateLogic:
    """check_staleness closes issues AI says are obsolete AND still open on GitHub."""

    @pytest.mark.asyncio
    async def test_ai_obsolete_and_still_open_is_returned(self) -> None:
        """Issue flagged by AI that is still open → included in obsolete list."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from afcore.nightshift.staleness import StalenessResult, check_staleness
        from afissues.protocol import IssueResult

        fixed = IssueResult(number=1, title="Fixed", html_url="", body="")
        remaining = [IssueResult(number=2, title="Remaining", html_url="", body="")]

        mock_platform = AsyncMock()
        # GitHub says issue 2 is still open
        mock_platform.list_issues_by_label = AsyncMock(
            return_value=[IssueResult(number=2, title="Remaining", html_url="", body="")]
        )

        config = MagicMock()

        # AI says issue 2 is obsolete
        ai_result = StalenessResult(obsolete_issues=[2], rationale={2: "fixed by issue 1"})

        with patch(
            "afcore.nightshift.staleness._run_ai_staleness",
            AsyncMock(return_value=ai_result),
        ):
            result = await check_staleness(fixed, remaining, _SAMPLE_DIFF, config, mock_platform)

        assert 2 in result.obsolete_issues
        assert result.rationale[2] == "fixed by issue 1"

    @pytest.mark.asyncio
    async def test_ai_obsolete_but_already_closed_is_not_returned(self) -> None:
        """Issue flagged by AI that is already closed → NOT in obsolete list.

        close_issue() should not be called on an already-closed issue.
        """
        from unittest.mock import AsyncMock, MagicMock, patch

        from afcore.nightshift.staleness import StalenessResult, check_staleness
        from afissues.protocol import IssueResult

        fixed = IssueResult(number=1, title="Fixed", html_url="", body="")
        remaining = [IssueResult(number=2, title="Remaining", html_url="", body="")]

        mock_platform = AsyncMock()
        # GitHub says issue 2 is already closed (not in open list)
        mock_platform.list_issues_by_label = AsyncMock(return_value=[])

        config = MagicMock()

        ai_result = StalenessResult(obsolete_issues=[2], rationale={2: "fixed by issue 1"})

        with patch(
            "afcore.nightshift.staleness._run_ai_staleness",
            AsyncMock(return_value=ai_result),
        ):
            result = await check_staleness(fixed, remaining, _SAMPLE_DIFF, config, mock_platform)

        assert 2 not in result.obsolete_issues

    @pytest.mark.asyncio
    async def test_ai_failure_returns_empty(self) -> None:
        """When AI call fails, no issues are closed (71-REQ-5.E1)."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from afcore.nightshift.staleness import check_staleness
        from afissues.protocol import IssueResult

        fixed = IssueResult(number=1, title="Fixed", html_url="", body="")
        remaining = [IssueResult(number=2, title="Remaining", html_url="", body="")]

        mock_platform = AsyncMock()
        # Issue 2 still open on GitHub
        mock_platform.list_issues_by_label = AsyncMock(
            return_value=[IssueResult(number=2, title="Remaining", html_url="", body="")]
        )

        config = MagicMock()

        with patch(
            "afcore.nightshift.staleness._run_ai_staleness",
            AsyncMock(side_effect=RuntimeError("AI unavailable")),
        ):
            result = await check_staleness(fixed, remaining, _SAMPLE_DIFF, config, mock_platform)

        # Without AI, we cannot know what to close
        assert result.obsolete_issues == []

    @pytest.mark.asyncio
    async def test_ai_failure_label_removed_still_open_returns_empty(self) -> None:
        """Issue #54: AI fails and issue lost af:fix label but is still open.

        The label-scoped list_issues_by_label returns [] because the label
        was removed — NOT because the issue was closed.  The old code would
        nominate this issue as obsolete; the fix must return empty.
        """
        from unittest.mock import AsyncMock, MagicMock, patch

        from afcore.nightshift.staleness import check_staleness
        from afissues.protocol import IssueResult

        fixed = IssueResult(number=1, title="Fixed", html_url="", body="")
        remaining = [IssueResult(number=2, title="Remaining", html_url="", body="")]

        mock_platform = AsyncMock()
        # Issue 2 is still open but lost its af:fix label → absent from
        # the label-scoped query.
        mock_platform.list_issues_by_label = AsyncMock(return_value=[])

        config = MagicMock()

        with patch(
            "afcore.nightshift.staleness._run_ai_staleness",
            AsyncMock(side_effect=RuntimeError("AI unavailable")),
        ):
            result = await check_staleness(fixed, remaining, _SAMPLE_DIFF, config, mock_platform)

        # Must NOT nominate issue 2 — we have no AI confirmation.
        assert result.obsolete_issues == []

    @pytest.mark.asyncio
    async def test_ai_failure_issue_genuinely_closed_returns_empty(self) -> None:
        """Issue #54: AI fails, issue genuinely closed externally → still empty.

        Even when the issue is truly closed (absent from label list for
        legitimate reasons), on AI failure we should not nominate it.
        The next successful AI cycle can handle it.
        """
        from unittest.mock import AsyncMock, MagicMock, patch

        from afcore.nightshift.staleness import check_staleness
        from afissues.protocol import IssueResult

        fixed = IssueResult(number=1, title="Fixed", html_url="", body="")
        remaining = [
            IssueResult(number=2, title="Remaining", html_url="", body=""),
            IssueResult(number=3, title="Also remaining", html_url="", body=""),
        ]

        mock_platform = AsyncMock()
        # Neither issue in the open-af:fix list (one closed, one lost label)
        mock_platform.list_issues_by_label = AsyncMock(return_value=[])

        config = MagicMock()

        with patch(
            "afcore.nightshift.staleness._run_ai_staleness",
            AsyncMock(side_effect=RuntimeError("AI unavailable")),
        ):
            result = await check_staleness(fixed, remaining, _SAMPLE_DIFF, config, mock_platform)

        # No issues nominated — AI failure means return empty.
        assert result.obsolete_issues == []

    @pytest.mark.asyncio
    async def test_github_failure_returns_empty(self) -> None:
        """When GitHub re-fetch fails, return empty (71-REQ-5.E2)."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from afcore.nightshift.staleness import StalenessResult, check_staleness
        from afissues.protocol import IssueResult

        fixed = IssueResult(number=1, title="Fixed", html_url="", body="")
        remaining = [IssueResult(number=2, title="Remaining", html_url="", body="")]

        mock_platform = AsyncMock()
        mock_platform.list_issues_by_label = AsyncMock(side_effect=RuntimeError("GitHub unavailable"))

        config = MagicMock()
        ai_result = StalenessResult(obsolete_issues=[2], rationale={2: "fixed"})

        with patch(
            "afcore.nightshift.staleness._run_ai_staleness",
            AsyncMock(return_value=ai_result),
        ):
            result = await check_staleness(fixed, remaining, _SAMPLE_DIFF, config, mock_platform)

        assert result.obsolete_issues == []
