# Drive work items through the hub under mandates

> Superseded by [02-drive-work-items-through-the-hub.md](02-drive-work-items-through-the-hub.md),
> which removes the mandate. Kept as the record of the first design.

## Intent

A project that is built by agents needs more than a loop over a list of spec
packages: defects and change requests keep arriving after the specs are
done, and a person should not have to start, watch and merge each piece of
work. nightshift is the process that owns that work. It takes every piece of
work (a spec package, a defect, a change request) from intake to a landed
and verified change on a Parley hub, with agents doing the planning, the
implementation and the review. The person steers and decides: they say
which item may land without them, and they answer the questions nightshift
cannot answer itself, in an inbox built for that. Everything else goes on
without them.

## Goals

- **One path for every kind of work.** A spec package, an issue imported
  from a forge and a change request typed as free text each become a work
  item, and each reaches `done` through the same states: planned, mandated,
  implemented, reviewed, landed, verified.
- **Nothing lands without a mandate.** A change produced by nightshift lands
  only when a person has given a mandate to its work item, and only while
  the change stays inside the mandate's paths and budget. A change outside
  them does not land; it becomes a decision.
- **A second model reviews.** Every drafted plan and every change is
  reviewed by a model of another provider than the one that wrote it,
  before a person is asked and before anything lands.
- **The person is never waited on by a process.** A question for the person
  is a durable decision in the inbox. No agent session stays alive for it,
  and work that does not depend on the answer goes on.
- **The inbox shows two things.** `Unread` holds what finished or changed
  since the person last looked; `Action` holds every decision that waits
  for them. Both are read over an HTTP API with a server-sent event stream,
  and through the `nightshift` command.
- **Independent items run at once.** Up to a configured number of agent
  sessions (3 unless configured) run in parallel, each item in a task fork
  of its own.
- **A landing that breaks the main branch is dealt with.** After every
  landing nightshift runs the workspace's health command on the landed commit.
  A failure starts one repair; when that does not make the branch healthy,
  the landing is reverted.
- **A decision is not asked twice.** An answered decision of a kind that
  sets a precedent is written back into the repository as a rule, through a
  change the person approves.
- **One brief a day.** Once a day `Unread` gets one item that says what
  landed, what waits for a decision and what was spent.
- **It survives a restart.** After `nightshiftd` stops, by signal or by crash,
  the next start continues every work item from the state it had reached,
  and no decision, mandate or thread is lost.

## Non-goals

- **Hosting code, landing or policy.** Forks, changes, landing, policy,
  sandboxes and the event log are Parley's. nightshift uses them and
  implements none of them.
- **Working without a hub.** nightshift always needs a Parley hub. A project
  without one keeps using `specrun` from `agent-fox-dev/spec`.
- **A web or desktop client.** Version 1 has the API and the command-line
  client. Other clients are a later PRD on the same API.
- **An MCP facade for the inbox.** A later PRD.
- **Intake from forge webhooks.** A person imports an issue by naming it. A
  new issue becoming a work item by itself is a later PRD.
- **A team.** One installation has one person who decides. Routing a
  decision to one of several people is a later PRD.
- **Stacked changes.** An item that depends on another waits until that one
  has landed. Starting on top of an open change is a later PRD.
- **Several attempts at one item side by side.** One fork and one change
  per item.
- **A learning loop.** nightshift does not propose changes to skills or to the
  steering from a run's outcome, beyond the rules written back from
  decisions.
- **Health of a main branch that moved by other means.** The health command
  runs after nightshift's own landings and at registration, not after a sync
  from origin or a direct push.
- **Changes to the spec format or to `afspec`.** nightshift reads and writes
  packages through the library as it is.
- **The agents' prompts.** What the planner, the implementer and the
  reviewer are told is the text of the skills (`af-spec`, `af-impl`) and of
  nightshift's own review brief; this PRD says what each role must produce,
  not how it is worded.

## Background

Three repositories meet here.

**`agent-fox-dev/spec`** holds the spec toolchain. `specrun` (`cmd/specrun`,
`docs/specrun.md`) is today's implementation loop: it works in one checkout,
takes the next open package with `afspec.ChooseNext`, starts one headless
session (`claude -p "/af-impl <spec_dir> --unattended"`), and then proves
from git that the package landed on the base branch. It keeps a state file
outside the repository, a ledger and the sessions' streams. Open items live
as forge issues with `af:*` labels; a person settles a deviation by adding
`af:accepted` or `af:rejected`, and a reconcile step acts on it later. It is
serial, it merges into the base branch itself, and it stops when something
needs a person. The skills `af-spec` (writes a package) and `af-impl`
(implements one) are in `skills/`; the library `afspec` loads, validates and
renders packages, and a task in `tasks.json` names the files it changes in
`touches`. The module depends on `github.com/agent-fox-dev/agentkit-go`
v0.9.0, the provider-neutral driver the `spec` command runs on.

