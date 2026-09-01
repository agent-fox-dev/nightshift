"""CarryPatchMonitor — monitors carry-patch workspaces for conflicts.

Polls the hub for patches in conflict status and resolves them using
the coder archetype in carry-patch mode.

Specification: 03_carry_patch_pipeline_monitor
Requirements: 03-REQ-2, 03-REQ-3, 03-REQ-4
"""

from __future__ import annotations

import dataclasses
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from afaudit import emit as _audit_emit
from afaudit.events import AuditEventType
from afhub.errors import HubConflictError as _HubConflictError
from afhub.polling import poll_rebuild as _poll_rebuild

from afcore.archetypes import ARCHETYPE_REGISTRY, resolve_effective_config
from afcore.workspace import git as _workspace_git

if TYPE_CHECKING:
    from afaudit.sink import SessionSink, SinkDispatcher
    from afhub import HubClient

    from afcore.core.config import AgentFoxConfig
    from afcore.nightshift.engine import NightShiftEngine

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_emit(
    sink: SinkDispatcher | SessionSink | None,
    run_id: str,
    event_type: AuditEventType,
    **kwargs: object,
) -> None:
    """Emit an audit event, swallowing any exception (best-effort).

    Audit emission failures must never abort the monitor cycle
    (03-REQ-8.E1).
    """
    try:
        _audit_emit.emit_audit_event(sink, run_id, event_type, **kwargs)
    except Exception:
        logger.warning(
            "Failed to emit audit event %s",
            event_type,
            exc_info=True,
        )


def resolve_carry_patch_mode(mode: str) -> object:
    """Resolve a carry-patch mode from the coder archetype registry.

    This is a carry-patch-specific wrapper around ``resolve_effective_config``
    that validates the result and raises ``KeyError`` when the requested mode
    does not exist in the coder archetype's mode registry.

    The general ``resolve_effective_config`` function is *not* modified — it
    still logs a warning and returns the base entry for unknown modes.  This
    wrapper detects that fallback and raises ``KeyError`` explicitly.

    Requirements: 03-REQ-5.E1

    Args:
        mode: The mode name to resolve (e.g. ``'carry-patch'``).

    Returns:
        The resolved ``ArchetypeEntry`` with mode overrides applied.

    Raises:
        KeyError: If *mode* is not registered in the coder archetype's modes.
    """
    coder_entry = ARCHETYPE_REGISTRY["coder"]
    if mode not in coder_entry.modes:
        raise KeyError(mode)
    return resolve_effective_config(coder_entry, mode=mode)


# ---------------------------------------------------------------------------
# MonitorCycleResult
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class MonitorCycleResult:
    """Result of a single CarryPatchMonitor.run_cycle() invocation.

    All count fields default to zero and ``rebuild_triggered`` defaults to
    ``False`` so that a bare ``MonitorCycleResult()`` represents a no-op
    cycle (e.g. hub error, empty dashboard).

    Requirements: 03-REQ-2.2
    """

    conflicts_detected: int = 0
    conflicts_resolved: int = 0
    conflicts_failed: int = 0
    patches_merged: int = 0
    rebuild_triggered: bool = False


# ---------------------------------------------------------------------------
# CarryPatchMonitor
# ---------------------------------------------------------------------------


