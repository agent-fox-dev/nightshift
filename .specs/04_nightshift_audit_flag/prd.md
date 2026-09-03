---
spec_id: '04'
spec_name: nightshift_audit_flag
title: "nightshift --audit flag: trace sink activation and spec audit events"
status: draft
created_at: '2026-09-03T15:29:37.590426+00:00'
updated_at: '2026-09-03T15:29:37.590426+00:00'
owner: nightshift team
source: interactive
schema_version: 1
---

# nightshift `--audit` Flag

## Summary

Add an `--audit` flag to the `nightshift` CLI that enables full audit logging
for daemon runs. When set, it activates the existing `AgentTraceSink` and
`AuditJsonlSink` infrastructure (writing model prompts, tool calls, and model
responses to JSONL files in `.agent-fox/audit/`). It also introduces two new
audit event types that serialize the in-memory specs built during the fix
pipeline so they can be inspected after a run.

## Problem

The `AgentTraceSink` and `AuditJsonlSink` classes are fully implemented in
`afaudit` but are never attached to the `SinkDispatcher` in the nightshift
startup. Operators have no way to inspect the full conversation trace or the
in-memory specs (both the lightweight `InMemorySpec` object and the richer
afspec `Spec` object built from triage output). Without this flag, debugging a
run requires reading DuckDB tables, which do not contain prompts or spec
content.

## Goals

- Write full model conversation traces (system prompt, task prompt, assistant
  messages, tool calls) to `.agent-fox/audit/agent_{run_id}.jsonl` when
  `--audit` is active.
- Write structured audit events to `.agent-fox/audit/audit_{run_id}.jsonl`
  when `--audit` is active.
- Serialize `InMemorySpec` and afspec `Spec` objects as audit events
  immediately after they are built so they appear in the JSONL log.
- Produce no change in behavior when `--audit` is not set (opt-in only,
  zero overhead when inactive).

## Non-Goals

- Tool result tracing (what tools return to the model); covered by a
  subsequent spec (`05_tool_result_tracing`).
- Real-time streaming of audit data to external systems.
- Changes to the DuckDB sink or its schema.
- Modification of the `purge_stale_audit_files` behavior.

## Technical Context

**Startup flow** (relevant to wiring):
1. `nightshift/app.py::_run_daemon()` calls
   `nightshift/_startup.py::init_knowledge()`, which returns
   `(kdb, sink, kprov)` where `sink` is a `SinkDispatcher([DuckDBSink(...)])`.
2. The sink is passed to `NightShiftEngine` and transitively to `FixPipeline`.
3. When `--audit` is set, `AgentTraceSink` and `AuditJsonlSink` must be
   added to the dispatcher before the engine starts. The `run_id` must be
   generated first (via `generate_run_id()`), stored in `ctx.obj`, and shared
   with the sinks and the engine.

**Spec building** (relevant to new audit events):
- `fix_pipeline.py::FixPipeline.process_issue()` calls
  `build_in_memory_spec(issue, issue_body)` → `InMemorySpec`.
- `fix_pipeline.py::FixPipeline._assemble_afspec_context()` (and
  `_build_coder_prompt()`, `_build_reviewer_prompt()`) call
  `build_afspec_from_triage(triage, issue_number)` → afspec `Spec`.

**New audit event types** (to be added to `AuditEventType` in `afaudit/events.py`):
- `SPEC_INMEMORY_CREATED = "spec.inmemory_created"` — payload:
  `issue_number` (int), `title` (str), `branch_name` (str),
  `task_prompt` (str), `system_context` (str, truncated to 10 000 chars).
- `SPEC_AFSPEC_CREATED = "spec.afspec_created"` — payload:
  `issue_number` (int), `spec_id` (str), `requirements_count` (int),
  `test_cases_count` (int), `subtasks_count` (int),
  `rendered` (str — output of `afspec.render.render_combined(spec)`,
  truncated to 50 000 chars).

**Emit sites**:
- `InMemorySpec` event: emit in `FixPipeline.process_issue()` after the
  `build_in_memory_spec()` call.
- `Spec` event: emit in `FixPipeline._assemble_afspec_context()` after the
  `build_afspec_from_triage()` call.

Both emits are best-effort (wrapped in try/except, failure logged at DEBUG).

**Hub API note**: The hub API (afhub) reads audit files produced by nightshift.
No structural changes to the JSONL schema are required; new event types are
additive. The hub API should be updated to recognize `spec.inmemory_created`
and `spec.afspec_created` event types, but this is a separate task outside
this spec's scope.

## Verified External API

### `afaudit` (v1.0.0, Python, `packages/afaudit/`)

