# Night Shift Documentation

## Overview

Night Shift is an autonomous fix-only daemon that polls for `af:fix`-labelled
GitHub issues and processes them through a multi-stage pipeline: triage,
coder, reviewer. Each fix is implemented on an isolated branch and merged
back into the integration branch.

## Reference

- [Configuration Reference](config-reference.md) — every `config.toml`
  option (all sections and fields)
- [Model Tiers and Variants](model-escalation.md) — model selection and
  retry behavior

## Architecture

For a deeper understanding of the system's internals — how issues are
triaged, how agents are dispatched, how the knowledge store works — see
the [Architecture Guide](architecture/README.md).

- [Night Shift](architecture/04-night-shift.md) — the fix pipeline,
  issue selection, drain behavior
- [Knowledge System](architecture/05-knowledge-system-architecture.md) —
  institutional memory across sessions
- [Architecture Overview](architecture.md) — single-document summary
