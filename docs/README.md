# agent-fox Documentation

## How It Works

TBD

### Agent Archetypes

agent-fox uses a six-entry archetype registry with a mode system to divide
labor:

- **Coder** — the primary implementation agent. Receives the full spec
  context and implements one task group per session. Follows a test-first
  workflow: group 1 writes failing tests, subsequent groups implement code.
- **Reviewer** — a single archetype with four modes that cover all review
  roles:
  - *pre-review* — reviews spec quality before implementation. Checks
    completeness, consistency, feasibility, and security. Can block coding
    if critical findings exceed a threshold.
  - *drift-review* — validates spec assumptions against the actual codebase.
    Detects drift between what specs expect and what actually exists.
    Automatically skipped when the spec references no existing code.
  - *audit-review* — validates test quality against test spec contracts
    after tests are written. Triggers coder retries when tests are missing,
    weak, or misaligned with their specifications.
  - *fix-review* — reviews fix-mode patches (quality fixes, night-shift
    repairs) with full tool access and extended turn budget.
- **Curator** — performs post-implementation curation after coders and
  before the verifier. Read-only access with medium effort.
- **Verifier** — performs post-implementation verification. Runs the test
  suite, checks each requirement against acceptance criteria, and triggers
  coder retries when verification fails.
- **Gate** — lightweight checkpoint verification for mid-spec progress
  checks. Assigned automatically to `checkpoint` task groups.
- **Maintainer** — drives night-shift operations with three modes (hunt,
  fix-triage, extraction). Not assignable to spec tasks.

Review and verification archetypes can run multiple instances in parallel on
the same task, with outputs merged using mode-specific convergence strategies.
For full archetype details, see the
[Archetypes section](architecture/03-execution-and-archetypes.md#agent-archetypes)
in the Architecture Guide.

### Night Shift

For ongoing codebase health, the standalone `night-shift` CLI runs as a continuously
running fix-only daemon. It polls GitHub for issues labelled `af:fix` and
processes them through a three-stage pipeline (Triage, Coder, Reviewer in
fix-review mode). Each fix is implemented on an isolated branch and merged
back into the integration branch.

### Knowledge System

agent-fox maintains a persistent knowledge store that provides
institutional memory across sessions. Each new session starts with a fresh
context window but receives curated, relevant knowledge from prior sessions
so agents build on each other's work rather than starting blind.

The knowledge system tracks three categories of context: review findings
(active critical and major findings for the current task group), cross-group
findings (issues found in other groups of the same spec), and same-spec
session summaries (what earlier groups accomplished). Findings follow a
closed-loop lifecycle — when a finding is injected into a session and the
session completes, the finding is automatically superseded. This keeps the
active knowledge set current without manual intervention.

## Architecture

TBD

## Reference

TBD