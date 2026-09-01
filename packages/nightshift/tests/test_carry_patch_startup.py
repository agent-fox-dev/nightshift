"""Tests for CLI flag 3-tier resolution, CWD validation, config generation,
and workspace variable initialization in nightshift carry-patch mode.

Tests verify that hub URL, workspace slug, and PAT are resolved in priority
order: CLI flag > environment variable > config file. They also verify the
conditions under which carry-patch mode is activated or skipped.

CWD validation tests (TS-02-16 through TS-02-26) verify the async startup
helper: HubClient construction, workspace state checks, git subprocess
invocation, origin URL matching, and logging on success.

Config generation tests (TS-02-27 through TS-02-32) verify the default
config file atomic write, skip-if-exists behaviour, PAT exclusion,
integration_branch handling, OS error resilience, and no-reload guarantee.

Workspace variable init tests (TS-02-33 through TS-02-35) verify
set_variable calls, non-fatal exception handling, and HubClient lifecycle.

These are initially failing tests (groups 2–4). They will pass once the CLI
flags (--hub-url, --workspace, --token), 3-tier resolution logic, the async
startup helper, config generator, and variable init are implemented
(groups 5–9).

Specification: 02_carry_patch_bootstrap
Test IDs: TS-02-7 through TS-02-15 (CLI resolution),
          TS-02-16 through TS-02-26 (CWD validation),
          TS-02-27 through TS-02-32 (config generation),
          TS-02-33 through TS-02-35 (workspace variable init)
Requirements: 02-REQ-2, 02-REQ-3, 02-REQ-4, 02-REQ-5
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from afhub.errors import (
    HubAuthError,
    HubConnectionError,
    HubForbiddenError,
    HubNotFoundError,
)
from afhub.models import Workspace
from click.testing import CliRunner
from nightshift._carry_patch_startup import startup_helper
from nightshift.app import main

# ---------------------------------------------------------------------------
# Test helpers and context managers
# ---------------------------------------------------------------------------


def _make_hub_subconfig(endpoint_url: str = "") -> MagicMock:
    """Create a MagicMock HubConfig with the given endpoint_url."""
    hub = MagicMock()
    hub.endpoint_url = endpoint_url
    return hub


def _make_carry_patch_subconfig(workspace: str = "", enabled: bool = False) -> MagicMock:
    """Create a MagicMock CarryPatchConfig with the given workspace."""
    cp = MagicMock()
    cp.workspace = workspace
    cp.enabled = enabled
    return cp


def _make_config(
    hub_endpoint_url: str = "",
    carry_patch_workspace: str = "",
) -> MagicMock:
    """Create a MagicMock AgentFoxConfig with hub and carry_patch sub-configs.

    Returns a mock whose .hub.endpoint_url and .carry_patch.workspace are
    set to concrete strings (not nested MagicMocks), so resolution logic
    that tests falsy/truthy values behaves predictably.
    """
    config = MagicMock()
    config.theme = None
    config.orchestrator.max_cost = 10.0
    config.hub = _make_hub_subconfig(hub_endpoint_url)
    config.carry_patch = _make_carry_patch_subconfig(carry_patch_workspace)
    return config


@contextmanager
def _set_env(**env_vars: str) -> Generator[None, None, None]:
    """Temporarily set the given environment variables, restoring on exit."""
    originals: dict[str, str | None] = {}
    for key in env_vars:
        originals[key] = os.environ.get(key)
    try:
        for key, value in env_vars.items():
            os.environ[key] = value
        yield
    finally:
        for key, original in originals.items():
            if original is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = original


@contextmanager
def _clear_env(*keys: str) -> Generator[None, None, None]:
    """Temporarily remove the given environment variables, restoring on exit."""
    originals: dict[str, str | None] = {}
    for key in keys:
        originals[key] = os.environ.pop(key, None)
    try:
        yield
    finally:
        for key, original in originals.items():
            if original is not None:
                os.environ[key] = original


# ---------------------------------------------------------------------------
# Hub URL 3-tier resolution tests (TS-02-7, TS-02-8, TS-02-9, TS-02-13)
# ---------------------------------------------------------------------------


class TestHubUrlResolution:
    """Hub URL is resolved via CLI flag > AF_HUB_URL env > config.hub.endpoint_url.

    Requirements: 02-REQ-2.1, 02-REQ-2.2, 02-REQ-2.3, 02-REQ-2.7
    """

    def test_hub_url_from_flag(self, cli_runner: CliRunner) -> None:
        """CLI --hub-url flag provides hub URL when carry-patch mode is active (TS-02-7).

        With --hub-url, --workspace, and --token all provided as CLI flags,
        carry-patch mode is activated and the hub URL comes from the flag value.
        resolve_hub_url must be called with the flag value, and the async
        startup helper must be invoked.

        Requirements: 02-REQ-2.1
        Test ID: TS-02-7
        """
        config = _make_config(hub_endpoint_url="")
        with (
            patch("nightshift.app.load_config", return_value=config),
            patch(
                "nightshift.app.resolve_hub_url",
                create=True,
                return_value="https://hub.example.com",
            ) as mock_resolve_url,
            patch("nightshift.app.resolve_hub_pat", create=True, return_value="myPAT"),
            patch(
                "nightshift.app._carry_patch_startup",
                create=True,
                new_callable=AsyncMock,
            ) as mock_startup,
            _clear_env("AF_HUB_URL", "AF_WORKSPACE", "AF_HUB_TOKEN"),
        ):
            result = cli_runner.invoke(
                main,
                ["--hub-url", "https://hub.example.com", "--workspace", "my-slug", "--token", "myPAT"],
            )

        assert result.exit_code == 0, (
            f"Expected exit 0 with all flags provided; got {result.exit_code}.\n"
            f"stdout: {result.output!r}\nstderr: {result.stderr!r}"
        )
        assert mock_resolve_url.call_count == 1, "resolve_hub_url was not called; hub URL resolution logic is missing"
        called_with = mock_resolve_url.call_args
        assert called_with is not None
        # The CLI flag value must be forwarded to resolve_hub_url
        all_passed = list(called_with.args) + list(called_with.kwargs.values())
        assert "https://hub.example.com" in str(all_passed), (
            f"resolve_hub_url was not called with the CLI flag value. Call args: {called_with}"
        )
        assert mock_startup.call_count == 1, "Async startup helper was not called; carry-patch mode was not activated"

    def test_hub_url_from_env(self, cli_runner: CliRunner) -> None:
        """AF_HUB_URL env var is used when --hub-url flag is absent (TS-02-8).

        When AF_HUB_URL is set in the environment and no --hub-url flag is
        passed, carry-patch mode is activated using the env var value as the
        hub URL.

        Requirements: 02-REQ-2.2
        Test ID: TS-02-8
        """
        config = _make_config(hub_endpoint_url="")
        with (
            patch("nightshift.app.load_config", return_value=config),
            patch(
                "nightshift.app.resolve_hub_url",
                create=True,
                return_value="https://env.hub.example.com",
            ) as mock_resolve_url,
            patch("nightshift.app.resolve_hub_pat", create=True, return_value="myPAT"),
            patch(
                "nightshift.app._carry_patch_startup",
                create=True,
                new_callable=AsyncMock,
            ) as mock_startup,
            _set_env(AF_HUB_URL="https://env.hub.example.com"),
            _clear_env("AF_WORKSPACE", "AF_HUB_TOKEN"),
        ):
            result = cli_runner.invoke(
                main,
                ["--workspace", "my-slug", "--token", "myPAT"],
            )

        assert result.exit_code == 0, (
            f"Expected exit 0 when AF_HUB_URL is set; got {result.exit_code}.\n"
            f"stdout: {result.output!r}\nstderr: {result.stderr!r}"
        )
        assert mock_resolve_url.call_count == 1, "resolve_hub_url was not called; hub URL resolution logic is missing"
        assert mock_startup.call_count == 1, "Async startup helper was not called; carry-patch mode was not activated"

    def test_hub_url_from_config(self, cli_runner: CliRunner) -> None:
        """config.hub.endpoint_url is used when flag and env var are both absent (TS-02-9).

        When neither --hub-url flag nor AF_HUB_URL env var is present, the hub
        URL falls back to config.hub.endpoint_url. resolve_hub_url must be called
        with the config URL value.

        Requirements: 02-REQ-2.3
        Test ID: TS-02-9
        """
        config = _make_config(hub_endpoint_url="https://config.hub.example.com")
        with (
            patch("nightshift.app.load_config", return_value=config),
            patch(
                "nightshift.app.resolve_hub_url",
                create=True,
                return_value="https://config.hub.example.com",
            ) as mock_resolve_url,
            patch("nightshift.app.resolve_hub_pat", create=True, return_value="myPAT"),
            patch(
                "nightshift.app._carry_patch_startup",
                create=True,
                new_callable=AsyncMock,
            ) as mock_startup,
            _clear_env("AF_HUB_URL", "AF_WORKSPACE", "AF_HUB_TOKEN"),
        ):
            result = cli_runner.invoke(
                main,
                ["--workspace", "my-slug", "--token", "myPAT"],
            )

        assert result.exit_code == 0, (
            f"Expected exit 0 when config hub URL is set; got {result.exit_code}.\n"
            f"stdout: {result.output!r}\nstderr: {result.stderr!r}"
        )
        assert mock_resolve_url.call_count == 1, "resolve_hub_url was not called; hub URL resolution logic is missing"
        called_with = mock_resolve_url.call_args
        assert called_with is not None
        # The config URL must be forwarded to resolve_hub_url
        all_passed = list(called_with.args) + list(called_with.kwargs.values())
        assert "https://config.hub.example.com" in str(all_passed), (
            f"resolve_hub_url was not called with config.hub.endpoint_url. Call args: {called_with}"
        )
        assert mock_startup.call_count == 1, "Async startup helper was not called; carry-patch mode was not activated"

    def test_hub_url_only_skips_carry_patch(self, cli_runner: CliRunner) -> None:
        """Resolving hub URL alone (no workspace, no token) does not activate carry-patch (TS-02-13).

        When --hub-url is provided but neither --workspace nor any token source
        is present, carry-patch mode must NOT be activated. The daemon runs
        normally.

        Requirements: 02-REQ-2.7
        Test ID: TS-02-13
        """
        config = _make_config(hub_endpoint_url="", carry_patch_workspace="")
        with (
            patch("nightshift.app.load_config", return_value=config),
            patch(
                "nightshift.app.resolve_hub_url",
                create=True,
                return_value="https://hub.example.com",
            ),
            patch("nightshift.app.resolve_hub_pat", create=True, return_value=""),
            patch(
                "nightshift.app._carry_patch_startup",
                create=True,
                new_callable=AsyncMock,
            ) as mock_startup,
            _clear_env("AF_HUB_URL", "AF_WORKSPACE", "AF_HUB_TOKEN"),
        ):
            result = cli_runner.invoke(
                main,
                ["--hub-url", "https://hub.example.com"],
            )

        assert result.exit_code == 0, (
            f"Expected exit 0 when only hub URL is given; got {result.exit_code}.\n"
            f"stdout: {result.output!r}\nstderr: {result.stderr!r}"
        )
        assert mock_startup.call_count == 0, (
            "Carry-patch startup helper was called despite no workspace or token; "
            "carry-patch mode must not be activated without both workspace and PAT"
        )


# ---------------------------------------------------------------------------
# Workspace slug 3-tier resolution tests (TS-02-10, TS-02-11, workspace-flag)
# ---------------------------------------------------------------------------


class TestWorkspaceSlugResolution:
    """Workspace slug is resolved via --workspace flag > AF_WORKSPACE env > config.carry_patch.workspace.

    Requirements: 02-REQ-2.1, 02-REQ-2.4, 02-REQ-2.5
    """

    def test_workspace_from_flag(self, cli_runner: CliRunner) -> None:
        """--workspace CLI flag is used first; overrides env var and config (TS-02-7 workspace aspect).

        When --workspace is provided along with AF_WORKSPACE set in the
        environment and a non-empty config.carry_patch.workspace, the flag
        value takes precedence. The startup helper must be called with the
        flag slug, not the env or config slug.

        Requirements: 02-REQ-2.1
        Test ID: TS-02-7 (workspace resolution dimension)
        """
        config = _make_config(
            hub_endpoint_url="https://hub.example.com",
            carry_patch_workspace="config-slug",
        )
        with (
            patch("nightshift.app.load_config", return_value=config),
            patch(
                "nightshift.app.resolve_hub_url",
                create=True,
                return_value="https://hub.example.com",
            ),
            patch("nightshift.app.resolve_hub_pat", create=True, return_value="myPAT"),
            patch(
                "nightshift.app._carry_patch_startup",
                create=True,
                new_callable=AsyncMock,
            ) as mock_startup,
            _set_env(AF_WORKSPACE="env-slug"),
            _clear_env("AF_HUB_URL", "AF_HUB_TOKEN"),
        ):
            result = cli_runner.invoke(
                main,
                [
                    "--hub-url",
                    "https://hub.example.com",
                    "--workspace",
                    "flag-slug",
                    "--token",
                    "myPAT",
                ],
            )

        assert result.exit_code == 0, (
            f"Expected exit 0 when --workspace flag is provided; got {result.exit_code}.\n"
            f"stdout: {result.output!r}\nstderr: {result.stderr!r}"
        )
        assert mock_startup.call_count == 1, "Async startup helper was not called; carry-patch mode was not activated"
        called_with = mock_startup.call_args
        assert called_with is not None
        # The flag slug must be used, not env-slug or config-slug
        all_passed = list(called_with.args) + list(called_with.kwargs.values())
        assert "flag-slug" in str(all_passed), (
            f"Startup helper was not called with the flag slug 'flag-slug'. Call args: {called_with}"
        )

    def test_workspace_from_env(self, cli_runner: CliRunner) -> None:
        """AF_WORKSPACE env var is used when --workspace flag is absent (TS-02-10).

        When --workspace flag is not provided but AF_WORKSPACE is set, the env
        var value is used as the workspace slug. The startup helper must be
        called with the env var slug.

        Requirements: 02-REQ-2.4
        Test ID: TS-02-10
        """
        config = _make_config(
            hub_endpoint_url="",
            carry_patch_workspace="config-slug",
        )
        with (
            patch("nightshift.app.load_config", return_value=config),
            patch(
                "nightshift.app.resolve_hub_url",
                create=True,
                return_value="https://hub.example.com",
            ),
            patch("nightshift.app.resolve_hub_pat", create=True, return_value="myPAT"),
            patch(
                "nightshift.app._carry_patch_startup",
                create=True,
                new_callable=AsyncMock,
            ) as mock_startup,
            _set_env(AF_WORKSPACE="env-slug"),
            _clear_env("AF_HUB_URL", "AF_HUB_TOKEN"),
        ):
            result = cli_runner.invoke(
                main,
                ["--hub-url", "https://hub.example.com", "--token", "myPAT"],
            )

        assert result.exit_code == 0, (
            f"Expected exit 0 when AF_WORKSPACE is set; got {result.exit_code}.\n"
            f"stdout: {result.output!r}\nstderr: {result.stderr!r}"
        )
        assert mock_startup.call_count == 1, "Async startup helper was not called; carry-patch mode was not activated"
        called_with = mock_startup.call_args
        assert called_with is not None
        all_passed = list(called_with.args) + list(called_with.kwargs.values())
        assert "env-slug" in str(all_passed), (
            f"Startup helper was not called with AF_WORKSPACE value 'env-slug'. Call args: {called_with}"
        )

    def test_workspace_from_config(self, cli_runner: CliRunner) -> None:
        """config.carry_patch.workspace is used when flag and env var are both absent (TS-02-11).

        When neither --workspace flag nor AF_WORKSPACE env var is set, the
        workspace slug falls back to config.carry_patch.workspace. The startup
        helper must be called with the config slug.

        Requirements: 02-REQ-2.5
        Test ID: TS-02-11
        """
        config = _make_config(
            hub_endpoint_url="",
            carry_patch_workspace="config-slug",
        )
        with (
            patch("nightshift.app.load_config", return_value=config),
            patch(
                "nightshift.app.resolve_hub_url",
                create=True,
                return_value="https://hub.example.com",
            ),
            patch("nightshift.app.resolve_hub_pat", create=True, return_value="myPAT"),
            patch(
                "nightshift.app._carry_patch_startup",
                create=True,
                new_callable=AsyncMock,
            ) as mock_startup,
            _clear_env("AF_HUB_URL", "AF_WORKSPACE", "AF_HUB_TOKEN"),
        ):
            result = cli_runner.invoke(
                main,
                ["--hub-url", "https://hub.example.com", "--token", "myPAT"],
            )

        assert result.exit_code == 0, (
            f"Expected exit 0 when config workspace is set; got {result.exit_code}.\n"
            f"stdout: {result.output!r}\nstderr: {result.stderr!r}"
        )
        assert mock_startup.call_count == 1, "Async startup helper was not called; carry-patch mode was not activated"
        called_with = mock_startup.call_args
        assert called_with is not None
        all_passed = list(called_with.args) + list(called_with.kwargs.values())
        assert "config-slug" in str(all_passed), (
            f"Startup helper was not called with config.carry_patch.workspace value. Call args: {called_with}"
        )


# ---------------------------------------------------------------------------
# Carry-patch mode activation / error-exit tests (TS-02-12, TS-02-14, TS-02-15)
# ---------------------------------------------------------------------------


class TestCarryPatchModeActivation:
    """Tests for conditions under which carry-patch mode activates or exits with an error.

    Requirements: 02-REQ-2.6, 02-REQ-2.8, 02-REQ-2.9
    """

    def test_no_workspace_no_token_skips_carry_patch(self, cli_runner: CliRunner) -> None:
        """Carry-patch mode is skipped entirely when neither workspace nor token resolves (TS-02-12).

        With no --workspace flag, empty AF_WORKSPACE, empty config workspace,
        and no token from any source, nightshift must run normally without
        activating carry-patch mode. No hub API calls should be made.

        Requirements: 02-REQ-2.6
        Test ID: TS-02-12
        """
        config = _make_config(hub_endpoint_url="", carry_patch_workspace="")
        with (
            patch("nightshift.app.load_config", return_value=config),
            patch("nightshift.app.resolve_hub_url", create=True, return_value=""),
            patch("nightshift.app.resolve_hub_pat", create=True, return_value=""),
            patch(
                "nightshift.app._carry_patch_startup",
                create=True,
                new_callable=AsyncMock,
            ) as mock_startup,
            _clear_env("AF_HUB_URL", "AF_WORKSPACE", "AF_HUB_TOKEN"),
        ):
            result = cli_runner.invoke(main, [])

        assert result.exit_code == 0, (
            f"Expected exit 0 with no carry-patch sources; got {result.exit_code}.\n"
            f"stdout: {result.output!r}\nstderr: {result.stderr!r}"
        )
        assert mock_startup.call_count == 0, (
            "Carry-patch startup helper was called despite no workspace or token; "
            "carry-patch mode must be skipped when neither workspace nor PAT is present"
        )

    def test_missing_token_exits(self, cli_runner: CliRunner) -> None:
        """CLI exits with code 1 and PAT-related error message when workspace resolves but token is absent (TS-02-14).

        When a workspace slug is resolved (via --workspace flag here) but no PAT
        is available from any source (no --token, no AF_HUB_TOKEN, resolve_hub_pat
        returns empty), nightshift must exit with code 1 and write an error to
        stderr that explains a PAT is required.

        Requirements: 02-REQ-2.8
        Test ID: TS-02-14
        """
        config = _make_config(hub_endpoint_url="https://hub.example.com", carry_patch_workspace="")
        with (
            patch("nightshift.app.load_config", return_value=config),
            patch(
                "nightshift.app.resolve_hub_url",
                create=True,
                return_value="https://hub.example.com",
            ),
            patch("nightshift.app.resolve_hub_pat", create=True, return_value=""),
            patch(
                "nightshift.app._carry_patch_startup",
                create=True,
                new_callable=AsyncMock,
            ),
            _clear_env("AF_HUB_URL", "AF_WORKSPACE", "AF_HUB_TOKEN"),
        ):
            result = cli_runner.invoke(
                main,
                ["--workspace", "my-slug"],
            )

        assert result.exit_code == 1, (
            f"Expected exit 1 when workspace is set but no PAT; got {result.exit_code}.\n"
            f"stdout: {result.output!r}\nstderr: {result.stderr!r}"
        )
        stderr_lower = result.stderr.lower()
        assert "pat" in result.stderr or "token" in stderr_lower, (
            f"Expected error message mentioning PAT or token in stderr; got: {result.stderr!r}"
        )

    def test_token_only_no_workspace_skips_carry_patch(self, cli_runner: CliRunner) -> None:
        """Carry-patch mode is skipped when token resolves but workspace does not (02-REQ-2.11).

        When --token is provided (or AF_HUB_TOKEN is set) but --workspace
        resolves to empty from all sources (flag, AF_WORKSPACE, and
        config.carry_patch.workspace), nightshift must silently skip
        carry-patch mode and run normally. The resolved token is discarded.
        No hub API calls should be made; no error is emitted.

        Requirements: 02-REQ-2.11
        """
        config = _make_config(hub_endpoint_url="https://hub.example.com", carry_patch_workspace="")
        with (
            patch("nightshift.app.load_config", return_value=config),
            patch(
                "nightshift.app.resolve_hub_url",
                create=True,
                return_value="https://hub.example.com",
            ),
            patch("nightshift.app.resolve_hub_pat", create=True, return_value="fallback-pat"),
            patch(
                "nightshift.app._carry_patch_startup",
                create=True,
                new_callable=AsyncMock,
            ) as mock_startup,
            _clear_env("AF_HUB_URL", "AF_WORKSPACE", "AF_HUB_TOKEN"),
        ):
            result = cli_runner.invoke(
                main,
                ["--token", "myPAT"],
            )

        assert result.exit_code == 0, (
            f"Expected exit 0 when token is set but no workspace; got {result.exit_code}.\n"
            f"stdout: {result.output!r}\nstderr: {result.stderr!r}"
        )
        assert mock_startup.call_count == 0, (
            "Carry-patch startup helper was called despite empty workspace; "
            "carry-patch mode must not activate without a resolved workspace slug"
        )

    def test_cli_token_skips_resolve_hub_pat(self, cli_runner: CliRunner) -> None:
        """CLI --token flag is used directly without calling resolve_hub_pat() (02-REQ-2.E1).

        When the operator supplies --token on the CLI, the flag value must be
        used as the PAT. resolve_hub_pat() should not be called at all since
        the CLI flag short-circuits the resolution.

        Requirements: 02-REQ-2.E1
        """
        config = _make_config(hub_endpoint_url="", carry_patch_workspace="")
        with (
            patch("nightshift.app.load_config", return_value=config),
            patch(
                "nightshift.app.resolve_hub_url",
                create=True,
                return_value="https://hub.example.com",
            ),
            patch(
                "nightshift.app.resolve_hub_pat",
                create=True,
                return_value="env-pat-should-not-be-used",
            ) as mock_resolve_pat,
            patch(
                "nightshift.app._carry_patch_startup",
                create=True,
                new_callable=AsyncMock,
            ) as mock_startup,
            _clear_env("AF_HUB_URL", "AF_WORKSPACE", "AF_HUB_TOKEN"),
        ):
            result = cli_runner.invoke(
                main,
                ["--hub-url", "https://hub.example.com", "--workspace", "my-slug", "--token", "cli-pat"],
            )

        assert result.exit_code == 0, (
            f"Expected exit 0 with all flags provided; got {result.exit_code}.\n"
            f"stdout: {result.output!r}\nstderr: {result.stderr!r}"
        )
        assert mock_startup.call_count == 1, "Startup helper was not called; carry-patch mode was not activated"
        # When --token is provided on the CLI, the CLI value must be used
        # directly. resolve_hub_pat() should NOT be called.
        assert mock_resolve_pat.call_count == 0, (
            "resolve_hub_pat() was called despite --token being provided on the CLI; "
            "the CLI --token flag must take precedence without calling resolve_hub_pat()"
        )

    def test_missing_hub_url_exits(self, cli_runner: CliRunner) -> None:
        """CLI exits with code 1 and hub-URL-related error when carry-patch is active but hub URL is absent (TS-02-15).

        When workspace and token are both resolved (carry-patch mode would
        activate), but no hub URL is available from any source (no --hub-url,
        no AF_HUB_URL, empty config.hub.endpoint_url), nightshift must exit
        with code 1 and write an error to stderr that explains the hub URL
        is required.

        Requirements: 02-REQ-2.9
        Test ID: TS-02-15
        """
        config = _make_config(hub_endpoint_url="", carry_patch_workspace="")
        with (
            patch("nightshift.app.load_config", return_value=config),
            patch("nightshift.app.resolve_hub_url", create=True, return_value=""),
            patch("nightshift.app.resolve_hub_pat", create=True, return_value="myPAT"),
            patch(
                "nightshift.app._carry_patch_startup",
                create=True,
                new_callable=AsyncMock,
            ),
            _clear_env("AF_HUB_URL", "AF_WORKSPACE", "AF_HUB_TOKEN"),
        ):
            result = cli_runner.invoke(
                main,
                ["--workspace", "my-slug", "--token", "myPAT"],
            )

        assert result.exit_code == 1, (
            f"Expected exit 1 when hub URL is absent but workspace+token are set; "
            f"got {result.exit_code}.\n"
            f"stdout: {result.output!r}\nstderr: {result.stderr!r}"
        )
        stderr_lower = result.stderr.lower()
        assert "hub" in stderr_lower and ("url" in stderr_lower or "required" in stderr_lower), (
            f"Expected error message mentioning hub URL in stderr; got: {result.stderr!r}"
        )


# ---------------------------------------------------------------------------
# CWD validation test helpers
# ---------------------------------------------------------------------------

_VALID_HUB_URL = "https://hub.example.com"
_VALID_PAT = "myPAT"
_VALID_SLUG = "my-slug"
_VALID_GIT_URL = "https://git.example.com/repo.git"


def _valid_workspace(**overrides: object) -> Workspace:
    """Create a Workspace model that passes all CWD validation checks.

    Override individual fields to trigger specific failure modes.
    """
    defaults: dict[str, object] = {
        "slug": _VALID_SLUG,
        "workspace_mode": "carry_patch",
        "clone_status": "ready",
        "git_url": _VALID_GIT_URL,
        "status": "active",
        "sync_status": "synced",
    }
    defaults.update(overrides)
    return Workspace(**defaults)  # type: ignore[arg-type]


def _mock_hub_client(
    *,
    get_workspace_returns: Workspace | None = None,
    get_workspace_raises: BaseException | None = None,
) -> MagicMock:
    """Build a mock HubClient whose get_workspace is an AsyncMock.

    Either *returns* the given workspace or *raises* the given exception.
    set_variable is mocked to succeed silently by default.
    """
    client = MagicMock()
    gw = AsyncMock()
    if get_workspace_raises is not None:
        gw.side_effect = get_workspace_raises
    else:
        gw.return_value = get_workspace_returns or _valid_workspace()
    client.get_workspace = gw
    client.set_variable = AsyncMock()
    return client


def _git_result(
    returncode: int = 0,
    stdout: bytes = b"",
    stderr: bytes = b"",
) -> MagicMock:
    """Build a mock subprocess.CompletedProcess for git remote get-url."""
    result = MagicMock()
    result.returncode = returncode
    result.stdout = stdout
    result.stderr = stderr
    return result


def _run_startup(
    *,
    hub_url: str = _VALID_HUB_URL,
    pat: str = _VALID_PAT,
    slug: str = _VALID_SLUG,
) -> None:
    """Run the async startup_helper synchronously.

    Caller is responsible for patching HubClient and subprocess.run
    before calling this helper.
    """
    asyncio.run(
        startup_helper(
            hub_url=hub_url,
            pat=pat,
            slug=slug,
            config=MagicMock(),
        )
    )


# ---------------------------------------------------------------------------
# TS-02-16: HubClient construction and get_workspace invocation
# ---------------------------------------------------------------------------


class TestCwdValidationHubClientConstruction:
    """Verify the startup helper constructs exactly one HubClient and calls
    get_workspace with the resolved slug.

    Requirements: 02-REQ-3.1
    Test ID: TS-02-16
    """

    def test_startup_constructs_one_hubclient_and_calls_get_workspace(self) -> None:
        """HubClient is constructed once with the resolved endpoint_url and pat,
        and get_workspace is called once with the workspace slug.

        TS-02-16 / 02-REQ-3.1
        """
        mock_client = _mock_hub_client()
        mock_git = _git_result(
            returncode=0,
            stdout=_VALID_GIT_URL.encode() + b"\n",
        )

        with (
            patch(
                "nightshift._carry_patch_startup.HubClient",
                create=True,
                return_value=mock_client,
            ) as mock_cls,
            patch(
                "subprocess.run",
                return_value=mock_git,
            ),
        ):
            _run_startup()

        # Exactly one HubClient instance was created
        assert mock_cls.call_count == 1, (
            f"Expected HubClient to be constructed exactly once; got {mock_cls.call_count} calls"
        )

        # Constructed with the correct keyword arguments
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs.get("endpoint_url") == _VALID_HUB_URL, (
            f"Expected endpoint_url={_VALID_HUB_URL!r}; got {call_kwargs}"
        )
        assert call_kwargs.get("pat") == _VALID_PAT, f"Expected pat={_VALID_PAT!r}; got {call_kwargs}"

        # get_workspace called once with the slug
        assert mock_client.get_workspace.call_count == 1, (
            f"Expected get_workspace to be called once; got {mock_client.get_workspace.call_count}"
        )
        assert mock_client.get_workspace.call_args[0][0] == _VALID_SLUG, (
            f"Expected get_workspace to be called with {_VALID_SLUG!r}; got {mock_client.get_workspace.call_args}"
        )


# ---------------------------------------------------------------------------
# TS-02-17, TS-02-18: get_workspace API error exits
# REQ-3.12 (HubForbiddenError) and REQ-3.E4 (HubConnectionError)
# ---------------------------------------------------------------------------


class TestCwdValidationGetWorkspaceErrors:
    """Verify the startup helper exits with code 1 and writes diagnostic
    errors to stderr when get_workspace raises hub API exceptions.

    Requirements: 02-REQ-3.2, 02-REQ-3.3, 02-REQ-3.12, 02-REQ-3.E4
    Test IDs: TS-02-17, TS-02-18
    """

    def test_hub_auth_error_exits(self, capfd: pytest.CaptureFixture[str]) -> None:
        """Exit 1 with stderr diagnostic when get_workspace raises HubAuthError.

        TS-02-17 / 02-REQ-3.2
        """
        mock_client = _mock_hub_client(
            get_workspace_raises=HubAuthError(
                status_code=401,
                message="invalid token",
                error_type="unauthorized",
            ),
        )

        with (
            patch(
                "nightshift._carry_patch_startup.HubClient",
                create=True,
                return_value=mock_client,
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_startup()

        assert exc_info.value.code == 1
        captured = capfd.readouterr()
        stderr_lower = captured.err.lower()
        assert "pat" in stderr_lower or "permission" in stderr_lower or "auth" in stderr_lower, (
            f"Expected stderr to mention PAT, permission, or auth; got: {captured.err!r}"
        )

    def test_hub_not_found_error_exits(self, capfd: pytest.CaptureFixture[str]) -> None:
        """Exit 1 with stderr diagnostic when get_workspace raises HubNotFoundError.

        TS-02-18 / 02-REQ-3.3
        """
        mock_client = _mock_hub_client(
            get_workspace_raises=HubNotFoundError(
                status_code=404,
                message="workspace not found",
                error_type="not_found",
            ),
        )

        with (
            patch(
                "nightshift._carry_patch_startup.HubClient",
                create=True,
                return_value=mock_client,
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_startup()

        assert exc_info.value.code == 1
        captured = capfd.readouterr()
        stderr_lower = captured.err.lower()
        assert "not found" in stderr_lower or "slug" in stderr_lower, (
            f"Expected stderr to mention 'not found' or 'slug'; got: {captured.err!r}"
        )

    def test_hub_forbidden_error_exits(self, capfd: pytest.CaptureFixture[str]) -> None:
        """Exit 1 with stderr diagnostic when get_workspace raises HubForbiddenError.

        02-REQ-3.12 (additional coverage beyond TS-02-17/18)
        """
        mock_client = _mock_hub_client(
            get_workspace_raises=HubForbiddenError(
                status_code=403,
                message="insufficient scope",
                error_type="forbidden",
            ),
        )

        with (
            patch(
                "nightshift._carry_patch_startup.HubClient",
                create=True,
                return_value=mock_client,
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_startup()

        assert exc_info.value.code == 1
        captured = capfd.readouterr()
        stderr_lower = captured.err.lower()
        assert "scope" in stderr_lower or "permission" in stderr_lower or "forbidden" in stderr_lower, (
            f"Expected stderr to mention scope/permission/forbidden; got: {captured.err!r}"
        )

    def test_hub_connection_error_exits(self, capfd: pytest.CaptureFixture[str]) -> None:
        """Exit 1 with stderr diagnostic when get_workspace raises HubConnectionError.

        02-REQ-3.E4 (edge case: network-level failure)
        """
        mock_client = _mock_hub_client(
            get_workspace_raises=HubConnectionError(
                status_code=0,
                message="Connection refused",
                error_type="connection_error",
            ),
        )

        with (
            patch(
                "nightshift._carry_patch_startup.HubClient",
                create=True,
                return_value=mock_client,
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_startup()

        assert exc_info.value.code == 1
        captured = capfd.readouterr()
        stderr_lower = captured.err.lower()
        assert "connect" in stderr_lower or "network" in stderr_lower, (
            f"Expected stderr to mention connection/network failure; got: {captured.err!r}"
        )


# ---------------------------------------------------------------------------
# TS-02-19, TS-02-20: workspace_mode and clone_status validation
# REQ-3.E2 (pending), REQ-3.E3 (failed)
# ---------------------------------------------------------------------------


class TestCwdValidationWorkspaceChecks:
    """Verify the startup helper exits with code 1 when workspace_mode or
    clone_status do not match expected values.

    Requirements: 02-REQ-3.4, 02-REQ-3.5, 02-REQ-3.E2, 02-REQ-3.E3
    Test IDs: TS-02-19, TS-02-20
    """

    def test_wrong_workspace_mode_exits(self, capfd: pytest.CaptureFixture[str]) -> None:
        """Exit 1 with stderr when workspace_mode is not 'carry_patch'.

        TS-02-19 / 02-REQ-3.4
        """
        workspace = _valid_workspace(workspace_mode="standard")
        mock_client = _mock_hub_client(get_workspace_returns=workspace)

        with (
            patch(
                "nightshift._carry_patch_startup.HubClient",
                create=True,
                return_value=mock_client,
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_startup()

        assert exc_info.value.code == 1
        captured = capfd.readouterr()
        assert "carry_patch" in captured.err or "carry-patch" in captured.err, (
            f"Expected stderr to mention carry_patch/carry-patch mode; got: {captured.err!r}"
        )

    def test_clone_status_not_ready_exits(self, capfd: pytest.CaptureFixture[str]) -> None:
        """Exit 1 with stderr showing actual clone_status when not 'ready'.

        TS-02-20 / 02-REQ-3.5
        """
        workspace = _valid_workspace(clone_status="cloning")
        mock_client = _mock_hub_client(get_workspace_returns=workspace)

        with (
            patch(
                "nightshift._carry_patch_startup.HubClient",
                create=True,
                return_value=mock_client,
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_startup()

        assert exc_info.value.code == 1
        captured = capfd.readouterr()
        assert "cloning" in captured.err, (
            f"Expected stderr to show actual clone_status 'cloning'; got: {captured.err!r}"
        )

    def test_clone_status_pending_exits(self, capfd: pytest.CaptureFixture[str]) -> None:
        """Exit 1 when clone_status is 'pending'.

        02-REQ-3.E2
        """
        workspace = _valid_workspace(clone_status="pending")
        mock_client = _mock_hub_client(get_workspace_returns=workspace)

        with (
            patch(
                "nightshift._carry_patch_startup.HubClient",
                create=True,
                return_value=mock_client,
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_startup()

        assert exc_info.value.code == 1
        captured = capfd.readouterr()
        assert "pending" in captured.err, f"Expected stderr to show clone_status 'pending'; got: {captured.err!r}"

    def test_clone_status_failed_exits(self, capfd: pytest.CaptureFixture[str]) -> None:
        """Exit 1 when clone_status is 'failed'.

        02-REQ-3.E3
        """
        workspace = _valid_workspace(clone_status="failed")
        mock_client = _mock_hub_client(get_workspace_returns=workspace)

        with (
            patch(
                "nightshift._carry_patch_startup.HubClient",
                create=True,
                return_value=mock_client,
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_startup()

        assert exc_info.value.code == 1
        captured = capfd.readouterr()
        assert "failed" in captured.err, f"Expected stderr to show clone_status 'failed'; got: {captured.err!r}"


# ---------------------------------------------------------------------------
# TS-02-21, TS-02-22, TS-02-23, TS-02-24: git subprocess behaviour
# ---------------------------------------------------------------------------


class TestCwdValidationGitSubprocess:
    """Verify the startup helper invokes subprocess.run correctly and handles
    git binary errors (missing, timeout, non-zero exit).

    Requirements: 02-REQ-3.6, 02-REQ-3.7, 02-REQ-3.8, 02-REQ-3.9
    Test IDs: TS-02-21, TS-02-22, TS-02-23, TS-02-24
    """

    def test_subprocess_called_with_correct_args(self) -> None:
        """subprocess.run is invoked with the correct git command and kwargs.

        TS-02-21 / 02-REQ-3.6
        """
        mock_client = _mock_hub_client()
        mock_git = _git_result(
            returncode=0,
            stdout=_VALID_GIT_URL.encode() + b"\n",
        )

        with (
            patch(
                "nightshift._carry_patch_startup.HubClient",
                create=True,
                return_value=mock_client,
            ),
            patch(
                "subprocess.run",
                return_value=mock_git,
            ) as mock_run,
        ):
            _run_startup()

        assert mock_run.call_count >= 1, "subprocess.run was not called"
        call_args = mock_run.call_args
        assert call_args.args[0] == ["git", "remote", "get-url", "origin"], (
            f"Expected git remote get-url origin command; got: {call_args.args[0]}"
        )
        assert call_args.kwargs.get("stdout") == subprocess.PIPE
        assert call_args.kwargs.get("stderr") == subprocess.PIPE
        assert call_args.kwargs.get("timeout") == 10

    def test_git_not_installed_exits_with_exact_message(
        self,
        capfd: pytest.CaptureFixture[str],
    ) -> None:
        """Exit 1 with exact message when git binary is not found.

        TS-02-22 / 02-REQ-3.7
        """
        mock_client = _mock_hub_client()

        with (
            patch(
                "nightshift._carry_patch_startup.HubClient",
                create=True,
                return_value=mock_client,
            ),
            patch(
                "subprocess.run",
                side_effect=FileNotFoundError("No such file"),
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_startup()

        assert exc_info.value.code == 1
        captured = capfd.readouterr()
        assert captured.err.strip() == ("git is not installed or not in PATH; nightshift requires git"), (
            f"Expected exact git-not-found message; got: {captured.err!r}"
        )

    def test_git_timeout_exits(self, capfd: pytest.CaptureFixture[str]) -> None:
        """Exit 1 with timeout message when git subprocess times out.

        TS-02-23 / 02-REQ-3.8
        """
        mock_client = _mock_hub_client()

        with (
            patch(
                "nightshift._carry_patch_startup.HubClient",
                create=True,
                return_value=mock_client,
            ),
            patch(
                "subprocess.run",
                side_effect=subprocess.TimeoutExpired(
                    cmd=["git", "remote", "get-url", "origin"],
                    timeout=10,
                ),
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_startup()

        assert exc_info.value.code == 1
        captured = capfd.readouterr()
        stderr_lower = captured.err.lower()
        assert "timed out" in stderr_lower or "timeout" in stderr_lower, (
            f"Expected stderr to mention timeout; got: {captured.err!r}"
        )

    def test_git_nonzero_exit_exits_with_stderr(
        self,
        capfd: pytest.CaptureFixture[str],
    ) -> None:
        """Exit 1 with captured git stderr when subprocess returns non-zero.

        TS-02-24 / 02-REQ-3.9
        """
        mock_client = _mock_hub_client()
        mock_git = _git_result(
            returncode=128,
            stderr=b"fatal: not a git repository",
        )

        with (
            patch(
                "nightshift._carry_patch_startup.HubClient",
                create=True,
                return_value=mock_client,
            ),
            patch(
                "subprocess.run",
                return_value=mock_git,
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_startup()

        assert exc_info.value.code == 1
        captured = capfd.readouterr()
        assert "fatal: not a git repository" in captured.err, (
            f"Expected git stderr to appear in output; got: {captured.err!r}"
        )


# ---------------------------------------------------------------------------
# TS-02-25, TS-02-26: origin URL comparison and validation success
# REQ-3.E1 (trailing whitespace), REQ-3.E5 (empty git_url)
# ---------------------------------------------------------------------------


class TestCwdValidationOriginUrl:
    """Verify origin URL comparison, mismatch exit behaviour, trailing
    whitespace handling, and the success path (logging.info).

    Requirements: 02-REQ-3.10, 02-REQ-3.11, 02-REQ-3.E1, 02-REQ-3.E5
    Test IDs: TS-02-25, TS-02-26
    """

    def test_origin_url_mismatch_exits(self, capfd: pytest.CaptureFixture[str]) -> None:
        """Exit 1 showing both URLs and a cd instruction when they differ.

        TS-02-25 / 02-REQ-3.10
        """
        workspace = _valid_workspace(
            git_url="https://git.example.com/correct-repo.git",
        )
        mock_client = _mock_hub_client(get_workspace_returns=workspace)
        mock_git = _git_result(
            returncode=0,
            stdout=b"https://git.example.com/wrong-repo.git\n",
        )

        with (
            patch(
                "nightshift._carry_patch_startup.HubClient",
                create=True,
                return_value=mock_client,
            ),
            patch(
                "subprocess.run",
                return_value=mock_git,
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_startup()

        assert exc_info.value.code == 1
        captured = capfd.readouterr()
        assert "https://git.example.com/wrong-repo.git" in captured.err, (
            f"Expected local URL in stderr; got: {captured.err!r}"
        )
        assert "https://git.example.com/correct-repo.git" in captured.err, (
            f"Expected workspace git_url in stderr; got: {captured.err!r}"
        )
        assert "cd" in captured.err, f"Expected 'cd' instruction in stderr; got: {captured.err!r}"

    def test_validation_success_logs_and_continues(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """logging.info emitted and execution continues when all checks pass.

        TS-02-26 / 02-REQ-3.11
        """
        mock_client = _mock_hub_client()
        mock_git = _git_result(
            returncode=0,
            stdout=_VALID_GIT_URL.encode() + b"\n",
        )

        with (
            patch(
                "nightshift._carry_patch_startup.HubClient",
                create=True,
                return_value=mock_client,
            ),
            patch(
                "subprocess.run",
                return_value=mock_git,
            ),
            caplog.at_level(logging.INFO),
        ):
            _run_startup()

        info_messages = [r.message for r in caplog.records if r.levelname == "INFO"]
        assert any("valid" in m.lower() or "success" in m.lower() or "pass" in m.lower() for m in info_messages), (
            f"Expected an INFO log indicating CWD validation succeeded; got messages: {info_messages}"
        )

    def test_trailing_whitespace_stripped_before_comparison(self) -> None:
        """Trailing whitespace/newline is stripped before URL comparison.

        02-REQ-3.E1: no false mismatch when git output has trailing newline.
        """
        mock_client = _mock_hub_client()
        # stdout has trailing whitespace — should still match
        mock_git = _git_result(
            returncode=0,
            stdout=_VALID_GIT_URL.encode() + b"\n  \n",
        )

        with (
            patch(
                "nightshift._carry_patch_startup.HubClient",
                create=True,
                return_value=mock_client,
            ),
            patch(
                "subprocess.run",
                return_value=mock_git,
            ),
        ):
            # Should NOT raise SystemExit — validation passes
            _run_startup()

    def test_empty_git_url_mismatch_exits(
        self,
        capfd: pytest.CaptureFixture[str],
    ) -> None:
        """Empty workspace git_url treated as mismatch against any local URL.

        02-REQ-3.E5
        """
        workspace = _valid_workspace(git_url="")
        mock_client = _mock_hub_client(get_workspace_returns=workspace)
        mock_git = _git_result(
            returncode=0,
            stdout=b"https://git.example.com/repo.git\n",
        )

        with (
            patch(
                "nightshift._carry_patch_startup.HubClient",
                create=True,
                return_value=mock_client,
            ),
            patch(
                "subprocess.run",
                return_value=mock_git,
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_startup()

        assert exc_info.value.code == 1
        captured = capfd.readouterr()
        assert "cd" in captured.err, f"Expected URL mismatch error with 'cd' instruction; got: {captured.err!r}"


# ---------------------------------------------------------------------------
# Helpers for config generation and variable init tests (TS-02-27 – TS-02-35)
# ---------------------------------------------------------------------------


def _successful_cwd_patches(
    mock_client: MagicMock,
    mock_git: MagicMock,
) -> contextmanager:
    """Context manager that patches HubClient construction and subprocess.run
    so that CWD validation succeeds, allowing tests to focus on behaviour
    that happens *after* validation (config generation, variable init).
    """
    from contextlib import ExitStack

    @contextmanager
    def _cm():
        with ExitStack() as stack:
            stack.enter_context(
                patch(
                    "nightshift._carry_patch_startup.HubClient",
                    create=True,
                    return_value=mock_client,
                )
            )
            stack.enter_context(
                patch(
                    "subprocess.run",
                    return_value=mock_git,
                )
            )
            yield

    return _cm()


def _passing_cwd_mocks(
    *,
    set_variable_side_effect: BaseException | list | None = None,
) -> tuple[MagicMock, MagicMock]:
    """Build a (mock_client, mock_git) pair that passes CWD validation.

    ``set_variable_side_effect`` is forwarded to
    ``mock_client.set_variable.side_effect`` for variable-init tests.
    """
    client = _mock_hub_client()
    if set_variable_side_effect is not None:
        client.set_variable.side_effect = set_variable_side_effect
    git = _git_result(returncode=0, stdout=_VALID_GIT_URL.encode() + b"\n")
    return client, git


# ---------------------------------------------------------------------------
# TS-02-27: Config written on first start (atomic write, sections present)
# ---------------------------------------------------------------------------


class TestConfigGenerationOnFirstStart:
    """Verify that .nightshift/config.toml is atomically written when absent.

    Requirements: 02-REQ-4.1, 02-REQ-4.E1
    Test ID: TS-02-27
    """

    def test_config_written_on_first_start(self, tmp_path: Path) -> None:
        """When .nightshift/config.toml does not exist in CWD, the startup
        helper creates it with [hub], [carry_patch], and [workspace] sections
        via an atomic temp-file rename.  The .tmp file must not remain.

        TS-02-27 / 02-REQ-4.1
        """
        mock_client, mock_git = _passing_cwd_mocks()
        config_path = tmp_path / ".nightshift" / "config.toml"
        tmp_file = tmp_path / ".nightshift" / "config.toml.tmp"

        with (
            _successful_cwd_patches(mock_client, mock_git),
            patch("os.getcwd", return_value=str(tmp_path)),
        ):
            _run_startup(hub_url=_VALID_HUB_URL, slug=_VALID_SLUG)

        assert config_path.exists(), ".nightshift/config.toml was not created on first start"
        content = config_path.read_text(encoding="utf-8")
        assert "[hub]" in content, f"Generated config missing [hub] section; content:\n{content}"
        assert "[carry_patch]" in content, f"Generated config missing [carry_patch] section; content:\n{content}"
        assert "[workspace]" in content, f"Generated config missing [workspace] section; content:\n{content}"
        assert "endpoint_url" in content, f"Generated config missing endpoint_url field; content:\n{content}"
        assert not tmp_file.exists(), ".nightshift/config.toml.tmp should not remain after successful write"

    def test_config_dir_already_exists(self, tmp_path: Path) -> None:
        """When .nightshift/ directory exists but config.toml is absent,
        config generation proceeds without error (no double-mkdir crash).

        02-REQ-4.E1
        """
        mock_client, mock_git = _passing_cwd_mocks()
        nightshift_dir = tmp_path / ".nightshift"
        nightshift_dir.mkdir()
        config_path = nightshift_dir / "config.toml"

        with (
            _successful_cwd_patches(mock_client, mock_git),
            patch("os.getcwd", return_value=str(tmp_path)),
        ):
            _run_startup(hub_url=_VALID_HUB_URL, slug=_VALID_SLUG)

        assert config_path.exists(), ".nightshift/config.toml was not created when directory already existed"


# ---------------------------------------------------------------------------
# TS-02-28: Config not overwritten if exists
# ---------------------------------------------------------------------------


class TestConfigGenerationSkipIfExists:
    """Verify that existing .nightshift/config.toml is never modified.

    Requirements: 02-REQ-4.2, 02-PROP-5
    Test ID: TS-02-28
    """

    def test_config_not_overwritten_if_exists(self, tmp_path: Path) -> None:
        """When .nightshift/config.toml already exists, config generation is
        skipped entirely and the existing file is unchanged.

        TS-02-28 / 02-REQ-4.2
        """
        mock_client, mock_git = _passing_cwd_mocks()
        nightshift_dir = tmp_path / ".nightshift"
        nightshift_dir.mkdir()
        config_path = nightshift_dir / "config.toml"
        original_content = "existing content"
        config_path.write_text(original_content)

        with (
            _successful_cwd_patches(mock_client, mock_git),
            patch("os.getcwd", return_value=str(tmp_path)),
        ):
            _run_startup(hub_url=_VALID_HUB_URL, slug=_VALID_SLUG)

        assert config_path.read_text() == original_content, (
            "Existing .nightshift/config.toml was modified during startup; "
            "config generation must be skipped when the file already exists"
        )


# ---------------------------------------------------------------------------
# TS-02-29: integration_branch = "" when workspace.integration_branch is None
# TS-02-E3: integration_branch = "<branch_name>" when non-empty
# ---------------------------------------------------------------------------


class TestConfigIntegrationBranch:
    """Verify integration_branch is written correctly in generated config.

    Requirements: 02-REQ-4.3, 02-REQ-4.E3
    Test IDs: TS-02-29, 02-REQ-4.E3
    """

    def test_integration_branch_none_writes_empty_string(
        self,
        tmp_path: Path,
    ) -> None:
        """When workspace.integration_branch is None, the generated config
        writes integration_branch = "" in the [workspace] section.

        TS-02-29 / 02-REQ-4.3
        """
        workspace = _valid_workspace()
        workspace.integration_branch = None  # type: ignore[attr-defined]
        mock_client = _mock_hub_client(get_workspace_returns=workspace)
        mock_git = _git_result(
            returncode=0,
            stdout=_VALID_GIT_URL.encode() + b"\n",
        )
        config_path = tmp_path / ".nightshift" / "config.toml"

        with (
            _successful_cwd_patches(mock_client, mock_git),
            patch("os.getcwd", return_value=str(tmp_path)),
        ):
            _run_startup(hub_url=_VALID_HUB_URL, slug=_VALID_SLUG)

        assert config_path.exists(), ".nightshift/config.toml was not created"
        content = config_path.read_text()
        assert 'integration_branch = ""' in content, f'Expected integration_branch = "" for None; content:\n{content}'

    def test_integration_branch_nonempty_writes_value(
        self,
        tmp_path: Path,
    ) -> None:
        """When workspace.integration_branch is a non-empty string, the
        generated config writes the actual branch name.

        02-REQ-4.E3
        """
        workspace = _valid_workspace()
        workspace.integration_branch = "develop"  # type: ignore[attr-defined]
        mock_client = _mock_hub_client(get_workspace_returns=workspace)
        mock_git = _git_result(
            returncode=0,
            stdout=_VALID_GIT_URL.encode() + b"\n",
        )
        config_path = tmp_path / ".nightshift" / "config.toml"

        with (
            _successful_cwd_patches(mock_client, mock_git),
            patch("os.getcwd", return_value=str(tmp_path)),
        ):
            _run_startup(hub_url=_VALID_HUB_URL, slug=_VALID_SLUG)

        assert config_path.exists(), ".nightshift/config.toml was not created"
        content = config_path.read_text()
        assert 'integration_branch = "develop"' in content, (
            f'Expected integration_branch = "develop"; content:\n{content}'
        )


# ---------------------------------------------------------------------------
# TS-02-30: Config write failure is non-fatal
# ---------------------------------------------------------------------------


class TestConfigWriteFailureNonFatal:
    """Verify that OS-level errors during config generation are handled
    gracefully with a warning log and continued startup.

    Requirements: 02-REQ-4.4, 02-REQ-4.E2
    Test ID: TS-02-30
    """

    def test_config_write_failure_logs_warning_and_continues(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """When writing .nightshift/config.toml raises an OS-level exception,
        the startup helper emits logging.warning and continues without exiting.

        TS-02-30 / 02-REQ-4.4
        """
        mock_client, mock_git = _passing_cwd_mocks()

        with (
            _successful_cwd_patches(mock_client, mock_git),
            patch("os.getcwd", return_value=str(tmp_path)),
            patch("os.rename", side_effect=PermissionError("Permission denied")),
            caplog.at_level(logging.WARNING),
        ):
            # Should NOT raise — startup continues despite the write failure
            _run_startup(hub_url=_VALID_HUB_URL, slug=_VALID_SLUG)

        warning_messages = [r.message for r in caplog.records if r.levelname == "WARNING"]
        assert any(
            "permission" in m.lower() or "failed" in m.lower() or "config" in m.lower() for m in warning_messages
        ), f"Expected a WARNING log about config write failure; got warnings: {warning_messages}"

    def test_rename_failure_leaves_tmp_and_continues(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """When the atomic rename step fails after writing the temp file,
        the .tmp file may remain on disk but nightshift continues.

        02-REQ-4.E2
        """
        mock_client, mock_git = _passing_cwd_mocks()
        nightshift_dir = tmp_path / ".nightshift"
        nightshift_dir.mkdir()

        original_rename = os.rename

        def _failing_rename(src: str, dst: str) -> None:
            # Only fail the config.toml rename, not other renames
            if "config.toml" in str(dst) and not str(dst).endswith(".tmp"):
                raise OSError("Simulated rename failure")
            original_rename(src, dst)

        with (
            _successful_cwd_patches(mock_client, mock_git),
            patch("os.getcwd", return_value=str(tmp_path)),
            patch("os.rename", side_effect=_failing_rename),
            caplog.at_level(logging.WARNING),
        ):
            # Should NOT raise — startup continues despite rename failure
            _run_startup(hub_url=_VALID_HUB_URL, slug=_VALID_SLUG)

        warning_messages = [r.message for r in caplog.records if r.levelname == "WARNING"]
        assert len(warning_messages) >= 1, "Expected at least one WARNING log about the rename failure"


# ---------------------------------------------------------------------------
# TS-02-31: PAT is never written to the generated config
# ---------------------------------------------------------------------------


class TestConfigPatExclusion:
    """Verify that the PAT / token value is never persisted in the config file.

    Requirements: 02-REQ-4.5, 02-PROP-6
    Test ID: TS-02-31
    """

    def test_pat_never_written_to_config(self, tmp_path: Path) -> None:
        """The generated .nightshift/config.toml must not contain the PAT
        or token value under any circumstances.

        TS-02-31 / 02-REQ-4.5
        """
        mock_client, mock_git = _passing_cwd_mocks()
        secret_pat = "super-secret-token-value-xyzzy"
        config_path = tmp_path / ".nightshift" / "config.toml"

        with (
            _successful_cwd_patches(mock_client, mock_git),
            patch("os.getcwd", return_value=str(tmp_path)),
        ):
            _run_startup(
                hub_url=_VALID_HUB_URL,
                pat=secret_pat,
                slug=_VALID_SLUG,
            )

        assert config_path.exists(), ".nightshift/config.toml was not created"
        content = config_path.read_text()
        assert secret_pat not in content, (
            f"PAT value '{secret_pat}' found in generated config file; PAT must never be persisted to disk"
        )
        # Also check for common token/pat key names with the secret value
        assert "super-secret" not in content, "PAT value fragment found in generated config"


# ---------------------------------------------------------------------------
# TS-02-32: In-memory config unchanged after generation
# ---------------------------------------------------------------------------


class TestConfigNoReload:
    """Verify nightshift does not reload the newly-written config into memory.

    Requirements: 02-REQ-4.6
    Test ID: TS-02-32
    """

    def test_in_memory_config_unchanged_after_generation(
        self,
        tmp_path: Path,
    ) -> None:
        """The in-memory AgentFoxConfig instance must remain unchanged after
        config file generation. Nightshift must not reload the written file.

        TS-02-32 / 02-REQ-4.6
        """
        mock_client, mock_git = _passing_cwd_mocks()
        config = _make_config(
            hub_endpoint_url=_VALID_HUB_URL,
            carry_patch_workspace="original-slug",
        )

        with (
            _successful_cwd_patches(mock_client, mock_git),
            patch("os.getcwd", return_value=str(tmp_path)),
        ):
            asyncio.run(
                startup_helper(
                    hub_url=_VALID_HUB_URL,
                    pat=_VALID_PAT,
                    slug="different-slug",
                    config=config,
                )
            )

        # The in-memory config must still have 'original-slug'
        assert config.carry_patch.workspace == "original-slug", (
            "In-memory config.carry_patch.workspace was changed after "
            "config file generation; nightshift must not reload the config"
        )


# ---------------------------------------------------------------------------
# TS-02-33: set_variable called with correct arguments
# ---------------------------------------------------------------------------


class TestSetVariableCorrectArgs:
    """Verify the startup helper calls set_variable for both auto-rebuild
    variables with the correct arguments, in the correct order.

    Requirements: 02-REQ-5.1, 02-PROP-8
    Test ID: TS-02-33
    """

    def test_set_variable_called_with_correct_args(
        self,
        tmp_path: Path,
    ) -> None:
        """set_variable is called twice: first with AUTO_REBUILD_AFTER_SYNC,
        then with AUTO_REBUILD_AFTER_PUSH, both set to 'false'.

        TS-02-33 / 02-REQ-5.1
        """
        mock_client, mock_git = _passing_cwd_mocks()

        with (
            _successful_cwd_patches(mock_client, mock_git),
            patch("os.getcwd", return_value=str(tmp_path)),
        ):
            _run_startup(slug=_VALID_SLUG)

        calls = mock_client.set_variable.call_args_list
        assert len(calls) == 2, f"Expected exactly 2 set_variable calls; got {len(calls)}: {calls}"
        assert calls[0].args == (_VALID_SLUG, "AUTO_REBUILD_AFTER_SYNC", "false") or (
            calls[0].args[0] == _VALID_SLUG
            and calls[0].args[1] == "AUTO_REBUILD_AFTER_SYNC"
            and calls[0].args[2] == "false"
        ), f"First set_variable call has wrong args: {calls[0]}"
        assert calls[1].args == (_VALID_SLUG, "AUTO_REBUILD_AFTER_PUSH", "false") or (
            calls[1].args[0] == _VALID_SLUG
            and calls[1].args[1] == "AUTO_REBUILD_AFTER_PUSH"
            and calls[1].args[2] == "false"
        ), f"Second set_variable call has wrong args: {calls[1]}"

    def test_single_hubclient_reused_for_all_calls(
        self,
        tmp_path: Path,
    ) -> None:
        """A single HubClient instance is constructed and reused for
        get_workspace and both set_variable calls.

        02-PROP-8
        """
        mock_client, mock_git = _passing_cwd_mocks()

        with (
            patch(
                "nightshift._carry_patch_startup.HubClient",
                create=True,
                return_value=mock_client,
            ) as mock_cls,
            patch("subprocess.run", return_value=mock_git),
            patch("os.getcwd", return_value=str(tmp_path)),
        ):
            _run_startup(slug=_VALID_SLUG)

        # Exactly one HubClient constructed
        assert mock_cls.call_count == 1, f"Expected exactly 1 HubClient construction; got {mock_cls.call_count}"
        # Same instance used for get_workspace and set_variable
        assert mock_client.get_workspace.call_count >= 1, "get_workspace was not called on the HubClient instance"
        assert mock_client.set_variable.call_count == 2, (
            "set_variable was not called exactly twice on the same HubClient"
        )


# ---------------------------------------------------------------------------
# TS-02-34: set_variable exception is non-fatal
# ---------------------------------------------------------------------------


class TestSetVariableExceptionNonFatal:
    """Verify that exceptions from set_variable are caught and logged as
    warnings without aborting startup.

    Requirements: 02-REQ-5.2, 02-REQ-5.E1, 02-PROP-7
    Test ID: TS-02-34
    """

    def test_both_set_variable_raise_forbidden_continues(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """When both set_variable calls raise HubForbiddenError, the startup
        helper logs warnings and continues — nightshift does not exit.

        TS-02-34 / 02-REQ-5.2
        """
        err = HubForbiddenError(
            status_code=403,
            message="insufficient scope",
            error_type="forbidden",
        )
        mock_client, mock_git = _passing_cwd_mocks(
            set_variable_side_effect=err,
        )

        with (
            _successful_cwd_patches(mock_client, mock_git),
            patch("os.getcwd", return_value=str(tmp_path)),
            caplog.at_level(logging.WARNING),
        ):
            # Should NOT raise — startup continues despite set_variable failures
            _run_startup(slug=_VALID_SLUG)

        warning_messages = [r.message for r in caplog.records if r.levelname == "WARNING"]
        assert len(warning_messages) >= 1, "Expected at least one WARNING log for set_variable failure"

    def test_connection_error_is_nonfatal(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """When set_variable raises HubConnectionError, the startup helper
        logs a warning and continues.

        02-REQ-5.2 (HubConnectionError variant)
        """
        err = HubConnectionError(
            status_code=0,
            message="Connection refused",
            error_type="connection_error",
        )
        mock_client, mock_git = _passing_cwd_mocks(
            set_variable_side_effect=err,
        )

        with (
            _successful_cwd_patches(mock_client, mock_git),
            patch("os.getcwd", return_value=str(tmp_path)),
            caplog.at_level(logging.WARNING),
        ):
            _run_startup(slug=_VALID_SLUG)

        warning_messages = [r.message for r in caplog.records if r.levelname == "WARNING"]
        assert len(warning_messages) >= 1, "Expected WARNING log for HubConnectionError in set_variable"

    def test_first_set_variable_fails_second_still_attempted(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """When the first set_variable call (AUTO_REBUILD_AFTER_SYNC) raises,
        the second call (AUTO_REBUILD_AFTER_PUSH) is still attempted.

        02-REQ-5.E1
        """
        call_count = {"n": 0}
        err = HubForbiddenError(
            status_code=403,
            message="insufficient scope",
            error_type="forbidden",
        )

        async def _set_variable_track(*args, **kwargs):  # noqa: ARG001
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise err
            # Second call succeeds

        mock_client, mock_git = _passing_cwd_mocks()
        mock_client.set_variable = AsyncMock(side_effect=_set_variable_track)

        with (
            _successful_cwd_patches(mock_client, mock_git),
            patch("os.getcwd", return_value=str(tmp_path)),
            caplog.at_level(logging.WARNING),
        ):
            _run_startup(slug=_VALID_SLUG)

        assert call_count["n"] == 2, (
            f"Expected 2 set_variable calls (both attempted independently); got {call_count['n']}"
        )
        # At least one warning for the first failure
        warning_messages = [r.message for r in caplog.records if r.levelname == "WARNING"]
        assert len(warning_messages) >= 1, "Expected WARNING for first set_variable failure"


# ---------------------------------------------------------------------------
# TS-02-35: Startup helper returns, DaemonRunner.run() invoked
# ---------------------------------------------------------------------------


class TestStartupHelperReturnsHubClient:
    """Verify the async startup helper returns the HubClient to main(),
    which then invokes DaemonRunner.run().

    Requirements: 02-REQ-5.3
    Test ID: TS-02-35
    """

    def test_startup_helper_returns_hub_client(
        self,
        tmp_path: Path,
    ) -> None:
        """The async startup helper returns the constructed HubClient instance
        after completing CWD validation and variable initialization.

        TS-02-35 / 02-REQ-5.3
        """
        mock_client, mock_git = _passing_cwd_mocks()

        with (
            patch(
                "nightshift._carry_patch_startup.HubClient",
                create=True,
                return_value=mock_client,
            ),
            patch("subprocess.run", return_value=mock_git),
            patch("os.getcwd", return_value=str(tmp_path)),
        ):
            result = asyncio.run(
                startup_helper(
                    hub_url=_VALID_HUB_URL,
                    pat=_VALID_PAT,
                    slug=_VALID_SLUG,
                    config=MagicMock(),
                )
            )

        # The startup helper must return the HubClient instance
        assert result is mock_client, (
            f"Expected startup_helper to return the HubClient instance; got {type(result).__name__}: {result!r}"
        )
