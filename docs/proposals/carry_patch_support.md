# Carry-Patch Support for Nightshift

> Proposal for adding carry-patch workspace automation to the nightshift daemon,
> backed by the af-hub carry-patch API.

## What is Carry-Patch?

Carry-patch is a workspace mode in af-hub for organizations that maintain a fork
of an upstream repository they do not control. It automates the recurring burden
of carrying local modifications -- configuration changes, bug fixes, security
patches, vendor-specific features -- on top of a moving upstream baseline.

Without automation, engineers must manually fetch upstream, rebase or cherry-pick
each local branch, resolve conflicts (which reappear after force-pushes), and
notice when patches have been merged upstream. The carry-patch workflow
mechanizes all of this through:

1. **Upstream tracking**: Automated fetch and detection of upstream advances
2. **Merged-patch detection**: Ancestry-based detection of patches that have been
   accepted upstream
3. **Integration branch rebuilding**: Mechanical reconstruction of a "deploy"
   branch as upstream HEAD + all active patches in position order
4. **Conflict resolution memory**: Git rerere records resolutions so the same
   conflicts do not require repeated manual fixes

The core entities are:

- **Workspace** (carry_patch mode): Links a fork repo (origin) with an upstream
  repo, defining an integration branch (default "deploy")
- **Patch**: A registered branch name with position, status
  (active/merged_upstream/conflict/disabled), and metadata
- **Rebuild Job**: An asynchronous job that reconstructs the integration branch
  by replaying patches onto upstream HEAD

The value proposition for nightshift is significant: carry-patch workspaces
create a steady stream of operational work (conflict resolution, patch
maintenance, rebuild monitoring) that is ideally suited to AI-driven automation.

---

## Hub Integration Surface

Nightshift needs to call the following hub API endpoints, all mounted under
`/api/v1` and authenticated via `Authorization: Bearer <token>`:

### Workspace Operations

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/workspaces` | POST | Create carry-patch workspace |
| `/workspaces/:slug/sync` | POST | Trigger upstream sync (returns `patches_merged`, `rebuild_triggered`) |
| `/workspaces/:slug/patch-status` | GET | Dashboard: workspace metadata, per-patch status, rebuild summary |

### Patch Management

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/workspaces/:slug/patches` | GET | List patches ordered by position |
| `/workspaces/:slug/patches` | POST | Add patch (branch_name, position, description) |
| `/workspaces/:slug/patches/:id` | PATCH | Update status/position/description |
| `/workspaces/:slug/patches/:id` | DELETE | Remove patch, auto-compact positions |
| `/workspaces/:slug/patches/reorder` | POST | Full reorder via ordered patch_ids array |

### Rebuild Operations

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/workspaces/:slug/rebuild` | POST | Submit rebuild job (returns 202 with job ID) |
| `/workspaces/:slug/rebuilds` | GET | List rebuild jobs |
| `/workspaces/:slug/rebuilds/:id` | GET | Get rebuild with patch_results |

### Support Operations

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/workspaces/:slug/rerere` | GET | List recorded conflict resolutions |
| `/workspaces/:slug/rerere/*pathspec` | DELETE | Forget a resolution |
| `/workspaces/:slug/secrets` | POST | Store upstream credentials |
| `/workspaces/:slug/vars` | GET/POST/PATCH/DELETE | Manage workspace variables |

### Key Protocol Details

- Rebuild jobs are asynchronous: POST returns 202 with job ID; nightshift must
  poll GET `/rebuilds/:id` for completion
- Only one rebuild can be queued/running per workspace (concurrent submission
  returns 409)
- Sync for carry-patch workspaces returns extra fields (`patches_merged`,
  `rebuild_triggered`) indicating merged patch detection and auto-rebuild
  triggering
- Errors use envelope format: `{"error": {"code": NNN, "message": "..."}}`
- Anti-enumeration: unauthorized access returns 404, not 403

---

## Current Nightshift Architecture

### Daemon Framework

Nightshift runs as a daemon via `DaemonRunner` that schedules `WorkStream`
instances. Today there are two streams:

- **fix-pipeline**: Polls for `af:fix` labeled issues, processes them through
  `FixPipeline` (triage -> coder-reviewer loop -> integration)
- **pr-feedback**: Polls for `af:pr` labeled issues and handles CI
  failure/review feedback (only active when `merge_strategy="pr"`)

Streams are registered in `build_streams()` at
`packages/agentfox/agentfox/nightshift/streams.py`. Each stream wraps an engine
method via `EngineWorkStream`, which delegates to `NightShiftEngine` methods.

### Fix Pipeline Flow

The `FixPipeline` in `packages/agentfox/agentfox/nightshift/fix_pipeline.py`
orchestrates per-issue processing:

1. Build `InMemorySpec` from issue
2. Create isolated git worktree
3. Run triage session (maintainer:fix-triage archetype)
4. Coder-reviewer loop with retry/escalation
5. Auto-commit pending changes
6. Integrate fix via one of three merge strategies (direct/branch/pr)
7. Handle result (close issue, add labels)

