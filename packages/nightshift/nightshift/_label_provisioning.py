"""Startup label provisioning: verify and create required platform labels.

NS-REQ-2: On startup, verify all required labels are present and create missing ones.
NS-REQ-3: If labels cannot be created, exit with a clear explanation.
"""

from __future__ import annotations

import asyncio
import logging
import sys

import click

logger = logging.getLogger(__name__)


def ensure_labels(platform: object) -> None:
    """Ensure all required labels exist on the platform.

    Attempts to create each required label.  Labels that already exist
    are silently skipped (HTTP 422 / "already_exists" errors).  Any other
    failure causes an immediate exit with a human-readable explanation.

    Requirements: NS-REQ-2, NS-REQ-3
    """
    from afissues.errors import IntegrationError
    from afissues.labels import REQUIRED_LABELS

    async def _provision() -> None:
        for spec in REQUIRED_LABELS:
            try:
                await platform.create_label(spec.name, spec.color, spec.description)  # type: ignore[union-attr]
                logger.info("Created label: %s", spec.name)
            except IntegrationError as exc:
                exc_msg = str(exc).lower()
                # GitHub returns 422 when a label already exists.
                # Gitea/GitLab may return similar responses.
                if "422" in exc_msg or "already_exists" in exc_msg or "already exists" in exc_msg:
                    logger.debug("Label already exists: %s", spec.name)
                    continue
                click.echo(
                    f"Error: could not create label '{spec.name}' — {exc}",
                    err=True,
                )
                sys.exit(1)

    asyncio.run(_provision())
