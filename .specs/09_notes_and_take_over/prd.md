---
spec_id: "09"
spec_name: "notes_and_take_over"
title: "Tell a running item something, or take it over"
status: "active"
created_at: "2026-11-28T10:22:56Z"
intent_hash: "dd06f380a28b952c86c0af019bd260ece75a126de1c02aaaf48b8d5978e29208"
contract_hash:
  requirements.json: "ddd51fcd479a8a83239d0a0c21efbdf39d15361c9bceafaa4bd916839416931c"
  tasks.json: "dd3cae50e2f5a7c6ea235a450e349fb3005623918d06556951ff79e15106bf1d"
schema_version: 3
source: "docs/prd/02-drive-work-items-through-the-hub.md"
---

# Tell a running item something, or take it over

## Intent

nightshift drives a work item without the person, and a person who watches
an item go wrong needs two things short of abandoning it. They can tell the
item something: a note is kept, never interrupts a running session, and
reaches the item's next session as a finding that session must follow. And
they can take the item over: nightshift stops working on it, the person
pushes to its branch themselves, and when they give the item back
nightshift treats what they pushed as it treats an agent's work.

## Goals

- **The person can say something to a running item.** `nightshift item note`
  writes a note to the item's thread, and the note reaches the item's next
  session as a finding of the person which that session must follow.
- **A note never costs a session its work.** A session that is running is
  not interrupted; the note waits for the next one.
- **A note is never too late while the item has not landed.** In every
  state before the landing there is a next session: the planner's next
  round, the implementer's first session, the next rework, or a rework that
  a withdrawn landing starts.
- **The person can take an item over.** `nightshift item take-over`, or the
  option `take_over` of five decision kinds, ends the item's session, holds
  the item, and tells the person where to push.
- **A taken-over item goes on where the person left it.** `nightshift item
  resume` gives it back; a version the person pushed is gated and reviewed
  as any other.

## Non-goals

This package is the ninth of ten written from
`docs/prd/02-drive-work-items-through-the-hub.md`. What its neighbourhood
holds that is another package's:

- **`01_daemon_api_and_credentials`**: the API server, the client's command
  table, the store, the event log, the hub client and the fake hub. This
  package adds two routes, two commands and two store migrations.
- **`02_workspaces_and_policy`**: workspaces and their policy; nothing of
  it is used here directly.
- **`03_work_items_and_inbox`**: work items, their states and transitions,
  `item show` and `item list` with their markers, threads, and the decision
  machinery with its registry. This package adds an option to five kinds
  through that registry and registers one marker.
- **`04_sessions_and_scheduling`**: sessions, the scheduler, pausing and
  `nightshift item resume` with its route, the `paused` marker, and the
  decision kinds `session_failed` and `session_limit` with their other
  options. This package holds a taken-over item with that package's pause
  and gives it back through that package's resume.
- **`05_planning`**: the planner's rounds, what a planner is told and how a
  finding is written for it, the `plan` decision and its options, the
  item's task fork and change. This package hands the planner a note as a finding
  of the person.
- **`06_implementation_and_review`**: the implementer's sessions and
  reworks, what a rework is told and how a finding is written for it, the
  gate, the review, a reviewer's findings starting a rework, and the
  decision kinds `review_disagreement` and `blocked` with their other
  options. This package hands the implementer a note as a
  finding of a rework, and relies on that package's steps to gate a
  version the person pushed.
- **`07_landing`**: the landing, the check `nightshift-release` and its
  report to the hub, and the decision kind `conflict` with its other
  options. This package asks it to withdraw a landing that has not
  happened.
- **`08_health_and_publishing`**: everything after a landing. An item that
  has landed takes no note and cannot be taken over.
- **`10_rules_brief_and_forge`**: the count of items taken over among the
  outcome numbers; this package keeps every take-over in its store and
  writes a thread entry of kind `taken_over` for each.

