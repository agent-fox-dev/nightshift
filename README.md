# Night Shift

Autonomous fix-only daemon that polls for `af:fix`-labelled GitHub issues and
processes them through a three-stage pipeline (triage → coder → reviewer).
Each fix is implemented on an isolated branch and squash-merged back into the
integration branch.

```bash
nightshift
```

## How It Works

1. **Poll** — Fetches open issues with the `af:fix` label from GitHub, GitLab,
   or Gitea.
2. **Triage** — A read-only agent analyzes the issue, traces the code path,
   and produces a structured root-cause report with acceptance criteria.
3. **Fix** — A coder agent implements the fix on an isolated branch. A reviewer
   agent validates the patch. If the review fails, the coder retries with
   feedback.
4. **Merge** — The fix branch is squash-merged into the integration branch.
   The issue is closed with the `af:fixed` label.

## Installation

Install the CLI:

```bash
curl -fsSL https://raw.githubusercontent.com/agent-fox-dev/nightshift/refs/heads/main/install.sh | sh
```

Or install from source:

```bash
uv sync
```

## Configuration

Night Shift reads its configuration from `.nightshift/config.toml`. Key
settings:

```toml
[platform]
type = "github"

[backend]
provider = "claude"

[night_shift]
issue_check_interval = 900    # seconds between polls
push_fix_branch = false       # push fix branches before merge
```

Set the `GITHUB_PAT` (or `GITLAB_TOKEN` / `GITEA_TOKEN`) environment variable
for platform authentication.

See [docs/config-reference.md](docs/config-reference.md) for all options.

## Development

This is a [uv workspace](https://docs.astral.sh/uv/concepts/workspaces/)
with five packages:

| Package | Description |
|---------|-------------|
| `packages/nightshift/` | CLI entry point (`nightshift` command) |
| `packages/afcore/` | Core library — session infrastructure, knowledge system, archetypes |
| `packages/afissues/` | Platform abstraction — GitHub, GitLab, Gitea integration |
| `packages/afaudit/` | Audit infrastructure — structured events, sinks, traces |
| `packages/afhub/` | Hub API client — authentication, polling, carry-patch helpers |

```bash
uv sync                      # install all packages
make check                   # lint + all tests
make test                    # all tests
```

## Documentation

- [docs/](docs/README.md) — full documentation index
- [docs/config-reference.md](docs/config-reference.md) — configuration reference
- [docs/architecture/](docs/architecture/README.md) — architecture guide
