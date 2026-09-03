# AgentKit: A Dependency-Free Agent SDK for Python and Go

**Author:** [Platform Engineering]
**Date:** 2026-09-03
**Status:** Draft
**Version:** 0.1.0

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Problem Statement](#2-problem-statement)
3. [Goals and Non-Goals](#3-goals-and-non-goals)
4. [User Personas](#4-user-personas)
5. [Core Concepts and Architecture](#5-core-concepts-and-architecture)
6. [Feature Requirements](#6-feature-requirements)
7. [API Design Sketches](#7-api-design-sketches)
8. [Non-Functional Requirements](#8-non-functional-requirements)
9. [Open Questions](#9-open-questions)

---

## 1. Executive Summary

### Overview

AgentKit is a lightweight, dependency-free Agent SDK that gives developers direct, transparent control over the full agentic stack. It provides a production-grade agent loop, a unified tool system, a skills packaging format, a plugin extension model, and bidirectional MCP (Model Context Protocol) support — all without wrapping external frameworks such as LangChain, Google ADK, or the Claude Code CLI. First-class implementations are provided in both Python (asyncio-native) and Go (goroutine-native) with a comparable public API surface in each language.

AgentKit is not a platform, not a cloud service, and not a visual builder. It is a library — a set of composable interfaces that a developer imports, wires together, and controls directly. Every meaningful behavior in AgentKit is expressed in ordinary code that a developer can read, step through with a debugger, and override.

### Motivation

Existing agent frameworks each impose non-trivial abstraction taxes. The Claude Agent SDK shells out to a closed-source Bun-compiled binary rather than calling the Anthropic Messages API directly. Google ADK is a large, opinionated framework with deep coupling to Google Cloud services and no Go implementation. LangChain/LangGraph carries a layered package ecosystem and a state-machine DSL that must be learned before simple tool-calling works.

AgentKit eliminates that overhead by calling model provider APIs directly, owning the loop explicitly, and exposing every extension point through small, composable interfaces. The loop is six lines of logic. The tool system is a dataclass with a callable. The extension system is three orthogonal axes: middleware, turn hooks, and tool interceptors. Nothing is hidden inside a subprocess or a graph engine.

The core insight behind AgentKit is that the agentic loop is not complex — it is a while loop with a switch statement. What the existing frameworks actually provide is tooling around that loop: retries, compaction, streaming, observability, multi-agent coordination, and secure tool execution. AgentKit provides all of that tooling as transparent, composable components rather than as opaque framework internals.

---

## 2. Problem Statement

### Why Not the Claude Agent SDK?

The `claude-agent-sdk` Python package is a thin subprocess wrapper around the Claude Code CLI binary. It does not call the Anthropic Messages API directly. It resolves the `claude` binary using `shutil.which()` or a bundled copy at `package_root/_bundled/claude` (version 2.1.44 as of the current release), then spawns it using `anyio.open_process()`. The binary is a Mach-O arm64 executable compiled with Bun (Oven's TypeScript bundler), embedding a complete Node.js runtime.

The agent loop runs entirely inside that closed binary. Every turn is mediated through a bidirectional NDJSON control protocol over stdin/stdout: the SDK sends `control_request` messages (initialize, interrupt, set_model, set_permission_mode), the CLI sends `control_response` plus `can_use_tool` permission gates and `hook_callback` events. Extending the loop — custom retry logic, custom compaction, cost tracking at turn granularity — requires intercepting this control protocol rather than composing ordinary code.

For production systems that need auditability, multi-provider routing, a Go implementation, or the ability to step through the agent loop in a debugger, the Claude Agent SDK is not viable.

### Why Not Google ADK?

Google ADK is open-source (Apache 2.0) and genuinely model-agnostic, but it is a large, opinionated framework. ADK 2.0 (GA May 2026) introduced a graph-based execution engine where agents and tool nodes are graph nodes, edges are typed transitions, and the runner is a stateless graph traversal orchestrator. Understanding an ADK agent requires understanding `LlmAgent`, `Runner`, `Session`, `Event`, `NodeInfo`, `isolationScope`, `RetryConfig`, and the `NodeInterruptedError` control-flow exception used internally for human-in-the-loop pauses (catching `BaseException` in user code will silently break HITL pause/resume).

Non-Gemini providers are supported only via LiteLLM, adding another dependency layer. The Google Cloud toolsets (VertexAI, BigQuery, AlloyDB) are first-class citizens; Anthropic models are second-class. There is no production-ready Go implementation for ADK despite the `adk-go` repository existing. For teams building on Anthropic models with Go services, ADK is not suitable.

### Why Not LangChain / LangGraph?

LangChain is a mature ecosystem but carries significant complexity across its layered package tree: `langchain-core`, `langchain`, `langchain-community` (now officially sunset), and a growing set of provider packages. The LCEL pipe-operator abstraction (`chain = prompt | llm | parser`) is elegant for linear chains but becomes opaque when composed with `RunnableParallel`, `RunnableWithMessageHistory`, and `RunnableLambda`. The `AgentExecutor` is deprecated; the replacement is LangGraph's `StateGraph`, which requires learning a typed state-machine DSL.

Tool registration alone has multiple partially-overlapping patterns: the `@tool` decorator, `StructuredTool.from_function`, `BaseTool` subclass, `bind_tools()` on the model, and `ToolNode` in LangGraph. MCP is supported via a separate `langchain-mcp-adapters` package maintained by the LangChain team. There is no Go implementation. For teams that want a minimal, auditable agent loop without framework lock-in, LangChain is over-specified.

### The Core Gap

| Requirement | Claude Agent SDK | Google ADK | LangChain / LangGraph | AgentKit |
|---|---|---|---|---|
| Direct provider API calls, no subprocess | No (shells to CLI) | Yes | Yes | Yes |
| Explicit, readable agent loop | No (inside binary) | Partial (graph engine) | Partial (LangGraph) | Yes |
| Python first-class | Yes | Yes | Yes | Yes |
| Go first-class | No | No (alpha) | No | Yes |
| Built-in coding-agent tool library | Via CLI binary | Partial (code_execution) | Via community | Yes |
| Skills packaging system | Via CLI skills | No | No | Yes |
| Plugin extension model | No | Via Toolsets+Callbacks | Via Toolkits | Yes |
| MCP client (consume servers) | Via CLI | Via McpToolset | Via langchain-mcp-adapters | Yes |
| MCP server (expose capabilities) | No | Via FastMCP | No | Yes |
| Zero mandatory framework dependencies | N/A | No | No | Yes |

No existing SDK simultaneously satisfies all seven requirements. AgentKit is built to fill that gap.

---

## 3. Goals and Non-Goals

### Goals

**G1 — Complete agentic loop, both languages.** Implement the full loop (call model, dispatch tools, loop until stop) in Python and Go with no dependency on external agent frameworks.

**G2 — Provider abstraction.** Abstract Anthropic Messages API and OpenAI Chat Completions API behind a thin `ProviderClient` interface. Provider-specific types must not appear in agent logic.

**G3 — Built-in local tool library.** Provide file read/write/edit/delete/move/find/stat/append, grep (with ripgrep acceleration), shell command execution (with allowlist enforcement), and SSRF-protected HTTP fetch.

**G4 — Skills packaging format.** Define a TOML manifest + Markdown prompt file + optional Python tools module format, with a discovery/loading pipeline across SDK-built-in, user-global, and project-local directories.

**G5 — Plugin extension model.** Define four plugin categories (backend, tool provider, storage, event hook) via Protocol interfaces, discoverable via Python entry points and local plugin directories.

**G6 — MCP client support.** Consume MCP servers as tool providers using the official `mcp` Python SDK, exposing them through the same unified tool registry as native tools, with stdio and streamable-HTTP transports.

**G7 — MCP server support.** Optionally expose AgentKit capabilities as an MCP server consumable by Claude Desktop, Claude Code, and other MCP hosts.

**G8 — Two-level streaming.** Provide token-level text deltas and tool-call lifecycle events via `AsyncIterator` in Python and channel-based `EventStream` in Go.

**G9 — Three orthogonal extension axes.** Support middleware (wraps `complete()` calls), turn hooks (lifecycle callbacks), and tool interceptors (wraps individual tool executions).

**G10 — Security boundary enforcement.** Enforce path containment, output size limits, command allowlist, shell operator rejection, SSRF guard, skill import restrictions, and plugin private-API blocklist.

### Non-Goals

**NG1** — AgentKit is not a hosted platform or cloud service. It is a library.

**NG2** — AgentKit does not implement LLM inference. It calls external provider APIs.

**NG3** — AgentKit does not provide a web UI, dashboard, or REPL.

**NG4** — AgentKit does not wrap or depend on LangChain, LangGraph, Google ADK, Claude Code CLI, or the `claude-agent-sdk` package.

**NG5** — AgentKit does not implement a vector database or embedding store; it may define a protocol for a pluggable knowledge store but does not ship one.

**NG6** — The Go implementation does not need to be a mechanical translation of the Python code. Go-idiomatic patterns (goroutines, channels, explicit interfaces, manual JSON Schema literals) are preferred.

**NG7** — MCP Resources and MCP Prompts are not in scope for the initial client release. Only MCP Tools are consumed. Resources and Prompts may be added in a future release.

**NG8** — AgentKit does not provide a graphical agent builder or visual workflow editor.

---

## 4. User Personas

### Persona A: Platform Engineer (Python)

Builds internal automation tooling for an engineering organization. Writes Python asyncio services. Wants to add agentic capabilities to an existing service without adopting a heavyweight framework. Needs direct control over retry logic, cost tracking, and context compaction to stay within budget. Will register custom tools against internal APIs. Will write skills to encode company-specific coding standards. Evaluates the SDK by reading the agent loop source code directly — if the loop is opaque, the SDK is disqualified.

**Key needs:** Composable middleware for retries and budget gates; sync and async variants of the Agent class; clean separation between session configuration and system prompt; observable cost per turn.

### Persona B: Backend Engineer (Go)

Maintains a Go service that processes engineering issues at scale. Needs to embed agent capabilities in the same process as the Go service without introducing a Python runtime dependency or spawning subprocess agents. Requires a Go-native interface with goroutine-safe concurrent tool execution and `context.Context` cancellation propagation. Will use the Anthropic provider. Will not use the skills system initially — primarily needs the core loop and tool registration.

**Key needs:** Zero required external dependencies beyond stdlib and mcp-go; explicit JSON Schema literals for tool registration; typed sentinel errors for programmatic error handling; channel-based streaming.

### Persona C: AI Application Developer

Building a multi-agent product where one orchestrator delegates to multiple specialist agents. Needs subagent delegation via the tool-as-agent pattern, parallel execution of child agents, and budget propagation across delegation boundaries. Needs streaming events to drive a UI showing tool calls and partial text in real time. Will use both Python (for agent logic) and potentially Go (for an API server hosting the agent). Will leverage MCP to integrate third-party tools (GitHub, filesystem, database) without writing custom tool implementations.

**Key needs:** `SubagentTool` delegation wrapper; `asyncio.gather` parallel subagent execution; `BudgetTracker` propagation via `contextvars`; `AgentDoneEvent` / `ToolCallStartEvent` in the streaming taxonomy; MCP server pool with qualified tool names.

### Persona D: SDK Integrator

Maintains a plugin that adds a new capability domain (Kubernetes tooling, Slack integration) to AgentKit deployments. Publishes the plugin as a pip package with a Python entry point. The plugin registers a `ToolProviderPlugin` returning callables and an `EventHookPlugin` logging all tool calls to an external observability platform. Needs the plugin protocol to be stable and the private-API blocklist to be clearly documented so they do not accidentally depend on internal modules that may change.

**Key needs:** Stable `Protocol` interfaces for all four plugin categories; entry-point discovery via `afcore.plugins` group; `PLUGIN_DEPS` declaration for local plugins; `nightshift --validate-plugins` command to check conformance without starting the daemon.

---

## 5. Core Concepts and Architecture

### The Agentic Loop

The loop has six phases per turn, repeated until a stop condition is met:

```
PRE:  Append user message to history.
LOOP:
  1. Call provider.complete(history, tools, config) → response
  2. Append assistant response to history
  3. If response.stop_reason != "tool_use": break
  4. Extract all tool_use blocks from response.content
  5. Execute each tool (parallel if configured)
  6. Append ONE user message containing ALL tool_result blocks → goto LOOP
```

Stop conditions are checked in priority order:

1. `stop_reason == "end_turn"` or `"stop_sequence"` — normal completion
2. `stop_reason == "max_tokens"` — surfaced as error or passed through per config
3. Refusal detected — raises `AgentRefusalError`
4. `turn_count >= max_turns` — raises `MaxTurnsError`
5. `budget_usd_used >= max_budget_usd` — raises `BudgetExceededError`

**Critical correctness invariant:** All `tool_result` blocks from a single model turn must be collected into a single `user` message. Splitting them across multiple messages silently degrades parallel tool call quality. This is the most common implementation mistake in hand-rolled agent loops. When a tool handler raises an exception, the loop returns a `tool_result` with `is_error=true` rather than aborting — the model can observe the error and self-correct.

### Type System

AgentKit defines a canonical content block type system that is provider-independent. No provider-specific type leaks into agent logic.

| Type | Fields |
|---|---|
| `AgentConfig` | `model`, `provider`, `max_tokens`, `max_turns`, `temperature`, `system_prompt`, `max_budget_usd` |
| `Tool` | `name`, `description`, `input_schema` (JSON Schema), `handler`, `is_async`, `strict` |
| `Message` | `role` (`"user"` or `"assistant"`), `content: list[ContentBlock]` |
| `ContentBlock` | Union of `TextBlock`, `ToolUseBlock`, `ToolResultBlock`, `ThinkingBlock` |
| `RunResult` | `messages`, `stop_reason`, `usage`, `turn_count` |
| `Usage` | `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_tokens`, `cost_usd` |

Providers translate between canonical types and provider wire formats on every call. The Anthropic provider maps `ToolUseBlock` to `{"type": "tool_use", "id": ..., "name": ..., "input": {...}}` and maps inbound `{"type": "tool_use"}` content blocks back to `ToolUseBlock`. The OpenAI provider maps to and from `tool_calls` in Chat Completions format.

### Agent as Value Object

An Agent holds its own config, tool registry, history, and middleware chain. It has no global state. Creating a child agent for delegation is constructing a new `Agent` value — not registering with a global executor or inheriting a global session. History is owned by the agent but injectable from outside at construction time to enable session resumability and testing. The system prompt is stored on `AgentConfig`, not in history, so it is excluded from token count estimates and can be updated without mutating conversation state.

### Extension Axes

Three orthogonal extension axes are defined, each with a distinct scope:

**Axis 1 — Middleware** wraps the entire `complete()` call. Last registered is outermost, first to execute. Built-in middlewares include: `RetryMiddleware` (exponential backoff on 429/5xx), `RateLimitMiddleware` (token bucket), `BudgetMiddleware` (cost gate before each turn), `TracingMiddleware` (OpenTelemetry spans), `CachingMiddleware` (identical request deduplication), `CompactionMiddleware` (history compaction before each turn).

**Axis 2 — Turn hooks** fire at lifecycle points (`OnTurnStart`, `OnTurnEnd`, `OnAgentDone`, `OnError`) without modifying request/response. Hooks are for observation, not for interception.

**Axis 3 — Tool interceptors** wrap individual tool executions. Approval gate is outermost (reject before executing anything); caching interceptor is innermost (skip the network call on a cache hit).

### Skills and Plugins Relationship

| Concept | What it extends | Scope | Who writes it |
|---|---|---|---|
| Archetype | Session execution parameters | Agent runtime | SDK authors / operators |
| Skill | Agent behavior — prompt additions and tools | Session | Domain experts |
| Plugin | SDK runtime infrastructure | Framework-wide | Third-party integrators |

The `steering.md` convention used in the existing nightshift codebase is a degenerate always-on skill with no manifest. Skills target specific archetypes; plugins are framework-wide. Skill Python code (`tools.py`) is sandboxed: it cannot import `afcore.session`, any LLM client library, or model API packages.

---

## 6. Feature Requirements

### 6.1 Core Agent Loop

- **REQ-LOOP-01:** The loop must implement all six phases in the exact order specified: append user message, call provider, append assistant response, check stop_reason, extract tool_use blocks, execute tools, append tool_results.
- **REQ-LOOP-02:** All tool_result blocks from a single model turn must be collected into a single user message. The SDK must never split them across turns.
- **REQ-LOOP-03:** Tool handler exceptions must be caught and converted to `ToolResultBlock(is_error=True)`. The loop continues rather than aborting.
- **REQ-LOOP-04:** Stop conditions must be checked in the priority order: `end_turn/stop_sequence` > `max_tokens` > `refusal` > `max_turns` > `budget_exceeded`.
- **REQ-LOOP-05:** Parallel tool execution is supported and opt-in via `AgentConfig.parallel_tools=True`. In Python, `asyncio.gather` is used. In Go, `errgroup` with per-tool goroutines is used.
- **REQ-LOOP-06:** Both `AsyncAgent` and `Agent` (sync wrapper) are provided in Python. In Go, a single `Agent` type suffices.
- **REQ-LOOP-07:** The loop accepts a `max_turns` limit (default configurable) and raises `MaxTurnsError` when exceeded.
- **REQ-LOOP-08:** The loop accepts a `max_budget_usd` limit and raises `BudgetExceededError` when cumulative cost exceeds it.
- **REQ-LOOP-09:** The loop supports context cancellation (`asyncio.CancelledError` in Python, `context.Context` in Go) that cleanly terminates mid-turn without corrupting history state.

### 6.2 Model Provider Abstraction

- **REQ-PROV-01:** A `ProviderClient` Protocol/interface with a single required method: `complete(messages, tools, config) -> CompleteResponse`.
- **REQ-PROV-02:** Implementations for Anthropic Messages API (claude-3-5-sonnet-latest, claude-opus-4, claude-haiku-4 family) and OpenAI Chat Completions API (gpt-4o, gpt-4o-mini, o1, o3 family).
- **REQ-PROV-03:** Each provider translates canonical `Message`/`ContentBlock` types to and from the provider wire format. No provider-specific types in agent logic.
- **REQ-PROV-04:** Each provider implements a streaming variant: `stream_complete()` returning an async generator of canonical `StreamEvent` objects.
- **REQ-PROV-05:** Each provider populates `Usage` on every response, including `cache_read_tokens` and `cache_write_tokens` where the provider exposes them.
- **REQ-PROV-06:** Built-in `RetryMiddleware` handles transient errors (HTTP 429, 500, 502, 503) with exponential backoff.
- **REQ-PROV-07:** The Anthropic provider supports the server-side compaction mechanism (`compact-2026-01-12` beta): compaction blocks from the response are passed back unchanged in subsequent turns.
- **REQ-PROV-08:** Per-agent model selection is supported. Different agents in the same delegation tree can use different models or providers.

### 6.3 Tool System — Built-in and Custom

- **REQ-TOOL-01:** A `Tool` dataclass/struct with: `name`, `description`, `input_schema` (JSON Schema), `handler`, `is_async` (inferred in Python), `strict`.
- **REQ-TOOL-02:** Python schema inference from PEP 484 annotations and docstrings for `@tool` decorator. Go callers provide JSON Schema literals.
- **REQ-TOOL-03:** All tool handlers invoked via a unified dispatch path that catches panics/exceptions and produces `tool_result(is_error=True)`.

**REQ-TOOL-04 — File system tools:**

| Tool | Inputs | Output |
|---|---|---|
| `read_file` | `path: str, offset: int = 0, limit: int \| None = None` | `{content: str, encoding: str}` |
| `write_file` | `path: str, content: str` | `{written: true, bytes: int}` |
| `edit_file` | `path: str, old_string: str, new_string: str` | `{replaced: int}` |
| `delete_file` | `path: str` | `{deleted: true}` |
| `move_file` | `src: str, dst: str` | `{moved: true}` |
| `find_files` | `pattern: str, path: str = ".", file_type: str = "file", max_results: int = 200` | `{files: list[str], truncated: bool}` |
| `stat_file` | `path: str` | `{size_bytes: int, mtime_iso: str, is_dir: bool, is_symlink: bool}` |
| `append_file` | `path: str, content: str` | `{appended: true, bytes: int}` |
| `list_files` | `path: str, max_entries: int = 1000` | `{entries: list[str], truncated: bool}` |

**REQ-TOOL-05 — Search tool:**

`search_files(pattern, path, *, context_lines=0, file_glob=None, case_sensitive=True, max_matches=500)` returns:
```json
{
  "matches": [
    {"file": "src/foo.py", "line": 42, "text": "    def foo(self):", "before": [], "after": []}
  ],
  "truncated": false,
  "files_searched": 87
}
```
Implemented as `grep_files`, accelerated by shelling out to `rg --json` if available, falling back to Python `re`. The `rg` call is internal to the tool implementation — it does not pass through the bash allowlist.

**REQ-TOOL-06 — Shell tools:** `execute(command: str)` with mandatory `AllowlistPolicy`, shell operator rejection, mandatory timeout (default 120s), `stdin=DEVNULL`, `cwd` fixed to workspace root. `run_command(argv: list[str])` variant with `shell=False` for structured invocation.

**REQ-TOOL-07 — HTTP tool:** `fetch_url(url, *, method="GET", headers=None, body=None, timeout_s=30.0)` with `SSRFGuardTransport` (blocks private/loopback/link-local IPs at DNS and TCP time), HTTPS-only by default, 512 KB response cap, 5-hop redirect limit with per-hop SSRF re-validation, optional HTML-to-text extraction.

**REQ-TOOL-08 — Output envelope:** All built-in tools return a `ToolResult` dataclass:
```python
@dataclass(frozen=True)
class ToolResult:
    ok: bool
    data: dict[str, Any]
    error: str | None = None
    detail: str | None = None
    metadata: ToolMetadata | None = None
```
`to_llm_dict()` strips metadata. Providers receive only the payload.

**REQ-TOOL-09 — Size limits:** `read_file` defaults to 200 KB. `search_files` caps at 500 matches. `list_files` caps at 1000 entries. `execute` captures up to 64 KB stdout and 64 KB stderr. Truncated output sets `metadata.truncated=True` with a marker line.

### 6.4 Multi-Agent and Subagent Support

- **REQ-MULTI-01:** Delegation is implemented as tool use. `SubagentTool` wraps an `Agent` instance; its handler calls `child_agent.run(prompt)` and returns the text result as a `tool_result` string.
- **REQ-MULTI-02:** Child agents always start with fresh, empty history. Sharing parent history with a child is prohibited — it creates prompt injection risk and inflates context.
- **REQ-MULTI-03:** Budget propagation: before delegating, the parent passes a budget slice to the child (`child.config.max_budget_usd = parent.budget.remaining() * fraction`). The child enforces its own budget independently.
- **REQ-MULTI-04:** In Python, budget context propagation uses `contextvars.ContextVar[BudgetTracker]` so child agents inherit the tracker without explicit passing.
- **REQ-MULTI-05:** Parallel delegation is safe by construction (each child is an independent value). `asyncio.gather` in Python, `errgroup` in Go.
- **REQ-MULTI-06:** Agent definitions (name, description, system prompt, tool allowlist, model) are registerable by name so the parent model can invoke a named specialist by name as a tool call.

### 6.5 Skills System

- **REQ-SKILL-01:** A skill is a packaged, reusable agent behavior unit combining system prompt additions, tool set overrides, trigger conditions, and metadata.
- **REQ-SKILL-02:** Skill directory layout: `skill.toml` (manifest), `prompt.md` (system prompt additions), `tools.py` (optional), `README.md` (optional).
- **REQ-SKILL-03:** Manifest fields: `name`, `version`, `description`, `author`, `sdk_min_version`, `archetypes`, `injection` (`'always'|'on_demand'|'keyword'`), `keywords`, `prompt_file`, `prompt_position` (`'pre'|'post'|'replace_section'`), `[skill.tools]` module+factory, `[skill.security]` allowlist_extend, `[skill.session]` max_turns_add, `[skill.subagent]` archetype+mode+prompt_template+result_key.
- **REQ-SKILL-04:** Discovery searches three locations in priority order: SDK built-in (`afcore/_skills/`), user global (`~/.nightshift/skills/`), project (``.nightshift/skills/``). Project-level takes precedence.
- **REQ-SKILL-05:** `SkillRegistry.load_for_session(archetype, task_prompt, config)` returns the ordered list of skills to inject, applying archetype filter and injection mode filter.
- **REQ-SKILL-06:** Prompt injection applied inside `session.py` before `backend.execute()`. Pre-position injections are prepended; post-position injections are appended; separated by `---` dividers.
- **REQ-SKILL-07:** Tool registrations from skills are merged with the session's base tool list. Name collisions raise `SkillConflictError` unless one skill declares `overrides`.
- **REQ-SKILL-08:** Skill subagent spawning is declarative only. The session runner spawns the subagent session, waits for the result, and injects the result text into the main session's system prompt. Skill Python code may not directly call `session.py` or any backend.
- **REQ-SKILL-09:** Skill Python code (`tools.py`) is sandboxed: no imported module names may contain `'claude'`, `'anthropic'`, `'openai'`, `'google.adk'`, or `'afcore.session'`. Violations reject the skill at load time.
- **REQ-SKILL-10:** Skill manifests are parsed and validated with Pydantic (strict unknown-key rejection).
- **REQ-SKILL-11:** Every loaded skill name is recorded in the session's audit event.

### 6.6 Plugin System

- **REQ-PLUGIN-01:** Four plugin categories: `BackendPlugin`, `ToolProviderPlugin`, `StoragePlugin`, `EventHookPlugin`.
- **REQ-PLUGIN-02:** Each category defines a `Protocol` with `runtime_checkable`. `EventHookPlugin.on_tool_use(tool_name, tool_input, ctx) -> 'allow' | 'block' | None`.
- **REQ-PLUGIN-03:** All `EventHookPlugin` methods have default no-op implementations.
- **REQ-PLUGIN-04:** Event hooks execute in registration order. The first hook returning `'block'` from `on_tool_use` wins. Security allowlist check runs before hooks — hooks cannot forge allow decisions for allowlist-blocked commands.
- **REQ-PLUGIN-05:** Discovery via Python entry points (`afcore.plugins` group) and local plugin directories (`[plugins] paths` in config.toml).
- **REQ-PLUGIN-06:** Loading order: built-in SDK components, entry-point plugins (alphabetical by package name), local plugins last. Name collision: later registration wins with a warning.
- **REQ-PLUGIN-07:** `[plugins] disabled` list allows opt-out by name.
- **REQ-PLUGIN-08:** Local plugins may declare `PLUGIN_DEPS = [...]` as PEP 508 requirement strings. Missing or version-mismatched dependencies skip the plugin with a warning.
- **REQ-PLUGIN-09:** Plugin code may not import private `afcore` module paths (`afcore.session.backends`, `afcore.engine.*`). Violations reject the plugin at load time.
- **REQ-PLUGIN-10:** `nightshift --validate-plugins` loads all configured plugins, runs protocol conformance checks, and reports violations without starting the daemon.
- **REQ-PLUGIN-11:** `PluginRegistry` is held on `AgentFoxConfig`, not a module-level global, so tests can inject mock plugins without patching global state.

### 6.7 MCP Client Support

- **REQ-MCP-CLIENT-01:** Consume MCP servers as tool providers using `mcp>=1.0`.
- **REQ-MCP-CLIENT-02:** Three transports: stdio (subprocess + NDJSON), HTTP/SSE (2024-11-05 spec), streamable HTTP (2025-03-26 spec).
- **REQ-MCP-CLIENT-03:** `MCPServerConnection` wraps `mcp.ClientSession` and provides: initialization (protocol handshake + capability negotiation), tool list caching with refresh on `notifications/tools/list_changed`, async `call(tool_name, arguments) -> dict`, and audit logging of every call via `afaudit`.
- **REQ-MCP-CLIENT-04:** `MCPServerPool` holds `server_name -> MCPServerConnection`, instantiated during session initialization, torn down after the session ends.
- **REQ-MCP-CLIENT-05:** MCP tools exposed through the unified tool registry with qualified names using `server_name__tool_name` convention (e.g., `github__create_issue`). Configurable per-server prefix.
- **REQ-MCP-CLIENT-06:** MCP tool names may not shadow native tool names. Collision raises `MCPNameCollisionError` at connection time.
- **REQ-MCP-CLIENT-07:** Servers configured in `config.toml` as `[[mcp.servers]]` with fields: `name`, `command`, `url`, `env` (with `${VAR}` interpolation), `tool_prefix`, `allow_sampling`, `per_session_call_limit` (default 1000), `timeout_s` (default 30.0).
- **REQ-MCP-CLIENT-08:** Sampling requests require explicit `allow_sampling = true` per server. All sampling requests are logged in the audit trail.
- **REQ-MCP-CLIENT-09:** MCP tool result content is capped at 50K characters per result with truncation and a note to the LLM.
- **REQ-MCP-CLIENT-10:** Stdio server processes spawned with a reduced environment. Credentials resolved from the secrets store at spawn time.
- **REQ-MCP-CLIENT-11:** `AllowlistPolicy` and `PermissionCallback` include MCP qualified tool names. `on_tool_use` event hooks fire for MCP tool calls with the qualified name.

### 6.8 MCP Server Support

- **REQ-MCP-SERVER-01:** Optional, off by default. Enabled via `[mcp_server] enabled = true` in `config.toml`.
- **REQ-MCP-SERVER-02:** Two serving modes: stdio (`nightshift --mcp-server`) and HTTP (`mcp_server.transport = 'http'`, `mcp_server.port`).
- **REQ-MCP-SERVER-03:** Server implementation uses `fastmcp` (decorator-based registration).
- **REQ-MCP-SERVER-04:** Exposed tools: `process_issue(issue_number, mode)`, `get_session_status(session_id)`, `list_active_sessions()`, `cancel_session(session_id)`.
- **REQ-MCP-SERVER-05:** Exposed resources: `nightshift://issues/{number}/triage-report`, `nightshift://sessions/{id}/audit-log`, `nightshift://config`.
- **REQ-MCP-SERVER-06:** Implements MCP 2025-03-26 protocol version.
- **REQ-MCP-SERVER-07:** HTTP mode requires API key authentication on every request. Unauthenticated requests return HTTP 401. Stdio mode relies on OS-level process isolation.

### 6.9 Python Implementation

- **REQ-PY-01:** Python 3.10+ required. Asyncio-native throughout.
- **REQ-PY-02:** Both `AsyncAgent` and `Agent` (sync wrapper) provided. Sync wrapper runs the asyncio event loop internally.
- **REQ-PY-03:** `ProviderClient` Protocol uses `@runtime_checkable`. Structural subtyping — no explicit registration required.
- **REQ-PY-04:** Tool handlers may be sync or async. SDK detects via `inspect.iscoroutinefunction` and dispatches sync handlers via `asyncio.to_thread`.
- **REQ-PY-05:** Schema inference covers: `str`, `int`, `bool`, `float`, `list[T]`, `Optional[T]`/`T|None`, `Literal[...]`, `TypedDict`, `dataclass`.
- **REQ-PY-06:** Streaming exposed as `AsyncIterator[Event]` via async generator. `agent.stream(prompt)` is an async context manager handling cleanup.
- **REQ-PY-07:** Budget propagation in delegation uses `contextvars.ContextVar[BudgetTracker]`.
- **REQ-PY-08:** `ConversationHistory.snapshot()` (defensive copy) and `restore(messages)` for session resumability.
- **REQ-PY-09:** Four compaction strategies: `NoCompaction`, `TurnWindowCompaction(max_turns)`, `TokenWindowCompaction(max_tokens)`, `SummarizationCompaction(model, threshold_tokens)`. Compaction checked before each model call.
- **REQ-PY-10:** `mcp>=1.0` used for MCP client. `fastmcp` used for MCP server. Both are optional extras.
- **REQ-PY-11:** The package is importable with no optional dependencies installed. Provider packages, `mcp`, and `fastmcp` are optional extras.

### 6.10 Go Implementation

- **REQ-GO-01:** Go 1.21+ target.
- **REQ-GO-02:** Single `Agent` type. No sync/async distinction.
- **REQ-GO-03:** Tool handler signature: `func(ctx context.Context, input json.RawMessage) (json.RawMessage, error)`.
- **REQ-GO-04:** Parallel tool execution via `errgroup`. Handler errors converted to `ToolResultBlock{IsError: true}`.
- **REQ-GO-05:** `context.Context` cancellation propagates through all levels.
- **REQ-GO-06:** JSON-RPC id generation and response correlation for MCP use a goroutine-safe map protected by `sync.Mutex` or `sync.Map`.
- **REQ-GO-07:** Tool JSON Schema provided as `json.RawMessage` literals. Optional `agentkit-schemagen` code-gen tool available.
- **REQ-GO-08:** Streaming via channel-based `EventStream` with `Next() (Event, bool)` and `Close() error`.
- **REQ-GO-09:** Typed sentinel errors: `ErrMaxTurns`, `ErrBudgetExceeded`, `ErrToolRejected`, `ErrRefusal`.
- **REQ-GO-10:** Go MCP client uses `github.com/mark3labs/mcp-go` (MIT licensed).
- **REQ-GO-11:** Zero required external dependencies beyond stdlib and `mcp-go`. Provider dependencies loaded via build tags or separate sub-packages.

### 6.11 Observability and Hooks

- **REQ-OBS-01:** Every model call wrapped in an OpenTelemetry span (when `TracingMiddleware` is active) with attributes: `model`, `provider`, `turn_count`, `input_tokens`, `output_tokens`, `cost_usd`, `stop_reason`.
- **REQ-OBS-02:** Every tool call emits `on_tool_start` and `on_tool_end` spans with: `tool_name`, `tool_use_id`, `is_error`, `elapsed_ms`.
- **REQ-OBS-03:** Session start and end fire `EventHookPlugin.on_session_start` and `on_session_end` for all registered hooks.
- **REQ-OBS-04:** Every loaded skill name recorded in the session audit event.
- **REQ-OBS-05:** Every MCP tool call logged in the `afaudit` trail with `server_name`, `tool_name`, arguments hash, and `is_error` status.
- **REQ-OBS-06:** Streaming event taxonomy: `TextDeltaEvent`, `ThinkingDeltaEvent`, `ToolCallStartEvent`, `ToolInputDeltaEvent`, `ToolCallEndEvent`, `ToolExecutionEvent`, `ToolResultEvent`, `TurnEndEvent`, `AgentDoneEvent`.
- **REQ-OBS-07:** Turn hooks (`OnTurnStart`, `OnTurnEnd`, `OnAgentDone`, `OnError`) provided as callback registration points separate from middleware.

### 6.12 Security and Sandboxing

- **REQ-SEC-01 (Path containment):** All file system tools call `_check_path()` before any I/O. Paths resolving outside workspace root (via `..`, symlinks) are rejected with `error='path_not_allowed'`.
- **REQ-SEC-02 (Output size limits):** `read_file`: 200 KB default. `execute` stdout/stderr: 64 KB each. MCP tool results: 50 KB per result. `search_files`: 500 matches. `list_files`: 1000 entries.
- **REQ-SEC-03 (Command allowlist):** `execute` tool accepts `AllowlistPolicy` at construction time. Blocked commands return `error='command_not_allowed'`.
- **REQ-SEC-04 (Shell operator rejection):** `check_shell_operators` regex applied inside `execute` before subprocess invocation. Blocked: `|`, `;`, `&`, `$()`, backtick, variable expansion patterns.
- **REQ-SEC-05 (SSRF guard):** `fetch_url` uses `SSRFGuardTransport` validating against private/loopback/link-local/reserved IP ranges at DNS resolution time and TCP connection time.
- **REQ-SEC-06 (Skill sandbox):** `tools.py` validated at load time for prohibited imports. Symlink directories and symlink prompt files rejected. Skill allowlist extensions are additive only.
- **REQ-SEC-07 (Plugin sandbox):** Plugin code validated at load time for imports of private `afcore` module paths. Missing dependencies cause graceful skip. Event hook veto is additive only.
- **REQ-SEC-08 (MCP threat mitigations):** Tool name prefix prevents shadowing. Sampling requires explicit opt-in. Resource URI auto-fetch disabled. Stdio server credentials resolved from secrets store at spawn time. Per-server call count limits enforced per session.
- **REQ-SEC-09 (HTTPS enforcement):** `fetch_url` allows only `https://` by default. HTTP is opt-in via `tools.allow_http = true`.

---

## 7. API Design Sketches

### Python: Core Agent API

```python
import os
from pathlib import Path
from agentkit import AsyncAgent, AgentConfig
from agentkit.providers import AnthropicProvider
from agentkit.tools import make_local_tools
from agentkit.security import AllowlistPolicy
from agentkit.events import TextDeltaEvent, ToolCallStartEvent, AgentDoneEvent

# Build configuration
config = AgentConfig(
    model='claude-opus-4-5',
    provider='anthropic',
    max_tokens=8192,
    max_turns=50,
    max_budget_usd=2.00,
    system_prompt='You are a coding assistant.',
    parallel_tools=True,
)

# Build provider
provider = AnthropicProvider(api_key=os.environ['ANTHROPIC_API_KEY'])

# Build local tool set with a command allowlist
policy = AllowlistPolicy(['ls', 'cat', 'grep', 'git', 'pytest', 'rg'])
tools = make_local_tools(cwd=Path('.'), allowlist_policy=policy)

# Create the agent
agent = AsyncAgent(config=config, provider=provider, tools=tools)

# Non-streaming run
result = await agent.run('Fix the bug in src/foo.py')
print(result.final_text())
print(f'Cost: ${result.usage.cost_usd:.4f}')

# Streaming run
async for event in agent.stream('Explain the architecture'):
    match event:
        case TextDeltaEvent(text=t):
            print(t, end='', flush=True)
        case ToolCallStartEvent(name=n):
            print(f'\n[calling {n}]')
        case AgentDoneEvent(result=r):
            print(f'\nDone: {r.turn_count} turns, ${r.usage.cost_usd:.4f}')
```

### Python: Tool Registration

```python
from agentkit import Tool
from agentkit.delegation import SubagentTool

# Decorator pattern — schema inferred from type hints and docstring
@agent.tool(description='Search the codebase for a pattern')
async def grep(pattern: str, path: str = '.', context_lines: int = 0) -> dict:
    """Search files for a regex pattern, returning matching lines with context."""
    return await grep_files(pattern, path, context_lines=context_lines)

# Explicit registration — full control over schema
agent.register_tool(Tool(
    name='create_issue',
    description='Open a GitHub issue',
    input_schema={
        'type': 'object',
        'properties': {
            'title': {'type': 'string'},
            'body': {'type': 'string'},
        },
        'required': ['title'],
    },
    handler=create_github_issue,
))

# Subagent as tool
reviewer_config = AgentConfig(
    model='claude-opus-4-5',
    provider='anthropic',
    max_tokens=4096,
    max_turns=20,
    system_prompt='You are a strict code reviewer.',
)
reviewer = AsyncAgent(config=reviewer_config, provider=provider, tools=review_tools)
agent.register_tool(SubagentTool(
    name='review',
    description='Run a code review on changed files',
    agent=reviewer,
))
```

### Python: Middleware and Hooks

```python
from agentkit.middleware import RetryMiddleware, BudgetMiddleware, TracingMiddleware
from agentkit.errors import ToolRejectedError
from opentelemetry import trace

tracer = trace.get_tracer('my-service')

# Middleware stack — retry is outermost, tracing is innermost
agent.use(RetryMiddleware(max_retries=3, base_delay=1.0, jitter=True))
agent.use(BudgetMiddleware(max_usd=2.00))
agent.use(TracingMiddleware(tracer=tracer))

# Turn hooks for observation without interception
agent.on_turn_end(
    lambda turn, resp: logger.info(
        'turn %d: %d tokens, $%.4f', turn, resp.usage.output_tokens, resp.usage.cost_usd
    )
)
agent.on_error(lambda err: alerting.send(str(err)))

# Tool interceptor for human-in-the-loop approval
async def approval_interceptor(call, next_fn):
    if call.name in {'delete_file', 'write_file', 'execute'}:
        if not await ask_approval(call.name, call.input):
            raise ToolRejectedError(call.name)
    return await next_fn(call)

agent.intercept_tools(approval_interceptor)
```

### Python: MCP Client Configuration

```toml
# .nightshift/config.toml
[[mcp.servers]]
name = "github"
command = ["npx", "-y", "@modelcontextprotocol/server-github@1.5.0"]
env = {GITHUB_TOKEN = "${GITHUB_PAT}"}
tool_prefix = "gh"
per_session_call_limit = 200
timeout_s = 30.0
allow_sampling = false

[[mcp.servers]]
name = "filesystem"
command = ["npx", "-y", "@modelcontextprotocol/server-filesystem@1.0.0", "/workspace"]
tool_prefix = "fs"
```

```python
from agentkit.mcp import MCPServerPool

# Automatic via config
pool = MCPServerPool.from_config(config.mcp.servers)

async with pool:
    mcp_tools = await pool.all_tools()  # list[MCPToolDescriptor]
    # Each tool has a qualified name: gh__create_issue, fs__read_file, etc.
    agent = AsyncAgent(
        config=config,
        provider=provider,
        tools=local_tools + mcp_tools,
    )
    result = await agent.run('Create a GitHub issue for the auth bug in src/auth.py')
    # The model calls gh__create_issue, which routes through pool to the GitHub MCP server
```

The protocol flow for each MCP tool call:

```
1. Session init:
   pool.connect("github") ->
     spawn npx subprocess ->
     send: {"jsonrpc":"2.0","id":1,"method":"initialize","params":{...}}
     recv: {"jsonrpc":"2.0","id":1,"result":{"capabilities":{"tools":{}},...}}
     send: {"jsonrpc":"2.0","method":"notifications/initialized"}
     send: {"jsonrpc":"2.0","id":2,"method":"tools/list"}
     recv: {"jsonrpc":"2.0","id":2,"result":{"tools":[{"name":"create_issue",...}]}}
     cache tool list as MCPToolDescriptors with prefix "gh__"

2. Agent loop — model emits tool_use for gh__create_issue:
   pool.call("github", "create_issue", {"title":"Bug","body":"..."}) ->
     send: {"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"create_issue","arguments":{...}}}
     recv: {"jsonrpc":"2.0","id":3,"result":{"content":[{"type":"text","text":"Issue #42 created"}],"isError":false}}
     return {"content": [...], "is_error": false}
   inject as tool_result in next user message

3. Session end:
   pool.close("github") -> terminate subprocess
```

### Python: Skill Loading

```toml
# .nightshift/skills/python-testing/skill.toml
[skill]
name = "python-testing"
version = "1.0.0"
description = "Enforces pytest conventions and coverage thresholds"
archetypes = ["coder"]
injection = "keyword"
keywords = ["test", "pytest", "coverage", "spec"]
prompt_file = "prompt.md"
prompt_position = "post"

[skill.tools]
module = "tools"
factory = "make_tools"

[skill.security]
allowlist_extend = ["pytest", "coverage", "python"]

[skill.session]
max_turns_add = 30
```

```python
from agentkit.skills import SkillRegistry

registry = SkillRegistry()
registry.discover(project_root=Path('.'))

skills = registry.load_for_session(
    archetype='coder',
    task_prompt='Add tests for the auth module',
    config=config.skills,
)
# Returns [Skill(name='python-testing', ...)] because 'tests' matches keyword

# Skills are applied automatically in session.py before backend.execute()
# The skill's prompt.md is appended to the system prompt
# The skill's tools.py.make_tools() results are merged into the tool registry
# The skill's allowlist_extend is unioned with the archetype's allowlist
```

### Go: Core Agent API

```go
package main

import (
    "context"
    "encoding/json"
    "fmt"
    "log"
    "os"

    agentkit "github.com/agentfox/agentkit-go"
    "github.com/agentfox/agentkit-go/providers"
)

func main() {
    cfg := agentkit.AgentConfig{
        Model:        "claude-opus-4-5",
        MaxTokens:    8192,
        MaxTurns:     50,
        MaxBudgetUSD: 2.00,
        SystemPrompt: "You are a coding assistant.",
    }

    provider := providers.NewAnthropic(os.Getenv("ANTHROPIC_API_KEY"))
    agent := agentkit.NewAgent(cfg, provider)

    agent.RegisterTool(agentkit.Tool{
        Name:        "search",
        Description: "Search files for a regex pattern",
        InputSchema: []byte(`{
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "path":    {"type": "string"}
            },
            "required": ["pattern"]
        }`),
        Handler: func(ctx context.Context, input json.RawMessage) (json.RawMessage, error) {
            var params struct {
                Pattern string `json:"pattern"`
                Path    string `json:"path"`
            }
            if err := json.Unmarshal(input, &params); err != nil {
                return nil, err
            }
            matches, err := grepFiles(ctx, params.Pattern, params.Path)
            if err != nil {
                return nil, err
            }
            return json.Marshal(matches)
        },
    })

    ctx := context.Background()
    result, err := agent.Run(ctx, "Find all TODOs in the codebase and summarize them")
    if err != nil {
        log.Fatal(err)
    }
    fmt.Printf("Result: %s\n", result.FinalText())
    fmt.Printf("Turns: %d, Cost: $%.4f\n", result.TurnCount, result.Usage.CostUSD)
}
```

### Go: Streaming API

```go
stream, err := agent.Stream(ctx, "Refactor the auth module to use the new token interface")
if err != nil {
    log.Fatal(err)
}
defer stream.Close()

for {
    event, ok := stream.Next()
    if !ok {
        break
    }
    switch e := event.(type) {
    case agentkit.TextDeltaEvent:
        fmt.Print(e.Text)
    case agentkit.ToolCallStartEvent:
        fmt.Printf("\n[calling %s (id: %s)]\n", e.Name, e.ToolUseID)
    case agentkit.ToolResultEvent:
        if e.IsError {
            fmt.Printf("[tool error: %v]\n", e.Content)
        }
    case agentkit.TurnEndEvent:
        fmt.Printf("\n[turn complete: %s, %d tokens]\n", e.StopReason, e.Usage.OutputTokens)
    case agentkit.AgentDoneEvent:
        fmt.Printf("\nDone in %d turns, cost $%.4f\n",
            e.Result.TurnCount, e.Result.Usage.CostUSD)
    }
}
```

### Go: Subagent Delegation

```go
// Define a specialist agent
reviewerCfg := agentkit.AgentConfig{
    Model:        "claude-opus-4-5",
    MaxTokens:    4096,
    MaxTurns:     20,
    SystemPrompt: "You are a strict security code reviewer.",
}
reviewer := agentkit.NewAgent(reviewerCfg, provider)

// Wrap it as a tool callable by the orchestrator
orchestrator.RegisterTool(agentkit.SubagentTool(
    "security_review",
    "Run a security-focused code review on a file or diff",
    reviewer,
))

// The orchestrator model can now call security_review as a tool.
// The child always starts with empty history.
// Budget is propagated: child.MaxBudgetUSD = parent.RemainingBudget() * 0.3
```

---

## 8. Non-Functional Requirements

### Performance

- **NFR-PERF-01:** The agentic loop overhead (excluding model API latency and tool execution time) must be less than 5 ms per turn in Python and less than 1 ms per turn in Go.
- **NFR-PERF-02:** MCP server connection setup (stdio subprocess spawn + initialize handshake) must complete within 2 seconds for typical local MCP servers. Connection setup happens once per session, not per tool call.
- **NFR-PERF-03:** Tool schema serialization (translating canonical `Tool` objects to provider wire format) must be computed once per session and cached — not recomputed on every model call.
- **NFR-PERF-04:** Parallel tool execution must use true concurrency (`asyncio.gather` in Python, `errgroup` in Go). Sequential fallback is only used when `parallel_tools=False`.
- **NFR-PERF-05:** The streaming path must not buffer complete model responses before yielding the first token. `TextDeltaEvent` must be emitted as soon as the first streaming delta arrives from the provider.

### Reliability

- **NFR-REL-01:** The agentic loop must tolerate transient provider errors (429, 500–503) via `RetryMiddleware` with exponential backoff and jitter. Default: 3 retries, 1s base delay, 2x multiplier, 0.5 jitter factor.
- **NFR-REL-02:** Tool handler panics (Go) and unhandled exceptions (Python) must never crash the agent process. They must be caught and converted to `tool_result(is_error=True)`.
- **NFR-REL-03:** MCP server disconnects during a session surface as `ToolResultEvent{IsError: true}` for the affected tool call. The agent loop continues. Reconnection is attempted up to `per_session_reconnect_limit` (default 3).
- **NFR-REL-04:** Session state (`ConversationHistory`) must be serializable to JSON and restorable. An interrupted session can resume from a saved snapshot.
- **NFR-REL-05:** History compaction must be applied before each model call (not after), ensuring the model always receives a valid context window. Compaction failures fall back to `TurnWindowCompaction` rather than aborting the session.

### Security

- **NFR-SEC-01:** No credentials (API keys, tokens) may appear in log output, audit events, or error messages. Credentials must be redacted to the first 4 and last 4 characters with `***` in the middle.
- **NFR-SEC-02:** Path containment is enforced by resolving all paths to their absolute canonical form before comparison. No string manipulation that could be fooled by non-canonical paths.
- **NFR-SEC-03:** MCP server configs referencing `${VAR}` environment variables must be resolved at spawn time. Unexpanded variable references are a configuration error, not silently passed to the subprocess.
- **NFR-SEC-04:** The AgentKit MCP server (when enabled in HTTP mode) must require API key authentication on every request. Unauthenticated requests return HTTP 401.
- **NFR-SEC-05:** Plugin and skill code is imported in a context where `sys.path` manipulation is reverted after import. No persistent modification of the process-level Python path.

### Compatibility

- **NFR-COMPAT-01:** Python 3.10, 3.11, 3.12, and 3.13 supported.
- **NFR-COMPAT-02:** Go 1.21 and later minor versions supported.
- **NFR-COMPAT-03:** MCP client supports MCP protocol version 2025-03-26 (current) and maintains backward compatibility with 2024-11-05 servers.
- **NFR-COMPAT-04:** The Anthropic provider supports all current Claude model IDs without hardcoding. The model string is passed through to the API unchanged.
- **NFR-COMPAT-05:** The OpenAI provider supports the OpenAI-compatible API interface so that self-hosted models (vLLM, Ollama with OpenAI-compatible mode) work with only a `base_url` configuration change.

### Testability

- **NFR-TEST-01:** The `ProviderClient` Protocol allows mock implementations with zero external network calls, enabling full loop testing offline.
- **NFR-TEST-02:** The `PluginRegistry` accepts injected plugins without touching entry points or the file system, enabling unit tests for plugin-dependent code.
- **NFR-TEST-03:** `ConversationHistory.snapshot()` and `from_dict()` must round-trip losslessly through JSON. This must be tested with property-based tests covering all `ContentBlock` subtypes.
- **NFR-TEST-04:** Every built-in tool must be independently testable against a temporary directory fixture. No tool may have hidden dependencies on global state.
- **NFR-TEST-05:** The agentic loop must be testable with a scripted mock provider that returns a predetermined sequence of responses (text, tool_use, tool_use+text) to verify correct multi-turn behavior without live API calls.

---

## 9. Open Questions

### OQ-1: Conversation history ownership in multi-turn skill subagent spawning

When a skill declares `[skill.subagent]` with `mode='before_session'`, the session runner spawns a separate session and injects the result into the main session's system prompt. If the pre-analysis session fails or times out, should the main session: (a) abort with an error surfaced to the caller, (b) proceed without the pre-analysis with a warning injected into the system prompt, or (c) make the behavior configurable per skill via a `on_failure` manifest field?

The current design specifies result injection but is silent on failure behavior. A decision is needed before implementing the subagent spawning path in `session.py`. Recommendation: default to option (b) — proceed with warning — since pre-analysis is enrichment, not a hard dependency. Make `on_failure = "abort" | "warn" | "skip"` a configurable manifest field.

### OQ-2: Schema inference in Go — code-gen vs. runtime reflection

The design specifies that Go callers provide JSON Schema literals as `json.RawMessage`. An optional `agentkit-schemagen` code-gen tool would read struct tags and emit schema constants as a build step. The alternative is using Go's `reflect` package at runtime for basic type mapping (`struct` fields to JSON Schema properties), which would reduce boilerplate at the cost of runtime complexity and potential schema inaccuracies for complex types (embedded structs, interface fields, unexported fields).

The code-gen approach is safer and more explicit. The reflection approach is friendlier for simple use cases. For the initial release, the recommendation is to ship with the literal approach and offer `agentkit-schemagen` as an optional companion. Runtime reflection can be added in a later minor version once the core is stable.

### OQ-3: MCP Resources and Prompts in client path

The initial release scopes MCP client support to Tools only. MCP Resources (URI-addressed data blobs) and MCP Prompts (parameterized message templates) are marked as non-goals for v1. However, several planned MCP servers (GitHub, filesystem) expose resources that could reduce token usage by letting the agent reference resources by URI rather than fetching full content.

The implementation complexity for `resources/list` and `resources/read` alongside the existing `tools/list` and `tools/call` in `MCPServerConnection` is low — it is two additional JSON-RPC method implementations on the same connection. The question is whether exposing resources introduces a new attack surface (resource URI auto-fetch as SSRF vector) that requires additional design work. Decision needed before `MCPServerConnection` is finalized.

### OQ-4: SummarizationCompaction — in-process API call vs. Anthropic server-side compaction

`SummarizationCompaction` can either: (a) call the model API directly from within the SDK to generate a summary (adding latency and token cost, but provider-agnostic), or (b) delegate to Anthropic's server-side `compact-2026-01-12` beta mechanism (lower latency, but Anthropic-specific, and requires passing compaction blocks unchanged in subsequent turns — stripping them to plain text silently breaks compaction state).

Recommendation: option (b) as the default for the Anthropic provider with option (a) as the fallback for other providers. The server-side compaction API has been available since early 2026 and appears stable. Stability of the `compact-2026-01-12` beta header needs to be confirmed before shipping.

### OQ-5: AgentKit MCP server authentication in stdio mode

When AgentKit runs as an MCP server in stdio mode (`nightshift --mcp-server`, consumed by Claude Desktop), the process is spawned by the host application and authentication relies on OS-level process isolation. Any process that can read the MCP server's stdio can issue tool calls without a credential check. Is OS-level isolation sufficient for typical developer deployment scenarios, or should an API key be required even for stdio mode (passed via environment variable at spawn time)?

The tradeoff is security vs. usability for local developer setups where Claude Desktop manages the process lifecycle. Claude Code's own built-in tools rely on OS-level isolation for stdio MCP. Recommendation: match Claude Code's approach — no additional credential check for stdio mode in the initial release, but document the threat and provide the environment variable opt-in mechanism for teams with stricter requirements.

### OQ-6: Plugin local directory auto-discovery scope

The design specifies that every Python file under `[plugins] paths` directories containing a class implementing any plugin Protocol is auto-discovered. Auto-discovery is convenient but adds startup latency and risks importing unintended modules. An alternative is requiring an explicit `plugins/__init__.py` or a manifest listing which classes to load — consistent with the entry-point mechanism for installed packages, which requires explicit declaration.

Recommendation: require explicit declaration for local plugins. Add a `plugin.toml` manifest per plugin directory analogous to `skill.toml`. This makes the plugin set predictable and auditable, matches the entry-point registration model, and avoids importing unexpected files in the plugin directory.

### OQ-7: Streaming backpressure in Go

The Go streaming API returns a channel-based `EventStream` with `Next() (Event, bool)`. If the caller processes events slowly, the buffer fills and the producer blocks. The design does not specify channel buffer size or behavior when the buffer is full. Options: (a) unbuffered channel (deadlocks if caller is slow), (b) fixed buffer (e.g., 64 events — appropriate for UI-driving use cases), (c) dropping oldest events with a metric (loses information), (d) blocking producer until consumer catches up (correct for pipeline use cases).

Recommendation: fixed buffer of 64 events as the default, with `StreamOptions.BufferSize` configurable. Document that callers must process events promptly or risk blocking the agent loop. The `Close()` method drains the channel and unblocks the producer if the caller abandons the stream early.