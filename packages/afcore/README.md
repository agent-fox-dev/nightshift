# afcore

Core library for the [Night Shift](https://github.com/agent-fox-dev/nightshift)
autonomous fix daemon. Provides the session runtime, configuration system,
workspace management, knowledge store, archetype system, and the Night Shift
engine.

Requires Python 3.12+.

## Installation

Install from the nightshift monorepo via git:

```bash
pip install "afcore @ git+https://github.com/agent-fox-dev/nightshift.git#subdirectory=packages/afcore"
```

Pin to a release tag:

```bash
pip install "afcore @ git+https://github.com/agent-fox-dev/nightshift.git@v4.2.0#subdirectory=packages/afcore"
```

In `pyproject.toml`:

```toml
[project]
dependencies = [
    "afcore @ git+https://github.com/agent-fox-dev/nightshift.git@v4.2.0#subdirectory=packages/afcore",
]
```

Dependencies include `afaudit`, `afissues`, `afhub`, `afspec`, `anthropic`,
`claude-agent-sdk`, `duckdb`, `pydantic`, `rich`, `click`, and others — see
`pyproject.toml` for the full list.

## Quick Start

```python
from afcore.core.config import load_config
from afcore.nightshift.engine import NightShiftEngine

# Load configuration (merges global + local .nightshift/config.toml)
config = load_config()
```

## API Reference

The package does not re-export from the top level. Import from submodules
directly: `from afcore.core.config import load_config`.

### Configuration (`afcore.core.config`)

| Symbol | Description |
|--------|-------------|
| `load_config(path=None)` | Load and merge global + local TOML config into `AgentFoxConfig`. Single entry point for all CLIs. |
| `resolve_spec_root(config, project_root)` | Resolve the spec directory path from config and project root. |
| `shallow_merge(global_dict, local_dict)` | Merge two config dicts with section-level replacement semantics. |
| `AgentFoxConfig` | Root pydantic model. Contains all sub-configs below. |

Sub-config models (all pydantic `BaseModel` subclasses with documented defaults):

| Model | Key Fields |
|-------|------------|
| `BackendConfig` | `provider` (claude, deepagents, google) |
| `OrchestratorConfig` | `max_retries`, `session_timeout`, `max_cost`, `max_sessions`, `max_budget_usd` |
| `SecurityConfig` | `bash_allowlist`, `bash_allowlist_extend`, `permission_mode` |
| `WorkspaceConfig` | `integration_branch`, `merge_strategy` |
| `KnowledgeConfig` | `store_path`, `provider` (sub-config) |
| `PricingConfig` | Model-keyed `ModelPricing` entries (`input_price_per_m`, `output_price_per_m`, `cache_read_price_per_m`, `cache_creation_price_per_m`) |
| `CachingConfig` | `cache_policy` (NONE / DEFAULT / EXTENDED) |
| `PerArchetypeConfig` | `model_tier`, `max_turns`, `thinking_mode`, `effort`, `allowlist`, `max_budget_usd`, `compaction`, `modes` |
| `ArchetypesConfig` | `overrides` dict of per-archetype config |
| `PlatformConfig` | `type` (none, github, gitlab, gitea), `url` |
| `NightShiftConfig` | `issue_check_interval`, `pr_check_interval`, `push_fix_branch`, `max_parallel`, `max_pr_retries` |
| `HubConfig` | `endpoint_url` |
| `CarryPatchConfig` | `enabled`, `workspace`, `check_interval`, `auto_resolve`, `rebuild_timeout`, `rebuild_poll_interval`, `max_resolve_retries` |
| `ThemeConfig` | `header`, `muted` |

### Archetypes (`afcore.archetypes`)

| Symbol | Description |
|--------|-------------|
| `ArchetypeEntry` | Dataclass — full archetype config: `name`, `templates`, `default_model_tier`, `injection`, `task_assignable`, `retry_predecessor`, `default_allowlist`, `default_max_turns`, `default_thinking_mode`, `default_effort`, `default_compaction`, `injection_order`, `modes: dict[str, ModeConfig]`. |
| `ModeConfig` | Dataclass — per-mode overrides: `templates`, `injection`, `allowlist`, `model_tier`, `max_turns`, `thinking_mode`, `effort`, `retry_predecessor`. |
| `ARCHETYPE_REGISTRY` | `dict[str, ArchetypeEntry]` — built-in archetypes: `coder`, `reviewer`, `verifier`, `gate`, `maintainer`. |
| `get_archetype` | `(name, project_dir=None, config=None) -> ArchetypeEntry` — look up by name with custom archetype fallback. |
| `resolve_effective_config` | `(entry, mode) -> ArchetypeEntry` — merge mode overrides onto base entry. |

### Engine (`afcore.engine`)

| Symbol | Module | Description |
|--------|--------|-------------|
| `ExecutionState` | `engine.state` | Run outcome. Fields: `run_status`, `node_states: dict[str, str]`, `session_history`, `total_cost`, `total_input_tokens`, `total_output_tokens`, `total_sessions`, `blocked_reasons`. |
| `RunStatus` | `engine.state` | StrEnum: `RUNNING`, `COMPLETED`, `COMPLETED_DIRTY`, `INTERRUPTED`, `COST_LIMIT`, `SESSION_LIMIT`, `STALLED`, `BLOCK_LIMIT`. |
| `SessionRecord` | `engine.state` | Per-session outcome: `node_id`, `attempt`, `status`, `archetype`, `model`, `duration_ms`, `cost`, `error_message`, token counts. |

### Night Shift (`afcore.nightshift`)

| Symbol | Module | Description |
|--------|--------|-------------|
| `NightShiftEngine` | `nightshift.engine` | Fix daemon engine. Handles issue polling, triage, coder-reviewer loop, harvest, and staleness detection. |
| `DaemonRunner` | `nightshift.daemon` | Manages work stream lifecycles, PID file, cost budget, and signals. |
| `FixPipeline` | `nightshift.fix_pipeline` | Multi-stage fix pipeline: triage → coder-reviewer loop → harvest. |

### Anthropic Client (`afcore.core.client`)

| Symbol | Description |
|--------|-------------|
| `create_anthropic_client()` | Sync Anthropic client. Auto-detects Vertex AI / Bedrock / direct API via env vars. |
| `create_async_anthropic_client()` | Async variant. |
| `ai_call` | `async (*, model_tier, max_tokens, messages, system, context, cache_policy) -> (text, response)` — high-level: resolve model + create client + retry + track usage + extract text. |
| `ai_call_sync` | Synchronous variant of `ai_call`. |
| `cached_messages_create` | `async (client, *, model, max_tokens, messages, system, cache_policy) -> response` — prompt-caching wrapper around `client.messages.create()`. |
| `cached_messages_create_sync` | Synchronous variant. |
| `retry_api_call_async` | `async (fn, *, context, max_retries=3) -> T` — retry with exponential backoff on transient API errors. |
| `retry_api_call` | Synchronous variant. |
| `extract_response_text` | `(response) -> str \| None` — extract text from first content block. |

### Models (`afcore.core.models`)

| Symbol | Description |
|--------|-------------|
| `ModelTier` | Enum: `SIMPLE`, `STANDARD`, `ADVANCED`. |
| `ModelEntry` | Dataclass: `model_id`, `tier`. |
| `MODEL_REGISTRY` | `dict[str, ModelEntry]` — all known model IDs. |
| `TIER_DEFAULTS` | `dict[ModelTier, str]` — default model ID for each tier. |
| `ModelEntryConfig` | Pydantic model for user-configurable `[models.registry.<id>]` entries: `tier`. |
| `resolve_model` | `(name, *, models_config=None) -> str` — resolve a tier name or model ID to a concrete model ID. |
| `collect_configured_model_ids` | `(models_config=None) -> set[str]` — collect all model IDs that archetypes will use at runtime. |
| `validate_model_access` | `(models_config=None) -> None` — validate configured model IDs are accessible via the API key. |
| `calculate_cost` | `(input_tokens, output_tokens, model_id, pricing, *, cache_read_input_tokens=0, cache_creation_input_tokens=0) -> float` — USD cost. |

### Session (`afcore.session`)

| Symbol | Module | Description |
|--------|--------|-------------|
| `build_system_prompt` | `session.prompt` | `(context, task_group, spec_name, archetype, mode, project_dir) -> str` — 2-layer system prompt assembly (profile + task context). |
| `build_task_prompt` | `session.prompt` | Task prompt construction from spec artifacts and injected findings. |
| `assemble_context` | `session.context` | Gather spec documents, review findings, and steering directives into a structured context object. |

### Knowledge (`afcore.knowledge`)

| Symbol | Module | Description |
|--------|--------|-------------|
| `KnowledgeProvider` | `knowledge.fox_provider` | Protocol with `ingest(session_id, spec_name, context)` and `retrieve(spec_name, task_description, task_group?, session_id?, file_footprint?, archetype?) -> list[str]`. |
| `NoOpKnowledgeProvider` | `knowledge.fox_provider` | Default no-op implementation. |
| `FoxKnowledgeProvider` | `knowledge.fox_provider` | Concrete implementation: review finding carry-forward, session summaries, drift findings. |
| `KnowledgeDB` | `knowledge.db` | DuckDB connection manager for the knowledge store. |

### Task Graph (`afcore.graph.types`)

| Symbol | Description |
|--------|-------------|
| `TaskGraph` | Dataclass: `nodes: dict[str, Node]`, `edges: list[Edge]`, `order: list[str]`, `metadata`. Methods: `predecessors(node_id)`, `successors(node_id)`. |
| `Node` | Dataclass: `id`, `spec_name`, `group_number`, `title`, `optional`, `status`, `archetype`, `mode`, `instances`. |
| `Edge` | Dataclass: `source`, `target`, `kind`. |
| `NodeStatus` | Enum: `PENDING`, `IN_PROGRESS`, `COMPLETED`, `FAILED`, `BLOCKED`, `SKIPPED`, `COST_BLOCKED`, `MERGE_BLOCKED`, `DEFERRED`. |

### Workspace (`afcore.workspace`)

| Symbol | Module | Description |
|--------|--------|-------------|
| `create_worktree` | `workspace.worktree` | `(repo_root, branch_name, base_ref, worktree_dir) -> WorkspaceInfo` — create an isolated git worktree for a coding session. |
| `destroy_worktree` | `workspace.worktree` | `(workspace) -> None` — remove worktree and delete feature branch. |
| `WorkspaceInfo` | `workspace.worktree` | Dataclass: `path`, `branch`, `base_ref`. |
| `run_git` | `workspace.git` | `(*args, cwd) -> str` — run a git command and return stdout. |
| `ensure_integration_branch` | `workspace.integration` | Set up the integration branch for merging. |
| `push_to_remote` | `workspace.git` | `(branch, cwd, remote="origin") -> None` — push a branch to origin. |

### Platform (via `afissues`)

The platform/forge abstraction layer has been extracted to the standalone
[`afissues`](../afissues/) package. Import from `afissues` directly:

| Symbol | Module | Description |
|--------|--------|-------------|
| `PlatformProtocol` | `afissues.protocol` | Protocol for issue/PR management: `create_issue`, `list_issues_by_label`, `add_issue_comment`, `assign_label`, `close_issue`, `create_pull_request`, etc. |
| `IssueResult` | `afissues.protocol` | Dataclass: `number`, `title`, `body`, `labels`, `html_url`. |
| `GitHubPlatform` | `afissues.github` | GitHub implementation of `PlatformProtocol` using `httpx.AsyncClient`. |

### Security (`afcore.core.security`)

| Symbol | Description |
|--------|-------------|
| `DEFAULT_ALLOWLIST` | `frozenset[str]` — ~46 default-allowed shell commands (ls, cat, git, make, pytest, etc.). |
| `make_pre_tool_use_hook` | `(security_config) -> Callable` — build a permission callback for the session runtime. |

### Errors (`afcore.core.errors`)

| Exception | Description |
|-----------|-------------|
| `AgentFoxError` | Base exception with `context: dict` for structured error metadata. |
| `ConfigError` | Configuration loading or validation failure. |
| `PlanError` | Task graph construction failure. |
| `WorkspaceError` | Git/worktree operation failure. |
| `IntegrationError` | Merge/push failure. Has `retryable: bool` flag. |
| `SecurityError` | Blocked command or permission violation. |
| `KnowledgeStoreError` | DuckDB or knowledge provider failure. |