### Git Operations Layer

The workspace package (`packages/agentfox/agentfox/workspace/`) provides:

- `git.py`: Low-level async git wrappers (`run_git()`, `validate_ref_name()`,
  `create_branch()`, etc.)
- `worktree.py`: Worktree lifecycle (`create_worktree()`, `destroy_worktree()`)
- `harvest.py`: Squash-merge integration via `harvest()`
- `merge_lock.py`: Cross-process/cross-task merge serialization
- `merge_agent.py`: AI-driven conflict resolution

### Configuration and Archetypes

- Config in Pydantic models (`AgentFoxConfig` with `NightShiftConfig`,
  `WorkspaceConfig`, etc.)
- Archetype registry with 5 built-in archetypes (coder, reviewer, verifier,
  gate, maintainer)
- Mode-based overrides (e.g., coder has a "fix" mode with specific parameters)
- 3-tier resolution: mode TOML > archetype TOML > registry default

### HTTP Infrastructure

The `afissues` package uses `httpx` for async HTTP with retry logic in
`_http.py`. The `request_with_retry()` function handles transient errors
(ConnectTimeout, ConnectError, ReadTimeout) with exponential backoff.

---

## Gap Analysis

### What nightshift has that can be reused

1. **WorkStream protocol**: The daemon framework directly supports registering
   new streams via `build_streams()`
2. **Archetype/mode system**: New modes (e.g., `coder:carry-patch`) can be added
   to the registry without schema changes
3. **Async git wrappers**: `run_git()`, `validate_ref_name()`,
   `create_branch()`, `checkout_branch()` all work for carry-patch operations
4. **HTTP retry infrastructure**: `request_with_retry()` in `afissues/_http.py`
   provides the pattern (though not directly reusable due to package boundaries)
5. **Merge lock**: `MergeLock` serializes concurrent integration branch mutations
6. **Merge agent**: AI-driven conflict resolution already exists
7. **Knowledge system**: Session outcomes, prior attempts, and knowledge
   retrieval all apply
8. **Audit events**: The event infrastructure needs only new event types
9. **Config system**: `ConfigDict(extra="ignore")` means new config sections are
   backward-compatible

### What nightshift is missing

1. **Hub API client**: No HTTP client exists for communicating with af-hub. The
   `afissues` HTTP layer talks to GitHub/GitLab/Gitea APIs, not to hub. A new
   `HubClient` class is needed.
2. **Hub authentication**: No mechanism to read hub credentials
   (`~/.af/config.toml` with `endpoint_url`, `user_id`, `api_key`). Nightshift
   currently authenticates only via platform-specific env vars (GITHUB_PAT,
   etc.).
3. **Asynchronous job polling**: The fix pipeline is synchronous per-issue (run
   session, wait for result). Carry-patch rebuilds are asynchronous (submit, poll
   for completion). Nightshift has no polling-with-backoff infrastructure for
   external jobs.
4. **Carry-patch domain models**: No data structures for Workspace, Patch,
   RebuildJob, PatchResult, PatchStatusDashboard.
5. **Carry-patch work stream**: No stream implementation for the carry-patch
   polling/dispatch loop.
6. **Carry-patch pipeline**: No pipeline analogous to `FixPipeline` for the
   carry-patch workflow (sync, detect conflicts, resolve, rebuild, verify).
7. **Cherry-pick operations**: `git.py` has no `cherry_pick()` function. The
   existing `rebase_onto()` does whole-branch rebase but not selective commit
   cherry-picking. (Note: this may not be needed if nightshift delegates
   rebuilding entirely to hub.)
8. **Multi-workspace awareness**: Nightshift currently operates on a single
   repository. Carry-patch may require the daemon to monitor multiple hub
   workspaces.
9. **Carry-patch profile templates**: No agent profile exists for carry-patch
   behavioral instructions.
10. **Carry-patch labels**: Only `af:fix`, `af:fixed`, `af:pr`, `af:no-change`
    exist. Carry-patch needs its own label set or a different triggering
    mechanism.

---

## Risk Analysis

### High Risk

- **Scope creep**: The hub's carry-patch surface is large (workspace CRUD, patch
  management, rebuild, sync, rerere, credentials, variables). Trying to automate
  everything at once will lead to an unwieldy implementation. Phased delivery is
  essential.
- **Hub availability dependency**: Nightshift currently operates independently
  against a git repository and issue tracker. Adding hub as a dependency creates
  a new failure mode. Hub being unavailable must not crash the daemon or block
  the fix pipeline.
- **Conflict resolution quality**: The existing merge agent resolves conflicts in
  squash-merge scenarios. Carry-patch conflicts may be more complex (upstream
  divergence + multiple patches interacting). The agent may need a specialized
  prompt profile.

### Medium Risk

