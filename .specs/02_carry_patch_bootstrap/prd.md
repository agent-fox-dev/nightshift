---
spec_id: '02'
spec_name: carry_patch_bootstrap
title: Carry Patch Bootstrap
status: draft
created_at: '2026-09-01T10:27:46.132115+00:00'
updated_at: '2026-09-01T10:34:32.606395+00:00'
owner: ''
source: interactive
schema_version: 1
---
# carry_patch_bootstrap: Config, CLI Flags, and Startup Validation

## Intent

Extend the existing `agentfox` config system and `nightshift` CLI entry point
to support carry-patch mode. This spec covers the Pydantic config models, the
CLI flags that activate carry-patch, the startup validation sequence (hub URL
resolution, CWD verification), and the workspace variable initialization that
disables hub-side auto-rebuild.

This is a prerequisite for the conflict monitoring and fix pipeline specs that
run the actual carry-patch work loop.

## Goals

- Add `HubConfig` and `CarryPatchConfig` Pydantic models to `AgentFoxConfig`
  with safe defaults (carry-patch disabled when not configured).
- Add `--hub-url`, `--workspace`, and `--token` CLI flags to the `nightshift`
  command with 3-tier resolution (CLI flag > env var > config file).
- On startup with carry-patch flags, validate the CWD is a matching clone of
  the specified hub workspace; exit with a clear error message if not.
- Generate a default `.nightshift/config.toml` on first start when none exists.
- Set `AUTO_REBUILD_AFTER_SYNC=false` and `AUTO_REBUILD_AFTER_PUSH=false` via
  the hub API on first startup, so nightshift controls rebuild timing.

## Non-Goals

- Conflict monitoring stream or fix pipeline changes (Spec 3)
- Hub workspace creation or patch registration
- Multi-workspace support (one nightshift instance per workspace)
- Token rotation or PAT refresh during a running session; a PAT that expires
  mid-run causes hub API errors that are logged as `HubAuthError`

## Background

This spec implements the bootstrapping layer — config models, CLI surface, and
startup validation — that downstream specs (conflict monitoring, fix pipeline)
depend on.

The carry-patch feature allows `nightshift` to operate as a local daemon that
monitors a hub workspace for conflicts and drives a fix pipeline, with the hub
controlling patch state and nightshift controlling rebuild timing. The
bootstrapping layer establishes that the local checkout matches the expected hub
workspace before any monitoring begins.

Full motivation and design rationale are documented in
`docs/proposals/carry_patch_support.md`.

## Tech Stack

- Python 3.12+, pydantic v2, click
- `agentfox.core.config` (existing config system, `packages/agentfox/`)
- `nightshift.app` (CLI entry point, `packages/nightshift/`)
- `afhub` package (Spec 01_afhub_client) for `HubClient`, `resolve_hub_url`,
  `resolve_hub_pat`
- Git via blocking `subprocess.run()`: `git remote get-url origin`
- Logging: Python standard `logging` module (`logging.info`, `logging.warning`,
  `logging.error`); `click.echo(..., err=True)` only for user-facing error
  messages immediately before `sys.exit(1)`

## Dependencies

| Spec | From Group | To Group | Relationship |
|------|-----------|----------|--------------|
| 01_afhub_client | 1 | 1 | Imports HubClient, resolve_hub_url, resolve_hub_pat, HubAuthError, HubNotFoundError, HubConnectionError, HubForbiddenError |

## External API Surface (afhub_client cross-reference)

This spec consumes the following types and functions from `afhub_client`
(Spec 01_afhub_client). The authoritative definitions live in that spec;
the summaries below exist to make this spec self-contained for reviewers.

### `HubNotFoundError`

A confirmed export of `afhub_client`. Maps to HTTP 404 responses. Hierarchy:

```python
class HubNotFoundError(HubError): ...  # 404 — workspace/patch/rebuild not found
```

### `Workspace` model fields used in this spec

| Field | Type | Notes |
|-------|------|-------|
| `workspace_mode` | `str` | e.g. `"carry_patch"` |
| `clone_status` | `str` | `"pending"` \| `"cloning"` \| `"ready"` \| `"failed"` |
| `git_url` | `str` | Required; compared against local origin URL |
| `integration_branch` | `str \| None` | Used when generating default config; defaults to `None` |

### `HubClient(endpoint_url, pat)`

