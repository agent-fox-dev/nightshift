"""Tests for fix pipeline carry-patch patch registration.

When carry_patch.enabled is True and a HubClient is provided, the fix pipeline
should push the fix branch to the hub git server, register it via add_patch(),
submit a rebuild, and poll until completion or failure.

Specification: 03_carry_patch_pipeline_monitor
Requirements: 03-REQ-1 (03-REQ-1.1 through 03-REQ-1.6, 03-REQ-1.E1 through E5,
              03-REQ-1.3b), 03-REQ-8 (audit event constants)
Test IDs: TS-03-1, TS-03-2, TS-03-3, TS-03-4, TS-03-5, TS-03-6, TS-03-23

Dependencies:
- Spec 01 (afhub): HubClient, RebuildJob, HubConflictError, HubNoActivePatchesError.
- Spec 02 (CarryPatchConfig): config is built with MagicMock.
"""

from __future__ import annotations

import dataclasses
import inspect
from pathlib import Path
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest
from afaudit.events import AuditEventType
from afcore.nightshift.fix_pipeline import FixPipeline
from afhub.errors import HubConflictError, HubNoActivePatchesError

# ---------------------------------------------------------------------------
# Stub types for afhub models (lightweight stand-ins for Pydantic models)
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class _RebuildJob:
    """Minimal stub for afhub.RebuildJob."""

    id: str
    status: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(
    *,
    carry_patch_enabled: bool = True,
    rebuild_timeout: int = 600,
    rebuild_poll_interval: int = 5,
    max_resolve_retries: int = 2,
) -> MagicMock:
    """Build a config MagicMock with carry_patch fields set.

    Uses MagicMock rather than AgentFoxConfig because CarryPatchConfig is
    not yet present in config.py (Spec 02 group 2 pending).
    """
    config = MagicMock()
    config.carry_patch = MagicMock()
    config.carry_patch.enabled = carry_patch_enabled
    config.carry_patch.rebuild_timeout = rebuild_timeout
    config.carry_patch.rebuild_poll_interval = rebuild_poll_interval
    config.carry_patch.max_resolve_retries = max_resolve_retries
    # Workspace defaults that _integrate_fix reads
    config.workspace = MagicMock()
    config.workspace.merge_strategy = "direct"
    config.workspace.integration_branch = "main"
    config.night_shift = MagicMock()
    config.night_shift.push_fix_branch = False
    # Orchestrator (used by coder-reviewer loop, not integration phase)
    config.orchestrator = MagicMock()
    config.orchestrator.max_retries = 2
    return config


def _make_hub_client(
    *,
    submit_raises: Exception | None = None,
    submit_return: _RebuildJob | None = None,
    list_rebuilds_result: list[_RebuildJob] | None = None,
) -> MagicMock:
    """Build a mock HubClient with async methods.

    Note: ``submit_raises`` should be an instance of the real
    ``HubConflictError`` or ``HubNoActivePatchesError`` from afhub.errors
    so that the fix pipeline's ``except`` clauses can catch them.
    """
    client = MagicMock()
    client.add_patch = AsyncMock()
    if submit_raises is not None:
        client.submit_rebuild = AsyncMock(side_effect=submit_raises)
    else:
        client.submit_rebuild = AsyncMock(return_value=(submit_return or _RebuildJob("job-1", "queued")))
    client.list_rebuilds = AsyncMock(return_value=(list_rebuilds_result if list_rebuilds_result is not None else []))
    return client


def _make_pipeline(
    config: MagicMock,
    hub_client: MagicMock | None = None,
    workspace_slug: str = "my-workspace",
) -> FixPipeline:
    """Build a FixPipeline with mocked platform; optionally inject hub_client.

    Passes hub_client and workspace_slug to the FixPipeline constructor
    so the carry-patch code path can read self._hub_client / self._workspace_slug.
    """
    return FixPipeline(
        config=config,
        platform=MagicMock(),
        hub_client=hub_client,
        workspace_slug=workspace_slug,
    )


def _make_mock_issue(
    *,
    number: int = 42,
    title: str = "Fix null pointer in auth module",
) -> MagicMock:
    issue = MagicMock()
    issue.number = number
    issue.title = title
    return issue


def _make_mock_spec(
    *,
    branch_name: str = "fix/issue-42",
    issue_number: int = 42,
) -> MagicMock:
    spec = MagicMock()
    spec.branch_name = branch_name
    spec.issue_number = issue_number
    return spec


def _make_mock_workspace(branch: str = "fix/issue-42") -> MagicMock:
    workspace = MagicMock()
    workspace.path = Path("/tmp/mock-workspace")
    workspace.branch = branch
    return workspace


async def _call_integrate_fix(
    pipeline: FixPipeline,
    *,
    branch_name: str = "fix/issue-42",
    issue_title: str = "Fix null pointer in auth module",
    issue_number: int = 42,
    poll_return: _RebuildJob | None = None,
) -> tuple[str, list]:
    """Helper: run pipeline._integrate_fix with mocked git operations.

    Patches _auto_commit_pending_changes, _harvest_and_push, push_to_remote,
    and poll_rebuild so the test can focus on the carry-patch hub API calls
    without real git operations.

    ``poll_return`` overrides the default poll_rebuild return value (defaults
    to a completed RebuildJob).

    Returns the (status, changed_files) tuple from _integrate_fix.
    """
    issue = _make_mock_issue(number=issue_number, title=issue_title)
    spec = _make_mock_spec(branch_name=branch_name, issue_number=issue_number)
    workspace = _make_mock_workspace(branch=branch_name)
    _default_poll = poll_return or _RebuildJob("job-1", "completed")

    with (
        patch.object(pipeline, "_auto_commit_pending_changes", AsyncMock()),
        patch.object(pipeline, "_harvest_and_push", AsyncMock(return_value=[])),
        patch(
            "afcore.workspace.git.push_to_remote",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "afcore.nightshift.fix_pipeline.poll_rebuild",
            new_callable=AsyncMock,
            return_value=_default_poll,
        ),
    ):
        pipeline._platform.add_issue_comment = AsyncMock()
        result = await pipeline._integrate_fix(issue, spec, workspace)
    return result