- **Polling complexity**: Rebuild jobs are asynchronous. Nightshift must poll for
  completion without busy-waiting, handle timeouts, and deal with the 409
  constraint (only one rebuild per workspace). The existing WorkStream
  interval-based polling is coarse-grained for this.
- **State synchronization**: Hub is the source of truth for patch status, rebuild
  results, and upstream state. Nightshift must avoid caching stale state and
  always re-fetch before acting.
- **Known hub bugs**: The `conflict_files` column does not exist in hub's
  production schema, causing 500 errors from the patch-status endpoint.
  Nightshift must handle this gracefully.
- **Testing complexity**: End-to-end testing requires a running hub instance or
  comprehensive mocks of the hub API surface.

### Low Risk

- **Configuration extension**: Adding new config sections is backward-compatible
  due to `extra="ignore"`. No migration needed.
- **Archetype/mode extension**: The mode system already supports arbitrary mode
  names. No schema changes needed.
- **Git operations**: The existing git wrapper layer is robust with timeout
  routing, ref validation, and error classification.

---

## Architectural Fit

### Strong Fit

The carry-patch workflow maps well onto nightshift's existing patterns:

1. **WorkStream as extension point**: A `carry-patch` stream slots naturally into
   `build_streams()` alongside `fix-pipeline` and `pr-feedback`. The daemon
   framework handles scheduling, budget tracking, and graceful shutdown.

2. **Archetype modes for behavioral control**: A `coder:carry-patch` mode with a
   dedicated profile template follows the exact pattern used by `coder:fix`. The
   3-tier resolution system provides user customizability without code changes.

3. **Issue-driven triggering**: The fix pipeline's label-based triggering
   (`af:fix`) provides a pattern for carry-patch. A label like
   `af:carry-conflict` could flag patches needing AI-assisted conflict
   resolution.

4. **Fail-open error handling**: Nightshift's pervasive pattern of logging
   failures and continuing (never crashing the daemon) is exactly right for hub
   integration, where the hub may be temporarily unreachable.

### Areas of Tension

1. **Single-repo vs. multi-workspace**: Nightshift assumes a single repository
   context. Carry-patch operates on hub workspaces that may correspond to
   different repositories. The daemon may need to manage multiple workspace
   contexts.

2. **Synchronous pipeline vs. asynchronous jobs**: `FixPipeline.process_issue()`
   runs a complete pipeline synchronously (blocking until the fix is merged or
   fails). Carry-patch rebuilds are fire-and-forget with polling. This requires a
   different execution model -- closer to a state machine than a linear pipeline.

3. **Local git vs. remote hub**: The fix pipeline operates on local git
   worktrees. Carry-patch operations (sync, rebuild, patch status) are remote API
   calls to hub. The "workspace" concept in nightshift (a local directory with a
   worktree) differs from the hub "workspace" (a server-side repository clone).

4. **Budget accounting**: Carry-patch conflict resolution sessions consume LLM
   tokens just like fix sessions. The `SharedBudget` mechanism works, but the
   budget must be partitioned or prioritized between fix-pipeline and carry-patch
   streams.

### Recommended Approach

The cleanest integration is a **new WorkStream** (`carry-patch`) with its own
**pipeline class** (`CarryPatchPipeline`), backed by a **hub API client**
(`HubClient`). This approach:

- Keeps carry-patch logic isolated from the fix pipeline
- Uses the existing daemon framework for scheduling and lifecycle
- Adds hub as an optional dependency (the daemon still works without hub
  configured)
- Allows independent development and testing of carry-patch features

---

## Design Decisions (Resolve Before/During Implementation)

### DD-1: Hub as optional dependency

**Decision**: Hub integration is opt-in via configuration. When `[hub]` section
is absent or `hub.endpoint_url` is empty, all carry-patch functionality is
disabled. The fix-pipeline and pr-feedback streams continue to work
independently.

**Rationale**: Nightshift must remain functional without hub. Many users will
never use carry-patch.

### DD-2: Workspace discovery mechanism

**Decision**: Nightshift monitors a configured list of hub workspace slugs (via
`[hub] workspaces = ["slug-1", "slug-2"]` in config). It does not auto-discover
workspaces.

**Rationale**: Auto-discovery would require listing all workspaces and filtering
by mode, which introduces security and scope concerns. Explicit configuration is
safer and more predictable.

**Alternative considered**: Single workspace mode (one slug in config). Rejected
because organizations often maintain multiple forks.

### DD-3: Conflict resolution strategy

**Decision**: When a rebuild fails with a conflict, nightshift:

1. Checks out the conflicting patch branch locally (via hub git server clone)
2. Applies the upstream changes that cause the conflict
3. Runs a coder:carry-patch session to resolve the conflict
4. Pushes the resolution back to hub
5. Resets patch status to active and resubmits the rebuild

**Rationale**: This leverages the existing merge agent pattern but with a
carry-patch-specific profile that understands the upstream-vs-patch context.