Constructor documented in 01_afhub_client REQ-1. A single shared instance is
constructed once inside the async startup helper (before the first API call) and
reused across REQ-3 (CWD validation) and REQ-5 (variable initialization):

```python
hub_client = HubClient(endpoint_url=resolved_hub_url, pat=resolved_pat)
```

### `HubClient.get_workspace(slug: str) -> Workspace`

Returns a `Workspace` model. Raises `HubAuthError` (401/403) or
`HubNotFoundError` (404) on failure.

### `HubClient.set_variable(slug: str, key: str, value: str) -> None`

Uses PATCH-then-POST upsert as documented in 01_afhub_client REQ-4.
Any exception from this call is treated as non-fatal in this spec (see REQ-5).

### `resolve_hub_url(hub_url_flag, config_url)` / `resolve_hub_pat()`

Imported from `afhub.auth`.

`resolve_hub_url()` encapsulates the full 3-tier resolution chain internally:
it reads from `hub_url_flag` first, then the `AF_HUB_URL` environment variable,
then `config_url`. The caller passes all three sources in a single call:

```python
resolved_hub_url = resolve_hub_url(hub_url_flag=hub_url_flag, config_url=config.hub.endpoint_url)
```

`resolve_hub_pat()` reads `AF_HUB_TOKEN` from the environment; the caller
handles the CLI `--token` flag tier before invoking it.

There is no equivalent helper for workspace slug resolution. The `--workspace`
3-tier resolution is handled inline in `app.py`:

```python
resolved_slug = workspace_flag or os.environ.get('AF_WORKSPACE', '') or config.carry_patch.workspace
```

## Requirements

### REQ-1: HubConfig and CarryPatchConfig Pydantic models

Add to `packages/agentfox/agentfox/core/config.py`:

```python
class HubConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")
    endpoint_url: str = Field(default="", description="Hub API base URL")

class CarryPatchConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")
    enabled: bool = Field(default=False)
    workspace: str = Field(default="", description="Hub workspace slug")
    check_interval: Annotated[int, Clamped(ge=60)] = Field(default=300)
    auto_resolve: bool = Field(default=True)
    rebuild_timeout: int = Field(default=600)
    rebuild_poll_interval: Annotated[int, Clamped(ge=2)] = Field(default=5)
    max_resolve_retries: Annotated[int, Clamped(ge=0, le=10)] = Field(default=2)
```

**`Clamped` validator:** `Clamped` is an internal annotated-type utility
already defined in `agentfox.core.config` (the same file being modified). It
is used throughout existing models such as `NightShiftConfig`. No new import
or module is required. It silently clamps out-of-range values rather than
raising `ValidationError`, matching the established pattern in the codebase.

Add `hub: HubConfig` and `carry_patch: CarryPatchConfig` fields to
`AgentFoxConfig` with `default_factory`. Existing config files without these
sections load cleanly (backward-compatible via `extra="ignore"`).

### REQ-2: CLI flags with 3-tier resolution

In `packages/nightshift/nightshift/app.py`, add three CLI options to `main`:

- `--hub-url TEXT`: hub API base URL. Resolution is handled by a single call to
  `resolve_hub_url(hub_url_flag=hub_url_flag, config_url=config.hub.endpoint_url)`,
  which internally checks: CLI flag > `AF_HUB_URL` env var > `config.hub.endpoint_url`.
  Required (non-empty result) when carry-patch mode is active.
- `--workspace TEXT`: hub workspace slug. Resolved inline in `app.py` as:
  `resolved_slug = workspace_flag or os.environ.get('AF_WORKSPACE', '') or config.carry_patch.workspace`.
  No external helper is used for this resolution.
- `--token TEXT`: hub PAT. Resolved from: CLI `--token` flag first; if absent,
  `resolve_hub_pat()` reads `AF_HUB_TOKEN` from the environment.

**Mode activation rules:**

- Carry-patch mode is active only when **both** `--workspace` and `--token`
  (from any source) are resolved to non-empty values. If either is absent,
  carry-patch mode is skipped and nightshift runs normally (fix-pipeline only).
- A resolved `--hub-url` alone (without `--workspace` and `--token`) is
  silently ignored — it does **not** activate carry-patch mode and nightshift
  runs normally.
- If `--workspace` is resolved but `--token` is absent (from any source),
  nightshift exits with an error explaining the PAT is required.
- If `--hub-url` cannot be resolved from any source when carry-patch mode is
  active, nightshift exits with an error explaining the hub URL is required on
  first start.

