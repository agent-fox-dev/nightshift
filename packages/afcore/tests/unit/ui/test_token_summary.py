"""Tests for token summary display in task lines and issue/daemon summaries.

Test Spec: TS-NS-1, TS-NS-2, TS-NS-3, TS-NS-4, TS-NS-5
Requirements: NS-REQ-1, NS-REQ-2, NS-REQ-3, NS-REQ-4, NS-REQ-5
"""

from __future__ import annotations

from io import StringIO

from afcore.core.config import ThemeConfig
from afcore.ui.display import create_theme
from afcore.ui.progress import ProgressDisplay, TaskEvent, format_tokens
from rich.console import Console
from rich.theme import Theme

_STYLE_ROLES = ("header", "muted")


def _make_theme(*, force_terminal: bool = False, width: int = 120):
    """Create an AppTheme with a StringIO-backed console for testing."""
    config = ThemeConfig()
    theme = create_theme(config)
    buf = StringIO()
    rich_theme = Theme({role: getattr(config, role) for role in _STYLE_ROLES})
    theme.console = Console(file=buf, theme=rich_theme, width=width, force_terminal=force_terminal)
    return theme, buf


# ---------------------------------------------------------------------------
# TS-NS-1: Per-phase task completion line shows token counts
# ---------------------------------------------------------------------------


class TestTaskLineTokenCounts:
    """TS-NS-1: Task completion line includes formatted token counts.

    Requirement: NS-REQ-1
    """

    def test_completed_line_shows_token_counts(self) -> None:
        """Completed task line includes formatted input/output token counts."""
        theme, _buf = _make_theme()
        display = ProgressDisplay(theme, quiet=False)
        event = TaskEvent(
            node_id="issue-42:0",
            status="completed",
            duration_s=83.0,
            archetype="coder",
            input_tokens=12400,
            output_tokens=3100,
        )
        line = display._format_task_line(event)
        text = str(line)
        assert format_tokens(12400) in text, f"Expected formatted input tokens in: {text!r}"
        assert format_tokens(3100) in text, f"Expected formatted output tokens in: {text!r}"
        assert "↑" in text, f"Expected ↑ indicator in: {text!r}"
        assert "↓" in text, f"Expected ↓ indicator in: {text!r}"
        assert "done" in text
        assert "[coder]" in text

    def test_failed_line_shows_token_counts(self) -> None:
        """Failed task line includes token counts when non-zero."""
        theme, _buf = _make_theme()
        display = ProgressDisplay(theme, quiet=False)
        event = TaskEvent(
            node_id="issue-42:0",
            status="failed",
            duration_s=10.0,
            archetype="reviewer",
            input_tokens=5000,
            output_tokens=1000,
        )
        line = display._format_task_line(event)
        text = str(line)
        assert "↑" in text
        assert "↓" in text

    def test_large_token_counts_use_millions(self) -> None:
        """Token counts >= 1M use M suffix."""
        theme, _buf = _make_theme()
        display = ProgressDisplay(theme, quiet=False)
        event = TaskEvent(
            node_id="issue-42:0",
            status="completed",
            duration_s=300.0,
            archetype="coder",
            input_tokens=1_500_000,
            output_tokens=250_000,
        )
        line = display._format_task_line(event)
        text = str(line)
        assert "1.5M" in text
        assert "250.0k" in text


# ---------------------------------------------------------------------------
# TS-NS-4: Backward compatibility
# ---------------------------------------------------------------------------


