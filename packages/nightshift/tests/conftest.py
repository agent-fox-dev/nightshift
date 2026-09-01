"""Shared fixtures for nightshift CLI test suite.

Fixture migration notes (07-REQ-8.4 / TS-07-37):
  - _reset_agent_fox_logger: shared with af tests, COPIED here
  - cli_runner: shared with af tests, COPIED here
  - cli_runner_separated: shared with af tests, COPIED here
  - hypothesis CI profile: shared with af tests, COPIED here
"""

from __future__ import annotations

import json as json_mod
import logging
import sys
from collections.abc import Generator
from pathlib import Path
from unittest.mock import MagicMock, patch

import click
import pytest
from click.testing import CliRunner
from hypothesis import settings

# Make afhub importable for carry-patch tests.  afhub is a sibling workspace
# package but not yet a nightshift wheel dependency.  Placed after all imports
# so that subsequent test modules (collected after conftest) can import afhub.
_afhub_root = str(Path(__file__).resolve().parent.parent.parent / "afhub")
if _afhub_root not in sys.path:
    sys.path.insert(0, _afhub_root)

settings.register_profile("ci", deadline=None)
settings.load_profile("ci")


def _make_mock_config() -> MagicMock:
    """Create a minimal mock config for nightshift CLI tests."""
    config = MagicMock()
    config.theme = None
    config.orchestrator.max_cost = 10.0
    config.knowledge = MagicMock()
    config.night_shift = MagicMock()
    config.night_shift.issue_check_interval = 900
    config.night_shift.push_fix_branch = False
    config.platform.type = "github"
    config.hub.endpoint_url = ""
    config.carry_patch.workspace = ""
    return config


@pytest.fixture(autouse=True)
def _reset_agent_fox_logger() -> Generator[None, None, None]:
    """Reset the agentfox logger after each test."""
    yield
    agent_logger = logging.getLogger("agentfox")
    agent_logger.setLevel(logging.NOTSET)
    agent_logger.handlers.clear()


@pytest.fixture(autouse=True)
def _mock_config_loading():
    """Mock config loading so tests don't need a real config file.

    The standalone nightshift CLI calls ``load_config()`` on startup.
    Without mocking, tests that invoke the CLI via CliRunner would fail
    trying to read ``.agent-fox/config.toml`` from the filesystem.
    """
    with patch("nightshift.app.load_config", return_value=_make_mock_config()):
        yield


@pytest.fixture(autouse=True)
def _mock_hub_auth():
    """Mock hub auth resolution so carry-patch resolution does not hit stubs.

    Without this, tests that invoke the CLI would call the real
    resolve_hub_url / resolve_hub_pat stubs that raise NotImplementedError.
    Returning empty strings ensures carry-patch mode stays inactive by default.
    """
    with (
        patch("nightshift.app.resolve_hub_url", return_value=""),
        patch("nightshift.app.resolve_hub_pat", return_value=""),
    ):
        yield


@pytest.fixture(autouse=True)
def _mock_daemon():
    """Mock the daemon so CLI tests don't block on ``asyncio.run(runner.run())``.

    ``_run_daemon()`` starts the real daemon loop which blocks indefinitely.
    This fixture replaces it with a mock that simulates the essential output
    (startup message, summary stats, JSONL events) without starting the daemon.
    """

    def _fake_run_daemon(ctx, om, config, *, hub_client=None):  # noqa: ARG001
        click.echo("Nightshift daemon starting. Press Ctrl-C to stop gracefully.")
        if om.json_mode:
            # Emit JSONL: one JSON object per line (not pretty-printed).
            click.echo(json_mod.dumps({"status": "stopped", "issues_fixed": 0, "total_cost": 0.0}))
        else:
            click.echo("Nightshift stopped. Issues fixed: 0, Total cost: $0.00")

    with patch("nightshift.app._run_daemon", side_effect=_fake_run_daemon):
        yield


@pytest.fixture
def cli_runner() -> CliRunner:
    """Provide a Click CLI test runner."""
    return CliRunner()


@pytest.fixture
def cli_runner_separated() -> CliRunner:
    """Provide a Click CLI test runner.

    Click 8.3+ always captures stderr separately, so this is identical
    to ``cli_runner``. Kept for backward-compatibility with test code
    that explicitly requests the separated variant.
    """
    return CliRunner()
