---
spec_id: '01'
spec_name: afhub_client
title: Afhub Client
status: draft
created_at: '2026-09-01T10:12:38.140795+00:00'
updated_at: '2026-09-01T10:20:20.201249+00:00'
owner: ''
source: interactive
schema_version: 1
---
# afhub: Hub API Client Package

## Intent

Create a new Python package `packages/afhub/` that provides an async HTTP
client for the af-hub carry-patch REST API. This package is the sole
communication layer between nightshift and the hub server. It has no
nightshift-specific logic — it is a general-purpose API client that could
be used by any tool targeting af-hub.

## Goals

- Provide `HubClient`, an async HTTP client covering the full hub API surface
  (workspace, patch, rebuild, rerere, workspace variables, secrets).
- Provide Pydantic data models for all hub response schemas, tolerating unknown
  fields via `extra="ignore"`.
- Provide a typed error hierarchy that maps hub HTTP status codes and
  `error_type` discriminators to Python exceptions.
- Provide auth helpers that resolve a hub PAT and hub URL from CLI flags,
  environment variables, and config file, in priority order.
- Provide polling helpers that implement client-side wait loops for rebuild
  jobs and workspace clone readiness (the hub has no server-side blocking).
- Provide transient-error retry with exponential backoff (up to 3 retries,
  base 1 s, factor 2, cap 30 s).
- Achieve test coverage consistent with `packages/afissues/` patterns, using
  pytest + pytest-asyncio with `asyncio_mode=auto`.

## Non-Goals

- nightshift daemon logic, config models, or CLI flags (Spec 2)
- conflict monitoring or fix pipeline integration (Spec 3)
- hub workspace creation or admin operations
- Support for any af-hub API version other than v1 (`/api/v1/`)
- Rate-limit handling or pagination (the Hub API does not implement either)
- Version negotiation or multi-version compatibility logic
- A `list_variables` client method (the GET /vars list response is not needed by nightshift)

## Background

`af-hub` is a server that manages carry-patch workspaces used by the
nightshift daemon. This package, `afhub`, is the sole communication layer
between nightshift and af-hub. It is intentionally free of nightshift-specific
logic so that any tool targeting af-hub can use it.

The Hub REST API is documented in `docs/proposals/carry_patch_support.md`
under "Hub Integration Surface". All endpoints are prefixed with `/api/v1/`.
Authentication uses `Authorization: Bearer <pat>` on every request. The API
uses a standard error envelope: `{"error": {"code": N, "message": "...",
"error_type": "..."}}`. Additive field changes in the Hub API are handled
gracefully by Pydantic's `extra="ignore"` configuration. Breaking Hub API
changes would require a new client version.

**Owner:** nightshift (Michael Kuehl / candlekeep)

## Tech Stack

- Python 3.12+, asyncio
- `httpx` for async HTTP (already used by `afissues`); minimum version `httpx>=0.27`
- `pydantic` v2 for data models (already used by `afcore`); minimum version `pydantic>=2.0`
- `pytest` + `pytest-asyncio` (`asyncio_mode=auto`) for testing
- `unittest.mock.AsyncMock` / `MagicMock` for httpx mocking (no external mock
  library; matches the pattern in `packages/afissues/tests/`)
- Package layout mirrors `packages/afissues/`
- Lower-bound dependency versions match those used by `afissues` and `afcore`
  to avoid monorepo incompatibilities

## External API Surface

All endpoints are relative to the base URL `/api/v1`. Every request carries
`Authorization: Bearer <pat>`.

### Workspace Endpoints

| Method | Path | Success | Response Body |
|--------|------|---------|---------------|
| `POST` | `/workspaces` | 201 | `Workspace` |
| `GET` | `/workspaces` | 200 | `list[Workspace]` |
| `GET` | `/workspaces/:slug` | 200 | `Workspace` |
| `PATCH` | `/workspaces/:slug` | 200 | `Workspace` |
| `POST` | `/workspaces/:slug/sync` | 200 | `SyncResult` |
| `POST` | `/workspaces/:slug/reclone` | 200 | `Workspace` |
| `GET` | `/workspaces/:slug/patch-status` | 200 | `PatchStatusDashboard` |

