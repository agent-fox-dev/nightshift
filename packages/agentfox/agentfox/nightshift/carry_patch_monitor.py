"""CarryPatchMonitor — stub pending implementation (groups 5, 6, 7).

Provides the skeletal types so test files can be imported and collected
by pytest.  All non-trivial logic is deferred to the implementation groups.

Specification: 03_carry_patch_pipeline_monitor
Requirements: 03-REQ-2, 03-REQ-3, 03-REQ-4
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# MonitorCycleResult — intentionally NOT a dataclass yet.
# Group 5 (task 5.1) will convert this to a @dataclasses.dataclass.
# TS-03-8 asserts dataclasses.is_dataclass(MonitorCycleResult), which FAILS
# here because the type is a plain class stub.
# ---------------------------------------------------------------------------


class MonitorCycleResult:
    """Result of a single CarryPatchMonitor.run_cycle() invocation.

    Stub — not yet implemented as a dataclass.  Group 5 will replace
    this with:

        @dataclasses.dataclass
        class MonitorCycleResult:
            conflicts_detected: int = 0
            conflicts_resolved: int = 0
            conflicts_failed: int = 0
            patches_merged: int = 0
            rebuild_triggered: bool = False

    Tests that check ``dataclasses.is_dataclass(MonitorCycleResult)`` will
    FAIL until the dataclass is added in group 5.
    """


# ---------------------------------------------------------------------------
# CarryPatchMonitor — minimal stub so tests can import and instantiate.
# run_cycle() raises NotImplementedError; groups 5–7 fill in the logic.
# ---------------------------------------------------------------------------


class CarryPatchMonitor:
    """Monitors carry-patch workspace for conflicts and resolves them.

    Constructor accepts all four required parameters so tests can
    instantiate the class without errors.  run_cycle() raises
    NotImplementedError — implementation is pending groups 5, 6, and 7.

    The ``_retry_counter`` dict is pre-allocated here so tests can seed
    retry counts (e.g., TS-03-12 sets a count to max_resolve_retries before
    calling run_cycle()).

    Requirements: 03-REQ-2, 03-REQ-3
    """

    def __init__(
        self,
        hub_client: object,
        workspace_slug: str,
        config: object,
        engine: object,
        sink: object | None = None,
        run_id: str = "",
    ) -> None:
        self._hub_client = hub_client
        self._workspace_slug = workspace_slug
        self._config = config
        self._engine = engine
        self._sink = sink
        self._run_id = run_id
        # Per-(slug, patch_id) in-memory session retry counter.
        # Pre-allocated so tests can seed values; real logic in group 5/6.
        self._retry_counter: dict[tuple[str, str], int] = {}

    async def run_cycle(self) -> MonitorCycleResult:
        """Execute one monitor cycle and return a MonitorCycleResult.

        Implementation pending groups 5, 6, and 7.
        """
        raise NotImplementedError(
            "CarryPatchMonitor.run_cycle() is not yet implemented. "
            "Implementation is scheduled for task groups 5, 6, and 7."
        )
