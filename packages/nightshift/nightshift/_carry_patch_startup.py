"""Carry-patch startup — CWD validation and workspace variable init.

Stub — implementation pending (spec 02, groups 7–9).
"""

from __future__ import annotations


async def startup_helper(
    *,
    hub_url: str,
    pat: str,
    slug: str,
    config: object,
) -> object:
    """Validate CWD against the hub workspace and initialise variables.

    Steps performed (once implemented):
    1. Construct a HubClient and call get_workspace(slug).
    2. Validate workspace_mode == 'carry_patch'.
    3. Validate clone_status == 'ready'.
    4. Read local git origin URL via subprocess.
    5. Compare against workspace.git_url.
    6. Set workspace variables (AUTO_REBUILD_AFTER_SYNC/PUSH).

    Raises NotImplementedError until implementation is complete.
    """
    raise NotImplementedError