### Patch Endpoints

All paths are relative to `/workspaces/:slug/patches`.

| Method | Path | Success | Response Body |
|--------|------|---------|---------------|
| `GET` | `` (list) | 200 | `list[Patch]` |
| `POST` | `` (single object body) | 201 | `Patch` |
| `POST` | `` (JSON array body) | 201 | `list[Patch]` |
| `PATCH` | `/:id` | 200 | `Patch` |
| `DELETE` | `/:id` | **204** | *(no body — return `None`)* |
| `POST` | `/:id/restore` | 200 | `Patch` |
| `POST` | `/reorder` | 200 | `list[Patch]` |

`add_patch` and `add_patches_batch` are two distinct client methods that each
call `POST /patches` — the former with a single-object body, the latter with a
JSON array. They are kept separate rather than unified by runtime type dispatch.

### Rebuild Endpoints

All paths are relative to `/workspaces/:slug`.

| Method | Path | Success | Response Body |
|--------|------|---------|---------------|
| `POST` | `/rebuild` | **202** | `RebuildJob` |
| `GET` | `/rebuilds` | 200 | `{"jobs": [...]}` → `list[RebuildJob]` |
| `GET` | `/rebuilds/:id` | 200 | `RebuildJob` |
| `DELETE` | `/rebuilds/:id` | 200 | `RebuildJob` (cancelled job) |
| `POST` | `/rebuilds/:id/requeue` | 200 | `RebuildJob` |
| `POST` | `/rebuilds/:id/rollback` | 200 | `{"previous_integration_head_sha": "<sha>"}` → `str` |
| `GET` | `/rebuild-preview` | 200 | `RebuildPreview` |

`rollback_rebuild` receives a JSON object `{"previous_integration_head_sha": "<sha>"}`,
extracts the value of that key, and returns it as a plain `str`.

### Rerere Endpoints

| Method | Path | Success | Response Body |
|--------|------|---------|---------------|
| `GET` | `/workspaces/:slug/rerere` | 200 | `{"resolutions": [...]}` → `list[RerereEntry]` |
| `DELETE` | `/workspaces/:slug/rerere/*pathspec` | **204** | *(no body — return `None`)* |

### Variable Endpoints

Variable response bodies are opaque and not parsed into a Pydantic model.
All mutating variable methods return `None`. `list_variables` is intentionally
omitted (not needed by nightshift).

| Method | Path | Success | Client Return |
|--------|------|---------|---------------|
| `POST` | `/workspaces/:slug/vars` | **201** | `None` |
| `PATCH` | `/workspaces/:slug/vars/:key` | 200 | `None` |
| `DELETE` | `/workspaces/:slug/vars/:key` | **204** | `None` |
| `GET` | `/workspaces/:slug/vars/resolved` | 200 | `dict[str, str]` |

### Secret Endpoints

| Method | Path | Success | Client Return |
|--------|------|---------|---------------|
| `POST` | `/workspaces/:slug/secrets` | **201** | `None` *(no useful body)* |

### Void / No-Content Responses

The following operations return HTTP 204 (or 201 with no useful body) and
their corresponding `HubClient` methods return `None`:

- `DELETE /patches/:id` → `remove_patch`
- `DELETE /rerere/*pathspec` → `forget_rerere`
- `DELETE /vars/:key` → `delete_variable`
- `POST /vars` → `create_variable`
- `PATCH /vars/:key` → `update_variable`
- `POST /secrets` → `create_secret`

### Error Envelope

All error responses follow:
```json
{"error": {"code": <int>, "message": "<str>", "error_type": "<str>"}}
```

### Failure Modes

