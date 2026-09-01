# Packages

This monorepo contains the following packages, managed as a
[uv workspace](https://docs.astral.sh/uv/concepts/workspaces/).

| Package | Description | Install |
|---------|-------------|---------|
| **[nightshift](nightshift/)** | CLI entry point — provides the `nightshift` command. | `uv pip install -e packages/nightshift` |
| **[afcore](afcore/)** | Core library — session runtime, knowledge store, workspace tools, and nightshift daemon. | `uv pip install -e packages/afcore` |
| **[afissues](afissues/)** | Platform/forge abstraction layer — protocol, GitHub/GitLab/Gitea integration, label definitions. | `uv pip install -e packages/afissues` |
| **[afaudit](afaudit/)** | Audit infrastructure — events, sinks, traces, and postmortem reporting. | `uv pip install -e packages/afaudit` |
| **[afhub](afhub/)** | Hub API client — authentication, polling, and GitHub REST API helpers. | `uv pip install -e packages/afhub` |

## Dependency graph

```
nightshift  ──▶  afcore  ──▶  afissues
                   │
                   └──▶  afaudit
                   │
                   └──▶  afhub
```

`afissues`, `afaudit`, and `afhub` have no internal dependencies and can be used independently.

## Development

From the repo root:

```bash
uv sync          # install all packages in editable mode
make check       # lint + test everything
```
