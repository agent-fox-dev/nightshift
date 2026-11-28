---
spec_id: "03"
spec_name: "work_items_and_inbox"
title: "Take in work items and show them in an inbox"
status: "active"
created_at: "2026-11-28T10:01:09Z"
intent_hash: "d4a066f2e84d28710ee865a52707f62f884914a4389bce3959ec1adadc4556d0"
contract_hash:
  requirements.json: "c223b6b6579eecee408a51df862dc63e3f153389c4089050bb568d32272480f7"
  tasks.json: "4b0ed032f6b5a7e8ef58a69bf71cecf3d4306ab06c7a5d34f92311065ee25f68"
schema_version: 3
source: "docs/prd/02-drive-work-items-through-the-hub.md"
---

# Take in work items and show them in an inbox

## Intent

nightshift owns a project's work, and a person steers it. Every piece of
work, whether typed as text, imported from an issue or imported as a spec
package, becomes a work item with one state out of a fixed set, and
everything that happens to it is written to a thread the person can read.
Where nightshift cannot go on by itself it asks through a durable decision
with fixed options, which waits in an inbox until the person answers, and
it tells the person that they are needed.

## Goals

- **One path for every kind of work.** A spec package, an issue imported
  from a forge and a change request typed as free text each become a work
  item with a number, a kind, a state and one thread.
- **One state machine for every item.** An item is in one of eleven states,
  and it moves only along the transitions this package allows; the packages
  that plan, implement, land and publish drive it through one function.
- **The inbox shows two things.** `Unread` holds what finished or changed
  since the person last looked; `Action` holds every decision that waits
  for them. Both are read over the HTTP API and through the `nightshift`
  command.
- **The person is never waited on by a process.** A question for the person
  is a durable decision. It never takes an option by itself, it survives a
  restart, and its answer takes effect within 5 seconds.
- **The person is told when they are needed.** A configured command runs
  when a decision opens, when a reminder is due and when the hub has been
  unreachable for 15 minutes.
- **The person can end an item.** `nightshift item abandon` ends an item
  that has not landed.

## Non-goals

This package is the third of ten written from
`docs/prd/02-drive-work-items-through-the-hub.md`. What its neighbourhood
holds that is another package's:

- **`01_daemon_api_and_credentials`**: the daemon, the API server, the
  client's base, the event log and its stream, the hub client and its
  monitor. This package appends the first events and adds routes and
  commands.
- **`02_workspaces_and_policy`**: registering a workspace, its settings and
  state, the policy commands, `nightshift workspace list` and `nightshift
  workspace remove` as commands. This package adds the item counts to the
  first and the refusal and the removal of threads to the second.
- **`04_sessions_and_scheduling`**: sessions, the scheduler, the daily
  limit with its `Unread` entry, pausing and the `paused` marker, the
  `session_failed` and `session_limit` decisions, and ending an abandoned
  item's sessions (a hook it registers here).
- **`05_planning`**: starting the planning of an item a person created,
  `nightshift item plan`, acting on `--no-plan-approval` (this package only
  stores it), the `plan` decision, moving an imported package from `new` to
  `ready`, and abandoning an abandoned item's change (a hook it registers
  here).
- **`06_implementation_and_review`**: the `blocked` and
  `review_disagreement` decisions, and the items it creates from a
  session's open items through the function this package offers.
- **`07_landing`**: the `approval`, `conflict` and `policy` decisions, and
  the notifications `workspace_held`.
- **`08_health_and_publishing`**: the `main_red` and `origin` decisions,
  the entries for a repair and a revert, and the notification
  `workspace_red`.
- **`09_notes_and_take_over`**: `nightshift item note`, `nightshift item
  take-over`, the option `take_over` (added through the registry of this
  package) and the `taken over` marker.
- **`10_rules_brief_and_forge`**: acting on the `record` member of an
  answer (this package stores it), the `rule` decision, the content of the
  daily brief and the notification `brief` (this package offers the brief
  thread), and writing back to an imported issue.

