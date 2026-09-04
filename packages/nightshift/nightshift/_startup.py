"""Daemon startup helpers — knowledge store, migrations, progress bridge."""

from __future__ import annotations

import logging
import os
import sys

logger = logging.getLogger(__name__)


def check_root_permission_mode(config) -> None:
    """Fail fast when running as root with bypassPermissions (#11).

    Claude Code's CLI rejects ``--dangerously-skip-permissions`` when the
    effective UID is 0.  Rather than letting every session fail with opaque
    transport retries, detect the misconfiguration at daemon startup and
    exit with a clear, actionable error message.
    """
    try:
        is_root = os.getuid() == 0
    except AttributeError:
        # Windows — root-restriction is POSIX-only
        return

    if not is_root:
        return

    mode = getattr(config.security, "permission_mode", "bypassPermissions")
    if mode == "bypassPermissions":
        logger.critical(
            "Night Shift is running as root (UID 0) with "
            "permission_mode='bypassPermissions'. Claude Code rejects "
            "--dangerously-skip-permissions for root/sudo. "
            "Set permission_mode = 'acceptEdits' in the [security] section "
            "of config.toml to run as root."
        )
        print(
            "Error: cannot run as root with permission_mode='bypassPermissions'. "
            "Set permission_mode = 'acceptEdits' in [security] of config.toml.",
            file=sys.stderr,
        )
        sys.exit(1)


def init_knowledge(config, project_root):
    """Open knowledge store, run migrations. Returns (db, sink, provider)."""
    kdb = sink = kprov = None
    try:
        from afaudit.sink import SinkDispatcher
        from afcore.knowledge.db import open_knowledge_store
        from afcore.knowledge.duckdb_sink import DuckDBSink
        from afcore.knowledge.fox_provider import FoxKnowledgeProvider

        kdb = open_knowledge_store(config.knowledge, read_only=False)
        sink = SinkDispatcher([DuckDBSink(kdb.connection)])
        kprov = FoxKnowledgeProvider(kdb, config.knowledge.provider)
    except Exception:
        logger.warning("Failed to open knowledge store", exc_info=True)
        return None, None, None
    # Run legacy migrations at startup via the canonical helper.
    from afcore.core.config import resolve_spec_root
    from afcore.engine.migrations import run_startup_migrations

    specs = resolve_spec_root(config, project_root)
    try:
        run_startup_migrations(kdb, specs, project_root)
    except Exception:
        logger.warning("Startup migrations failed", exc_info=True)
    return kdb, sink, kprov


def wrap_task_callback(progress, om):
    """Bridge UI task events to JSONL when ``om.json_mode`` is active."""
    if not om.json_mode:
        return progress.task_callback
    from afcore.io.progress import ProgressDisplay as JsonlProgress

    jl = JsonlProgress(output_manager=om, json_mode=True)
    ui_cb = progress.task_callback

    def _cb(event):
        ui_cb(event)
        nid = getattr(event, "node_id", None)
        status = getattr(event, "status", "")
        if status == "completed":
            jl.task_started(node_id=nid)
            jl.task_completed(node_id=nid)
        elif status == "failed":
            jl.task_failed(node_id=nid, error=getattr(event, "error_message", "") or "")
        else:
            jl.task_started(node_id=nid)

    return _cb
