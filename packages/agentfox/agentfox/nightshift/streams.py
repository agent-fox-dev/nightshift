"""Work stream protocol and concrete implementations for the daemon framework.

Defines the ``WorkStream`` protocol and provides the fix-pipeline stream
via ``EngineWorkStream``, plus a ``build_streams()`` factory that applies
CLI flags, config, and platform degradation rules.

Requirements: 85-REQ-1.1, 85-REQ-6.1, 85-REQ-6.2, 85-REQ-6.3,
              85-REQ-7.1, 85-REQ-7.E1, 125-REQ-3.1, 125-REQ-3.2,
              125-REQ-3.3, 125-REQ-3.4, 125-REQ-3.E1,
              03-REQ-7.1, 03-REQ-7.2, 03-REQ-7.E1
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from agentfox.nightshift.daemon import SharedBudget


# ---------------------------------------------------------------------------
# WorkStream protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class WorkStream(Protocol):
    """Protocol for daemon work streams.

    Each work stream has a name, polling interval, enabled flag, and
    async methods for running one cycle and shutting down gracefully.

    Requirements: 85-REQ-1.1
    """

    @property
    def name(self) -> str:
        """Unique name identifying this work stream."""
        ...

    @property
    def interval(self) -> int:
        """Seconds between run_once() invocations."""
        ...

    @property
    def enabled(self) -> bool:
        """Whether this stream should be scheduled for execution."""
        ...

    async def run_once(self) -> None:
        """Execute one cycle of this work stream's logic."""
        ...

    async def shutdown(self) -> None:
        """Clean up resources before daemon exit."""
        ...


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# EngineWorkStream — unified wrapper for engine-method-based streams
# ---------------------------------------------------------------------------


class EngineWorkStream:
    """Wraps a NightShiftEngine method as a work stream.

    Requirements: 85-REQ-1.1, 85-REQ-6.3
    """

    def __init__(
        self,
        stream_name: str,
        engine: object,
        method_name: str,
        budget: SharedBudget,
        *,
        enabled: bool = True,
        interval: int = 900,
    ) -> None:
        self._name = stream_name
        self._engine = engine
        self._method_name = method_name
        self._budget = budget
        self._enabled = enabled
        self._interval = interval

    @property
    def name(self) -> str:
        return self._name

    @property
    def interval(self) -> int:
        return self._interval

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    async def run_once(self) -> None:
        """Run one cycle via the configured engine method and report cost delta."""
        cost_before = getattr(getattr(self._engine, "state", None), "total_cost", 0.0)
        method = getattr(self._engine, self._method_name)
        await method()
        cost_after = getattr(getattr(self._engine, "state", None), "total_cost", 0.0)
        delta = cost_after - cost_before
        if delta > 0:
            self._budget.add_cost(delta)

    async def shutdown(self) -> None:
        """No resources to clean up."""


# ---------------------------------------------------------------------------
# CarryPatchStream — wraps CarryPatchMonitor.run_cycle()
# ---------------------------------------------------------------------------


class CarryPatchStream:
    """Work stream that delegates to ``CarryPatchMonitor.run_cycle()``.

    Unlike ``EngineWorkStream`` (which wraps an engine method by name),
    this stream holds a direct reference to a ``CarryPatchMonitor`` and
    invokes ``run_cycle()`` on each tick.

    Requirements: 03-REQ-7.1
    """

    def __init__(
        self,
        *,
        monitor: object,
        check_interval: int,
        enabled: bool = True,
    ) -> None:
        self._monitor = monitor
        self._interval = check_interval
        self._enabled = enabled

    @property
    def name(self) -> str:
        return "carry-patch"

    @property
    def interval(self) -> int:
        return self._interval

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    async def run_once(self) -> None:
        """Run one monitor cycle."""
        await self._monitor.run_cycle()

    async def shutdown(self) -> None:
        """No resources to clean up — HubClient is closed by main()."""


# ---------------------------------------------------------------------------
# build_streams() factory
# ---------------------------------------------------------------------------


def build_streams(
    config: object,
    *,
    engine: object | None = None,
    budget: SharedBudget | None = None,
    hub_client: object | None = None,
) -> list[WorkStream]:
    """Build work streams with proper enabled/disabled state.

    Returns a list of ``WorkStream`` instances — the fix-pipeline stream
    (always present), optional pr-feedback, and optional carry-patch.
    The fix-pipeline stream is disabled when the platform type is ``"none"``.

    Requirements: 85-REQ-6.1, 85-REQ-7.1, 125-REQ-3.3, 125-REQ-3.4,
                  125-REQ-3.E1, 03-REQ-7.1, 03-REQ-7.2, 03-REQ-7.E1
    """
    from agentfox.nightshift.daemon import SharedBudget as _SharedBudget

    if budget is None:
        budget = _SharedBudget(max_cost=None)

    ns = getattr(config, "night_shift", None)

    # Platform degradation (85-REQ-7.1): platform.type="none" disables
    # the fix-pipeline stream.
    platform_type = getattr(getattr(config, "platform", None), "type", "github")
    fixes_enabled = True
    if platform_type == "none":
        logger.warning("Platform type is 'none'; disabling fix-pipeline stream")
        fixes_enabled = False

    # Get interval from config
    issue_check_interval = getattr(ns, "issue_check_interval", 900)

    # Build streams — fix-pipeline first, then optional pr-feedback
    # (125-REQ-3.3, 07-REQ-2.1)
    streams: list[WorkStream] = []

    streams.append(
        EngineWorkStream(
            stream_name="fix-pipeline",
            engine=engine,
            method_name="_drain_issues",
            budget=budget,
            enabled=fixes_enabled,
            interval=issue_check_interval,
        )
    )

    # PR feedback stream: only enabled when merge_strategy='pr' and
    # platform is not 'none' (07-REQ-2.1, 07-REQ-2.2).
    merge_strategy = getattr(getattr(config, "workspace", None), "merge_strategy", "direct")
    pr_check_interval = getattr(ns, "pr_check_interval", 900)
    if merge_strategy == "pr" and platform_type != "none":
        streams.append(
            EngineWorkStream(
                stream_name="pr-feedback",
                engine=engine,
                method_name="_check_open_prs",
                budget=budget,
                enabled=True,
                interval=pr_check_interval,
            )
        )

    # Carry-patch stream: only when carry_patch.enabled and a HubClient
    # is available (03-REQ-7.1, 03-REQ-7.2, 03-REQ-7.E1).
    carry_patch = getattr(config, "carry_patch", None)
    if carry_patch is not None and getattr(carry_patch, "enabled", False) and hub_client is not None:
        check_interval = getattr(carry_patch, "check_interval", 0)
        if check_interval <= 0:
            raise ValueError(
                f"check_interval must be a positive duration, got {check_interval}"
            )
        from agentfox.nightshift.carry_patch_monitor import CarryPatchMonitor  # noqa: PLC0415

        # Build monitor — reuses the hub_client owned by main().
        monitor = CarryPatchMonitor(
            hub_client=hub_client,
            workspace_slug=getattr(carry_patch, "workspace", ""),
            config=config,
            engine=engine,
        )
        # Wire the monitor to the engine so _run_carry_patch_monitor()
        # can delegate to it (03-REQ-7.4, 11.2 wiring verification).
        if engine is not None:
            engine._carry_patch_monitor = monitor
        streams.append(
            CarryPatchStream(
                monitor=monitor,
                check_interval=check_interval,
                enabled=True,
            )
        )

    return streams
