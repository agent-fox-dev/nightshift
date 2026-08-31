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

- Rebuild and sync calls support a blocking mode (`?wait=true`) — the hub holds
  the connection open until the operation completes. Nightshift uses this mode
  by default to avoid client-side polling.
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
2. **Hub authentication**: No mechanism for hub tokens. Nightshift currently
   authenticates only via platform-specific env vars (GITHUB_PAT, etc.). The
   hub token will be accepted via `--token` CLI flag or `AF_HUB_TOKEN` env var
   and held in memory only.
3. ~~**Asynchronous job polling**~~: Resolved — the hub API/CLI is being updated
   to support blocking calls (e.g., `POST /rebuild?wait=true`), eliminating the
   need for client-side polling infrastructure.
4. **Carry-patch domain models**: No data structures for Workspace, Patch,
   RebuildJob, PatchResult, PatchStatusDashboard.
5. **Conflict monitoring stream**: A lightweight work stream is needed to poll
   hub for patches in conflict status after upstream advances. This does not
   duplicate the fix pipeline — it only detects conflicts and invokes the
   existing coder to resolve them.
6. **Patch registration in fix pipeline**: When in carry-patch mode, the fix
   pipeline skips local merge entirely. Instead of harvest/squash-merge, the
   integration phase registers the fix branch as a patch with hub and requests
   a rebuild. Hub owns the integration branch — nightshift does not touch it.
7. **Carry-patch profile templates**: No agent profile exists for carry-patch
   conflict resolution behavioral instructions.
8. **Carry-patch conflict resolution labels**: Only `af:fix`, `af:fixed`,
   `af:pr`, `af:no-change` exist. Carry-patch conflict resolution may need a
   label (e.g., `af:carry-conflict`) or may bypass the issue tracker entirely
   and invoke the coder directly from the conflict monitoring stream.

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

### Areas of Tension (Resolved)

1. ~~**Single-repo vs. multi-workspace**~~: Not an issue. Each nightshift
   instance works against exactly one workspace (`--workspace <slug>`). For
   multiple workspaces, run multiple instances.

2. ~~**Synchronous pipeline vs. asynchronous jobs**~~: Not an issue. The hub
   API/CLI is being updated to support blocking calls (`--wait` / `?wait=true`),
   so rebuild and sync operations can be called synchronously just like the
   existing fix pipeline.