**`agent-fox-dev/parley`** specifies the hub (`docs/prd/`): task forks with
a token bound to the fork and limited to paths (§4), changes with versions
(§7), optimistic landing with conflict reports, path-based policy, checks
and one-call revert (§8), advisory claims and the activity query (§9),
forge reads and writes (§12), sandboxes (§13), and sessions with token
usage plus a durable, replayable event log (§14). On 2026-11-13 its packages
`01` to `04` and `09` are implemented (skeleton, API conventions, server
lifecycle, authentication, CLI foundation); every package nightshift relies on
is still pending.

**`agent-fox-dev/nightshift`** is new and holds a `README.md` only. Every
command, route and term this PRD names for nightshift is new.

Mapped onto the hub, most of what `specrun` builds for itself is a fact the
hub reports: "landed" is a land attempt's status, the gate is a check on a
change version, a session's cost is its usage record. What the hub does not
have, by its own scope, is the work item, the mandate and a place where a
person is asked a question. Those are nightshift's.

The inbox follows the pattern of Pizza Bot
(`github.com/agent-fox-dev/pizza-bot`): two global views over threads,
`Unread` for finished work and `Action` for durable approval requests,
served to thin clients over HTTP and server-sent events, with runs that go
on when no client is connected.

## Requirements

Constraint: Linux and macOS; Windows is best effort. Constraint: Go, built
as two programs, `nightshiftd` (the daemon) and `nightshift` (the client), which
speaks only the daemon's HTTP API. Constraint: `nightshiftd` is one process
holding the controller, its store and the inbox API.

### The daemon and its API

`nightshiftd` reads one configuration file (`~/.nightshift/config.toml` unless
`--config` names another) and listens on `127.0.0.1:7878` unless configured.
The configuration holds the hub's endpoint and an API key of the person's
Parley account, the roles (below) and the limits this document names.

At start it checks, in this order, that the configuration loads, that the
hub answers and accepts the API key, and that the roles are valid. A check
that fails ends the process with exit code 1 and one line on stderr naming
the check and the reason (`config: …`, `hub: 401 unauthorized`,
`roles: reviewer and implementer share the provider "anthropic"`); nothing
is started.

On its first start it writes a client token to `~/.nightshift/token`, readable
by its owner only. Every API request carries it as a bearer token; without
it, or with another, the answer is `401` with `error_type: unauthorized`.
The API lives under `/api/v1` and answers JSON; an error is
`{"error_type", "message"}` with a 4xx or 5xx status.
`GET /api/v1/status` reports the daemon's version, the hub's endpoint and
whether it is reachable, the running sessions and each workspace's state.

`GET /api/v1/events` is a server-sent event stream of every change to a
thread, a decision or a work item, each event with an increasing `seq`. A
client that reconnects with `Last-Event-ID` receives every event after that
`seq`; when the `seq` is older than the oldest event kept (events are kept
for 7 days), the answer is `410` with `error_type: cursor_expired`.

On `SIGTERM` or `SIGINT` the daemon stops starting sessions, ends the
running ones, records each as interrupted and exits with code 0 within 30
seconds. At the next start, a work item whose session was interrupted, or
was running when the process died, gets a new session for the same step;
the interrupted one does not count as a failed attempt.

**When the hub is unreachable**, no session is started and nothing is
landed; running sessions go on. `status` reports `hub: unreachable` with
the time of the first failure. nightshift tries again every 30 seconds and
continues by itself. After 15 minutes without the hub, one item goes to
`Unread` saying so; it is not repeated until the hub has been reachable in
between.

### Workspaces

`nightshift workspace add <slug> --health "<command>"` registers a Parley
workspace the person owns. Options: `--runner local|sandbox` (default
`local`), `--health-timeout <duration>` (default `30m`) and
`--adopt-policy`.

Registration checks four things, and each failure refuses the registration
with exit code 1, the reason on stderr, and nothing stored:

- the workspace exists on the hub and the API key has owner access to it
  (`workspace not found`, `not the owner of <slug>`);
- its policy requires the three checks nightshift reports, `nightshift-gate`,
  `nightshift-review` and `nightshift-mandate`, in `default` and in every rule
  that does not block. When it does not, the refusal lists the rules that
  lack them and names `--adopt-policy`; with that option nightshift adds the
  three names to those rules, writes the policy, and reports the new
  revision;