No decision kind is declared by this package: each kind, its options and
what an answer does belong to the package named above. This package is the
machinery they register into.

Also out, for every package of that PRD: intake from forge webhooks, a web
or desktop client, an MCP facade for the inbox, and routing a decision to
one of several people.

## Background

Package 01 gives this package a daemon with an authenticated API under
`/api/v1`, a client with a command table, a store with migrations, an
event log whose events the stream `GET /api/v1/events` serves, and a hub
client with a monitor that knows since when the hub is unreachable and
which token the hub stopped accepting. Package 02 gives it registered
workspaces and a way to get a workspace's repository from the hub.

Parley (`agent-fox-dev/parley`) is the hub. What this package uses of it,
as its `docs/api.md` states at commit `7eabfb9`:

- `GET /api/v1/workspaces/{slug}/issues/{number}` answers the issue thread
  `{issue, comments, comments_next_cursor, comments_error}` with the first
  page of at most 100 comments, oldest first. An issue is `{number, title,
  body, state, url, author {login}, labels, created_at, updated_at,
  closed_at, is_pr}`; `labels` holds the names. When the comments could
  not be read the answer is still `200`, with `comments_error`
  `{error_type, message}`.
- `GET /api/v1/workspaces/{slug}/issues/{number}/comments` lists comments
  `{id, body, author {login}, created_at, updated_at, url}` oldest first as
  `{"items", "next_cursor"}`, read on with `cursor`.
- Both need `forge:read`, which the worker token's `forge:write`
  satisfies. A forge failure is the hub's error envelope with a
  `forge_…` type (`404 forge_not_found`, `502 forge_unreachable`, …).

`afspec` (`github.com/agent-fox-dev/spec/afspec`) loads and validates a
spec package and tells which of its tasks are open.

The inbox follows the pattern of Pizza Bot
(`github.com/agent-fox-dev/pizza-bot`): two global views over threads,
`Unread` for finished work and `Action` for durable approval requests,
served to thin clients over HTTP and server-sent events.

## Requirements

Constraint: Linux and macOS; Windows is best effort. Constraint: the
`nightshift` client speaks only the daemon's HTTP API, so every command
below has a route.

### Work items and intake

A work item belongs to one workspace and has a number unique in nightshift,
a kind (`spec`, `defect`, `change_request`), a title, its source, a state
and one thread. Numbers start at 1, rise by one over all workspaces and
are never given twice. There are three ways to create one:

- `nightshift item new <slug> --text "<text>" [--kind defect|change_request]
  [--no-plan-approval]` creates an item from free text (default kind
  `change_request`). The text is 1 to 20,000 characters; outside that the
  command exits 2. The item's title is the first line of the text, cut to
  80 characters.
- `nightshift item import <slug> --issue <N> [--no-plan-approval]` reads the
  issue and its comments through the hub's forge routes. The kind is
  `defect` when the issue carries `af:defect` or a label named `bug`, else
  `change_request`. The title is the issue's. An issue already imported is
  refused with exit code 1, naming the existing item. An issue that cannot
  be read is refused with the hub's error.
- `nightshift item import <slug> --spec <NN>` takes an active spec package
  on the default branch. The title is the package's. It is refused with
  exit code 1 when the package does not load (with the validator's first
  finding), is not `active`, has no task `pending` or `in_progress`, or is
  already an item.

Each prints the new item's number and its thread's URL path:

```text
item 12
thread /api/v1/threads/31
```

An item is created in the state `new`. This package stores
`--no-plan-approval` with the item; package 05 starts the planning and acts
on it.

The routes behind the commands:

