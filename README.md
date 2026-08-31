## Night Shift — Fix-Only Daemon

Keep your codebase healthy while you sleep. Night Shift is a continuously-running
fix-only daemon that polls for `af:fix`-labelled GitHub issues and autonomously
processes them through a three-stage pipeline (triage → coder → reviewer).

```bash
# Start the fix daemon (Ctrl-C to stop gracefully)
nightshift
```

## Installation

Install the CLI:

```bash
curl -fsSL https://raw.githubusercontent.com/agent-fox-dev/agent-fox/refs/heads/main/install.sh | sh
```

## Development

The repository is a [uv workspace](https://docs.astral.sh/uv/concepts/workspaces/):

| Package | Description |
|---------|-------------|
| `packages/nightshift/` | Standalone CLI for the night-shift fix daemon (`nightshift` command) |
| `packages/afissues/` | Standalone platform/forge abstraction layer — protocol, GitHub integration, label definitions |
| `packages/afaudit/` | Standalone audit infrastructure — structured events, sinks, postmortem, traces (zero dependencies) |

The specification format library (`afspec`) and AI-powered spec creation tools
(`agentspec`, `spec` CLI) live in the separate
[agent-fox-dev/spec-format](https://github.com/agent-fox-dev/spec-format) repository.

```
af  ──▶  agentfox  ──▶  afspec (external: spec-format)
              │
              ├──▶  afissues
              └──▶  afaudit

nightshift ──▶ agentfox
```

```bash
uv sync                      # install all packages in editable mode
```

| Command | What it does |
|---------|-------------|
| `make check` | Lint + all tests (use before committing) |
| `make test` | All tests |
| `make test-unit` | Unit tests only |
| `make test-property` | Property-based tests only |
| `make test-integration` | Integration tests only |
| `make lint` | Check lint + formatting |
| `make format` | Auto-format code |

Changes are immediately reflected via editable install. To run the local
version explicitly (rather than a globally installed release):

```bash
uv run nightshift <command>
```

## Documentation

Full documentation lives in [`docs/`](docs/README.md):

- [CLI Reference](docs/cli-reference.md) — all commands, flags, and exit codes
- [Configuration Reference](docs/config-reference.md) — every `config.toml` option (all sections and fields)
- [Agent Archetypes](docs/architecture/03-execution-and-archetypes.md#agent-archetypes) — archetype registry, modes, convergence
- [Skills](docs/skills.md) — bundled Claude Code slash commands (`/afspec`)

For a deeper understanding of the system's internals — how specs become task
graphs, how agents are dispatched in parallel, how the knowledge store works,
and how nightshift processes fix issues — see the
[Architecture Guide](docs/architecture/README.md).

---
Built exclusively for Claude Code. And mostly by agent-fox.