- with `--runner sandbox`, the hub has sandboxes enabled
  (`sandboxes are not enabled on this hub`);
- the health command exits 0 on the tip of the default branch, within the
  timeout (`health command exited <N>` or `health command timed out`, with
  the last 50 lines of its output).

`nightshift workspace list` shows each workspace with its runner, its state
(`green`, `red`) and its counts of open work items. `nightshift workspace
remove <slug>` is refused with exit code 1 while the workspace has a work
item that is neither `done` nor `abandoned`, naming them; otherwise the
workspace and its finished threads are removed from nightshift, and nothing on
the hub is changed.

### Work items and intake

A work item belongs to one workspace and has a number unique in nightshift, a
kind (`spec`, `defect`, `change_request`), a title, its source, a state and
one thread. There are three ways to create one:

- `nightshift item new <slug> --text "<text>" [--kind defect|change_request]`
  creates an item from free text (default kind `change_request`). The text
  is 1 to 20,000 characters; outside that the command exits 2.
- `nightshift item import <slug> --issue <N>` reads the issue and its comments
  through the hub's forge routes. The kind is `defect` when the issue
  carries `af:defect` or a label named `bug`, else `change_request`. An
  issue already imported is refused with exit code 1, naming the existing
  item. An issue that cannot be read is refused with the hub's error.
- `nightshift item import <slug> --spec <NN>` takes an active spec package on
  the default branch. It is refused with exit code 1 when the package does
  not load (with the validator's first finding), is not `active`, has no
  task `pending` or `in_progress`, or is already an item.

Each prints the new item's number and its thread's URL path.

A work item is in one of these states:

| State | Meaning |
|---|---|
| `new` | Created; no session has worked on it |
| `planning` | A plan is being drafted or reviewed |
| `awaiting_mandate` | A decision in `Action` asks the person for the mandate |
| `ready` | Mandated; waits for a free session, a dependency or an overlapping item |
| `implementing` | An implementer session works in the item's fork |
| `reviewing` | The reviewer works on the change's current version |
| `landing` | Every check passed; the land is requested or waits on a person's approval |
| `verifying` | Landed; the health command runs |
| `done` | Landed and the main branch healthy |
| `abandoned` | Ended without landing, by the person |

An item that waits for a decision keeps its state and names the decision in
`waiting_on`. `nightshift item list [<slug>] [--state <s>]` lists items;
`nightshift item abandon <N>` ends the item's sessions, abandons its change,
releases its claim and sets `abandoned`; it is refused with exit code 1 for
an item that is `verifying` or `done`.

### Planning

An item of kind `spec` is planned already: its package exists. It goes from
`new` straight to `awaiting_mandate`.

For a `defect` or a `change_request`, nightshift creates a task fork of the
workspace and starts a planner session in it. The planner writes one spec
package on a branch of the fork, as `af-spec` writes one: for a
`change_request`, a package of the profile the work needs; for a `defect`,
a `lite` package with one requirement whose check is a test that fails on
the default branch for the reason the defect describes. The session's
result is accepted when the package validates through `afspec` with no
finding of severity error and, for a `defect`, the test was run and failed.
A result that is not accepted counts as a failed session (see *Sessions*).

The reviewer then reviews the package against the item's source text. Its
verdict is `agree` or `object`, with findings. On `object` the planner gets
the findings and revises; after 3 rounds without `agree`, planning ends
with the objections attached.

Planning ends in a decision of kind `plan` in `Action`. It shows the
package's rendered text, the paths its tasks touch, the proposed budget,
the reviewer's last verdict and, when it objected to the end, each open
finding with the planner's reply. Its options are `approve`, `revise` (with
a comment, required; planning runs once more with it) and `abandon`.
Approving it gives the mandate.

### Mandates

A mandate belongs to one work item and holds: the paths the change may
touch (globs; for a package, the union of every task's `touches` and the
package's own directory), a budget in tokens, and an expiry. The budget is
`[mandate].default_budget_tokens` (20,000,000 unless configured) and the
expiry 7 days after the grant, unless the person's answer sets others
(`nightshift decide <id> --option approve --budget <tokens> --expires
<duration>`).

For an imported `spec` item the mandate is asked for with a decision of
kind `mandate`, showing the package's title, its tasks, the paths and the
budget; its options are `approve` and `abandon`.

A mandate ends when its item is `done` or `abandoned`, when it expires, or
when the person runs `nightshift mandate revoke <item>`. An item whose mandate
ended otherwise than by `done` has its sessions ended and returns to
`awaiting_mandate` with a new `mandate` decision that says why; its fork
and change stay.

**The budget** counts the input and output tokens, cache reads excluded, of
every session of the item since the grant. When the count reaches the
budget, no further session starts for the item, and a decision of kind
`budget` goes to `Action` with the count per session; its options are
`extend` (by the original amount, or by `--budget`) and `abandon`. A
session still running then is ended when the count reaches 120% of the
budget.

**The scope.** The implementer's fork token is limited to the mandate's
paths, so a push that touches another path is refused by the hub. When a
session reports that it needs a path outside the mandate, or a push is
refused for that reason, the item waits on a decision of kind `widen` that
lists the paths and the session's reason; its options are `widen` (the
paths join the mandate, and a new token is issued), `refuse` (the session
is started again and told the paths are closed) and `abandon`.

