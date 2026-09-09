# Packages

## Local workspace members

This repository contains two packages, managed as a
[uv workspace](https://docs.astral.sh/uv/concepts/workspaces/).

| Package | Description | Install |
|---------|-------------|---------|
| **[nightshift](nightshift/)** | CLI entry point — provides the `nightshift` command. | `uv pip install -e packages/nightshift` |
| **[afcore](afcore/)** | Core library — session runtime, knowledge store, workspace tools, and nightshift daemon. | `uv pip install -e packages/afcore` |

## External dependencies

Three packages live in the separate
[agent-fox-dev/af-python](https://github.com/agent-fox-dev/af-python)
repository and are pulled as git dependencies via `[tool.uv.sources]` in the
root `pyproject.toml`:

| Package | Description | Source |
|---------|-------------|--------|
| **afissues** | Platform/forge abstraction layer — protocol, GitHub/GitLab/Gitea integration, label definitions. | [`af-python/packages/afissues`](https://github.com/agent-fox-dev/af-python/tree/main/packages/afissues) |
| **afaudit** | Audit infrastructure — events, sinks, traces, and postmortem reporting. | [`af-python/packages/afaudit`](https://github.com/agent-fox-dev/af-python/tree/main/packages/afaudit) |
| **afhub** | Hub API client — authentication, polling, and GitHub REST API helpers. | [`af-python/packages/afhub`](https://github.com/agent-fox-dev/af-python/tree/main/packages/afhub) |

To modify `afissues`, `afaudit`, or `afhub`, open a PR against
[agent-fox-dev/af-python](https://github.com/agent-fox-dev/af-python).

## Dependency graph

```
nightshift  ──▶  afcore  ──▶  afissues  (external)
                   │
                   ├──▶  afaudit   (external)
                   │
                   └──▶  afhub     (external)
```

## Development

From the repo root:

```bash
uv sync          # install all packages in editable mode
make check       # lint + test everything
```
