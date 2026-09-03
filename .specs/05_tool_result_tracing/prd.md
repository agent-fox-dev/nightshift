---
spec_id: '05'
spec_name: tool_result_tracing
title: "Tool result tracing: capture tool outputs in AgentTraceSink"
status: draft
created_at: '2026-09-03T15:46:16.537469+00:00'
updated_at: '2026-09-03T15:46:16.537469+00:00'
owner: nightshift team
source: interactive
schema_version: 1
---

# Tool Result Tracing

## Summary

Extend the audit trace infrastructure to capture what tools *return* to the
model after execution. Currently `AgentTraceSink` records tool *calls* (tool
name + input) but not tool *results* (the output returned to the model). This
spec wires the existing `PostToolUse` SDK hook through a new callback interface
into a new `record_tool_result` method on `AgentTraceSink` and `SinkDispatcher`.
When `--audit` is active, the full tool result is written to
`.agent-fox/audit/agent_{run_id}.jsonl`, completing the model call audit trail.

## Problem

The audit trace written by `AgentTraceSink` records `session.init`, `tool.use`,
`assistant.message`, `tool.error`, and `session.result`. The `tool.use` event
captures the tool name and input. However, the tool's *output* (the
`tool_response` field from the `PostToolUseHookInput` SDK type) is never
recorded. An operator inspecting the trace file cannot see what the model
received back from each tool call, making it impossible to reconstruct the
full conversation including tool feedback.

The `claude_agent_sdk` already exposes a `PostToolUse` hook with a
`PostToolUseHookInput` dataclass that includes `tool_name`, `tool_input`,
`tool_response`, and `tool_use_id`. The `ClaudeBackend` already registers a
`PostToolUseFailure` hook (for tool errors) and a `PreToolUse` hook (for
permission enforcement). Adding a `PostToolUse` hook is a small, contained
change to the same wiring.

## Goals

- Add `record_tool_result(*, run_id, node_id, tool_name, tool_use_id, tool_response)` to `AgentTraceSink`, writing a `tool.result` trace event to `agent_{run_id}.jsonl`.
- Add `record_tool_result(**kwargs)` dispatch to `SinkDispatcher` via `_dispatch_optional`.
- Add a `tool_result_callback` parameter to `ClaudeBackend.execute()` (same pattern as `tool_error_callback`).
- Register a `PostToolUse` hook in `ClaudeBackend.execute()` that calls `tool_result_callback` when the callback is provided.
- Wire `session.py::_execute_query()` to pass a `tool_result_callback` to the backend that calls `sink_dispatcher.record_tool_result(...)`.
- The `tool_response` is serialized as a truncated string (max 50 000 chars) to prevent extremely large tool outputs from bloating the trace file.

## Non-Goals

- Changes to the DuckDB sink schema.
- Changes to the `AuditJsonlSink` or structured audit events.
- Filtering or redacting tool responses (captured verbatim, subject to truncation).
- Changes to other backends (deepagents, google_adk) — those are out of scope; only `ClaudeBackend` is extended.

## Technical Context

**SDK hook available** (verified in `claude_agent_sdk/types.py`):

```python
class PostToolUseHookInput(BaseHookInput, _SubagentContextMixin):
    hook_event_name: Literal["PostToolUse"]
    tool_name: str
    tool_input: dict[str, Any]
    tool_response: Any
    tool_use_id: str
```

The hook is registered identically to `PostToolUseFailure`:
```python
hooks["PostToolUse"] = [HookMatcher(hooks=[_post_tool_use_hook])]
```

**Existing pattern** (`ClaudeBackend.execute()`):
```python
if tool_error_callback is not None:
    hooks["PostToolUseFailure"] = [
        HookMatcher(hooks=[_build_tool_error_hook(tool_error_callback)])
    ]
```

The new `PostToolUse` hook follows the same pattern with a `tool_result_callback`.

**`tool_response` serialization**: The `tool_response` field is typed `Any` in the SDK — it can be a string, dict, or list. It must be JSON-serialized (via `json.dumps(tool_response, default=str)`) before being stored in the trace event. The serialized string is then truncated to 50 000 characters using the same truncation pattern as `truncate_tool_input`.

