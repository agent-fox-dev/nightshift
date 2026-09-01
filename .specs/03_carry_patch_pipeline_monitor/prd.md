---
spec_id: '03'
spec_name: carry_patch_pipeline_monitor
title: Carry Patch Pipeline Monitor
status: draft
created_at: '2026-09-01T10:38:53.984585+00:00'
updated_at: '2026-09-01T10:38:53.984585+00:00'
owner: ''
source: interactive
schema_version: 1
---
---
title: "carry_patch_pipeline_monitor: Fix Pipeline Integration and Conflict Monitor"
owner: nightshift
status: draft
source: "docs/proposals/carry_patch_support.md"
---

# carry_patch_pipeline_monitor: Fix Pipeline Integration and Conflict Monitor

## Overview

Implement the core carry-patch work loop in nightshift: (1) modify the fix
pipeline to register completed fix branches as hub patches and trigger a hub
rebuild instead of local squash-merge, and (2) add a `CarryPatchMonitor` work
stream that polls the hub for patches in conflict status and resolves them
using the existing coder archetype with a new `carry-patch` mode and profile.
Also adds audit event types for carry-patch operations and registers the
monitoring stream in the daemon.

## Goals

- In carry-patch mode, the fix pipeline skips local harvest and instead pushes
  the fix branch to the hub git server, registers it as a patch via
  `HubClient.add_patch()`, submits a rebuild, and polls until completion.
- `CarryPatchMonitor.run_cycle()` fetches the patch-status dashboard, detects
  patches in `conflict` status, resolves each by invoking the coder archetype
  in `carry-patch` mode, pushes the resolved branch, and triggers a rebuild.
- A `carry-patch` stream is registered in `build_streams()` alongside
  `fix-pipeline` and `pr-feedback` when carry-patch is configured.
- A `coder:carry-patch` archetype mode instructs the AI agent on how to
  preserve patch intent while adapting to upstream changes.
- Carry-patch audit event types are added to `AuditEventType`.

## Tech Stack

- Python 3.12+, asyncio
- `packages/agentfox/agentfox/nightshift/fix_pipeline.py` — existing fix pipeline
- `packages/agentfox/agentfox/nightshift/streams.py` — stream registration
- `packages/agentfox/agentfox/nightshift/engine.py` — engine method
- `packages/agentfox/agentfox/nightshift/daemon.py` — display names
- `packages/agentfox/agentfox/archetypes.py` — archetype mode registry
- `packages/agentfox/agentfox/_templates/profiles/` — profile templates
- `packages/afaudit/afaudit/events.py` — audit event types
- `afhub` package (Spec 01_afhub_client): HubClient, poll_rebuild, HubConflictError,
  HubNoActivePatchesError, PatchStatusDashboard, RebuildJob
- `agentfox.core.config`: AgentFoxConfig with CarryPatchConfig (Spec 02)
- Existing: `FixPipeline._run_coder_session()`, `run_git()`, `push_to_remote()`,
  `fetch_remote()`, `MergeLock`, `emit_audit_event()`

## Out of Scope

- Hub workspace creation, reclone, or workspace variable management (Spec 2)
- HubClient implementation (Spec 1)
- CWD validation or CLI flag handling (Spec 2)
- Rerere management beyond querying for context
- Manual CLI subcommands for carry-patch operations (users use `afc` for that)

## Dependencies

| Spec | From Group | To Group | Relationship |
|------|-----------|----------|--------------|
| 01_afhub_client | 1 | 1 | HubClient, poll_rebuild, model types, error types |
| 02_carry_patch_bootstrap | 1 | 1 | CarryPatchConfig fields, HubClient instance passed from app.py |

## Requirements

### REQ-1: Fix pipeline patch registration

When `config.carry_patch.enabled` is `True` and a `HubClient` is provided,
the fix pipeline's integration phase (after the coder-reviewer loop) replaces
the local harvest/squash-merge with:

1. Push the fix branch to the hub git server using the existing `push_to_remote()`.
2. Call `hub_client.add_patch(slug, branch_name, description=..., skip_branch_check=True, if_not_exists=True)`.
3. Call `hub_client.submit_rebuild(slug)` (returns 202 + queued `RebuildJob`).
   If `HubConflictError` is raised (concurrent rebuild), retrieve the active
   rebuild via `hub_client.list_rebuilds(slug)` and poll that one instead.
   If `HubNoActivePatchesError` is raised, log a warning and skip the rebuild.
4. Poll to terminal status via `poll_rebuild(hub_client, slug, job.id, timeout=..., interval=...)`.
5. On `completed` status: proceed with normal issue closure.
6. On `failed`/`dead_letter`: log the error and mark the issue for retry
   (same behavior as a failed local harvest).

### REQ-2: CarryPatchMonitor class

Create `packages/agentfox/agentfox/nightshift/carry_patch_monitor.py`:

```python
class CarryPatchMonitor:
    def __init__(
        self,
        hub_client: HubClient,
        workspace_slug: str,
        config: AgentFoxConfig,
        engine: NightShiftEngine,
    ) -> None: ...

    async def run_cycle(self) -> MonitorCycleResult: ...
```

`MonitorCycleResult` is a dataclass with:
- `conflicts_detected: int`
- `conflicts_resolved: int`
- `conflicts_failed: int`
- `patches_merged: int` (informational — patches transitioned to merged_upstream)
- `rebuild_triggered: bool`

### REQ-3: Monitor cycle logic

`run_cycle()` executes the following steps on each invocation:

