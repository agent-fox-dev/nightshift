"""Night Shift engine: business logic for the fix pipeline.

Provides the core operations that work streams delegate to.  Lifecycle
management (scheduling, signals, budget) is handled by ``DaemonRunner``.

Requirements: 61-REQ-1.1, 61-REQ-1.E1, 61-REQ-9.3
"""

from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from afaudit.emit import emit_audit_event as _emit_audit_event
from afaudit.events import AuditEventType, generate_run_id
from afissues.labels import LABEL_FIX, LABEL_FIXED, LABEL_PR

from afcore.core.config import AgentFoxConfig
from afcore.core.errors import FatalAPIError
from afcore.nightshift.dep_graph import build_graph, build_parallel_graph, merge_edges
from afcore.nightshift.fix_pipeline import LABEL_FAILED, FixPipeline
from afcore.nightshift.pr_feedback import process_pr_issue
from afcore.nightshift.reference_parser import (
    fetch_github_relationships,
    parse_text_references,
)
from afcore.nightshift.staleness import check_staleness
from afcore.nightshift.triage import run_batch_triage
from afcore.ui.progress import ActivityCallback, SpinnerCallback, TaskCallback
from afcore.workspace.repo_root import resolve_repo_root as _resolve_repo_root

if TYPE_CHECKING:
    import duckdb
    from afaudit.sink import SinkDispatcher

    from afcore.knowledge.fox_provider import KnowledgeProvider
    from afcore.nightshift.daemon import SharedBudget

logger = logging.getLogger(__name__)

# Maximum number of af:pr issues processed per poll cycle.
# Issues beyond this cap are deferred to the next cycle.
# Requirements: 07-REQ-3.3
_MAX_PR_CHECKS: int = 5


@dataclass(frozen=True)
class IssueOutcome:
    """Per-issue result captured during a nightshift run."""

    issue_number: int
    title: str
    run_id: str
    outcome: str
    duration_ms: int
    cost_usd: float
    sessions_run: int
    input_tokens: int
    output_tokens: int


@dataclass
class NightShiftState:
    """Runtime state for the daemon.

    Mutable -- fields are updated during the daemon lifecycle.
    Protected by an asyncio.Lock for safe concurrent access from
    parallel issue processing tasks.
    """

    total_cost: float = 0.0
    total_sessions: int = 0
    issues_created: int = 0
    issues_fixed: int = 0
    issue_checks_completed: int = 0
    is_shutting_down: bool = False
    issue_outcomes: list[IssueOutcome] = field(default_factory=list)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    @property
    def total_input_tokens(self) -> int:
        """Sum of input_tokens across all issue outcomes."""
        return sum(o.input_tokens for o in self.issue_outcomes)

    @property
    def total_output_tokens(self) -> int:
        """Sum of output_tokens across all issue outcomes."""
        return sum(o.output_tokens for o in self.issue_outcomes)

    async def add_fix_result(
        self,
        cost: float,
        sessions: int,
        outcome: IssueOutcome,
        *,
        succeeded: bool,
    ) -> None:
        """Thread-safe update of counters after a fix completes."""
        async with self._lock:
            self.total_sessions += sessions
            self.total_cost += cost
            if succeeded:
                self.issues_fixed += 1
            self.issue_outcomes.append(outcome)


def validate_night_shift_prerequisites(config: AgentFoxConfig) -> None:
    """Validate that the platform is configured for night-shift.

    Aborts with exit code 1 if the platform type is 'none' or missing.

    Requirements: 61-REQ-1.E1
    """
    platform_type = getattr(getattr(config, "platform", None), "type", "none")
    if platform_type == "none":
        logger.error("Night-shift requires a configured platform. Set [platform] type = 'github' in your config.")
        sys.exit(1)