**Alternative considered**: Only report conflicts and wait for human resolution.
Rejected as it defeats the purpose of nightshift automation, though a
`carry_patch.auto_resolve = false` config option should exist as an escape hatch.

### DD-4: Rebuild polling strategy

**Decision**: After submitting a rebuild, poll at increasing intervals: 5s, 10s,
20s, 30s, then every 30s up to a configurable timeout (default 10 minutes). Use
the WorkStream's `run_once()` cycle to check pending rebuilds rather than
blocking.

**Rationale**: Rebuilds typically complete in seconds to minutes. Exponential
backoff avoids hammering the hub API while maintaining responsiveness.

### DD-5: Integration with issue tracker

**Decision**: Carry-patch operations are NOT driven by issue labels. Instead, the
carry-patch stream polls hub workspaces on a timer, checking for: (a) pending
syncs, (b) patches in conflict status, (c) patches detected as merged upstream.
Issues on the fork's tracker may optionally be created for conflicts that require
human attention.

**Rationale**: Carry-patch is workspace-driven, not issue-driven. The hub is the
source of truth, not the issue tracker. This avoids coupling two external
systems.

### DD-6: Where to place the hub client

**Decision**: Create a new `packages/afhub/` package for the hub API client,
following the same pattern as `packages/afissues/`. This package owns all hub
communication, data models, and authentication.

**Rationale**: Separation of concerns. The hub client has its own authentication
model (API keys from `~/.af/config.toml`), its own error types, and its own data
models. Placing it in `agentfox` would violate the existing package boundaries.

**Alternative considered**: Adding hub client to `agentfox/nightshift/`. Rejected
because the hub client is a general-purpose API layer, not nightshift-specific
logic.

### DD-7: Local clone for conflict resolution

**Decision**: For conflict resolution, nightshift clones the workspace from hub's
git server into a local worktree (under
`.nightshift/worktrees/carry-patch/{workspace-slug}/`). The clone is shallow and
cached between cycles.

**Rationale**: Conflict resolution requires local file access for the coder
agent. Hub's git server at `/git/:org/:slug.git` provides authenticated access.
Caching the clone avoids re-cloning on every cycle.

---

## Implementation Plan

### Phase 1: Foundation — Hub Client and Configuration (~35% of effort)

#### 1.1 Create the `afhub` package

**Complexity**: Large

Files to create:

- `packages/afhub/pyproject.toml` -- Package metadata, dependencies (httpx,
  pydantic)
- `packages/afhub/afhub/__init__.py` -- Public API exports
- `packages/afhub/afhub/client.py` -- `HubClient` class
- `packages/afhub/afhub/models.py` -- Pydantic data models
- `packages/afhub/afhub/errors.py` -- Hub-specific error types
- `packages/afhub/afhub/auth.py` -- Credential loading from
  `~/.af/config.toml`
- `packages/afhub/tests/` -- Test directory

**`HubClient` class** (`client.py`):

```python
class HubClient:
    """Async HTTP client for the af-hub API."""

    def __init__(self, endpoint_url: str, api_key: str) -> None: ...

    # Workspace operations
    async def get_workspace(self, slug: str) -> Workspace: ...
    async def create_workspace(self, **kwargs) -> Workspace: ...
    async def sync_workspace(self, slug: str, *, reset_to_upstream: bool = False) -> SyncResult: ...
    async def get_patch_status(self, slug: str) -> PatchStatusDashboard: ...

    # Patch operations
    async def list_patches(self, slug: str) -> list[Patch]: ...
    async def add_patch(self, slug: str, branch_name: str, **kwargs) -> Patch: ...
    async def update_patch(self, slug: str, patch_id: str, **kwargs) -> Patch: ...
    async def remove_patch(self, slug: str, patch_id: str) -> None: ...
    async def reorder_patches(self, slug: str, patch_ids: list[str]) -> None: ...

    # Rebuild operations
    async def submit_rebuild(self, slug: str) -> RebuildJob: ...
    async def get_rebuild(self, slug: str, rebuild_id: str) -> RebuildJob: ...
    async def list_rebuilds(self, slug: str) -> list[RebuildJob]: ...
    async def poll_rebuild(self, slug: str, rebuild_id: str, *, timeout: float = 600) -> RebuildJob: ...

    # Rerere operations
    async def list_rerere(self, slug: str) -> list[RerereEntry]: ...
    async def forget_rerere(self, slug: str, pathspec: str) -> None: ...

    # Variables
    async def get_variable(self, slug: str, key: str) -> str | None: ...
    async def set_variable(self, slug: str, key: str, value: str) -> None: ...
```

The `poll_rebuild()` method implements the polling strategy from DD-4 internally,
returning the terminal-state rebuild job or raising `RebuildTimeoutError`.

**Data models** (`models.py`):