Also out: interrupting a session for a note, a note to a workspace or to
several items, editing or withdrawing a note, a credential for the person's
pushes (they use their own), and any difference in how a person's version
is gated, reviewed or landed.

## Background

Packages 01 to 04 leave a daemon with work items in states, threads,
decisions with a registry of kinds, and a scheduler that calls a step
function per item state, runs sessions and can pause an item. Packages 05
to 07 plan an item, implement and review it, and land it. Every command,
route and Go package this document names for notes and taking over is new:
`internal/note` and `internal/takeover`.

What this package uses of the earlier ones, by their names:

- `internal/api` `Handle`, `internal/cli` `Register`, migrations of
  `internal/store`, `internal/event` `Append` and `Subscribe`, and the
  hub client's `Available` (package 01);
- `internal/item`: `Get`, `Transition`, `RegisterMarker`; `internal/inbox`:
  `Append`, `AddOption`, `OnAnswer`, `Open`, `Close`, `OpenDecisions(item)`
  (package 03);
- `internal/sched`: `Scheduler.Paused`, `Scheduler.OnResume`, the pause of
  an item and the route `POST /api/v1/items/{number}/resume` (package 04).

What this package uses of packages 05, 06 and 07 is listed under *What
this package uses of packages 05, 06 and 07*, with the two things its tasks
add to them.

What this package uses of Parley, as its `docs/api.md` states at commit
`7eabfb9`:

- Every workspace and every task fork is a git remote over smart HTTP. Its
  URL is the workspace object's `hub_url`, `<external URL of the
  hub>/git/<org slug>/<workspace slug>.git`. git authenticates with HTTP
  Basic, the credential as the password; a PAT needs `git:write` to push.
- A push to a branch that is the source of a draft or open change records
  the next version of that change, whoever pushes. Versions are numbered
  from 1.
- `GET /api/v1/workspaces/{slug}/changes/{number}` answers the change
  object, whose `version` is the number of the current version.
- `PUT /api/v1/workspaces/{slug}/changes/{number}/checks/{name}` reports a
  check on a version with `status` `pending`, `passed` or `failed`,
  `version` and a `summary` of at most 2,000 characters.

## Requirements

Constraint: Linux and macOS; Windows is best effort. Constraint: the
`nightshift` client speaks only the daemon's HTTP API, so every command
below has a route.

### A note

`nightshift item note <N> "<text>"` (1 to 20,000 characters; outside that
the command exits 2) tells a running item something. The note is written to
the item's thread and reaches the item's next session as a finding of the
person, which that session must follow. A session that is running is not
interrupted. What "next session" means depends on the item's state:

- `new` or `planning`: the planner's next round, or one more round when
  the reviewer had agreed already;
- `awaiting_approval`: the planner is started with the note; the open
  `plan` decision is closed as `superseded by a note` and a new one opens
  when planning ends;
- `ready`: the implementer's first session, beside the package;
- `implementing` or `reviewing`: the next rework, which nightshift starts
  when the running session ended, also when the gate and the reviewer had
  nothing to send back;
- `landing`, not yet landed: `nightshift-release` is reported `failed`
  (`note from the owner`), a rework is started with the note, and its
  version is gated and reviewed again.

The command exits 1 for an item that is `verifying`, `publishing`, `done`
or `abandoned` (`item <N> has landed; create a new item`). Several notes
given before the next session reach it together, in the order given.

The route behind the command is `POST /api/v1/items/{number}/notes` with
`{"text"}`, answering `201` with the note:

```json
{"id": 4, "item": 21, "text": "Keep the old log line",
 "time": "2026-11-28T10:00:00Z", "delivered": null}