| Condition | Behaviour |
|-----------|-----------|
| 401 Unauthorized | Raise `HubAuthError` |
| 403 Forbidden | Raise `HubForbiddenError` |
| 404 Not Found | Raise `HubNotFoundError` |
| 409 Conflict | Raise `HubConflictError` (store `error_type`) |
| 400 + known `error_type` | Raise matching typed subclass |
| 400 + unknown `error_type` | Raise `HubError` |
| Network timeout / connection error | Retry up to 3× then raise `HubConnectionError` |
| No rate limiting | Not applicable — Hub API has no rate limits |
| No pagination | Not applicable — all list endpoints return complete arrays |

## Requirements

### REQ-1: HubClient — workspace operations

`HubClient.__init__(self, endpoint_url: str, pat: str)` creates a single
shared `httpx.AsyncClient` instance for the lifetime of the `HubClient`.
This enables connection pooling and is consistent with httpx best practices.
An `aclose()` async method must be provided for explicit cleanup. `HubClient`
may also be used as an async context manager (`async with HubClient(...) as
client`) delegating to `aclose()` on exit.

All requests carry `Authorization: Bearer <pat>`.

The client provides:
- `get_workspace(slug: str) -> Workspace`
- `sync_workspace(slug: str, *, reset_to_upstream: bool = False) -> SyncResult`
- `get_patch_status(slug: str) -> PatchStatusDashboard`
- `reclone_workspace(slug: str) -> Workspace`

### REQ-2: HubClient — patch operations

The client provides two distinct methods for creating patches, kept separate
for explicitness (single-object creation is the common path; batch creation
is used for initial bootstrap):

- `list_patches(slug: str) -> list[Patch]`
- `add_patch(slug, branch_name, *, position=None, upstream_pr_url=None, description=None, skip_branch_check=False, if_not_exists=False) -> Patch` — sends a single-object JSON body; hub returns 201 with `Patch`
- `add_patches_batch(slug: str, patches: list[dict]) -> list[Patch]` — sends a JSON array body; hub returns 201 with `list[Patch]`
- `update_patch(slug: str, patch_id: str, **kwargs) -> Patch`
- `remove_patch(slug: str, patch_id: str) -> None` — hub returns 204 No Content
- `restore_patch(slug: str, patch_id: str) -> Patch`
- `reorder_patches(slug: str, patch_ids: list[str]) -> list[Patch]`

### REQ-3: HubClient — rebuild operations

The client provides:
- `submit_rebuild(slug, *, strategy=None, fail_mode=None) -> RebuildJob` — hub returns 202
- `get_rebuild(slug: str, rebuild_id: str) -> RebuildJob`
- `list_rebuilds(slug: str) -> list[RebuildJob]` — hub returns `{"jobs": [...]}`; client unwraps the array
- `cancel_rebuild(slug: str, rebuild_id: str) -> RebuildJob` — hub returns 200 with cancelled job
- `requeue_rebuild(slug: str, rebuild_id: str) -> RebuildJob`
- `rollback_rebuild(slug: str, rebuild_id: str) -> str` — hub returns `{"previous_integration_head_sha": "<sha>"}`;
  client extracts and returns the SHA string
- `get_rebuild_preview(slug: str) -> RebuildPreview`

### REQ-4: HubClient — rerere, variables, secrets

Variable response bodies are opaque (no Pydantic model). All variable-mutating
methods return `None`. `list_variables` is intentionally omitted.

The client provides:
- `list_rerere(slug: str) -> list[RerereEntry]` — hub returns `{"resolutions": [...]}`; client unwraps the array
- `forget_rerere(slug: str, pathspec: str) -> None` — hub returns 204 No Content
- `create_variable(slug: str, key: str, value: str) -> None` — `POST /vars`, hub returns 201; client returns `None`
- `update_variable(slug: str, key: str, value: str) -> None` — `PATCH /vars/:key`, hub returns 200; client returns `None`
- `set_variable(slug: str, key: str, value: str) -> None` — convenience upsert: tries `PATCH /vars/:key` first;
  if the hub returns 404, falls back to `POST /vars`. Returns `None`. Does not issue a prior GET.
- `delete_variable(slug: str, key: str) -> None` — `DELETE /vars/:key`, hub returns 204; client returns `None`
- `get_resolved_variables(slug: str) -> dict[str, str]`
- `create_secret(slug: str, key: str, value: str) -> None` — hub returns 201 with no useful body; client returns `None`