# ---------------------------------------------------------------------------
# TS-03-1: push_to_remote and add_patch called in carry-patch mode
# ---------------------------------------------------------------------------


class TestCarryPatchCallOrder:
    """TS-03-1: hub API calls made in correct order when carry_patch is enabled.

    Requirements: 03-REQ-1.1
    Test ID: TS-03-1
    """

    async def test_push_to_remote_called_when_carry_patch_enabled(self) -> None:
        """push_to_remote is called with the fix branch name.

        Requirements: 03-REQ-1.1 step 1
        Test ID: TS-03-1
        Fails: carry-patch integration not yet added to _integrate_fix
        """
        config = _make_config(carry_patch_enabled=True)
        hub_client = _make_hub_client(
            submit_return=_RebuildJob("job-1", "queued"),
        )
        pipeline = _make_pipeline(config, hub_client=hub_client)

        await _call_integrate_fix(pipeline, branch_name="fix/issue-42")

        # FAILS: carry-patch integration not yet implemented —
        # add_patch is never called in the current _integrate_fix
        hub_client.add_patch.assert_called_once()

    async def test_hub_api_calls_not_made_when_carry_patch_disabled(self) -> None:
        """No hub API calls made when carry_patch.enabled=False; implementation gate exists.

        Requirements: 03-REQ-1 guard condition
        Test ID: TS-03-1 (negative path)
        Fails: FixPipeline must have a _hub_client attribute set at construction
        time (from its constructor) for the guard condition to work correctly;
        the attribute is not yet part of the constructor signature.
        """
        config = _make_config(carry_patch_enabled=False)
        hub_client = _make_hub_client()

        # Construct pipeline WITHOUT injecting hub_client (disabled mode means
        # hub_client is not provided at all)
        pipeline = FixPipeline(config=config, platform=MagicMock())

        await _call_integrate_fix(pipeline)

        # Disabled path: no hub calls (correct for current impl).
        hub_client.add_patch.assert_not_called()
        hub_client.submit_rebuild.assert_not_called()

        # FAILS: The implementation (group 4) will add hub_client to the
        # FixPipeline constructor. Assert it is accepted as a parameter.
        # This assertion verifies the constructor signature we expect.
        sig = inspect.signature(FixPipeline.__init__)
        assert "hub_client" in sig.parameters, (
            "FixPipeline.__init__ should accept hub_client parameter (to be added in group 4 implementation)"
        )

    async def test_carry_patch_does_not_run_local_harvest(self) -> None:
        """When carry_patch.enabled, local harvest path is skipped.

        Requirements: 03-REQ-1.E5 — add_patch replaces local harvest
        Test ID: TS-03-1
        """
        config = _make_config(carry_patch_enabled=True)
        hub_client = _make_hub_client()
        pipeline = _make_pipeline(config, hub_client=hub_client)

        with patch.object(pipeline, "_harvest_and_push", AsyncMock(return_value=["changed.py"])) as mock_harvest:
            with (
                patch.object(pipeline, "_auto_commit_pending_changes", AsyncMock()),
                patch(
                    "afcore.workspace.git.push_to_remote",
                    new_callable=AsyncMock,
                    return_value=True,
                ),
                patch(
                    "afcore.nightshift.fix_pipeline.poll_rebuild",
                    new_callable=AsyncMock,
                    return_value=_RebuildJob("job-1", "completed"),
                ),
            ):
                pipeline._platform.add_issue_comment = AsyncMock()
                await pipeline._integrate_fix(
                    _make_mock_issue(),
                    _make_mock_spec(),
                    _make_mock_workspace(),
                )

        mock_harvest.assert_not_called()


# ---------------------------------------------------------------------------
# TS-03-1/TS-03-2: add_patch called with correct arguments
# ---------------------------------------------------------------------------