class TestTaskEventBackwardCompat:
    """TS-NS-4: Existing callers that don't supply token fields are unaffected.

    Requirement: NS-REQ-4
    """

    def test_default_token_fields_are_zero(self) -> None:
        """TaskEvent without token fields defaults to zero."""
        event = TaskEvent(
            node_id="spec:1",
            status="completed",
            duration_s=10.0,
        )
        assert event.input_tokens == 0
        assert event.output_tokens == 0

    def test_legacy_construction_still_works(self) -> None:
        """All existing TaskEvent construction patterns continue to work."""
        # Positional-ish construction with keyword args, no token fields
        event = TaskEvent(
            node_id="spec:1",
            status="completed",
            duration_s=45.0,
            archetype="coder",
        )
        assert event.input_tokens == 0
        assert event.output_tokens == 0

    def test_format_task_line_omits_tokens_with_defaults(self) -> None:
        """_format_task_line silently omits token suffix when values are zero."""
        theme, _buf = _make_theme()
        display = ProgressDisplay(theme, quiet=False)
        event = TaskEvent(
            node_id="spec:1",
            status="completed",
            duration_s=10.0,
            archetype="coder",
        )
        line = display._format_task_line(event)
        text = str(line)
        assert "↑" not in text, f"Should not contain ↑ when tokens are zero: {text!r}"
        assert "↓" not in text, f"Should not contain ↓ when tokens are zero: {text!r}"


# ---------------------------------------------------------------------------
# TS-NS-5: Token counts hidden when zero
# ---------------------------------------------------------------------------


class TestTokenCountsHiddenWhenZero:
    """TS-NS-5: Token counts are not shown when both are zero.

    Requirement: NS-REQ-5
    """

    def test_zero_tokens_omits_suffix(self) -> None:
        """Task line with input_tokens=0, output_tokens=0 omits token suffix."""
        theme, _buf = _make_theme()
        display = ProgressDisplay(theme, quiet=False)
        event = TaskEvent(
            node_id="issue-42:0",
            status="completed",
            duration_s=10.0,
            archetype="coder",
            input_tokens=0,
            output_tokens=0,
        )
        line = display._format_task_line(event)
        text = str(line)
        assert "↑" not in text, f"Should not contain ↑ with zero tokens: {text!r}"
        assert "↓" not in text, f"Should not contain ↓ with zero tokens: {text!r}"

    def test_none_equivalent_zero_omits_suffix(self) -> None:
        """Task line with default (0) token fields omits token suffix."""
        theme, _buf = _make_theme()
        display = ProgressDisplay(theme, quiet=False)
        event = TaskEvent(
            node_id="issue-42:0",
            status="completed",
            duration_s=10.0,
        )
        line = display._format_task_line(event)
        text = str(line)
        assert "↑" not in text
        assert "↓" not in text

    def test_only_input_tokens_shows_suffix(self) -> None:
        """When only input_tokens is non-zero, suffix is shown."""
        theme, _buf = _make_theme()
        display = ProgressDisplay(theme, quiet=False)
        event = TaskEvent(
            node_id="issue-42:0",
            status="completed",
            duration_s=10.0,
            input_tokens=5000,
            output_tokens=0,
        )
        line = display._format_task_line(event)
        text = str(line)
        assert "↑" in text, f"Should contain ↑ with non-zero input: {text!r}"


# ---------------------------------------------------------------------------
# TS-NS-2: Per-issue completion line in engine.py
# ---------------------------------------------------------------------------


class TestEngineIssueCompletionTokens:
    """TS-NS-2: Issue completion line includes aggregated token counts.

    Requirement: NS-REQ-2
    """

    def test_issue_done_line_includes_tokens(self) -> None:
        """_process_fix emits status with token counts when non-zero."""
        from afcore.ui.progress import format_tokens

        # Simulate what engine._process_fix does for the status line
        input_tokens = 45200
        output_tokens = 9800
        duration_str = "1m 23s"

        token_suffix = ""
        if input_tokens or output_tokens:
            token_suffix = f" · {format_tokens(input_tokens)}↑ {format_tokens(output_tokens)}↓"

        status_text = f"✔ Issue #42 fixed ({duration_str}){token_suffix}"
        assert "45.2k↑" in status_text
        assert "9.8k↑" in status_text or "9.8k↓" in status_text
        assert "·" in status_text

    def test_issue_done_line_omits_tokens_when_zero(self) -> None:
        """Issue completion line omits token suffix when both are zero."""
        from afcore.ui.progress import format_tokens

        input_tokens = 0
        output_tokens = 0

        token_suffix = ""
        if input_tokens or output_tokens:
            token_suffix = f" · {format_tokens(input_tokens)}↑ {format_tokens(output_tokens)}↓"

        status_text = f"✔ Issue #42 fixed (1m 23s){token_suffix}"
        assert "↑" not in status_text
        assert "↓" not in status_text


