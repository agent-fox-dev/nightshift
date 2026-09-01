"""Tests for CLI flag 3-tier resolution in nightshift carry-patch mode.

Tests verify that hub URL, workspace slug, and PAT are resolved in priority
order: CLI flag > environment variable > config file. They also verify the
conditions under which carry-patch mode is activated or skipped.

These are initially failing tests (group 2). They will pass once the CLI
flags (--hub-url, --workspace, --token) and 3-tier resolution logic are
implemented in nightshift.app (groups 5-6).

Specification: 02_carry_patch_bootstrap
Test IDs: TS-02-7, TS-02-8, TS-02-9, TS-02-10, TS-02-11, TS-02-12,
          TS-02-13, TS-02-14, TS-02-15
Requirements: 02-REQ-2
"""

from __future__ import annotations

import os
from collections.abc import Generator
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from click.testing import CliRunner
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
        assert mock_resolve_url.call_count == 1, (
            "resolve_hub_url was not called; hub URL resolution logic is missing"
        )
        called_with = mock_resolve_url.call_args
        assert called_with is not None
        # The CLI flag value must be forwarded to resolve_hub_url
        all_passed = list(called_with.args) + list(called_with.kwargs.values())
        assert "https://hub.example.com" in str(all_passed), (
            f"resolve_hub_url was not called with the CLI flag value. "
            f"Call args: {called_with}"
        )
        assert mock_startup.call_count == 1, (
            "Async startup helper was not called; carry-patch mode was not activated"
        )

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
        assert mock_resolve_url.call_count == 1, (
            "resolve_hub_url was not called; hub URL resolution logic is missing"
        )
        assert mock_startup.call_count == 1, (
            "Async startup helper was not called; carry-patch mode was not activated"
        )

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
        assert mock_resolve_url.call_count == 1, (
            "resolve_hub_url was not called; hub URL resolution logic is missing"
        )
        called_with = mock_resolve_url.call_args
        assert called_with is not None
        # The config URL must be forwarded to resolve_hub_url
        all_passed = list(called_with.args) + list(called_with.kwargs.values())
        assert "https://config.hub.example.com" in str(all_passed), (
            f"resolve_hub_url was not called with config.hub.endpoint_url. "
            f"Call args: {called_with}"
        )
        assert mock_startup.call_count == 1, (
            "Async startup helper was not called; carry-patch mode was not activated"
        )

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
                    "--hub-url", "https://hub.example.com",
                    "--workspace", "flag-slug",
                    "--token", "myPAT",
                ],
            )

        assert result.exit_code == 0, (
            f"Expected exit 0 when --workspace flag is provided; got {result.exit_code}.\n"
            f"stdout: {result.output!r}\nstderr: {result.stderr!r}"
        )
        assert mock_startup.call_count == 1, (
            "Async startup helper was not called; carry-patch mode was not activated"
        )
        called_with = mock_startup.call_args
        assert called_with is not None
        # The flag slug must be used, not env-slug or config-slug
        all_passed = list(called_with.args) + list(called_with.kwargs.values())
        assert "flag-slug" in str(all_passed), (
            f"Startup helper was not called with the flag slug 'flag-slug'. "
            f"Call args: {called_with}"
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
        assert mock_startup.call_count == 1, (
            "Async startup helper was not called; carry-patch mode was not activated"
        )
        called_with = mock_startup.call_args
        assert called_with is not None
        all_passed = list(called_with.args) + list(called_with.kwargs.values())
        assert "env-slug" in str(all_passed), (
            f"Startup helper was not called with AF_WORKSPACE value 'env-slug'. "
            f"Call args: {called_with}"
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
        assert mock_startup.call_count == 1, (
            "Async startup helper was not called; carry-patch mode was not activated"
        )
        called_with = mock_startup.call_args
        assert called_with is not None
        all_passed = list(called_with.args) + list(called_with.kwargs.values())
        assert "config-slug" in str(all_passed), (
            f"Startup helper was not called with config.carry_patch.workspace value. "
            f"Call args: {called_with}"
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
        assert "hub" in stderr_lower and (
            "url" in stderr_lower or "required" in stderr_lower
        ), (
            f"Expected error message mentioning hub URL in stderr; got: {result.stderr!r}"
        )