### REQ-3: CWD validation sequence

When carry-patch mode is active (`workspace` slug and PAT both resolved), before
starting the daemon, execute the following steps inside an async helper function
invoked via `asyncio.run()` (see REQ-5 for context on async execution):

1. Call `hub_client.get_workspace(slug)`. If the call raises `HubAuthError`
   or `HubNotFoundError`, exit with a diagnostic error (invalid PAT or slug).
2. Verify `workspace.workspace_mode == "carry_patch"`. If not, exit with error.
3. Verify `workspace.clone_status == "ready"`. If not (`"pending"`,
   `"cloning"`, or `"failed"`), exit with error indicating the workspace clone
   is not ready.
4. Read the local origin URL via **blocking `subprocess.run()`** in CWD. Using
   the blocking form is acceptable here because this is a short-lived startup
   call and does not need to be non-blocking:
   - Command: `["git", "remote", "get-url", "origin"]`
   - Capture: `stdout=PIPE`, `stderr=PIPE`, `timeout=10` seconds
   - If the subprocess raises `FileNotFoundError` (git not installed or not in
     PATH), emit the exact message `'git is not installed or not in PATH;
     nightshift requires git'` and `sys.exit(1)`.
   - If the subprocess raises `subprocess.TimeoutExpired`, exit with a clear
     error message of the implementer's choosing (e.g. indicating that the
     `git remote get-url origin` command timed out).
   - If the return code is non-zero (not a git repo or no origin remote
     configured), exit with an error using the captured stderr as detail.
5. Compare the local origin URL (stripped of trailing whitespace) to
   `workspace.git_url`. If they do not match, exit with an error showing both
   URLs and instructing the operator to `cd` into the correct directory.
6. If all checks pass, emit `logging.info(...)` indicating validation succeeded,
   and continue to workspace variable setup.

All exit paths use `click.echo(..., err=True)` and `sys.exit(1)`.

**Distinction between step-4 failure modes:**
- `FileNotFoundError`: git binary is not present or not in PATH. Use the
  verbatim message above.
- Non-zero return code: git is installed but the CWD is not a git repository,
  or no `origin` remote is configured. Use captured stderr as detail.
- `subprocess.TimeoutExpired`: the git process did not complete within 10
  seconds. The error message wording is left to the implementer.

These three cases are tested separately: `test_git_not_installed_exits`,
`test_no_git_repo_exits`, and `test_git_timeout_exits` respectively.

**Note on event loop:** The async helper is invoked via `asyncio.run()` from
synchronous `main()`. In test environments using `pytest-asyncio`, tests that
invoke this helper should call it via `asyncio.run()` (or use a dedicated
synchronous test wrapper) rather than `await`-ing it directly, to avoid
`RuntimeError: This event loop is already running`.

### REQ-4: Default config generation on first start

When carry-patch mode passes CWD validation but `.nightshift/config.toml` does
**not** exist in the CWD, nightshift writes a default config. If
`.nightshift/config.toml` already exists, generation is skipped entirely — the
existing file is never overwritten.

**Generated content:**

All tunable `CarryPatchConfig` fields are written explicitly so the generated
file is self-documenting for operators:

```toml
[hub]
endpoint_url = "<resolved hub URL>"

[carry_patch]
enabled = true
workspace = "<slug>"
check_interval = 300
auto_resolve = true
rebuild_timeout = 600
rebuild_poll_interval = 5
max_resolve_retries = 2

[workspace]
integration_branch = "<workspace.integration_branch from API, or empty string if None>"
merge_strategy = "direct"
```

**Notes on the `[workspace]` section:**
- `merge_strategy` is an existing field on the `WorkspaceConfig` model in
  `agentfox.core.config`, typed as `Literal['direct', 'branch', 'pr']` with a
  default of `'direct'`. No new model or field is required; the generated config
  simply writes the default value explicitly so the file is self-documenting.

**Write behavior:**
- Encoding: UTF-8.
- Write strategy: write to a temporary file named `config.toml.tmp` in
  `.nightshift/`, then rename (atomic replace) to `config.toml` to avoid
  partial writes.
- The `.nightshift/` directory is created if it does not exist.
- If `.nightshift/` already exists but `config.toml` is absent, proceed
  normally with the atomic write.
- If writing fails for any reason (permissions, disk full, etc.), emit
  `logging.warning(...)` and continue — config generation is non-fatal.