```python
class Workspace(BaseModel):
    slug: str
    git_url: str
    upstream_url: str | None
    integration_branch: str
    workspace_mode: str  # "standard" | "carry_patch"
    clone_status: str
    sync_status: str
    upstream_head_sha: str | None
    head_sha: str | None
    last_sync_at: str | None

class Patch(BaseModel):
    id: str
    workspace_slug: str
    branch_name: str
    position: int
    status: str  # "active" | "merged_upstream" | "conflict" | "disabled"
    upstream_pr_url: str | None
    description: str | None
    added_at: str
    updated_at: str

class PatchResult(BaseModel):
    patch_id: str
    branch_name: str
    position: int
    status: str  # "success" | "conflict" | "skipped"
    new_head_sha: str | None

class RebuildJob(BaseModel):
    id: str
    status: str  # "queued" | "running" | "completed" | "failed"
    strategy: str | None
    patch_results: list[PatchResult]
    error: str | None
    created_at: str
    completed_at: str | None

class SyncResult(BaseModel):
    patches_merged: list[str]
    rebuild_triggered: bool

class PatchStatusDashboard(BaseModel):
    workspace_slug: str
    workspace_mode: str
    upstream_url: str | None
    upstream_head_sha: str | None
    integration_branch: str
    integration_head_sha: str | None
    last_sync_at: str | None
    last_rebuild: RebuildSummary | None
    patches: list[PatchDetail]
    summary: PatchSummary
```

**Error types** (`errors.py`):

```python
class HubError(Exception): ...
class HubAuthError(HubError): ...
class HubNotFoundError(HubError): ...
class HubConflictError(HubError): ...   # 409 - rebuild already running
class RebuildTimeoutError(HubError): ...
class HubConnectionError(HubError): ...
```

**Auth** (`auth.py`):

- `load_hub_credentials()` reads `~/.af/config.toml` and returns
  `(endpoint_url, api_key)`
- Falls back to environment variables `AF_HUB_URL` and `AF_HUB_API_KEY`
- Returns `None` if neither is configured (hub is optional)

#### 1.2 Add hub configuration to nightshift

**Complexity**: Small

Files to modify:

- `packages/agentfox/agentfox/core/config.py` -- Add `HubConfig` and
  `CarryPatchConfig` models

```python
class HubConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    endpoint_url: str = Field(default="", description="Hub API endpoint URL")
    api_key: str = Field(default="", description="Hub API key (prefer AF_HUB_API_KEY env var)")

class CarryPatchConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = Field(default=False, description="Enable carry-patch work stream")
    workspaces: list[str] = Field(default_factory=list, description="Hub workspace slugs to monitor")
    check_interval: int = Field(default=300, description="Seconds between checks (minimum 60)")
    auto_resolve: bool = Field(default=True, description="Auto-resolve conflicts via AI agent")
    rebuild_timeout: int = Field(default=600, description="Seconds to wait for rebuild completion")
    max_resolve_retries: int = Field(default=2, description="Max conflict resolution attempts per patch")
```

Add to `AgentFoxConfig`:

```python
hub: HubConfig = Field(default_factory=HubConfig)
carry_patch: CarryPatchConfig = Field(default_factory=CarryPatchConfig)
```

#### 1.3 Add afhub as a dependency

**Complexity**: Small

Files to modify:

- `packages/agentfox/pyproject.toml` -- Add `afhub` as an optional dependency
- `packages/nightshift/pyproject.toml` -- Add `afhub` as a dependency

#### 1.4 Tests for Phase 1

**Complexity**: Medium

Files to create:

- `packages/afhub/tests/test_client.py` -- Unit tests with httpx mock transport
- `packages/afhub/tests/test_models.py` -- Model serialization/deserialization
- `packages/afhub/tests/test_auth.py` -- Credential loading
- `packages/afhub/tests/test_errors.py` -- Error classification
- `packages/afhub/tests/test_poll_rebuild.py` -- Polling behavior (backoff,
  timeout)

---

### Phase 2: Core Carry-Patch Logic (Depends on Phase 1, ~30% of effort)

#### 2.1 Carry-patch pipeline

**Complexity**: Large

Files to create:

- `packages/agentfox/agentfox/nightshift/carry_patch_pipeline.py`

```python
class CarryPatchPipeline:
    """Manages the carry-patch lifecycle for a single hub workspace."""

    def __init__(
        self,
        hub_client: HubClient,
        workspace_slug: str,
        config: AgentFoxConfig,
        callbacks: ...,
        knowledge_provider: KnowledgeProvider | None = None,
        conn: duckdb.DuckDBPyConnection | None = None,
    ) -> None: ...

    async def run_cycle(self) -> CarryPatchCycleResult:
        """Execute one carry-patch maintenance cycle.

        Steps:
        1. Fetch patch-status dashboard from hub
        2. Report any newly merged patches (informational)
        3. If patches are in conflict status and auto_resolve is enabled:
           a. Clone/update workspace from hub git server
           b. For each conflicting patch, run conflict resolution
           c. Push resolved patch branch back to hub
           d. Reset patch status to active
           e. Submit new rebuild
        4. If no conflicts but rebuild is needed (e.g., after sync):
           a. Submit rebuild
           b. Poll for completion
        5. Return cycle result with actions taken
        """

    async def _resolve_conflict(
        self, patch: Patch, dashboard: PatchStatusDashboard
    ) -> bool: ...

    async def _ensure_local_clone(self) -> Path: ...

    async def _push_resolution(self, patch: Patch, clone_path: Path) -> None: ...
```

