"""Carry-patch startup — CWD validation, config generation, and variable init.

Implements REQ-3 (CWD validation sequence), REQ-4 (config generation),
and REQ-5 (workspace variable initialization) from spec
02_carry_patch_bootstrap.
"""

from __future__ import annotations

import logging
import os
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
    6. Set workspace variables (AUTO_REBUILD_AFTER_SYNC/PUSH) — REQ-5.

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

    # -- Step 7: Generate default config if absent (REQ-4) ------------------
    _maybe_generate_config(hub_url=hub_url, slug=slug, workspace=workspace)

    # -- Step 8: Set workspace variables (REQ-5) ---------------------------
    # Each call is wrapped independently so that a failure in one does not
    # prevent the other from being attempted (02-REQ-5.E1).  Any exception
    # is non-fatal — we log a warning and continue (02-REQ-5.2, 02-PROP-7).
    for var_name in ("AUTO_REBUILD_AFTER_SYNC", "AUTO_REBUILD_AFTER_PUSH"):
        try:
            await hub_client.set_variable(slug, var_name, "false")
        except Exception as exc:
            logger.warning(
                "Failed to set workspace variable %s: %s",
                var_name,
                exc,
            )

    return hub_client


def _maybe_generate_config(
    *,
    hub_url: str,
    slug: str,
    workspace: object,
) -> None:
    """Generate ``.nightshift/config.toml`` if it does not already exist.

    The file is written atomically via a ``.tmp`` intermediate and then
    renamed.  Any OS-level error is logged as a warning and does **not**
    abort startup.

    Requirements: 02-REQ-4.1, 02-REQ-4.2, 02-REQ-4.3, 02-REQ-4.4,
                  02-REQ-4.5, 02-REQ-4.E1, 02-REQ-4.E2
    """
    cwd = os.getcwd()
    config_dir = os.path.join(cwd, ".nightshift")
    config_path = os.path.join(config_dir, "config.toml")
    tmp_path = os.path.join(config_dir, "config.toml.tmp")

    # REQ-4.2: skip entirely if config already exists
    if os.path.exists(config_path):
        return

    # Build TOML content — PAT is never written (REQ-4.5)
    integration_branch = getattr(workspace, "integration_branch", None)
    if integration_branch is None:
        integration_branch = ""

    content = (
        f"[hub]\n"
        f'endpoint_url = "{hub_url}"\n'
        f"\n"
        f"[carry_patch]\n"
        f"enabled = true\n"
        f'workspace = "{slug}"\n'
        f"check_interval = 300\n"
        f"auto_resolve = true\n"
        f"rebuild_timeout = 600\n"
        f"rebuild_poll_interval = 5\n"
        f"max_resolve_retries = 2\n"
        f"\n"
        f"[workspace]\n"
        f'integration_branch = "{integration_branch}"\n'
        f'merge_strategy = "direct"\n'
    )

    try:
        # REQ-4.E1: create directory if absent; no-op if it already exists
        os.makedirs(config_dir, exist_ok=True)

        # Write to temp file first (UTF-8)
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(content)

        # Atomic rename — REQ-4.1
        os.rename(tmp_path, config_path)
    except OSError as exc:
        # REQ-4.4, REQ-4.E2: non-fatal — log warning and continue
        logger.warning("Failed to write .nightshift/config.toml: %s", exc)