- The PAT is **never** written to the config file.
- The written file is for future startups only. All values are already resolved
  in memory for the current session; nightshift does **not** reload the
  newly-written config into the live `AgentFoxConfig`.

### REQ-5: Workspace variable initialization

The CWD validation sequence (REQ-3) and workspace variable initialization are
extracted into an async helper function. The naming and exact signature of this
function are left to the implementer; the PRD describes its behavior and
responsibilities. A single `HubClient` instance — constructed as
`HubClient(endpoint_url=resolved_hub_url, pat=resolved_pat)` — is created once
inside this helper and reused for all API calls in REQ-3 and REQ-5. This
function is invoked via `asyncio.run()` at startup, before the synchronous
daemon loop (`DaemonRunner.run()`) begins. This is consistent with the existing
pattern in `app.py`, which already uses `asyncio.run(runner.run())` for the
daemon.

After CWD validation passes, call:

```python
await hub_client.set_variable(slug, "AUTO_REBUILD_AFTER_SYNC", "false")
await hub_client.set_variable(slug, "AUTO_REBUILD_AFTER_PUSH", "false")
```

These calls are idempotent (set_variable uses PATCH-then-POST upsert per
01_afhub_client REQ-4). **Any exception raised by `set_variable`** — including
connection errors, `HubForbiddenError` (403 scope missing), `HubConnectionError`,
or any other `HubError` subclass — is treated as **non-fatal**: emit
`logging.warning(...)` and continue. The daemon functions correctly without
these variables because it handles concurrent rebuild (409) gracefully.

## Files Modified

| File | Change |
|------|--------|
| `packages/agentfox/agentfox/core/config.py` | Add HubConfig, CarryPatchConfig, extend AgentFoxConfig |
| `packages/agentfox/agentfox/core/config_gen.py` | Add `[hub]` and `[carry_patch]` sections to the global default AgentFoxConfig comment-annotated TOML template (shown when no config file exists at all). This is separate from and complementary to the workspace-level `.nightshift/config.toml` written in REQ-4. |
| `packages/nightshift/nightshift/app.py` | Add --hub-url, --workspace, --token flags and startup validation |
| `packages/nightshift/pyproject.toml` | Add afhub as dependency |
| `packages/agentfox/pyproject.toml` | Add afhub as optional dependency |

## Test Files

| File | Package | Coverage |
|------|---------|----------|
| `packages/agentfox/tests/test_carry_patch_config.py` | agentfox | HubConfig, CarryPatchConfig defaults, Clamped clamping behavior, extra="ignore" backward compatibility |
| `packages/nightshift/tests/test_carry_patch_startup.py` | nightshift | CLI flag 3-tier resolution (REQ-2); CWD validation steps 1–6 (REQ-3); default config atomic write and non-fatal failure (REQ-4); set_variable non-fatal exception handling (REQ-5) |

### Requirement-to-Test Mapping

| Requirement | Test cases in `test_carry_patch_startup.py` |
|-------------|---------------------------------------------|
| REQ-2: flag resolution | `test_hub_url_from_flag`, `test_hub_url_from_env`, `test_hub_url_from_config`, `test_missing_token_exits`, `test_missing_hub_url_exits`, `test_no_workspace_no_token_skips_carry_patch`, `test_hub_url_only_skips_carry_patch` |
| REQ-2: workspace slug resolution | `test_workspace_from_flag`, `test_workspace_from_env` (`AF_WORKSPACE`), `test_workspace_from_config` |
| REQ-3 step 1 | `test_get_workspace_auth_error_exits`, `test_get_workspace_not_found_exits` |
| REQ-3 step 2 | `test_wrong_workspace_mode_exits` |
| REQ-3 step 3 | `test_clone_status_pending_exits`, `test_clone_status_failed_exits` |
| REQ-3 step 4 | `test_no_git_repo_exits`, `test_git_timeout_exits`, `test_git_not_installed_exits` |
| REQ-3 step 5 | `test_origin_url_mismatch_exits` |
| REQ-3 step 6 | `test_validation_success_logs_and_continues` |
| REQ-4 | `test_config_written_on_first_start`, `test_config_write_failure_is_nonfatal`, `test_config_not_overwritten_if_exists` |
| REQ-5 | `test_set_variable_exception_is_nonfatal`, `test_set_variable_called_with_correct_args` |

## Owner

nightshift (Michael Kuehl / candlekeep)