Key behaviors:

- **Idempotent cycles**: Each `run_cycle()` call fetches fresh state from hub
  and acts only on what is needed. No persistent local state beyond the cached
  git clone.
- **Conflict resolution**: Uses the coder:carry-patch archetype+mode. The agent
  receives the conflicting patch's changes, the upstream changes that cause the
  conflict, and instructions to resolve while preserving the patch's intent.
- **Rebuild management**: Respects the 409 constraint (only one rebuild at a
  time). If a rebuild is already running, the cycle logs this and returns without
  submitting.
- **Error handling**: Follows nightshift's fail-open pattern. A failed conflict
  resolution for one patch does not block attempts on other patches. Hub
  connection failures are logged and the cycle skips.

#### 2.2 Carry-patch engine integration

**Complexity**: Medium

Files to modify:

- `packages/agentfox/agentfox/nightshift/engine.py` -- Add carry-patch dispatch
  method

```python
async def _run_carry_patch_cycle(self) -> None:
    """Run one carry-patch maintenance cycle across all configured workspaces.

    For each workspace slug in config.carry_patch.workspaces:
    1. Get or create CarryPatchPipeline instance
    2. Call pipeline.run_cycle()
    3. Update state counters
    """
```

The engine lazily creates `CarryPatchPipeline` instances (one per workspace
slug), cached in a dict. The `HubClient` is shared across all pipelines.

#### 2.3 Register carry-patch work stream

**Complexity**: Small

Files to modify:

- `packages/agentfox/agentfox/nightshift/streams.py` -- Add carry-patch stream
  to `build_streams()`

```python
carry_patch_cfg = getattr(config, "carry_patch", None)
carry_patch_enabled = (
    getattr(carry_patch_cfg, "enabled", False)
    and bool(getattr(carry_patch_cfg, "workspaces", []))
)
if carry_patch_enabled:
    cp_interval = getattr(carry_patch_cfg, "check_interval", 300)
    streams.append(
        EngineWorkStream(
            stream_name="carry-patch",
            engine=engine,
            method_name="_run_carry_patch_cycle",
            budget=budget,
            enabled=True,
            interval=cp_interval,
        )
    )
```

- `packages/agentfox/agentfox/nightshift/daemon.py` -- Add `"carry-patch"` to
  `_STREAM_DISPLAY_NAMES` and `_STREAM_ACTIVE_LABELS`

#### 2.4 Hub client initialization in daemon startup

**Complexity**: Small

Files to modify:

- `packages/nightshift/nightshift/app.py` -- Initialize `HubClient` in
  `_run_daemon()` and pass to engine

```python
hub_client = None
if config.carry_patch.enabled and config.carry_patch.workspaces:
    from afhub.auth import load_hub_credentials
    creds = load_hub_credentials(config.hub)
    if creds:
        from afhub.client import HubClient
        hub_client = HubClient(creds.endpoint_url, creds.api_key)
    else:
        logger.warning("Carry-patch enabled but hub credentials not found; disabling")
```

#### 2.5 Audit events for carry-patch

**Complexity**: Small

Files to modify:

- `packages/afaudit/afaudit/events.py` -- Add new event types

```python
CARRY_PATCH_CYCLE_START = "carry_patch_cycle_start"
CARRY_PATCH_CYCLE_COMPLETE = "carry_patch_cycle_complete"
CARRY_PATCH_CONFLICT_DETECTED = "carry_patch_conflict_detected"
CARRY_PATCH_CONFLICT_RESOLVED = "carry_patch_conflict_resolved"
CARRY_PATCH_CONFLICT_FAILED = "carry_patch_conflict_failed"
CARRY_PATCH_REBUILD_SUBMITTED = "carry_patch_rebuild_submitted"
CARRY_PATCH_REBUILD_COMPLETED = "carry_patch_rebuild_completed"
CARRY_PATCH_REBUILD_FAILED = "carry_patch_rebuild_failed"
CARRY_PATCH_MERGED_DETECTED = "carry_patch_merged_detected"
```

#### 2.6 Tests for Phase 2

**Complexity**: Large

Files to create:

- `packages/agentfox/tests/test_carry_patch_pipeline.py` -- Pipeline logic tests
  with mocked HubClient
- `packages/agentfox/tests/test_carry_patch_stream.py` -- Stream registration
  and enablement