### Roles and sessions

The configuration names three roles. `planner` and `implementer` each name
a model run by the Claude Code command (`claude -p`); `reviewer` names a
provider and a model run through `agentkit-go`. The reviewer's provider
must differ from the provider of the implementer and of the planner; the
daemon does not start otherwise (see *The daemon and its API*).

Every agent session is registered on the hub as a session with its role as
`archetype`, its model, and the item's number in `metadata`; forks, pushes,
claims and lands name it. nightshift reports each session's token usage to the
hub when the session ends, and marks it completed or failed.

A session runs in the workspace's runner. With `local`, it is a process on
the daemon's machine in a fresh clone of the item's fork. With `sandbox`,
it is the main process of a hub sandbox created on the fork. In both, the
session holds no credential but its fork token, and the thread, the checks
and the item's states are the same; only where the process runs differs.

A session that writes nothing to its output for `[sessions].stall` (30
minutes unless configured) is ended and counts as failed, with the reason
`stalled`. A session that ends without the result its step requires, or
with an error, counts as failed with that error's first line. After a
failed session the step is started once more. After the second failure in
a row for the same step, the item waits on a decision of kind
`session_failed`, showing both reasons and the path of each session's
stream; its options are `retry` and `abandon`.

The full output stream of every session is kept on the daemon's machine for
30 days and can be read with `nightshift session log <session-id>`.

### Scheduling

At most `[sessions].max_parallel` sessions (3 unless configured, 1 to 16)
run at once, over all workspaces. When more items could start than
sessions are free, they start in this order: repairs first, then items
already `implementing` or `reviewing`, then `ready` items by the time of
their mandate, oldest first.

A `ready` item does not start while:

- a package it depends on (the `dependencies` of its `tasks.json`) is not
  finished on the default branch;
- another nightshift item of the same workspace with an earlier mandate has
  claimed a path its own mandate also matches, and has not landed or been
  abandoned;
- its workspace is `red` and it has reached `landing` (see *After a
  landing*).

`nightshift item show <N>` names what an item waits for. When an item starts,
nightshift creates a claim on the hub for the mandate's paths with the item's
title as intent, and renews it while the item is open. An overlap with a
change or claim that is not nightshift's does not hold the item back; it is
noted in the thread with the other change's number.

### Implementation

For a `ready` item nightshift creates the task fork, unless planning created
it, with a token limited to the mandate's paths, and starts an implementer
session that implements the item's package with `af-impl`. The session
commits on a branch and pushes it to the fork; it merges nothing. The
first push opens the item's change on the hub, with the item's title and
the package's intent.

When the session ends, nightshift reads from the hub and the fork, not from
the session's account: the change exists and has a version newer than at
the session's start, and the package on that version has no task `pending`
or `in_progress`. When either does not hold, the session counts as failed
with the reason (`no new version`, `tasks still open`).

nightshift then runs the package's gate on the version (`speca`'s gate, in a
runner, on a clone of the fork at that version) and reports it to the hub
as the check `nightshift-gate`, `passed` or `failed`, with the verdict's
summary. On `failed` the implementer is started again with the verdict;
this counts toward the two attempts of *Roles and sessions*.

Open items the session reports (what `af-impl` calls `unmet`) each become a
new work item of kind `defect` in state `new`, linked to the item they came
from, and are listed in the thread. Unlike an item a person created, which
is planned at once, such an item stays `new`, with no session and no
mandate, until the person runs `nightshift item plan <N>`.

### Review of a change

When `nightshift-gate` passed on a version, the item is `reviewing`. The
reviewer session reads the package, the diff of the version against its
base and the gate's verdict, with a token that can read the fork and
nothing else. Its verdict is `agree` or `object`, with findings that each
name a file and a line or a criterion.

On `agree`, nightshift reports the check `nightshift-review` as `passed` on that
version. On `object`, it reports `failed`, and the implementer is started
with the findings; its new version is gated and reviewed again. After 3
reviews of one item without `agree`, the item waits on a decision of kind
`review_disagreement`, showing each open finding with the implementer's
reply; its options are `side_with_reviewer` (the implementer gets one more
session with the findings marked as binding), `side_with_implementer`
(`nightshift-review` is reported `passed`, the summary naming the person's
decision) and `abandon`.

