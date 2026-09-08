"""Unit tests for agent-fox logging configuration.

Covers setup_logging() verbosity tiers.

Requirements: 01-REQ-6.1, 01-REQ-6.2, 01-REQ-6.3, 01-REQ-6.E1
"""

from __future__ import annotations

import logging

import pytest

# ---------------------------------------------------------------------------
# setup_logging() verbosity tiers
# ---------------------------------------------------------------------------


class TestSetupLoggingTiers:
    """setup_logging() sets the correct level for each verbosity tier."""

    def _get_agent_fox_level(self) -> int:
        return logging.getLogger("afcore").level

    def test_default_level_is_warning(self) -> None:
        """No flags → WARNING level."""
        from afcore.core.logging import setup_logging

        setup_logging(verbose=False, quiet=False)
        assert self._get_agent_fox_level() == logging.WARNING

    def test_verbose_sets_debug(self) -> None:
        """--verbose → DEBUG level."""
        from afcore.core.logging import setup_logging

        setup_logging(verbose=True, quiet=False)
        assert self._get_agent_fox_level() == logging.DEBUG

    def test_quiet_sets_error(self) -> None:
        """--quiet → ERROR level."""
        from afcore.core.logging import setup_logging

        setup_logging(verbose=False, quiet=True)
        assert self._get_agent_fox_level() == logging.ERROR

    def test_verbose_wins_over_quiet(self) -> None:
        """--verbose --quiet → DEBUG level (01-REQ-6.E1: most info wins)."""
        from afcore.core.logging import setup_logging

        setup_logging(verbose=True, quiet=True)
        assert self._get_agent_fox_level() == logging.DEBUG

    def test_setup_logging_rejects_trace_kwarg(self) -> None:
        """setup_logging(trace=True) raises TypeError — trace param removed."""
        from afcore.core.logging import setup_logging

        with pytest.raises(TypeError):
            setup_logging(trace=True)  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# TRACE constant removed
# ---------------------------------------------------------------------------


class TestTraceConstantRemoved:
    """TRACE constant no longer exported from afcore.core.logging."""

    def test_trace_import_raises_import_error(self) -> None:
        """from afcore.core.logging import TRACE must raise ImportError."""
        with pytest.raises(ImportError):
            from afcore.core.logging import TRACE  # noqa: F401

    def test_level_5_not_named_trace(self) -> None:
        """logging.getLevelName(5) must NOT return 'TRACE' after module import."""
        import afcore.core.logging  # noqa: F401

        name = logging.getLevelName(5)
        assert name != "TRACE", f"Expected level 5 not to be 'TRACE', got {name!r}"


class TestLiveConsoleMarkupDisabled:
    """Bracketed TOML table names must survive the Rich Live console.

    ``console.print()`` parses markup by default, so a message naming
    ``[gate]`` or ``[models.tier_defaults]`` had the bracketed text deleted
    from the output — exactly the part telling the user what to configure.
    ``highlight=False`` does not disable markup; ``markup=False`` does.
    """

    @staticmethod
    def _render(message: str) -> str:
        import io

        from afcore.core.logging import LiveAwareHandler
        from rich.console import Console

        buffer = io.StringIO()
        console = Console(file=buffer, force_terminal=False, width=200, soft_wrap=True)

        handler = LiveAwareHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        handler.set_live_console(console)
        handler.emit(
            logging.LogRecord(
                name="afcore.test",
                level=logging.WARNING,
                pathname=__file__,
                lineno=1,
                msg=message,
                args=(),
                exc_info=None,
            )
        )
        return buffer.getvalue()

    def test_gate_table_name_survives(self) -> None:
        """The [gate] hint is what the warning exists to deliver."""
        output = self._render("Configure [gate] command in config.toml")

        assert "[gate]" in output

    def test_models_table_name_survives(self) -> None:
        """The inaccessible-model error prints a [models.tier_defaults] block."""
        output = self._render('  [models.tier_defaults]\n  SIMPLE = "claude-sonnet-5"')

        assert "[models.tier_defaults]" in output

    def test_bracketed_text_is_not_treated_as_style(self) -> None:
        """A bracket sequence that is not a valid style must not raise."""
        output = self._render("ignored entirely, including any [models] overrides")

        assert "[models]" in output
