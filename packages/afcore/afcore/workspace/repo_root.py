"""Repository root resolution.

The repository root is resolved once at startup and threaded explicitly
through the engine, the fix pipeline and the carry-patch monitor rather
than being re-derived from the process working directory at each git
call site.

Requirements: NS-REQ-6 (issue #43)
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# Timeout for the `git rev-parse` probe (seconds).  The command is local
# and near-instant; the bound exists only to avoid hanging startup.
_REV_PARSE_TIMEOUT = 10


def resolve_repo_root(start: Path | None = None) -> Path:
    """Resolve the git work-tree root containing *start*.

    Runs ``git rev-parse --show-toplevel`` so that launching from a
    subdirectory of the repository yields the repository root rather
    than the subdirectory.  Falls back to *start* itself (resolved) when
    git is unavailable or *start* is not inside a work tree — the daemon
    reports that condition through its own prerequisite checks.

    *start* defaults to the process working directory.  This is the one
    place the working directory is consulted; every other consumer takes
    the resolved root as an explicit value.

    Requirements: NS-REQ-6 (issue #43)
    """
    base = Path(start) if start is not None else Path.cwd()
    try:
        result = subprocess.run(  # noqa: S603
            ["git", "rev-parse", "--show-toplevel"],  # noqa: S607
            cwd=base,
            capture_output=True,
            timeout=_REV_PARSE_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        logger.debug("git rev-parse --show-toplevel failed for %s", base, exc_info=True)
        return base.resolve()

    if result.returncode != 0:
        logger.debug(
            "git rev-parse --show-toplevel returned %d for %s; using it as the root",
            result.returncode,
            base,
        )
        return base.resolve()

    top = result.stdout.decode("utf-8", errors="replace").strip()
    return Path(top).resolve() if top else base.resolve()