nightshift never records an approval on the hub for a model's verdict: a
model's review is a check, so that an approval a policy rule requires
stays a person's.

### Landing

With `nightshift-gate` and `nightshift-review` passed on the current version,
nightshift evaluates the mandate: the mandate is live, the count is below the
budget, and every file the version touches matches the mandate's paths. It
reports the result as the check `nightshift-mandate`, the summary naming the
first condition that failed. A `failed` for the paths starts a `widen`
decision; for the budget, a `budget` decision; for a mandate that ended, a
`mandate` decision.

With all three passed, the item is `landing` and nightshift asks the hub to
land the change. What the hub answers decides:

- **landed**: the item is `verifying`.
- **pending on an approval** (a policy rule requires a person): a decision
  of kind `approval` goes to `Action`, naming the rule and the files it
  matched; its options are `approve` (nightshift records the approval on the
  hub with the person's API key and asks to land again), `reject` (the
  rejection is recorded and the implementer is started with the comment,
  required) and `abandon`.
- **blocked** by a rule that blocks: the item waits on a decision of kind
  `blocked`, naming the rule and files; its only options are `revise`
  (with a comment; the implementer is started with it) and `abandon`.
- **conflict**: the implementer is started with the hub's conflict report
  and resolves it in the fork; the new version is gated and reviewed
  again. After 2 conflicts in a row on one item, it waits on a decision of
  kind `conflict`, with the report; its options are `retry` and `abandon`.
- **failed** for another reason: the land is asked again after 1 minute,
  at most 5 times, then the item waits on a decision of kind
  `session_failed` carrying the hub's reason.

nightshift never overrides a policy requirement.

### After a landing

When a change landed, nightshift runs the workspace's health command in a
runner, on a clone of the workspace at the landed commit. Exit code 0
within the timeout is healthy: the item is `done`, the fork is left to
expire, and the thread gets an `Unread` entry with the change, the commit
and the tokens spent.

Any other exit code, or the timeout, makes the workspace `red`. While a
workspace is `red`, nightshift lands nothing else in it: other items go on up
to `landing` and wait there. Then:

1. **Fix forward.** nightshift starts one repair: an implementer session in a
   new fork, with the health command's last 200 lines of output, the
   landed change and the item's package, and a budget of
   `[repair].budget_tokens` (2,000,000 unless configured) under the paths
   of the item's mandate. Its change is gated, reviewed and landed as any
   other, ahead of everything else. When it landed, the health command
   runs again; healthy makes the workspace `green` and the item `done`.
2. **Revert.** When the repair's session failed, its review ended in
   disagreement, its budget is spent, or the health command still fails
   after it landed, nightshift asks the hub to revert the item's change (and
   the repair's, when it landed), lands the reverting changes, and runs
   the health command again. Healthy makes the workspace `green`; the item
   returns to `awaiting_mandate` with a `mandate` decision that carries
   the failure's output, and its earlier mandate is ended.
3. **Still unhealthy.** When the health command fails after the revert,
   or a revert cannot be landed, the workspace stays `red` and a decision
   of kind `main_red` goes to `Action` with the output and the commits
   tried; its only option is `resume`, which the person chooses once the
   branch is repaired by hand and which runs the health command once
   more, staying `red` on a failure.

Each of the three steps writes an `Unread` entry to the item's thread.

### The inbox