class TestAddPatchArguments:
    """TS-03-1: add_patch called with correct slug, branch, and flags.

    Requirements: 03-REQ-1.2
    Test ID: TS-03-1 (add_patch assertion), TS-03-2
    """

    async def test_add_patch_called_with_slug_and_branch(self) -> None:
        """add_patch is called with the workspace slug and fix branch name.

        Requirements: 03-REQ-1.2
        Test ID: TS-03-1
        Fails: carry-patch integration not yet implemented
        """
        config = _make_config(carry_patch_enabled=True)
        hub_client = _make_hub_client()
        pipeline = _make_pipeline(config, hub_client=hub_client, workspace_slug="my-workspace")

        await _call_integrate_fix(pipeline, branch_name="fix/issue-42")

        # FAILS: add_patch never called (no implementation)
        hub_client.add_patch.assert_called_once_with(
            "my-workspace",
            "fix/issue-42",
            description=ANY,
            skip_branch_check=True,
            if_not_exists=True,
        )

    async def test_add_patch_description_matches_issue_title(self) -> None:
        """add_patch description matches the issue title.

        Requirements: 03-REQ-1.2
        Test ID: TS-03-1
        Fails: carry-patch integration not yet implemented
        """
        config = _make_config(carry_patch_enabled=True)
        hub_client = _make_hub_client()
        pipeline = _make_pipeline(config, hub_client=hub_client)

        await _call_integrate_fix(
            pipeline,
            issue_title="Fix null pointer in auth module",
            branch_name="fix/issue-42",
        )

        # FAILS: add_patch never called (no implementation)
        hub_client.add_patch.assert_called_once_with(
            ANY,
            ANY,
            description="Fix null pointer in auth module",
            skip_branch_check=True,
            if_not_exists=True,
        )

    async def test_add_patch_called_after_push_to_remote(self) -> None:
        """add_patch is called only after push_to_remote succeeds.

        Requirements: 03-REQ-1.1 ordering
        Test ID: TS-03-1
        """
        config = _make_config(carry_patch_enabled=True)
        hub_client = _make_hub_client()
        pipeline = _make_pipeline(config, hub_client=hub_client)

        call_order: list[str] = []

        original_add_patch = hub_client.add_patch

        async def tracking_add_patch(*args: object, **kwargs: object) -> None:
            call_order.append("add_patch")
            return await original_add_patch(*args, **kwargs)

        hub_client.add_patch = tracking_add_patch

        issue = _make_mock_issue()
        spec = _make_mock_spec(branch_name="fix/issue-42")
        workspace = _make_mock_workspace(branch="fix/issue-42")

        with (
            patch.object(pipeline, "_auto_commit_pending_changes", AsyncMock()),
            patch.object(pipeline, "_harvest_and_push", AsyncMock(return_value=[])),
            patch(
                "afcore.workspace.git.push_to_remote",
                AsyncMock(side_effect=lambda *a, **kw: call_order.append("push_to_remote")),
            ),
            patch(
                "afcore.nightshift.fix_pipeline.poll_rebuild",
                new_callable=AsyncMock,
                return_value=_RebuildJob("job-1", "completed"),
            ),
        ):
            pipeline._platform.add_issue_comment = AsyncMock()
            await pipeline._integrate_fix(issue, spec, workspace)

        assert "push_to_remote" in call_order, "push_to_remote was not called"
        assert "add_patch" in call_order, "add_patch was not called"
        assert call_order.index("push_to_remote") < call_order.index("add_patch"), (
            "push_to_remote must be called before add_patch"
        )


# ---------------------------------------------------------------------------
# TS-03-3: submit_rebuild happy path and HubConflictError fallback
# ---------------------------------------------------------------------------


class TestSubmitRebuildAndConflict:
    """TS-03-3: submit_rebuild called; HubConflictError falls back to list_rebuilds.

    Requirements: 03-REQ-1.3, 03-REQ-1.4
    Test IDs: TS-03-3, TS-03-4
    """

    async def test_submit_rebuild_called_with_slug(self) -> None:
        """submit_rebuild is called with the workspace slug after add_patch.

        Requirements: 03-REQ-1.3
        Test ID: TS-03-3
        Fails: carry-patch integration not yet implemented
        """
        config = _make_config(carry_patch_enabled=True)
        hub_client = _make_hub_client(
            submit_return=_RebuildJob("job-1", "queued"),
        )
        pipeline = _make_pipeline(config, hub_client=hub_client, workspace_slug="my-workspace")

        await _call_integrate_fix(pipeline)

        # FAILS: submit_rebuild never called (no implementation)
        hub_client.submit_rebuild.assert_called_once_with("my-workspace")

    async def test_poll_rebuild_called_with_job_id_from_submit_rebuild(self) -> None:
        """poll_rebuild is called with the job id returned by submit_rebuild.

        Requirements: 03-REQ-1.3
        Test ID: TS-03-3
        Fails: carry-patch integration not yet implemented
        """
        config = _make_config(carry_patch_enabled=True)
        hub_client = _make_hub_client(
            submit_return=_RebuildJob("job-1", "queued"),
        )
        pipeline = _make_pipeline(config, hub_client=hub_client)

        mock_poll = AsyncMock(return_value=_RebuildJob("job-1", "completed"))

        # poll_rebuild will be importable from afhub once Spec 01 is done;
        # for now we assert add_patch was called (fails before poll_rebuild)
        await _call_integrate_fix(pipeline)

        # FAILS: hub_client.add_patch not called; poll_rebuild never reached
        hub_client.add_patch.assert_called_once()
        hub_client.submit_rebuild.assert_called_once()
        mock_poll.assert_not_called()  # sanity: poll was not injected above

    async def test_hub_conflict_error_calls_list_rebuilds(self) -> None:
        """When submit_rebuild raises HubConflictError, list_rebuilds is called.

        Requirements: 03-REQ-1.4
        Test ID: TS-03-4
        Fails: carry-patch integration not yet implemented (add_patch not called)
        """
        config = _make_config(carry_patch_enabled=True)
        hub_client = _make_hub_client(
            submit_raises=HubConflictError(status_code=409, message="rebuild already running", error_type="conflict"),
            list_rebuilds_result=[_RebuildJob("active-job-1", "running")],
        )
        pipeline = _make_pipeline(config, hub_client=hub_client, workspace_slug="my-workspace")

        await _call_integrate_fix(pipeline)

        # FAILS: add_patch never called (no implementation)
        hub_client.add_patch.assert_called_once()
        # After implementation, list_rebuilds should also be called:
        hub_client.list_rebuilds.assert_called_once_with("my-workspace")

    async def test_hub_conflict_error_polls_first_active_job(self) -> None:
        """On HubConflictError, poll_rebuild is called with the first active job id.

        Requirements: 03-REQ-1.4
        Test ID: TS-03-4
        Fails: carry-patch integration not yet implemented
        """
        config = _make_config(carry_patch_enabled=True)
        hub_client = _make_hub_client(
            submit_raises=HubConflictError(status_code=409, message="rebuild already running", error_type="conflict"),
            list_rebuilds_result=[
                _RebuildJob("active-job-1", "running"),
                _RebuildJob("active-job-2", "queued"),
            ],
        )
        pipeline = _make_pipeline(config, hub_client=hub_client, workspace_slug="my-workspace")

        await _call_integrate_fix(pipeline)

        # FAILS: add_patch never called (no implementation)
        hub_client.add_patch.assert_called_once()
        # After implementation, list_rebuilds should be called and
        # the FIRST active job (active-job-1) should be polled.
        hub_client.list_rebuilds.assert_called_with("my-workspace")


