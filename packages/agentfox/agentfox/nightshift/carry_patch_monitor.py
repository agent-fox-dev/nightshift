"""CarryPatchMonitor — monitors carry-patch workspaces for conflicts.

Polls the hub for patches in conflict status and resolves them using
the coder archetype in carry-patch mode.

Specification: 03_carry_patch_pipeline_monitor
Requirements: 03-REQ-2, 03-REQ-3, 03-REQ-4
"""

from __future__ import annotations

import dataclasses
import logging
from typing import TYPE_CHECKING

from afaudit import emit as _audit_emit
from afaudit.events import AuditEventType

if TYPE_CHECKING:
    from afaudit.sink import SessionSink, SinkDispatcher
    from afhub import HubClient

    from agentfox.core.config import AgentFoxConfig
    from agentfox.nightshift.engine import NightShiftEngine

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
            raise ValueError(
                "hub_client is required — CarryPatchMonitor cannot "
                "operate without a HubClient instance"
            )
        if not workspace_slug:
            raise ValueError(
                "workspace_slug must be non-empty — a workspace slug "
                "is required to address the hub API"
            )

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
                "get_patch_status failed for workspace %s — returning "
                "empty MonitorCycleResult (fail-open)",
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
        conflict_patches = [
            p
            for p in dashboard.patches
            if getattr(p, "status", None) == "conflict"
        ]
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
                "%d conflict(s) detected for workspace %s; "
                "auto_resolve is disabled — skipping resolution",
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
                    "Patch %s (branch %s) skipped — retry limit "
                    "reached (%d/%d)",
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

    async def _resolve_conflict(
        self,
        patch_detail: object,
        result: MonitorCycleResult,
    ) -> None:
        """Resolve a single conflicting patch.

        Group 7 will expand this with fetch/checkout, context assembly,
        push, and rebuild polling.

        Requirements: 03-REQ-3.3
        """
        # TODO(group-7): fetch_remote + checkout_branch before coder
        # TODO(group-7): assemble conflict context dict (REQ-4)
        # TODO(group-7): push_to_remote + submit_rebuild + poll_rebuild

        await self._engine._run_coder_session()

        # Resolution succeeded
        result.conflicts_resolved += 1
        result.rebuild_triggered = True
        _safe_emit(
            self._sink,
            self._run_id,
            AuditEventType.CARRY_PATCH_CONFLICT_RESOLVED,
            payload={"patch_id": patch_detail.id},  # type: ignore[attr-defined]
        )
