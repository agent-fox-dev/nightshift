# Night Shift Architecture

How Night Shift turns GitHub issues into committed fixes through autonomous,
knowledge-accumulating coding sessions.

---

## 1. Overview

Night Shift is a fix-only daemon that polls for `af:fix`-labelled issues and
processes each through a multi-stage pipeline: triage analysis, coder session,
reviewer session, and harvest. It reuses the session infrastructure from the
`agentfox` library — agents running in isolated git worktrees — but generates
lightweight in-memory specs from issues rather than reading human-authored
spec files.

### Core Principles

- **Isolation by default.** Each fix runs in its own git worktree on its own
  branch. Concurrent fixes share no mutable workspace state.
- **Knowledge compounds over time.** Review findings and session summaries
  accumulate in DuckDB and feed into future sessions.
- **Graceful degradation everywhere.** If knowledge retrieval or ingestion
  fails, the session proceeds unaffected.

---

## 2. Persistent State in DuckDB

All system state lives in `.agent-fox/knowledge.duckdb` — an embedded DuckDB
file. Key tables:

- **`session_outcomes`** — One row per session attempt with status, tokens,
  cost, duration, touched files, commit SHA, archetype.
- **`review_findings`** / **`drift_findings`** — Quality findings from review
  sessions, classified by severity.
- **`session_summaries`** — Enriched summaries from completed sessions,
  carrying rejected approaches, gotchas, and assumptions.
- **`finding_injections`** — Tracks which findings were served to which
  sessions, enabling automatic supersession.
- **`audit_events`** — Structured log of significant operations.

Schema evolution uses forward-only, idempotent migrations applied at open time.

---

## 3. The Fix Pipeline

Each issue passes through these stages:

### 3.1 Triage Analysis

A Maintainer agent in `fix-triage` mode (STANDARD tier, read-only access)
explores the codebase, traces the code path, and produces a structured JSON
report: root cause summary, affected files, acceptance criteria, and
complexity assessment.

### 3.2 In-Memory Spec Construction

The pipeline generates a lightweight `InMemorySpec` from the issue (number,
title, task prompt, system context, branch name) without writing spec files
to disk. When triage produces acceptance criteria, a full `afspec` Spec
object is built and rendered to markdown for the coder and reviewer prompts.

### 3.3 Coder-Reviewer Loop

Retries up to `max_retries + 1` iterations (default 4):

1. **Coder** (fix mode, STANDARD tier) implements the fix on an isolated branch.
   On retry, previous review feedback is injected.
2. **Reviewer** (fix-review mode, ADVANCED tier) reviews the patch and produces
   a PASS/FAIL verdict with per-criterion assessments.

If PASS, the loop exits. If FAIL with retries remaining, feedback feeds into
the next coder iteration.

### 3.4 Harvest and Integration

After success: auto-commit sweep, optional branch push, squash merge into the
integration branch. Merge conflicts are resolved by a dedicated merge agent.

### 3.5 Issue Updates

- **Successful merge**: `af:fixed` label, issue closed with fix branch link.
- **No commits**: `af:no-change` label, issue stays open.
- **Failure**: failure comment with branch name for manual recovery.

---

## 4. Issue Selection and Ordering

Issues are fetched from the configured platform (GitHub, GitLab, Gitea) with
the `af:fix` label. When three or more issues exist, batch analysis determines
processing order using:

- Text reference parsing (explicit "depends on #N" patterns)
- Platform cross-references
- AI batch triage (Maintainer in `hunt` mode)

Topological sort produces a safe order; cycles are broken by removing edges
to the oldest issue.

---

## 5. Agent Archetypes

Night Shift uses four archetypes from the registry:

| Archetype | Mode | Tier | Role |
|-----------|------|------|------|
| Maintainer | hunt | SIMPLE | Batch dependency analysis |
| Maintainer | fix-triage | STANDARD | Read-only issue triage |
| Coder | fix | STANDARD | Fix implementation |
| Reviewer | fix-review | ADVANCED | Patch review |

All tiers are configurable via `[archetypes.overrides]` in `config.toml`.

---

## 6. The Knowledge System

The knowledge system provides institutional memory across sessions through
a protocol-based interface (`KnowledgeProvider`):

- **`retrieve()`** — Called before each session. Returns relevant review
  findings, cross-group findings, and same-spec session summaries.
- **`ingest()`** — Called after each session. Supersedes injected findings
  and stores session summaries.

Findings follow a closed-loop lifecycle: created by reviewers, injected into
coder sessions, automatically superseded when the session completes. See
[Knowledge System Architecture](architecture/05-knowledge-system-architecture.md)
for details.

---

## 7. Session Lifecycle

Each fix runs as a session with four phases:

1. **Prepare** — Create an isolated git worktree, build system and task prompts.
2. **Execute** — Run the Claude agent via the SDK.
3. **Harvest** — Squash merge the feature branch into the integration branch.
4. **Assess** — Parse review findings, ingest knowledge, record outcomes.

---

## 8. Engine Lifecycle

### Startup

Validates platform configuration, initializes the platform client and
knowledge store, cleans up stale merge locks, writes a PID file. The first
fix cycle fires immediately.

### Event Loop

The `DaemonRunner` runs the fix-pipeline stream on a timer (default 15
minutes). A drain loop re-polls after each fix until zero `af:fix` issues
remain.

### Cost and Session Limits

Night Shift enforces a cost ceiling at 50% of the configured maximum as a
safety margin for autonomous operation. Both cost and session limits trigger
graceful shutdown.

### Graceful Shutdown

Two-stage signal handling: first SIGINT/SIGTERM prevents new work and drains
in-flight sessions; second signal exits immediately.

---

## 9. Workspace Isolation

Each session gets its own `git worktree` on a feature branch
(`fix/{issue_number}-{slug}`). Worktrees share the object store but have
independent working trees. A two-layer merge lock (asyncio + file lock with
stale detection) serializes all merge operations.