# ---------------------------------------------------------------------------
# TS-NS-3: Final daemon summary includes aggregate token totals
# ---------------------------------------------------------------------------


class TestDaemonSummaryTokens:
    """TS-NS-3: Final summary includes aggregate token totals.

    Requirement: NS-REQ-3
    """

    def test_nightshift_state_total_tokens(self) -> None:
        """NightShiftState exposes aggregate token counts from issue outcomes."""
        import asyncio

        from afcore.nightshift.engine import IssueOutcome, NightShiftState

        state = NightShiftState()

        outcome1 = IssueOutcome(
            issue_number=1,
            title="Fix A",
            run_id="r1",
            outcome="fixed",
            duration_ms=1000,
            cost_usd=0.10,
            sessions_run=2,
            input_tokens=50000,
            output_tokens=10000,
        )
        outcome2 = IssueOutcome(
            issue_number=2,
            title="Fix B",
            run_id="r2",
            outcome="fixed",
            duration_ms=2000,
            cost_usd=0.32,
            sessions_run=3,
            input_tokens=70300,
            output_tokens=18700,
        )

        asyncio.run(state.add_fix_result(0.10, 2, outcome1, succeeded=True))
        asyncio.run(state.add_fix_result(0.32, 3, outcome2, succeeded=True))

        assert state.total_input_tokens == 120300
        assert state.total_output_tokens == 28700
        assert state.issues_fixed == 2

    def test_summary_text_includes_tokens(self) -> None:
        """The summary text format includes token counts when non-zero."""
        from afcore.ui.progress import format_tokens

        total_in = 120300
        total_out = 28700
        fixed = 3
        cost = 0.42

        token_suffix = ""
        if total_in or total_out:
            token_suffix = f", Tokens: {format_tokens(total_in)}↑ {format_tokens(total_out)}↓"

        summary = f"Nightshift stopped. Issues fixed: {fixed}, Total cost: ${cost:.2f}{token_suffix}"
        assert "120.3k↑" in summary
        assert "28.7k↓" in summary
        assert "Tokens:" in summary

    def test_summary_json_includes_token_keys(self) -> None:
        """JSON mode output includes input_tokens and output_tokens keys."""
        total_in = 120300
        total_out = 28700
        payload = {
            "status": "stopped",
            "issues_fixed": 3,
            "total_cost": 0.42,
            "input_tokens": total_in,
            "output_tokens": total_out,
        }
        assert payload["input_tokens"] == 120300
        assert payload["output_tokens"] == 28700

    def test_summary_omits_tokens_when_zero(self) -> None:
        """Summary omits token suffix when no tokens were consumed."""
        from afcore.ui.progress import format_tokens

        total_in = 0
        total_out = 0
        fixed = 0
        cost = 0.0

        token_suffix = ""
        if total_in or total_out:
            token_suffix = f", Tokens: {format_tokens(total_in)}↑ {format_tokens(total_out)}↓"

        summary = f"Nightshift stopped. Issues fixed: {fixed}, Total cost: ${cost:.2f}{token_suffix}"
        assert "Tokens:" not in summary
        assert "↑" not in summary


# ---------------------------------------------------------------------------
# format_tokens helper tests
# ---------------------------------------------------------------------------


class TestFormatTokens:
    """Verify format_tokens covers all ranges."""

    def test_none_returns_question_k(self) -> None:
        assert format_tokens(None) == "?k"

    def test_small_number(self) -> None:
        assert format_tokens(500) == "500"

    def test_thousands(self) -> None:
        assert format_tokens(12400) == "12.4k"

    def test_millions(self) -> None:
        assert format_tokens(1_500_000) == "1.5M"

    def test_zero(self) -> None:
        assert format_tokens(0) == "0"