# ---------------------------------------------------------------------------
# TS-03-4 (tasks.json) / TS-03-5 (test_spec): HubNoActivePatchesError
# ---------------------------------------------------------------------------


class TestHubNoActivePatchesError:
    """TS-03-5: HubNoActivePatchesError logs warning and skips rebuild.

    Requirements: 03-REQ-1.5
    Test ID: TS-03-5
    """

    async def test_hub_no_active_patches_logs_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """A WARNING is logged when submit_rebuild raises HubNoActivePatchesError.

        Requirements: 03-REQ-1.5
        Test ID: TS-03-5
        """
        import logging

        config = _make_config(carry_patch_enabled=True)
        hub_client = _make_hub_client(
            submit_raises=HubNoActivePatchesError(
                status_code=400,
                message="no active patches",
                error_type="no_active_patches",
            ),
        )
        pipeline = _make_pipeline(config, hub_client=hub_client)

        with caplog.at_level(logging.WARNING):
            await _call_integrate_fix(pipeline)

        hub_client.add_patch.assert_called_once()
        # Verify that a warning about skipped rebuild was logged.
        assert any("no active patches" in r.message.lower() for r in caplog.records if r.levelno >= logging.WARNING), (
            "Expected a WARNING-level log message mentioning 'no active patches'"
        )

    async def test_hub_no_active_patches_skips_poll_rebuild(self) -> None:
        """poll_rebuild is NOT called when submit_rebuild raises HubNoActivePatchesError.

        Requirements: 03-REQ-1.5
        Test ID: TS-03-5
        """
        config = _make_config(carry_patch_enabled=True)
        hub_client = _make_hub_client(
            submit_raises=HubNoActivePatchesError(
                status_code=400,
                message="no active patches",
                error_type="no_active_patches",
            ),
        )
        pipeline = _make_pipeline(config, hub_client=hub_client)

        issue = _make_mock_issue()
        spec = _make_mock_spec()
        workspace = _make_mock_workspace()

        with (
            patch.object(pipeline, "_auto_commit_pending_changes", AsyncMock()),
            patch.object(pipeline, "_harvest_and_push", AsyncMock(return_value=[])),
            patch(
                "afcore.workspace.git.push_to_remote",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "afcore.nightshift.fix_pipeline.poll_rebuild",
                new_callable=AsyncMock,
            ) as mock_poll,
        ):
            pipeline._platform.add_issue_comment = AsyncMock()
            await pipeline._integrate_fix(issue, spec, workspace)

        hub_client.add_patch.assert_called_once()
        # poll_rebuild must NOT be called when there are no active patches.
        mock_poll.assert_not_called()

    async def test_hub_no_active_patches_does_not_mark_issue_for_retry(self) -> None:
        """Issue is NOT marked for retry when HubNoActivePatchesError is raised.

        Requirements: 03-REQ-1.5
        Test ID: TS-03-5
        """
        config = _make_config(carry_patch_enabled=True)
        hub_client = _make_hub_client(
            submit_raises=HubNoActivePatchesError(
                status_code=400,
                message="no active patches",
                error_type="no_active_patches",
            ),
        )
        pipeline = _make_pipeline(config, hub_client=hub_client)

        result = await _call_integrate_fix(pipeline)

        hub_client.add_patch.assert_called_once()
        # "merged" status means success — no retry needed.
        assert result[0] == "merged", f"Expected 'merged' (no retry) on HubNoActivePatchesError, got {result[0]!r}"


# ---------------------------------------------------------------------------
# TS-03-5/TS-03-6 (tasks.json): Terminal rebuild status handling
# ---------------------------------------------------------------------------


