"""Daemon startup helpers — knowledge store, migrations, progress bridge."""

from __future__ import annotations

import logging
import os
import sys
from typing import NoReturn

logger = logging.getLogger(__name__)


def report_failure(exc: Exception) -> NoReturn:
    """Report a daemon failure on stderr and exit with code 1.

    A :class:`~afcore.core.errors.FatalAPIError` already carries an
    operator-facing explanation — an exhausted credit balance, rejected
    credentials — so it is shown verbatim and logged without a traceback.
    Anything else is an unexpected failure and keeps its traceback.
    """
    from afcore.core.errors import FatalAPIError

    if isinstance(exc, FatalAPIError):
        logger.error("Nightshift aborted: %s", exc)
        print(f"Error: {exc}", file=sys.stderr)
    else:
        logger.error("Night-shift daemon failed: %s", exc, exc_info=True)
        print(f"Error: nightshift daemon failed: {exc}", file=sys.stderr)
    sys.exit(1)


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


def check_cache_policy_advisory(config) -> None:
    """Warn once at startup when cache_policy is non-default on a backend that ignores it.

    The ``claude`` backend delegates request construction to the Claude Code
    CLI subprocess, which manages its own prompt caching.  A non-default
    ``cache_policy`` is recorded for metric correlation but does not change
    caching behaviour.  This warning prevents operators from believing their
    setting controls spend.

    Issue #40.
    """
    from afcore.core.config import CachePolicy

    policy = getattr(getattr(config, "caching", None), "cache_policy", CachePolicy.DEFAULT)
    if policy == CachePolicy.DEFAULT:
        return

    backend_name = getattr(getattr(config, "backend", None), "provider", "claude")
    if backend_name != "claude":
        return

    logger.warning(
        "cache_policy is set to '%s' but the '%s' backend cannot honour it — "
        "the Claude Code CLI subprocess manages its own prompt caching. "
        "This setting is advisory only and records intent for metric correlation.",
        policy.value,
        backend_name,
    )


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

    # Enforce audit retention — prune oldest runs beyond the configured
    # limit.  Best-effort: a failure here must not prevent daemon startup.
    try:
        from afaudit.constants import AUDIT_DIR
        from afcore.knowledge.duckdb_sink import enforce_audit_retention

        max_runs = getattr(config.knowledge, "audit_max_runs", 20)
        enforce_audit_retention(AUDIT_DIR, kdb.connection, max_runs=max_runs)
    except Exception:
        logger.warning("Audit retention failed", exc_info=True)

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