| Route | Body | Answer |
|---|---|---|
| `POST /api/v1/items` | `{"workspace", "text", "kind"?, "no_plan_approval"?}` | `201` with the item |
| `POST /api/v1/items/import` | `{"workspace", "issue": N, "no_plan_approval"?}` or `{"workspace", "spec": "NN"}` | `201` with the item |
| `GET /api/v1/items` | `workspace=<slug>`, `state=<s>` | `200` `{"items": […]}`, by number |
| `GET /api/v1/items/{number}` | | `200` with the item |
| `POST /api/v1/items/{number}/abandon` | | `200` with the item |

An item in JSON:

```json
{"number": 12, "workspace": "demo", "kind": "defect",
 "title": "Greeting drops the name", "state": "new",
 "state_since": "2026-11-28T10:00:00Z", "created_at": "2026-11-28T10:00:00Z",
 "plan_approval": true,
 "source": {"type": "issue", "issue": 7, "url": "https://…"},
 "parent": null, "children": [],
 "thread": "/api/v1/threads/31",
 "waiting_on": null, "markers": []}
```

`source.type` is `text`, `issue`, `spec` or `item` (for an item created
from another, see below). `GET /api/v1/items/{number}` adds the source's
content: the `text`; the issue's `title`, `body`, `labels` and `comments`;
or the package's `spec_id`, `spec_name` and directory.

A work item is in one of these states:

| State | Meaning |
|---|---|
| `new` | Created; no session has worked on it |
| `planning` | A plan is being drafted or reviewed |
| `awaiting_approval` | A `plan` decision in `Action` asks the person to approve the plan |
| `ready` | Approved, or an imported package; waits for a free session, the daily limit or a dependency |
| `implementing` | An implementer session works in the item's fork |
| `reviewing` | The reviewer works on the change's current version |
| `landing` | The gate and the review passed; the land is requested or waits on a person or on the workspace |
| `verifying` | Landed; the health command runs |
| `publishing` | Healthy; the push to origin is requested or waits on a person |
| `done` | Landed, the main branch healthy, and the commit on origin |
| `abandoned` | Ended without landing, by the person |

The packages that drive an item move it through one function, which allows
these transitions and refuses every other, a move to the state the item is
in among them:

| From | To |
|---|---|
| `new` | `planning`, `ready`, `abandoned` |
| `planning` | `awaiting_approval`, `ready`, `abandoned` |
| `awaiting_approval` | `planning`, `ready`, `abandoned` |
| `ready` | `implementing`, `abandoned` |
| `implementing` | `reviewing`, `abandoned` |
| `reviewing` | `implementing`, `landing`, `abandoned` |
| `landing` | `implementing`, `verifying`, `abandoned` |
| `verifying` | `publishing`, `awaiting_approval` |
| `publishing` | `done` |
| `done`, `abandoned` | none |

Every transition is written to the item's thread and to the event log.

An item that waits for a decision keeps its state and names the decision in
`waiting_on`: the id, the kind and the question of the item's oldest open
decision, or `null`. `nightshift item list [<slug>] [--state <s>]` lists
items, one line each: number, workspace, state, markers, what it waits on,
title. `nightshift item show <N>` prints one with what it waits for.

`nightshift item abandon <N>` ends the item's sessions, abandons its change
and sets `abandoned`; it is refused with exit code 1 for an item that is
`verifying`, `publishing` or `done`, and for one already `abandoned`. The
first two are hooks packages 04 and 05 register; this package calls them in
the order they were registered, closes the item's open decisions with the
reason `item abandoned`, and sets the state.

