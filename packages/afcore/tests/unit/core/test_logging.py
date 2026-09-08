"""Unit tests for agent-fox logging configuration.

Covers setup_logging() verbosity tiers and multi-package logger wiring.

Requirements: 01-REQ-6.1, 01-REQ-6.2, 01-REQ-6.3, 01-REQ-6.E1
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest
from afcore.core.logging import LiveAwareHandler

# ---------------------------------------------------------------------------
# Fixture: reset logging state to prevent cross-test contamination
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_logging_state():
    """Snapshot and restore logger state touched by setup_logging.

    setup_logging() mutates global logger objects (level, handlers) and the
    module-level _live_handler singleton.  Without cleanup, tests that call
    it leak configuration into later tests — especially harmful under
    pytest-xdist where unrelated tests share a worker process.
    """
    import afcore.core.logging as _mod

    original_handler = _mod._live_handler
    pkg_snapshots: dict[str, tuple[int, list[logging.Handler]]] = {}
    for pkg in _mod._CONFIGURED_PACKAGES:
        lgr = logging.getLogger(pkg)
        pkg_snapshots[pkg] = (lgr.level, list(lgr.handlers))

    yield

    _mod._live_handler = original_handler
    for pkg, (lvl, handlers) in pkg_snapshots.items():
        lgr = logging.getLogger(pkg)
        lgr.setLevel(lvl)
        lgr.handlers = handlers


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


# ---------------------------------------------------------------------------
# Multi-package logger configuration (issue #59)
# ---------------------------------------------------------------------------


class TestNightshiftLoggerConfiguration:
    """setup_logging() must configure nightshift.* loggers identically to afcore.*."""

    def test_verbose_enables_debug_on_nightshift(self) -> None:
        """AC-1: verbose=True → nightshift.app effective level is DEBUG."""
        from afcore.core.logging import setup_logging

        setup_logging(verbose=True)
        assert logging.getLogger("nightshift.app").getEffectiveLevel() == logging.DEBUG

    def test_quiet_silences_nightshift_warnings(self) -> None:
        """AC-2: quiet=True → nightshift.app WARNING is suppressed."""
        from afcore.core.logging import setup_logging

        setup_logging(quiet=True)
        ns_logger = logging.getLogger("nightshift.app")
        assert not ns_logger.isEnabledFor(logging.WARNING)
        assert ns_logger.isEnabledFor(logging.ERROR)

    def test_nightshift_routes_through_live_handler(self) -> None:
        """AC-3: nightshift.* records go through LiveAwareHandler with console."""
        from afcore.core.logging import get_live_handler, setup_logging

        setup_logging()
        handler = get_live_handler()
        assert handler is not None

        mock_console = MagicMock()
        handler.set_live_console(mock_console)

        try:
            ns_logger = logging.getLogger("nightshift.app")
            ns_logger.warning("test message")

            mock_console.print.assert_called_once()
            call_args = mock_console.print.call_args
            assert "test message" in call_args[0][0]
        finally:
            handler.set_live_console(None)

    def test_no_duplicate_handlers_on_repeated_calls(self) -> None:
        """AC-4: Calling setup_logging() twice does not duplicate handlers."""
        from afcore.core.logging import setup_logging

        setup_logging()
        setup_logging()

        for pkg in ("afcore", "nightshift"):
            lgr = logging.getLogger(pkg)
            live_handlers = [h for h in lgr.handlers if isinstance(h, LiveAwareHandler)]
            assert len(live_handlers) == 1, f"{pkg} has {len(live_handlers)} LiveAwareHandlers, expected 1"

    def test_shared_handler_singleton(self) -> None:
        """AC-5: get_live_handler() is the same instance on both loggers."""
        from afcore.core.logging import get_live_handler, setup_logging

        setup_logging()
        handler = get_live_handler()
        assert handler is not None

        afcore_handlers = [h for h in logging.getLogger("afcore").handlers if isinstance(h, LiveAwareHandler)]
        nightshift_handlers = [h for h in logging.getLogger("nightshift").handlers if isinstance(h, LiveAwareHandler)]

        assert len(afcore_handlers) == 1
        assert len(nightshift_handlers) == 1
        assert afcore_handlers[0] is handler
        assert nightshift_handlers[0] is handler
        assert afcore_handlers[0] is nightshift_handlers[0]

    def test_default_level_is_warning_for_nightshift(self) -> None:
        """Default call → nightshift gets WARNING, same as afcore."""
        from afcore.core.logging import setup_logging

        setup_logging()
        assert logging.getLogger("nightshift").level == logging.WARNING
        assert logging.getLogger("afcore").level == logging.WARNING
