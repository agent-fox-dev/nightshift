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

Nightshift communicates with the af-hub carry-patch API. All REST endpoints are
mounted under `/api/v1`. Git operations use the smart HTTP protocol at
`/git/:org/:slug.git`. Authentication for both is via
`Authorization: Bearer <credential>`.

### Authentication

The hub supports three credential types, all carried in the `Authorization:
Bearer` header:

| Credential | Format | Use Case |
|------------|--------|----------|
| API Key | `af_<key_id>_<secret>` | Full access to owner's resources; created via OAuth login |
| PAT | `af_pat_<token_id>_<secret>` | Scoped access; created via `POST /user/tokens` |
| Admin Token | `af_admin_<64-hex>` | Cross-tenant admin access; cannot create workspaces |

**Nightshift should use a PAT** with the following scopes:

| Scope | Grants |
|-------|--------|
| `workspaces:read` | GET workspace, GET patch-status, GET rerere list |
| `workspaces:sync` | POST sync, POST reclone (no ownership check) |
| `patches:write` | All patch CRUD (implies `patches:read`; no ownership check) |
| `rebuilds:write` | POST rebuild, DELETE cancel, POST requeue, POST rollback (implies `rebuilds:read`) |
| `git:write` | Push to hub git server (implies `git:read` for clone/fetch) |
| `vars:read` | Read workspace variables |
| `secrets:write` | Store upstream credentials |

Note: workspace CRUD endpoints return 404 (not 403) when a PAT lacks scope or
the workspace is not owned by the caller. Patch, rebuild, rerere, and variable
endpoints return 403 for missing scopes.

The PAT is resolved from (in priority order): `--token` CLI flag,
`AF_HUB_TOKEN` environment variable. The same credential authenticates both API
calls and git operations (as HTTP Basic password).

### Workspace Endpoints

| Method | Endpoint | Response | Purpose |
|--------|----------|----------|---------|
| POST | `/workspaces` | 201 | Create workspace (`workspace_mode: "carry_patch"`, `upstream_url`, `integration_branch`) |
| GET | `/workspaces` | 200 | List workspaces |
| GET | `/workspaces/:slug` | 200 | Get workspace details |
| PATCH | `/workspaces/:slug` | 200 | Update mutable workspace fields |
| POST | `/workspaces/:slug/sync` | 200 | Trigger upstream sync (carry-patch extended response) |
| POST | `/workspaces/:slug/reclone` | 200 | Nuclear recovery: delete clone, re-clone from scratch |
| GET | `/workspaces/:slug/patch-status` | 200 | Comprehensive dashboard: workspace state, per-patch status, rebuild summary |

Carry-patch fields set at creation are immutable: `workspace_mode`,
`upstream_url`, `integration_branch`, `slug`.

**Sync response** (carry-patch mode returns additional fields):

```json
{
  "patches_merged": ["feature/already-merged"],
  "rebuild_triggered": true,
  "rebuild_job_id": "d3b07384-...",
  "force_push_detected": false
}
```

- `patches_merged`: branch names of patches detected as merged upstream
- `rebuild_triggered`: whether a rebuild was auto-enqueued (controlled by
  `AUTO_REBUILD_AFTER_SYNC` variable)
- `rebuild_job_id`: UUID of the enqueued rebuild (present only when
  `rebuild_triggered` is true)
- `force_push_detected`: whether upstream HEAD is not a descendant of the
  stored upstream SHA

Sync accepts `?reset_to_upstream=true` to force-reset the local ref to upstream
HEAD regardless of ancestry (recovery from upstream force-pushes).

**Patch-status dashboard** response includes:

- Workspace metadata: `workspace_slug`, `workspace_mode`, `status`,
  `clone_status`, `clone_error`, `sync_status`, `sync_error`, `sync_mode`,
  `head_sha`, `git_url`, `upstream_url`, `upstream_head_sha`,
  `integration_branch`, `integration_head_sha`, `last_sync_at`
- `last_rebuild`: `{id, status}` or null if no rebuild attempted
- `patches[]`: each with `id`, `branch_name`, `position`, `status`,
  `last_rebuild_result`, `conflict_files`
- `summary`: `total_patches`, `active`, `merged_upstream`, `conflict`,
  `disabled`, `total_rerere_resolutions`

### Patch Endpoints

| Method | Endpoint | Response | Purpose |
|--------|----------|----------|---------|
| GET | `.../patches` | 200 | List patches ordered by position (excludes soft-deleted) |
| POST | `.../patches` | 201 | Add patch (single object or JSON array for batch) |
| PATCH | `.../patches/:id` | 200 | Update position, status, description, upstream_pr_url |
| DELETE | `.../patches/:id` | 204 | Soft-delete patch, auto-compact positions |
| POST | `.../patches/:id/restore` | 200 | Restore soft-deleted patch (active status, appended position) |
| POST | `.../patches/reorder` | 200 | Full reorder via `{"patch_ids": [...]}` (must include all IDs) |

**Add-patch request fields:**

| Field | Required | Type | Notes |
|-------|----------|------|-------|
| `branch_name` | yes | string | Must not equal integration_branch; must not already exist in patch list |
| `position` | no | integer | >= 1; values beyond max clamped to append; omit to append |
| `upstream_pr_url` | no | string | Used for squash-merge detection via PR-number scanning |
| `description` | no | string | Free-form |
| `skip_branch_check` | no | boolean | Skip git branch existence validation (default false) |
| `if_not_exists` | no | boolean | Return existing record (200) instead of 409 if duplicate (default false) |

**Patch statuses:** `active`, `merged_upstream`, `conflict`, `disabled`,
`deleted`

**Soft-delete lifecycle:**
1. Sync detects patch merged upstream -> status transitions to `merged_upstream`
2. Successful rebuild -> `merged_upstream` patches soft-deleted (`deleted`,
   `deleted_at` set)
3. Restorable via POST `.../patches/:id/restore` within 7 days
4. After 7 days, permanently purged from database

### Rebuild Endpoints

| Method | Endpoint | Response | Purpose |
|--------|----------|----------|---------|
| POST | `.../rebuild` | 202 | Enqueue rebuild job |
| GET | `.../rebuilds` | 200 | List rebuild jobs (`{"jobs": [...]}`) |
| GET | `.../rebuilds/:id` | 200 | Get rebuild with per-patch progress |
| DELETE | `.../rebuilds/:id` | 200 | Cancel queued job (only `queued` status) |
| POST | `.../rebuilds/:id/requeue` | 200 | Requeue `dead_letter` job |
| POST | `.../rebuilds/:id/rollback` | 200 | Reset integration branch to `previous_integration_head_sha` |
| GET | `.../rebuild-preview` | 200 | Dry-run conflict prediction via `git merge-tree` |

**Rebuild submit** accepts an optional request body:

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `strategy` | string | `REBUILD_STRATEGY` variable or `"rebase"` | `"rebase"` or `"merge"` |
| `fail_mode` | string | `REBUILD_FAIL_MODE` variable or `"fail_fast"` | `"fail_fast"` or `"continue"` |

Preconditions: carry_patch mode, active workspace, clone ready, at least one
patch with `active` or `conflict` status. Only one rebuild can be queued or
running per workspace; concurrent submission returns 409 with
`error_type: "concurrent_rebuild"`.

**Rebuild job statuses:** `queued`, `running`, `completed`, `failed`,
`dead_letter`, `cancelled`

**Rebuild job response** (fields with `omitempty` omitted when empty):

```json
{
  "id": "<uuid>",
  "status": "completed",
  "strategy": "rebase",
  "error": "",
  "created_at": "<rfc3339>",
  "completed_at": "<rfc3339>",
  "patch_results": [...],
  "integration_head_sha": "<sha>",
  "previous_integration_head_sha": "<sha>"
}
```

**Patch result** within rebuild:

```json
{
  "patch_id": "<uuid>",
  "branch_name": "feature/foo",
  "position": 1,
  "status": "success",
  "skipped_reason": "merged_upstream",
  "new_head_sha": "<sha>",
  "conflict_files": ["file.go"]
}
```

- Patch result statuses: `success`, `conflict`, `skipped`
- Skipped reasons: `merged_upstream`, `disabled`, `deleted`, `branch_not_found`

**Rebuild preview** returns predicted outcomes without modifying state:

```json
{
  "patch_results": [
    {"patch_id": "<uuid>", "branch_name": "feature/foo", "position": 1,
     "status": "would_succeed", "tree_sha": "<sha>", "conflict_files": []},
    {"patch_id": "<uuid>", "branch_name": "feature/bar", "position": 2,
     "status": "would_conflict", "conflict_files": ["src/main.go"]}
  ]
}
```

### Rerere Endpoints

| Method | Endpoint | Response | Purpose |
|--------|----------|----------|---------|
| GET | `.../rerere` | 200 | List recorded conflict resolutions (`{resolutions: [{path, recorded_at}]}`) |
| DELETE | `.../rerere/*pathspec` | 204 | Forget a resolution (`git rerere forget <pathspec>`) |

### Secrets and Variables

| Method | Endpoint | Response | Purpose |
|--------|----------|----------|---------|
| POST | `.../secrets` | 201 | Store workspace-scoped secrets |
| GET | `.../vars` | 200 | List workspace variables |
| POST | `.../vars` | 201 | Create workspace variable |
| PATCH | `.../vars/:key` | 200 | Update variable |
| DELETE | `.../vars/:key` | 204 | Delete variable |
| GET | `.../vars/resolved` | 200 | Get effective variables (workspace > org > user resolution) |

**Upstream credentials** (stored as secrets):

| Key | Purpose |
|-----|---------|
| `UPSTREAM_GIT_PAT` | PAT for upstream remote |
| `UPSTREAM_GIT_USERNAME` | Username for HTTP basic auth |
| `UPSTREAM_GIT_PASSWORD` | Password for HTTP basic auth |

