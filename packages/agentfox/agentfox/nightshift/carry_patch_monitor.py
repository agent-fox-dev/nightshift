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

if TYPE_CHECKING:
    from afaudit.sink import SessionSink, SinkDispatcher
    from afhub import HubClient

    from agentfox.core.config import AgentFoxConfig
    from agentfox.nightshift.engine import NightShiftEngine

logger = logging.getLogger(__name__)


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

        Requirements: 03-REQ-3.1, 03-REQ-3.E1, 03-REQ-3.E2
        """
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
            return MonitorCycleResult()

        # An empty dashboard means no patches exist for the workspace.
        if not getattr(dashboard, "patches", None):
            return MonitorCycleResult()

        # Full cycle logic (merged detection, conflict resolution,
        # auto_resolve guard, retry counter) is implemented in groups
        # 6 and 7.
        return MonitorCycleResult()