1. Fetch `PatchStatusDashboard` via `hub_client.get_patch_status(slug)`. Log
   any hub errors and return an empty `MonitorCycleResult` (fail-open).
2. Log any patches in `merged_upstream` status for informational tracking.
3. If `config.carry_patch.auto_resolve` is `False`, log conflict count and
   return without resolving.
4. For each `PatchDetail` with `status == "conflict"` (in position order):
   a. If the patch has already been attempted `max_resolve_retries` times
      this session (tracked in a per-slug, per-patch-id in-memory counter),
      skip it and log a warning.
   b. Check out the patch branch locally from the hub git server clone via
      `fetch_remote()` + `checkout_branch()`.
   c. Invoke the coder via `engine._run_coder_session()` or equivalent with
      `archetype="coder"`, `mode="carry-patch"`, passing conflict context.
   d. On successful resolution: commit, push branch, submit rebuild, poll.
   e. On failed resolution: increment retry counter, emit audit event.
5. Return `MonitorCycleResult` with counts.

### REQ-4: Conflict resolution context

When invoking the coder session for conflict resolution, pass a context dict
containing:
- `patch_description`: from `PatchDetail` (if available from the dashboard)
- `conflict_files`: list of conflicting file paths from `PatchDetail.conflict_files`
- `upstream_context`: diff of changes since last successful rebuild (obtained
  via local `git diff` between the integration branch and upstream HEAD)
- `rerere_resolutions`: list of paths from `hub_client.list_rerere(slug)` for
  context (does not modify rerere state)

### REQ-5: Carry-patch archetype mode

Add to `coder` archetype in `packages/agentfox/agentfox/archetypes.py`:

```python
"carry-patch": ModeConfig(
    model_tier="STANDARD",
    max_turns=200,
    thinking_mode="adaptive",
    effort="high",
)
```

### REQ-6: Carry-patch profile template

Create `packages/agentfox/agentfox/_templates/profiles/coder_carry-patch.md`.
The profile instructs the agent to:
- Preserve the patch's original intent (explain the intent from the provided description)
- Adapt to upstream changes in the conflict files only — no unrelated refactoring
- Use conventional commits (`fix: resolve conflict in <file>`)
- Run tests if available to verify the resolution
- Explain the resolution in the commit message body

### REQ-7: Stream registration

In `packages/agentfox/agentfox/nightshift/streams.py`, `build_streams()`
adds a `CarryPatchStream` (wrapping `CarryPatchMonitor.run_cycle`) when:
- `config.carry_patch.enabled` is `True`, AND
- A `HubClient` instance is available (passed from app.py)

The stream has:
- `name = "carry-patch"`
- `check_interval = config.carry_patch.check_interval`
- `enabled = True`

In `packages/agentfox/agentfox/nightshift/daemon.py`, add `"carry-patch"` to
`_STREAM_DISPLAY_NAMES` and `_STREAM_ACTIVE_LABELS` dicts.

In `packages/agentfox/agentfox/nightshift/engine.py`, add
`_run_carry_patch_monitor(self, slug: str) -> MonitorCycleResult` that
delegates to `CarryPatchMonitor`.

### REQ-8: Audit event types

Add to `AuditEventType` in `packages/afaudit/afaudit/events.py`:

```python
CARRY_PATCH_CONFLICT_DETECTED = "carry_patch.conflict_detected"
CARRY_PATCH_CONFLICT_RESOLVED = "carry_patch.conflict_resolved"
CARRY_PATCH_CONFLICT_FAILED = "carry_patch.conflict_failed"
CARRY_PATCH_PATCH_REGISTERED = "carry_patch.patch_registered"
CARRY_PATCH_REBUILD_REQUESTED = "carry_patch.rebuild_requested"
CARRY_PATCH_REBUILD_COMPLETED = "carry_patch.rebuild_completed"
CARRY_PATCH_REBUILD_FAILED = "carry_patch.rebuild_failed"
CARRY_PATCH_MERGED_DETECTED = "carry_patch.merged_detected"
```

Emit these events via the existing `emit_audit_event()` helper at the
appropriate points in fix pipeline and monitor cycle.

## Files Modified / Created

| File | Change |
|------|--------|
| `agentfox/nightshift/fix_pipeline.py` | Add patch registration + rebuild polling in carry-patch mode |
| `agentfox/nightshift/carry_patch_monitor.py` | New: CarryPatchMonitor, MonitorCycleResult |
| `agentfox/nightshift/streams.py` | Register carry-patch stream in build_streams() |
| `agentfox/nightshift/engine.py` | Add _run_carry_patch_monitor() method |
| `agentfox/nightshift/daemon.py` | Add carry-patch display name/label entries |
| `agentfox/archetypes.py` | Add carry-patch ModeConfig to coder archetype |
| `agentfox/_templates/profiles/coder_carry-patch.md` | New: conflict resolution profile |
| `afaudit/events.py` | Add 8 carry-patch audit event type constants |

## Test Files

| File | Package | Coverage |
|------|---------|----------|
| `packages/agentfox/tests/test_carry_patch_monitor.py` | agentfox | MonitorCycleResult, run_cycle happy path and failures |
| `packages/agentfox/tests/test_carry_patch_registration.py` | agentfox | Fix pipeline patch registration, concurrent rebuild handling |
| `packages/agentfox/tests/test_carry_patch_stream.py` | agentfox | Stream enablement, disabled when hub not configured |
| `packages/agentfox/tests/test_carry_patch_profile.py` | agentfox | Profile template loading, archetype mode presence |