| Symbol | Module | Signature | Notes |
|--------|--------|-----------|-------|
| `AgentTraceSink` | `afaudit.trace` | `(audit_dir: Path, run_id: str)` | Already exists |
| `AuditJsonlSink` | `afaudit.events` | `(audit_dir: Path, run_id: str)` | Already exists |
| `SinkDispatcher.add` | `afaudit.sink` | `(sink: SessionSink) -> None` | Already exists |
| `AuditEventType` | `afaudit.events` | `StrEnum` | Extend with two new values |
| `emit_audit_event` | `afaudit.emit` | `(sink, run_id, event_type, *, payload) -> None` | Already exists |
| `generate_run_id` | `afaudit.events` | `() -> str` | Already exists |
| `AUDIT_DIR` | `afaudit.constants` | `Path` | `.agent-fox/audit` |

### `afspec` (v4.1.3, Python, installed at `.venv/`)

| Symbol | Module | Signature | Notes |
|--------|--------|-----------|-------|
| `render_combined` | `afspec.render` | `(spec: Spec, *, max_tokens: int \| None = None) -> str` | Renders full spec as markdown |
| `Spec` | `afspec.models` | Pydantic model | `requirements`, `test_spec`, `tasks`, `prd` fields |

## Design Decisions

1. **`--audit` is opt-in**: Zero overhead when not set. This avoids performance
   impact on production runs and prevents large JSONL files from accumulating
   without explicit operator intent.

2. **`system_context` truncation to 10 000 chars** in `SPEC_INMEMORY_CREATED`:
   Matches the existing `truncate_tool_input` limit in `AgentTraceSink`. The
   full context is available in the `session.init` trace event (system prompt).

3. **`rendered` truncation to 50 000 chars** in `SPEC_AFSPEC_CREATED`: The
   rendered spec can be large for complex issues. This limit keeps individual
   audit events manageable while preserving the full content for small specs.

4. **`run_id` generated in `_run_daemon`**: When `--audit` is active, a
   `run_id` must be created before the engine starts so it can be shared with
   `AgentTraceSink`, `AuditJsonlSink`, and eventually `FixPipeline.process_issue()`.
   The `run_id` is stored in `ctx.obj["audit_run_id"]` and passed to `init_knowledge`
   or passed as a parameter to `_run_daemon`.

5. **Emit site for `SPEC_AFSPEC_CREATED`**: `_assemble_afspec_context()` is the
   canonical construction point — it calls `build_afspec_from_triage` on the
   happy path and falls back on failure. The emit happens only on success (inside
   the `try` block, before the fallback except).

6. **`FixPipeline` must receive the `run_id`**: Currently `process_issue()` accepts
   an optional `run_id` param. When `--audit` is active, the same `run_id` used
   for the sinks is passed to `process_issue()` so all events share the same
   identifier. This is already supported by the existing signature.

## Tech Stack

- **Language**: Python 3.12
- **Test framework**: pytest + pytest-asyncio
- **Linting/typing**: ruff, mypy
- **Test runner command**: `uv run pytest -q`

## Acceptance Criteria

1. When `--audit` is set, a single-issue run creates `.agent-fox/audit/agent_{run_id}.jsonl` containing at least one `session.init` event with non-empty `system_prompt` and `task_prompt` fields.
2. When `--audit` is set, a single-issue run creates `.agent-fox/audit/audit_{run_id}.jsonl` containing at least one `spec.inmemory_created` event where `payload.issue_number` matches the processed issue number.
3. When `--audit` is NOT set, neither `agent_*.jsonl` nor `audit_*.jsonl` files are created in `.agent-fox/audit/` and neither `AgentTraceSink` nor `AuditJsonlSink` appears in the dispatcher's sink list.
4. After `_assemble_afspec_context()` succeeds with non-empty criteria, `audit_{run_id}.jsonl` contains a `spec.afspec_created` event where `payload.requirements_count >= 1` and `payload.rendered` is a non-empty string.
5. If `.agent-fox/audit/` is not writable at daemon startup with `--audit` set, the daemon logs a WARNING-level message and continues running without audit sinks (no crash, no non-zero exit code).

## Click Flag Signature

```python
@click.option(
    "--audit",
    is_flag=True,
    default=False,
    help="Enable full audit logging to .agent-fox/audit/.",
)
```

## Clarifications

**Q: Who is the owner?** nightshift team.

**Q: Tech stack?** Python 3.12, pytest + pytest-asyncio, ruff + mypy.

**Q: Relationship with `carry_patch_pipeline_monitor` on `AuditEventType`?** Independent — this spec adds `SPEC_INMEMORY_CREATED` and `SPEC_AFSPEC_CREATED`; `carry_patch_pipeline_monitor` adds `CARRY_PATCH_*` values. Different enum values, no conflict.

**Q: Relationship with `carry_patch_bootstrap` on startup wiring?** Independent — the carry_patch startup wiring is already implemented in the codebase. This spec adds purely additive changes (sink attachment) to `_run_daemon` that don't conflict with existing code.

**Q: What is `ctx.obj`?** `ctx.obj` is a plain `dict`. `audit_run_id` is a new optional `str` key (empty string when `--audit` is not set).

**Q: Error handling for unwritable audit dir?** Log a WARNING and continue without audit sinks. `--audit` is opt-in diagnostics — the daemon must not fail to start.

## Dependencies

None — first spec in the audit flag group.