Resolution: `UPSTREAM_GIT_PAT` > `UPSTREAM_GIT_USERNAME`+`PASSWORD` > fallback
to origin credentials.

### Workspace Variables Controlling Carry-Patch

These variables govern automated behavior. Set via `POST .../vars` or the CLI.
Resolution order: workspace > org > user.

| Variable | Values | Default | Description |
|----------|--------|---------|-------------|
| `REBUILD_STRATEGY` | `rebase`, `merge` | `rebase` | Patch application strategy; overridable per-rebuild via request body |
| `REBUILD_FAIL_MODE` | `fail_fast`, `continue` | `fail_fast` | `fail_fast` aborts on first conflict; `continue` skips conflicting patches |
| `AUTO_REBUILD_AFTER_SYNC` | `true`, `false` | `true` | Auto-enqueue rebuild after sync detects upstream advancement |
| `AUTO_REBUILD_AFTER_PUSH` | `true`, `false` | `true` | Auto-enqueue rebuild when push to a registered patch branch is received |
| `SQUASH_MERGE_DETECTION` | `ancestry_only`, `content_based`, `both` | `both` | Merge detection strategies during sync (ancestry, git cherry + PR-number scanning, or both) |

### Git Server

The hub exposes a smart HTTP git server at `/git/:org/:slug.git` for
clone, fetch, and push. Requires `git:read` (clone/fetch) or `git:write`
(push) PAT scope.

**Auto-rebuild on push:** After a successful push, if any pushed branch matches
a registered patch and `AUTO_REBUILD_AFTER_PUSH` is not `"false"`, the hub
automatically enqueues a rebuild (with duplicate suppression).

### Key Protocol Details

- **No server-side blocking mode.** The hub API does not support `?wait=true`
  or any long-polling mechanism. The CLI's `--wait` flag is implemented as
  client-side polling (default: 5-second interval, 5-minute timeout).
  **Nightshift must implement its own polling loops** for rebuild and sync
  completion.
- **Rebuild is asynchronous.** `POST .../rebuild` returns 202 Accepted with the
  job record. Poll `GET .../rebuilds/:id` until status reaches a terminal state
  (`completed`, `failed`, `dead_letter`, `cancelled`).
- **Concurrency constraint.** Only one rebuild can be queued or running per
  workspace at a time. Concurrent submission returns 409 with
  `error_type: "concurrent_rebuild"`.
- **Error envelope.** All errors use:
  `{"error": {"code": <int>, "message": "<text>", "error_type": "<slug>"}}`
  where `error_type` is present only for machine-actionable conditions
  (`workspace_mode_mismatch`, `no_active_patches`, `concurrent_rebuild`,
  `duplicate_merge`).
- **Anti-enumeration.** Workspace CRUD endpoints return 404 (not 403) when a PAT
  lacks scope or the workspace is not owned by the caller.
- **Immutable fields.** `workspace_mode`, `upstream_url`,
  `integration_branch`, and `slug` cannot be changed after creation.
- **`omitempty` fields.** Several response fields (`strategy`, `error`,
  `patch_results`, `integration_head_sha`, `previous_integration_head_sha`,
  `clone_error`, `sync_error`) are omitted entirely when empty rather than
  returned as null.

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
`packages/afcore/afcore/nightshift/streams.py`. Each stream wraps an engine
method via `EngineWorkStream`, which delegates to `NightShiftEngine` methods.

### Fix Pipeline Flow

The `FixPipeline` in `packages/afcore/afcore/nightshift/fix_pipeline.py`
orchestrates per-issue processing:

1. Build `InMemorySpec` from issue
2. Create isolated git worktree
3. Run triage session (maintainer:fix-triage archetype)
4. Coder-reviewer loop with retry/escalation
5. Auto-commit pending changes
6. Integrate fix via one of three merge strategies (direct/branch/pr)
7. Handle result (close issue, add labels)

### Git Operations Layer

The workspace package (`packages/afcore/afcore/workspace/`) provides:

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

1. **WorkStream protocol**: The daemon framework (`DaemonRunner` +
   `EngineWorkStream`) directly supports registering new streams via
   `build_streams()`. A carry-patch stream slots in alongside `fix-pipeline`
   and `pr-feedback` with no framework changes. Priority ordering, budget
   tracking, and graceful shutdown all apply as-is.
2. **Archetype/mode system**: New modes (e.g., `coder:carry-patch`) can be added
   to the registry without schema changes. The 3-tier resolution system
   (mode TOML > archetype TOML > registry default) provides user
   customizability for conflict resolution behavior.
3. **Async git wrappers**: `run_git()`, `validate_ref_name()`,
   `create_branch()`, `checkout_branch()`, `fetch_remote()`,
   `push_to_remote()`, and `auto_commit_worktree()` all work for carry-patch
   operations. The timeout routing (60s local, 120s remote) and
   `GIT_TERMINAL_PROMPT=0` are correct defaults for hub git operations.
4. **HTTP retry infrastructure**: `request_with_retry()` in `afissues/_http.py`
   provides the async retry pattern with exponential backoff for transient
   errors (`ConnectTimeout`, `ConnectError`, `ReadTimeout`). This pattern will
   be replicated in the hub client (not directly importable due to package
   boundaries, but the logic is straightforward).
5. **Merge lock**: `MergeLock` serializes concurrent integration branch
   mutations across asyncio tasks and OS processes. In carry-patch mode, hub
   owns the integration branch, so `MergeLock` protects local worktree
   operations rather than the integration branch itself.
6. **Merge agent**: AI-driven conflict resolution via
   `run_merge_agent(worktree_path, conflict_output, model_id)` already exists
   and can be invoked for carry-patch conflicts.
7. **Knowledge system**: Session outcomes, prior attempts, and knowledge
   retrieval all apply to carry-patch conflict resolution sessions.
8. **Audit events**: The `AuditEventType` enum, `emit_audit_event()` helper,
   and `SinkDispatcher` infrastructure need only new event type entries -- no
   structural changes.
9. **Config system**: Pydantic models with `ConfigDict(extra="ignore")` mean
   new config sections (`HubConfig`, `CarryPatchConfig`) are
   backward-compatible. Existing config files without these sections load
   cleanly with defaults.
10. **Worktree lifecycle**: `create_worktree()` and `destroy_worktree()` provide
    isolated working directories for conflict resolution, including stale
    cleanup and ref conflict handling.

### What nightshift is missing

1. **Hub API client**: No HTTP client exists for communicating with af-hub. The
   `afissues` HTTP layer talks to GitHub/GitLab/Gitea APIs, not to hub. A new
   `HubClient` class is needed that covers the full carry-patch API surface:
   workspace operations (sync, patch-status, reclone), patch CRUD (add, update,
   delete, restore, reorder -- including batch add), rebuild lifecycle (submit,
   list, get, cancel, requeue, rollback, preview), rerere management, workspace
   variables, and upstream credential storage. The client must handle the hub's
   error envelope format (`{"error": {"code": N, "message": "...",
   "error_type": "..."}}`) and map machine-readable `error_type` values
   (`workspace_mode_mismatch`, `no_active_patches`, `concurrent_rebuild`,
   `duplicate_merge`) to typed exceptions.

2. **Hub authentication with PAT scopes**: Nightshift currently authenticates
   only via platform-specific env vars (`GITHUB_PAT`, etc.). The hub uses a
   Personal Access Token (PAT) system with granular scopes. The `--token` CLI
   flag (or `AF_HUB_TOKEN` env var) provides the PAT value, which is used as
   `Authorization: Bearer <token>` for API calls and as HTTP Basic password for
   git operations against the hub git server. The PAT must be provisioned with
   the following scopes:

   | Scope | Required for |
   |-------|-------------|
   | `workspaces:read` | `GET /patch-status`, `GET /rerere` |
   | `workspaces:sync` | `POST /sync`, `POST /reclone` |
   | `patches:read` | `GET /patches` |
   | `patches:write` | `POST /patches`, `PATCH /patches/:id`, `DELETE /patches/:id`, `POST /patches/:id/restore`, `POST /patches/reorder` |
   | `rebuilds:read` | `GET /rebuilds`, `GET /rebuilds/:id`, `GET /rebuild-preview` |
   | `rebuilds:write` | `POST /rebuild`, `DELETE /rebuilds/:id`, `POST /rebuilds/:id/requeue`, `POST /rebuilds/:id/rollback` |
   | `git:read` | git fetch from hub |
   | `git:write` | git push to hub |

   Note: `patches:write` implies `patches:read`, `git:write` implies
   `git:read`, and `workspaces:sync` does not imply `workspaces:read` (both
   are needed). The hub enforces an anti-enumeration policy where unauthorized
   workspace access returns 404 (not 403), which the client must account for
   in error handling.

3. **Client-side polling for rebuild completion**: The hub API does not support
   a blocking `?wait=true` query parameter on any endpoint. The hub CLI
   implements `--wait` via client-side polling: it calls `GET /rebuilds/:id`
   repeatedly until the job reaches a terminal status (`completed`, `failed`,
   `dead_letter`, `cancelled`). Nightshift must implement the same polling
   pattern in `HubClient` for rebuild and sync operations. This requires:
   - A poll loop with configurable interval (default 5s) and timeout (default
     5m)
   - Terminal status detection across all six job statuses: `queued`,
     `running`, `completed`, `failed`, `dead_letter`, `cancelled`
   - Intermediate progress reporting (running jobs expose per-patch progress
     via `patch_results` in the GET response)
   - Timeout handling that does not cancel the server-side job (the rebuild
     continues regardless of whether the client is polling)