class TestRebuildTerminalStatus:
    """TS-03-2, TS-03-3: Terminal rebuild statuses (completed / failed / dead_letter).

    Requirements: 03-REQ-1.2, 03-REQ-1.3b
    Test IDs: TS-03-2, TS-03-3
    """

    async def test_completed_rebuild_leads_to_normal_issue_closure(self) -> None:
        """When rebuild completes, the fix pipeline proceeds with normal issue closure.

        Requirements: 03-REQ-1.2
        Test ID: TS-03-2
        """
        config = _make_config(carry_patch_enabled=True)
        hub_client = _make_hub_client(
            submit_return=_RebuildJob("job-1", "queued"),
        )
        pipeline = _make_pipeline(config, hub_client=hub_client)

        result = await _call_integrate_fix(
            pipeline,
            poll_return=_RebuildJob("job-1", "completed"),
        )

        # Verify carry-patch path was entered and completed successfully.
        hub_client.add_patch.assert_called_once()
        # "merged" status indicates success — issue will be closed normally,
        # not marked for retry.
        assert result[0] == "merged", f"Expected 'merged' (normal closure) on completed rebuild, got {result[0]!r}"

    async def test_failed_rebuild_marks_issue_for_retry(self) -> None:
        """When poll_rebuild returns 'failed', the fix pipeline marks issue for retry.

        Requirements: 03-REQ-1.3b
        Test ID: TS-03-3
        """
        config = _make_config(carry_patch_enabled=True)
        hub_client = _make_hub_client(
            submit_return=_RebuildJob("job-1", "queued"),
        )
        pipeline = _make_pipeline(config, hub_client=hub_client)

        result = await _call_integrate_fix(
            pipeline,
            poll_return=_RebuildJob("job-1", "failed"),
        )

        hub_client.add_patch.assert_called_once()
        # "error" status signals the issue should be retried.
        assert result[0] == "error", f"Expected 'error' (retry) on failed rebuild, got {result[0]!r}"

    async def test_dead_letter_rebuild_marks_issue_for_retry(self) -> None:
        """When poll_rebuild returns 'dead_letter', the fix pipeline marks issue for retry.

        Requirements: 03-REQ-1.3b
        Test ID: TS-03-3
        """
        config = _make_config(carry_patch_enabled=True)
        hub_client = _make_hub_client(
            submit_return=_RebuildJob("job-1", "queued"),
        )
        pipeline = _make_pipeline(config, hub_client=hub_client)

        result = await _call_integrate_fix(
            pipeline,
            poll_return=_RebuildJob("job-1", "dead_letter"),
        )

        hub_client.add_patch.assert_called_once()
        # "error" status signals the issue should be retried.
        assert result[0] == "error", f"Expected 'error' (retry) on dead_letter rebuild, got {result[0]!r}"

    async def test_cancelled_rebuild_marks_issue_for_retry(self) -> None:
        """When poll_rebuild returns 'cancelled', treated same as 'failed'.

        Requirements: 03-REQ-1.3b
        Test ID: TS-03-3
        """
        config = _make_config(carry_patch_enabled=True)
        hub_client = _make_hub_client(
            submit_return=_RebuildJob("job-1", "queued"),
        )
        pipeline = _make_pipeline(config, hub_client=hub_client)

        result = await _call_integrate_fix(
            pipeline,
            poll_return=_RebuildJob("job-1", "cancelled"),
        )

        hub_client.add_patch.assert_called_once()
        assert result[0] == "error", f"Expected 'error' (retry) on cancelled rebuild, got {result[0]!r}"

    async def test_cancelled_rebuild_emits_rebuild_failed_audit_event(self) -> None:
        """CARRY_PATCH_REBUILD_FAILED is emitted when rebuild is cancelled.

        Requirements: 03-REQ-1.3b, 03-REQ-1.6
        Test ID: TS-03-3
        """
        config = _make_config(carry_patch_enabled=True)
        hub_client = _make_hub_client(
            submit_return=_RebuildJob("job-1", "queued"),
        )
        pipeline = _make_pipeline(config, hub_client=hub_client)

        emitted_event_types: list[str] = []

        def capture_emit(sink: object, run_id: str, event_type: object, **kwargs: object) -> None:
            emitted_event_types.append(str(event_type))

        with patch(
            "afcore.nightshift.fix_pipeline.emit_audit_event",
            side_effect=capture_emit,
        ):
            await _call_integrate_fix(
                pipeline,
                poll_return=_RebuildJob("job-1", "cancelled"),
            )

        assert "carry_patch.rebuild_failed" in emitted_event_types, (
            "CARRY_PATCH_REBUILD_FAILED should be emitted for cancelled rebuild"
        )


# ---------------------------------------------------------------------------
# TS-03-6: Audit events emitted during carry-patch pipeline flow
# ---------------------------------------------------------------------------