class CarryPatchMonitor:
    """Monitors a carry-patch workspace for conflicts and resolves them.

    The monitor does **not** own the ``HubClient`` lifecycle — the client
    is closed by ``main()`` in a ``finally`` block on daemon shutdown.

    Requirements: 03-REQ-2.1, 03-REQ-2.E1, 03-REQ-2.E2
    """

    def __init__(
        self,
        hub_client: HubClient,
        workspace_slug: str,
        config: AgentFoxConfig,
        engine: NightShiftEngine,
        sink: SinkDispatcher | SessionSink | None = None,
        run_id: str = "",
    ) -> None:
        # --- Validation (03-REQ-2.E1, 03-REQ-2.E2) ---
        if hub_client is None:
            raise ValueError("hub_client is required — CarryPatchMonitor cannot operate without a HubClient instance")
        if not workspace_slug:
            raise ValueError("workspace_slug must be non-empty — a workspace slug is required to address the hub API")

        self._hub_client = hub_client
        self._workspace_slug = workspace_slug
        self._config = config
        self._engine = engine
        self._sink = sink
        self._run_id = run_id

        # Per-(slug, patch_id) in-memory session retry counter.
        # Tracks how many resolution attempts have been made for each
        # patch within the current daemon session.
        self._retry_counter: dict[tuple[str, str], int] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run_cycle(self) -> MonitorCycleResult:
        """Execute one monitor cycle and return a MonitorCycleResult.

        Calls ``hub_client.get_patch_status(slug)`` to fetch the current
        patch dashboard.  On any hub error the cycle is fail-open: the
        error is logged and an empty ``MonitorCycleResult`` is returned
        without propagating the exception to the caller.

        Requirements: 03-REQ-3.1, 03-REQ-3.2, 03-REQ-3.3, 03-REQ-3.4,
                      03-REQ-3.7, 03-REQ-3.E1, 03-REQ-3.E2
        """
        result = MonitorCycleResult()

        # ── Step 1: Fetch dashboard (fail-open: 03-REQ-3.E1) ─────────
        try:
            dashboard = await self._hub_client.get_patch_status(
                self._workspace_slug,
            )
        except Exception:
            logger.error(
                "get_patch_status failed for workspace %s — returning empty MonitorCycleResult (fail-open)",
                self._workspace_slug,
                exc_info=True,
            )
            return result

        # Empty dashboard — no patches for workspace (03-REQ-3.E2)
        if not getattr(dashboard, "patches", None):
            return result

        # ── Step 2: Log merged_upstream patches (03-REQ-3.1) ─────────
        for p in dashboard.patches:
            if getattr(p, "status", None) == "merged_upstream":
                result.patches_merged += 1
                logger.info(
                    "Patch %s (%s) has merged_upstream status",
                    p.id,
                    getattr(p, "branch_name", ""),
                )
                _safe_emit(
                    self._sink,
                    self._run_id,
                    AuditEventType.CARRY_PATCH_MERGED_DETECTED,
                    payload={"patch_id": p.id},
                )

        # ── Step 3: Detect conflicts (03-REQ-3.7) ────────────────────
        conflict_patches = [p for p in dashboard.patches if getattr(p, "status", None) == "conflict"]
        result.conflicts_detected = len(conflict_patches)

        # Emit CONFLICT_DETECTED before the retry counter check
        # (03-REQ-3.7).
        for p in conflict_patches:
            _safe_emit(
                self._sink,
                self._run_id,
                AuditEventType.CARRY_PATCH_CONFLICT_DETECTED,
                payload={
                    "patch_id": p.id,
                    "branch_name": getattr(p, "branch_name", ""),
                },
            )

        # ── Step 4: auto_resolve guard (03-REQ-3.2) ──────────────────
        if not self._config.carry_patch.auto_resolve:
            logger.info(
                "%d conflict(s) detected for workspace %s; auto_resolve is disabled — skipping resolution",
                result.conflicts_detected,
                self._workspace_slug,
            )
            return result

        # ── Step 5: Process each conflict patch (03-REQ-3.3/3.4) ─────
        for p in conflict_patches:
            key = (self._workspace_slug, p.id)
            count = self._retry_counter.get(key, 0)

            # 03-REQ-3.4: Skip if retry limit reached
            if count >= self._config.carry_patch.max_resolve_retries:
                logger.warning(
                    "Patch %s (branch %s) skipped — retry limit reached (%d/%d)",
                    p.id,
                    getattr(p, "branch_name", ""),
                    count,
                    self._config.carry_patch.max_resolve_retries,
                )
                result.conflicts_failed += 1
                continue

            # 03-REQ-3.3: Attempt resolution
            try:
                await self._resolve_conflict(p, result)
            except Exception:
                logger.error(
                    "Conflict resolution failed for patch %s",
                    p.id,
                    exc_info=True,
                )
                self._retry_counter[key] = count + 1
                result.conflicts_failed += 1
                _safe_emit(
                    self._sink,
                    self._run_id,
                    AuditEventType.CARRY_PATCH_CONFLICT_FAILED,
                    payload={"patch_id": p.id},
                )

        return result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _build_conflict_context(
        self,
        patch_detail: object,
        slug: str,
        repo_root: Path,
    ) -> dict[str, object]:
        """Assemble the conflict resolution context dict for the coder.

        Returns a dict with keys: ``patch_description``, ``conflict_files``,
        ``upstream_context``, and ``rerere_resolutions``.

        All error cases are handled gracefully — a missing or failed field
        defaults to an empty value; the coder session proceeds with
        degraded context rather than aborting.

        Requirements: 03-REQ-4.1, 03-REQ-4.E1, 03-REQ-4.E2, 03-REQ-4.E3,
                      03-REQ-3.E6
        """
        # ── patch_description (03-REQ-4.E3) ─────────────────────────
        desc = getattr(patch_detail, "description", None)
        if desc is None:
            logger.debug("PatchDetail.description is None; defaulting to ''")
        patch_description: str = desc if desc is not None else ""

        # ── conflict_files (03-REQ-3.E6) ────────────────────────────
        conflict_files = getattr(patch_detail, "conflict_files", None)
        if conflict_files is None:
            logger.warning("PatchDetail.conflict_files is None; defaulting to []")
            conflict_files = []

        # ── rerere_resolutions (03-REQ-4.1, 03-REQ-4.E1) ────────────
        rerere_resolutions: list[str] = []
        try:
            entries = await self._hub_client.list_rerere(slug)
            rerere_resolutions = [e.path for e in entries]
        except Exception:
            logger.warning(
                "list_rerere failed for workspace %s; using empty rerere_resolutions",
                slug,
                exc_info=True,
            )

        # ── upstream_context (03-REQ-4.1, 03-REQ-4.E2) ──────────────
        upstream_context: str = ""
        try:
            rc, stdout, stderr = await _workspace_git.run_git(
                ["diff", "origin/main", "HEAD"],
                cwd=repo_root,
                check=False,
            )
            if rc == 0:
                upstream_context = stdout
            else:
                logger.warning(
                    "git diff failed (rc=%d): %s",
                    rc,
                    stderr.strip(),
                )
        except Exception:
            logger.warning(
                "git diff command failed; using empty upstream_context",
                exc_info=True,
            )

        return {
            "patch_description": patch_description,
            "conflict_files": list(conflict_files),
            "upstream_context": upstream_context,
            "rerere_resolutions": rerere_resolutions,
        }

    async def _submit_and_poll_rebuild(self, slug: str) -> None:
        """Submit a rebuild and poll for completion.

        Handles ``HubConflictError`` by falling back to the active
        rebuild (03-REQ-3.E5).  Poll failures are logged but do not
        fail the resolution — the rebuild was already triggered.
        """
        job = None
        try:
            job = await self._hub_client.submit_rebuild(slug)
        except _HubConflictError:
            # 03-REQ-3.E5: concurrent rebuild already in progress
            logger.info(
                "submit_rebuild raised HubConflictError for %s — looking up active rebuild",
                slug,
            )
            active_jobs = await self._hub_client.list_rebuilds(slug)
            if active_jobs:
                job = active_jobs[0]
            else:
                logger.warning(
                    "list_rebuilds returned empty after HubConflictError for %s",
                    slug,
                )

        if job is None:
            return

        # Best-effort poll — failures do not abort the resolution.
        try:
            await _poll_rebuild(
                self._hub_client,
                slug,
                job.id,
                timeout=self._config.carry_patch.rebuild_timeout,
                poll_interval=self._config.carry_patch.rebuild_poll_interval,
            )
        except Exception:
            logger.warning(
                "poll_rebuild failed for job %s in workspace %s; rebuild was triggered but completion status unknown",
                getattr(job, "id", ""),
                slug,
                exc_info=True,
            )

    async def _resolve_conflict(
        self,
        patch_detail: object,
        result: MonitorCycleResult,
    ) -> None:
        """Resolve a single conflicting patch.

        Sequence: build context → fetch → checkout → coder session →
        push → submit_rebuild → poll_rebuild.

        Any exception during fetch/checkout or the coder session
        propagates to ``run_cycle()`` which handles it as a failure
        (retry counter + ``conflicts_failed``).

        Requirements: 03-REQ-3.3, 03-REQ-4
        """
        slug = self._workspace_slug
        branch = getattr(patch_detail, "branch_name", "")
        repo_root = Path.cwd()

        # Step 1: Assemble conflict resolution context (03-REQ-4.1)
        context = await self._build_conflict_context(
            patch_detail,
            slug,
            repo_root,
        )

        # Step 2: Fetch and checkout the patch branch (03-REQ-3.3)
        await _workspace_git.fetch_remote(repo_root, branch=branch)
        await _workspace_git.checkout_branch(repo_root, branch)

        # Step 3: Run coder session in carry-patch mode (03-REQ-3.3)
        await self._engine._run_coder_session(
            archetype="coder",
            mode="carry-patch",
            context=context,
        )

        # Step 4: Push resolved branch (03-REQ-3.3)
        await _workspace_git.push_to_remote(repo_root, branch)

        # Step 5: Submit and poll rebuild (03-REQ-3.3, 03-REQ-3.E5)
        await self._submit_and_poll_rebuild(slug)

        # Step 6: Record success
        result.conflicts_resolved += 1
        result.rebuild_triggered = True
        _safe_emit(
            self._sink,
            self._run_id,
            AuditEventType.CARRY_PATCH_CONFLICT_RESOLVED,
            payload={"patch_id": patch_detail.id},  # type: ignore[attr-defined]
        )