4. **Carry-patch domain models**: No data structures exist for the hub's
   response schemas. The models must cover the full API surface:

   - **Workspace**: `slug`, `git_url`, `upstream_url`, `integration_branch`,
     `workspace_mode`, `clone_status`, `clone_error`, `sync_status`,
     `sync_error`, `sync_mode`, `head_sha`, `upstream_head_sha`,
     `last_sync_at`, `status`, `owner_id`, plus immutability constraints
     (workspace_mode, upstream_url, integration_branch cannot change after
     creation)
   - **Patch**: `id`, `workspace_slug`, `branch_name`, `position`, `status`
     (five values: `active`, `merged_upstream`, `conflict`, `disabled`,
     `deleted`), `conflict_files`, `upstream_pr_url`, `description`,
     `deleted_at`, `added_at`, `updated_at`
   - **RebuildJob**: `id`, `status` (six values: `queued`, `running`,
     `completed`, `failed`, `dead_letter`, `cancelled`), `strategy`,
     `error`, `created_at`, `completed_at`, `patch_results`,
     `integration_head_sha`, `previous_integration_head_sha` (several fields
     use omitempty -- omitted rather than null when empty)
   - **PatchResult**: `patch_id`, `branch_name`, `position`, `status`
     (`success`, `conflict`, `skipped`), `skipped_reason`
     (`merged_upstream`, `disabled`, `deleted`, `branch_not_found`),
     `new_head_sha`, `conflict_files`
   - **SyncResult**: `patches_merged`, `rebuild_triggered`, `rebuild_job_id`,
     `force_push_detected`
   - **PatchStatusDashboard**: `workspace_slug`, `workspace_mode`, `status`,
     `clone_status`, `clone_error`, `sync_status`, `sync_error`, `sync_mode`,
     `head_sha`, `git_url`, `upstream_url`, `upstream_head_sha`,
     `integration_branch`, `integration_head_sha`, `last_sync_at`,
     `last_rebuild` (nullable rebuild summary), `patches` (array with
     per-patch `last_rebuild_result` and `conflict_files`), `summary`
     (total_patches, active, merged_upstream, conflict, disabled,
     total_rerere_resolutions)
   - **RebuildPreview**: per-patch `would_succeed` / `would_conflict` results
     with tree SHAs and conflict file lists

5. **Workspace variables integration**: The hub exposes five workspace variables
   that control carry-patch behavior: `REBUILD_STRATEGY`, `REBUILD_FAIL_MODE`,
   `AUTO_REBUILD_AFTER_SYNC`, `AUTO_REBUILD_AFTER_PUSH`, and
   `SQUASH_MERGE_DETECTION`. Nightshift must set
   `AUTO_REBUILD_AFTER_SYNC=false` on workspaces it manages, because nightshift
   controls its own rebuild timing (it needs to inspect sync results, detect
   conflicts, attempt resolution, and then trigger a rebuild). Leaving the
   default (`true`) would cause the hub to auto-enqueue a rebuild immediately
   after sync, racing with nightshift's conflict resolution logic. Similarly,
   nightshift should be aware that `AUTO_REBUILD_AFTER_PUSH=true` (the default)
   means pushing a resolved patch branch will auto-trigger a rebuild on the hub
   side, which may be desirable or may conflict with nightshift's explicit
   rebuild submission.

6. **Rebuild preview for conflict prediction**: The hub provides a read-only
   conflict prediction endpoint (`GET /rebuild-preview`) that uses
   `git merge-tree --write-tree` to predict which patches would succeed or
   conflict without actually running a rebuild. Nightshift should call this
   before committing to a full rebuild cycle, especially after conflict
   resolution, to verify the fix before submitting a rebuild. Preview results
   use statuses `would_succeed` and `would_conflict` with conflict file lists.

7. **Conflict monitoring stream**: A lightweight work stream is needed to poll
   hub for patches in conflict status after upstream advances. This does not
   duplicate the fix pipeline -- it only detects conflicts and invokes the
   existing coder to resolve them.

8. **Patch registration in fix pipeline**: When in carry-patch mode, the fix
   pipeline skips local merge entirely. Instead of harvest/squash-merge, the
   integration phase registers the fix branch as a patch with hub and requests
   a rebuild. Hub owns the integration branch -- nightshift does not touch it.
   The patch add endpoint supports `if_not_exists=true` for idempotent
   registration and `skip_branch_check=true` if the branch has not yet been
   pushed.

9. **Carry-patch profile templates**: No agent profile exists for carry-patch
   conflict resolution behavioral instructions.

10. **Carry-patch conflict resolution labels**: Only `af:fix`, `af:fixed`,
    `af:pr`, `af:no-change` exist. Carry-patch conflict resolution may need a
    label (e.g., `af:carry-conflict`) or may bypass the issue tracker entirely
    and invoke the coder directly from the conflict monitoring stream.

11. **Soft-delete lifecycle awareness**: Hub soft-deletes patches that are
    detected as merged upstream (after a successful rebuild). These patches
    can be restored via `POST /patches/:id/restore` within 7 days, after which
    they are permanently purged. Nightshift should understand this lifecycle to
    avoid attempting operations on soft-deleted patches and to support restore
    if a merge detection was incorrect.

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
- **API schema evolution**: Hub API fields may evolve between versions (e.g.,
  new fields added, `omitempty` behavior changes). The `extra="ignore"` model
  config and optional field defaults handle forward-compatible changes
  gracefully, but nightshift must still be tested against the hub version it
  targets.
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
   API is fully asynchronous (rebuild and sync operations return immediately
   with a job ID), but nightshift implements client-side polling to await
   completion, matching the `afc` CLI's `--wait` pattern. This integrates
   naturally with the existing fix pipeline's sequential processing model.

3. **Local git vs. remote hub**: The fix pipeline operates on local git
   worktrees. Carry-patch operations (sync, rebuild, patch status) are remote API
   calls to hub. The "workspace" concept in nightshift (a local directory with a
   worktree) differs from the hub "workspace" (a server-side repository clone).
   Nightshift requires the operator to provide a local clone of the hub
   workspace. At startup, nightshift validates the CWD against the hub
   workspace metadata and exits with an error if they do not match (see
   [Bootstrapping](#bootstrapping)).

4. ~~**Budget accounting**~~: Not an issue. There is one shared budget for all
   work -- fix pipeline and conflict resolution draw from the same pool. No
   partitioning or prioritization needed.

### Recommended Approach

A lightweight **conflict monitoring stream** plus **patch registration** in the
existing fix pipeline, backed by a **hub API client** (`HubClient`). This
approach:

- Reuses the fix pipeline for all coding work -- no duplicate pipeline logic
- Adds only a small monitoring stream for conflict detection
- Uses the existing daemon framework for scheduling and lifecycle
- Adds hub as an optional dependency (nightshift still works without it)

---

## Bootstrapping

An operator sets up the hub workspace, provisions a Personal Access Token
(PAT), and clones the workspace repository locally before nightshift starts.
Nightshift validates its environment using two CLI arguments and the hub's
REST API -- it does not create infrastructure on its own.

### Prerequisites

1. **Hub workspace exists.** An operator has created a carry-patch workspace on
   the hub (via the hub UI, the `afc` CLI, or the `POST /api/v1/workspaces`
   endpoint), configured the upstream remote and credentials, and registered
   the initial set of patch branches.

2. **Hub PAT provisioned.** A Personal Access Token created via
   `afc tokens create` (or `POST /user/tokens`) with the following scopes:

   | Scope | Purpose |
   |-------|---------|
   | `workspaces:read` | Fetch workspace metadata and patch-status dashboard |
   | `workspaces:sync` | Trigger upstream sync |
   | `patches:read` | List patches |
   | `patches:write` | Register fix branches as patches, update status, reorder |
   | `rebuilds:read` | Poll rebuild job status until completion |
   | `rebuilds:write` | Submit, cancel, requeue, and rollback rebuilds |
   | `git:read` | Clone and fetch from the hub git server |
   | `git:write` | Push conflict resolutions and fix branches |

   The PAT format is `af_pat_<token_id>_<secret>`. Unlike API keys, PATs are
   restricted to their granted scopes and cannot escalate privileges. This is
   the recommended credential type for unattended automation.

3. **Local clone exists.** The operator has cloned the hub workspace into the
   directory where nightshift will run, using the hub git server URL (obtained
   from the workspace metadata via `afc workspace show` or the hub UI). The
   clone must use the hub's git server as its origin remote:
   ```
   git clone https://_:<pat>@hub.example.com/git/<org>/<slug>.git
   cd <slug>
   ```
   Nightshift must be invoked from the root of this clone. It will not create
   or clone repositories on its own.

### Invocation

```
nightshift --hub-url <url> --workspace <slug> --token <pat>
```

All three flags are required on first start (before a
`.nightshift/config.toml` exists). On subsequent starts, each value is
resolved from multiple sources in priority order:

| Value | Resolution order |
|-------|-----------------|
| Hub URL | `--hub-url` flag > `AF_HUB_URL` env var > `[hub] endpoint_url` in config |
| Workspace slug | `--workspace` flag > `[carry_patch] workspace` in config |
| PAT | `--token` flag > `AF_HUB_TOKEN` env var |

After the first successful start, the hub URL and workspace slug are
persisted in `.nightshift/config.toml`, so only the PAT (via `--token` or
`AF_HUB_TOKEN`) is needed on subsequent invocations. The PAT is never
written to disk.

The operator must invoke nightshift from within a git clone of the hub
workspace (see prerequisite 3). When no workspace slug and no PAT can be
resolved from any source, nightshift falls back to its normal fix-pipeline
behavior (if configured) or exits with an error if no work mode is available.

### Startup Behavior

On startup, nightshift resolves the hub endpoint URL from (in priority order):
the `--hub-url` CLI flag, the `AF_HUB_URL` environment variable, or the
`[hub] endpoint_url` field in an existing `.nightshift/config.toml`. If none
of these is available, nightshift exits with an error explaining that a hub
URL is required on first start.

It then validates the local working directory against the hub workspace
metadata:

1. Fetches workspace metadata via `GET /api/v1/workspaces/<slug>`
   (authenticated with `Authorization: Bearer <pat>`). The response provides
   `git_url`, `upstream_url`, `integration_branch`, `workspace_mode`, and
   `clone_status`.
2. Verifies that `workspace_mode` is `"carry_patch"` and `clone_status` is
   `"ready"`. If either check fails, nightshift logs a diagnostic error and
   exits.
3. Reads the local repository's origin remote URL (e.g., via
   `git remote get-url origin`). If the CWD is not a git repository,
   nightshift exits with an error explaining that it must be run from within a
   clone of the hub workspace.
4. Compares the workspace `git_url` from the API response with the local
   origin remote URL. If they match, nightshift proceeds -- it reads any
   existing `.nightshift/config.toml` in the CWD and merges in the
   `--workspace` and `--token` values.
5. If the URLs do not match, nightshift exits with an error explaining the
   mismatch (showing the expected `git_url` and the actual local origin URL)
   and telling the operator to `cd` into the correct directory or clone the
   workspace manually first. Nightshift does not create clones or switch
   directories on its own.

### Token Handling

- The `--token` flag accepts a hub PAT (`af_pat_...`). The PAT is used for
  both hub API authentication (`Authorization: Bearer <pat>`) and git
  operations against the hub's git server (HTTP Basic auth with the PAT as
  the password).
