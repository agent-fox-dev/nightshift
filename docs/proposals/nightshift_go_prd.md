# Night Shift in Go: A Rewrite on AgentKit and Hub

**Author:** [Platform Engineering]
**Date:** 2026-09-07
**Status:** Draft
**Version:** 1.3.0 — the Issue Service is part of hub
**Supersedes:** the Python implementation in `packages/nightshift` + `packages/afcore`
**Companion:** [`issue_service_prd.md`](issue_service_prd.md) — the Issue Service API this daemon depends on

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [How Night Shift Works Today](#2-how-night-shift-works-today)
3. [Findings: What Is Broken, Duplicated, or Obsolete](#3-findings-what-is-broken-duplicated-or-obsolete)
4. [Goals and Non-Goals](#4-goals-and-non-goals)
5. [Target Architecture](#5-target-architecture)
6. [Feature Requirements](#6-feature-requirements)
7. [API and Type Sketches](#7-api-and-type-sketches)
8. [Configuration](#8-configuration)
9. [Non-Functional Requirements](#9-non-functional-requirements)
10. [Migration Plan](#10-migration-plan)
11. [Risks](#11-risks)
12. [Resolved Decisions](#12-resolved-decisions)
13. [Appendix A: Deleted Surface](#appendix-a-deleted-surface)
14. [Appendix B: Behaviour Change Ledger](#appendix-b-behaviour-change-ledger)

---

## 1. Executive Summary

### Overview

Night Shift is an autonomous daemon that polls an issue tracker for
`af:fix`-labelled issues and drives each one through triage → code → review →
integrate, unattended. It works. It is also ~19,000 lines of Python
application code carrying ~100,000 lines of tests, wrapping a closed-source
subprocess, maintaining a 26-migration analytics database whose features were
deleted, and re-implementing — locally and worse — most of what the `hub`
service already exposes over REST.

This proposal is a complete rewrite in Go, built on two foundations that did
not exist when Night Shift was written:

- **`coder` (AgentKit)** — a dependency-free Go agent SDK whose PRD names
  Night Shift as its canonical consumer, down to the MCP resource URIs
  (`nightshift://issues/{number}/triage-report`) and the skill discovery
  directory (`~/.nightshift/skills`). It owns the loop, the tool system, the
  provider abstraction, model catalog and pricing, session persistence,
  skills, plugins and bidirectional MCP.
- **`hub`** — a Go service that already owns agent session lifecycle, token
  usage and cost aggregation, audit ingestion (events, traces, tool calls,
  tool errors, session outcomes, postmortems), a unified audit query API,
  transcript reconstruction, an SSE event stream, retention, Prometheus
  metrics, workspace-scoped secrets and variables, a git server, a durable
  job queue, and the whole carry-patch machinery.

### The shape of the change

```
today                                    proposed

nightshift (python, click)               nightshift (single static Go binary)
  └─ afcore                                └─ imports agentkit-go
      ├─ session/backends/claude ──┐           ├─ core, catalog, provider/*
      ├─ session/backends/deepagents│  spawn   ├─ tools, skills, session, mcp
      ├─ session/backends/google_adk│  claude  └─ plugins
      ├─ core/client (raw anthropic)┘  code    └─ hub client (REST)
      ├─ knowledge/ (DuckDB, 26 mig)              ├─ sessions + usage + cost
      ├─ engine/state (DuckDB)                    ├─ audit ingest + query
      ├─ afaudit (JSONL + DuckDB)                 ├─ patches / rebuilds / rerere
      └─ afissues, afhub, afspec                  └─ secrets / vars
```

Four model-calling paths collapse to one. Two audit stores and one analytics
database collapse to *hub plus a small local ledger*. A Python 3.14 + `uv` +
Node-runtime container collapses to a static binary and `git`.

### Why now

1. **The subprocess backend is the largest single source of defect.** Six of
   the last twenty commits are workarounds for it: root/`bypassPermissions`
   rejection, `can_use_tool` being silently shadowed, a `ProcessError(143)`
   race on teardown, feature-detection by poking attributes onto an SDK
   options object, transport retries that buffer the entire stream, and a
   `cache_policy` setting that is accepted, logged, and then ignored.
   AgentKit removes the subprocess entirely.

2. **Cost is inferred, not measured, and the arithmetic is wrong in three
   places.** Night Shift recomputes USD from a hand-maintained price table
   while the CLI's own `total_cost_usd` is read only to build an error
   string (`session/backends/claude.py:395`). AgentKit's `catalog` +
   `provider.ComputeCost` own pricing, cache-token netting and service tiers,
   pinned by a four-wire conformance suite.

3. **Hub already stores everything Night Shift stores locally**, with
   pagination, retention, cross-run query, transcript reconstruction and live
   SSE. Night Shift writes a local DuckDB nobody can query from anywhere else.

4. **The knowledge store is mostly rubble.** Twenty modules are banned by a
   `ruff` rule (`pyproject.toml`, `flake8-tidy-imports.banned-api`) because
   spec 114 deleted them, but their migrations still run and their tables are
   still created. The live retrieval path is three `SELECT`s. The dependency
   cost of that is `duckdb`, `sentence-transformers`, `scikit-learn` and
   nineteen `tree-sitter` grammars.

### What does not change

The product. Poll for `af:fix`, triage, fix on an isolated branch, review,
integrate, close the issue, report cost. The label vocabulary, the branch
naming (`fix/<n>-<slug>`), the issue-comment protocol and the config file
location (`.nightshift/config.toml`) all survive.

---

## 2. How Night Shift Works Today

This section is derived from the code, not the docs. File references are to
the current `main`.

### 2.1 Startup (`packages/nightshift/nightshift/app.py`)

1. Load `.nightshift/config.toml` (or global+local pair) into a Pydantic
   `AgentFoxConfig` with thirteen sections.
2. Render an ANSI banner.
3. If a hub workspace slug is resolvable (`--workspace` / `AF_WORKSPACE` /
   `carry_patch.workspace`), run `_carry_patch_startup`: fetch the workspace,
   assert `workspace_mode == "carry_patch"` and `clone_status == "ready"`,
   shell out to `git remote get-url origin` and string-compare it against
   `workspace.git_url`, write a default `config.toml` if absent, and set two
   workspace variables to `"false"`.
4. Pre-flight: refuse to run as UID 0 with `permission_mode =
   "bypassPermissions"`; validate the platform is not `"none"`; check the PID
   file; make a live API call to validate model access; construct the platform
   client and check credentials; create the seven required labels; clear a
   stale merge lock; purge last run's audit files; open the DuckDB knowledge
   store and run 26 migrations.
5. Build `NightShiftEngine`, `SharedBudget`, `DaemonRunner`, install SIGINT/
   SIGTERM handlers, `asyncio.run(runner.run())`.

### 2.2 The daemon (`afcore/nightshift/daemon.py`, `streams.py`)

`DaemonRunner` launches one `asyncio.Task` per enabled `WorkStream`, sorted by
a fixed priority list. Each task loops on a **50 ms tick**
(`_TICK = 0.05`), comparing `time.monotonic()` against `last_run +
stream.interval`, refreshing a spinner every 600 ticks. Budget is checked
after each cycle. A second signal raises `SystemExit(130)`.

Three streams exist:

| Stream | Method | Enabled when | Default interval |
|---|---|---|---|
| `fix-pipeline` | `engine._drain_issues` | `platform.type != "none"` | 900 s |
| `pr-feedback` | `engine._check_open_prs` | `workspace.merge_strategy == "pr"` | 900 s |
| `carry-patch` | `CarryPatchMonitor.run_cycle` | `carry_patch.enabled` and a `HubClient` | `check_interval` |

### 2.3 The fix stream (`afcore/nightshift/engine.py`)

`_drain_issues` loops up to fifty times, each iteration:

1. `list_issues_by_label("af:fix", sort="created", direction="asc")`, sorted
   again locally by number, filtered against a `seen` set and a per-process
   `_processed_issues` set.
2. Build dependency edges: regex over issue bodies for
   `depends on|blocked by|after|requires #N`, plus GitHub timeline
   `cross-referenced` events.
3. If the batch has ≥ 3 issues, run **AI batch triage** (`maintainer:hunt`,
   SIMPLE tier) returning a processing order, extra edges and
   `(keep, obsolete)` supersession pairs. Supersession pairs are *closed
   immediately* and labelled `af:fixed`.
4. Topologically sort, then dispatch up to `night_shift.max_parallel`
   (1–8) issues concurrently through a `ParallelGraph`, re-checking each
   issue's freshness before starting.
5. After each successful fix, run a **staleness check** over remaining
   issues (AI + a GitHub re-fetch) and close whatever it names obsolete.

### 2.4 The per-issue pipeline (`afcore/nightshift/fix_pipeline.py`, 1,945 lines)

`FixPipeline.process_issue`:

1. Reject an empty issue body with a comment.
2. Build an `InMemorySpec` (branch `fix/<n>-<slug>`, sanitized title/body).
3. `ensure_integration_branch` then `create_worktree` at
   `.nightshift/worktrees/fix-issue-<n>/0`.
4. Post "Starting fix session on branch …".
5. **Triage** — a `maintainer:fix-triage` session with a read-only allowlist,
   prompted to emit bare JSON. Parsed by `parse_triage_output` through a
   fuzzy JSON extractor; posts a triage comment.
6. Gather context: prior attempts (`session_outcomes` DuckDB query) +
   knowledge retrieval (review findings, drift, cross-group, cross-spec,
   summaries).
7. **Coder/reviewer loop** (`coder_reviewer.py`): render the triage into an
   in-memory `afspec` `Spec` and back out to markdown, run `coder:fix`, check
   the outcome status (transport error → retry without consuming an attempt;
   timeout/failure → skip the reviewer, consume an attempt), run
   `reviewer:fix-review` (which is instructed to run `make check`), parse the
   verdict JSON with a one-shot retry on parse failure, post a review comment,
   and loop until `PASS` or `orchestrator.max_retries` is exhausted.
8. Auto-commit anything the sessions left uncommitted.
9. **Integrate**, four-way on `workspace.merge_strategy` plus carry-patch:
   - `carry_patch.enabled` → push branch, `hub.add_patch`, `submit_rebuild`,
     `poll_rebuild`
   - `branch` → leave the branch, comment
   - `pr` → push, `create_pr`, store the number, emit an `af:pr-tracking`
     comment, apply `af:pr`, remove `af:fix`
   - `direct` → `harvest()`: squash-merge into the integration branch under a
     file lock, spawn a *merge agent* on conflict, push with rebase-retry
10. Handle the result: `merged` closes the issue and adds `af:fixed`;
    `no_changes` adds `af:no-change` and leaves it open; `error` comments.

### 2.5 Sessions (`afcore/session/session.py`, `backends/`)

`run_session` resolves model/security/turns/thinking/effort/compaction/cache
policy from a three-step cascade (mode override → archetype override →
registry default), builds a `PreToolUse` allowlist hook, and streams messages
from a `Backend`. `ClaudeBackend` constructs `ClaudeAgentOptions`, opens a
`ClaudeSDKClient` — which spawns the Claude Code CLI — and maps SDK messages
to three canonical types. Two other backends (`deepagents`, `google_adk`)
exist. A fourth model path, `nightshift_ai_call`, hits the Anthropic SDK
directly for batch triage and staleness.

### 2.6 Archetypes and profiles

`ARCHETYPE_REGISTRY` holds five archetypes (`coder`, `reviewer`, `verifier`,
`gate`, `maintainer`) with per-mode overrides for tier, turns, thinking,
effort, allowlist and injection. Prompts are markdown files under
`_templates/profiles/` resolved in four steps
(project+mode → package+mode → project → package), frontmatter stripped, and
concatenated with a `## Context` block.

### 2.7 Persistence

- **`knowledge.duckdb`** — 26 migrations; live tables are `session_outcomes`,
  `tool_calls`, `tool_errors`, `review_findings`, `drift_findings`,
  `audit_events`, `runs`, `finding_injections`, `session_summaries`. Dead
  tables from removed specs are still created.
- **`.agent-fox/*.jsonl`** — audit event files, purged at startup.
- **`.nightshift/daemon.pid`**, **`.nightshift/merge.lock`**.

---

## 3. Findings: What Is Broken, Duplicated, or Obsolete

Each finding is stated with its evidence. These drive the requirements in §6.
Findings are numbered in discovery order, not section order, so references
from issues and commits stay stable.

### 3.1 Correctness defects

**F-1 — The daemon stops at half its budget.**
`NightShiftEngine._check_cost_limit` returns `True` when
`remaining < max_cost * 0.5` (`engine.py:188`). With
`orchestrator.max_cost = 20`, Night Shift refuses to dispatch new work once
it has spent $10. The docstring calls this "conservative"; it is a 50 %
under-run of an explicitly configured limit.

**F-2 — The shared budget is not actually shared safely.**
`SharedBudget.add_cost_async` exists, takes the `asyncio.Lock`, and is called
from nowhere. `EngineWorkStream.run_once` calls the unlocked
`add_cost` (`streams.py:125`). With three streams and `max_parallel > 1`,
cost accumulation is a read-modify-write race.

**F-3 — The staleness check is asked to reason about a diff it is never
given.** `engine.py:501` passes `""` for `fix_diff` with the comment `# diff
not available in current implementation`. The prompt then renders
`The fix diff: (no diff available)` and asks a SIMPLE-tier model which of the
user's remaining issues the invisible fix has made obsolete — and the daemon
**closes** whatever it names. The "GitHub API verification" step only
confirms the issue is still open, which verifies nothing about obsolescence.

**F-4 — Creating a worktree force-deletes the remote branch.**
`create_worktree` unconditionally runs
`git push origin --delete <branch>` (`workspace/worktree.py:272`). Under
`merge_strategy = "pr"`, re-processing an issue whose pull request is open
deletes that PR's head branch on the remote, which GitHub renders as a closed,
unmergeable PR.

**F-5 — The carry-patch monitor mutates the primary checkout without the merge
lock.** `CarryPatchMonitor._resolve_conflict` runs `fetch_remote` and
`checkout_branch` against `Path.cwd()` and then runs a coder session *in the
repository root*. `harvest()` concurrently checks out the integration branch
in the same tree under `MergeLock`. The carry-patch path takes no lock. With
both streams enabled — the documented carry-patch deployment — this corrupts
the working tree.

**F-6 — `af:no-change` issues are re-processed forever.** `_handle_result`
leaves the issue open with `af:fix` still applied. `_run_issue_check` polls
`list_issues_by_label("af:fix")` with no exclusion for `af:no-change`, so the
next daemon start re-triages, re-codes and re-reviews an issue already known
to produce no changes. `_processed_issues` only guards within one process.

**F-7 — A failing issue has no attempt ceiling.** There is no persisted
per-issue attempt counter and no `af:failed` label. An issue that fails for a
systemic reason is retried every `issue_check_interval`, indefinitely.

**F-8 — `_drain_issues` fails open on a platform error.** When the re-poll
raises, the method logs a warning and `return True` — "drain succeeded, no
issues remain". A GitHub outage is indistinguishable from an empty queue.

**F-9 — The reviewer's verdict can override a red build.** The reviewer is
*asked in prose* to run `make check`. Nothing reads an exit code. A model that
returns `overall_verdict: "PASS"` on a broken build causes a squash-merge into
the integration branch and a push.

**F-10 — Streaming is not streaming.** `ClaudeBackend.execute` accumulates the
entire message stream into `buffered` and yields it only after the stream
closes cleanly (`backends/claude.py:246–301`). Every `ToolUseMessage`-derived
audit event and trace for a session lands at once, at the end.

**F-11 — `cache_policy` is a no-op.** The only shipping backend logs it and
relies on "the CLI's internal caching" (`backends/claude.py:108`). The config
key, its enum, its validator and its cascade are decoration.

**F-12 — `Path.cwd()` is the repo root in sixteen places.** The pipeline, the
platform factory, the carry-patch monitor and the knowledge ingest all read
the process working directory rather than a value threaded from startup. Any
future `chdir`, or launching from a subdirectory, silently changes behaviour.

**F-13 — PR-feedback loses carry-patch wiring.** `_check_open_prs` constructs a
`FixPipeline` with no `hub_client` and no `workspace_slug`, so a feedback
iteration inside a carry-patch workspace silently takes the local-harvest
path.

**F-14 — Dead parameter.** `format_tracking_comment(pr_number, attempt,
pr_url, message)` never uses `pr_url`.

**F-28 — The "issue closed between poll and dispatch" guard is dead code.**
`_run_one` re-fetches an issue before starting work and tests
`getattr(fresh, "state", "open") == "closed"` (`engine.py:403`). `IssueResult`
(`afissues/protocol.py:17-27`) has no `state` field and no implementation sets
one, so the expression always evaluates to `"open"` and the branch is never
taken. The code knows: the comment directly above it says the check is
"forward-compatible with `IssueResult` gaining a `state` field in the future".
The label half of the same guard does work. Consequence: an issue a human
closes while it waits in the dispatch queue still gets a full triage → coder →
reviewer run. This is the direct motivation for `state` being a required field
on the new API's issue schema (§5.8, and REQ-IS-3.1 in the
[Issue Service PRD](issue_service_prd.md)).

### 3.2 Architectural cost

**F-15 — Four independent model-calling paths.** `ClaudeBackend` (subprocess),
`DeepAgentsBackend`, `GoogleADKBackend`, and `nightshift_ai_call` (raw
Anthropic SDK). Only the first is exercised in production; all four have to
agree on token accounting, cost, retries and error classification, and they do
not.

**F-16 — The subprocess backend's workaround tax.** In the current file:
a root/UID-0 pre-flight guard, a non-retryable root-restriction error
classifier, `PreToolUse` hooks because `can_use_tool` is shadowed under
`bypassPermissions` (issue #8), a `response_stream.aclose()` in a `finally`
to avoid `ProcessError(143)` on teardown (issue #215), and three
`try: options.X = … except TypeError` blocks doing capability detection
against a struct.

**F-17 — Pricing is hand-maintained, duplicated, and thin.**
`MODEL_REGISTRY` hardcodes four model IDs; `PricingConfig` carries a default
price table; `calculate_cost` is called from `emit_auxiliary_cost` with a
fallback to a *second* default table when the first yields zero. There is no
notion of a service tier, and the cache-token netting rule is implicit.
Meanwhile the CLI's own reported `total_cost_usd` is parsed only into an error
string.

**F-18 — The knowledge store is 90 % dead weight.** Twenty modules are
`ruff`-banned as "Removed in spec 114". Migrations still create
`memory_facts`, `memory_embeddings`, `entity_graph`, `entity_edges`,
`fact_entities`, `sleep_artifacts`, `plan_nodes`, `plan_edges`, `plan_meta`,
`complexity_assessments`, `blocking_history`, `learned_thresholds`,
`gotchas`, `errata`, `errata_index`, `adr_entries`. The live retrieval path is
three parameterised `SELECT`s. The runtime cost is `duckdb` (CGo),
`sentence-transformers`, `scikit-learn` and nineteen `tree-sitter` grammars.

**F-19 — Everything is local-only.** The knowledge DB, the audit JSONL, the
run/session tables and the PID file live in the checkout. There is no
cross-host view, no retention, no query API, no metrics endpoint and no health
check — while `hub` ships all of them (§2 of `hub/docs/api.md`).

**F-20 — An entire external dependency for string formatting.**
`build_afspec_from_triage` inflates 2–5 acceptance criteria into a full
`afspec.Spec` (Requirements + TestSpec + Tasks + PRDDocument) so that
`render_inmemory_spec_sections` can render markdown, inside a `try/except`
that falls back to a five-line renderer. `afspec` is a separate git
repository.

**F-21 — Structured output by prose contract plus a fuzzy parser.**
`review_parser.py` is 949 lines of JSON-block extraction, wrapper-key fuzzy
matching, key normalization, legacy-markdown fallbacks and failure dumps,
because the contract with the model is a sentence ("Output bare JSON only").
There is a dedicated reviewer re-run purely for parse failures.

**F-22 — Two prompt systems, one used.** `_templates/profiles/*.md` is a
bespoke four-step resolver with `{{ placeholder }}` string replacement done by
`str.replace` at `engine.py:790`. AgentKit ships a skills system with
manifests, progressive disclosure, tiered discovery, a trust gate, an
archetype filter and shadowing diagnostics.

**F-23 — Prompt-injection surface is only half-covered.**
`sanitize_prompt_content` is applied to the issue title and body, and to
triage prompt previews. It is *not* applied to triage output, reviewer
evidence, prior-attempt error messages, hub patch descriptions or rerere paths
— all of which are concatenated into later prompts.

**F-24 — The PR feedback loop re-implements git.** `_setup_feedback_worktree`
and `_cleanup_feedback_worktree` use `subprocess` and `shutil` directly rather
than the async `workspace/git.py` used everywhere else.

**F-25 — The scheduler busy-waits.** Twenty wakeups per second per stream,
forever, to compare two monotonic clock readings, plus a tick counter to
decide when to redraw a spinner.

**F-26 — Magic constants that should be policy.**
`_MAX_DRAIN_ITERATIONS = 50`, `_MAX_PR_CHECKS = 5`,
`MAX_TRANSPORT_RETRIES = 2`, `_MAX_ERROR_LENGTH = 500`, the
`len(issues) >= 3` batch-triage threshold, the 500-char body preview and the
3000-char diff preview.

**F-27 — The container is enormous.** `hub/containers/agents/Containerfile`
installs Python 3.14 in a `uv` venv, plus `pi`, plus `opencode`, plus Claude
Code (a Bun-compiled binary embedding a Node runtime), plus `PyGithub`,
`python-gitlab`, `psutil`, `pylint`.

### 3.3 Capabilities available and unused

| Capability | Available in | Night Shift today |
|---|---|---|
| In-process agent loop, no subprocess | `coder` root package | spawns a CLI |
| Model catalog: context window, pricing, thinking-level map, tier clamping | `coder/catalog` | hardcoded 4-model registry |
| Correct cost, cache-token netting, service tiers | `coder/provider/cost.go` | local table, 3 call sites |
| Four wire APIs behind one conformance suite | `coder/provider/*` | 3 half-built backends |
| Path-contained file tools, layered gitignore, SSRF-guarded fetch | `coder/tools` | delegated to the CLI |
| Schema-constrained tool output | `core.ConstrainedSampling` | prose + 949-line parser |
| One authorization boundary with an unguarded-shell failure | `coder/policy.go` | allowlist inside a hook |
| Skills: manifests, progressive disclosure, trust gate | `coder/skills` | markdown profiles |
| Durable JSONL session log + damage-tolerant resume | `coder/session` | none (sessions are ephemeral) |
| Compaction with checkpoint-before-estimate | `coder/compaction.go` | a bool passed to the CLI |
| Subagent delegation with fresh history + budget slices | `coder/subagent.go` | none |
| MCP client and server | `coder/mcp` | none |
| Plugin categories + registry + manifest + lint | `coder/plugins` | none |
| Agent sessions, token usage, cost aggregation | `hub` `/api/v1/sessions*` | local DuckDB |
| Audit events / traces / tool calls / errors, batched, idempotent | `hub` `/runs/:run_id/*` | local JSONL + DuckDB |
| Unified audit query, transcript reconstruction, SSE | `hub` `/api/v1/audit`, `/events` | none |
| Retention worker, Prometheus `/metrics` | `hub` | none |
| Workspace-scoped secrets and variables | `hub` `/secrets`, `/vars/resolved` | environment variables |
| Patch status dashboard, rebuild preview, rerere listing | `hub` carry-patch API | partially used |
| Durable job queue with backoff and dead-letter | `hub/internal/jobqueue` | none |

---

## 4. Goals and Non-Goals

### Goals

**G1 — One agent runtime.** Every model call in Night Shift goes through
AgentKit's loop. No subprocess, no second SDK, no direct provider HTTP.

**G2 — Correct money.** Cost comes from AgentKit's catalog and provider
arithmetic, per request, and is enforced as a hard budget before dispatch —
not a 50 % heuristic after the fact.

**G3 — Hub is the system of record.** Sessions, usage, audit events, traces,
tool calls, tool errors, session outcomes and postmortems go to hub. Night
Shift keeps only the local state it needs to survive a hub outage.

**G4 — Deterministic gates beat model opinions.** A build that fails cannot be
merged by a reviewer that says PASS.

**G5 — Structured output by construction.** Agents end phases by calling a
schema-validated tool, not by emitting prose that a parser has to rescue.

**G6 — Safe concurrency.** A single lock discipline over the shared `.git`,
per-issue worktrees, and `context.Context` cancellation throughout.

**G7 — Bounded failure.** Every issue has an attempt ceiling, a terminal
label, and a dead-letter path. No infinite retry loops.

**G8 — Operable.** `/healthz`, `/metrics`, structured JSON logs, an MCP
control surface, and a live event stream.

**G9 — A single static binary.** `nightshift` plus `git`. Nothing else.

**G10 — Feature parity on the paths that are used**, verified by a parity
harness against a fixture repository, before cutover.

### Non-Goals

**NG1** — Not a spec-execution orchestrator. The `verifier` and `gate`
archetypes, the plan/task-graph tables and `af code` belong to a different
product; they are not carried over.

**NG2** — Not a Python-to-Go transliteration. Where the Python design is a
workaround for the subprocess or for a missing library, the workaround is
deleted rather than ported.

**NG3** — Not a replacement for hub. Night Shift is a hub *client*.

**NG4** — Not a general agent framework. That is AgentKit.

**NG5** — No local vector search, embeddings, or code-graph extraction in
v1. Those features were already deleted from the Python implementation; only
their migrations survive.

**NG6** — No interactive TUI. A readable TTY renderer and `--json` NDJSON.

---

## 5. Target Architecture

### 5.1 Package layout

```
nightshift/
  cmd/nightshift/          main; cobra command tree
  internal/config/         TOML load, layering, validation, defaults
  internal/daemon/         supervisor, streams, scheduler, budget ledger, signals
  internal/pipeline/       the per-issue state machine
  internal/agentrt/        AgentKit wiring: archetypes → AgentConfig, tools, policy
  internal/archetype/      registry, modes, cascade resolution
  internal/issues/         Issue Service API client — the only issue path
  internal/hubclient/      hub REST client + offline spool
  internal/repo/           git: worktrees, locks, harvest, integration, push retry
  internal/carrypatch/     conflict monitor
  internal/prfeedback/     PR CI/review feedback loop
  internal/ledger/         local SQLite: issue attempts, claims, retry counters
  internal/telemetry/      slog, metrics, event fan-out, TTY + NDJSON renderers
  internal/mcpsrv/         nightshift:// MCP server surface
  _skills/                 embedded built-in skills (triage, coder, reviewer, carry-patch)
```

Dependencies: `github.com/agentfox/agentkit-go` (and subpackages),
`modernc.org/sqlite`, `github.com/spf13/cobra`, `github.com/BurntSushi/toml`,
`github.com/prometheus/client_golang`. The same set hub already uses, minus
DuckDB — so no CGo.

### 5.2 The session boundary

Every archetype session is one `agentkit.Agent`:

```
archetype + mode
      │
      ├─ catalog.ResolveModel(spec)          → *core.Model  (window, cost, thinking map)
      ├─ tools.All / a narrowed ToolPolicy   → []core.Tool
      ├─ policy for this archetype           → core.BeforeToolCall
      ├─ skills.LoadForSession(archetype,…)  → PromptBlocks
      ├─ StopAny(StopAfterTurns, StopOverBudget)
      ├─ Middleware: Retry, Budget, Tracing  → hub trace ingest
      ├─ Hooks: OnTurnEnd → hub /sessions/:id/usage; OnAudit → hub events
      └─ SessionStore: .nightshift/sessions/<run>/<node>.jsonl
                 │
            agent.Stream(ctx, task) → core.EventStream → renderer + telemetry
```

The `Backend` protocol, the three adapters, `_QueryExecutionState`,
`with_timeout`, the transport-retry loop and the canonical message types all
disappear: AgentKit already owns them.

### 5.3 Phase contracts as tools

Each phase terminates by calling a schema-constrained tool. The tool's
`ToolResult.Terminate` ends the run (REQ-TOOL-13), and the arguments *are* the
result — already validated, already typed.

```
triage   → submit_triage(summary, affected_files[], criteria[{id,description,
                          preconditions,expected,assertion}], complexity)
review   → submit_review(verdicts[{criterion_id, verdict, evidence}],
                          overall_verdict, summary)
carrypatch → submit_resolution(files_resolved[], strategy, notes)
```

`ConstrainedSampling{Type: ConstrainJSONSchema, Strict: StrictPrefer}` is set
on each; the `schema` package builds the shape with no reflection. This
deletes `review_parser.py`, `json_extraction.py`, the wrapper-key fuzzy
matcher, the parse-failure dump and the reviewer parse-retry path.

### 5.4 The gate

The reviewer produces findings. A **verification gate** produces the verdict:

```
gate.Run(ctx, worktree) → GateResult{Passed bool, Command string,
                                     ExitCode int, Output string}
```

The command comes from config (`[gate] command = "make check"`), runs in the
worktree with a timeout and a captured, bounded output. `Passed` is
`ExitCode == 0`. Integration requires `gate.Passed && review.Overall == PASS`;
a failing gate short-circuits the reviewer entirely and feeds its output back
to the coder as the next attempt's context. If no gate command is configured,
the reviewer's verdict stands alone and the daemon logs that it is running
ungated — once, at startup, not per issue.

### 5.5 Persistence split

| Data | Home | Rationale |
|---|---|---|
| Agent transcripts | AgentKit `SessionStore` JSONL, per run | resumable, damage-tolerant, local |
| Audit events, traces, tool calls/errors, session outcomes | hub, batched | queryable across hosts, retained, streamed |
| Token usage and cost | hub `sessions` + `token_usage` | one aggregation point; `/cost` endpoint |
| Run postmortem | hub `/runs/:run_id/postmortem` | one per daemon run |
| Issue attempt ledger, in-flight claims, retry counters | local SQLite (`modernc.org/sqlite`) | must work with hub unreachable |
| Carry-forward findings and session summaries | hub (§6.7) | shared across hosts and runs; hub owns the lifecycle |
| Config | `.nightshift/config.toml` + hub workspace vars | vars for operational toggles |
| Secrets (provider API keys) | hub secrets, env fallback | only `AF_HUB_TOKEN` stays on the box |

**Not DuckDB.** DuckDB requires CGo, which is what forces hub's own
`CGO_ENABLED=1` build. Night Shift's local queries are point lookups and small
scans; `modernc.org/sqlite` is pure Go, already a hub dependency, and keeps the
binary static.

**Offline spool.** Every hub write goes to an append-only local spool first and
is drained by a background sender with exponential backoff. Hub being down
degrades observability, never the fix pipeline (F-19's inverse: hub must not
become a new single point of failure).

### 5.6 Concurrency and locking

- One `repo.Lock` (flock on `.nightshift/repo.lock`) guards **every** operation
  that touches the shared `.git`: worktree add/remove/prune, branch create/
  delete, integration checkout, squash-merge, push, and carry-patch checkout.
  This closes F-5 and the unlocked races in `_integrate_fix`.
- Per-issue work happens only inside that issue's worktree.
- Carry-patch conflict resolution gets its own worktree
  (`.nightshift/worktrees/carry-patch/<patch-id>`) instead of checking out in
  the primary tree.
- `context.Context` replaces `is_shutting_down`; cancellation propagates into
  AgentKit runs, git subprocesses and HTTP calls.
- Streams are `time.Ticker` goroutines. No polling tick (F-25).
- The repo root is resolved once at startup and threaded as a value (F-12).

### 5.7 Budget

```
Ledger { limit, spent, reserved }

before dispatching issue N:
    estimate = Σ over planned sessions of archetype.MaxBudgetUSD
    if spent + reserved + estimate > limit: do not dispatch; log; stop the stream
    reserve(estimate)
after the issue completes:
    release(estimate); commit(actual)   // actual from AgentKit provider cost
```

Per-session enforcement is `StopOverBudget` inside AgentKit; daemon-level
enforcement is the reservation above. The 50 % margin (F-1) is deleted: the
reservation model is what the margin was crudely approximating. All ledger
mutation is under one mutex (F-2).

### 5.8 Issues are a service, code is plain git

Night Shift does not talk to GitHub, GitLab or Gitea. Two separate paths
replace today's single `afissues` platform layer, and the split is the whole
decision:

- **Code is plain git.** Clone, fetch, branch, worktree, checkout, merge,
  rebase, push and tag are ordinary git against the repository's own remote,
  authenticated by ordinary git credentials. No forge REST API participates in
  any operation on code. This is already how integration works; it now becomes
  exclusive and is stated as a constraint rather than an implementation detail.
- **Issues are a service, and that service is hub.** Everything about issues,
  labels, comments and pull requests goes through one HTTP contract — the
  **Issue Service API** — served by hub. Night Shift ships exactly one client
  and knows nothing about GitHub.

Adapting a real tracker moves *behind* the API, into its implementer. That is
where it belongs: the GitHub/GitLab/Gitea divergences that made `afissues`
three parallel implementations are tracker concerns, and Night Shift does not
need to hold three of them to fix a bug.

> **The contract is specified separately.**
> [`docs/proposals/issue_service_prd.md`](issue_service_prd.md) is the
> normative definition — endpoints, schemas, capability model, error codes,
> conformance suite and hub's implementation of it. It is a standalone
> document because the service has
> consumers other than this daemon and must be implementable without reading
> this PRD.
>
> This document specifies only Night Shift's side of it: what the client must
> do (§6.10), how it is wired (§7.7), how it is configured (§8), and what its
> absence costs (R-8).

#### The issue service is part of hub

It is not a second service, a second endpoint or a second credential. Night
Shift opens **one** connection to hub and uses it for everything:

| | |
|---|---|
| **Endpoint** | `[hub] endpoint_url`. The issue API lives on hub's existing `/api/v1` surface beside sessions, audit, patches, secrets and variables. There is no `[issues] endpoint_url`. |
| **Authentication** | hub's existing bearer credentials — a PAT, an API key or an admin token. `Authorization: Bearer <token>` exactly as every other hub call. There is no `AF_ISSUES_TOKEN`. |
| **Scopes** | `issues:read` / `issues:write`, granted on the same token that already carries `sessions:*` and `audit:*`. A PAT needs an explicit grant; API keys and admin tokens have implicit access, matching hub's existing rule. |
| **Project identity** | the contract's `{project}` **is** the hub workspace slug — `[hub] workspace`. One name, not a workspace slug plus a separate project id. |
| **Authorization** | hub's workspace ACL, unchanged. A workspace-scoped token addresses only its own workspace; an archived workspace returns `409`, as it does elsewhere in hub. |
| **Audit** | issue mutations emit hub audit events under the existing `hub.*` taxonomy, so issue activity appears in the same unified query as everything else. |

The contract stays a separate specification because it is testable
independently of both hub and this daemon, and because hub is not its only
possible implementer — only its actual one. Night Shift is configured against
hub and nothing else.

**This makes hub a hard dependency.** Before this decision the daemon could
run with hub absent and keep telemetry local; now, no hub means no issues,
which means no work. REQ-NS-HUB-05 states what degrades and what stops.

Two properties of that contract are load-bearing here and are called out so
the dependency is visible rather than buried in a reference:

- **`exclude_label` is server-side.** REQ-NS-POLL-01 drops `af:fixed`,
  `af:no-change` and `af:failed` at the query, not after fetching them.
- **`Issue.state` is a required field.** Without it the dispatch freshness
  guard is dead code, which is exactly the state of the current implementation
  (F-28).

---

## 6. Feature Requirements

### 6.1 Daemon and lifecycle

- **REQ-NS-DAEMON-01** — `nightshift` with no subcommand runs the daemon.
  `nightshift fix <issue>` runs one issue and exits. `nightshift status`,
  `nightshift mcp-server`, `nightshift validate-plugins`, `nightshift version`.
- **REQ-NS-DAEMON-02** — Single-instance enforcement uses an OS advisory lock
  on `.nightshift/daemon.lock`, not a PID file. A crashed process releases the
  lock by dying; the `PidStatus.STALE` heuristic is deleted.
- **REQ-NS-DAEMON-03** — Streams are registered with a name, an interval and a
  `RunOnce(ctx) error`, and are driven by `time.Ticker`. A stream's error is
  logged, counted in a metric, and does not stop the daemon.
- **REQ-NS-DAEMON-04** — First SIGINT/SIGTERM cancels the root context and
  waits up to `shutdown_grace` (default 120 s) for in-flight issues to reach a
  safe point. A second signal exits 130 immediately.
- **REQ-NS-DAEMON-05** — Startup runs pre-flight checks in a fixed order and
  reports **all** failures before exiting, rather than exiting on the first.
  The root/`bypassPermissions` check is deleted: AgentKit runs in-process and
  has no UID restriction.
- **REQ-NS-DAEMON-06** — On startup the daemon opens a hub run
  (`run_id` in hub's `YYYYMMDD_HHMMSS_6hex` format) and on exit submits a
  postmortem with `run_status` ∈ {`stalled`, `block_limit`, `cost_limit`,
  `session_limit`}, `task_summary` and `cost_summary`.
- **REQ-NS-DAEMON-07** — `--healthz-addr` exposes `/healthz`, `/readyz` and
  `/metrics`. Metrics include issues processed, outcome counts, session count
  by archetype, cost, budget headroom, stream cycle duration, hub spool depth,
  and gate pass/fail counts.

### 6.2 Issue polling and scheduling

- **REQ-NS-POLL-01** — Poll open issues labelled `af:fix`, **excluding** any
  carrying `af:fixed`, `af:no-change` or `af:failed` (closes F-6).
- **REQ-NS-POLL-02** — Ordering is oldest-first by creation, then by number,
  then by priority label. Local re-sort is retained as the fallback.
- **REQ-NS-POLL-03** — Explicit dependency edges are parsed from issue bodies
  (`depends on|blocked by|after|requires #N`) and from
  `GET /issues/{n}/relationships` when the issue service advertises the
  `relationships` capability. Edges are kept only when both endpoints are in
  the batch.
- **REQ-NS-POLL-04** — AI batch triage runs when the batch size reaches
  `night_shift.batch_triage_threshold` (default 3, configurable — F-26) and
  contributes additional edges and supersession *candidates*.
- **REQ-NS-POLL-05** — Cycles are detected; a cycle is broken at the lowest
  issue number, and the break is logged and audited rather than silently
  resolved.
- **REQ-NS-POLL-06** — Dispatch is dependency-aware with a configurable
  concurrency limit. Before starting, an issue is re-fetched to confirm it is
  open and still labelled; before *and* after dispatch, the local ledger
  records an in-flight claim so a restart does not double-start an issue.
- **REQ-NS-POLL-07** — Drain iterates until the queue is empty or a limit is
  reached. **A platform error aborts the drain and returns an error** — the
  fail-open `return True` is deleted (closes F-8).

### 6.3 Supersession and staleness

- **REQ-NS-STALE-01** — Supersession and staleness produce a
  `SupersessionProposal{issue, superseded_by, rationale, evidence}`.
- **REQ-NS-STALE-02** — The action taken is configured by
  `[triage] supersession_action = "comment" | "label" | "close"`, defaulting to
  **`comment`**. Closing a human's issue on a model's say-so becomes opt-in
  (closes the policy half of F-3).
- **REQ-NS-STALE-03** — When staleness runs, the *actual* diff of the
  completed fix (`git diff <base>..<head>`, bounded) is supplied. If no diff is
  available, the staleness check is **skipped**, not run blind (closes the
  correctness half of F-3).
- **REQ-NS-STALE-04** — Issues currently in flight are excluded from staleness
  evaluation (behaviour retained).

### 6.4 The per-issue pipeline

- **REQ-NS-PIPE-01** — Phases: `Claim → Triage → Plan → Code → Gate → Review →
  Integrate → Report`, expressed as an explicit state machine with a
  persisted current state, so a restart resumes rather than restarts.
- **REQ-NS-PIPE-02** — An issue whose body is below `min_body_chars` is
  commented on and labelled `af:needs-detail`; it is not re-processed until the
  body changes (tracked by body hash in the ledger).
- **REQ-NS-PIPE-03** — Triage runs **before** any branch or worktree is
  created. A triage that yields no criteria and no summary aborts the issue
  without leaving a branch behind.
- **REQ-NS-PIPE-04** — Triage returns via `submit_triage` with a JSON Schema.
  A run that ends without calling it is a failed triage, retried once, then
  abandoned. No prose parsing (closes F-21).
- **REQ-NS-PIPE-05** — The worktree is created under
  `.nightshift/worktrees/fix/<issue>` on branch `fix/<issue>-<slug>` from the
  fetched integration branch tip, under the repo lock.
- **REQ-NS-PIPE-06** — `create_worktree` **never** deletes a remote branch. A
  stale local branch is force-deleted; the remote is left alone (closes F-4).
- **REQ-NS-PIPE-07** — The coder receives: the sanitized issue, the triage
  result, prior-attempt context from the ledger, carry-forward findings, and —
  on a retry — the gate output and reviewer feedback. Every one of these is
  passed through the prompt sanitizer, not just the issue body (closes F-23).
- **REQ-NS-PIPE-08** — After the coder session, the gate (§5.4) runs. A failing
  gate skips the reviewer and starts the next attempt with the gate output.
- **REQ-NS-PIPE-09** — The reviewer runs only on a green gate, returns via
  `submit_review`, and its `FAIL` verdicts feed the next attempt.
- **REQ-NS-PIPE-10** — Attempts are bounded by `orchestrator.max_retries`
  *within a run* and by `night_shift.max_attempts_per_issue` (default 3)
  *across runs*, counted in the ledger. Exhaustion applies `af:failed`, posts a
  summary of every attempt, and permanently excludes the issue from polling
  until a human removes the label (closes F-7).
- **REQ-NS-PIPE-11** — Uncommitted changes left in the worktree are committed
  before integration (behaviour retained), with the commit message naming the
  sweep so it is distinguishable in history.
- **REQ-NS-PIPE-12** — Integration strategies: `direct` (squash-merge into the
  integration branch under the repo lock, push with rebase-retry), `branch`
  (leave it, comment), `pr` (push, open a PR, `af:pr` + tracking comment), and
  `carry_patch` (push, register the patch, request and poll a rebuild). The
  carry-patch strategy is selected by `merge_strategy = "carry_patch"`, not by
  a separate `enabled` flag crossed with client presence.
- **REQ-NS-PIPE-13** — Outcomes are `merged`, `pr_opened`, `branch_left`,
  `no_changes`, `gate_failed`, `review_failed`, `error`. Each has exactly one
  comment template, one label transition and one audit event.
- **REQ-NS-PIPE-14** — Worktrees are destroyed on every exit path. Orphans
  older than `worktree_ttl` are reaped at startup.

### 6.5 Agent runtime

- **REQ-NS-AGENT-01** — All sessions run through `agentkit.Agent`. The
  `Backend` protocol and its three adapters are deleted (closes F-15, F-16).
- **REQ-NS-AGENT-02** — Models are resolved through `catalog.ResolveModel`.
  Tier names (`SIMPLE`/`STANDARD`/`ADVANCED`) remain as *config aliases* that
  map to catalog model specs; a spec may also be given directly
  (`vendor/model-id`). The hardcoded `MODEL_REGISTRY` is deleted.
- **REQ-NS-AGENT-03** — `max_tokens` is clamped by the catalog row; thinking
  level is a `core.ThinkingLevel` mapped by the catalog's
  `thinking_level_map`. The `{"type":"adaptive","display":"summarized"}` dict
  and the `effort` string are replaced by the SDK's typed equivalents.
- **REQ-NS-AGENT-04** — Cost is `provider.ComputeCost` against the resolved
  catalog row, per request, accumulated in `core.Usage` (closes F-17).
- **REQ-NS-AGENT-05** — Tool authorization is a single
  `core.BeforeToolCall` per archetype. An archetype whose tool set contains a
  shell tool with no interceptor fails construction with
  `core.ErrUnguardedExecute`. `RestrictedPolicy` supplies the program
  allowlist and shell-operator rejection.
- **REQ-NS-AGENT-06** — Archetype tool sets:

  | Archetype | Tools | Shell |
  |---|---|---|
  | `triage` | read, list, find, search + `submit_triage` | none |
  | `coder` | full `tools.All` + MCP tools | `RestrictedPolicy` |
  | `reviewer` | read, list, find, search + `submit_review` | read-only allowlist |
  | `carry-patch` | read, write, edit + `submit_resolution` | git-only allowlist |
  | `maintainer` (batch triage, staleness) | none | none |

- **REQ-NS-AGENT-07** — Every session gets an AgentKit `SessionStore` JSONL
  log under `.nightshift/sessions/<run_id>/<node_id>.jsonl`. A session
  interrupted mid-turn is resumable via `session.Fold`, and the log is the
  local artifact a human reads when a fix goes wrong.
- **REQ-NS-AGENT-08** — Compaction is `SummarizationCompaction` bound to the
  resolved context window, per archetype. `cache_policy` maps to
  `core.CacheRetention` and actually takes effect (closes F-11).
- **REQ-NS-AGENT-09** — Middleware chain per session: `RetryMiddleware`
  (transient wire failures), `BudgetMiddleware`, `TracingMiddleware` (→ hub
  traces). `core.Hooks.OnTurnEnd` reports usage to hub; `OnAudit` forwards
  session start/end.
- **REQ-NS-AGENT-10** — The event stream drives both renderers; events are
  emitted as they arrive, not buffered (closes F-10).
- **REQ-NS-AGENT-11** — Batch triage and staleness are ordinary AgentKit runs
  with no tools, not a fourth SDK path.

### 6.6 Archetypes, skills and prompts

- **REQ-NS-SKILL-01** — Archetype behavioural prompts are AgentKit **skills**,
  shipped embedded in the binary as the built-in tier. `_skills/` contains
  `fix-triage`, `fix-coder`, `fix-reviewer`, `carry-patch-resolver`.
- **REQ-NS-SKILL-02** — Discovery is `skills.Discover` over
  built-in → `~/.nightshift/skills` → `<repo>/.nightshift/skills`. Selection is
  `LoadForSession(archetype, task, cfg)`.
- **REQ-NS-SKILL-03** — **`TrustProject` defaults to `false`.** Night Shift
  operates on repositories whose issues are its input; a hostile issue body
  plus a hostile `.nightshift/skills` in the same repository is the exact
  threat the trust gate exists for. Enabling project skills is an explicit
  `[skills] trust_project = true`, and the daemon logs it at startup.
- **REQ-NS-SKILL-04** — The archetype registry keeps its shape (name, default
  model tier, max turns, thinking level, effort, allowlist, per-mode
  overrides) and its three-step cascade (mode override → archetype override →
  registry default). It gains a `Tools` field and a `Policy` field, and loses
  `injection`, `injection_order`, `templates` and `retry_predecessor`, which no
  code path in the fix daemon reads.
- **REQ-NS-SKILL-05** — Template substitution uses `text/template` with
  explicit data, not `strings.Replace` on `{{ name }}` (closes the mechanism
  half of F-22).
- **REQ-NS-SKILL-06** — The `verifier` and `gate` archetypes are removed
  (NG1). The `maintainer` archetype survives as the batch-triage/staleness
  analyst.

### 6.7 Hub integration

- **REQ-NS-HUB-01** — A hub client covering: workspaces (`GET`, `PATCH`,
  `sync`, `patch-status`), patches (`POST`, `GET`, `PATCH`, `DELETE`,
  `reorder`, `restore`), rebuilds (`POST`, list, get, `requeue`, `rollback`,
  `rebuild-preview`), rerere (list, delete), merges and `rebase`, sessions
  (`POST`, `usage`, `complete`, list, get, `/cost`), audit ingestion (events,
  events/batch, sessions/outcomes, tools/calls, tools/errors, traces,
  traces/batch, postmortem), audit query (`/api/v1/audit`, `transcript`),
  secrets and variables (including `/vars/resolved`).
- **REQ-NS-HUB-02** — A hub session is opened per agent session with
  `workspace_slug`, `run_id`, `node_id`, `archetype` and `model`; usage is
  reported per turn; the session is closed with a terminal status and
  `duration_ms`. Client-generated UUIDs make every call idempotent.
- **REQ-NS-HUB-03** — Audit writes are batched (up to 1000 per request per
  hub's limit) and flushed on a timer or a size threshold. Every record
  carries a client UUID; hub's `INSERT OR IGNORE` makes retries safe.
- **REQ-NS-HUB-04** — All hub writes go through the offline spool. Spool depth
  is a metric; a spool that cannot drain for `spool_max_age` logs an error and
  begins dropping oldest-first, loudly.
- **REQ-NS-HUB-05** — Hub is **required**, because it serves the issue API
  (§5.8): a daemon without hub has no way to find work. `[hub] endpoint_url`
  unset is a startup failure, not a local-only mode. What degrades rather than
  fails is per subsystem, and the difference matters operationally:

  | Hub subsystem unreachable | Effect |
  |---|---|
  | Issues | fix-pipeline and pr-feedback stall; the client backs off and retries and never reports an outage as an empty queue (REQ-NS-ISSUES-04, contrast F-8) |
  | Audit, sessions, usage | spooled locally and drained on recovery (REQ-NS-HUB-04); no effect on fixing |
  | Memory (findings, summaries) | retrieval returns empty, recording spools; sessions run without carry-forward (REQ-NS-HUB-10) |
  | Secrets, variables | last resolved values are reused; a cold start that cannot resolve a required secret fails |
  | Carry-patch | the carry-patch stream stalls and retries |

  An in-flight issue is not abandoned when hub goes away mid-fix: the pipeline
  completes locally through integration, and the reporting it could not
  deliver is spooled.
- **REQ-NS-HUB-06** — One hub credential, resolved from `AF_HUB_TOKEN` then
  config, authenticates every hub call including the issue API. Everything
  else — provider API keys above all — resolves hub secret → environment
  variable → config, so the hub token is the only secret that must exist on
  the box (closes the credential half of F-19).
- **REQ-NS-HUB-07** — Operational toggles are read from hub workspace
  variables at each cycle so a running daemon can be steered without a
  restart: `NIGHTSHIFT_PAUSED`, `NIGHTSHIFT_MAX_PARALLEL`,
  `NIGHTSHIFT_BUDGET_USD`.
- **REQ-NS-HUB-08** — Hub workspace variables that already exist
  (`AUTO_REBUILD_AFTER_SYNC`, `AUTO_REBUILD_AFTER_PUSH`, `REBUILD_STRATEGY`,
  `REBUILD_FAIL_MODE`, `SQUASH_MERGE_DETECTION`, `CHECK_COMMAND`) are read and
  honoured rather than blindly overwritten. Today's startup unconditionally
  sets both `AUTO_REBUILD_*` to `"false"`; the rewrite sets them only when
  `[carry_patch] manage_auto_rebuild = true`.
- **REQ-NS-HUB-09** — **Carry-forward memory is hub's, entirely.** Night Shift
  stores no findings locally. Hub must implement, and Night Shift consumes:

  | Method | Path | Purpose |
  |---|---|---|
  | `POST` | `/workspaces/:slug/findings` | record findings from a review session (batch) |
  | `GET` | `/workspaces/:slug/findings` | retrieve active findings for injection |
  | `POST` | `/workspaces/:slug/findings/injections` | record which finding ids entered which session |
  | `POST` | `/workspaces/:slug/findings/supersede` | supersede the findings a completed session saw |
  | `POST` | `/workspaces/:slug/summaries` | record a session summary |
  | `GET` | `/workspaces/:slug/summaries` | retrieve summaries for a spec |

  `GET /findings` filters on `paths` (repeatable), `severity`, `state` and
  `limit`; a finding carries `{id, run_id, node_id, spec, severity, category,
  path, line_start, line_end, description, evidence, state, created_at}` where
  `state` is `active | superseded | dismissed`. The injection/supersession
  cycle is the one behaviour worth preserving from the DuckDB knowledge store:
  a finding injected into a session is superseded when that session completes
  successfully, so it is not re-injected forever.
- **REQ-NS-HUB-10** — Memory is enrichment, not a dependency. With hub
  unreachable, carry-forward retrieval returns empty and sessions run without
  it; recording is spooled (REQ-NS-HUB-04). A memory outage never blocks a fix.

### 6.8 Carry-patch

- **REQ-NS-CP-01** — The monitor polls `GET /workspaces/:slug/patch-status`
  and acts on `conflict` patches; `merged_upstream` patches are reported and,
  under `auto_prune`, soft-deleted.
- **REQ-NS-CP-02** — Conflict resolution happens in a dedicated worktree under
  the repo lock. The primary checkout is never used (closes F-5).
- **REQ-NS-CP-03** — Before spending a coder session, the monitor calls
  `GET /rebuild-preview` and `GET /rerere` and includes both in the resolution
  context. A conflict that rerere already resolves is not sent to a model.
- **REQ-NS-CP-04** — Per-patch retries are bounded, persisted in the ledger
  (not an in-memory map that resets on restart), and exhaustion raises a
  patch-level `conflict_failed` audit event and stops retrying.
- **REQ-NS-CP-05** — Resolution ends with `submit_resolution`; the branch is
  pushed and a rebuild requested, with `HubConflictError` falling back to the
  active rebuild.

### 6.9 PR feedback

- **REQ-NS-PR-01** — Track a PR through a machine-readable comment carrying
  `pr_number`, `attempt` and `run_id`. The dead `pr_url` parameter is removed
  (closes F-14).
- **REQ-NS-PR-02** — On each cycle: check PR state (merged → close the issue
  and label `af:fixed`; closed → label and stop), then CI, then reviews.
- **REQ-NS-PR-03** — A feedback iteration reuses the **same** pipeline code as
  a fresh fix — same worktree helper, same gate, same integration — rather than
  a second `subprocess`-based implementation (closes F-24).
- **REQ-NS-PR-04** — Feedback iterations inherit the full pipeline
  configuration, hub client and workspace slug included (closes F-13).
- **REQ-NS-PR-05** — Iterations are bounded by `night_shift.max_pr_retries`,
  persisted in the ledger.

### 6.10 Issues and git

- **REQ-NS-ISSUES-01** — **Code operations are plain git.** Every operation on
  the repository — clone, fetch, branch, worktree, checkout, merge, rebase,
  push, tag — is git against the repository's own remote with ordinary git
  credentials. No forge REST API takes part in any code operation, in any
  merge strategy.
- **REQ-NS-ISSUES-02** — **Issue operations go through the Issue Service API
  on hub** and nothing else. The contract is normative in the [Issue Service PRD](issue_service_prd.md); the
  daemon contains no GitHub, GitLab or Gitea code, no tracker-specific request
  shaping and no tracker-specific error handling.
- **REQ-NS-ISSUES-02a** — The issue client is constructed from `[hub]`:
  `endpoint_url` for the base URL, the hub token for `Authorization`, and
  `workspace` as the contract's `{project}`. There is no separate issue
  endpoint, token or project setting, and the daemon opens no second
  connection (§5.8).
- **REQ-NS-ISSUES-03** — Capabilities are read once at startup from
  `GET /projects/{project}/capabilities` (REQ-IS-4.1). A configuration
  requiring an unadvertised capability fails at startup with a message naming
  both the capability and the setting that needs it — never at the point of
  use. `merge_strategy = "pr"` against a service without `pull_requests` is
  the motivating case.
- **REQ-NS-ISSUES-04** — The client honours `Retry-After` on `rate_limited`
  and applies bounded exponential backoff on `upstream_error` and 5xx.
  Consecutive failures are surfaced as a metric (REQ-NS-DAEMON-07), not only
  as log text.
- **REQ-NS-ISSUES-05** — Every call carries the request context and a per-call
  timeout; cancellation propagates.
- **REQ-NS-ISSUES-06** — Label provisioning at startup uses
  `PUT /labels/{name}`, idempotent by contract (REQ-IS-2.9), so "already
  exists" stops being an error string to pattern-match. The required set is
  `af:fix`, `af:fixed`, `af:no-change`, `af:pr`, `af:failed` (new,
  REQ-NS-PIPE-10) and `af:needs-detail` (new, REQ-NS-PIPE-02).
- **REQ-NS-ISSUES-07** — Night Shift depends on hub having implemented the
  contract; it does not ship an implementation and is not configured against
  one other than hub. Delivery belongs to the [Issue Service PRD](issue_service_prd.md).
  What Night Shift owns is the dependency: see §10 Phase 0 for sequencing and
  R-8 for the risk.
- **REQ-NS-ISSUES-08** — Night Shift's own test suite runs against the
  in-process fake the service spec ships (REQ-IS-8.4), so the daemon's tests
  need no network and no live service.

### 6.11 Memory and the ledger

- **REQ-NS-MEM-01** — Local SQLite holds only what must survive a hub outage:
  `issue_attempts`, `issue_claims`, `patch_retries`, `pr_iterations`, `spool`.
  Migrations are ordinary numbered Go migrations. No table exists for a
  feature that is not shipped (closes F-18).
- **REQ-NS-MEM-02** — Prior-attempt context for the coder is read from
  `issue_attempts` (date, outcome, model, gate output tail, error). This is
  local because it gates dispatch (REQ-NS-PIPE-10) and must work offline.
- **REQ-NS-MEM-03** — **Carry-forward findings and session summaries live in
  hub** and nowhere else (REQ-NS-HUB-09). Night Shift records them after a
  review session, retrieves them by path before a coder session, and drives
  the injection/supersession cycle through the hub API. It keeps no local
  copy and no local cache with its own lifetime.
- **REQ-NS-MEM-04** — `duckdb`, `sentence-transformers`, `scikit-learn` and
  every `tree-sitter` grammar are gone.

### 6.12 MCP

- **REQ-NS-MCP-01** — `nightshift mcp-server` serves stdio; `[mcp_server]
  transport = "http"` serves HTTP with API-key auth, using `coder/mcp`.
- **REQ-NS-MCP-02** — Tools: `process_issue(issue_number, mode)`,
  `get_session_status(session_id)`, `list_active_sessions()`,
  `cancel_session(session_id)` — the inventory AgentKit's PRD already scopes to
  Night Shift.
- **REQ-NS-MCP-03** — Resources: `nightshift://issues/{number}/triage-report`,
  `nightshift://sessions/{id}/audit-log`, `nightshift://config`, registered as
  URI templates.
- **REQ-NS-MCP-04** — MCP **clients** configured in `[mcp]` contribute tools
  to the coder archetype through `mcp.Pool`, with the server-name prefix
  convention and `SplitDeferredTools` so a late-connecting server does not
  invalidate the cached prompt prefix.

### 6.13 Plugins

- **REQ-NS-PLUGIN-01** — `coder/plugins`' four categories are exposed:
  `backend`, `tool_provider`, `storage`, `event_hook`. `[plugins] paths` and
  `disabled` are read from the same config file.
- **REQ-NS-PLUGIN-02** — `nightshift validate-plugins` runs conformance
  checks and the import lint without starting the daemon.

### 6.14 Observability and UX

- **REQ-NS-OBS-01** — `log/slog` with a JSON handler by default and a text
  handler on a TTY. Every record carries `run_id`, `issue`, `node_id`,
  `archetype`.
- **REQ-NS-OBS-02** — The TTY renderer shows a live line per in-flight issue
  (phase, elapsed, tokens, cost) and a permanent line per completed phase.
  `--json` emits NDJSON of the same events. Both read the same event channel.
- **REQ-NS-OBS-03** — Prometheus metrics per REQ-NS-DAEMON-07.
- **REQ-NS-OBS-04** — An audit trail entry per tool call records the argument
  **hash**, never the arguments, per AgentKit's `audit.go`. This is a
  strengthening: today's `tool.invocation` payload includes a
  `param_summary` built from abbreviated argument values, which can carry file
  contents and credentials into a retained log.
- **REQ-NS-OBS-05** — `nightshift status` prints the local ledger state and,
  when hub is configured, the workspace cost summary and recent run outcomes.

---

## 7. API and Type Sketches

### 7.1 Archetype resolution

```go
package archetype

type Tier string // "SIMPLE" | "STANDARD" | "ADVANCED"

type Spec struct {
    Name       string
    ModelTier  Tier
    ModelSpec  string        // optional explicit "vendor/id"; wins over tier
    MaxTurns   int
    Thinking   core.ThinkingLevel
    MaxBudget  float64
    ToolSet    ToolSet       // Triage | Coder | Reviewer | CarryPatch | None
    Allowlist  []string      // program allowlist for RestrictedPolicy
    Compaction bool
    Modes      map[string]Override
}

// Resolve applies mode override -> config override -> registry default.
func Resolve(cfg *config.Config, name, mode string) (Spec, error)
```

### 7.2 Building a session

```go
package agentrt

type Session struct {
    RunID     string
    NodeID    string
    Archetype string
    Mode      string
    Worktree  string
    Budget    *budget.Ledger
}

// Build wires an archetype spec into a ready agentkit.Agent.
func (r *Runtime) Build(ctx context.Context, s Session, spec archetype.Spec,
    extra []core.Tool) (*agentkit.Agent, error) {

    model, err := catalog.ResolveModel(r.modelSpec(spec))
    if err != nil { return nil, err }

    ws, err := tools.NewWorkspace(s.Worktree)
    if err != nil { return nil, err }

    toolset, err := r.toolsFor(spec.ToolSet, ws, extra)
    if err != nil { return nil, err }

    cfg := core.AgentConfig{
        Model:         model,
        SystemPrompt:  r.basePrompt(spec),
        PromptBlocks:  r.skillBlocks(spec.Name, s),
        ThinkingLevel: spec.Thinking,
        ToolPolicy:    core.ToolPolicy{Tools: toolset},
        // A shell tool with no interceptor is ErrUnguardedExecute at construction.
        BeforeToolCall: policy.For(spec),
        StopPolicy: agentkit.StopAny(
            agentkit.StopAfterTurns(spec.MaxTurns),
            agentkit.StopOverBudget(spec.MaxBudget),
        ),
        Middleware: []core.Middleware{
            agentkit.RetryMiddleware(r.retryOpts),
            agentkit.TracingMiddleware(r.tracer),
        },
        Hooks: core.Hooks{
            OnTurnEnd:      r.reportUsage(s),
            OnAudit:        r.forwardAudit(s),
            OnError:        r.recordError(s),
        },
        SessionStore:   r.sessionLog(s),
        OnPersistError: r.logPersistError,
        Providers:      r.providers,
        CacheRetention: r.cacheRetention,
        TrustProject:   r.trustProject, // default false
    }
    return agentkit.NewAgent(cfg)
}
```

### 7.3 Phase contracts

```go
package pipeline

type Triage struct {
    Summary       string     `json:"summary"`
    AffectedFiles []string   `json:"affected_files"`
    Criteria      []Criterion `json:"criteria"`
    Complexity    Complexity `json:"complexity"`
}

type Criterion struct {
    ID            string `json:"id"`
    Description   string `json:"description"`
    Preconditions string `json:"preconditions"`
    Expected      string `json:"expected"`
    Assertion     string `json:"assertion"`
}

// submitTriageTool terminates the run and hands the typed result back.
func submitTriageTool(out *Triage) core.Tool {
    return core.Tool{
        Name:        "submit_triage",
        Description: "Submit the triage report. Calling this ends the session.",
        InputSchema: triageSchema(), // schema.Object(...) combinators
        ConstrainedSampling: &core.ConstrainedSampling{
            Type: core.ConstrainJSONSchema, Strict: core.StrictPrefer,
        },
        Execute: func(ctx context.Context, in json.RawMessage) core.ToolResult {
            if err := json.Unmarshal(in, out); err != nil {
                return core.ErrResult("invalid_triage", err.Error())
            }
            if len(out.Criteria) == 0 {
                return core.ErrResult("no_criteria",
                    "at least one acceptance criterion is required")
            }
            r := core.OKResult(map[string]any{"accepted": len(out.Criteria)})
            r.Terminate = true
            return r
        },
    }
}
```

A malformed submission is a tool error the model sees and can correct on the
next turn — which is what the 949-line parser and the one-shot reviewer re-run
were approximating from outside the loop.

### 7.4 The pipeline state machine

```go
type Phase string

const (
    PhaseClaim     Phase = "claim"
    PhaseTriage    Phase = "triage"
    PhaseCode      Phase = "code"
    PhaseGate      Phase = "gate"
    PhaseReview    Phase = "review"
    PhaseIntegrate Phase = "integrate"
    PhaseReport    Phase = "report"
)

type Outcome string

const (
    OutcomeMerged       Outcome = "merged"
    OutcomePROpened     Outcome = "pr_opened"
    OutcomeBranchLeft   Outcome = "branch_left"
    OutcomeNoChanges    Outcome = "no_changes"
    OutcomeGateFailed   Outcome = "gate_failed"
    OutcomeReviewFailed Outcome = "review_failed"
    OutcomeError        Outcome = "error"
)

func (p *Pipeline) Process(ctx context.Context, issue issues.Issue) (Result, error)
```

### 7.5 The gate

```go
package gate

type Result struct {
    Ran      bool
    Passed   bool
    Command  string
    ExitCode int
    Output   string        // bounded tail
    Duration time.Duration
}

func Run(ctx context.Context, dir string, cfg Config) (Result, error)
```

### 7.6 The budget ledger

```go
package budget

type Ledger struct { /* mu, limit, spent, reserved */ }

func (l *Ledger) Reserve(usd float64) (release func(), ok bool)
func (l *Ledger) Commit(usd float64)
func (l *Ledger) Headroom() float64
func (l *Ledger) Exceeded() bool
```

### 7.7 The issue client

One concrete client over the wire protocol defined in the [Issue Service PRD](issue_service_prd.md) — an
interface for test substitution, not for multiple production
implementations. There is no `github.go` behind it.

```go
package issues

// Caps mirrors GET /capabilities. Read once at startup; a configuration
// needing an unadvertised capability fails there (REQ-NS-ISSUES-03).
type Caps struct {
    PullRequests  bool
    Checks        bool
    Reviews       bool
    Relationships bool
}

type Query struct {
    Labels        []string // all must be present
    ExcludeLabels []string // none may be present — server-side (REQ-NS-POLL-01)
    State         string   // "open" | "closed" | "all"
    Sort          string
    Direction     string
    Limit         int
    Cursor        string
}

type Page struct {
    Issues     []Issue
    NextCursor string
    HasMore    bool
}

// Issue carries State as a required field: the guard that reads it is dead
// code today because the Python DTO never had one (F-28).
type Issue struct {
    Number    int
    State     string
    Title     string
    Body      string
    Labels    []string
    Author    string
    HTMLURL   string
    CreatedAt time.Time
    UpdatedAt time.Time
}

type Client interface {
    Capabilities(ctx context.Context) (Caps, error)

    CreateIssue(ctx context.Context, req CreateIssueRequest) (Issue, error)
    ListIssues(ctx context.Context, q Query) (Page, error)
    GetIssue(ctx context.Context, n int) (Issue, error)
    UpdateIssue(ctx context.Context, n int, req UpdateIssueRequest) (Issue, error)
    CloseIssue(ctx context.Context, n int, comment string) (Issue, error)

    ListComments(ctx context.Context, n int) ([]Comment, error)
    AddComment(ctx context.Context, n int, body string) (Comment, error)

    AddLabel(ctx context.Context, n int, label string) error    // idempotent
    RemoveLabel(ctx context.Context, n int, label string) error // idempotent
    EnsureLabel(ctx context.Context, l Label) error             // idempotent

    Relationships(ctx context.Context, n int) ([]Relationship, error)

    CreatePR(ctx context.Context, req CreatePRRequest) (PullRequest, error)
    GetPR(ctx context.Context, n int) (PullRequest, error)
    PRChecks(ctx context.Context, n int) ([]Check, error)
    PRReviews(ctx context.Context, n int) ([]Review, error)
}
```

The client is constructed from the same `[hub]` endpoint and token as
`internal/hubclient`, with `[hub] workspace` as the contract's `{project}`
(REQ-NS-ISSUES-02a). The two packages are separate because they speak
different parts of hub's API, not because they speak to different servers.

Git has no interface here at all: `internal/repo` shells out to `git`, and
that is the whole abstraction (REQ-NS-ISSUES-01).

---

## 8. Configuration

`.nightshift/config.toml`. Existing keys keep their names and meanings unless
listed in Appendix B.

```toml
[night_shift]
issue_check_interval    = 900
pr_check_interval       = 900
max_parallel            = 2
max_attempts_per_issue  = 3     # NEW: across runs (REQ-NS-PIPE-10)
max_pr_retries          = 3
batch_triage_threshold  = 3     # NEW: was a hardcoded `>= 3`
shutdown_grace          = 120   # NEW
worktree_ttl            = "24h" # NEW

[workspace]
integration_branch = "develop"
merge_strategy     = "direct"   # direct | branch | pr | carry_patch

[gate]                           # NEW (REQ-NS-PIPE-08)
command  = "make check"
timeout  = "15m"
required = true                 # false = reviewer verdict alone decides

[orchestrator]
max_retries       = 2
session_timeout   = "45m"
max_cost          = 20.0        # hard daemon ceiling; no 50% margin
max_budget_usd    = 5.0         # default per-session cap

[models]
simple   = "anthropic/claude-haiku-4-5"
standard = "anthropic/claude-sonnet-4-5"
advanced = "anthropic/claude-opus-4-5"

[archetypes.coder]
model_tier = "ADVANCED"
max_turns  = 300
thinking   = "high"
compaction = true

[archetypes.coder.modes.fix]
max_budget_usd = 8.0

[security]
allowlist_extend = ["make", "uv"]

[skills]
trust_project = false           # NEW default (REQ-NS-SKILL-03)

[hub]                            # required — hub serves the issue API (§5.8)
endpoint_url  = "https://hub.example.com"
workspace     = "my-workspace"   # also the issue contract's {project}
audit         = true
memory        = true             # carry-forward findings (REQ-NS-HUB-09)
spool_dir     = ".nightshift/spool"
spool_max_age = "72h"
# token: AF_HUB_TOKEN — needs issues:read, issues:write, sessions:*, audit:*

[carry_patch]
check_interval        = 300
auto_resolve          = true
max_resolve_retries   = 2
rebuild_timeout       = "10m"
rebuild_poll_interval = "5s"
manage_auto_rebuild   = false   # NEW: was unconditional (REQ-NS-HUB-08)

[triage]
supersession_action = "comment" # NEW default (REQ-NS-STALE-02)

[telemetry]
healthz_addr = "127.0.0.1:9090"

[mcp.servers.example]
command = "example-mcp"
args    = []

[plugins]
paths    = []
disabled = []
```

**Removed keys:** `platform.type` / `platform.url` / `platform.project_id` /
`platform.owner` / `platform.repo` (issues now come from `[hub]`),
`backend.provider`
(one runtime), `knowledge.*` (no knowledge store), `caching.cache_policy` moves
under `[models]` as `cache_retention`, `security.permission_mode` (no
subprocess permission model), `archetypes.overrides.*.injection` /
`injection_order` / `templates`.

**Removed environment variables:** `GITHUB_PAT`, `GITLAB_TOKEN`,
`GITEA_TOKEN` — the daemon no longer authenticates to a tracker. Git
credentials for push and pull are git's own concern. `AF_HUB_TOKEN` is the
only credential the daemon needs, and it covers issues as well as sessions,
audit and memory.

---

## 9. Non-Functional Requirements

- **NFR-01 — Single static binary.** `CGO_ENABLED=0`, cross-compiled for
  linux/amd64, linux/arm64, darwin/arm64. Runtime prerequisites: `git ≥ 2.38`
  and the project's own toolchain for the gate command.
- **NFR-02 — Container size.** The agents image drops Python, `uv`, Node,
  `pi`, `opencode` and Claude Code. Target: a UBI-micro base plus `git` plus
  the binary, under 100 MB (F-27).
- **NFR-03 — Startup.** Cold start to first poll under 2 s, excluding network.
- **NFR-04 — Loop overhead.** Per-turn overhead is AgentKit's, budgeted at
  < 1 ms and measured at ~48 µs; Night Shift adds no per-turn allocation on
  the hot path.
- **NFR-05 — Memory.** A daemon with `max_parallel = 4` stays under 512 MB
  RSS. Tool output is bounded by AgentKit's accumulator and spilled to disk
  above the cap.
- **NFR-06 — Concurrency safety.** `go test -race ./...` is the default gate.
  Every exported daemon type is documented as safe or unsafe for concurrent
  use.
- **NFR-07 — Determinism in tests.** The full suite runs offline with a
  scripted provider (`provider/faux`), a fixture git repository and an
  in-process issue service fake shipped by the service spec (REQ-IS-8.4). No
  network, no API key.
- **NFR-08 — Testing depth.** Unit tests per package; a golden-request suite
  for prompt assembly; a state-machine table test for the pipeline; a race
  test for parallel dispatch on one repository; a parity harness (§10) that
  runs the Python and Go daemons over the same fixture repository and diffs
  branches, comments, labels and outcomes.
- **NFR-09 — Compatibility.** The label vocabulary, branch naming, tracking
  comment format and config file location are unchanged, so a repository
  driven by the Python daemon can be handed to the Go daemon mid-flight.
- **NFR-10 — Security.** No secret is logged; tool arguments are hashed in the
  audit trail; project-local prompt content is untrusted by default; every
  string of external origin (issue text, triage output, review evidence, hub
  patch descriptions, CI logs) passes the prompt sanitizer before entering a
  prompt.
- **NFR-11 — Graceful degradation.** Hub is one endpoint serving several
  subsystems, and they degrade differently; REQ-NS-HUB-05 is the matrix.
  Issues unreachable → back off and retry, never fail-open as "queue empty"
  (contrast F-8). Audit unreachable → spool and continue. Memory unreachable →
  sessions run without carry-forward. An issue already in flight completes
  through integration regardless. Provider unreachable → retry middleware,
  then fail the session with a transport classification.

---

## 10. Migration Plan

**Phase 0 — Contracts (no behaviour).**
Config schema and loader; `internal/issues` client; `hubclient` covering §6.7
including the new findings endpoints; the SQLite ledger and its migrations.

**Hub's issue API is a prerequisite, not a parallel track.** Night Shift
cannot reach GitHub after this change, so the [Issue Service PRD](issue_service_prd.md)'s
Phase 2 — hub's implementation — gates Phase 1 here. Its Phase 1 (spec,
conformance suite and the in-process fake) unblocks client work earlier: the
fake is what Night Shift builds and tests the client against. That work is tracked in its own
document and is not restated as Night Shift deliverables; what Night Shift
owns is the client and the dependency.

Deliverable: `nightshift status` against a live hub, reading issues, sessions
and memory over one endpoint with one token.

**Phase 1 — One issue, end to end.**
`nightshift fix <n>`: claim → triage → code → gate → review → integrate
(`direct` only), on AgentKit, with local telemetry only. This is the
correctness milestone; it proves the phase-contract tools, the gate, the
worktree/lock discipline, the budget ledger and the issue client against a
real service. The one-shot mode is a first-class operator and testing
affordance, not a stepping stone to running under hub — see §12, D-6.

**Phase 2 — The daemon.**
Streams, scheduler, dependency graph, parallel dispatch, attempt ledger,
supersession/staleness under the new policy, hub audit + sessions + spool,
`/healthz` and `/metrics`, both renderers.

**Phase 3 — The remaining strategies.**
`branch`, `pr`, `carry_patch`; the PR feedback loop; the carry-patch monitor
with its own worktree, rebuild preview and rerere.

**Phase 4 — Extension surface.**
MCP server and client, plugins, `validate-plugins`, skills tiers.

**Phase 5 — Parity and cutover.**
Run both daemons against a fixture repository seeded with a corpus of real
`af:fix` issues; diff the produced branches, diffs, comments, labels and
outcomes; record every intentional divergence in Appendix B. Cut over one
workspace at a time. The Python implementation is archived, not deleted, until
one full release cycle has passed.

Each phase ships behind its own release; Phases 1–3 are the ones that must
land before the Python daemon can be retired.

---

## 11. Risks

**R-1 — AgentKit is v0.4.2 and self-describes as "not a finished product".**
Its differential harness reports DARK (no reference bodies), so the wire format
is pinned against regression but not against truth. *Mitigation:* Night Shift
uses Anthropic first, which has the deepest coverage; the parity harness in
Phase 5 compares against the Python daemon's actual behaviour, which is the
reference we care about; a real difftest reference is a shared dependency worth
funding.

**R-2 — The model catalog snapshot lags.** `catalog.json` is dated 2026-09-05
and carries `claude-*-4-5` rows while Night Shift's config names `4-6`.
Unknown ids clone the vendor default row, so pricing and context window can be
silently wrong for a newly released model. *Mitigation:* pin explicit
`vendor/id` specs in config, alert when a resolved model was cloned rather than
found, and contribute catalog rows upstream.

**R-3 — MCP is modern-era only (`2026-07-28`).** Most MCP servers in the wild
have not migrated, so REQ-NS-MCP-04's client half may find nothing to talk to.
*Mitigation:* ship the server half (which we control) in Phase 4 and treat the
client half as opportunistic.

**R-4 — Losing 100,000 lines of Python tests.** The Go suite starts from zero.
*Mitigation:* the parity harness is the acceptance gate, not the unit-test
count; port the property tests that encode real invariants (dependency graph,
branch-name sanitization, cost arithmetic, review parsing edge cases as
schema-validation cases) rather than the tests that pin Python internals.

**R-5 — Behaviour changes will surprise operators.** Supersession stops
closing issues; the budget stops stopping at half; `af:no-change` issues stop
being retried. *Mitigation:* Appendix B is the complete ledger, every entry
lands in the release notes, and each change has a config key that restores the
old behaviour where restoring it is defensible.

**R-6 — Hub is a hard dependency, by decision.** Hub serves the issue API
(§5.8) and owns carry-forward memory (D-5), so a deployment without hub cannot
run Night Shift at all, and a hub outage stops new work from being picked up.
This is not the accident the v1.1.0 draft guarded against — it is a chosen
coupling, and it is the single largest operational commitment in this document.
*Mitigation:* the degradation matrix in REQ-NS-HUB-05 keeps the blast radius
per subsystem explicit rather than uniform — telemetry and memory degrade,
issues stall, in-flight fixes complete; the offline spool is mandatory; a test
runs the whole Phase-2 suite with hub returning 503 and asserts that an
in-flight issue still reaches integration. What is deliberately **not**
mitigated is availability: if hub is down, no new issue is dispatched, because
the alternative is a daemon guessing at queue state.

**R-7 — Go rewrite scope creep into a spec orchestrator.** NG1 exists because
`afcore` grew that way once already. *Mitigation:* the archetype set is closed
at four; adding one requires an amendment to this document.

**R-8 — Hub must implement the issue contract before Night Shift can poll.**
Today the daemon reaches GitHub directly; after §5.8 it cannot, and the
replacement lives in hub. Sequencing therefore crosses a repository boundary:
Night Shift's Phase 1 is blocked on hub shipping the contract, and neither
team can unblock itself. *Mitigation:* the contract is specified and testable
independently (the [Issue Service PRD](issue_service_prd.md) and its conformance suite, REQ-IS-8.1) so hub's
implementation can be validated before Night Shift consumes it, and the
service spec's in-process fake (REQ-IS-8.4) gives Night Shift something
conforming to develop and test the client against while hub's implementation
lands. See R-6 for the runtime dependency this
creates once it has shipped.

**R-9 — Carry-forward memory now depends on hub.** With memory moved to hub
(D-5), a hub outage means coder sessions lose prior findings and summaries.
*Mitigation:* REQ-NS-HUB-10 makes memory enrichment rather than a dependency —
retrieval returns empty, recording spools, the fix proceeds. The degradation is
a quality reduction, not a failure. It is also the reason prior-attempt data
stays local (REQ-NS-MEM-02): that one gates dispatch and cannot degrade.

---

## 12. Resolved Decisions

All seven questions raised against v1.0.0 have been answered. Each is recorded
with the decision, who it binds and where it lands in the document, so a reader
arriving later does not reopen settled ground.

**D-1 (was OQ-1) — Git is git; issues are a service.**
Night Shift performs every code operation with plain git against the
repository's own remote, and every issue operation through a single HTTP
contract — the Issue Service API, specified in the [Issue Service PRD](issue_service_prd.md) and **served by
hub**, on hub's endpoint, with hub's credentials and hub's workspace ACL. Multi-forge support leaves the daemon entirely: there is no
GitHub, GitLab or Gitea code in Night Shift after this change.

This is a larger change than "port GitHub and keep the interface", which was
the v1.0.0 recommendation, and it is better. `afissues` is three parallel
implementations of one idea, and every tracker quirk it absorbs is a quirk the
fix daemon carries. Moving that behind a contract means the daemon holds one
client and one set of semantics, and a new tracker is a new server rather than
a fourth implementation inside the thing that fixes bugs.

The cost is stated plainly in R-8: the service becomes a hard dependency, and
hub must implement the contract before anything can poll, and hub thereby
becomes a hard runtime dependency (REQ-NS-HUB-05, R-6). The contract itself is
specified in the [Issue Service PRD](issue_service_prd.md). *Binds:* §5.8, §6.10, §7.7, §8, §10 Phase 0, R-6,
R-8.

**D-2 (was OQ-2) — The reviewer stays; the gate is a precondition.**
`[gate] required = true` means a green gate is necessary before the reviewer
runs, not that it replaces the reviewer. The gate answers "does it build and
pass"; the reviewer answers "does it satisfy each acceptance criterion, with
evidence". Those are different questions and only the second produces the
per-criterion record that feeds a retry. *Binds:* §5.4, REQ-NS-PIPE-08/09.

**D-3 (was OQ-3) — Subagent delegation is deferred past Phase 3.**
`SubagentTool` is a good fit for bounded read-and-report work inside a coder
session, and it is also a new failure surface and a new cost centre. Revisit
with measured data on coder context pressure rather than in anticipation of it.
*Binds:* §10 (absent from Phases 0–4), NG4.

**D-4 (was OQ-4) — Model tiers remain config aliases in v1.**
`SIMPLE`/`STANDARD`/`ADVANCED` map to catalog model specs in `[models]`, and an
explicit `vendor/model-id` may be given instead. Turning a tier into a policy
("cheapest model with a ≥ N context window and reasoning support") is a
follow-up once catalog metadata is load-bearing elsewhere. *Binds:*
REQ-NS-AGENT-02, §8.

**D-5 (was OQ-5) — Carry-forward memory belongs to hub, solely.**
Findings and session summaries are stored, queried, injected and superseded
through the hub API. Night Shift keeps no local copy and no cache with its own
lifetime; it knows the endpoints and drives the workflow. Hub must implement
the six endpoints in REQ-NS-HUB-09.

The v1.0.0 draft kept memory local with hub as a mirror, on availability
grounds. Hub ownership is the better call because memory's value is
cross-cutting — several daemons on one workspace, and successive runs on one
repository, should share what has been learned, which a local store cannot do.
Availability is handled by making memory enrichment rather than a dependency
(REQ-NS-HUB-10, R-9) instead of by duplicating the store. *Binds:*
REQ-NS-HUB-09/10, REQ-NS-MEM-01/03, §5.5, R-9.

**D-6 (was OQ-6) — Night Shift is its own system, not a hub workload.**
It keeps its own daemon lifecycle, scheduler, concurrency control, budget
ledger and crash recovery. Hub is a service Night Shift calls; it is not a
platform Night Shift runs on, and the durable job queue is not adopted as the
scheduler.

The `nightshift fix <n>` one-shot mode stays, because a single-issue run is
useful to operators and essential to testing — not as a step toward running
under hub.

D-6 and D-1 are worth reading together, because they pull in opposite
directions and both hold. Night Shift *depends* on hub heavily — for issues,
memory, audit, sessions, secrets and carry-patch, over one endpoint with one
token — while *running* nothing on it. Depending on a service is not the same
as being a workload of it: Night Shift decides what work to do, when to do it,
how much to spend and what to do when something fails, and it keeps deciding
those things when hub is unreachable (REQ-NS-HUB-05). What it cannot do
without hub is discover work, which is a dependency on data, not on a runtime.
*Binds:* §5.6, §6.1, §10 Phase 1, R-6.

**D-7 (was OQ-7) — The gate command comes from local config only.**
`[gate] command` is read from `.nightshift/config.toml` and from nowhere else.
Hub's `CHECK_COMMAND` workspace variable stays a hub-side rebuild concern and
is never read as the daemon's gate. A command that runs arbitrary shell in a
worktree should have exactly one source, and it should be the one an operator
edits on the machine that runs it. *Binds:* §5.4, §8, REQ-NS-HUB-08.

### Still open

Nothing blocking. Two items are deliberately deferred rather than unanswered:
the tier-as-policy model (D-4) and subagent delegation (D-3), both scheduled
for reconsideration after Phase 3 with data.

---

## Appendix A: Deleted Surface

Removed outright, with the reason:

| Surface | Lines (approx.) | Reason |
|---|---|---|
| `session/backends/{claude,deepagents,google_adk,adk_tools,protocol,types,_retry}` | 2,073 | AgentKit owns the loop and the wires (F-15, F-16) |
| `session/review_parser.py` | 949 | schema-constrained tool output (F-21) |
| `knowledge/{migrations,review_store,fox_provider,db,duckdb_sink,summary_store,extraction,formatting}` | 3,704 | 90 % dead; live part is ~200 lines (F-18) |
| `core/models.py` registry + `PricingConfig` + `calculate_cost` | ~600 | `catalog` + `provider.ComputeCost` (F-17) |
| `nightshift/cost_helpers.py` (`nightshift_ai_call`, aux cost emission) | 197 | fourth model path (F-15) |
| `nightshift/spec_builder.build_afspec_from_triage` + `afspec` dependency | ~150 + a repo | markdown formatting (F-20) |
| `session/{convergence,auditor_output}` , `engine/{migrations,review_persistence,state}` | ~1,900 | spec-orchestrator surface (NG1) |
| `archetypes` `verifier` / `gate` entries + profiles | ~200 | NG1 |
| `core/json_extraction.py` | ~120 | no prose JSON to extract |
| `nightshift/pid.py` | 94 | advisory lock (REQ-NS-DAEMON-02) |
| `_startup.check_root_permission_mode` + backend root guards | ~90 | no subprocess (F-16) |
| `io/{spinner,progress,help,cli}` + `ui/*` (rich) | ~1,200 | two renderers off one event channel |
| `afissues` (`github.py`, `gitlab.py`, `gitea.py`, `protocol.py`, `_http.py`, `_ssrf.py`) | 1,879 | moves behind the Issue Service API into hub (D-1, and §8 of the [Issue Service PRD](issue_service_prd.md)) |
| `nightshift/platform_factory.py` | 279 | one client, one endpoint — no per-forge construction (D-1) |

Python dependencies dropped: `claude-agent-sdk`, `deepagents`, `google-adk`,
`google-api-core`, `anthropic[vertex,bedrock]`, `duckdb`,
`sentence-transformers`, `scikit-learn`, nineteen `tree-sitter-*` grammars,
`pydantic`, `tomlkit`, `pathspec`, `rich`, `click`, `afspec`, `afissues`.

Note that `afissues` is **relocated, not deleted**: its GitHub implementation
is ported into hub's GitHub backend (REQ-IS-5.5) rather than rewritten. What
is deleted is Night Shift's dependency on it.

---

## Appendix B: Behaviour Change Ledger

Every intentional divergence from today's behaviour, with the config key that
restores the old behaviour where one exists.

| # | Today | Proposed | Restore |
|---|---|---|---|
| B-1 | Daemon stops at 50 % of `max_cost` | Stops at `max_cost`, with per-issue reservation | — (F-1 is a bug) |
| B-2 | Supersession and staleness **close** issues | They **comment** by default | `[triage] supersession_action = "close"` |
| B-3 | Staleness runs with an empty diff | Skipped when no diff is available | — (F-3 is a bug) |
| B-4 | Worktree creation deletes the remote branch | Never touches the remote | — (F-4 is a bug) |
| B-5 | `af:no-change` issues are re-processed forever | Excluded from polling | remove the label |
| B-6 | Unbounded retries per issue | `max_attempts_per_issue`, then `af:failed` | set it very high |
| B-7 | Reviewer PASS can merge a red build | Gate exit code is authoritative | `[gate] required = false` |
| B-8 | Drain returns "clear" on a platform error | Drain returns an error | — (F-8 is a bug) |
| B-9 | Project-local skills/profiles always loaded | Untrusted by default | `[skills] trust_project = true` |
| B-10 | `AUTO_REBUILD_*` forced to `false` at startup | Left alone | `[carry_patch] manage_auto_rebuild = true` |
| B-11 | Three backends selectable | One runtime, many providers | choose a provider, not a backend |
| B-12 | Local DuckDB knowledge store | SQLite ledger + hub | — |
| B-13 | Cost computed from a local price table | Cost from the model catalog | — |
| B-14 | `permission_mode` config key | Removed; tool policy is the boundary | `[security] allowlist*` |
| B-15 | Audit `tool.invocation` carries a param summary | Carries an argument hash | — (NFR-10) |
| B-16 | Daemon talks to GitHub/GitLab/Gitea directly | Talks to hub's Issue Service API; hub bridges the tracker | — (D-1) |
| B-17 | `[platform]` + `GITHUB_PAT` / `GITLAB_TOKEN` / `GITEA_TOKEN` | `[hub]` + `AF_HUB_TOKEN`, one endpoint and one token for everything | — (D-1) |
| B-20 | Hub optional; daemon runs local-only without it | Hub required — it serves the issue API | — (D-1, REQ-NS-HUB-05) |
| B-18 | Carry-forward findings in local DuckDB | In hub; absent when hub is down | — (D-5) |
| B-19 | The closed-between-poll-and-dispatch guard never fires | Fires, because `Issue.state` exists | — (F-28 is a bug) |