3. **Local git vs. remote hub**: The fix pipeline operates on local git
   worktrees. Carry-patch operations (sync, rebuild, patch status) are remote API
   calls to hub. The "workspace" concept in nightshift (a local directory with a
   worktree) differs from the hub "workspace" (a server-side repository clone).
   Nightshift bridges this by cloning the hub workspace locally at bootstrap
   time (see [Bootstrapping](#bootstrapping)).

4. ~~**Budget accounting**~~: Not an issue. There is one shared budget for all
   work — fix pipeline and conflict resolution draw from the same pool. No
   partitioning or prioritization needed.

### Recommended Approach

A lightweight **conflict monitoring stream** plus **patch registration** in the
existing fix pipeline, backed by a **hub API client** (`HubClient`). This
approach:

- Reuses the fix pipeline for all coding work — no duplicate pipeline logic
- Adds only a small monitoring stream for conflict detection
- Uses the existing daemon framework for scheduling and lifecycle
- Adds hub as an optional dependency (nightshift still works without it)

---

## Bootstrapping

An operator sets up the hub workspace and provisions a token before nightshift
starts. Nightshift then bootstraps itself from just two CLI arguments.

### Prerequisites

1. **Hub workspace exists.** An operator has created a carry-patch workspace on
   the hub (via the hub UI or API), configured the upstream remote, and
   registered the initial set of patch branches.
2. **Hub token provisioned.** A token with at least `git:write` scope and the
   hub API permissions needed for sync, rebuild, and patch management (exact
   permission set TBD).

### Invocation

```
nightshift --workspace <slug> --token <token>
```

Both `--workspace` and `--token` are required to activate carry-patch mode.
When either is absent, nightshift falls back to its normal fix-pipeline
behavior (if configured) or exits with an error if no work mode is available.

### Startup Behavior

On startup, nightshift resolves the local working directory in one of two ways:

1. **CWD matches the workspace.** If the current working directory is already
   a git clone whose origin matches the hub workspace identified by `<slug>`,
   nightshift starts working from there immediately. It reads any existing
   `.nightshift/config.toml` in the CWD and merges in the `--workspace` and
   `--token` values.

2. **CWD does not match.** If nightshift is not inside a directory that matches
   the `--workspace` slug, it:
   1. Queries the hub API for the workspace's git URL
      (`GET /api/v1/workspaces/<slug>` → `git_url`)
   2. Clones the repository into a new local directory (named after the slug)
   3. Changes its working directory to the new clone
   4. Creates a default `.nightshift/config.toml` inside the clone, pre-populated
      with the hub endpoint, workspace slug, and carry-patch-specific defaults
   5. Begins the carry-patch work loop

### Token Handling

- The `--token` flag value is used for both hub API authentication
  (`Authorization: Bearer <token>`) and git operations against the hub's git
  server (HTTP basic auth with the token).
- The token is held in memory only — it is never written to `config.toml` or
  any file on disk.
- Alternatively, the token can be provided via the `AF_HUB_TOKEN` environment
  variable. The `--token` flag takes precedence if both are set.

### Config Generation

When nightshift creates a new clone and config, the generated
`.nightshift/config.toml` contains:

```toml
[hub]
endpoint_url = "<resolved from workspace API>"

[carry_patch]
enabled = true
workspaces = ["<slug>"]
check_interval = 300
auto_resolve = true

[workspace]
integration_branch = "deploy"
merge_strategy = "direct"
```

The operator can customize this config after the first run. Subsequent
invocations with the same `--workspace` flag reuse the existing config and
clone directory.

---

## Design Decisions (Resolve Before/During Implementation)

### DD-1: Hub as optional dependency

**Decision**: Hub integration is opt-in. Carry-patch mode activates only when
`--workspace` and `--token` are provided on the command line (or `AF_HUB_TOKEN`
is set). When neither is present, all carry-patch functionality is disabled. The
fix-pipeline and pr-feedback streams continue to work independently.

**Rationale**: Nightshift must remain functional without hub. Many users will
never use carry-patch. The CLI-flag approach makes it explicit — no config file
is needed to get started.

### DD-2: Workspace discovery mechanism

**Decision**: Nightshift operates on the single workspace specified by
`--workspace <slug>`. The slug is persisted in the local config's
`[carry_patch] workspaces` list after bootstrapping. Multiple workspaces can
be monitored by running multiple nightshift instances, each with its own
`--workspace` flag and working directory.

**Rationale**: A single-workspace-per-process model is simpler, avoids
cross-repo CWD management, and maps cleanly to one clone per workspace. For
multi-workspace scenarios, an operator runs multiple nightshift instances
(e.g., one systemd unit or container per workspace).

**Alternative considered**: Multi-workspace in a single process (list of slugs
in config). Rejected for initial implementation because it requires managing
multiple local clones and CWD switching within a single daemon. Can be
reconsidered later if demand warrants it.

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

### DD-4: Rebuild call strategy

**Decision**: Use the hub API's blocking mode (`?wait=true`) for rebuild and sync
calls. The hub holds the connection open until the operation completes or a
server-side timeout is reached. Nightshift sets a client-side timeout matching
`carry_patch.rebuild_timeout` (default 600 seconds).

**Rationale**: The hub is adding `--wait` support to avoid the complexity of
client-side polling. This keeps nightshift's execution model synchronous and
consistent with the fix pipeline.

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
model (token via CLI flag or env var), its own error types, and its own data
models. Placing it in `agentfox` would violate the existing package boundaries.

**Alternative considered**: Adding hub client to `agentfox/nightshift/`. Rejected
because the hub client is a general-purpose API layer, not nightshift-specific
logic.

### DD-7: Local clone and working directory

**Decision**: Nightshift works from a local clone of the hub workspace. At
bootstrap time, if the CWD is not already a matching clone, nightshift creates
one automatically (see [Bootstrapping](#bootstrapping)). Conflict resolution,
patch inspection, and all git operations run against this local clone. The clone
is kept up-to-date via `git fetch` at the start of each carry-patch cycle.

**Rationale**: Conflict resolution requires local file access for the coder
agent. Hub's git server at `/git/:org/:slug.git` provides authenticated access.
A persistent clone (rather than ephemeral worktrees) avoids re-cloning on every
cycle and gives the coder agent a full repository context.

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
- `packages/afhub/afhub/auth.py` -- Credential resolution (`--token` flag,
  `AF_HUB_TOKEN` env var)
- `packages/afhub/tests/` -- Test directory

**`HubClient` class** (`client.py`):

```python
class HubClient:
    """Async HTTP client for the af-hub API."""

    def __init__(self, endpoint_url: str, api_key: str) -> None: ...

    # Workspace operations
    async def get_workspace(self, slug: str) -> Workspace: ...
    async def create_workspace(self, **kwargs) -> Workspace: ...
    async def sync_workspace(self, slug: str, *, reset_to_upstream: bool = False, wait: bool = True) -> SyncResult: ...
    async def get_patch_status(self, slug: str) -> PatchStatusDashboard: ...

    # Patch operations
    async def list_patches(self, slug: str) -> list[Patch]: ...
    async def add_patch(self, slug: str, branch_name: str, **kwargs) -> Patch: ...
    async def update_patch(self, slug: str, patch_id: str, **kwargs) -> Patch: ...
    async def remove_patch(self, slug: str, patch_id: str) -> None: ...
    async def reorder_patches(self, slug: str, patch_ids: list[str]) -> None: ...

    # Rebuild operations
    async def submit_rebuild(self, slug: str, *, wait: bool = True) -> RebuildJob: ...
    async def get_rebuild(self, slug: str, rebuild_id: str) -> RebuildJob: ...
    async def list_rebuilds(self, slug: str) -> list[RebuildJob]: ...
    # Rerere operations
    async def list_rerere(self, slug: str) -> list[RerereEntry]: ...
    async def forget_rerere(self, slug: str, pathspec: str) -> None: ...

    # Variables
    async def get_variable(self, slug: str, key: str) -> str | None: ...
    async def set_variable(self, slug: str, key: str, value: str) -> None: ...
```

The `submit_rebuild()` method uses the hub's blocking mode (`?wait=true`) by
default, returning the completed rebuild job directly. The client-side timeout
is set to `carry_patch.rebuild_timeout` (default 600 seconds).

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
class HubConnectionError(HubError): ...
```

**Auth** (`auth.py`):

- `resolve_hub_token()` resolves the hub token from (in priority order):
  1. Explicit value passed from `--token` CLI flag
  2. `AF_HUB_TOKEN` environment variable
- Returns `None` if neither is set (carry-patch mode unavailable)
- The hub endpoint URL is resolved from `--workspace` via the hub API, or
  read from `[hub] endpoint_url` in the local config on subsequent runs

#### 1.2 Add hub configuration to nightshift

**Complexity**: Small

Files to modify:

- `packages/agentfox/agentfox/core/config.py` -- Add `HubConfig` and
  `CarryPatchConfig` models

```python
class HubConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    endpoint_url: str = Field(default="", description="Hub API endpoint URL")

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

---

### Phase 2: Core Carry-Patch Logic (Depends on Phase 1, ~25% of effort)

Carry-patch mode does not duplicate the fix pipeline. The existing fix pipeline
already creates fixes on branches from `af:fix` issues. In carry-patch mode,
the only differences are:

- Fix branches are **registered as patches** with hub after integration
- A lightweight **conflict monitoring stream** polls hub for patches that need
  conflict resolution after upstream advances
- Conflict resolution reuses the existing coder via `FixPipeline._run_coder_session()`

#### 2.1 Patch registration in fix pipeline

**Complexity**: Small

Files to modify:

- `packages/agentfox/agentfox/nightshift/fix_pipeline.py` -- Replace
  harvest/integration with patch registration in carry-patch mode

When `carry_patch.enabled` is true, after the fix branch is created and the
coder-reviewer loop completes, the fix pipeline **skips local merge entirely**
(no harvest, no squash-merge). Instead:

1. Push the fix branch to origin
2. Register the fix branch as a patch with hub
   (`hub_client.add_patch(slug, branch_name, ...)`)
3. Request a rebuild (`hub_client.submit_rebuild(slug, wait=True)`)
4. On success, proceed with normal issue closure

Hub owns the integration branch — nightshift never touches it. The existing
merge strategies (`direct`, `branch`, `pr`) are not used in carry-patch mode.

#### 2.2 Conflict monitoring stream

**Complexity**: Medium

Files to create:

- `packages/agentfox/agentfox/nightshift/carry_patch_monitor.py`

```python
class CarryPatchMonitor:
    """Lightweight conflict monitor for a hub workspace.

    Polls hub for patch-status on each cycle. When patches are in
    conflict status, invokes the existing coder to resolve them.
    """

    def __init__(
        self,
        hub_client: HubClient,
        workspace_slug: str,
        config: AgentFoxConfig,
    ) -> None: ...

    async def run_cycle(self) -> MonitorCycleResult:
        """Execute one monitoring cycle.

        Steps:
        1. Fetch patch-status dashboard from hub
        2. Log any newly merged patches (informational)
        3. For each patch in conflict status (if auto_resolve is enabled):
           a. Fetch upstream diff and conflict details
           b. Run coder:carry-patch session to resolve
           c. Push resolved branch back to hub
           d. Request rebuild
        4. Return cycle result with actions taken
        """
```

Key behaviors:

- **Idempotent cycles**: Fetches fresh state from hub on each cycle. No
  persistent local state beyond the working directory clone.
- **Reuses existing coder**: Conflict resolution invokes
  `FixPipeline._run_coder_session()` (or the underlying `run_session()`) with
  `archetype="coder"` and `mode="carry-patch"`. No duplicate pipeline logic.
- **Error handling**: Follows nightshift's fail-open pattern. A failed conflict
  resolution for one patch does not block attempts on other patches. Hub
  connection failures are logged and the cycle skips.

#### 2.3 Register conflict monitoring work stream

**Complexity**: Small

Files to modify:

- `packages/agentfox/agentfox/nightshift/streams.py` -- Add carry-patch stream
  to `build_streams()`
- `packages/agentfox/agentfox/nightshift/engine.py` -- Add
  `_run_carry_patch_monitor()` method that delegates to `CarryPatchMonitor`
- `packages/agentfox/agentfox/nightshift/daemon.py` -- Add `"carry-patch"` to
  `_STREAM_DISPLAY_NAMES` and `_STREAM_ACTIVE_LABELS`

#### 2.4 Hub client initialization in daemon startup

**Complexity**: Small

Files to modify:

- `packages/nightshift/nightshift/app.py` -- Initialize `HubClient` in
  `_run_daemon()` and pass to engine

```python
hub_client = None
if workspace_slug and hub_token:
    from afhub.client import HubClient
    hub_client = HubClient(config.hub.endpoint_url, hub_token)
elif config.carry_patch.enabled:
    logger.warning("Carry-patch enabled in config but --workspace/--token not provided; disabling")
```

#### 2.5 Audit events for carry-patch

**Complexity**: Small

Files to modify:

- `packages/afaudit/afaudit/events.py` -- Add new event types

```python
CARRY_PATCH_CONFLICT_DETECTED = "carry_patch_conflict_detected"
CARRY_PATCH_CONFLICT_RESOLVED = "carry_patch_conflict_resolved"
CARRY_PATCH_CONFLICT_FAILED = "carry_patch_conflict_failed"
CARRY_PATCH_PATCH_REGISTERED = "carry_patch_patch_registered"
CARRY_PATCH_REBUILD_REQUESTED = "carry_patch_rebuild_requested"
CARRY_PATCH_MERGED_DETECTED = "carry_patch_merged_detected"
```

#### 2.6 Tests for Phase 2

**Complexity**: Medium

Files to create:

- `packages/agentfox/tests/test_carry_patch_monitor.py` -- Monitor logic tests
  with mocked HubClient
- `packages/agentfox/tests/test_carry_patch_registration.py` -- Patch
  registration in fix pipeline
- `packages/agentfox/tests/test_carry_patch_stream.py` -- Stream registration
  and enablement

Key test scenarios:

- Monitor cycle with no conflicts (no-op)
- Monitor cycle with one patch in conflict -> resolution -> rebuild
- Monitor cycle with hub unreachable (graceful degradation)
- Monitor cycle with merged patches detected (informational)
- Auto-resolve disabled (skip resolution, only report)
- Fix pipeline registers patch after successful fix
- Fix pipeline skips registration when not in carry-patch mode

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

Conflict resolution reuses the existing coder session infrastructure. The new
pieces are a dedicated archetype mode and profile template that give the coder
agent the right context and instructions for carry-patch conflicts.

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

#### 4.3 Context construction for conflict resolution

**Complexity**: Small

The `CarryPatchMonitor` (Phase 2.2) builds context for the coder session:

- Patch description (why the patch exists)
- Conflict files and markers
- Upstream changes that caused the conflict (diff)
- Git rerere history if available (from hub API)
- Prior resolution attempts (from knowledge store)

This context is passed to `run_session()` with `archetype="coder"` and
`mode="carry-patch"`, reusing the same session infrastructure as the fix
pipeline.

Files to modify (if needed):

- `packages/agentfox/agentfox/session/context.py` -- Add carry-patch to
  `_ARCHETYPE_ARTIFACTS`

#### 4.4 Tests for Phase 4

**Complexity**: Small

Files to create:

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

- Full happy path: fix issue -> register patch -> rebuild (blocking) -> verify
- Conflict monitoring: detect conflict -> resolve -> rebuild
- Hub returns 500 for patch-status (known conflict_files bug) -> graceful
  degradation
- Hub returns 409 on rebuild submission -> skip and report
- Hub connection timeout -> retry and fallback
- Upstream force-push -> sync with reset-to-upstream
- Patch detected as merged upstream -> informational reporting
- Multiple patches in conflict -> resolve in position order
- Budget exhaustion mid-resolution -> stop and report

#### 5.2 Error handling hardening

**Complexity**: Medium

Files to modify:

- `packages/agentfox/agentfox/nightshift/carry_patch_monitor.py` -- Add
  comprehensive error handling
- `packages/agentfox/agentfox/nightshift/fix_pipeline.py` -- Harden patch
  registration error paths

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
| `agentfox/nightshift/carry_patch_monitor.py` | agentfox | Conflict monitoring stream |
| `agentfox/ui/carry_patch.py` | agentfox | Rich formatters |
| `agentfox/_templates/profiles/coder_carry-patch.md` | agentfox | Agent profile |

### Modified files

| File | Change |
|------|--------|
| `agentfox/core/config.py` | Add HubConfig, CarryPatchConfig |
| `agentfox/archetypes.py` | Add carry-patch ModeConfig to coder |
| `agentfox/nightshift/fix_pipeline.py` | Add patch registration step |
| `agentfox/nightshift/engine.py` | Add `_run_carry_patch_monitor()` |
| `agentfox/nightshift/streams.py` | Register carry-patch stream |
| `agentfox/nightshift/daemon.py` | Display names for carry-patch stream |
| `agentfox/session/context.py` | Carry-patch artifact filtering (if needed) |
| `afaudit/events.py` | Add carry-patch audit event types |
| `nightshift/app.py` | `--workspace`/`--token` flags, hub client init, bootstrap |
| `agentfox/pyproject.toml` | Add afhub dependency |
| `nightshift/pyproject.toml` | Add afhub dependency |
| `agentfox/core/config_gen.py` | Add hub/carry_patch to visible sections |

### Effort Estimates

| Phase | Complexity | Relative Size |
|-------|-----------|---------------|
| Phase 1: Foundation | Large | ~35% |
| Phase 2: Core logic | Medium | ~25% |
| Phase 3: CLI | Medium | ~10% |
| Phase 4: Agent integration | Small | ~10% |
| Phase 5: Testing/hardening | Medium | ~20% (ongoing) |

Phases 1 and 3 can be partially parallelized (CLI subcommands only need the
HubClient from Phase 1). Phase 4 (profile template and archetype mode) can
be created independently of Phase 2.
