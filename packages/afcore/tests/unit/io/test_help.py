"""Unit tests for afcore.io.help — dead-code removal verification.

Issue #98 removed exit_codes, render_json_help, and the --json --help
interception block.  These tests verify the symbols are no longer
importable and the interception block is gone from AgentFoxGroup.invoke.

Requirements: NS-REQ-1, NS-REQ-2, NS-REQ-3
"""

from __future__ import annotations

import inspect
import json

import click
import pytest
from click.testing import CliRunner


class TestExitCodesRemoved:
    """Verify exit_codes is no longer importable from the public API."""

    def test_exit_codes_not_importable_from_io(self) -> None:
        """NS-REQ-2.1: from afcore.io import exit_codes raises ImportError."""
        with pytest.raises(ImportError):
            from afcore.io import exit_codes  # noqa: F401

    def test_render_json_help_not_importable(self) -> None:
        """NS-REQ-2.1: from afcore.io.help import render_json_help raises ImportError."""
        with pytest.raises(ImportError):
            from afcore.io.help import render_json_help  # noqa: F401


class TestInterceptionBlockRemoved:
    """Verify the --json --help interception block is removed from invoke."""

    def test_invoke_has_no_interception_symbols(self) -> None:
        """NS-REQ-3.1: invoke source has no interception variable references."""
        from afcore.io.cli import AgentFoxGroup

        source = inspect.getsource(AgentFoxGroup.invoke)
        for sym in ("render_json_help", "_json_in_subcommand_args", "help_in_args", "json_in_args"):
            assert sym not in source, f"{sym} should not appear in AgentFoxGroup.invoke"


class TestHelpOutputUnchanged:
    """TS-03-53: --help output is standard Click text, not JSON, in Spec 03."""

    def test_help_is_standard_text(self) -> None:
        """03-REQ-10.4: Click's standard human-readable text help output."""
        from afcore.io import AgentFoxGroup, common_options

        @click.group(cls=AgentFoxGroup)
        @common_options
        def cli(**kwargs: object) -> None:
            pass

        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "Usage" in result.output or "Options" in result.output
        # Must not be valid JSON
        with pytest.raises((json.JSONDecodeError, ValueError)):
            json.loads(result.output)


class TestMainHasNoExitCodesAttribute:
    """NS-REQ-4.1: nightshift main has no exit_codes attribute."""

    def test_main_no_exit_codes(self) -> None:
        """The @exit_codes decorator is removed from app.py."""
        from nightshift.app import main

        assert not hasattr(main, "exit_codes"), "main should not have exit_codes attribute"