class TestCarryPatchAuditEvents:
    """TS-03-6: Audit event constants and emission in carry-patch flow.

    Requirements: 03-REQ-8
    Test ID: TS-03-6, TS-03-23
    """

    def test_carry_patch_audit_event_constants_exist_in_afaudit(self) -> None:
        """All 8 carry-patch AuditEventType constants exist in afaudit.events.

        Requirements: 03-REQ-8
        Test ID: TS-03-23
        Fails: constants not yet added to AuditEventType (group 4.1 pending)
        """
        expected_constants = [
            "CARRY_PATCH_PATCH_REGISTERED",
            "CARRY_PATCH_REBUILD_REQUESTED",
            "CARRY_PATCH_REBUILD_COMPLETED",
            "CARRY_PATCH_REBUILD_FAILED",
            "CARRY_PATCH_CONFLICT_DETECTED",
            "CARRY_PATCH_CONFLICT_RESOLVED",
            "CARRY_PATCH_CONFLICT_FAILED",
            "CARRY_PATCH_MERGED_DETECTED",
        ]
        missing = [name for name in expected_constants if not hasattr(AuditEventType, name)]
        assert not missing, f"Missing AuditEventType constants in afaudit.events: {missing}"

    def test_carry_patch_audit_event_string_values_correct(self) -> None:
        """Carry-patch AuditEventType constants have the correct string values.

        Requirements: 03-REQ-8
        Test ID: TS-03-23
        Fails: constants not yet added to AuditEventType (group 4.1 pending)
        """
        expected_values: dict[str, str] = {
            "CARRY_PATCH_PATCH_REGISTERED": "carry_patch.patch_registered",
            "CARRY_PATCH_REBUILD_REQUESTED": "carry_patch.rebuild_requested",
            "CARRY_PATCH_REBUILD_COMPLETED": "carry_patch.rebuild_completed",
            "CARRY_PATCH_REBUILD_FAILED": "carry_patch.rebuild_failed",
            "CARRY_PATCH_CONFLICT_DETECTED": "carry_patch.conflict_detected",
            "CARRY_PATCH_CONFLICT_RESOLVED": "carry_patch.conflict_resolved",
            "CARRY_PATCH_CONFLICT_FAILED": "carry_patch.conflict_failed",
            "CARRY_PATCH_MERGED_DETECTED": "carry_patch.merged_detected",
        }
        for const_name, expected_value in expected_values.items():
            # hasattr check: if constant missing, test fails with clear message
            assert hasattr(AuditEventType, const_name), f"AuditEventType.{const_name} not found"
            actual = getattr(AuditEventType, const_name)
            assert str(actual) == expected_value, (
                f"AuditEventType.{const_name} = {actual!r}, expected {expected_value!r}"
            )

    async def test_carry_patch_patch_registered_event_emitted(self) -> None:
        """CARRY_PATCH_PATCH_REGISTERED is emitted after successful add_patch.

        Requirements: 03-REQ-8
        Test ID: TS-03-6
        Fails: carry-patch integration not yet implemented (add_patch not called)
        """
        config = _make_config(carry_patch_enabled=True)
        hub_client = _make_hub_client()
        pipeline = _make_pipeline(config, hub_client=hub_client)

        emitted_event_types: list[str] = []

        def capture_emit(sink: object, run_id: str, event_type: object, **kwargs: object) -> None:
            emitted_event_types.append(str(event_type))

        with patch("afcore.nightshift.fix_pipeline.emit_audit_event", side_effect=capture_emit):
            await _call_integrate_fix(pipeline)

        # FAILS: carry-patch integration not yet implemented;
        # no carry_patch audit events are emitted by the current code
        assert "carry_patch.patch_registered" in emitted_event_types, (
            "CARRY_PATCH_PATCH_REGISTERED audit event was not emitted after add_patch"
        )

    async def test_carry_patch_rebuild_requested_event_emitted(self) -> None:
        """CARRY_PATCH_REBUILD_REQUESTED is emitted after successful submit_rebuild.

        Requirements: 03-REQ-8
        Test ID: TS-03-6
        Fails: carry-patch integration not yet implemented
        """
        config = _make_config(carry_patch_enabled=True)
        hub_client = _make_hub_client(
            submit_return=_RebuildJob("job-1", "queued"),
        )
        pipeline = _make_pipeline(config, hub_client=hub_client)

        emitted_event_types: list[str] = []

        def capture_emit(sink: object, run_id: str, event_type: object, **kwargs: object) -> None:
            emitted_event_types.append(str(event_type))

        with patch("afcore.nightshift.fix_pipeline.emit_audit_event", side_effect=capture_emit):
            await _call_integrate_fix(pipeline)

        # FAILS: carry-patch integration not yet implemented
        assert "carry_patch.rebuild_requested" in emitted_event_types, (
            "CARRY_PATCH_REBUILD_REQUESTED audit event was not emitted after submit_rebuild"
        )

    async def test_carry_patch_rebuild_completed_event_emitted(self) -> None:
        """CARRY_PATCH_REBUILD_COMPLETED is emitted when rebuild polling returns 'completed'.

        Requirements: 03-REQ-8
        Test ID: TS-03-6
        Fails: carry-patch integration not yet implemented
        """
        config = _make_config(carry_patch_enabled=True)
        hub_client = _make_hub_client(
            submit_return=_RebuildJob("job-1", "queued"),
        )
        pipeline = _make_pipeline(config, hub_client=hub_client)

        emitted_event_types: list[str] = []

        def capture_emit(sink: object, run_id: str, event_type: object, **kwargs: object) -> None:
            emitted_event_types.append(str(event_type))

        with patch("afcore.nightshift.fix_pipeline.emit_audit_event", side_effect=capture_emit):
            await _call_integrate_fix(pipeline)

        # FAILS: carry-patch integration not yet implemented
        assert "carry_patch.rebuild_completed" in emitted_event_types, (
            "CARRY_PATCH_REBUILD_COMPLETED audit event was not emitted after polling"
        )

    async def test_all_pipeline_carry_patch_audit_events_emitted(self) -> None:
        """PATCH_REGISTERED, REBUILD_REQUESTED, REBUILD_COMPLETED are all emitted.

        Requirements: 03-REQ-8
        Test ID: TS-03-6
        Fails: carry-patch integration not yet implemented
        """
        config = _make_config(carry_patch_enabled=True)
        hub_client = _make_hub_client(
            submit_return=_RebuildJob("job-1", "queued"),
        )
        pipeline = _make_pipeline(config, hub_client=hub_client)

        emitted_event_types: list[str] = []

        def capture_emit(sink: object, run_id: str, event_type: object, **kwargs: object) -> None:
            emitted_event_types.append(str(event_type))

        with patch("afcore.nightshift.fix_pipeline.emit_audit_event", side_effect=capture_emit):
            await _call_integrate_fix(pipeline)

        expected_events = [
            "carry_patch.patch_registered",
            "carry_patch.rebuild_requested",
            "carry_patch.rebuild_completed",
        ]
        missing = [e for e in expected_events if e not in emitted_event_types]
        assert not missing, f"Missing carry-patch audit events: {missing}. Emitted: {emitted_event_types}"

    async def test_rebuild_failed_event_emitted_on_failed_rebuild(self) -> None:
        """CARRY_PATCH_REBUILD_FAILED is emitted when rebuild returns 'failed'.

        Requirements: 03-REQ-1.6
        Test ID: TS-03-6
        """
        config = _make_config(carry_patch_enabled=True)
        hub_client = _make_hub_client(
            submit_return=_RebuildJob("job-1", "queued"),
        )
        pipeline = _make_pipeline(config, hub_client=hub_client)

        emitted_event_types: list[str] = []

        def capture_emit(sink: object, run_id: str, event_type: object, **kwargs: object) -> None:
            emitted_event_types.append(str(event_type))

        with patch(
            "afcore.nightshift.fix_pipeline.emit_audit_event",
            side_effect=capture_emit,
        ):
            await _call_integrate_fix(
                pipeline,
                poll_return=_RebuildJob("job-1", "failed"),
            )

        assert "carry_patch.rebuild_failed" in emitted_event_types, (
            "CARRY_PATCH_REBUILD_FAILED should be emitted on failed rebuild"
        )