**Emit location in `session.py`**: The `_execute_query()` function already passes `tool_error_callback` to the backend. A parallel `tool_result_callback` parameter is added and wired the same way.

**`AgentTraceSink.record_tool_result` event schema**:
```json
{
  "event_type": "tool.result",
  "run_id": "<run_id>",
  "timestamp": "<iso8601>",
  "node_id": "<node_id>",
  "tool_name": "<tool_name>",
  "tool_use_id": "<tool_use_id>",
  "tool_response": "<json-serialized, truncated to 50000 chars>"
}
```

**Dependency on spec 04**: The `AgentTraceSink` and `SinkDispatcher` changes in this spec are additive to `afaudit`. The `--audit` flag (spec 04) controls whether `AgentTraceSink` is attached to the dispatcher. When `--audit` is not set, `record_tool_result` is never called (the callback is not wired). This spec does not require spec 04 to be implemented first — both specs touch different files and layers.

## Tech Stack

- **Language**: Python 3.12
- **Test framework**: pytest + pytest-asyncio
- **Linting/typing**: ruff, mypy
- **Test runner command**: `uv run pytest -q`

## Acceptance Criteria

1. When `--audit` is active and a tool is called during a session, `agent_{run_id}.jsonl` contains a `tool.result` event with matching `tool_name`, `tool_use_id`, and a non-empty `tool_response` string after the corresponding `tool.use` event.
2. When `--audit` is active and a tool returns a response longer than 50 000 characters, the `tool_response` field in the trace is exactly 50 000 chars followed by ` [truncated]`.
3. When `--audit` is NOT active (no `AgentTraceSink` in the dispatcher), no `tool.result` events are written.
4. A `PostToolUse` hook failure (e.g. serialization error) does not interrupt session execution — it logs a WARNING and the session continues.
5. Other backends (deepagents, google_adk) are not affected; they do not receive a `tool_result_callback`.

## Verified External API

### `claude_agent_sdk` (installed at `.venv/`)

| Symbol | Module | Signature | Notes |
|--------|--------|-----------|-------|
| `PostToolUseHookInput` | `claude_agent_sdk.types` | `hook_event_name, tool_name, tool_input, tool_response: Any, tool_use_id: str` | Confirmed in types.py:321 |
| `HookMatcher` | `claude_agent_sdk.types` | `(hooks: list[...])` | Already used in ClaudeBackend |
| Hook key `"PostToolUse"` | `claude_agent_sdk.types` | `Literal["PostToolUse"]` | In hook event name union at line 264 |

### `afaudit` (v1.0.0, Python, `packages/afaudit/`)

| Symbol | Module | Signature | Notes |
|--------|--------|-----------|-------|
| `AgentTraceSink._write_event` | `afaudit.trace` | `(event_type: str, run_id: str, data: dict) -> None` | Private helper, reuse for tool.result |
| `SinkDispatcher._dispatch_optional` | `afaudit.sink` | `(method: str, **kwargs) -> None` | Used for trace-specific dispatch |

## Design Decisions

1. **`tool_result_callback` pattern mirrors `tool_error_callback`**: Consistency with existing code reduces cognitive overhead. Both follow the same `if callback is not None: hooks[...] = [HookMatcher(...)]` pattern.

2. **JSON serialization with `default=str`**: `tool_response` is `Any`. Using `json.dumps(tool_response, default=str)` handles dicts, lists, and unexpected types gracefully without raising.

3. **50 000-char truncation limit**: Generous enough to capture most tool outputs (file reads, API responses) in full, while preventing single massive Bash outputs from bloating the trace file.

4. **Only `ClaudeBackend` is extended**: Other backends (deepagents, google_adk) do not have an equivalent post-tool hook. Extending them is a separate task once each backend's hook API is understood.

5. **`record_tool_result` is optional in `SinkDispatcher`**: Uses `_dispatch_optional` (already the pattern for trace methods) so non-trace sinks do not need to implement it.

## Dependencies

| Spec | From Group | To Group | Relationship |
|------|-----------|----------|--------------|
| 04_nightshift_audit_flag | 1 | 1 | AgentTraceSink is the target sink; spec 04 adds it to the dispatcher |

Note: The dependency is logical (spec 04 activates the sink), not a hard code dependency. This spec's changes compile and test independently.