- `packages/agentfox/tests/test_carry_patch_engine.py` -- Engine dispatch

Key test scenarios:

- Cycle with no conflicts, no rebuilds needed (no-op)
- Cycle with one patch in conflict status -> resolution -> rebuild submission
- Cycle with rebuild already running (409 handling)
- Cycle with hub unreachable (graceful degradation)
- Cycle with merged patches detected (informational reporting)
- Multiple workspaces in a single cycle
- Auto-resolve disabled (skip conflict resolution, only report)
- Rebuild timeout handling
- Budget exhaustion during conflict resolution

---

### Phase 3: CLI Integration (Depends on Phase 1, ~10% of effort)

#### 3.1 Carry-patch CLI subcommands

**Complexity**: Medium

Files to modify:

- `packages/nightshift/nightshift/app.py` -- Add carry-patch subcommand group

```python
@main.group("carry-patch")
def carry_patch_group():
    """Manage carry-patch workspaces."""

@carry_patch_group.command("status")
@click.argument("workspace_slug")
def cp_status(workspace_slug: str):
    """Show patch-status dashboard for a workspace."""

@carry_patch_group.command("sync")
@click.argument("workspace_slug")
@click.option("--reset-to-upstream", is_flag=True)
def cp_sync(workspace_slug: str, reset_to_upstream: bool):
    """Trigger upstream sync for a workspace."""

@carry_patch_group.command("rebuild")
@click.argument("workspace_slug")
@click.option("--wait/--no-wait", default=True)
def cp_rebuild(workspace_slug: str, wait: bool):
    """Submit a rebuild and optionally wait for completion."""

@carry_patch_group.command("patches")
@click.argument("workspace_slug")
def cp_patches(workspace_slug: str):
    """List patches for a workspace."""
```

These are one-shot commands (not daemon streams) for manual interaction with
carry-patch workspaces.

#### 3.2 Rich output formatting

**Complexity**: Small

Files to create:

- `packages/agentfox/agentfox/ui/carry_patch.py` -- Rich table formatters

Functions:

- `render_patch_status_dashboard(dashboard: PatchStatusDashboard) -> Table`
- `render_patch_list(patches: list[Patch]) -> Table`
- `render_rebuild_status(rebuild: RebuildJob) -> Table`

#### 3.3 Tests for Phase 3

**Complexity**: Small

Files to create:

- `packages/nightshift/tests/test_carry_patch_cli.py` -- CLI invocation tests

---

### Phase 4: Agent Integration — Conflict Resolution (Depends on Phase 2, ~15% of effort)

#### 4.1 Carry-patch archetype mode

**Complexity**: Small

Files to modify:

- `packages/agentfox/agentfox/archetypes.py` -- Add `carry-patch` mode to coder
  archetype

```python
"carry-patch": ModeConfig(
    model_tier="STANDARD",
    max_turns=200,
    thinking_mode="adaptive",
    effort="high",
)
```

#### 4.2 Carry-patch profile template

**Complexity**: Medium

Files to create:

- `packages/agentfox/agentfox/_templates/profiles/coder_carry-patch.md`

Profile content should instruct the agent:

- You are resolving a conflict between a local patch and upstream changes
- The patch exists for a specific reason (provided in patch description)
- Preserve the intent of the patch while adapting to upstream changes
- Do not introduce new features or refactor beyond what the conflict requires
- Use conventional commits for the resolution
- Test the resolution if a test suite is available
- Explain what changed and why in the commit message

#### 4.3 Conflict resolution session runner

**Complexity**: Medium

Files to create:

- `packages/agentfox/agentfox/nightshift/carry_patch_resolver.py`

```python
class CarryPatchResolver:
    """Runs a coder:carry-patch session to resolve a patch conflict."""

    async def resolve(
        self,
        clone_path: Path,
        patch: Patch,
        upstream_head: str,
        conflict_context: str,
    ) -> ResolveResult:
        """Set up the conflict scenario and run the agent.

        Steps:
        1. Checkout the patch branch
        2. Attempt rebase onto upstream HEAD to reproduce the conflict
        3. Build context with conflict details, patch description, upstream diff
        4. Run coder:carry-patch session
        5. Verify resolution (no conflict markers remain)
        6. Return success/failure
        """
```

This class uses `run_session()` from `agentfox/session/session.py` with
`archetype="coder"` and `mode="carry-patch"`.

#### 4.4 Context construction for conflict resolution

**Complexity**: Small

Files to modify (if needed):

- `packages/agentfox/agentfox/session/context.py` -- Add carry-patch to
  `_ARCHETYPE_ARTIFACTS`

The conflict resolution context includes:

- Patch description (why the patch exists)
- Conflict files and markers
- Upstream changes that caused the conflict (diff)
- Git rerere history if available
- Prior resolution attempts (from knowledge store)

#### 4.5 Tests for Phase 4

**Complexity**: Medium

Files to create:

