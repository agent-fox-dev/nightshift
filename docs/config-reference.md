# Night Shift Configuration Reference

Night Shift reads configuration from `.nightshift/config.toml` (local,
project-level) or `~/.nightshift/config.toml` (global, user-level). Local
config takes precedence — when a local config exists, the global config is
ignored entirely. If neither exists, a minimal local config is auto-created
with default values for reference.

### General behavior

- **Symlinks rejected.** `config.toml` must be a regular file, not a symlink.
- **Out-of-range values clamped.** Numeric values outside their valid bounds
  are silently clamped to the nearest bound. A warning is logged.
- **Unknown keys ignored.** All sections silently ignore unknown keys.

## Table of Contents

- [backend](#backend)
- [orchestrator](#orchestrator)
- [security](#security)
- [theme](#theme)
- [platform](#platform)
- [knowledge](#knowledge)
  - [knowledge.provider](#knowledgeprovider)
- [archetypes](#archetypes)
  - [archetypes.overrides](#archetypesoverrides)
- [models](#models)
  - [models.registry](#modelsregistry)
  - [models.tier_defaults](#modelstier_defaults)
- [pricing](#pricing)
- [night_shift](#night_shift)
- [workspace](#workspace)
- [caching](#caching)
- [hub](#hub)
- [carry_patch](#carry_patch)
  - [Carry-Patch Authentication](#carry-patch-authentication)

---

## backend

Selects the AI backend provider used for agent sessions.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `provider` | str | `"claude"` | Backend provider: `"claude"`, `"deepagents"`, or `"google"` |

```toml
[backend]
provider = "claude"
```

---

## orchestrator

Controls session retries, timeouts, and budgets.

| Field | Type | Default | Bounds | Description |
|-------|------|---------|--------|-------------|
| `max_retries` | int | `2` | >= 0 | Maximum retries per task group |
| `session_timeout` | int | `45` | >= 1 | Per-session timeout in minutes |
| `max_cost` | float\|null | `null` | -- | Hard cost ceiling for the entire run (null = no limit) |
| `max_sessions` | int\|null | `null` | -- | Maximum total sessions in a run (null = no limit) |
| `max_budget_usd` | float | `20.0` | >= 0 | Per-session spend cap in USD; `0` = unlimited |

```toml
[orchestrator]
max_budget_usd = 10.0
session_timeout = 45
max_retries = 3
```

---

## security

Controls the bash command allowlist and Claude Code permission mode.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `bash_allowlist` | list[str]\|null | `null` | Full replacement allowlist (null uses the built-in list) |
| `bash_allowlist_extend` | list[str] | `[]` | Additional commands appended to the built-in allowlist |
| `permission_mode` | str | `"bypassPermissions"` | Claude Code permission mode (see below) |

### permission_mode

Controls the Claude Code CLI permission enforcement mode. Accepted values:

| Value | Description |
|-------|-------------|
| `"bypassPermissions"` | Skip all permission prompts (default). **Cannot be used when running as root (UID 0).** |
| `"acceptEdits"` | Auto-accept file edits but prompt for other tool use. **Required for root environments.** |
| `"plan"` | Plan-only mode — no tool execution. |
| `"default"` | Full interactive permission prompts. |

**Root environments (Docker, CI as root):** Claude Code rejects
`bypassPermissions` when the process runs as root (`UID 0`) for security
reasons. If Night Shift is running as root, set `permission_mode = "acceptEdits"`.
The daemon will exit at startup with a clear error if it detects root + `bypassPermissions`.

Note that `"acceptEdits"` may require additional allowlist configuration
(via `bash_allowlist` or `bash_allowlist_extend`) to avoid interactive prompts
for non-edit tool calls.

```toml
[security]
bash_allowlist_extend = ["my-custom-tool", "deploy.sh"]

# Required when running as root (e.g. in Docker):
# permission_mode = "acceptEdits"
```

---

## theme

Rich text styles for terminal output. Values use
[Rich markup](https://rich.readthedocs.io/en/stable/style.html) syntax.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `header` | str | `"bold #ff8c00"` | Style for the startup banner header |
| `muted` | str | `"dim"` | Style for secondary/muted text |

```toml
[theme]
header = "bold blue"
```

---

## platform

Issue-tracker integration. Supports GitHub, GitLab, and Gitea.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `type` | str | `"none"` | Platform type: `"none"`, `"github"`, `"gitlab"`, or `"gitea"` |
| `url` | str | `""` | Issue tracker base URL (inferred for GitHub/GitLab; **required** for Gitea) |

### Authentication

| Platform | Environment variable | Default host |
|----------|---------------------|--------------|
| `github` | `GITHUB_PAT` | `github.com` |
| `gitlab` | `GITLAB_TOKEN` | `gitlab.com` |
| `gitea`  | `GITEA_TOKEN` | *(none — `url` required)* |

```toml
[platform]
type = "github"
```

---

## knowledge

Knowledge store configuration. Persists session outcomes, review findings,
and other artifacts across sessions.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `store_path` | str | `".nightshift/knowledge.duckdb"` | Path to the DuckDB knowledge store file |
| `provider` | table | -- | Knowledge provider configuration (see below) |

```toml
[knowledge]
store_path = ".nightshift/knowledge.duckdb"
```

### knowledge.provider

Controls retrieval limits for context injected into session prompts.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `max_items` | int | `10` | Max total retrieval items |
| `max_cross_group_items` | int | `3` | Max cross-group retrieval items |
| `max_cross_spec_items` | int | `3` | Max cross-spec drift items |
| `max_drift_age_days` | int\|null | `30` | Max age in days for drift findings; `null` disables pruning |
| `max_summary_items` | int | `5` | Max session summaries injected as context |

```toml
[knowledge.provider]
max_items = 10
max_cross_group_items = 3
```

---

## archetypes

Per-archetype configuration overrides.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `overrides` | dict[str, table] | `{}` | Per-archetype config overrides (see below) |

### archetypes.overrides

Each override is keyed by archetype name:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `model_tier` | str\|null | `null` | Model tier: SIMPLE, STANDARD, ADVANCED |
| `max_turns` | int\|null | `null` | Max turns (0 = unlimited) |
| `thinking_mode` | str\|null | `null` | `adaptive` or `disabled` |
| `effort` | str\|null | `null` | `low`, `medium`, `high`, `xhigh`, or `max` |
| `allowlist` | list[str]\|null | `null` | Bash command allowlist override |
| `max_budget_usd` | float\|null | `null` | Per-archetype budget ceiling in USD |
| `compaction` | bool\|null | `null` | Enable server-side context compaction |
| `modes` | dict[str, table] | `{}` | Per-mode overrides (same fields) |

```toml
[archetypes.overrides.coder]
model_tier = "ADVANCED"

[archetypes.overrides.reviewer.modes.fix-review]
model_tier = "ADVANCED"
max_turns = 120
```

---

## models

Config-driven model registry and tier-default overrides. Allows you to adopt
new Anthropic model IDs or reassign tier mappings without waiting for a
nightshift release. Entries here overlay the hardcoded defaults — omitting a
model or tier leaves the built-in value unchanged.

### models.registry

Additional model entries keyed by model ID. Each entry declares the tier
for that model.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `tier` | str | required | Model tier: `SIMPLE`, `STANDARD`, or `ADVANCED` |

### models.tier_defaults

Maps tier names to model IDs. Values must exist in the merged registry
(hardcoded built-ins plus any entries in `models.registry` above). A
misconfigured value raises `ConfigError` at startup.

| Key | Type | Description |
|-----|------|-------------|
| `SIMPLE` | str | Default model for the SIMPLE tier |
| `STANDARD` | str | Default model for the STANDARD tier |
| `ADVANCED` | str | Default model for the ADVANCED tier |

```toml
# Adopt claude-fable-5-1 as the new ADVANCED default without a release

[models.registry.claude-fable-5-1]
tier = "ADVANCED"

[models.tier_defaults]
ADVANCED = "claude-fable-5-1"
```

You can also redirect a tier to an existing built-in without adding a registry
entry:

```toml
# Use Sonnet as the ADVANCED default (cheap experiments)
[models.tier_defaults]
ADVANCED = "claude-sonnet-4-6"
```

---

## pricing

Custom per-model pricing for cost tracking.

| Sub-field | Type | Default | Description |
|-----------|------|---------|-------------|
| `input_price_per_m` | float | `0.0` | USD per million input tokens |
| `output_price_per_m` | float | `0.0` | USD per million output tokens |
| `cache_read_price_per_m` | float | `0.0` | USD per million cache-read tokens |
| `cache_creation_price_per_m` | float | `0.0` | USD per million cache-creation tokens |

Built-in defaults cover `claude-haiku-4-5`, `claude-sonnet-4-6`,
`claude-opus-4-5`, and `claude-opus-4-6`.

```toml
[pricing.models.claude-sonnet-4-6]
input_price_per_m = 3.00
output_price_per_m = 15.00
```

---

## night_shift

Daemon configuration for polling and fix processing.

| Field | Type | Default | Bounds | Description |
|-------|------|---------|--------|-------------|
| `issue_check_interval` | int | `900` | >= 60 | Seconds between issue-tracker checks |
| `pr_check_interval` | int | `900` | >= 60 | Seconds between PR feedback poll cycles |
| `push_fix_branch` | bool | `false` | -- | Push fix branches to origin before harvest |
| `max_parallel` | int | `1` | 1--8 | Maximum issues processed concurrently |
| `max_pr_retries` | int | `2` | 0--10 | Maximum PR feedback iterations before escalating to a human |

`pr_check_interval` and `max_pr_retries` control the **PR feedback loop** —
an autonomous work stream that monitors PRs created by Night Shift (labelled
`af:pr`) for CI failures and reviewer-requested changes. When either is
detected, Night Shift re-runs a coder session with the failure context
injected, then force-pushes the fix to the PR branch. This loop only runs
when `merge_strategy = "pr"`. See [Architecture § 8](architecture.md#8-pr-feedback-loop)
for the full workflow.

```toml
[night_shift]
issue_check_interval = 1800
max_parallel = 3
push_fix_branch = true
max_pr_retries = 3
```

---

## workspace

Branch integration configuration.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `integration_branch` | str | `"main"` | Git branch used as the integration target for merges |
| `merge_strategy` | str | `"direct"` | `"direct"` (squash-merge), `"branch"` (keep locally), or `"pr"` (open PR) |

When `merge_strategy = "pr"`, Night Shift opens pull requests instead of
merging directly and activates the **PR feedback loop** — an autonomous work
stream that monitors those PRs for CI failures and reviewer-requested changes.
See [Architecture § 8](architecture.md#8-pr-feedback-loop) for details.

```toml
[workspace]
integration_branch = "main"
merge_strategy = "direct"
```

---

## caching

Prompt caching configuration.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `cache_policy` | str | `"DEFAULT"` | `NONE`, `DEFAULT` (5-min TTL), or `EXTENDED` (1-hour TTL) |

```toml
[caching]
cache_policy = "DEFAULT"
```

---

## hub

Hub API configuration for carry-patch mode.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `endpoint_url` | str | `""` | Hub API base URL |

```toml
[hub]
endpoint_url = "https://hub.example.com/api/v1"
```

---

## carry_patch

Carry-patch mode configuration. Automates conflict resolution for
organizations maintaining a fork of an upstream repository. Requires
a running af-hub instance and the `[hub]` section to be configured.

| Field | Type | Default | Bounds | Description |
|-------|------|---------|--------|-------------|
| `enabled` | bool | `false` | -- | Enable carry-patch mode |
| `workspace` | str | `""` | -- | Hub workspace slug |
| `check_interval` | int | `300` | >= 60 | Seconds between conflict checks |
| `auto_resolve` | bool | `true` | -- | Auto-resolve detected conflicts |
| `rebuild_timeout` | int | `600` | >= 1 | Max seconds to wait for hub rebuild |
| `rebuild_poll_interval` | int | `5` | >= 2 | Seconds between rebuild poll checks |
| `max_resolve_retries` | int | `2` | 0--10 | Max automatic conflict-resolve retries |

```toml
[carry_patch]
enabled = true
workspace = "my-workspace"
check_interval = 300
```

### Carry-Patch Authentication

Carry-patch mode requires three credentials to connect to the hub:

| Credential | CLI flag | Environment variable | Config file | Description |
|------------|----------|---------------------|-------------|-------------|
| Hub PAT | `--token` | `AF_HUB_TOKEN` | *(none)* | Personal access token for hub API authentication |
| Hub URL | `--hub-url` | `AF_HUB_URL` | `hub.endpoint_url` | Hub API base URL |
| Workspace slug | `--workspace` | `AF_WORKSPACE` | `carry_patch.workspace` | Hub workspace identifier |

#### Resolution priority

Each credential is resolved with a three-tier priority order — the first
non-empty value wins:

1. **CLI flag** (`--token`, `--hub-url`, `--workspace`)
2. **Environment variable** (`AF_HUB_TOKEN`, `AF_HUB_URL`, `AF_WORKSPACE`)
3. **Config file** (`hub.endpoint_url`, `carry_patch.workspace`) — where
   applicable

> **Security note:** The hub PAT (`AF_HUB_TOKEN` / `--token`) has **no
> config-file equivalent** and is never written to disk. It must be supplied
> via the `--token` CLI flag or the `AF_HUB_TOKEN` environment variable to
> prevent accidental persistence of secrets. The auto-generated
> `.nightshift/config.toml` (created during carry-patch bootstrap) deliberately
> omits the PAT.

#### Error: missing PAT with workspace configured

If a workspace slug is configured (via `--workspace`, `AF_WORKSPACE`, or
`carry_patch.workspace` in config) but no PAT is available from any source
(no `--token` flag, no `AF_HUB_TOKEN` environment variable), Night Shift
exits immediately at startup with exit code 1 and the message:

```
Error: PAT is required for carry-patch mode (--token / AF_HUB_TOKEN)
```

To resolve this error, supply the hub token via either:
- `--token <your-pat>` on the command line, or
- `export AF_HUB_TOKEN=<your-pat>` in the environment.

Similarly, if both a workspace slug and PAT are present but no hub URL is
available, Night Shift exits with:

```
Error: hub URL required for carry-patch mode (--hub-url / AF_HUB_URL)
```

---

*See the [Architecture Guide](architecture/README.md) for how Night Shift
uses these settings.*