class NightShiftEngine:
    """Main daemon engine for night-shift.

    Coordinates issue checks and fix sessions on a timed schedule.

    Requirements: 61-REQ-1.1, 61-REQ-1.3, 61-REQ-1.4, 61-REQ-1.E2
    """

    # Maximum drain iterations to prevent infinite loops if issues are
    # created faster than they are fixed.
    _MAX_DRAIN_ITERATIONS: int = 50

    def __init__(
        self,
        config: AgentFoxConfig,
        platform: object,
        *,
        activity_callback: ActivityCallback | None = None,
        task_callback: TaskCallback | None = None,
        status_callback: Callable[[str, str], None] | None = None,
        spinner_callback: SpinnerCallback | None = None,
        sink_dispatcher: SinkDispatcher | None = None,
        conn: duckdb.DuckDBPyConnection | None = None,
        knowledge_provider: KnowledgeProvider | None = None,
        hub_client: object | None = None,
        budget: SharedBudget | None = None,
        repo_root: Path | None = None,
    ) -> None:
        self._config = config
        self._platform = platform
        # Resolved once at startup by the CLI and threaded down to the
        # fix pipeline and the carry-patch monitor rather than being
        # re-derived from the working directory (issue #43).
        self._repo_root = Path(repo_root) if repo_root is not None else _resolve_repo_root()
        self._activity_callback = activity_callback
        self._task_callback = task_callback
        self._status_callback = status_callback
        self._spinner_callback = spinner_callback
        self._sink = sink_dispatcher
        self._conn = conn
        self._knowledge_provider = knowledge_provider
        self._hub_client = hub_client
        self._budget = budget
        self.state = NightShiftState()
        # Track issue numbers processed in this run to guard against
        # re-processing issues that were closed/fixed but still returned
        # by the platform API due to eventual consistency (issue #465).
        self._processed_issues: set[int] = set()
        self._processed_issues_lock = asyncio.Lock()
        # Issues currently being processed in parallel — excluded from
        # staleness evaluation by sibling fixes.
        self._in_flight: set[int] = set()
        self._in_flight_lock = asyncio.Lock()
        # Single CarryPatchMonitor instance reused across all calls to
        # _run_carry_patch_monitor().  Set by build_streams() when
        # carry-patch is enabled (see streams.py).
        # Requirements: 03-REQ-7.4
        self._carry_patch_monitor: object | None = None
        # Sum of estimated worst-case costs reserved for issues currently
        # dispatched but not yet complete.  Mutated only from the
        # synchronous dispatch loop in _process_issues_parallel and from
        # _run_one's finally block, both on the event-loop thread with no
        # await between read and write, so no lock is needed (issue #33).
        self._reserved_cost: float = 0.0

        # AC-4 (issue #35): log once at startup when no gate command is
        # configured so the operator knows the daemon is running ungated.
        gate_cfg = getattr(config, "gate", None)
        gate_command = getattr(gate_cfg, "command", "") if gate_cfg is not None else ""
        if not isinstance(gate_command, str) or not gate_command:
            from afcore.nightshift.gate import log_ungated_warning

            log_ungated_warning()

    @property
    def repo_root(self) -> Path:
        """The repository root this engine operates on.

        Requirements: NS-REQ-6 (issue #43)
        """
        return self._repo_root

    def _build_pipeline(self) -> FixPipeline:
        """Build a fully-wired ``FixPipeline`` instance.

        Centralises the construction so that ``_process_fix`` and
        ``_check_open_prs`` always supply identical wiring — including
        the ``hub_client`` and ``workspace_slug`` required by the
        carry-patch integration path.

        Requirements: NS-REQ-41 (issue #41, AC-1, AC-4)
        """
        _cp_cfg = getattr(self._config, "carry_patch", None)
        _ws_slug = getattr(_cp_cfg, "workspace", "") if _cp_cfg else ""

        return FixPipeline(
            config=self._config,
            platform=self._platform,
            activity_callback=self._activity_callback,
            task_callback=self._task_callback,
            sink_dispatcher=self._sink,
            spinner_callback=self._spinner_callback,
            conn=self._conn,
            knowledge_provider=self._knowledge_provider,
            hub_client=self._hub_client,
            workspace_slug=_ws_slug,
            repo_root=self._repo_root,
        )

    def _check_cost_limit(self) -> bool:
        """Check whether the cost limit has been reached.

        Returns True once ``total_cost`` reaches the full configured
        ``max_cost`` -- the same comparison ``SharedBudget.exceeded`` uses
        in daemon.py.  Previously this tripped at 50% of ``max_cost``,
        silently halving the daemon's configured throughput (issue #33).
        Not starting an issue whose own estimated cost would not fit in
        what remains is handled separately, before dispatch, by
        ``_reserve_budget_for_issue()``.

        Requirements: 61-REQ-1.E2, 61-REQ-9.3, NS-REQ-33 (issue #33)
        """
        max_cost = getattr(getattr(self._config, "orchestrator", None), "max_cost", None)
        if not isinstance(max_cost, (int, float)):
            return False
        return self.state.total_cost >= max_cost

    def _estimate_issue_cost(self) -> float:
        """Return a conservative worst-case cost ceiling for one issue.

        Sums the per-archetype ``max_budget_usd`` ceilings for every
        session a single issue's fix pipeline can run: one triage session
        (the ``maintainer`` archetype in ``fix-triage`` mode) plus, for
        each of ``1 + orchestrator.max_retries`` coder-reviewer rounds, one
        coder and one reviewer session.

        Returns 0.0 -- "no usable ceiling" -- when any of those archetypes
        has an unlimited budget (``max_budget_usd = 0``), since no useful
        worst case can be computed in that case.

        Requirements: NS-REQ-33 (issue #33, AC-4)
        """
        from afcore.engine.sdk_params import resolve_max_budget

        config = self._config
        triage_budget = resolve_max_budget(config, "maintainer", mode="fix-triage")
        coder_budget = resolve_max_budget(config, "coder", mode="fix")
        reviewer_budget = resolve_max_budget(config, "reviewer", mode="fix-review")
        budgets = (triage_budget, coder_budget, reviewer_budget)
        # Also guards against non-numeric config stand-ins (e.g. a bare
        # MagicMock in tests) resolving to a mock object rather than a
        # real float or None -- treated the same as "unbounded".
        if any(not isinstance(b, (int, float)) for b in budgets):
            return 0.0
        max_retries = getattr(self._config.orchestrator, "max_retries", 0)
        if not isinstance(max_retries, (int, float)):
            max_retries = 0
        rounds = 1 + max_retries
        return triage_budget + rounds * (coder_budget + reviewer_budget)

    def _reserve_budget_for_issue(self) -> bool:
        """Reserve the estimated worst-case cost of one issue before dispatch.

        Returns False -- reserving nothing -- when the estimate would not
        fit in what remains of ``max_cost`` after already-committed spend
        and already-reserved (in-flight) estimates.  Returns True (a no-op)
        when ``max_cost`` is unset or the estimate cannot be computed, since
        neither bounds the run.

        The reservation is released by ``_release_reserved_cost()`` once the
        issue completes, regardless of outcome (issue #33, AC-4).
        """
        max_cost = getattr(getattr(self._config, "orchestrator", None), "max_cost", None)
        if not isinstance(max_cost, (int, float)):
            return True
        estimate = self._estimate_issue_cost()
        if estimate <= 0:
            return True
        remaining = max_cost - self.state.total_cost - self._reserved_cost
        if remaining < estimate:
            return False
        self._reserved_cost += estimate
        return True

    def _release_reserved_cost(self) -> None:
        """Release one issue's reservation after it completes.

        Recomputes the same estimate ``_reserve_budget_for_issue()`` used —
        config does not change mid-run, so this returns the identical value
        without needing to track per-issue reservations.  A no-op when
        nothing was reserved (unset ``max_cost`` or an unbounded estimate).
        """
        max_cost = getattr(getattr(self._config, "orchestrator", None), "max_cost", None)
        if not isinstance(max_cost, (int, float)):
            return
        estimate = self._estimate_issue_cost()
        if estimate <= 0:
            return
        self._reserved_cost = max(0.0, self._reserved_cost - estimate)

    def _check_session_limit(self) -> bool:
        """Check whether the session limit has been reached.

        Returns True when total_sessions >= max_sessions.

        Requirements: 61-REQ-9.3
        """
        max_sessions = getattr(getattr(self._config, "orchestrator", None), "max_sessions", None)
        if not isinstance(max_sessions, (int, float)):
            return False
        return self.state.total_sessions >= max_sessions

    def _emit_status(self, text: str, style: str = "bold cyan") -> None:
        """Emit a permanent status line via the status_callback.

        If no callback is set, this is a no-op.

        Requirements: 81-REQ-3.1, 81-REQ-3.2, 81-REQ-3.3, 81-REQ-3.4, 81-REQ-3.5
        """
        if self._status_callback is not None:
            try:
                self._status_callback(text, style)
            except Exception:
                logger.debug("Status callback failed", exc_info=True)

    async def _run_issue_check(self, _seen: set[int] | None = None) -> None:
        """Poll platform for af:fix issues and process them.

        Issues are fetched sorted by creation date ascending (oldest first).
        A local sort by issue number is applied as a fallback in case the
        platform ignores the sort parameters (71-REQ-1.E1).

        Triage phase: for batches >= 3, runs AI batch triage to detect
        dependencies and supersession candidates (71-REQ-3.1).

        Staleness phase: after each successful fix, evaluates remaining
        issues for obsolescence (71-REQ-5.1).

        Args:
            _seen: Optional set of issue numbers already processed in this
                   drain session.  Issues present in this set are skipped so
                   that a recently closed issue cannot be re-processed due to
                   platform API propagation delays between drain iterations
                   (fixes #465).

        Requirements: 61-REQ-2.1, 71-REQ-1.1, 71-REQ-1.2, 71-REQ-1.E1,
                      71-REQ-3.1, 71-REQ-3.5, 71-REQ-5.1, 71-REQ-5.E3
        """
        # Use the caller-supplied set so processed issues are remembered
        # across drain iterations; fall back to a local set for direct calls.
        seen: set[int] = _seen if _seen is not None else set()
        self._emit_status("Checking for af:fix issues\u2026")
        try:
            issues = await self._platform.list_issues_by_label(  # type: ignore[attr-defined]
                LABEL_FIX,
                sort="created",
                direction="asc",
            )
        except Exception:
            logger.warning(
                "Issue check failed due to platform API error",
                exc_info=True,
            )
            raise

        if not issues:
            self.state.issue_checks_completed += 1
            return

        # Local sort fallback: ensure ascending issue number order
        # even if the platform does not honour the sort parameters (71-REQ-1.E1).
        issues = sorted(issues, key=lambda i: i.number)

        # Skip issues already processed in this drain session or prior runs.
        # The platform API may return recently-closed issues due to eventual
        # consistency (issue #465).  The ``seen`` set covers superseded and
        # staleness-closed issues within the current drain; the instance-level
        # ``_processed_issues`` set covers issues handled in earlier runs.
        issues = [i for i in issues if i.number not in seen and i.number not in self._processed_issues]

        # Filter out issues carrying the af:failed label (issue #37,
        # NS-REQ-3).  These are issues that have exhausted their cross-run
        # attempt ceiling and should not be re-dispatched.
        issues = [i for i in issues if LABEL_FAILED not in (getattr(i, "labels", None) or [])]
        if not issues:
            self.state.issue_checks_completed += 1
            return

        # Build dependency graph from explicit references and GitHub metadata
        explicit_edges = parse_text_references(issues)
        try:
            github_edges = await fetch_github_relationships(self._platform, issues)
        except Exception:
            logger.warning(
                "Failed to fetch GitHub relationships, continuing without",
                exc_info=True,
            )
            github_edges = []

        all_edges = explicit_edges + github_edges

        # AI triage for batches >= 3 (71-REQ-3.1, 71-REQ-3.5)
        issue_check_run_id = generate_run_id()
        supersession_pairs: list[tuple[int, int]] = []
        if len(issues) >= 3:
            try:
                triage = await run_batch_triage(
                    issues, all_edges, self._config, sink=self._sink, run_id=issue_check_run_id
                )
                all_edges = merge_edges(all_edges, triage.edges)
                supersession_pairs = triage.supersession_pairs
            except FatalAPIError:
                # No credit / rejected credentials — abort the whole run
                # instead of degrading to explicit refs.
                raise
            except Exception:
                logger.warning(
                    "AI triage failed, using explicit refs only",
                    exc_info=True,
                )

        # Compute processing order via topological sort (used for logging)
        processing_order = build_graph(issues, all_edges)
        logger.info("Resolved processing order: %s", processing_order)

        issue_map = {i.number: i for i in issues}
        closed: set[int] = set()

        # Close AI-identified superseded issues before processing (71-REQ-3.5)
        for _keep, obsolete in supersession_pairs:
            if obsolete not in issue_map or obsolete in closed:
                continue
            try:
                await self._platform.close_issue(  # type: ignore[attr-defined]
                    obsolete,
                    f"Superseded by #{_keep} (AI triage).",
                )
                closed.add(obsolete)
                seen.add(obsolete)
                _emit_audit_event(
                    self._sink,
                    issue_check_run_id,
                    AuditEventType.ISSUE_SUPERSEDED,
                    payload={"closed_issue": obsolete, "superseded_by": _keep},
                )
                try:
                    await self._platform.assign_label(  # type: ignore[attr-defined]
                        obsolete,
                        LABEL_FIXED,
                    )
                except Exception:
                    logger.warning(
                        "Failed to assign af:fixed label to superseded issue #%d",
                        obsolete,
                        exc_info=True,
                    )
            except Exception:
                logger.warning(
                    "Failed to close superseded issue #%d",
                    obsolete,
                    exc_info=True,
                )

        # Parallel dispatch with dependency-aware scheduling
        _mp = getattr(
            getattr(self._config, "night_shift", None),
            "max_parallel",
            1,
        )
        max_parallel = _mp if isinstance(_mp, int) and _mp >= 1 else 1
        await self._dispatch_parallel(
            issues=issues,
            all_edges=all_edges,
            issue_map=issue_map,
            processing_order=processing_order,
            closed=closed,
            seen=seen,
            max_parallel=max_parallel,
            issue_check_run_id=issue_check_run_id,
        )

        self.state.issue_checks_completed += 1

    async def _dispatch_parallel(
        self,
        *,
        issues: list[object],
        all_edges: list[object],
        issue_map: dict[int, object],
        processing_order: list[int],
        closed: set[int],
        seen: set[int],
        max_parallel: int,
        issue_check_run_id: str,
    ) -> None:
        """Dependency-aware parallel issue dispatcher.

        Launches up to ``max_parallel`` concurrent ``_process_fix`` tasks.
        When a task completes, the dependency graph is updated and newly-
        ready issues are dispatched to fill the pool.

        With ``max_parallel=1`` this behaves identically to the previous
        serial loop — issues are processed one at a time in topological order.
        """
        graph = build_parallel_graph(issues, all_edges)  # type: ignore[arg-type]

        # Remove closed (superseded) issues from the graph
        for c in closed:
            graph.complete(c)

        pool: set[asyncio.Task[tuple[int, bool]]] = set()
        dispatched: set[int] = set()
        ceiling_exceeded: list[int] = []

        async def _run_one(issue_num: int) -> tuple[int, bool]:
            """Process a single issue and return (issue_num, succeeded)."""
            try:
                # Re-check issue freshness before starting work (NS-REQ-2).
                # Between the poll that discovered this issue and now, someone
                # may have closed the issue or removed its af:fix label.
                try:
                    fresh = await self._platform.get_issue(issue_num)  # type: ignore[attr-defined]
                    # Check closed state (forward-compatible with IssueResult
                    # gaining a ``state`` field in the future).
                    if getattr(fresh, "state", "open") == "closed":
                        logger.info(
                            "Issue #%d was closed between poll and dispatch, skipping",
                            issue_num,
                        )
                        return (issue_num, False)
                    # Check that af:fix label is still present.
                    fresh_labels = getattr(fresh, "labels", None)
                    if isinstance(fresh_labels, (tuple, list)) and LABEL_FIX not in fresh_labels:
                        logger.info(
                            "Issue #%d no longer has af:fix label, skipping",
                            issue_num,
                        )
                        return (issue_num, False)
                except Exception:
                    logger.warning(
                        "Failed to re-check issue #%d freshness, continuing with processing",
                        issue_num,
                        exc_info=True,
                    )

                issue = issue_map[issue_num]
                fix_succeeded = False
                try:
                    async with self._in_flight_lock:
                        self._in_flight.add(issue_num)
                    try:
                        # issue #50: use the pipeline's real outcome, not
                        # "the call did not raise" -- a fix that exhausted
                        # its retries or produced no changes returns
                        # normally without landing.
                        fix_succeeded = await self._process_fix(issue)
                    except Exception:
                        logger.warning(
                            "Fix failed for issue #%d, continuing to next",
                            issue_num,
                            exc_info=True,
                        )
                    finally:
                        async with self._in_flight_lock:
                            self._in_flight.discard(issue_num)
                        async with self._processed_issues_lock:
                            self._processed_issues.add(issue_num)
                except Exception:
                    logger.warning(
                        "Unexpected error processing issue #%d",
                        issue_num,
                        exc_info=True,
                    )
                return (issue_num, fix_succeeded)
            finally:
                self._release_reserved_cost()

        def _fill_pool() -> None:
            """Add ready issues to the pool up to max_parallel."""
            if self.state.is_shutting_down:
                return
            ready = graph.ready_issues()
            for issue_num in ready:
                if len(pool) >= max_parallel:
                    break
                if issue_num in dispatched or issue_num in closed:
                    continue
                if self.state.is_shutting_down:
                    break
                if self._check_cost_limit():
                    logger.info("Cost limit reached, stopping issue dispatch")
                    return
                if self._check_session_limit():
                    logger.info("Session limit reached, stopping issue dispatch")
                    return
                # Issue #33 (AC-4): don't start work that cannot be paid for.
                # Reserves a worst-case estimate against the remaining
                # budget; released in _run_one's finally once the issue
                # completes (success, failure, or exception).
                if not self._reserve_budget_for_issue():
                    logger.info(
                        "Estimated cost of issue #%d exceeds remaining budget, stopping issue dispatch",
                        issue_num,
                    )
                    return
                # Issue #37 (NS-REQ-1): check cross-run attempt ceiling
                # before dispatching.  Fail-open when DuckDB is unavailable.
                # Issue #90: also add to ``seen`` so the drain loop excludes
                # this issue on re-poll, and collect for deferred af:failed
                # labelling (async, handled after _fill_pool returns).
                if self._exceeds_attempt_ceiling(issue_num):
                    self._release_reserved_cost()
                    closed.add(issue_num)
                    seen.add(issue_num)
                    graph.complete(issue_num)
                    ceiling_exceeded.append(issue_num)
                    continue
                dispatched.add(issue_num)
                task = asyncio.create_task(
                    _run_one(issue_num),
                    name=f"fix-issue-{issue_num}",
                )
                pool.add(task)

        async def _label_ceiling_exceeded() -> None:
            """Apply af:failed label to ceiling-exceeded issues (issue #90).

            Deferred from _fill_pool (sync) because label application
            requires async platform calls.  Best-effort: failures are
            logged but do not propagate.
            """
            while ceiling_exceeded:
                num = ceiling_exceeded.pop()
                try:
                    await self._platform.assign_label(num, LABEL_FAILED)  # type: ignore[attr-defined]
                except Exception:
                    logger.warning(
                        "Failed to assign af:failed label to ceiling-exceeded issue #%d",
                        num,
                        exc_info=True,
                    )
                try:
                    await self._platform.add_issue_comment(  # type: ignore[attr-defined]
                        num,
                        "Issue has reached the cross-run attempt ceiling and will not be "
                        "retried automatically. Labelled `af:failed`.\n\n"
                        f"(run: `{issue_check_run_id}`)",
                    )
                except Exception:
                    logger.warning(
                        "Failed to post ceiling comment on issue #%d",
                        num,
                        exc_info=True,
                    )
                async with self._processed_issues_lock:
                    self._processed_issues.add(num)

        _fill_pool()
        await _label_ceiling_exceeded()

        # Set when a non-recoverable API error (no credit, rejected
        # credentials) surfaces mid-dispatch.  Dispatch stops, in-flight
        # fixes are allowed to finish, and the error is re-raised so the
        # daemon aborts with a clear message.
        fatal_error: FatalAPIError | None = None

        while pool:
            done, pool = await asyncio.wait(pool, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                issue_num, fix_succeeded = task.result()

                # Update graph: mark complete and find newly-ready issues
                graph.complete(issue_num)

                # Dedup against re-processing this session regardless of
                # outcome (issue #465) -- independent of whether the fix
                # actually landed (issue #50): a failed fix must not be
                # retried within the same drain loop just because the
                # platform still returns it.
                seen.add(issue_num)

                # Post-fix staleness check only for a genuinely integrated
                # fix -- a failed, skipped, or not-yet-merged issue has no
                # diff to justify closing anything else (71-REQ-5.1,
                # 71-REQ-5.E3; issue #50 AC-1, AC-4).
                if fix_succeeded:
                    remaining = [
                        issue_map[n]
                        for n in processing_order
                        if n != issue_num and n not in closed and n not in dispatched
                    ]
                    if remaining:
                        try:
                            async with self._in_flight_lock:
                                current_in_flight = set(self._in_flight)
                            staleness = await check_staleness(
                                issue_map[issue_num],
                                remaining,
                                "",  # diff not available in current implementation
                                self._config,
                                self._platform,
                                sink=self._sink,
                                run_id=issue_check_run_id,
                                in_flight=current_in_flight,
                            )
                            remaining_nums = {i.number for i in remaining}
                            for obsolete_num in staleness.obsolete_issues:
                                if obsolete_num not in remaining_nums:
                                    continue
                                await self._platform.close_issue(  # type: ignore[attr-defined]
                                    obsolete_num,
                                    f"Resolved by fix for #{issue_num}",
                                )
                                closed.add(obsolete_num)
                                seen.add(obsolete_num)
                                graph.complete(obsolete_num)
                                _emit_audit_event(
                                    self._sink,
                                    issue_check_run_id,
                                    AuditEventType.ISSUE_OBSOLETE,
                                    payload={
                                        "closed_issue": obsolete_num,
                                        "fixed_by": issue_num,
                                        "rationale": staleness.rationale.get(obsolete_num, ""),
                                    },
                                )
                                try:
                                    await self._platform.assign_label(  # type: ignore[attr-defined]
                                        obsolete_num,
                                        LABEL_FIXED,
                                    )
                                except Exception:
                                    logger.warning(
                                        "Failed to assign af:fixed label to obsolete issue #%d",
                                        obsolete_num,
                                        exc_info=True,
                                    )
                        except FatalAPIError as exc:
                            logger.error(
                                "Staleness check after fix #%d hit a non-recoverable API error: %s",
                                issue_num,
                                exc,
                            )
                            fatal_error = exc
                            break
                        except Exception:
                            logger.warning(
                                "Staleness check failed after fix #%d",
                                issue_num,
                                exc_info=True,
                            )

            if fatal_error is not None:
                break

            # Fill pool with newly-ready issues
            _fill_pool()
            await _label_ceiling_exceeded()

        if fatal_error is not None:
            if pool:
                logger.warning(
                    "Waiting for %d in-flight fix(es) to finish before aborting",
                    len(pool),
                )
                await asyncio.gather(*pool, return_exceptions=True)
            raise fatal_error

    def _exceeds_attempt_ceiling(self, issue_number: int) -> bool:
        """Check whether the issue has exhausted its cross-run attempt ceiling.

        Returns ``True`` when the number of prior runs for this issue
        meets or exceeds ``night_shift.max_attempts_per_issue``.  When
        the DuckDB store is unavailable the check fails open (returns
        ``False``) and a warning is logged.

        Requirements: NS-REQ-1, NS-REQ-4 (issue #37)
        """
        from afcore.nightshift.prior_attempts import count_prior_runs

        max_attempts = getattr(
            getattr(self._config, "night_shift", None),
            "max_attempts_per_issue",
            3,
        )
        if self._conn is None:
            return False
        try:
            spec_name = f"fix-issue-{issue_number}"
            prior_count = count_prior_runs(self._conn, spec_name)
            if prior_count >= max_attempts:
                logger.warning(
                    "Issue #%d has reached the attempt ceiling (%d/%d prior runs) — skipping dispatch",
                    issue_number,
                    prior_count,
                    max_attempts,
                )
                return True
        except Exception:
            logger.warning(
                "Failed to check attempt ceiling for issue #%d — dispatching (fail-open)",
                issue_number,
                exc_info=True,
            )
        return False

    def _calculate_fix_cost(self, metrics: object) -> float:
        """Return the accumulated per-session cost from FixMetrics.

        Each session's cost is computed at the model tier that actually ran
        (e.g. STANDARD for coder, ADVANCED for reviewer) and accumulated
        into ``FixMetrics.cost_usd`` by the pipeline.  This method simply
        returns that pre-calculated total instead of re-pricing all tokens
        at a single tier.
        """
        return getattr(metrics, "cost_usd", 0.0)

    async def _process_fix(self, issue: object, issue_body: str = "") -> bool:
        """Process a single af:fix issue through the fix pipeline.

        Builds an in-memory spec from the issue, runs the full archetype
        pipeline, harvests the branch, and updates the engine state
        including cost and session counters.

        Returns whether the issue was genuinely fixed -- the branch was
        actually merged into the integration branch -- per
        ``FixMetrics.outcome``.  A pipeline that returns without merging
        (retries exhausted, no changes, an empty issue body, an internal
        exception) or that raises is reported as not fixed, so callers no
        longer have to infer success from "did not raise" (issue #50).

        Requirements: 61-REQ-6.1, 61-REQ-6.2, 61-REQ-6.3, 61-REQ-6.4,
                      61-REQ-9.3, NS-REQ-50 (issue #50)
        """
        from afissues.protocol import IssueResult

        if not isinstance(issue, IssueResult):
            return False

        import time

        fix_run_id = generate_run_id()
        fix_start = time.monotonic()
        self._emit_status(f"Fixing issue #{issue.number}: {issue.title}")

        _emit_audit_event(
            self._sink,
            fix_run_id,
            AuditEventType.FIX_START,
            payload={"issue_number": issue.number, "title": issue.title},
        )

        pipeline = self._build_pipeline()

        effective_body = issue_body if issue_body else getattr(issue, "body", "")
        succeeded = False
        cost = 0.0
        sessions_run = 0
        input_tokens = 0
        output_tokens = 0
        try:
            metrics = await pipeline.process_issue(issue, issue_body=effective_body, run_id=fix_run_id)
            cost = self._calculate_fix_cost(metrics)
            sessions_run = getattr(metrics, "sessions_run", 0)
            input_tokens = getattr(metrics, "input_tokens", 0)
            output_tokens = getattr(metrics, "output_tokens", 0)
            # Only a genuinely merged fix counts as succeeded (issue #50) --
            # "the pipeline call returned without raising" is not evidence
            # the fix landed.
            succeeded = getattr(metrics, "outcome", "failed") == "fixed"
        except Exception:
            logger.warning(
                "Fix pipeline raised unexpectedly for issue #%d",
                issue.number,
                exc_info=True,
            )

        from afcore.ui.progress import format_duration, format_tokens

        duration_ms = int((time.monotonic() - fix_start) * 1000)
        duration_str = format_duration(duration_ms / 1000)

        token_suffix = ""
        if input_tokens or output_tokens:
            token_suffix = f" \u00b7 {format_tokens(input_tokens)}\u2191 {format_tokens(output_tokens)}\u2193"

        if succeeded:
            self._emit_status(f"\u2714 Issue #{issue.number} fixed ({duration_str}){token_suffix}", "bold green")
        else:
            self._emit_status(f"\u2718 Issue #{issue.number} failed ({duration_str}){token_suffix}", "bold red")

        outcome = IssueOutcome(
            issue_number=issue.number,
            title=issue.title,
            run_id=fix_run_id,
            outcome="fixed" if succeeded else "failed",
            duration_ms=duration_ms,
            cost_usd=cost,
            sessions_run=sessions_run,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        await self.state.add_fix_result(cost, sessions_run, outcome, succeeded=succeeded)

        # Push per-issue cost to the daemon-level budget under its lock.
        # This replaces the racy before/after delta sampling that was
        # previously done in EngineWorkStream.run_once().
        if self._budget is not None and cost > 0:
            await self._budget.add_cost_async(cost)

        _emit_audit_event(
            self._sink,
            fix_run_id,
            AuditEventType.FIX_COMPLETE if succeeded else AuditEventType.FIX_FAILED,
            payload={"issue_number": issue.number},
        )
        return succeeded

    async def _drain_issues(self) -> bool:
        """Run issue checks until no open af:fix issues remain.

        Loops calling ``_run_issue_check`` and re-polling the platform until
        zero ``af:fix`` issues are reported.  Respects shutdown, cost, and
        session limits between iterations, and enforces a safety-valve
        maximum iteration count to prevent infinite loops.

        A ``seen`` set is threaded through all iterations so that issues
        already processed (fixed, superseded, or staleness-closed) are never
        re-processed even if the platform still returns them due to API
        propagation delays (fixes #465).

        Returns True when no ``af:fix`` issues remain (drain succeeded),
        False when issues may still exist (limit hit, shutdown, or error).

        Requirements: 81-REQ-1.1, 81-REQ-1.4
        """
        seen: set[int] = set()
        for _ in range(self._MAX_DRAIN_ITERATIONS):
            if self.state.is_shutting_down:
                return False
            if self._check_cost_limit():
                logger.info("Cost limit reached during issue drain")
                return False
            if self._check_session_limit():
                logger.info("Session limit reached during issue drain")
                return False

            seen_before = len(seen)
            await self._run_issue_check(seen)

            # Re-poll to see if any af:fix issues remain.
            # Filter already-processed issues from the re-poll result so that
            # recently-closed issues returned by the platform due to eventual
            # consistency do not cause spurious additional drain iterations
            # (issue #465).
            # NOTE: platform errors are NOT caught here — they propagate to
            # _run_stream_loop which logs at ERROR level and retries on the
            # next interval.  This avoids the fail-open bug where an outage
            # was silently reported as a clean drain (issue #39).
            remaining = await self._platform.list_issues_by_label(  # type: ignore[attr-defined]
                LABEL_FIX,
                sort="created",
                direction="asc",
            )

            # Filter out issues already handled this session so that a
            # recently-closed issue returned by a stale platform response
            # does not trigger another fix iteration.
            # Also exclude af:failed issues (issue #37, NS-REQ-3).
            remaining = [
                r
                for r in remaining
                if r.number not in seen
                and r.number not in self._processed_issues
                and LABEL_FAILED not in (getattr(r, "labels", None) or [])
            ]
            if not remaining:
                return True

            # Issue #90: if this iteration made no progress (nothing was
            # dispatched, completed, or ceiling-skipped) yet issues remain,
            # further iterations will spin without effect.  Break early.
            if len(seen) == seen_before:
                logger.warning(
                    "Drain made no progress: %d issue(s) remain but none were dispatched",
                    len(remaining),
                )
                return False

        logger.warning("Issue drain safety valve reached after %d iterations", self._MAX_DRAIN_ITERATIONS)
        return False

    async def _check_open_prs(self) -> None:
        """Poll for open af:pr issues and process each sequentially.

        Lists issues labelled ``af:pr`` in oldest-first order, caps the
        batch to ``_MAX_PR_CHECKS``, and awaits ``process_pr_issue()``
        for each one.  ``issue_checks_completed`` is incremented after
        each successful call.

        Requirements: 07-REQ-3.1, 07-REQ-3.2, 07-REQ-3.E1, 07-REQ-3.E2
        """
        issues = await self._platform.list_issues_by_label(LABEL_PR)  # type: ignore[attr-defined]
        issues = issues[:_MAX_PR_CHECKS]

        if not issues:
            return

        pipeline = self._build_pipeline()

        for issue in issues:
            await process_pr_issue(
                issue,
                config=self._config,
                platform=self._platform,
                pipeline=pipeline,
            )
            self.state.issue_checks_completed += 1

    async def _run_coder_session(
        self,
        *,
        archetype: str,
        mode: str,
        context: dict[str, object],
    ) -> object:
        """Run a coder session for carry-patch conflict resolution.

        Loads the archetype profile, renders template placeholders with
        values from *context*, resolves SDK parameters for the requested
        mode, and delegates to :func:`run_session`.

        The workspace is constructed from ``context["repo_root"]`` and
        ``context["branch"]``; the coder runs in the repository checkout
        (not in an isolated worktree).  ``repo_root`` is required — a
        missing value raises rather than falling back to the process
        working directory.

        Requirements: 03-REQ-3.3, NS-REQ-6 (issue #43)
        """
        from afcore.core.models import resolve_model
        from afcore.engine.sdk_params import (
            resolve_model_tier,
            resolve_security_config,
            resolve_session_params,
        )
        from afcore.session.profiles import load_profile
        from afcore.session.session import run_session
        from afcore.workspace.worktree import WorkspaceInfo

        # Load the archetype profile template.
        profile = load_profile(archetype, project_dir=self._repo_root, mode=mode)

        # Render template placeholders with context values.
        patch_description = str(context.get("patch_description", ""))
        conflict_files = context.get("conflict_files", [])
        upstream_context = str(context.get("upstream_context", ""))
        rerere_resolutions = context.get("rerere_resolutions", [])

        conflict_files_str = "\n".join(f"- {f}" for f in conflict_files) if conflict_files else "(none)"
        rerere_str = "\n".join(f"- {r}" for r in rerere_resolutions) if rerere_resolutions else "(none)"

        system_prompt = profile.replace("{{ patch_description }}", patch_description)
        system_prompt = system_prompt.replace("{{ conflict_files }}", conflict_files_str)
        system_prompt = system_prompt.replace("{{ upstream_context }}", upstream_context)
        system_prompt = system_prompt.replace("{{ rerere_resolutions }}", rerere_str)

        # Build workspace info for the checked-out branch.  The caller
        # owns the repository root; defaulting to the working directory
        # would silently mask a missing argument (issue #43, AC-3).
        raw_repo_root = context.get("repo_root")
        if not raw_repo_root:
            raise ValueError("_run_coder_session requires 'repo_root' in the session context")
        repo_root = Path(str(raw_repo_root))
        branch = str(context.get("branch", ""))
        workspace = WorkspaceInfo(
            path=repo_root,
            branch=branch,
            spec_name="carry-patch",
            task_group=0,
            role="coder",
            mode=mode,
        )

        # Resolve SDK parameters for the archetype and mode.
        config = self._config
        model_id = resolve_model(
            resolve_model_tier(config, archetype, mode=mode),
            models_config=config.models,
        )
        security = resolve_security_config(config, archetype, mode=mode)
        params = resolve_session_params(config, archetype, mode=mode)

        task_prompt = (
            "Resolve the merge conflicts in the files listed above. Follow the resolution rules in the system prompt."
        )
        node_id = f"carry-patch:{branch}:{archetype}"

        return await run_session(
            workspace=workspace,
            node_id=node_id,
            system_prompt=system_prompt,
            task_prompt=task_prompt,
            config=config,
            activity_callback=self._activity_callback,
            model_id=model_id,
            security_config=security,
            max_turns=params.max_turns,
            max_budget_usd=params.max_budget_usd,
            thinking=params.thinking,
            effort=params.effort,
            compaction=params.compaction,
            cache_policy=params.cache_policy,
            archetype=archetype,
            sink_dispatcher=self._sink,
        )

    async def _run_carry_patch_monitor(self, slug: str) -> object:
        """Delegate to the stored CarryPatchMonitor instance.

        The monitor is stored as ``self._carry_patch_monitor`` and reused
        across all calls to preserve the in-memory session retry counter
        (03-PROP-3).  Any exception raised by the monitor is propagated
        to the caller (03-REQ-7.E2).

        Returns a ``MonitorCycleResult`` instance.

        Requirements: 03-REQ-7.4, 03-REQ-7.E2
        """
        return await self._carry_patch_monitor.run_cycle()