### REQ-5: Data models

All Pydantic models use `model_config = ConfigDict(extra="ignore")`.

No `Variable` model is defined. Variable endpoint responses are opaque; callers
use `get_resolved_variables` (which returns `dict[str, str]`) for read access.

#### Top-level models

##### `Workspace`

Required fields (no default): `slug`, `git_url`, `workspace_mode`, `status`,
`clone_status`, `sync_status`.

Optional fields (`= None`): `clone_error: str | None`, `sync_error: str | None`,
`sync_mode: str | None`, `upstream_url: str | None`, `upstream_head_sha: str | None`,
`head_sha: str | None`, `integration_branch: str | None`, `last_sync_at: str | None`.

##### `Patch`

Required fields (no default): `id`, `workspace_slug`, `branch_name`, `position`,
`status`, `added_at`, `updated_at`.

Optional fields (`= None`): `conflict_files: list[str] | None`,
`upstream_pr_url: str | None`, `description: str | None`, `deleted_at: str | None`.

##### `RebuildJob`

Fields: `id`, `status`, `created_at` (required). All other fields —
`strategy`, `error`, `patch_results`, `integration_head_sha`,
`previous_integration_head_sha`, `completed_at` — are optional with `None`
default (omitempty in hub response).

##### Other models

- **`PatchResult`**: `patch_id`, `branch_name`, `position`, `status`,
  `skipped_reason`, `new_head_sha`, `conflict_files`
- **`SyncResult`**: `patches_merged`, `rebuild_triggered`, `rebuild_job_id`,
  `force_push_detected`
- **`RerereEntry`**: `path`, `recorded_at`

#### `PatchStatusDashboard` and sub-models

- **`PatchStatusDashboard`**: `patches: list[PatchDetail]`, `summary: PatchSummary`
- **`PatchDetail`**: `id: str`, `branch_name: str`, `position: int`, `status: str`,
  `last_rebuild_result: str | None` (values: `success`, `conflict`, `skipped`, or `null`),
  `conflict_files: list[str] | None`, `description: str | None` (the patch description from the hub API; default `None`)
- **`PatchSummary`**: `total_patches: int`, `active: int`, `merged_upstream: int`,
  `conflict: int`, `disabled: int`, `total_rerere_resolutions: int` (default `0`)

#### `RebuildPreview` and sub-models

- **`RebuildPreview`**: `patch_results: list[RebuildPreviewPatchResult]`
- **`RebuildPreviewPatchResult`**: `patch_id: str`, `branch_name: str`,
  `position: int`, `status: str` (values: `would_succeed`, `would_conflict`),
  `tree_sha: str | None`, `conflict_files: list[str] | None`

### REQ-6: Error hierarchy

Hub errors follow the envelope `{"error": {"code": N, "message": "...", "error_type": "..."}}`.

Error classes:
- `HubError(Exception)`: base, carries `status_code`, `message`, `error_type`
- `HubAuthError(HubError)`: 401
- `HubForbiddenError(HubError)`: 403
- `HubNotFoundError(HubError)`: 404
- `HubConflictError(HubError)`: 409 — `error_type` stored for caller inspection
- `HubConnectionError(HubError)`: network-level failure
- `HubModeError(HubError)`: `error_type == "workspace_mode_mismatch"`
- `HubNoActivePatchesError(HubError)`: `error_type == "no_active_patches"`

When a 400 response carries a recognized `error_type`, the matching typed
subclass is raised. Otherwise `HubError` is raised.

### REQ-7: Auth helpers

`resolve_hub_pat(*, token_flag: str | None = None, env_var: str = "AF_HUB_TOKEN") -> str | None`
— returns PAT from flag, then env var, then `None`.

`resolve_hub_url(*, hub_url_flag: str | None = None, config_url: str = "", env_var: str = "AF_HUB_URL") -> str | None`
— returns URL from flag, then env var, then non-empty `config_url`, then `None`.

