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

## 8. PR Feedback Loop

When `merge_strategy = "pr"`, Night Shift opens pull requests instead of
squash-merging directly. The **pr-feedback** work stream then monitors those
PRs for CI failures and reviewer-requested changes, automatically iterating
on its own work.

### Activation

The pr-feedback stream is registered only when both conditions are met:

- `merge_strategy = "pr"` in `[workspace]`
- `platform.type` is not `"none"`

It polls on a separate timer (`pr_check_interval`, default 900 seconds).

### Per-PR Processing

Each issue labelled `af:pr` is processed through a four-step state machine:

1. **Parse tracking comment.** Scans the issue's comments for a tracking
   pattern that records the PR number and current attempt count. Skips the
   issue if no tracking comment is found (the PR was not created by Night
   Shift).

2. **Check PR state.** If the PR was merged, the issue is labelled `af:fixed`,
   `af:pr` is removed, and the issue is closed. If the PR was closed without
   merge, `af:pr` is removed for manual triage. If the PR is still open,
   processing continues.

3. **Check CI status.** Queries the platform's check/status API. If checks
   are in progress or queued, the PR is skipped (wait for next cycle). If any
   check failed or timed out, a feedback iteration is triggered. If all checks
   pass, processing continues to the review step.

4. **Check reviews.** Only reached when CI has passed. If the latest active
   review requests changes, a feedback iteration is triggered. Otherwise the
   PR is healthy and awaits human merge — no action taken.

### Feedback Iteration

When CI fails or a reviewer requests changes:

1. Check the retry limit (`max_pr_retries`, default 2). If exceeded, post a
   warning comment and stop iterating — the PR needs human attention.
2. Set up a git worktree on the PR's source branch.
3. Compute affected files via `git diff` against the integration branch.
4. Collect feedback context: CI failure logs for failed checks, or review
   comments rendered as markdown for reviewer-requested changes.
5. Build a synthetic `TriageResult` and `InMemorySpec`, then run a coder
   session through `FixPipeline._run_coder_session()` with the feedback
   injected as context.
6. Auto-commit changes and force-push to the PR branch.
7. Post an updated tracking comment (incrementing the attempt count).
8. Clean up the worktree.

### Labels

| Label | Meaning |
|-------|---------|
| `af:pr` | PR is open and being monitored by the feedback loop |
| `af:fixed` | PR was merged successfully |

---

## 9. Engine Lifecycle

### Startup

Validates platform configuration, initializes the platform client and
knowledge store, cleans up stale merge locks, writes a PID file. The first
fix cycle fires immediately.

### Event Loop

The `DaemonRunner` schedules work streams on independent timers. Two streams
are built-in:

- **fix-pipeline** — polls for `af:fix` issues (default every 15 minutes). A
  drain loop re-polls after each fix until zero issues remain.
- **pr-feedback** — polls for `af:pr` issues (default every 15 minutes). Only
  active when `merge_strategy = "pr"`.

### Cost and Session Limits

Night Shift enforces a cost ceiling at 50% of the configured maximum as a
safety margin for autonomous operation. Both cost and session limits trigger
graceful shutdown.

### Graceful Shutdown

Two-stage signal handling: first SIGINT/SIGTERM prevents new work and drains
in-flight sessions; second signal exits immediately.

---

## 10. Workspace Isolation

Each session gets its own `git worktree` on a feature branch
(`fix/{issue_number}-{slug}`). Worktrees share the object store but have
independent working trees. A two-layer merge lock (asyncio + file lock with
stale detection) serializes all merge operations.