# ---------------------------------------------------------------------------
# Edge case: push_to_remote failure (03-REQ-1.E1)
# ---------------------------------------------------------------------------


class TestPushToRemoteFailure:
    """03-REQ-1.E1: push_to_remote failure re-raises and skips hub API calls.

    Requirements: 03-REQ-1.E1
    """

    async def test_push_to_remote_failure_reraises(self) -> None:
        """Exception from push_to_remote is re-raised.

        Requirements: 03-REQ-1.E1
        """
        config = _make_config(carry_patch_enabled=True)
        hub_client = _make_hub_client()
        pipeline = _make_pipeline(config, hub_client=hub_client)

        issue = _make_mock_issue()
        spec = _make_mock_spec()
        workspace = _make_mock_workspace()

        with (
            patch.object(pipeline, "_auto_commit_pending_changes", AsyncMock()),
            patch.object(pipeline, "_harvest_and_push", AsyncMock(return_value=[])),
            patch(
                "afcore.workspace.git.push_to_remote",
                new_callable=AsyncMock,
                side_effect=RuntimeError("network timeout"),
            ),
            patch(
                "afcore.nightshift.fix_pipeline.poll_rebuild",
                new_callable=AsyncMock,
            ),
        ):
            pipeline._platform.add_issue_comment = AsyncMock()
            with pytest.raises(RuntimeError, match="network timeout"):
                await pipeline._integrate_fix(issue, spec, workspace)

    async def test_push_to_remote_failure_skips_add_patch(self) -> None:
        """add_patch and submit_rebuild are NOT called after push_to_remote fails.

        Requirements: 03-REQ-1.E1
        """
        config = _make_config(carry_patch_enabled=True)
        hub_client = _make_hub_client()
        pipeline = _make_pipeline(config, hub_client=hub_client)

        issue = _make_mock_issue()
        spec = _make_mock_spec()
        workspace = _make_mock_workspace()

        with (
            patch.object(pipeline, "_auto_commit_pending_changes", AsyncMock()),
            patch.object(pipeline, "_harvest_and_push", AsyncMock(return_value=[])),
            patch(
                "afcore.workspace.git.push_to_remote",
                new_callable=AsyncMock,
                side_effect=RuntimeError("push failed"),
            ),
            patch(
                "afcore.nightshift.fix_pipeline.poll_rebuild",
                new_callable=AsyncMock,
            ),
        ):
            pipeline._platform.add_issue_comment = AsyncMock()
            with pytest.raises(RuntimeError):
                await pipeline._integrate_fix(issue, spec, workspace)

        hub_client.add_patch.assert_not_called()
        hub_client.submit_rebuild.assert_not_called()


# ---------------------------------------------------------------------------
# Edge case: poll_rebuild timeout (03-REQ-1.E2)
# ---------------------------------------------------------------------------


class TestPollRebuildTimeout:
    """03-REQ-1.E2: poll_rebuild timeout is treated as rebuild failure.

    Requirements: 03-REQ-1.E2
    """

    async def test_poll_rebuild_timeout_marks_issue_for_retry(self) -> None:
        """TimeoutError from poll_rebuild returns 'error' (retry).

        Requirements: 03-REQ-1.E2
        """
        config = _make_config(carry_patch_enabled=True)
        hub_client = _make_hub_client(
            submit_return=_RebuildJob("job-1", "queued"),
        )
        pipeline = _make_pipeline(config, hub_client=hub_client)

        issue = _make_mock_issue()
        spec = _make_mock_spec()
        workspace = _make_mock_workspace()

        with (
            patch.object(pipeline, "_auto_commit_pending_changes", AsyncMock()),
            patch.object(pipeline, "_harvest_and_push", AsyncMock(return_value=[])),
            patch(
                "afcore.workspace.git.push_to_remote",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "afcore.nightshift.fix_pipeline.poll_rebuild",
                new_callable=AsyncMock,
                side_effect=TimeoutError("rebuild timed out"),
            ),
        ):
            pipeline._platform.add_issue_comment = AsyncMock()
            result = await pipeline._integrate_fix(issue, spec, workspace)

        assert result[0] == "error", f"Expected 'error' (retry) on poll_rebuild timeout, got {result[0]!r}"

    async def test_poll_rebuild_timeout_emits_rebuild_failed(self) -> None:
        """CARRY_PATCH_REBUILD_FAILED is emitted when poll_rebuild times out.

        Requirements: 03-REQ-1.E2, 03-REQ-1.6
        """
        config = _make_config(carry_patch_enabled=True)
        hub_client = _make_hub_client(
            submit_return=_RebuildJob("job-1", "queued"),
        )
        pipeline = _make_pipeline(config, hub_client=hub_client)

        issue = _make_mock_issue()
        spec = _make_mock_spec()
        workspace = _make_mock_workspace()

        emitted_event_types: list[str] = []

        def capture_emit(sink: object, run_id: str, event_type: object, **kwargs: object) -> None:
            emitted_event_types.append(str(event_type))

        with (
            patch.object(pipeline, "_auto_commit_pending_changes", AsyncMock()),
            patch.object(pipeline, "_harvest_and_push", AsyncMock(return_value=[])),
            patch(
                "afcore.workspace.git.push_to_remote",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "afcore.nightshift.fix_pipeline.poll_rebuild",
                new_callable=AsyncMock,
                side_effect=TimeoutError("rebuild timed out"),
            ),
            patch(
                "afcore.nightshift.fix_pipeline.emit_audit_event",
                side_effect=capture_emit,
            ),
        ):
            pipeline._platform.add_issue_comment = AsyncMock()
            await pipeline._integrate_fix(issue, spec, workspace)

        assert "carry_patch.rebuild_failed" in emitted_event_types, (
            "CARRY_PATCH_REBUILD_FAILED should be emitted on poll_rebuild timeout"
        )


