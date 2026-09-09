"""Startup label provisioning: verify and create required platform labels.

NS-REQ-2: On startup, verify all required labels are present and create missing ones.
NS-REQ-3: If labels cannot be created, exit with a clear explanation.
"""

from __future__ import annotations

import asyncio
import logging
import re
import sys

import click

logger = logging.getLogger(__name__)

_COLOR_RE = re.compile(r"[0-9a-fA-F]{6}")


def _normalize_color(raw: str) -> str:
    """Strip a leading '#' from a hex colour string if present."""
    return raw.lstrip("#")


def ensure_labels(platform: object) -> None:
    """Ensure all required labels exist on the platform.

    Validates each label colour, then delegates creation to the platform.
    The platform layer is responsible for idempotency (silently succeeding
    when a label already exists).  Any ``IntegrationError`` from the
    platform is treated as a fatal startup failure.

    Requirements: NS-REQ-2, NS-REQ-3
    """
    from afissues.errors import IntegrationError
    from afissues.labels import REQUIRED_LABELS

    # Validate colours before attempting any network calls.
    for spec in REQUIRED_LABELS:
        color = _normalize_color(spec.color)
        if not _COLOR_RE.fullmatch(color):
            click.echo(
                f"Error: label '{spec.name}' has invalid color '{spec.color}' "
                f"(expected 6-character hex without leading '#')",
                err=True,
            )
            sys.exit(1)

    async def _provision() -> None:
        for spec in REQUIRED_LABELS:
            color = _normalize_color(spec.color)
            try:
                await platform.create_label(spec.name, color, spec.description)  # type: ignore[union-attr]
                logger.info("Created label: %s", spec.name)
            except IntegrationError as exc:
                click.echo(
                    f"Error: could not create label '{spec.name}' — {exc}",
                    err=True,
                )
                sys.exit(1)

    asyncio.run(_provision())