```

`delivered` is `{"session", "time"}` once a session that was given the note
completed. The command prints `note 4 added to item 21`.

**The queue.** nightshift keeps every note in its store and hands it, when
it is given, to the package whose session is next: to the planner
(`AddFinding`, or `Revise` for an item that waits for approval) or to the
implementer (`Rework`, as a binding finding with the id `note-<id>`). Those
packages keep it for their next session, in the order given, and keep it
when a session failed or was interrupted. A note is pending until a session
that was given it has completed; this package finds that out when a session
ends, and writes it to the thread.

### Taking over

`nightshift item take-over <N>`, or the option `take_over` of a
`session_failed`, `session_limit`, `review_disagreement`, `conflict` or
`blocked` decision, hands the item's branch to the person. The item's
running session is ended, recorded as interrupted; no session starts for
it; it is not landed; and `item show` and `item list` mark it `taken
over`. The command prints the fork's git URL and the branch. The person
pushes to that branch with a credential of their own; every push is a
version of the item's change, as a session's is. `nightshift item resume
<N>` gives the item back: when the branch has a version newer than at the
take-over, that version is gated and reviewed as any other; when it has
not, the item continues with the step it was at. A reviewer's findings on
the person's version start a rework, as on any version. `take-over` exits
1 for an item that has no change yet, or is `verifying`, `publishing`,
`done` or `abandoned`, with the reason.

The route behind the command is `POST /api/v1/items/{number}/take-over`,
answering `200`:

```json
{"item": {"number": 21, "state": "implementing",
          "markers": ["paused", "taken over"]},
 "git_url": "https://hub.example/git/acme/demo--i21.git",
 "branch": "item-21", "version": 3}
```

with `item` the whole item object of package 03. The command prints:

```text
item 21 taken over
git https://hub.example/git/acme/demo--i21.git
branch item-21
```

**One command gives back and resumes.** `nightshift item resume <N>` is
package 04's command and route, and they stay as they are. A taken-over
item is held by package 04's pause flag of the item, so the resume clears
that flag as for any paused item and calls the functions registered for a
resume; the one this package registers ends the take-over when the item has
one, and does nothing for an item that was only paused. An item that is
neither paused nor taken over is refused by package 04 (`item <N> is not
paused`).

### What this package uses of packages 05, 06 and 07

By their names, read in their packages:

- **`internal/plan`** (05): `Service.AddFinding(ctx, item, text)` stores a
  finding of the person for the planner's next revision and, when the
  reviewer agrees while one is pending, has one more revision run for it;
  `Service.Revise(ctx, item, comment)` stores the comment the same way and
  moves an item from `awaiting_approval` to `planning`.
- **`internal/change`** (05): `Service.Get(ctx, item)` answers a `Record`
  with the item's `Workspace`, `Fork`, `GitURL`, `Branch` and `Change`
  (0 while no change is open), or `change.ErrNotFound`.
- **`internal/impl`** (06): `Service.Rework(ctx, item, findings)` keeps
  findings for the item's next implementer session, marks those of the
  source `person` binding, and moves an item from `reviewing` or `landing`
  to `implementing`; the step of an `implementing` item starts a rework
  when findings wait, before it gates, and gates a current version that
  has no gate verdict with no session before it. `Service.History(item)`
  lists the findings an accepted rework was given; `Service.Version(ctx,
  item)` answers the change's current version as `Number`.
- **`internal/land`** (07): `Service.Withdraw(ctx, item, summary)` waits
  until no land of the item is being answered and reports
  `nightshift-release` as `failed` with the summary, or answers
  `land.ErrLanded`; `Service.AddCondition(name, fn)` adds a landing
  condition. Its `landing` step moves an item whose current version is not
  gated and reviewed to `implementing`.

Two things those packages do not have are added to them by this package's
tasks, and are new there:

- **`plan.Service.PendingFindings(ctx, item)`**: the number of the item's
  pending findings of the person, from which this package tells that a
  note reached a planner session.
- **The input `notes` of a first implementation** in `internal/impl`:
  package 06 gives findings to a rework only, because `af-impl --rework`
  refuses a package with open tasks, so a note to a `ready` item would
  wait for the rework after the first session. The source PRD gives it to
  the first session, beside the package: the first session's request gets
  the person's waiting findings as an input of their own, named in its
  prompt.

### End-to-end flows

**A note while the implementer runs.** Item 21 is `implementing` and its
session runs. The person runs `nightshift item note 21 "Keep the old log
line"`. The session is not interrupted and finishes. Because a note waits,
nightshift starts a rework with the note as a finding before anything else;
the implementer's new version is gated and reviewed, and the item goes on
to `landing`. The thread shows the note and
the session it reached.

