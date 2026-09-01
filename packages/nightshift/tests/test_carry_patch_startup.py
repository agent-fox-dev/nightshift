"""Tests for CLI flag 3-tier resolution and CWD validation in nightshift carry-patch mode.

Tests verify that hub URL, workspace slug, and PAT are resolved in priority
order: CLI flag > environment variable > config file. They also verify the
conditions under which carry-patch mode is activated or skipped.

CWD validation tests (TS-02-16 through TS-02-26) verify the async startup
helper: HubClient construction, workspace state checks, git subprocess
invocation, origin URL matching, and logging on success.

These are initially failing tests (groups 2–3). They will pass once the CLI
flags (--hub-url, --workspace, --token), 3-tier resolution logic, and the
async startup helper are implemented (groups 5–9).

Specification: 02_carry_patch_bootstrap
Test IDs: TS-02-7 through TS-02-15 (CLI resolution),
          TS-02-16 through TS-02-26 (CWD validation)
Requirements: 02-REQ-2, 02-REQ-3
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from collections.abc import Generator
from contextlib import contextmanager
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
        "workspace_mode": "carry_patch",
        "clone_status": "ready",
        "git_url": _VALID_GIT_URL,
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
            f"Expected HubClient to be constructed exactly once; "
            f"got {mock_cls.call_count} calls"
        )

        # Constructed with the correct keyword arguments
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs.get("endpoint_url") == _VALID_HUB_URL, (
            f"Expected endpoint_url={_VALID_HUB_URL!r}; got {call_kwargs}"
        )
        assert call_kwargs.get("pat") == _VALID_PAT, (
            f"Expected pat={_VALID_PAT!r}; got {call_kwargs}"
        )

        # get_workspace called once with the slug
        assert mock_client.get_workspace.call_count == 1, (
            f"Expected get_workspace to be called once; "
            f"got {mock_client.get_workspace.call_count}"
        )
        assert mock_client.get_workspace.call_args[0][0] == _VALID_SLUG, (
            f"Expected get_workspace to be called with {_VALID_SLUG!r}; "
            f"got {mock_client.get_workspace.call_args}"
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
        assert "pending" in captured.err, (
            f"Expected stderr to show clone_status 'pending'; got: {captured.err!r}"
        )

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
        assert "failed" in captured.err, (
            f"Expected stderr to show clone_status 'failed'; got: {captured.err!r}"
        )


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
        assert captured.err.strip() == (
            "git is not installed or not in PATH; nightshift requires git"
        ), f"Expected exact git-not-found message; got: {captured.err!r}"

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
        assert "cd" in captured.err, (
            f"Expected 'cd' instruction in stderr; got: {captured.err!r}"
        )

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

        info_messages = [
            r.message for r in caplog.records if r.levelname == "INFO"
        ]
        assert any(
            "valid" in m.lower() or "success" in m.lower() or "pass" in m.lower()
            for m in info_messages
        ), (
            f"Expected an INFO log indicating CWD validation succeeded; "
            f"got messages: {info_messages}"
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
        assert "cd" in captured.err, (
            f"Expected URL mismatch error with 'cd' instruction; got: {captured.err!r}"
        )