The `config_url` parameter is the caller's responsibility to populate: callers
must read their own config file and pass the resolved URL string. The
`resolve_hub_url` function does not read any config file directly; it only
evaluates the pre-resolved string. This keeps auth helpers free of
nightshift-specific config logic.

### REQ-8: Polling helpers

`poll_rebuild(client, slug, rebuild_id, *, timeout=600.0, interval=5.0) -> RebuildJob`
— polls `get_rebuild()` until status is one of `completed`, `failed`,
`dead_letter`, `cancelled`. Raises `TimeoutError` if `timeout` seconds elapse.
If `HubConnectionError` propagates from `get_rebuild()` (after the HTTP-layer
retries in REQ-9 are exhausted), the poll aborts immediately and surfaces the
error to the caller.

`poll_clone_ready(client, slug, *, timeout=300.0, interval=5.0) -> Workspace`
— polls `get_workspace()` until `clone_status` is `ready` or `failed`.
- If `clone_status` transitions to `failed`, raises
  `HubError(status_code=0, error_type="clone_failed", message=workspace.clone_error or "Workspace clone failed")`.
- If `HubConnectionError` propagates from `get_workspace()` (after HTTP-layer
  retries in REQ-9 are exhausted), the poll aborts immediately and surfaces the
  error to the caller.
- Raises `TimeoutError` if timeout elapses before a terminal clone status.

Both polling helpers call `asyncio.sleep(interval)` between polls. In tests,
`asyncio.sleep` is patched via `unittest.mock.patch('asyncio.sleep',
new_callable=AsyncMock)` in test fixtures — consistent with how `afissues`
tests handle sleep — so no real delays occur during the test suite.

### REQ-9: Transient retry

When `httpx` raises `ConnectTimeout`, `ReadTimeout`, or `ConnectError`
(the specific named subclasses, not the base `httpx.TimeoutException`),
`HubClient` retries the request up to 3 times with exponential backoff
(base 1 s, factor 2, cap 30 s). After exhausting retries, raises
`HubConnectionError`.

### REQ-10: API versioning

This client targets af-hub API **v1** exclusively. All endpoint paths are
prefixed with `/api/v1/`. No version negotiation logic is required. Additive
field changes in hub responses are tolerated by Pydantic's `extra="ignore"`.
Breaking hub API changes require a new client version.

## Package Layout

```
packages/afhub/
  pyproject.toml          # httpx>=0.27, pydantic>=2.0; matches afissues/afcore lower bounds
  afhub/
    __init__.py           # public exports (see below)
    client.py             # HubClient
    models.py             # Pydantic models
    errors.py             # Error hierarchy
    auth.py               # resolve_hub_pat, resolve_hub_url
    polling.py            # poll_rebuild, poll_clone_ready
    _http.py              # shared httpx session, retry logic
  tests/
    test_client.py
    test_models.py
    test_errors.py
    test_auth.py
    test_polling.py
```

### `__init__.py` Public Exports

The following symbols must be exported from `afhub.__init__`:

**Client:** `HubClient`

**Models:** `Workspace`, `Patch`, `RebuildJob`, `PatchResult`, `SyncResult`,
`RerereEntry`, `PatchStatusDashboard`, `PatchDetail`, `PatchSummary`,
`RebuildPreview`, `RebuildPreviewPatchResult`

**Errors:** `HubError`, `HubAuthError`, `HubForbiddenError`, `HubNotFoundError`,
`HubConflictError`, `HubConnectionError`, `HubModeError`, `HubNoActivePatchesError`

**Auth helpers:** `resolve_hub_pat`, `resolve_hub_url`

**Polling helpers:** `poll_rebuild`, `poll_clone_ready`

### `pyproject.toml` Requirements

- Package name: `afhub`
- Initial version: `0.1.0`
- Python: `>=3.12`
- Runtime dependencies: `httpx>=0.27`, `pydantic>=2.0`
- Dev/test dependencies: `pytest`, `pytest-asyncio`
- Lower bounds match those used by `afissues` and `afcore` in the monorepo