**A note supersedes a plan that waits for approval.** Item 22 is
`awaiting_approval` with a `plan` decision in `Action`. The person runs
`nightshift item note 22 "Use the existing config key"`. The decision is
closed as `superseded by a note`, the planner runs once more with the note,
the reviewer agrees, and a new `plan` decision opens.

**A take-over from a decision.** The gate of item 21 failed twice and a
`session_failed` decision is open. The person answers `take_over`. The item
is marked `taken over` and its thread names the fork's git URL and the
branch. The person pushes a fix to the branch with their own credential;
the hub records a new version; nightshift starts nothing. The person runs
`nightshift item resume 21`. The new version is gated, reviewed and landed.

## Design Decisions

From the source PRD, by its numbers:

- **21. API and command-line client in version 1.** Kept from PRD 01.
- **36. Work is sent back through `af-impl`'s rework mode.** The owner
  decided it. Here: a note to an item that is being implemented reaches it
  as a finding of a rework.
- **37. Notes to a running item and taking an item over** are two of the
  seven changes the owner decided after a comparison with other internal
  coding agents.
- **39. A note is queued for the next session and never interrupts one.**
  Chosen there: an interrupted session's work is lost, and the rework mode
  already takes a person's finding.
- **40. A person's pushed version is gated and reviewed as an agent's.**
  Chosen there: taking over changes who writes, not what must hold before a
  landing.

Chosen here:

1. **Two Go packages: `internal/note` and `internal/takeover`.** Why: a
   note needs no take-over and a take-over no note, and each has its own
   table, route and command.
2. **Routes: `POST /api/v1/items/{number}/notes` and `POST
   /api/v1/items/{number}/take-over`.** Why: the client speaks only the
   daemon's API, and the source PRD names the commands alone.
3. **A text outside 1 to 20,000 characters is refused by the client with
   exit code 2 and by the API with `400 invalid_request`; characters are
   Unicode code points and the text is kept as given.** Why: it is what
   package 03 decided for `item new`.
4. **Notes are stored by this package and handed, when they are given, to
   the planner (`plan.Service.AddFinding`, `Revise`) or the implementer
   (`impl.Service.Rework`), which queue them for their next session.** Why:
   packages 05 and 06 already keep the person's findings in order, across
   failed sessions and restarts, and run the extra round or rework for
   them; a second queue here would have to be kept in step with theirs.
   Package 04's scheduler offers no hook into a session's request, so
   nothing can be injected there. `Pending`, `MarkDelivered` and
   `Source()` of `internal/note` stay as the record of what reached a
   session.
5. **A note is pending until a session that was given it completed, and
   this package finds that out at each `session.ended` event:** a note
   handed to the implementer is delivered when `impl.Service.History`
   holds its finding `note-<id>`; the notes handed to the planner are
   delivered when `plan.Service.PendingFindings`, which this package adds,
   answers 0. Why: neither package reports a delivery, and the thread
   should say which session a note reached.
6. **A note is written to the thread as an entry of kind `note` with its
   text, and its delivery as an entry of kind `note_delivered`; neither
   marks the thread unread.** Why: the person wrote the note, and the
   session's own entries follow.
7. **In `new`, `planning`, `ready`, `implementing` and `reviewing` a note
   is handed over and nothing else.** For an item that is `reviewing`,
   package 06's `Rework` moves the item to `implementing` at once; the
   running review is not ended. A note closes and answers no decision
   there: an item that waits on a decision in one of those states gets the
   note with the session that follows the person's answer. Why: the source
   PRD closes a decision for a note only in `awaiting_approval`.
