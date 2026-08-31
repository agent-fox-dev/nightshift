# Architecture Guide

This guide describes the internal architecture of Night Shift. It is written
for engineers who want to understand how the system works before reading the
source code. The documents stay at the conceptual level — no code snippets,
no method signatures, no class hierarchies.

For configuration specifics, see the
[Configuration Reference](../config-reference.md).

## Architectural Principles

**Isolation by default.** Each coding session runs in its own git worktree on
its own feature branch. Multiple fixes can run simultaneously without stepping
on each other. Integration happens through a serializing merge lock.

**Separation of concerns through archetypes.** Four archetypes divide labor:
Maintainer (triage), Coder (implementation), Reviewer (quality gate), and
a merge agent (conflict resolution). Review agents cannot modify code.

**Graceful degradation everywhere.** Every component handles failure
non-fatally. If knowledge retrieval fails, the session proceeds without
knowledge context. If knowledge ingestion fails, the session outcome is
unaffected.

## Document Map

### [Night Shift](04-night-shift.md)

How the fix daemon works. Covers the fix pipeline (triage, coder-reviewer
loop, harvest), issue selection and dependency ordering, batch triage,
drain behavior, cost limits, and staleness detection.

### [Knowledge System Architecture](05-knowledge-system-architecture.md)

How the system remembers. Covers the `KnowledgeProvider` protocol, the
three-category retrieval pipeline (review findings, cross-group findings,
same-spec summaries), the closed-loop finding lifecycle
(create → inject → supersede), session summary storage, and the DuckDB-backed
knowledge store.

## See Also

For a single-document overview, see [Night Shift Architecture](../architecture.md).

## Reading Order

Read the Night Shift document first for the pipeline overview, then the
Knowledge System document for how institutional memory works across sessions.