Every work item has one thread; a workspace has one more for what concerns
no single item (the workspace turning `red` with no item to blame, the
hub's outage); the installation has one for the daily brief. A thread is a
list of entries in the order they happened, each with a time, a kind and a
text: the item's state changes, each session's start and end with its role
and tokens, each check reported, each land attempt, and each decision with
its answer. nightshift writes the entries from what it did and what the hub
reported; no agent writes to a thread directly.

A thread is **unread** from an entry of a kind that marks it (the item
`done` or `abandoned`, a decision opened, a revert, a repair, the hub's
outage, a brief) until the person opens it (`GET` of the thread through
the client, or `nightshift thread show <id>`) or marks it read.

A **decision** has an id, a kind, the item and thread, a question in one
sentence, its evidence (links to the change, the version's diff, the
sessions' streams and the texts named above for its kind), its options
with one marked as recommended and one line on what each does, what it
blocks, and, once answered, the option, the comment and the time. The
kinds and their options are the ones this document names: `plan`,
`mandate`, `budget`, `widen`, `session_failed`, `review_disagreement`,
`approval`, `blocked`, `conflict`, `main_red` and `rule`.

A decision waits until it is answered; it never takes an option by
itself. One that has been open for 24 hours puts one reminder entry in its
thread, marking it unread, and again every 24 hours.

The API serves `GET /api/v1/threads` (with `view=all|unread|action`, a
workspace filter, newest activity first, 50 per page), `GET
/api/v1/threads/:id`, `POST /api/v1/threads/:id/read`, `GET
/api/v1/decisions` (open ones unless `state=all`), `GET
/api/v1/decisions/:id` and `POST /api/v1/decisions/:id/answer` with
`{"option", "comment"?, …}`. An answer naming an option the decision does
not have is `400` with `error_type: unknown_option`; a missing required
comment is `400` with `error_type: comment_required`; a second answer to
an answered decision is `409` with `error_type: already_answered` and the
first answer in the body. An answer takes effect within 5 seconds: the
item's next step is started or queued, and the thread shows it.

The client mirrors these: `nightshift inbox [--unread|--action]` (the default
shows `Action` first, then `Unread`), `nightshift thread show <id>`, `nightshift
decision show <id>`, `nightshift decide <id> --option <key> [--comment
"<text>"]`, and `nightshift watch`, which prints one line per event from the
stream until interrupted. Every command prints text for a terminal and,
with `--json`, the API's JSON. Exit codes: 0 on success, 1 when the daemon
refuses or cannot be reached (`cannot reach nightshiftd at <address>`), 2 for
a usage error.

### Decisions written back as rules

When a decision of kind `review_disagreement` or `widen` is answered with
an option other than `abandon`, or any decision is answered with
`--record`, nightshift starts a planner session in a new fork of the
workspace that writes the answer down as a rule: the question, the answer
and the person's comment, as a new ADR where the workspace has `docs/adr/`,
else as a line appended to `.specs/steering.md`, else as a new file under
`docs/decisions/`. The session changes no other file; a change that does
is discarded and counts as a failed session.

The change is not mandated. It reaches the person as a decision of kind
`rule` showing the text; its options are `approve` (nightshift reports its
three checks as passed with the summary `rule approved by the owner` and
lands it), `revise` (with a comment) and `discard`. A rule change runs the
health command after landing as any other. Planner and reviewer sessions
started after it landed find the rule in the repository.

### The daily brief

Once a day, at `[brief].at` (`08:00` in the daemon's time zone unless
configured), nightshift adds one entry to the brief thread and marks it
unread. It covers the time since the previous brief and lists, per
workspace: the items that reached `done`, with their changes; the items
reverted or abandoned; the decisions open, oldest first, with their age;
the items waiting and what for; and the tokens spent, per role and in
total. A day with no item changed and no decision open gets no brief.
`nightshift brief` prints the same for the time since the last brief without
storing it.

### Writing back to the forge

For an item imported from an issue, and for no other, nightshift writes to
that issue through the hub's forge routes: one comment when the mandate is
given, naming the change; one when the item is `done`, naming the landed
commit, after which it closes the issue; one when the item is reverted or
abandoned, saying which. It writes no labels. A forge call that fails is
tried again every 10 minutes for 24 hours, then noted once in the item's
thread; it never holds the item back.

### End-to-end flows

**A change request, from text to verified landing.** The person runs
`nightshift item new demo --text "Add a greeting endpoint"`. `nightshiftd`
stores the item, creates a task fork on the hub and starts the planner in
the runner; the planner pushes a package; the reviewer agrees; a `plan`
decision appears in `nightshift inbox --action`. The person runs `nightshift
decide <id> --option approve`. nightshift issues the path-limited token,
creates the claim and starts the implementer; its push opens a change;
nightshift gates it and reports `nightshift-gate`; the reviewer agrees and
`nightshift-review` is reported; `nightshift-mandate` passes; the hub lands the
change; the health command exits 0; the item is `done` and its thread is
in `Unread`.

**A change that leaves its mandate.** The implementer's push touches
`deploy/config.yaml`, outside the paths. The hub refuses the push; the
item waits on a `widen` decision listing the file. The person answers
`refuse`; the implementer is started again with the path closed, pushes a
version inside the paths, and the item goes on to review. A planner
session writes the answer as an ADR, which reaches the person as a `rule`
decision.

**A landing that breaks the main branch.** Item 12 lands; the health
command exits 1. The workspace is `red` and item 13, ready to land, waits.
The repair lands and the health command fails again. nightshift reverts both
changes, the health command exits 0, the workspace is `green`, item 13
lands, and item 12 is back in `Action` with a `mandate` decision carrying
the failure's output.

**A restart in the middle.** `nightshiftd` is killed while two implementers
run. At the next start both items get a new session for the same step;
the open decisions are still in `Action` with their evidence; a client
that reconnects to the event stream with its last `seq` receives what it
missed.

## Design Decisions

1. **A dedicated repository and product.** The owner decided it: the
   controller and inbox are neither part of Parley, which hosts code and
   knows no work items, nor of the spec toolchain, which is short-lived
   commands.
2. **nightshift's store is the source of truth for work items.** The owner
   decided it. Issues and packages are sources it imports from.
3. **A person pins each item.** The owner decided it: there is no standing
   rule that mandates a class of items.
4. **The planner drafts, the person approves, and the approval is the
   mandate.** The owner decided it.
5. **The reviewer is a model of another provider, and reviews the plan and
   every change.** The owner decided it.
6. **The implementer runs on `claude -p` with `af-impl`; the reviewer on
   `agentkit-go`.** The owner decided it.
7. **One runner contract with two runners, `local` first.** The owner
   decided it; `local` needs only the hub's forks and changes.
8. **Parallel with a cap and a budget per mandate.** The owner decided it.
9. **An unanswered decision waits.** The owner decided it; no option is
   ever taken by default.
10. **Fix forward once, then revert.** The owner decided it.
11. **The health check is one command per workspace.** The owner decided
    it.
12. **API and command-line client in version 1.** The owner decided it.
13. **One person, several repositories.** The owner decided it.
14. **One daemon.** The owner decided it.
15. **Parley is always required, and nightshift replaces `specrun`.** The
    owner decided it; `specrun` stays for projects without a hub until
    they move.
16. **Only items imported from an issue are written back to the forge.**
    The owner decided it.
17. **Rules from decisions and the daily brief are in; webhook intake and
    the learning loop are out.** The owner decided it.
18. **Every work item is carried by a spec package, a `lite` one for a
    defect.** Chosen here: one shape gives every item a gate, paths from
    `touches` and a text the person can approve, and the plan lands with
    the code in one change.
19. **A model's review is a check, never an approval.** Chosen here: the
    hub counts an approval by credential, and nightshift holds the owner's
    API key, so an approval recorded for a model would satisfy a rule that
    means a person.
20. **The mandate is enforced on the hub twice:** by the fork token's
    paths at push, and by the check `nightshift-mandate` at landing. Chosen
    here: the first stops the work early, the second holds when a token
    was widened or the mandate ended meanwhile.
21. **The budget is counted in tokens, cache reads excluded.** Chosen
    here: the hub's usage records hold tokens, not money, and prices differ
    per model.
22. **Registration refuses a workspace whose policy does not require
    nightshift's checks, and changes the policy only with `--adopt-policy`.**
    Chosen here: a mandate that policy does not enforce can be bypassed by
    any land call, and a tool should not rewrite a policy unasked.
23. **While a workspace is `red`, work goes on and only landing waits.**
    Chosen here: it keeps the repair's base stable without idling the
    sessions.
24. **After a revert the mandate is spent.** Chosen here: the person
    decides again, with the failure in front of them.
25. **Open items a session reports become unplanned `defect` items.**
    Chosen here: they are tracked without spending anything until the
    person asks for a plan.
26. **The numbers** (3 parallel sessions, 3 review rounds, 2 attempts per
    step, a 30-minute stall, a 20,000,000-token budget, a 7-day mandate, a
    2,000,000-token repair, 24-hour reminders) are chosen here as starting
    values; each that the text calls configured can be changed without a
    new release.
27. **Size.** This PRD covers 14 areas and is expected to be split into
    several packages, the daemon and its API first, then workspaces, work
    items and the inbox, then planning and mandates, then implementation,
    review and landing, then the handling after a landing, the rules and
    the brief.

## Dependencies

- **Parley** (`agent-fox-dev/parley`, `docs/prd/`), the hub. nightshift needs
  these packages of its spec root, each `active` with every task `pending`
  on 2026-11-13, so nightshift cannot run end to end before them:
  `15_task_fork_creation`, `16_task_fork_lifetime`, `17_git_smart_http`,
  `19_git_push_to_change`, `22_change_opening_and_versions`,
  `23_change_lifecycle_and_mergeability`, `25_optimistic_landing`,
  `26_conflict_reports_and_resolution`, `27_landing_policy`,
  `28_approvals_and_checks`, `29_auto_land_revert_and_path_limits`,
  `30_awareness`, `41_forge_reads`, `42_forge_writes`,
  `49_agent_sessions_and_usage`, `52_durable_event_log_and_stream`; and for
  the `sandbox` runner `44_sandbox_runtime_and_profiles` to
  `47_sandbox_lifecycle_and_checkpoints`. Its packages `01` to `04` and
  `09` have every task `done`.
- **`afspec`** (`github.com/agent-fox-dev/spec/afspec`), to load, validate
  and render packages and to tell whether one is finished.
- **`af-spec` and `af-impl`** (`agent-fox-dev/spec`, `skills/`). `af-impl`
  today ends by merging into the base branch and pushing it, and opens its
  open items as forge issues. nightshift needs a mode of it, new and not yet
  specified, that ends once the branch is pushed, merges nothing, and
  returns its open items in its result. That is a PRD for
  `agent-fox-dev/spec`.
- **`speca`** (`agent-fox-dev/spec`, `cmd/speca`), for the gate on a
  version.
- **`specrun`** (`agent-fox-dev/spec`, `cmd/specrun`, its packages
  `20_specrun_driver`, `23_specrun_reconcile`, `24_specrun_repair`):
  superseded by nightshift for a project on a hub. Nothing in it changes.
- **Claude Code** (`claude`), installed where the `local` runner runs and
  in the sandbox profile the `sandbox` runner uses.

## Verified External API

- **Parley's REST API: unverified.** The routes nightshift calls are specified
  in `agent-fox-dev/parley`, `docs/prd/appendix_a_routes.md`, and none is
  implemented yet. Assumed from the PRD: `POST /api/v1/workspaces/:slug/forks`
  with `token.permissions` and `token.paths` (§4); `GET` and `PUT
  …/policy` (§8.3.4); `PUT …/changes/:number/checks/:name` with `status`
  `pending|passed|failed`, `summary` up to 2,000 characters (§8.3.6);
  `POST …/changes/:number/land`, `POST …/changes/:number/revert`, `POST
  …/changes/:number/approvals`, `GET …/changes/:number/conflict` (§8.5);
  `POST …/claims` with 1 to 50 path globs and an expiry of at most 24
  hours (§9.3); `GET` and `POST …/issues/:number/comments`, `POST
  …/issues/:number/close` (§12); `POST …/sandboxes` and its exec and logs
  routes (§13); `POST /api/v1/sessions`, `POST /sessions/:id/usage`, `POST
  /sessions/:id/complete` (§14.3.2), and `GET /api/v1/events` (§14).
- **`github.com/agent-fox-dev/spec/afspec`**, read in
  `docs/generated/afspec.txt` of `agent-fox-dev/spec`:
  `func LoadSpec(dir string) (*Spec, error)`,
  `func (s *Spec) IsFinished() bool`,
  `func ValidateInRoot(specsDir string, spec *Spec) (ValidationResult, error)`,
  `func DiscoverSpecs(root string) ([]SpecMeta, error)`. The field
  `touches` of a task is in `afspec/schemas/tasks.v3.json`.
- **`github.com/agent-fox-dev/agentkit-go` v0.9.0: unverified.** It is in
  `go.mod` of `agent-fox-dev/spec`; its source was not available to read,
  so no signature is given. Assumed: it runs a session against a model of
  a provider other than Anthropic and reports the session's token usage.
- **`claude -p`**: the invocation `specrun` uses, read in
  `docs/specrun.md` of `agent-fox-dev/spec`: `claude -p "<prompt>"
  --output-format stream-json --verbose --permission-mode
  bypassPermissions`, whose last `result` event carries `is_error`.

## Glossary

- **work item**: one piece of work nightshift drives to a landed change: a
  spec package, a defect or a change request.
- **mandate**: a person's permission for one work item to land without
  them, limited to paths, a token budget and a time.
- **thread**: the record of one work item (or of a workspace, or of the
  briefs) as the inbox shows it, written by nightshift from what happened.
- **decision**: a question to the person with fixed options, kept in the
  inbox until answered.
- **Unread**: the view of threads with something new since the person last
  opened them.
- **Action**: the view of threads with an open decision.
- **role**: `planner`, `implementer` or `reviewer`; each has a model, and
  the reviewer's is of another provider.
- **runner**: where a session's process runs: `local`, on the daemon's
  machine, or `sandbox`, in a hub sandbox.
- **health command**: the command a workspace configures that must exit 0
  on the main branch after a landing.
- **red, green**: a workspace whose last health command failed, or passed.
- **rule**: a decision's answer written into the repository as an ADR, a
  steering line or a file under `docs/decisions/`.
- **hub**: a Parley server (`parleyd`).
- **task fork, change, version, check, claim, policy**: as Parley's PRD
  defines them (§4, §7, §8, §9).
- **package, gate, `touches`, `lite`**: as the spec format of
  `agent-fox-dev/spec` defines them.