8. **A note to an item in `landing` closes every open decision of the item
   as `superseded by a note`.** Why: the source PRD starts a rework, and
   the scheduler calls no step for an item with an open decision; an
   `approval` decision about the version that is being reworked asks about
   a version that will not land.
9. **The landing is withdrawn by package 07, in one function that excludes
   a land request of the same item, and not by this package reporting the
   check itself.** Why: between "is it landed?" and "report
   `nightshift-release` failed" a land can complete; only the package that
   requests the land can close that gap. When the change has landed, the
   note is refused as for `verifying`.
10. **A note whose effect needs the hub and cannot have it is refused with
    `503 hub_unavailable` (or `502 hub_error` with the hub's error) and
    nothing is stored.** Why: a note in the thread whose rework never
    started would say something untrue; the person runs the command again.
11. **A taken-over item is held by package 04's pause flag of the item,
    and this package keeps beside it the take-over with the version at
    that moment; it also registers the landing condition `taken over`
    with package 07's `AddCondition`.** Why: ending the session as
    interrupted, starting none, not landing and reporting
    `nightshift-release` failed are what a pause already does in packages
    04 and 07, and `item resume` then serves both without a second route.
    The item carries both markers, `paused` and `taken over`.
12. **This package adds `Scheduler.HoldItem(ctx, item)` to `internal/sched`:
    the pause of an item as a Go function, which also sets the item's own
    flag while its workspace is paused and is no error for an item already
    paused.** Why: package 04 offers the pause as a route alone, and a
    workspace's resume must not release an item the person took over.
13. **`take-over` records the current version from package 06's
    `impl.Service.Version` and prints the git URL and the branch of
    package 05's `change.Service.Get`; an item whose record has no change
    number has "no change yet". When the version cannot be read the
    command is refused with `503 hub_unavailable` or `502 hub_error`, and
    a `take_over` answer is tried again by the inbox every 30 seconds.**
    Why: without the version the resume cannot tell whether the person
    pushed.
14. **`take-over` by the command closes the item's open decisions of the
    five kinds that carry `take_over`, with the reason `taken over`; other
    open decisions stay.** Why: the command and the option are the same
    act, and a `session_failed` decision left open would hold the item
    after the resume.
15. **`take-over` of an item that is taken over already is refused
    (`item <N> is already taken over`); a `take_over` answer for one is
    carried out as nothing.** Why: a handler may be called twice for one
    answer (package 03).
16. **A `take_over` answer for an item with no change yet writes the entry
    `take-over refused: item <N> has no change yet` and opens the decision
    again, with the question, evidence, recommended option and `blocks` of
    the answered one.** Why: the option belongs to the kind, not to the
    decision, so a planner that failed before its first push offers it
    too; the person must still be able to choose `retry` or `abandon`.
17. **A `take_over` answer takes effect at once, also for a paused item.**
    Why: the item is held either way, and package 04 defers only its own
    options.
18. **At the resume, "newer" is a current version number greater than the
    one recorded at the take-over. This package starts nothing for it:
    package 06's `implementing` step gates a current version that has no
    gate verdict, and its `reviewing` step and package 07's `landing` step
    move the item to `implementing` for one. For an item in
    `implementing`, `reviewing` or `landing` with a newer version this
    package closes the item's open decisions as `superseded by version
    <n>`, so that the scheduler calls the step; for an item in `planning`,
    `awaiting_approval` or `ready` the item continues with its step, which
    works on the branch as the person left it.** Why: a decision about an
    older version asks about what will not land, and before an
    implementation there is no gate to run.
19. **When the version cannot be read at the resume, the take-over ends
    all the same and no decision is closed.** Why: package 04's resume has
    cleared the pause by then and cannot be refused from a resume
    function, and the steps find a version that is not gated by
    themselves.
23. **A note to a `ready` item reaches the first implementation in an
    input of its own, `notes`, which this package adds to `internal/impl`.**
    Why: the source PRD says "the implementer's first session, beside the
    package", and package 06 gives findings to a rework alone.
20. **The marker `taken over` ends with the take-over or with the item
    being `abandoned`; a note to a taken-over or paused item is accepted
    and waits.**
21. **Thread entries: `taken_over` (`taken over at version <n>; push to
    branch <branch> of <git URL>, then run nightshift item resume <N>`) and
    `given_back`; neither marks the thread unread. Events: `item.updated`
    when a take-over starts and when it ends.**
22. **A decision kind that is not declared when this package adds
    `take_over` ends the start of the daemon (`take_over: decision kind
    "<kind>" is not declared`).** Why: an option that silently went
    missing would be found by the person at the worst moment.

## Dependencies

- **`docs/prd/02-drive-work-items-through-the-hub.md`**: the source PRD,
  of which this package specifies *Notes and taking over* and the flow *A
  small fix, a note and a take-over*.
- **`01_daemon_api_and_credentials`** (status `draft`): `Handle`,
  `Register`, the store's migrations, the event log, the hub client and
  `Available`, `internal/hubtest` and the end-to-end helper.
- **`03_work_items_and_inbox`** (status `draft`): `internal/item` and
  `internal/inbox` as *Background* names them.
- **`04_sessions_and_scheduling`** (status `draft`): the pause of an item,
  `Scheduler.Paused`, `Scheduler.OnResume` and the resume route.
- **`05_planning`** (status `draft`): `plan.Service.AddFinding` and
  `Revise`, and `change.Service.Get`.
- **`06_implementation_and_review`** (status `draft`):
  `impl.Service.Rework`, `History` and `Version`, and its steps, which gate
  a person's version.
- **`07_landing`** (status `draft`): `land.Service.Withdraw` and
  `AddCondition`.
- **Parley** (`agent-fox-dev/parley`), the hub: its packages
  `17_git_smart_http`, `19_git_push_to_change`,
  `22_change_opening_and_versions` and `28_approvals_and_checks`, each with
  every task `done` on 2026-11-27.

## Verified External API

- **Parley's REST API**, read in `docs/api.md` of `agent-fox-dev/parley`
  at commit `7eabfb9`: the git remote of a workspace or fork
  (`hub_url`, `<external URL of the hub>/git/<org slug>/<workspace
  slug>.git`, `null` when the hub has no external URL), smart HTTP with
  HTTP Basic and `git:write` for a push; a push to a branch that is the
  source of a draft or open change records the next version, "whatever
  moves it"; `GET /api/v1/workspaces/{slug}/changes/{number}` answering the
  change object with `version`, `head_sha`, `state` and `source
  {workspace_slug, branch}`; `PUT
  /api/v1/workspaces/{slug}/changes/{number}/checks/{name}` with `status`,
  `version`, `summary`, scope `checks:write`.
- **Packages 01 to 07**, read in `.specs/` as drafts: the names under
  *Background* and *What this package uses of packages 05, 06 and 07*.
- **Not verified, because this package adds them:**
  `plan.Service.PendingFindings` and the input `notes` of a first
  implementation in `internal/impl`.

## Glossary

- **note**: a text the person gives a running item, which its next session
  must follow.
- **pending**: a note no completed session was given yet.
- **finding of the person**: a finding a session must fix and may not
  answer; a note reaches a session as one.
- **findings source**: the two functions, `Pending` and `Delivered`, of
  `note.Service.Source()`, over the notes no completed session was given.
- **taken over**: an item whose branch the person works on themselves,
  until they resume it.
- **give back**: what `nightshift item resume` does to a taken-over item.
- **hold**: to set the pause flag of an item, with everything a pause does.
- **version**: one head of a change's source branch, as the hub numbers
  them from 1.
- **hub**: a Parley server (`parleyd`).
- **task fork, change, check**: as Parley defines them (`docs/api.md`).