**An item made from another.** Packages 05 and 06 create items from an
item: the parts of work too large for one package, and the open items of an
implementation. This package offers the function: the new item is in the
state `new`, of the kind and with the text it is given, linked to the item
it came from (`parent`, and the parent's `children`), and the parent's
thread gets an entry naming it. Such an item is not planned until the
person asks (package 05).

**Workspaces.** `nightshift workspace list` (package 02) shows with each
workspace its counts of open work items: how many are in each state other
than `done` and `abandoned`. `nightshift workspace remove <slug>` is refused
with exit code 1 while the workspace has a work item that is neither `done`
nor `abandoned`, naming them (`workspace demo has open work items: 12
(ready), 14 (landing)`); otherwise the workspace's items and all of its
threads are removed from nightshift with it. The counts are the member
`items` of the workspace object (`{"ready": 2, "landing": 1}`), printed as
`3 open (2 ready, 1 landing)` or `0 open`.

### The inbox

Every work item has one thread; a workspace has one more for what concerns
no single item (the workspace turning `red` or `held` with no item to
blame, the daily limit, the hub's outage); the installation has one for the
daily brief. A thread is a list of entries in the order they happened, each
with a time, a kind and a text: the item's state changes, each session's
start and end with its role and tokens, each check reported, each land
attempt, each push to origin, and each decision with its answer. nightshift
writes the entries from what it did and what the hub reported; no agent
writes to a thread directly. The entries of this package are the item's
creation, its state changes, an item made from it, a decision opened,
answered or closed, a reminder, the hub's outage and a token the hub
stopped accepting; the other packages write theirs through the same
function.

A thread is **unread** from an entry of a kind that marks it (the item
`done` or `abandoned`, a decision opened, a revert, a repair, the daily
limit, the hub's outage, a brief) until the person opens it (`GET` of the
thread through the client, or `nightshift thread show <id>`) or marks it
read. The writer of an entry says whether it marks the thread.

A **decision** has an id, a kind, the item and thread, a question in one
sentence, its evidence (links to the change, the version's diff, the
sessions' streams and the texts named for its kind), its options with one
marked as recommended and one line on what each does, what it blocks, and,
once answered, the option, the comment and the time. The kinds and their
options are the ones the source PRD names: `plan`, `session_failed`,
`session_limit`, `review_disagreement`, `approval`, `blocked`, `conflict`,
`main_red`, `origin`, `policy` and `rule`. Each is declared, in a registry
this package keeps, by the package that owns it: its name, its options with
the line on what each does and whether it requires a comment. Another
package can add an option to a declared kind, and registers for a kind and
an option the handler that carries out an answer.

A decision waits until it is answered; it never takes an option by
itself. One that has been open for 24 hours puts one reminder entry in its
thread, marking it unread, and again every 24 hours.

A decision can be closed without an answer by the package that opened it,
with a reason (`superseded by a note`, `item abandoned`); a closed decision
is no longer in `Action` and cannot be answered.

**Telling the person.** When `[notify].command` is configured, nightshift
runs that command, as the daemon's user, each time a decision opens, a
reminder is due, a workspace turns `red` or `held`, the hub has been
unreachable for 15 minutes, or a brief is written. The command gets one
JSON document on its standard input: `event` (`decision_opened`,
`decision_reminder`, `workspace_red`, `workspace_held`, `hub_unreachable`
or `brief`), `time`, the `workspace` and `item` where there are any, the
thread's URL path, and for a decision its `id`, `kind`, `question` and
option keys:

```json
{"event": "decision_opened", "time": "2026-11-28T10:00:00Z",
 "workspace": "demo", "item": 12, "thread": "/api/v1/threads/31",
 "decision": {"id": 5, "kind": "plan",
              "question": "Approve the plan for item 12?",
              "options": ["approve", "revise", "abandon"]}}
```

A command that exits other than 0, or runs longer than 30 seconds and is
ended, is noted in the daemon's log with its exit code and the first line
of its error output; it is not run again for that event, and nothing waits
for it. Without the key, nothing is run.

**The hub's outage.** After 15 minutes without the hub, one entry goes to
`Unread` saying so (`hub unreachable since <time>`), in the thread of every
registered workspace, and the notification `hub_unreachable` is sent once;
it is not repeated until the hub has been reachable in between. A token
that the hub stops accepting while the daemon runs is named in one `Unread`
entry (`worker token: 401 unauthenticated`), written the same way and not
repeated until the hub accepted the token in between.

The API serves `GET /api/v1/threads` (with `view=all|unread|action`, a
workspace filter, newest activity first, 50 per page), `GET
/api/v1/threads/:id`, `POST /api/v1/threads/:id/read`, `GET
/api/v1/decisions` (open ones unless `state=all`), `GET
/api/v1/decisions/:id` and `POST /api/v1/decisions/:id/answer` with
`{"option", "comment"?, "record"?}`. An answer naming an option the
decision does not have is `400` with `error_type: unknown_option`; a
missing required comment is `400` with `error_type: comment_required`; a
second answer to an answered decision is `409` with `error_type:
already_answered` and the first answer in the body. An answer takes effect
within 5 seconds: the item's next step is started or queued, and the thread
shows it.

A list answers `{"items": […], "next_cursor": <string or null>}` and is
read on with `cursor`. A thread in a list:

```json
{"id": 31, "path": "/api/v1/threads/31", "kind": "item",
 "workspace": "demo", "item": 12, "title": "Greeting drops the name",
 "unread": true, "last_activity": "2026-11-28T10:05:00Z",
 "open_decisions": [{"id": 5, "kind": "plan", "question": "…"}]}
```

`kind` is `item`, `workspace` or `brief`. `GET /api/v1/threads/:id` adds
`entries`, each `{"seq", "time", "kind", "text", "unread"}`, and
`decisions`, every decision of the thread. A decision:

```json
{"id": 5, "kind": "plan", "state": "open", "thread": "/api/v1/threads/31",
 "workspace": "demo", "item": 12,
 "question": "Approve the plan for item 12?",
 "evidence": [{"label": "draft change", "url": "https://…"},
              {"label": "reviewer's verdict", "text": "agree"}],
 "options": [{"key": "approve", "does": "activates the package",
              "comment_required": false, "recommended": true}],
 "blocks": "item 12", "opened_at": "2026-11-28T10:00:00Z",
 "answer": null, "closed_reason": null}
```

`state` is `open`, `answered` or `closed`; an answer is `{"option",
"comment", "record", "time"}`.

The client mirrors these: `nightshift inbox [--unread|--action]` (the
default shows `Action` first, then `Unread`), `nightshift thread show <id>`,
`nightshift decision show <id>`, `nightshift decide <id> --option <key>
[--comment "<text>"] [--record]`, and `nightshift watch`, which prints one
line per event from the stream until interrupted. Every command prints text
for a terminal and, with `--json`, the API's JSON. Exit codes: 0 on
success, 1 when the daemon refuses or cannot be reached (`cannot reach
nightshiftd at <address>`), 2 for a usage error.

**It survives a restart.** After `nightshiftd` stops, by signal or by
crash, no item, thread, entry or decision is lost, an answer whose effect
was not yet carried out is carried out at the next start, and a reminder
that fell due meanwhile is written once.

### End-to-end flows

**A text becomes an item, and a decision about it is answered.** The person
runs `nightshift item new demo --text "Add a greeting endpoint"` and reads
the item's number and its thread's path. A later package opens a decision
for the item; the configured command receives `decision_opened`; the
decision is in `nightshift inbox --action`. The person runs `nightshift
decide <id> --option approve`. Within 5 seconds the handler registered for
that option has run, and `nightshift thread show <id>` shows the item's
creation, the decision, its answer and what the handler did.

**An issue is imported, abandoned, and its workspace removed.** The person
runs `nightshift item import demo --issue 7`. `nightshiftd` reads the issue
and its comments from the hub; the issue carries `bug`, so the item is a
`defect`. A second import of the issue is refused, naming the item.
`nightshift workspace remove demo` is refused, naming the item. The person
runs `nightshift item abandon <N>`; the thread is in `Unread`. `nightshift
workspace remove demo` now succeeds, and the item and its thread are gone.

**The hub is away for 15 minutes.** The hub stops answering. After 15
minutes the thread of workspace `demo` is in `Unread` with `hub unreachable
since <time>` and the configured command receives `hub_unreachable`.
Another 15 minutes add nothing. The hub answers again, then goes away
again; 15 minutes later the entry and the notification come a second time.

## Design Decisions

From the source PRD, by its numbers:

- **3. The plan's approval is the one go-ahead; an imported spec package
  needs none.** The owner decided it. Here: an imported package becomes an
  item in `new`, and the transition `new` to `ready` exists for it.
- **21. An unanswered decision waits; API and command-line client in
  version 1; one daemon and one person.** Kept from PRD 01, where the owner
  decided it.
- **30. Open items a session reports become unplanned `defect` items.**
  Kept from PRD 01. Here: the function that creates an item from another.
- **31. 24-hour reminders** are a starting value.
- **35. The parts of work too large for one package become new, unplanned
  items.** The owner decided it. Here: the same function.
- **37, 42. Telling the person is one command, not a list of
  integrations.** The owner decided it on the comparison of decision 37;
  the form is chosen there.

Chosen here:

1. **This package is the third of ten.** The owner agreed to the split on
   2026-11-28; this package holds *Work items and intake* and *The inbox*.
2. **Three Go packages: `internal/inbox` (threads, entries, decisions,
   reminders), `internal/item` (work items, which imports `inbox`) and
   `internal/notify`.** Why: an item needs its thread, a thread needs no
   item, and the notification command is called by packages 07, 08 and 10
   as well.
3. **`waiting_on` is read from the inbox, not stored: the item's oldest
   open decision.** Why: one fact kept in two places goes out of step, and
   an item with two open decisions still names one.
4. **The transitions are the table in *Work items and intake*, by state
   alone and not by kind; a move to the current state is refused.** Why: it
   is every move the source PRD describes (`verifying` to
   `awaiting_approval` is the revert, `landing` to `implementing` a
   conflict, a rejection or a note), and a later package that needs another
   has found a gap in the PRD, not in the table.
5. **Item routes: `POST /api/v1/items`, `POST /api/v1/items/import`, `GET
   /api/v1/items`, `GET /api/v1/items/{number}`, `POST
   /api/v1/items/{number}/abandon`.** Why: the source PRD names the
   commands and no routes for them.
6. **A text outside 1 to 20,000 characters is refused by the client with
   exit code 2 and by the API with `400 invalid_request`; characters are
   Unicode code points.** Why: the API has other clients later.
7. **The title of a text item is the first line of the text, cut to 80
   characters.** Why: the source PRD gives an item a title and the command
   no way to state one.
8. **Labels are compared ignoring case.** Why: forges treat `Bug` and `bug`
   as one label.
9. **An issue is refused when its comments cannot be read
   (`comments_error`), and when its number is a pull request's (`is_pr`).**
   Why: the source PRD has the item carry "the issue and its comments", and
   a pull request is not an issue.
10. **At most 1,000 comments of an issue are read; an issue with more is
    imported with the first 1,000 and `source.comments_truncated: true`.**
    Why: the loop over the comment pages needs a bound.
11. **A refused import: `404 not_found` for a package number that names no
    package, `409 conflict` for the other refusals of a package and for
    something already imported, `502 hub_error` carrying the hub's
    `error_type` and message for a hub error, `503 hub_unavailable` while
    the hub is unreachable or the worker token is not accepted.** Why: the
    source PRD gives the exit code 1 and no status.
12. **"Already imported" counts every item of the workspace, an abandoned
    one included.** Why: the source PRD makes no exception.
13. **A spec package is looked up in `.specs/` of the default branch's
    tip: the workspace object's `head_sha`, brought into a clone of its own
    (`<clones_dir>/<slug>.import`) with `gitclone.Sync`.** Why: `.specs/`
    is the spec root's default and the source PRD names no other; the
    clone package 02 keeps per workspace is the health command's.
14. **`--no-plan-approval` with `--spec`, `--issue` together with `--spec`
    or neither, and `--kind spec` are usage errors.** Why: the source PRD
    gives the option only to the two commands whose items are planned.
15. **A hook that fails does not stop an abandon: the failure is written
    to the thread and the item is `abandoned` all the same.** Why: the
    person asked to stop; the hook's owner tries its own hub call again.
16. **Abandoning an item closes its open decisions with the reason `item
    abandoned`; abandoning an abandoned item is refused.** Why: a decision
    about an ended item must not stay in `Action`.
17. **A decision has the states `open`, `answered` and `closed`; an answer
    to a closed one is `409 conflict`.** Why: the source PRD closes a `plan`
    decision as `superseded by a note`.
18. **A kind declares its options; the recommended one is named when a
    decision is opened; a decision keeps the options it was opened with.**
    Why: which option to recommend depends on the case, and a decision must
    read the same after a restart or a later registration.
19. **An answer is stored first and carried out by the option's handler
    afterwards: at once, at the next start when the daemon stopped in
    between, and again every 30 seconds while the handler returns an error,
    the first error written to the thread. A handler may be called twice
    for one answer.** Why: the answer must not be lost, and the usual
    error, the hub being away, ends by itself.
20. **The writer of an entry says whether it marks the thread unread;
    entry kinds are free names.** Why: the kinds that mark belong to seven
    packages.
21. **`GET /api/v1/threads/:id` marks the thread read and answers `unread`
    as it was before.** Why: the source PRD counts a `GET` through the
    client as opening it, and the client still shows what was new.
22. **A reminder is due 24 hours after the later of the opening and the
    last reminder; a daemon that was stopped for several days writes one.**
    Why: three reminders at once say nothing more than one.
23. **The entries for the hub's outage and for a token go to the thread of
    every registered workspace; the notification `hub_unreachable` is sent
    once, without a workspace or a thread; with no workspace nothing is
    written.** Why: the source PRD puts the hub's outage in the workspace's
    thread, and the installation's thread is for the brief.
24. **The notification command is run with `sh -c` by `internal/notify`
    itself, not through `internal/command`.** Why: package 04 makes that
    package run as the session user, and this command runs as the daemon's.
25. **A decision in the notification's JSON is the member `decision`
    (`id`, `kind`, `question`, `options`).** Why: the source PRD lists the
    members and not their nesting.
26. **Notification commands still running at shutdown are ended.** Why:
    nothing waits for them.
27. **Removing a workspace removes its items with its threads, entries
    and decisions; item numbers are not given again.** Why: an item has one
    thread, and the removal is refused unless every item is finished.
28. **`nightshift watch` reconnects with its last `seq` when the stream
    ends, and exits 1 when it cannot connect or the cursor expired.** Why:
    the daemon disconnects a client that fell behind (package 01).
29. **An item carries `markers`, names that functions other packages
    register report for it (`paused`, `taken over`).** Why: `item list` and
    `item show` print them, and they are packages 04's and 09's.
30. **Event types: `item.created`, `item.updated`, `thread.updated`,
    `thread.removed`, `decision.opened`, `decision.answered`,
    `decision.closed`.**

## Dependencies

- **`docs/prd/02-drive-work-items-through-the-hub.md`**: the source PRD,
  of which this package specifies *Work items and intake* and *The inbox*,
  with the parts of *The daemon and its API*, *Credentials* and
  *Workspaces* named above.
- **`01_daemon_api_and_credentials`** (status `draft`): the API server's
  `Handle`, the client's `Register` and its client function, the store's
  migrations and test template, the event log's `Append`, the hub client
  with `Available` and the monitor's `Since()` and credential errors, the
  shutdown steps, the configuration struct, `internal/hubtest` and the
  end-to-end helper.
- **`02_workspaces_and_policy`** (status `draft`): `workspace.Service`
  with `Get` and `List`, `AddRemoveGuard` and `OnRemoved` of `workspace
  remove` (`DELETE /api/v1/workspaces/{slug}`), `Decorate` for members of
  the workspace object, `AddWorkspaceListColumn` of `internal/cli`, the hub
  client's `Workspace`, `gitclone.Sync`, and the fake hub's workspaces
  with a repository.
- **Parley** (`agent-fox-dev/parley`), the hub: its package
  `41_forge_reads`, with every task `done` on 2026-11-27.
- **`afspec`** (`github.com/agent-fox-dev/spec/afspec`), to load and
  validate a package and to tell whether a task of it is open.

## Verified External API

- **Parley's REST API**, read in `docs/api.md` of `agent-fox-dev/parley`
  at commit `7eabfb9`: `GET /api/v1/workspaces/{slug}/issues/{number}` and
  `GET /api/v1/workspaces/{slug}/issues/{number}/comments` as *Background*
  gives them; scope `forge:read`, satisfied by `forge:write`; lists as
  `{"items", "next_cursor"}` with `cursor`; the forge errors
  `forge_unknown` (`422`), `forge_not_found` (`404`),
  `forge_unauthenticated`, `forge_forbidden`, `forge_unreachable`,
  `forge_error` (`502`), `forge_rejected` (`422`), `forge_rate_limited`
  (`429`), `forge_timeout` (`504`).
- **`github.com/agent-fox-dev/spec/afspec`**, read in
  `docs/generated/afspec.txt` of `agent-fox-dev/spec` at commit `b456986`:
  `func DiscoverSpecs(root string) ([]SpecMeta, error)` (`SpecMeta` has
  `SpecID`, `SpecName`, `Status`, `Dir`), `func LoadSpec(dir string)
  (*Spec, error)` (`Spec` has `SpecID`, `SpecName`, `Title`, `Status`),
  `func ValidateInRoot(specsDir string, spec *Spec) (ValidationResult,
  error)` (`ValidationResult` has `Valid`, `Errors []ValidationEntry`; an
  entry has `Check` and `Message`), `func (s *Spec) OpenTasks()
  []PlannedTask` (the tasks pending or in progress, the integration task
  included), `func (s *Spec) IsFinished() bool`.
- **Parley's workspace object**, read in the same document: `GET
  /api/v1/workspaces/{slug}` answers it with `head_sha`, the hub's head of
  the default branch.
- **Package 02**, read in `.specs/02_workspaces_and_policy/` as a draft:
  `Service.AddRemoveGuard(func(ctx, slug) error)`,
  `Service.OnRemoved(func(ctx, slug))`, `Service.Decorate(func(slug,
  object))`, `Service.Get`, `Service.List`, `workspace.ErrNotFound`,
  `AddWorkspaceListColumn(header, func(object) string)`,
  `gitclone.Sync(ctx, Options{URL, Token, Dir, Commit, Runner})`.

## Glossary

- **work item**: one piece of work nightshift drives to a published
  change: a spec package, a defect or a change request.
- **intake**: creating a work item, from text, from an issue or from a
  spec package.
- **thread**: the record of one work item (or of a workspace, or of the
  briefs) as the inbox shows it, written by nightshift from what happened.
- **entry**: one line of a thread, with a time, a kind and a text.
- **decision**: a question to the person with fixed options, kept in the
  inbox until answered or closed.
- **decision kind**: the name under which a package declares the options
  of its decisions and what each does.
- **handler**: the function a package registers for a kind and an option,
  which carries out an answer.
- **Unread**: the view of threads with something new since the person last
  opened them.
- **Action**: the view of threads with an open decision.
- **`waiting_on`**: the open decision an item waits for.
- **marker**: a name another package reports for an item, `paused` or
  `taken over`.
- **hub**: a Parley server (`parleyd`).
- **package, `active`, task**: as the spec format of `agent-fox-dev/spec`
  defines them.