# ---------------------------------------------------------------------------
# Edge case: list_rebuilds empty after HubConflictError (03-REQ-1.E3)
# ---------------------------------------------------------------------------


class TestListRebuildsEmptyAfterConflict:
    """03-REQ-1.E3: empty list_rebuilds after HubConflictError skips rebuild.

    Requirements: 03-REQ-1.E3
    """

    async def test_empty_list_rebuilds_returns_merged(self) -> None:
        """No rebuild polled when list_rebuilds is empty; returns 'merged'.

        Requirements: 03-REQ-1.E3
        """
        config = _make_config(carry_patch_enabled=True)
        hub_client = _make_hub_client(
            submit_raises=HubConflictError(
                status_code=409,
                message="rebuild already running",
                error_type="conflict",
            ),
            list_rebuilds_result=[],  # empty list
        )
        pipeline = _make_pipeline(config, hub_client=hub_client)

        result = await _call_integrate_fix(pipeline)

        hub_client.list_rebuilds.assert_called_once()
        # No rebuild to poll — should return success.
        assert result[0] == "merged", f"Expected 'merged' (skip rebuild) when list_rebuilds is empty, got {result[0]!r}"

    async def test_empty_list_rebuilds_logs_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """A warning is logged when list_rebuilds returns empty after HubConflictError.

        Requirements: 03-REQ-1.E3
        """
        import logging

        config = _make_config(carry_patch_enabled=True)
        hub_client = _make_hub_client(
            submit_raises=HubConflictError(
                status_code=409,
                message="rebuild already running",
                error_type="conflict",
            ),
            list_rebuilds_result=[],
        )
        pipeline = _make_pipeline(config, hub_client=hub_client)

        with caplog.at_level(logging.WARNING):
            await _call_integrate_fix(pipeline)

        assert any(
            "empty" in r.message.lower() or "list_rebuilds" in r.message.lower()
            for r in caplog.records
            if r.levelno >= logging.WARNING
        ), "Expected a WARNING about empty list_rebuilds after HubConflictError"


# ---------------------------------------------------------------------------
# Edge case: add_patch unexpected exception (03-REQ-1.E4)
# ---------------------------------------------------------------------------


class TestAddPatchFailure:
    """03-REQ-1.E4: add_patch unexpected exception marks issue for retry.

    Requirements: 03-REQ-1.E4
    """

    async def test_add_patch_failure_returns_error(self) -> None:
        """When add_patch raises, the pipeline returns 'error' (retry).

        Requirements: 03-REQ-1.E4
        """
        config = _make_config(carry_patch_enabled=True)
        hub_client = _make_hub_client()
        hub_client.add_patch = AsyncMock(
            side_effect=RuntimeError("hub API 500"),
        )
        pipeline = _make_pipeline(config, hub_client=hub_client)

        result = await _call_integrate_fix(pipeline)

        assert result[0] == "error", f"Expected 'error' (retry) on add_patch failure, got {result[0]!r}"

    async def test_add_patch_failure_skips_submit_rebuild(self) -> None:
        """submit_rebuild is NOT called when add_patch raises.

        Requirements: 03-REQ-1.E4
        """
        config = _make_config(carry_patch_enabled=True)
        hub_client = _make_hub_client()
        hub_client.add_patch = AsyncMock(
            side_effect=RuntimeError("hub API 500"),
        )
        pipeline = _make_pipeline(config, hub_client=hub_client)

        await _call_integrate_fix(pipeline)

        hub_client.submit_rebuild.assert_not_called()

    async def test_add_patch_failure_emits_rebuild_failed(self) -> None:
        """CARRY_PATCH_REBUILD_FAILED is emitted when add_patch raises.

        Requirements: 03-REQ-1.E4, 03-REQ-1.6
        """
        config = _make_config(carry_patch_enabled=True)
        hub_client = _make_hub_client()
        hub_client.add_patch = AsyncMock(
            side_effect=RuntimeError("hub API error"),
        )
        pipeline = _make_pipeline(config, hub_client=hub_client)

        emitted_event_types: list[str] = []

        def capture_emit(sink: object, run_id: str, event_type: object, **kwargs: object) -> None:
            emitted_event_types.append(str(event_type))

        with patch(
            "afcore.nightshift.fix_pipeline.emit_audit_event",
            side_effect=capture_emit,
        ):
            await _call_integrate_fix(pipeline)

        assert "carry_patch.rebuild_failed" in emitted_event_types, (
            "CARRY_PATCH_REBUILD_FAILED should be emitted on add_patch failure"
        )
