"""Carry-patch startup — CWD validation and workspace variable init.

Implements REQ-3 (CWD validation sequence) from spec 02_carry_patch_bootstrap.
REQ-4 (config generation) and REQ-5 (variable init) pending groups 8–9.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

import click
from afhub.client import HubClient
from afhub.errors import (
    HubAuthError,
    HubConnectionError,
    HubForbiddenError,
    HubNotFoundError,
)

logger = logging.getLogger(__name__)


async def startup_helper(
    *,
    hub_url: str,
    pat: str,
    slug: str,
    config: object,  # noqa: ARG001
) -> HubClient:
    """Validate CWD against the hub workspace and initialise variables.

    Steps performed:
    1. Construct a HubClient and call get_workspace(slug).
    2. Validate workspace_mode == 'carry_patch'.
    3. Validate clone_status == 'ready'.
    4. Read local git origin URL via subprocess.
    5. Compare against workspace.git_url.
    6. Set workspace variables (AUTO_REBUILD_AFTER_SYNC/PUSH) — pending REQ-5.

    Returns the HubClient instance for reuse by the daemon.
    """
    # -- Step 1: Construct HubClient and fetch workspace (REQ-3.1) -----------
    hub_client = HubClient(endpoint_url=hub_url, pat=pat)

    try:
        workspace = await hub_client.get_workspace(slug)
    except HubAuthError:
        click.echo(
            "Error: invalid PAT or insufficient permissions — "
            "check your --token / AF_HUB_TOKEN value",
            err=True,
        )
        sys.exit(1)
    except HubForbiddenError:
        click.echo(
            "Error: PAT has insufficient scope or permissions "
            "for this workspace",
            err=True,
        )
        sys.exit(1)
    except HubNotFoundError:
        click.echo(
            f"Error: workspace slug {slug!r} not found on the hub",
            err=True,
        )
        sys.exit(1)
    except HubConnectionError:
        click.echo(
            "Error: could not connect to the hub — "
            "check your network connection and hub URL",
            err=True,
        )
        sys.exit(1)

    # -- Step 2: Validate workspace_mode (REQ-3.4) --------------------------
    if workspace.workspace_mode != "carry_patch":
        click.echo(
            f"Error: workspace is not in carry-patch mode "
            f"(workspace_mode={workspace.workspace_mode!r})",
            err=True,
        )
        sys.exit(1)

    # -- Step 3: Validate clone_status (REQ-3.5) ----------------------------
    if workspace.clone_status != "ready":
        click.echo(
            f"Error: workspace clone is not ready "
            f"(clone_status={workspace.clone_status!r})",
            err=True,
        )
        sys.exit(1)

    # -- Step 4: Read local git origin URL (REQ-3.6–3.9) --------------------
    try:
        result = subprocess.run(  # noqa: UP022
            ["git", "remote", "get-url", "origin"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
            cwd=Path.cwd(),
        )
    except FileNotFoundError:
        click.echo(
            "git is not installed or not in PATH; nightshift requires git",
            err=True,
        )
        sys.exit(1)
    except subprocess.TimeoutExpired:
        click.echo(
            "Error: git command timed out after 10 seconds",
            err=True,
        )
        sys.exit(1)

    if result.returncode != 0:
        git_stderr = result.stderr.decode("utf-8", errors="replace")
        click.echo(
            f"Error: git remote get-url origin failed: {git_stderr}",
            err=True,
        )
        sys.exit(1)

    # -- Step 5: Compare origin URLs (REQ-3.10, REQ-3.E1) -------------------
    local_url = result.stdout.decode("utf-8", errors="replace").rstrip()

    if local_url != workspace.git_url:
        click.echo(
            f"Error: local origin URL {local_url!r} does not match "
            f"workspace git_url {workspace.git_url!r}. "
            f"cd into the correct directory.",
            err=True,
        )
        sys.exit(1)

    # -- Step 6: Validation succeeded (REQ-3.11) ----------------------------
    logger.info("CWD validation succeeded for workspace %s", slug)

    return hub_client