- The PAT is held in memory only -- it is never written to `config.toml` or
  any file on disk.
- Alternatively, the PAT can be provided via the `AF_HUB_TOKEN` environment
  variable. The `--token` flag takes precedence if both are set.
- Nightshift validates the PAT's scopes at startup by making a test API call
  (`GET /api/v1/workspaces/<slug>`). If the call returns 401 or 404 (the
  hub's anti-enumeration policy returns 404 for insufficient scope),
  nightshift logs the error and exits.

### Config Generation

When nightshift starts in a matching workspace directory that does not yet
have a `.nightshift/config.toml`, it generates a default config using values
fetched from the hub workspace metadata:

```toml
[hub]
endpoint_url = "<resolved from workspace API response>"

[carry_patch]
enabled = true
workspace = "<slug>"
check_interval = 300
auto_resolve = true

[workspace]
integration_branch = "<from workspace API: integration_branch>"
merge_strategy = "direct"
```

The operator can customize this config after the first run or create it
manually before starting nightshift. Subsequent invocations with the same
`--workspace` flag reuse the existing config.

### Workspace Variable Setup

On first startup in a workspace, nightshift sets two workspace variables via
the hub API to prevent the hub from triggering rebuilds autonomously.
Nightshift controls rebuild timing itself -- it needs to observe rebuild
results, coordinate with conflict resolution, and track outcomes for audit
logging.

```
POST /api/v1/workspaces/<slug>/vars
{"key": "AUTO_REBUILD_AFTER_SYNC", "value": "false"}

POST /api/v1/workspaces/<slug>/vars
{"key": "AUTO_REBUILD_AFTER_PUSH", "value": "false"}
```

| Variable | Value | Reason |
|----------|-------|--------|
| `AUTO_REBUILD_AFTER_SYNC` | `"false"` | Nightshift triggers rebuilds explicitly after sync so it can poll for completion and act on the result (resolve conflicts, log outcomes). |
| `AUTO_REBUILD_AFTER_PUSH` | `"false"` | Nightshift triggers rebuilds explicitly after pushing fix branches so it can track whether the rebuild succeeds or produces new conflicts. |

These variables are set once during the first successful startup. Nightshift
does not reset them on subsequent startups (the hub persists them). If an
operator wants hub-side auto-rebuild for manual workflows alongside nightshift,
they can
override these variables and nightshift will still function correctly -- it
handles 409 (concurrent rebuild) gracefully by polling the existing rebuild
instead of submitting a new one.

---

## Design Decisions (Resolve Before/During Implementation)

### DD-1: Hub as optional dependency

**Decision**: Hub integration is opt-in. Carry-patch mode activates only when
`--workspace <slug>` and `--token <PAT>` are provided on the command line (or
`AF_HUB_TOKEN` is set). The token must be a Personal Access Token (PAT) with
the scopes required for carry-patch operations (at minimum: `workspaces:read`,
`patches:read`, `patches:write`, `rebuilds:read`, `rebuilds:write`, `git:read`,
`git:write`). When neither flag is present, all carry-patch functionality is
disabled. The fix-pipeline and pr-feedback streams continue to work
independently.

**Rationale**: Nightshift must remain functional without hub. Many users will
never use carry-patch. The CLI-flag approach makes it explicit -- no config file
is needed to get started. Using a scoped PAT rather than an API key follows the
principle of least privilege; nightshift never needs admin access.

### DD-2: Workspace discovery mechanism

**Decision**: Nightshift operates on the single workspace specified by
`--workspace <slug>`. At startup, nightshift fetches workspace metadata via
`GET /api/v1/workspaces/<slug>` to confirm the workspace exists, is in
`carry_patch` mode, and has `clone_status: ready`. It then verifies that the
CWD's origin remote URL matches the workspace's `git_url`. The slug is
persisted in the local config's `[carry_patch] workspaces` list after initial
configuration. Multiple workspaces can be monitored by running multiple
nightshift instances, each with its own `--workspace` flag and working
directory.

**Rationale**: A single-workspace-per-process model is simpler, avoids
cross-repo CWD management, and maps cleanly to one working directory per
workspace. Each nightshift instance operates in a single operator-provided
clone. For multi-workspace scenarios, an operator runs multiple nightshift
instances (e.g., one systemd unit or container per workspace).

**Alternative considered**: Multi-workspace in a single process (list of slugs
in config). Rejected for initial implementation because it requires operating
across multiple local working directories with CWD switching within a single
daemon. Can be reconsidered later if demand warrants it.

### DD-3: Conflict resolution strategy

**Decision**: When a rebuild fails with a conflict, nightshift:

1. Optionally runs a rebuild preview (`GET /api/v1/workspaces/<slug>/rebuild-preview`)
   before attempting the rebuild to predict which patches will conflict, using
   `git merge-tree --write-tree` without side effects.
2. After a failed rebuild, reads conflict details from the rebuild job result
   (`GET /api/v1/workspaces/<slug>/rebuilds/<id>`), which reports per-patch
   `conflict_files` arrays. The same information is available from the
   patch-status dashboard (`GET /api/v1/workspaces/<slug>/patch-status`).
3. Checks out the conflicting patch branch locally (via the hub git server clone).
4. Applies the upstream changes that cause the conflict and runs a
   coder:carry-patch session to resolve it.
5. Pushes the resolved branch back to the hub's git server
   (`POST /git/<org>/<slug>.git/git-receive-pack`). If `AUTO_REBUILD_AFTER_PUSH`
   is enabled (the default), the hub's post-push hook automatically enqueues a
   new rebuild. Otherwise, nightshift submits one explicitly via
   `POST /api/v1/workspaces/<slug>/rebuild`.
6. Queries recorded rerere resolutions via `GET /api/v1/workspaces/<slug>/rerere`
   to understand which conflicts the hub can auto-resolve on future rebuilds.
   Rerere is managed server-side by the hub; nightshift does not manage it
   directly, but can delete stale resolutions via
   `DELETE /api/v1/workspaces/<slug>/rerere/<pathspec>` if needed.

**Rationale**: This leverages the existing merge agent pattern but with a
carry-patch-specific profile that understands the upstream-vs-patch context. The
rebuild preview step allows nightshift to skip rebuilds that would fail with the
same conflicts, avoiding wasted work. The auto-rebuild-on-push hook means
nightshift does not always need to explicitly trigger a rebuild after fixing a
conflict.

**Alternative considered**: Only report conflicts and wait for human resolution.
Rejected as it defeats the purpose of nightshift automation, though a
`carry_patch.auto_resolve = false` config option should exist as an escape hatch.

### DD-4: Rebuild call strategy

**Decision**: Nightshift implements client-side polling for rebuild and sync
operations. The hub API does not support server-side blocking; all long-running
operations return immediately with a job ID.

The polling pattern for rebuilds:

1. `POST /api/v1/workspaces/<slug>/rebuild` returns 202 with a job record
   containing the job `id` and `status: "queued"`.
2. Nightshift polls `GET /api/v1/workspaces/<slug>/rebuilds/<id>` at a
   configurable interval (default 5 seconds) until the job reaches a terminal
   status: `completed`, `failed`, `dead_letter`, or `cancelled`.
3. A client-side timeout matching `carry_patch.rebuild_timeout` (default 600
   seconds) aborts the polling loop if the rebuild takes too long.

The same pattern applies to sync-triggered rebuilds: `POST /workspaces/<slug>/sync`
returns a `rebuild_job_id` when `rebuild_triggered` is true, and nightshift polls
that job ID.

**Rationale**: This is the same polling pattern the `afc` CLI uses with its
`--wait` flag. Server-side blocking is not available in the hub API. The polling
approach is straightforward to implement and allows nightshift to log
intermediate progress (running rebuilds expose per-patch `patch_results` as they
complete). The configurable poll interval and timeout give operators control
over the tradeoff between responsiveness and API load.

### DD-5: Integration with issue tracker

**Decision**: Carry-patch operations are NOT driven by issue labels. Instead, the
carry-patch stream polls the hub's patch-status dashboard
(`GET /api/v1/workspaces/<slug>/patch-status`) on a timer. This single endpoint
provides comprehensive state including: workspace sync status, per-patch status
and `conflict_files`, `last_rebuild` result, and a `summary` object with counts
of active, conflict, merged_upstream, and disabled patches plus
`total_rerere_resolutions`. Nightshift uses this dashboard to decide what actions
to take: trigger a sync, resolve conflicts, or report merged patches. Issues on
the fork's tracker may optionally be created for conflicts that require human
attention.

**Rationale**: Carry-patch is workspace-driven, not issue-driven. The hub is the
source of truth, not the issue tracker. The patch-status dashboard provides all
the state nightshift needs in a single API call, avoiding the need to
cross-reference multiple endpoints. This avoids coupling two external systems.

### DD-6: Where to place the hub client

**Decision**: Create a new `packages/afhub/` package for the hub API client,
following the same pattern as `packages/afissues/`. This package owns all hub
communication, data models, and authentication.

**Rationale**: Separation of concerns. The hub client has its own authentication
model (PAT with scopes, passed via `Authorization: Bearer <token>`), its own
error types (including the hub's `error_type` field for machine-readable error
classification), and its own data models. Placing it in `afcore` would violate
the existing package boundaries.

**Alternative considered**: Adding hub client to `afcore/nightshift/`. Rejected
because the hub client is a general-purpose API layer, not nightshift-specific
logic.

### DD-7: Local clone and working directory

**Decision**: Nightshift requires a local clone of the hub workspace as its
working directory. At startup, nightshift validates the CWD by comparing the
local origin remote URL against the `git_url` from the hub workspace metadata
(`GET /api/v1/workspaces/<slug>`). If the CWD does not match, nightshift exits
with a diagnostic error. The operator is responsible for cloning the repository
beforehand (e.g., via `afc` or `git clone` with the hub git URL). The clone URL
is the hub's built-in git server (`/git/<org>/<slug>.git`), and authentication
uses HTTP Basic with the PAT as the password (matching the
`afc credential-helper` pattern). Conflict resolution, patch inspection, and all
git operations run against this operator-provided clone. The clone is kept
up-to-date via `git fetch` at the start of each carry-patch cycle.

**Rationale**: Conflict resolution requires local file access for the coder
agent. Hub's git server at `/git/<org>/<slug>.git` provides authenticated access
via PAT-based HTTP Basic auth. A persistent clone (rather than ephemeral
worktrees) avoids re-cloning on every cycle and gives the coder agent a full
repository context. Requiring the operator to create the clone keeps nightshift
from creating infrastructure on its own, consistent with how operators also
create the hub workspace and PAT as prerequisites.

---

## Implementation Plan

### Phase 1: Foundation -- Hub Client and Configuration (~35% of effort)

#### 1.1 Create the `afhub` package

**Complexity**: Large

Files to create:

- `packages/afhub/pyproject.toml` -- Package metadata, dependencies (httpx,
  pydantic)
- `packages/afhub/afhub/__init__.py` -- Public API exports
- `packages/afhub/afhub/client.py` -- `HubClient` class
- `packages/afhub/afhub/polling.py` -- Rebuild and clone polling helpers
- `packages/afhub/afhub/models.py` -- Pydantic data models
- `packages/afhub/afhub/errors.py` -- Hub-specific error types
- `packages/afhub/afhub/auth.py` -- PAT credential resolution
- `packages/afhub/tests/` -- Test directory

**`HubClient` class** (`client.py`):

All requests use the `Authorization: Bearer <pat>` header. The PAT is an
af-hub Personal Access Token (format `af_pat_<token_id>_<secret>`), not a
generic API key. The required scopes for nightshift operation are documented in
section 1.1.1.

```python
class HubClient:
    """Async HTTP client for the af-hub carry-patch API.

    Authentication: all requests carry an af-hub PAT as
    ``Authorization: Bearer <pat>``.  The PAT must have the scopes
    listed in auth.REQUIRED_SCOPES.
    """

    def __init__(self, endpoint_url: str, pat: str) -> None: ...

    # Workspace operations
    async def get_workspace(self, slug: str) -> Workspace: ...
    async def create_workspace(self, **kwargs) -> Workspace: ...
    async def sync_workspace(
        self, slug: str, *, reset_to_upstream: bool = False,
    ) -> SyncResult: ...
    async def get_patch_status(self, slug: str) -> PatchStatusDashboard: ...
    async def reclone_workspace(self, slug: str) -> Workspace: ...

    # Patch operations
    async def list_patches(self, slug: str) -> list[Patch]: ...
    async def add_patch(
        self, slug: str, branch_name: str, *,
        position: int | None = None,
        upstream_pr_url: str | None = None,
        description: str | None = None,
        skip_branch_check: bool = False,
        if_not_exists: bool = False,
    ) -> Patch: ...
    async def add_patches_batch(
        self, slug: str, patches: list[dict],
    ) -> list[Patch]: ...
    async def update_patch(self, slug: str, patch_id: str, **kwargs) -> Patch: ...
    async def remove_patch(self, slug: str, patch_id: str) -> None: ...
    async def restore_patch(self, slug: str, patch_id: str) -> Patch: ...
    async def reorder_patches(self, slug: str, patch_ids: list[str]) -> list[Patch]: ...

    # Rebuild operations
    async def submit_rebuild(
        self, slug: str, *,
        strategy: str | None = None,
        fail_mode: str | None = None,
    ) -> RebuildJob: ...
    async def get_rebuild(self, slug: str, rebuild_id: str) -> RebuildJob: ...
    async def list_rebuilds(self, slug: str) -> list[RebuildJob]: ...
    async def cancel_rebuild(self, slug: str, rebuild_id: str) -> RebuildJob: ...
    async def requeue_rebuild(self, slug: str, rebuild_id: str) -> RebuildJob: ...
    async def rollback_rebuild(self, slug: str, rebuild_id: str) -> str: ...
    async def get_rebuild_preview(self, slug: str) -> RebuildPreview: ...

    # Rerere operations
    async def list_rerere(self, slug: str) -> list[RerereEntry]: ...
    async def forget_rerere(self, slug: str, pathspec: str) -> None: ...

    # Workspace variables
    async def get_variable(self, slug: str, key: str) -> str | None: ...
    async def set_variable(self, slug: str, key: str, value: str) -> None: ...
    async def delete_variable(self, slug: str, key: str) -> None: ...
    async def get_resolved_variables(self, slug: str) -> dict[str, str]: ...

    # Secrets (upstream credentials)
    async def list_secrets(self, slug: str) -> list[str]: ...
    async def create_secret(self, slug: str, key: str, value: str) -> None: ...
```

Key design points:

- **No blocking wait**. The hub API does not support `?wait=true` on any
  endpoint. `submit_rebuild()` returns a 202 with the queued job record.
  `sync_workspace()` returns immediately with a `SyncResult` that may contain a
  `rebuild_job_id`. Polling for completion is a separate concern handled by
  `polling.py` (see below).
- **`add_patch()` supports `skip_branch_check`** and **`if_not_exists`**
  flags. When nightshift registers a fix branch as a patch, it passes
  `skip_branch_check=True` (the branch exists in hub's git server, but hub
  validates this asynchronously) and `if_not_exists=True` (idempotent
  re-registration after retries).
- **`add_patches_batch()`** sends a JSON array body to `POST /patches` for
  atomic multi-patch insertion when registering several branches at once.
- **`submit_rebuild()`** accepts optional `strategy` (`"rebase"` or `"merge"`)
  and `fail_mode` (`"fail_fast"` or `"continue"`) parameters that override the
  workspace-level `REBUILD_STRATEGY` and `REBUILD_FAIL_MODE` variables for that
  single rebuild.
- **`get_rebuild_preview()`** calls `GET /rebuild-preview` which uses
  `git merge-tree --write-tree` to predict conflicts without mutating any refs.
  Nightshift uses this for proactive conflict detection before committing to a
  full rebuild.

##### 1.1.1 Required PAT scopes

Nightshift requires a PAT with the following scopes:

| Scope | Used for |
|-------|----------|
| `workspaces:read` | `get_workspace()`, `get_patch_status()`, `list_rerere()` |
| `workspaces:write` | `forget_rerere()` |
| `workspaces:sync` | `sync_workspace()`, `reclone_workspace()` |
| `patches:write` | `add_patch()`, `update_patch()`, `remove_patch()`, `restore_patch()`, `reorder_patches()` (implies `patches:read`) |
| `rebuilds:write` | `submit_rebuild()`, `cancel_rebuild()`, `requeue_rebuild()`, `rollback_rebuild()` (implies `rebuilds:read` for `get_rebuild()`, `list_rebuilds()`, `get_rebuild_preview()`) |
| `git:write` | Pushing resolved branches to hub's git server (implies `git:read`) |
| `vars:manage` | `get_variable()`, `set_variable()`, `delete_variable()` (implies `vars:read`, `vars:write`, `vars:delete`) |
| `secrets:manage` | `list_secrets()`, `create_secret()` for upstream credentials (implies `secrets:list`, `secrets:write`, `secrets:delete`) |

Note: scope implication chains mean that `patches:write` implicitly grants
`patches:read`, `git:write` implicitly grants `git:read`, etc. The PAT should
be created with the explicit scopes listed above; implied scopes do not need to
be listed at creation time.

**Polling helpers** (`polling.py`):

```python
async def poll_rebuild(
    client: HubClient,
    slug: str,
    rebuild_id: str,
    *,
    timeout: float = 600.0,
    interval: float = 5.0,
) -> RebuildJob:
    """Poll GET /rebuilds/:id until the job reaches a terminal status.

    Terminal statuses: completed, failed, dead_letter, cancelled.
    Raises TimeoutError if timeout exceeded.
    """

async def poll_clone_ready(
    client: HubClient,
    slug: str,
    *,
    timeout: float = 300.0,
    interval: float = 5.0,
) -> Workspace:
    """Poll GET /workspaces/:slug until clone_status is 'ready' or 'failed'.

    Raises TimeoutError if timeout exceeded.
    Raises HubError if clone_status transitions to 'failed'.
    """
```

These mirror the `afc` CLI's `--wait` behavior, which is implemented as
client-side polling (the hub has no server-side blocking mode). Default timeout
is 600 seconds for rebuilds and 300 seconds for clones. Default poll interval
is 5 seconds.

**Data models** (`models.py`):

```python
class Workspace(BaseModel):
    slug: str
    hub_url: str | None = None       # base URL of this hub instance
    display_name: str | None = None
    description: str | None = None
    git_url: str
    upstream_url: str | None = None
    integration_branch: str | None = None  # defaults to "deploy" for carry_patch
    workspace_mode: str               # "standard" | "carry_patch"
    status: str                       # "active" | "archived"
    clone_status: str                 # "pending" | "cloning" | "ready" | "failed"
    clone_error: str | None = None    # omitted when empty
    sync_status: str                  # "idle" | "syncing"
    sync_mode: str | None = None      # "pull_only" | ...
    sync_error: str | None = None     # omitted when empty
    upstream_head_sha: str | None = None
    head_sha: str | None = None
    last_sync_at: str | None = None

class Patch(BaseModel):
    id: str
    workspace_slug: str
    branch_name: str
    position: int
    status: str  # "active" | "merged_upstream" | "conflict" | "disabled" | "deleted"
    conflict_files: list[str] | None = None
    upstream_pr_url: str | None = None
    description: str | None = None
    deleted_at: str | None = None
    added_at: str
    updated_at: str

class PatchResult(BaseModel):
    patch_id: str
    branch_name: str
    position: int
    status: str  # "success" | "conflict" | "skipped"
    skipped_reason: str | None = None  # "merged_upstream" | "disabled" | "deleted" | "branch_not_found"
    new_head_sha: str | None = None
    conflict_files: list[str] | None = None

class RebuildJob(BaseModel):
    id: str
    status: str  # "queued" | "running" | "completed" | "failed" | "dead_letter" | "cancelled"
    strategy: str | None = None  # "rebase" | "merge"; omitted when empty
    error: str | None = None     # omitted when empty
    patch_results: list[PatchResult] | None = None  # omitted when empty
    integration_head_sha: str | None = None          # omitted when empty
    previous_integration_head_sha: str | None = None # omitted when empty
    created_at: str
    completed_at: str | None = None

class SyncResult(BaseModel):
    patches_merged: list[str]
    rebuild_triggered: bool
    rebuild_job_id: str | None = None   # present only when rebuild_triggered is true
    force_push_detected: bool = False

class RebuildPreviewPatchResult(BaseModel):
    patch_id: str
    branch_name: str
    position: int
    status: str  # "would_succeed" | "would_conflict"
    tree_sha: str | None = None
    conflict_files: list[str] | None = None

class RebuildPreview(BaseModel):
    patch_results: list[RebuildPreviewPatchResult]

class PatchDetail(BaseModel):
    id: str
    branch_name: str
    position: int
    status: str
    last_rebuild_result: str | None = None  # "success" | "conflict" | "skipped" | null
    conflict_files: list[str] | None = None

class PatchSummary(BaseModel):
    total_patches: int
    active: int
    merged_upstream: int
    conflict: int
    disabled: int
    total_rerere_resolutions: int = 0

class RebuildSummary(BaseModel):
    id: str
    status: str

class PatchStatusDashboard(BaseModel):
    workspace_slug: str
    workspace_mode: str
    status: str                        # workspace status: "active" | "archived"
    clone_status: str
    clone_error: str | None = None     # omitted when empty
    sync_status: str
    sync_error: str | None = None      # omitted when empty
    sync_mode: str | None = None
    head_sha: str | None = None
    git_url: str | None = None
    upstream_url: str | None = None
    upstream_head_sha: str | None = None
    integration_branch: str | None = None
    integration_head_sha: str | None = None  # from last rebuild, empty if none
    last_sync_at: str | None = None
    last_rebuild: RebuildSummary | None = None
    patches: list[PatchDetail]
    summary: PatchSummary

class RerereEntry(BaseModel):
    path: str | None = None
    recorded_at: str | None = None
```

All models use `model_config = ConfigDict(extra="ignore")` to tolerate
additional fields from future hub API versions.

**Error types** (`errors.py`):

The hub API returns errors in a standard envelope:
`{"error": {"code": <int>, "message": "<string>", "error_type": "<string>?"}}`.

The `error_type` field is present only on certain endpoints and provides a
machine-readable classification.

```python
class HubError(Exception):
    """Base error for hub API failures."""
    def __init__(self, status_code: int, message: str, error_type: str | None = None) -> None: ...

class HubAuthError(HubError): ...         # 401 - invalid or expired PAT
class HubForbiddenError(HubError): ...    # 403 - PAT lacks required scope
class HubNotFoundError(HubError): ...     # 404 - workspace/patch/rebuild not found
class HubConflictError(HubError): ...     # 409 - concurrent rebuild, duplicate merge, etc.
class HubConnectionError(HubError): ...   # network-level failure (timeout, DNS, etc.)

# Typed 400 errors using error_type from response body
class HubModeError(HubError): ...         # error_type: "workspace_mode_mismatch"
class HubNoActivePatchesError(HubError):  # error_type: "no_active_patches"
    ...
```

The client inspects the `error_type` field in 400 responses and raises the
appropriate typed exception. For 400 responses without a recognized
`error_type`, the base `HubError` is raised. For 409 responses, the
`error_type` (e.g., `"concurrent_rebuild"`, `"duplicate_merge"`) is stored on
the `HubConflictError` instance for caller inspection.

**Auth** (`auth.py`):

- `resolve_hub_pat()` resolves the af-hub Personal Access Token from (in
  priority order):
  1. Explicit value passed from `--token` CLI flag
  2. `AF_HUB_TOKEN` environment variable
- Returns `None` if neither is set (carry-patch mode unavailable)
- `resolve_hub_url()` resolves the hub endpoint URL from (in priority order):
  1. Explicit value passed from `--hub-url` CLI flag
  2. `AF_HUB_URL` environment variable
  3. `[hub] endpoint_url` from the existing `.nightshift/config.toml`
- Returns `None` if none is set (required on first start before config exists)
- The PAT must have the scopes listed in section 1.1.1

```python
REQUIRED_SCOPES: list[str] = [
    "workspaces:read", "workspaces:write", "workspaces:sync",
    "patches:write",
    "rebuilds:write",
    "git:write",
    "vars:manage",
    "secrets:manage",
]

def resolve_hub_pat(
    *, token_flag: str | None = None, env_var: str = "AF_HUB_TOKEN",
) -> str | None:
    """Return the af-hub PAT or None if unavailable."""

def resolve_hub_url(
    *, hub_url_flag: str | None = None, config_url: str = "",
    env_var: str = "AF_HUB_URL",
) -> str | None:
    """Return the hub endpoint URL from flag, env var, or config.

    Resolution order: --hub-url flag > AF_HUB_URL env var > config value.
    Returns None if none is set (required on first start).
    """
```

#### 1.2 Add hub configuration to nightshift

**Complexity**: Small

Files to modify:

- `packages/afcore/afcore/core/config.py` -- Add `HubConfig` and
  `CarryPatchConfig` models

```python
class HubConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    endpoint_url: str = Field(default="", description="Hub API endpoint URL")

class CarryPatchConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = Field(default=False, description="Enable carry-patch work stream")
    workspaces: list[str] = Field(default_factory=list, description="Hub workspace slugs to monitor")
    check_interval: int = Field(default=300, ge=60, description="Seconds between checks (minimum 60)")
    auto_resolve: bool = Field(default=True, description="Auto-resolve conflicts via AI agent")
    rebuild_timeout: int = Field(default=600, description="Seconds to wait for rebuild polling completion")
    rebuild_poll_interval: int = Field(default=5, ge=2, description="Seconds between rebuild status polls")
    max_resolve_retries: int = Field(default=2, ge=0, le=10, description="Max conflict resolution attempts per patch")
```

Add to `AgentFoxConfig`:

```python
hub: HubConfig = Field(default_factory=HubConfig)
carry_patch: CarryPatchConfig = Field(default_factory=CarryPatchConfig)
```

#### 1.3 Add afhub as a dependency

**Complexity**: Small

Files to modify:

- `packages/afcore/pyproject.toml` -- Add `afhub` as an optional dependency
- `packages/nightshift/pyproject.toml` -- Add `afhub` as a dependency

#### 1.4 Tests for Phase 1

**Complexity**: Medium

Files to create:

- `packages/afhub/tests/test_client.py` -- Unit tests with httpx mock transport
- `packages/afhub/tests/test_polling.py` -- Polling helper tests (timeout,
  terminal status detection, interval behavior)
- `packages/afhub/tests/test_models.py` -- Model serialization/deserialization,
  including omitempty field handling
- `packages/afhub/tests/test_auth.py` -- PAT resolution from flag, env var, and
  missing
- `packages/afhub/tests/test_errors.py` -- Error classification from HTTP
  status codes and error_type values

Key test scenarios for the client:

- `submit_rebuild()` returns 202 with queued job (not a completed job)
- `sync_workspace()` returns `SyncResult` with `rebuild_job_id` when
  `rebuild_triggered` is true
- `add_patch()` with `if_not_exists=True` returns 200 for existing patch
  instead of 409
- `add_patches_batch()` sends JSON array body and returns list
- `get_rebuild_preview()` returns `RebuildPreview` with per-patch conflict
  prediction
- 400 with `error_type: "workspace_mode_mismatch"` raises `HubModeError`
- 400 with `error_type: "no_active_patches"` raises `HubNoActivePatchesError`
- 409 with `error_type: "concurrent_rebuild"` raises `HubConflictError` with
  stored `error_type`
- 403 raises `HubForbiddenError` (PAT lacks scope)

Key test scenarios for polling:

- `poll_rebuild()` polls until `completed` status
- `poll_rebuild()` raises `TimeoutError` after deadline
- `poll_rebuild()` returns immediately for `failed`, `dead_letter`, `cancelled`
- `poll_clone_ready()` raises `HubError` on `failed` clone status

---

### Phase 2: Core Carry-Patch Logic (Depends on Phase 1, ~30% of effort)

Carry-patch mode does not duplicate the fix pipeline. The existing fix pipeline
already creates fixes on branches from `af:fix` issues. In carry-patch mode,
the only differences are:

- Fix branches are **registered as patches** with hub after integration
- A lightweight **conflict monitoring stream** polls hub for patches that need
  conflict resolution after upstream advances
- Conflict resolution reuses the existing coder via `FixPipeline._run_coder_session()`

#### 2.1 Workspace variable initialization

**Complexity**: Small

When nightshift first connects to a hub workspace, it configures workspace
variables to disable hub's automatic rebuild triggers. Nightshift controls
rebuild timing explicitly so that it can coordinate with conflict resolution
and budget constraints.

```python
async def initialize_workspace_variables(
    client: HubClient, slug: str,
) -> None:
    """Set workspace variables for nightshift-managed operation.

    Disables auto-rebuild so nightshift controls when rebuilds happen.
    """
    await client.set_variable(slug, "AUTO_REBUILD_AFTER_SYNC", "false")
    await client.set_variable(slug, "AUTO_REBUILD_AFTER_PUSH", "false")
```

This is idempotent. The variables are set once during initial startup and
persist across nightshift restarts (they are stored on the hub, not locally).

Hub variables that nightshift reads but does not set:

| Variable | Nightshift usage |
|----------|-----------------|
| `REBUILD_STRATEGY` | Default strategy for rebuilds (nightshift can override per-rebuild) |
| `REBUILD_FAIL_MODE` | Default fail mode (nightshift can override per-rebuild) |
| `SQUASH_MERGE_DETECTION` | Controls merge detection during sync; nightshift relies on hub's default `"both"` |

#### 2.2 Patch registration in fix pipeline

**Complexity**: Small

Files to modify:

- `packages/afcore/afcore/nightshift/fix_pipeline.py` -- Replace
  harvest/integration with patch registration in carry-patch mode

When `carry_patch.enabled` is true, after the fix branch is created and the
coder-reviewer loop completes, the fix pipeline **skips local merge entirely**
(no harvest, no squash-merge). Instead:

1. Push the fix branch to hub's git server
2. Register the fix branch as a patch with hub:
   ```python
   patch = await hub_client.add_patch(
       slug, branch_name,
       upstream_pr_url=upstream_pr_url,  # if available from issue
       description=f"Fix for #{issue_number}: {issue_title}",
       skip_branch_check=True,  # branch exists in hub's git server
       if_not_exists=True,       # idempotent for retry safety
   )
   ```
3. Submit a rebuild and poll for completion:
   ```python
   job = await hub_client.submit_rebuild(slug)
   completed = await poll_rebuild(
       hub_client, slug, job.id,
       timeout=config.carry_patch.rebuild_timeout,
       interval=config.carry_patch.rebuild_poll_interval,
   )
   ```
4. Check `completed.status` and `completed.patch_results` for the new patch
5. On success, proceed with normal issue closure

Hub owns the integration branch -- nightshift never touches it. The existing
merge strategies (`direct`, `branch`, `pr`) are not used in carry-patch mode.

The `submit_rebuild()` call returns a 202 with a queued job record.
`poll_rebuild()` then polls `GET /rebuilds/:id` at the configured interval
until the job reaches a terminal status (`completed`, `failed`, `dead_letter`,
or `cancelled`).

If `submit_rebuild()` raises `HubConflictError` (a rebuild is already
queued/running), nightshift retrieves the active rebuild via `list_rebuilds()`
and polls that one instead.

If `submit_rebuild()` raises `HubNoActivePatchesError` (no patches with
`active` or `conflict` status), nightshift logs a warning and skips the rebuild
-- this can happen if all patches were merged upstream between registration and
rebuild submission.

#### 2.3 Conflict monitoring stream

**Complexity**: Medium

Files to create:

- `packages/afcore/afcore/nightshift/carry_patch_monitor.py`

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
        1. Fetch patch-status dashboard from hub (GET /patch-status)
        2. Log any newly merged patches (informational -- hub transitions
           patches to merged_upstream during sync)
        3. Optionally run rebuild preview (GET /rebuild-preview) to detect
           conflicts proactively before they occur in a real rebuild
        4. For each patch in conflict status (if auto_resolve is enabled):
           a. Read conflict_files from the dashboard's patch detail
              (available in last_rebuild_result and conflict_files fields)
           b. Fetch upstream diff and conflict context from hub's git server
           c. Run coder:carry-patch session to resolve
           d. Push resolved branch back to hub's git server
           e. Submit rebuild and poll for completion
        5. Return cycle result with actions taken
        """
```

Key behaviors:

- **Idempotent cycles**: Fetches fresh state from hub on each cycle. No
  persistent local state beyond the working directory clone.
- **Rebuild polling**: After pushing a resolved branch, the monitor submits a
  rebuild via `submit_rebuild()` (returns 202) and then polls via
  `poll_rebuild()` to confirm the resolution succeeded. If the rebuild reveals
  the conflict persists, the patch stays in `conflict` status and the monitor
  will retry on the next cycle (up to `max_resolve_retries`).
- **Proactive conflict detection**: The monitor can call
  `get_rebuild_preview()` to predict which patches would conflict before
  committing to a full rebuild. This uses `git merge-tree --write-tree` on the
  hub side and does not mutate any refs. The preview returns
  `would_succeed`/`would_conflict` status per patch.
- **Reuses existing coder**: Conflict resolution invokes
  `FixPipeline._run_coder_session()` (or the underlying `run_session()`) with
  `archetype="coder"` and `mode="carry-patch"`. No duplicate pipeline logic.
- **Error handling**: Follows nightshift's fail-open pattern. A failed conflict
  resolution for one patch does not block attempts on other patches. Hub
  connection failures are logged and the cycle skips.

#### 2.4 Register conflict monitoring work stream

**Complexity**: Small

Files to modify:

- `packages/afcore/afcore/nightshift/streams.py` -- Add carry-patch stream
  to `build_streams()`
- `packages/afcore/afcore/nightshift/engine.py` -- Add
  `_run_carry_patch_monitor()` method that delegates to `CarryPatchMonitor`
- `packages/afcore/afcore/nightshift/daemon.py` -- Add `"carry-patch"` to
  `_STREAM_DISPLAY_NAMES` and `_STREAM_ACTIVE_LABELS`

#### 2.5 Hub client initialization in daemon startup

**Complexity**: Small

Files to modify:

- `packages/nightshift/nightshift/app.py` -- Initialize `HubClient` in
  `_run_daemon()` and pass to engine

```python
hub_client = None
if hub_pat := resolve_hub_pat(token_flag=token_flag):
    hub_url = resolve_hub_url(
        hub_url_flag=hub_url_flag, config_url=config.hub.endpoint_url,
    )
    if not hub_url:
        click.echo(
            "Error: hub URL required on first start. "
            "Pass --hub-url or set AF_HUB_URL.",
            err=True,
        )
        sys.exit(1)
    from afhub.client import HubClient
    hub_client = HubClient(hub_url, hub_pat)
    # Validate CWD against workspace metadata before proceeding
    for slug in config.carry_patch.workspaces:
        ws = await hub_client.get_workspace(slug)
        local_origin = run_git_sync(["remote", "get-url", "origin"], cwd=root)
        if local_origin != ws.git_url:
            click.echo(
                f"Error: CWD origin does not match workspace '{slug}': "
                f"local origin is '{local_origin}' but workspace expects "
                f"'{ws.git_url}'. cd into the correct directory or clone "
                f"the workspace first.",
                err=True,
            )
            sys.exit(1)
    # Initialize workspace variables on first connect
    for slug in config.carry_patch.workspaces:
        await initialize_workspace_variables(hub_client, slug)
elif config.carry_patch.enabled:
    logger.warning(
        "carry_patch.enabled is true but no hub PAT available "
        "(set AF_HUB_TOKEN or pass --token); disabling carry-patch stream"
    )
```

#### 2.6 Audit events for carry-patch

**Complexity**: Small

Files to modify:

- `packages/afaudit/afaudit/events.py` -- Add new event types

```python
CARRY_PATCH_CONFLICT_DETECTED = "carry_patch_conflict_detected"
CARRY_PATCH_CONFLICT_RESOLVED = "carry_patch_conflict_resolved"
CARRY_PATCH_CONFLICT_FAILED = "carry_patch_conflict_failed"
CARRY_PATCH_PATCH_REGISTERED = "carry_patch_patch_registered"
CARRY_PATCH_REBUILD_REQUESTED = "carry_patch_rebuild_requested"
CARRY_PATCH_REBUILD_COMPLETED = "carry_patch_rebuild_completed"
CARRY_PATCH_REBUILD_FAILED = "carry_patch_rebuild_failed"
CARRY_PATCH_MERGED_DETECTED = "carry_patch_merged_detected"
```

#### 2.7 Tests for Phase 2

**Complexity**: Medium

Files to create:

- `packages/afcore/tests/test_carry_patch_monitor.py` -- Monitor logic tests
  with mocked HubClient
- `packages/afcore/tests/test_carry_patch_registration.py` -- Patch
  registration in fix pipeline
- `packages/afcore/tests/test_carry_patch_stream.py` -- Stream registration
  and enablement

Key test scenarios:

- Monitor cycle with no conflicts (no-op)
- Monitor cycle with one patch in conflict -> resolution -> rebuild poll ->
  completed
- Monitor cycle with rebuild poll -> failed (conflict persists) -> retry on
  next cycle
- Monitor cycle with hub unreachable (graceful degradation)
- Monitor cycle with merged patches detected (informational)
- Auto-resolve disabled (skip resolution, only report)
- Fix pipeline registers patch via `add_patch()` with `skip_branch_check=True`
  and `if_not_exists=True`
- Fix pipeline skips registration when not in carry-patch mode
- Rebuild submission returns 409 (concurrent rebuild) -> poll existing rebuild
- Rebuild submission returns 400 with `no_active_patches` -> skip gracefully
- Workspace variable initialization is idempotent
- Rebuild preview returns `would_conflict` predictions

---

### Phase 3: Agent Integration -- Conflict Resolution (Depends on Phase 2, ~15% of effort)

Nightshift does not add CLI subcommands for carry-patch operations. Users
interact with carry-patch workspaces through the existing `afc` CLI
(`afc patch list`, `afc rebuild submit --wait`, `afc workspace sync`, etc.).
Nightshift's value is automation -- it polls, detects, resolves, and rebuilds
without human intervention.

Conflict resolution reuses the existing coder session infrastructure. The new
pieces are a dedicated archetype mode and profile template that give the coder
agent the right context and instructions for carry-patch conflicts.

#### 3.1 Carry-patch archetype mode

**Complexity**: Small

Files to modify:

- `packages/afcore/afcore/archetypes.py` -- Add `carry-patch` mode to coder
  archetype

```python
"carry-patch": ModeConfig(
    model_tier="STANDARD",
    max_turns=200,
    thinking_mode="adaptive",
    effort="high",
)
```

#### 3.2 Carry-patch profile template

**Complexity**: Medium

Files to create:

- `packages/afcore/afcore/_templates/profiles/coder_carry-patch.md`

Profile content should instruct the agent:

- You are resolving a conflict between a local patch and upstream changes
- The patch exists for a specific reason (provided in patch description)
- Preserve the intent of the patch while adapting to upstream changes
- Do not introduce new features or refactor beyond what the conflict requires
- Use conventional commits for the resolution
- Test the resolution if a test suite is available
- Explain what changed and why in the commit message

#### 3.3 Context construction for conflict resolution

**Complexity**: Small

The `CarryPatchMonitor` (Phase 2.3) builds context for the coder session using
data from the hub API:

- Patch description (why the patch exists) -- from `Patch.description`
- Conflict files with full paths -- from `PatchResult.conflict_files` in the
  rebuild result, or from `PatchDetail.conflict_files` in the dashboard
- Rebuild preview predictions -- from `GET /rebuild-preview` which returns
  `would_succeed`/`would_conflict` per patch with `conflict_files` listing
- Upstream changes that caused the conflict (diff between old and new upstream
  HEAD)
- Git rerere history if available (from `GET /rerere` hub API endpoint)
- Prior resolution attempts (from knowledge store)

This context is passed to `run_session()` with `archetype="coder"` and
`mode="carry-patch"`, reusing the same session infrastructure as the fix
pipeline.

Files to modify (if needed):

- `packages/afcore/afcore/session/context.py` -- Add carry-patch to
  `_ARCHETYPE_ARTIFACTS`

#### 3.4 Tests for Phase 3

**Complexity**: Small

Files to create:

- `packages/afcore/tests/test_carry_patch_profile.py` -- Profile loading and
  content tests

---

### Phase 4: Testing and Hardening (Depends on all previous phases, ~20% ongoing)

#### 4.1 Integration test suite

**Complexity**: Large

Files to create:

- `packages/afhub/tests/test_integration.py` -- Integration tests against a mock
  hub server (httpx mock transport)
- `packages/afcore/tests/test_carry_patch_e2e.py` -- End-to-end pipeline tests

Test scenarios:

- Full happy path: fix issue -> register patch -> submit rebuild (202) -> poll
  until completed -> verify
- Conflict monitoring: detect conflict via patch-status dashboard -> resolve ->
  submit rebuild -> poll until completed -> verify resolution
- Hub returns 409 on rebuild submission (concurrent rebuild) -> retrieve active
  rebuild via list_rebuilds -> poll that rebuild instead
- Hub returns 400 with `error_type: "no_active_patches"` -> skip rebuild
  gracefully
- Hub connection timeout -> retry via request_with_retry -> fallback
- Upstream force-push -> sync with reset-to-upstream -> force_push_detected in
  SyncResult
- Sync returns `rebuild_triggered: true` with `rebuild_job_id` -> poll that
  rebuild
- Patch detected as merged upstream -> informational reporting, soft-delete
  lifecycle
- Multiple patches in conflict -> resolve in position order
- Budget exhaustion mid-resolution -> stop and report
- Rebuild preview (GET /rebuild-preview) -> would_conflict predictions match
  actual rebuild conflicts
- Rebuild rollback -> integration branch reset to
  `previous_integration_head_sha`
- Rebuild cancel -> queued job transitions to cancelled
- Rebuild requeue -> dead_letter job transitions to queued
- Polling timeout -> TimeoutError raised after deadline
- PAT with insufficient scopes -> HubForbiddenError (403)

#### 4.2 Error handling hardening

**Complexity**: Medium

Files to modify:

- `packages/afcore/afcore/nightshift/carry_patch_monitor.py` -- Add
  comprehensive error handling
- `packages/afcore/afcore/nightshift/fix_pipeline.py` -- Harden patch
  registration error paths

Key error paths to harden:

- Hub API returns unexpected JSON schema (handle Pydantic validation errors
  with `extra="ignore"` and graceful None defaults)
- Git push to hub's git server fails (auth error, network error)
- Push of resolution fails (branch was modified on hub between clone and push)
- Patch status changes between dashboard fetch and action (stale state --
  re-fetch before acting)
- Hub returns 401 (PAT expired or revoked) vs 403 (PAT lacks scope) --
  distinct error handling
- Rebuild transitions to `dead_letter` (infrastructure failure) -- log and
  optionally requeue via `requeue_rebuild()`
- Hub returns omitempty fields (e.g., `clone_error`, `sync_error`,
  `patch_results` absent instead of null) -- models handle gracefully via
  `None` defaults

#### 4.3 Documentation

**Complexity**: Medium

Files to modify:

- `docs/config-reference.md` -- Document `[hub]` and `[carry_patch]` config
  sections
- `docs/architecture.md` -- Add carry-patch section

Files to create:

- `docs/carry-patch.md` -- User guide for carry-patch setup and operation

#### 4.4 Known limitations

Document the following known limitations:

1. **Single rebuild constraint**: Only one rebuild per workspace at a time.
   If nightshift submits a rebuild and one is already queued/running, it
   receives a 409 (`concurrent_rebuild`) and polls the existing rebuild
   instead.
2. **Conflict resolution quality**: The AI agent may not resolve all conflicts
   correctly. Failed resolutions are logged and the patch remains in conflict
   status for manual intervention via `afc`.
3. **Polling overhead**: Since the hub API has no server-side blocking mode,
   nightshift polls `GET /rebuilds/:id` at a configurable interval (default 5
   seconds). This is consistent with how the `afc` CLI implements `--wait`.
   The polling interval is tunable via `carry_patch.rebuild_poll_interval`.
4. **Soft-delete visibility**: Patches soft-deleted by the hub after a
   successful rebuild (merged_upstream -> deleted) are excluded from the
   `GET /patches` list and the patch-status dashboard. They can be restored
   via `afc patch restore` within 7 days but nightshift does not track them.
5. **Scope accumulation**: The PAT used by nightshift requires a broad set of
   scopes (see section 1.1.1). A PAT with missing scopes will receive 403
   errors on specific endpoints; nightshift logs these as `HubForbiddenError`
   with the failing endpoint for diagnosis.

---

## File Summary

### New package

| Package | Purpose | Files |
|---------|---------|-------|
| `packages/afhub/` | Hub API client, polling helpers, data models | ~7 source files, ~6 test files |

### New files in existing packages

| File | Package | Purpose |
|------|---------|---------|
| `afcore/nightshift/carry_patch_monitor.py` | afcore | Conflict monitoring stream |
| `afcore/_templates/profiles/coder_carry-patch.md` | afcore | Agent profile |

### Modified files

| File | Change |
|------|--------|
| `afcore/core/config.py` | Add HubConfig, CarryPatchConfig |
| `afcore/archetypes.py` | Add carry-patch ModeConfig to coder |
| `afcore/nightshift/fix_pipeline.py` | Add patch registration step with polling |
| `afcore/nightshift/engine.py` | Add `_run_carry_patch_monitor()` |
| `afcore/nightshift/streams.py` | Register carry-patch stream |
| `afcore/nightshift/daemon.py` | Display names for carry-patch stream |
| `afcore/session/context.py` | Carry-patch artifact filtering (if needed) |
| `afaudit/events.py` | Add carry-patch audit event types |
| `nightshift/app.py` | `--hub-url` and `--token` flags, hub client init, CWD validation, variable initialization |
| `afcore/pyproject.toml` | Add afhub dependency |
| `nightshift/pyproject.toml` | Add afhub dependency |
| `afcore/core/config_gen.py` | Add hub/carry_patch to visible sections |

### Effort Estimates

| Phase | Complexity | Relative Size |
|-------|-----------|---------------|
| Phase 1: Foundation (client, polling, config) | Large | ~35% |
| Phase 2: Core logic (registration, monitoring, variables) | Medium-Large | ~30% |
| Phase 3: Agent integration (archetype, profile, context) | Small-Medium | ~15% |
| Phase 4: Testing and hardening | Medium | ~20% (ongoing) |

Phase 3 (profile template and archetype mode) can be created independently of
Phase 2. Phases 1 and 2 are strictly sequential (Phase 2 depends on the
HubClient and polling infrastructure from Phase 1).