- `packages/agentfox/tests/test_carry_patch_resolver.py` -- Resolver tests with
  mock sessions
- `packages/agentfox/tests/test_carry_patch_profile.py` -- Profile loading and
  content tests

---

### Phase 5: Testing and Hardening (Depends on all previous phases, ~10% ongoing)

#### 5.1 Integration test suite

**Complexity**: Large

Files to create:

- `packages/afhub/tests/test_integration.py` -- Integration tests against a mock
  hub server (httpx mock transport)
- `packages/agentfox/tests/test_carry_patch_e2e.py` -- End-to-end pipeline tests

Test scenarios:

- Full happy path: sync -> detect conflict -> resolve -> rebuild -> verify
- Hub returns 500 for patch-status (known conflict_files bug) -> graceful
  degradation
- Hub returns 409 on rebuild submission -> skip and report
- Hub connection timeout -> retry and fallback
- Upstream force-push -> sync with reset-to-upstream
- Patch detected as merged upstream -> informational reporting
- Multiple patches in conflict -> resolve in position order
- Rebuild timeout -> report and skip
- Budget exhaustion mid-resolution -> stop and report
- Concurrent fix-pipeline and carry-patch streams -> no interference

#### 5.2 Error handling hardening

**Complexity**: Medium

Files to modify:

- `packages/agentfox/agentfox/nightshift/carry_patch_pipeline.py` -- Add
  comprehensive error handling

Key error paths to harden:

- Hub API returns unexpected JSON schema (handle Pydantic validation errors)
- Git clone of workspace fails (auth error, network error)
- Push of resolution fails (branch was modified on hub between clone and push)
- Patch status changes between dashboard fetch and action (stale state)
- Hub git server returns 401 (credential expiry)

#### 5.3 Documentation

**Complexity**: Medium

Files to modify:

- `docs/config-reference.md` -- Document `[hub]` and `[carry_patch]` config
  sections
- `docs/architecture.md` -- Add carry-patch section

Files to create:

- `docs/carry-patch.md` -- User guide for carry-patch setup and operation

#### 5.4 Known limitations

Document the following known limitations:

1. **Squash-merge detection**: Hub's ancestry-based detection misses squash
   merges. Users must manually mark these patches as merged_upstream.
2. **`conflict_files` bug**: Hub's patch-status endpoint may return 500 due to
   missing schema column. Nightshift handles this gracefully but conflict file
   details may be unavailable.
3. **Single rebuild constraint**: Only one rebuild per workspace at a time.
   Nightshift will not submit if one is already running.
4. **Conflict resolution quality**: The AI agent may not resolve all conflicts
   correctly. Failed resolutions are logged and the patch remains in conflict
   status for manual intervention.
5. **No per-rebuild strategy override**: Rebuild strategy is workspace-level
   only.

---

## File Summary

### New package

| Package | Purpose | Files |
|---------|---------|-------|
| `packages/afhub/` | Hub API client | ~6 source files, ~5 test files |

### New files in existing packages

| File | Package | Purpose |
|------|---------|---------|
| `agentfox/nightshift/carry_patch_pipeline.py` | agentfox | Main pipeline |
| `agentfox/nightshift/carry_patch_resolver.py` | agentfox | Conflict resolution |
| `agentfox/ui/carry_patch.py` | agentfox | Rich formatters |
| `agentfox/_templates/profiles/coder_carry-patch.md` | agentfox | Agent profile |

### Modified files

| File | Change |
|------|--------|
| `agentfox/core/config.py` | Add HubConfig, CarryPatchConfig |
| `agentfox/archetypes.py` | Add carry-patch ModeConfig to coder |
| `agentfox/nightshift/engine.py` | Add `_run_carry_patch_cycle()` |
| `agentfox/nightshift/streams.py` | Register carry-patch stream |
| `agentfox/nightshift/daemon.py` | Display names for carry-patch stream |
| `agentfox/session/context.py` | Carry-patch artifact filtering (if needed) |
| `afaudit/events.py` | Add carry-patch audit event types |
| `nightshift/app.py` | Carry-patch CLI group, hub client init |
| `agentfox/pyproject.toml` | Add afhub dependency |
| `nightshift/pyproject.toml` | Add afhub dependency |
| `agentfox/core/config_gen.py` | Add hub/carry_patch to visible sections |

### Effort Estimates

| Phase | Complexity | Relative Size |
|-------|-----------|---------------|
| Phase 1: Foundation | Large | ~35% |
| Phase 2: Core logic | Large | ~30% |
| Phase 3: CLI | Medium | ~10% |
| Phase 4: Agent integration | Medium | ~15% |
| Phase 5: Testing/hardening | Large | ~10% (ongoing) |

Phases 1 and 3 can be partially parallelized (CLI subcommands only need the
HubClient from Phase 1, not the pipeline from Phase 2). Phase 4 depends on Phase
2 for the pipeline integration points but the profile template and archetype mode
can be created independently.
