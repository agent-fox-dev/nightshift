"""Startup migration and summary helpers extracted from engine/run.py and session_lifecycle.py."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def compose_enriched_summary(
    summary: str,
    rejected_approaches: list[dict[str, str]] | None = None,
    gotchas: list[str] | None = None,
    assumptions: list[str] | None = None,
) -> str:
    """Merge structured session-summary fields into a single enriched text."""
    sections: list[str] = []

    if summary:
        sections.append(summary)

    if rejected_approaches:
        for entry in rejected_approaches:
            if not isinstance(entry, dict):
                continue
            approach = entry.get("approach")
            reason = entry.get("reason")
            if approach and reason:
                sections.append(f"Tried: {approach} — rejected because: {reason}")

    if gotchas:
        for gotcha in gotchas:
            if gotcha:
                sections.append(f"Watch out: {gotcha}")

    if assumptions:
        for assumption in assumptions:
            if assumption:
                sections.append(f"Assumes: {assumption}")

    return "\n".join(sections)


def run_startup_migrations(
    knowledge_db: Any,
    specs_path: Path,
    project_root: Path,
) -> None:
    """Run legacy file migrations at orchestrator startup.

    Migrates legacy review.md/verification.md files into DuckDB using the
    read-write connection, before any sessions are dispatched.

    Errors on individual specs are logged and skipped -- they do not abort
    the startup sequence.
    """
    from agentfox.session.context import _migrate_legacy_files

    conn = knowledge_db.connection

    if specs_path.is_dir():
        for spec_dir in sorted(specs_path.iterdir()):
            if not spec_dir.is_dir():
                continue
            spec_name = spec_dir.name
            try:
                _migrate_legacy_files(conn, spec_dir, spec_name)
            except Exception:
                logger.warning(
                    "Failed to migrate legacy files for spec %s, continuing",
                    spec_name,
                    exc_info=True,
                )
