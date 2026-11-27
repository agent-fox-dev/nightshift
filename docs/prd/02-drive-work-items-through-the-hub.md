# Drive work items through the hub

## Intent

A project that is built by agents needs more than a loop over a list of spec
packages: defects and change requests keep arriving after the specs are
done, and a person should not have to start, watch and merge each piece of
work. nightshift is the process that owns that work. It takes every piece of
work (a spec package, a defect, a change request) from intake to a change
that is landed on a Parley hub, verified and published to origin, with
agents doing the planning, the implementation and the review. The person
steers and decides: they approve what is to be built, they mark the parts of
a repository that need them, and they answer the questions nightshift cannot
answer itself, in an inbox built for that. Everything else goes on without
them.

## Goals

- **One path for every kind of work.** A spec package, an issue imported
  from a forge and a change request typed as free text each become a work
  item, and each reaches `done` through the same states: planned, approved,
  implemented, reviewed, landed, verified, published.
- **A person approves what is built, not which files it touches.** A defect
  or a change request is implemented only after the person approved its
  plan. The approval does not limit the files of the change.
- **The hub's policy says where a person is needed.** A change that touches
  a path the workspace's landing policy marks with an approval lands only
  after the person approved it; one that touches a blocked path never
  lands. Every other change lands without the person.
- **A reviewer with a fresh context reviews.** Every drafted plan and every
  change is reviewed by an agent session that shares no context with the
  session that wrote it, before a person is asked and before anything
  lands. Which provider and model the reviewer runs on is configured: it
  may be the writer's own.
- **The person is never waited on by a process.** A question for the person
  is a durable decision in the inbox. No agent session stays alive for it,
  and work that does not depend on the answer goes on.
- **The inbox shows two things.** `Unread` holds what finished or changed
  since the person last looked; `Action` holds every decision that waits
  for them. Both are read over an HTTP API with a server-sent event stream,
  and through the `nightshift` command.
- **Independent items run at once.** Up to a configured number of agent
  sessions (3 unless configured) run in parallel, each item in a task fork
  of its own. Items never wait for each other because of the files they
  touch.
- **Spending is bounded per workspace and day.** When a workspace's sessions
  have used its daily token limit, no further session starts there until
  the next day.
- **A landing that breaks the main branch is dealt with.** After every
  landing nightshift runs the workspace's health command on the landed commit.
  A failure starts one repair; when that does not make the branch healthy,
  the landing is reverted.
- **Only verified work reaches origin.** A landed commit is pushed to origin
  after the health command passed on it, and an item is `done` only then.
- **The person can stop work without losing it.** An item or a whole
  workspace can be paused and resumed.
- **One brief a day.** Once a day `Unread` gets one item that says what
  landed, what waits for a decision and what was spent.
- **It survives a restart.** After `nightshiftd` stops, by signal or by crash,
  the next start continues every work item from the state it had reached,
  and no decision or thread is lost.

## Non-goals

- **Hosting code, landing or policy.** Forks, changes, landing, policy and
  the event log are Parley's. nightshift uses them and implements none of
  them.
- **Working without a hub.** nightshift always needs a Parley hub. A project
  without one keeps using `specrun` from `agent-fox-dev/spec`.
- **Mandates.** There is no permission per item that limits paths, budget
  or time, as `01-drive-work-items-through-the-hub-under-mandates.md`
  specified. The plan's approval, the hub's policy and the daily limit
  take its place.
- **Protection against a session that attacks its host.** A session runs on
  the daemon's machine as the daemon's user and can read what that user can
  read, nightshift's own credentials included. The checks nightshift reports
  guard against an agent's mistakes, not against an agent that sets out to
  bypass them. Isolating sessions is the later PRD that adds a runner.
- **Running sessions in hub sandboxes.** Version 1 runs every session on the
  daemon's machine. A `sandbox` runner is a later PRD.
- **Announcing work through claims.** nightshift creates no claims on the
  hub. Its open changes are visible in the hub's activity query as any
  other.
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
  steering from a run's outcome, beyond the rules a person asks to have
  written back from a decision.
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
from git that the package landed on the base branch. It is serial, it merges
into the base branch itself, and it stops when something needs a person.
The skills `af-spec` (writes a package) and `af-impl` (implements one) are
in `skills/`; the library `afspec` loads, validates and renders packages,
and a task in `tasks.json` names the files it expects to change in
`touches`.

**`agent-fox-dev/parley`** is the hub. On 2026-11-27 all 61 packages of its
spec root have every task `done`, and its routes are described in its
`docs/api.md`. What nightshift builds on, as that document and the code
state it:

- A **task fork** is a disposable workspace with its own branches; work
  reaches the parent only as a change. A fork lives 7 days unless
  configured (30 at most) and is then removed with its branches, and its
  open changes are abandoned. The versions of a change stay readable in
  the parent after that.
- A **change** records a version for every head its source branch has. A
  draft change never lands. Check results and approvals attach to one
  version.
- A **land** replays the change onto the target's head and ends `landed`,
  `conflict`, `blocked` or `failed`. A land moves only the hub's branch:
  origin gets it through a separate push of the branch, and a sync from
  origin reports a branch that moved on both sides as `diverged`.
- The **landing policy** is a list of path rules. A rule requires named
  checks, approvals, or blocks its paths; the first rule a file matches
  applies, and the hub evaluates every version of a change against it.
- An **approval** counts only from a credential that created no version of
  the change.
- A **personal access token** (PAT) carries scopes. A user's API key is
  replaced, and the old one revoked, by every sign-in of that user.
- A **session** record holds an agent run's model, archetype and token
  usage.

**`agent-fox-dev/nightshift`** holds its `README.md`, the agent
instructions and PRD 01. Every command, route and term this PRD names for
nightshift is new.

**Why PRD 01 is replaced.** PRD 01 let a change land only inside a mandate:
the paths of the plan's `touches`, a token budget and an expiry. Measured
on Parley's own history, the plan's `touches` did not predict the diff: for
the eight packages whose commits could be attributed (48, 49, 56 to 61),
every implementation changed between 2 and 16 files outside its `touches`
(errata, reference documents, wiring in neighbouring packages, other
packages' tests). Every one of them would have stopped for the person. A
predicted diff is not a usable permission. This PRD removes the mandate and
relies on what the hub already enforces by path: its landing policy.

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
The configuration holds the hub's endpoint, the two hub credentials
(*Credentials*), the roles (*Roles and sessions*) and the limits this
document names.

At start it checks, in this order, that the configuration loads, that the
hub answers, that the credentials are accepted and fit their purpose, and
that the roles are valid. A check that fails ends the process with exit
code 1 and one line on stderr naming the check and the reason
(`config: …`, `hub: connection refused`, `credentials: …`,
`roles: reviewer names no model`); nothing is started.

On its first start it writes a client token to `~/.nightshift/token`, readable
by its owner only. Every API request carries it as a bearer token; without
it, or with another, the answer is `401` with `error_type: unauthorized`.
The API lives under `/api/v1` and answers JSON; an error is
`{"error_type", "message"}` with a 4xx or 5xx status.
`GET /api/v1/status` reports the daemon's version, the hub's endpoint and
whether it is reachable, the running sessions, and each workspace's state
and tokens used today.

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

### Credentials

The configuration holds two personal access tokens of the person's Parley
account, which the person mints on the hub:

- the **worker token**, which nightshift uses for everything it does itself.
  It holds the scopes `workspaces:write`, `git:write`, `changes:write`,
  `checks:write`, `tokens:manage`, `forge:write`, `sessions:write` and
  `audit:read`, and is not bound to a workspace;
- the **approver token**, which nightshift uses only to record the person's
  verdict when they answer an `approval` decision. It holds `changes:read`
  and `changes:approve`.

The start check `credentials` fails, with these reasons, when the hub
answers either token `401` (`worker token: 401 unauthenticated`), when the
two are the same token (`worker and approver are the same token`), when the
worker token holds `changes:approve` (`worker token must not hold
changes:approve`), or when a token lacks a scope named above (`worker token
lacks checks:write`).

A token that the hub stops accepting while the daemon runs (expired or
revoked) is treated as the hub being unreachable for what needs it, and
`status` and one `Unread` entry name the token (`worker token: 401
unauthenticated`). The daemon reads the configuration's credentials again
when it receives `SIGHUP`.

Every agent session gets a token of its own, minted by nightshift, bound to
the item's task fork and holding `git:write` only (the reviewer's:
`git:read`). It expires with the session. Such a token can fetch and push
in its fork and can do nothing to a change: not open, land, abandon or edit
one.

### Workspaces

`nightshift workspace add <slug> --health "<command>"` registers a Parley
workspace the person owns. Options: `--health-timeout <duration>` (default
`30m`), `--daily-tokens <N>` (default `[limits].daily_tokens`, 50,000,000
unless configured) and `--adopt-policy`.

Registration checks these, and each failure refuses the registration with
exit code 1, the reason on stderr, and nothing stored:

- the workspace exists on the hub and the worker token's user owns it
  (`workspace not found`, `not the owner of <slug>`);
- its setting `sync_enabled` is on (`sync is disabled for <slug>`);
- a sync from origin succeeds and reports the default branch as
  `up_to_date`, `fast_forward` or `created` (`default branch is ahead of
  origin`, `default branch has diverged from origin`, or the hub's error);
- its policy requires the three checks nightshift reports, `nightshift-gate`,
  `nightshift-review` and `nightshift-release`, counted only on the version
  itself (`fresh_checks`), in `default` and in every rule that does not
  block. When it does not, the refusal lists the rules that lack them and
  names `--adopt-policy`; with that option nightshift adds the three names
  and `fresh_checks` to those rules, writes the policy, and reports the new
  revision;
- the health command exits 0 on the tip of the default branch, within the
  timeout (`health command exited <N>` or `health command timed out`, with
  the last 50 lines of its output).

A registration that succeeds prints one warning line when the policy has no
rule that requires an approval or blocks: `no path of <slug> is marked
sensitive; every change lands without you`.

**Marking paths as sensitive.** `nightshift workspace protect <slug> <glob>…`
adds one rule to the top of the workspace's policy, so that it is the first
a matching file selects. Without an option the rule requires one approval
of the owner and the three checks: a change touching the paths waits for
the person. With `--block` the rule blocks: such a change never lands.
`--name <rule>` names the rule; otherwise it is `nightshift-<n>` with the
next free number. The command prints the rule and the policy's new
revision. It exits 2 for a glob the hub's policy format refuses, and 1 with
`policy changed meanwhile; run the command again` when the policy was
replaced between nightshift's read and its write. `nightshift workspace
unprotect <slug> <rule>` removes a rule that `protect` wrote, and exits 1
with `not a rule nightshift wrote` for another.

`nightshift workspace show <slug>` prints the workspace's state, health
command, daily limit, tokens used today and the policy's rules that require
an approval or block, each with its paths. `nightshift workspace set <slug>
[--health "<command>"] [--health-timeout <duration>] [--daily-tokens <N>]`
changes those values from the next use on.

A workspace is `green`, `red` (its last health command failed; see *After
a landing*) or `held` (an `origin` or `policy` decision is open; see
*Landing* and *Publishing to origin*). In a `red` or `held` workspace
nothing lands; sessions go on.

`nightshift workspace list` shows each workspace with its state, whether it
is paused, its tokens used today against its limit, and its counts of open
work items. `nightshift workspace remove <slug>` is refused with exit code
1 while the workspace has a work item that is neither `done` nor
`abandoned`, naming them; otherwise the workspace and its finished threads
are removed from nightshift, and nothing on the hub is changed.

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
| `awaiting_approval` | A `plan` decision in `Action` asks the person to approve the plan |
| `ready` | Approved, or an imported package; waits for a free session, the daily limit or a dependency |
| `implementing` | An implementer session works in the item's fork |
| `reviewing` | The reviewer works on the change's current version |
| `landing` | The gate and the review passed; the land is requested or waits on a person or on the workspace |
| `verifying` | Landed; the health command runs |
| `publishing` | Healthy; the push to origin is requested or waits on a person |
| `done` | Landed, the main branch healthy, and the commit on origin |
| `abandoned` | Ended without landing, by the person |

An item that waits for a decision keeps its state and names the decision in
`waiting_on`. `nightshift item list [<slug>] [--state <s>]` lists items;
`nightshift item show <N>` prints one with what it waits for.
`nightshift item abandon <N>` ends the item's sessions, abandons its change
and sets `abandoned`; it is refused with exit code 1 for an item that is
`verifying`, `publishing` or `done`.

### Planning

An item of kind `spec` is planned already and chosen by the person who
imported it: it goes from `new` straight to `ready`.

For a `defect` or a `change_request`, nightshift creates a task fork of the
workspace and starts a planner session in it. nightshift gives the planner
the number of the package to write: one above the highest package number on
the default branch and above every number it gave to an item of that
workspace that is neither `done` nor `abandoned`. The planner writes one
spec package on a branch of the fork, as `af-spec` writes one: for a
`change_request`, a package of the profile the work needs; for a `defect`,
a `lite` package with one requirement whose check is a test that fails on
the default branch for the reason the defect describes. The session's
result is accepted when the package has the number given, validates through
`afspec` with no finding of severity error and, for a `defect`, the test
was run and failed. A result that is not accepted counts as a failed
session (see *Roles and sessions*).

When the planner's branch is on the fork, nightshift opens the item's change
on the hub as a draft, with the item's title and the package's intent.

The reviewer then reviews the package against the item's source text. Its
verdict is `agree` or `object`, with findings. On `object` the planner gets
the findings and revises; after 3 rounds without `agree`, planning ends
with the objections attached.

Planning ends in a decision of kind `plan` in `Action`. It shows the
package's rendered text, a link to the draft change, the files its tasks
expect to touch, each policy rule those files would select that requires an
approval or blocks (`likely to need your approval under rule <name>`,
`blocked by rule <name>`), the reviewer's last verdict and, when it
objected to the end, each open finding with the planner's reply. Its
options are `approve`, `revise` (with a comment, required; planning runs
once more with it) and `abandon`. On `approve` the item is `ready`.

nightshift keeps the text of every package a planner wrote in its own store
until the item is `done` or `abandoned`.

### Roles and sessions

The configuration names three roles. `planner` and `implementer` each name
a model run by the Claude Code command (`claude -p`); `reviewer` names a
provider and a model run through `agentkit-go`. The provider and the model
may be the same as the planner's or the implementer's, or others. A role
that names no model, or a reviewer that names no provider or one
`agentkit-go` does not know, is invalid: the daemon does not start (see
*The daemon and its API*; `roles: reviewer names no model`, `roles: unknown
provider "<name>"`).

**The reviewer's context is fresh.** Every review is a session of its own.
It starts with nothing of the planner's or the implementer's session: not
its conversation, not its reasoning, not its working directory. It is given
only what *Planning* and *Review of a change* name as its input, and reads
the repository through a clone of its own. A later review of the same item
starts fresh too, and is given the earlier findings with the writer's
replies as input.

Every agent session is registered on the hub as a session on the item's
workspace with its role as `archetype`, its model, and the item's number in
`metadata`; the item's pushes, its change and its lands name it. nightshift
reports each session's token usage to the hub when the session ends, and
marks it completed or failed.

A session is a process on the daemon's machine, run as the daemon's user in
a clone of the item's fork. The clone is kept between the sessions of one
item and removed when the item is `done` or `abandoned`.

A session is ended and counts as failed:

- when it writes nothing to its output for `[sessions].stall` (30 minutes
  unless configured), with the reason `stalled`;
- when its input and output tokens, cache reads excluded, reach
  `[sessions].max_tokens` (10,000,000 unless configured), with the reason
  `token limit`.

A session that ends without the result its step requires, or with an
error, counts as failed with that error's first line. After a failed
session the step is started once more. After the second failure in a row
for the same step, the item waits on a decision of kind `session_failed`,
showing both reasons and the path of each session's stream; its options are
`retry` and `abandon`.

The full output stream of every session is kept on the daemon's machine for
30 days and can be read with `nightshift session log <session-id>`.

### Scheduling and the daily limit

At most `[sessions].max_parallel` sessions (3 unless configured, 1 to 16)
run at once, over all workspaces. When more items could start than
sessions are free, they start in this order: repairs first, then items
already `implementing` or `reviewing`, then `ready` items by the time they
became `ready`, oldest first.

A `ready` item does not start while a package it depends on (the
`dependencies` of its `tasks.json`) is not finished on the default branch.
Items do not wait for each other because of the files they touch. When the
hub reports that an item's change overlaps another open change, nightshift
notes it once in the item's thread with the other change's number.

**The daily limit.** For each workspace nightshift counts the input and
output tokens, cache reads excluded, of every session that ran for it since
midnight in the daemon's time zone. When the count reaches the workspace's
daily limit, no further session starts for that workspace until the next
midnight or until the person raises the limit with `nightshift workspace
set <slug> --daily-tokens <N>`; sessions that are running finish, and
gating, landing, health commands and publishing go on. The workspace's
thread gets one `Unread` entry, `daily limit reached: <count> of <limit>
tokens`, once per day. Repair sessions (*After a landing*) start even when
the limit is reached, and are counted.

### Pausing

`nightshift item pause <N>` ends the item's running session, recorded as
interrupted, and holds the item: no session starts for it and it is not
landed or published. Its state is kept and `item show` and `item list` mark
it `paused`. `nightshift item resume <N>` continues it with the step it was
at. `nightshift workspace pause <slug>` and `nightshift workspace resume
<slug>` do the same for every item of the workspace, repairs included, and
for items created while it is paused.

A decision of a paused item can still be answered; the answer takes effect
at the resume. Pausing an item that is `done` or `abandoned`, or one
already paused, exits 1 with the reason; so does resuming one that is not
paused.

### Implementation

For a `ready` item nightshift creates the task fork and the draft change,
unless planning created them, opens the change (it is a draft no longer),
and starts an implementer session that implements the item's package with
`af-impl`. The session commits on the change's branch and pushes it to the
fork; it merges nothing. Every push records a version of the change.

When the session ends, nightshift reads from the hub and the fork, not from
the session's account: the change has a version newer than at the session's
start, and the package on that version has no task `pending` or
`in_progress`. When either does not hold, the session counts as failed with
the reason (`no new version`, `tasks still open`).

nightshift then runs the package's gate on the version (`speca`'s gate, on a
clone of the fork at that version) and reports it to the hub as the check
`nightshift-gate`, `passed` or `failed`, with the verdict's summary. On
`failed` the implementer is started again with the verdict; this counts
toward the two attempts of *Roles and sessions*.

After every new version nightshift reads the hub's evaluation of it:

- when a requirement of it blocks, the item waits at once on a decision of
  kind `blocked`, naming the rule and the files; its only options are
  `revise` (with a comment, required; the implementer is started with it)
  and `abandon`;
- the files of the version that the package's tasks did not name in
  `touches` are listed in one thread entry, `outside the plan: <files>`
  (the first 50). Nothing is held back for them.

Open items the session reports (what `af-impl` calls `unmet`) each become a
new work item of kind `defect` in state `new`, linked to the item they came
from, and are listed in the thread. Unlike an item a person created, which
is planned at once, such an item stays `new`, with no session, until the
person runs `nightshift item plan <N>`.

**A fork that expired.** nightshift extends the lifetime of an item's fork
while the item does not wait on a decision. While it does, the fork may
expire on the hub. When the person then answers, nightshift creates a new
fork and a new change: for an item `awaiting_approval`, from the package
text it kept; for any other, from the last version the hub recorded of the
earlier change. The thread names both changes, and the gate and the review
run again on the new one.

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
the item is `landing`. nightshift lands it when all of these hold; until
then the item waits and `item show` names the first that does not:

- neither the item nor its workspace is paused;
- the workspace is `green`;
- the workspace's policy still requires the three checks as registration
  demands. When it does not, the workspace is `held` and a decision of
  kind `policy` goes to `Action`, listing the rules that lack them; its
  options are `adopt` (nightshift adds them, as `--adopt-policy` does) and
  `recheck` (the person changed the policy; nightshift reads it again);
- a sync of the workspace from origin leaves the default branch
  `up_to_date`, `ahead` or `fast_forward` (see *Publishing to origin* for
  the other results, and for what a `fast_forward` starts).

Then nightshift reports the check `nightshift-release` as `passed` on the
version and asks the hub to land exactly that version. Whenever one of the
conditions stops holding for an item whose `nightshift-release` is `passed`
and which has not landed, nightshift reports it `failed`, the summary naming
the condition (`paused`, `workspace red`, `workspace held`).

What the hub answers to the land decides:

- **landed**: the item is `verifying`.
- **pending on an approval** (a policy rule requires a person): a decision
  of kind `approval` goes to `Action`, naming the rule and the files it
  matched; its options are `approve` (nightshift records the approval on the
  hub with the approver token and asks to land again), `reject` (the
  rejection is recorded and the implementer is started with the comment,
  required) and `abandon`.
- **blocked** by a rule that blocks: the `blocked` decision of
  *Implementation*.
- **conflict**: the implementer is started with the hub's conflict report
  and resolves it in the fork; the new version is gated and reviewed
  again. After 2 conflicts in a row on one item, it waits on a decision of
  kind `conflict`, with the report; its options are `retry` and `abandon`.
- **the version is no longer the current one**: the new version is gated
  and reviewed, as any version.
- **failed** for another reason: the land is asked again after 1 minute,
  at most 5 times, then the item waits on a decision of kind
  `session_failed` carrying the hub's reason.

nightshift never lands with the hub's override of a policy requirement.

### After a landing

When a change landed, nightshift runs the workspace's health command on a
clone of the workspace at the landed commit. Exit code 0 within the timeout
is healthy: the item is `publishing` (see *Publishing to origin*).

Any other exit code, or the timeout, makes the workspace `red`. While a
workspace is `red`, nightshift lands nothing else in it and pushes nothing
to origin: other items go on up to `landing` and wait there. Then:

1. **Fix forward.** nightshift starts one repair: an implementer session in a
   new fork, with the health command's last 200 lines of output, the
   landed change and the item's package. Its change is gated, reviewed and
   landed as any other, ahead of everything else and although the
   workspace is `red`. When it landed, the health command runs again;
   healthy makes the workspace `green` and both changes go on to
   `publishing`.
2. **Revert.** When the repair's session failed twice, its review ended in
   disagreement, or the health command still fails after it landed,
   nightshift asks the hub to revert the repair's change, when it landed,
   and then the item's change. On each reverting change it reports its
   three checks as `passed`, with the summary `revert after failed health
   command on <commit>`, and lands it. When the hub answers that a
   reverting change needs an approval, an `approval` decision goes to
   `Action`, listed before every other decision. After the reverts the
   health command runs again. Healthy makes the workspace `green`; the
   item returns to `awaiting_approval` with a `plan` decision that carries
   the failure's output.
3. **Still unhealthy.** When the health command fails after the revert,
   or a revert cannot be built or landed, the workspace stays `red` and a
   decision of kind `main_red` goes to `Action` with the output and the
   commits tried; its only option is `resume`, which the person chooses once
   the branch is repaired by hand and which runs the health command once
   more, staying `red` on a failure.

Each of the three steps writes an `Unread` entry to the item's thread.

### Publishing to origin

When the health command passed on a landed commit, nightshift asks the hub
to push the workspace's default branch to origin. What the hub answers
decides:

- **pushed**, or origin already has the commit: the item is `done`, the
  fork is left to expire, and the thread gets an `Unread` entry with the
  change, the commit and the tokens spent.
- **origin has commits the hub lacks**: nightshift syncs, with the results
  below, and pushes again.
- **origin refused the push**, or the workspace's origin credential is
  missing or not accepted: the workspace is `held` and a decision of kind
  `origin` goes to `Action` with origin's reason; its only option is
  `retry`.
- **origin could not be reached**, or the push timed out: it is tried again
  after 1 minute, at most 5 times, then handled as a refused push.

**A sync from origin**, which nightshift runs before every landing and after
a push that origin's branch was ahead of, has one of these results for the
default branch:

- `up_to_date` or `ahead`: nothing more happens.
- `fast_forward`: origin brought commits. nightshift runs the health command
  on the new tip before it lands anything. A failure makes the workspace
  `red` with no item to blame: a `main_red` decision goes to `Action` in
  the workspace's thread.
- `diverged`: the workspace is `held` and an `origin` decision goes to
  `Action`, naming both heads; its only option is `retry`, which the person
  chooses once the two branches are reconciled by hand.
- the sync fails: it is tried again after 1 minute, at most 5 times, then
  handled as a refused push.

An `origin` decision answered with `retry` makes nightshift sync and push
again; the workspace is `green` when both succeed, and the decision opens
again with the new reason when they do not.

### The inbox

Every work item has one thread; a workspace has one more for what concerns
no single item (the workspace turning `red` or `held` with no item to
blame, the daily limit, the hub's outage); the installation has one for the
daily brief. A thread is a list of entries in the order they happened, each
with a time, a kind and a text: the item's state changes, each session's
start and end with its role and tokens, each check reported, each land
attempt, each push to origin, and each decision with its answer. nightshift
writes the entries from what it did and what the hub reported; no agent
writes to a thread directly.

A thread is **unread** from an entry of a kind that marks it (the item
`done` or `abandoned`, a decision opened, a revert, a repair, the daily
limit, the hub's outage, a brief) until the person opens it (`GET` of the
thread through the client, or `nightshift thread show <id>`) or marks it
read.

A **decision** has an id, a kind, the item and thread, a question in one
sentence, its evidence (links to the change, the version's diff, the
sessions' streams and the texts named above for its kind), its options
with one marked as recommended and one line on what each does, what it
blocks, and, once answered, the option, the comment and the time. The
kinds and their options are the ones this document names: `plan`,
`session_failed`, `review_disagreement`, `approval`, `blocked`, `conflict`,
`main_red`, `origin`, `policy` and `rule`.

A decision waits until it is answered; it never takes an option by
itself. One that has been open for 24 hours puts one reminder entry in its
thread, marking it unread, and again every 24 hours.

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

The client mirrors these: `nightshift inbox [--unread|--action]` (the default
shows `Action` first, then `Unread`), `nightshift thread show <id>`, `nightshift
decision show <id>`, `nightshift decide <id> --option <key> [--comment
"<text>"] [--record]`, and `nightshift watch`, which prints one line per
event from the stream until interrupted. Every command prints text for a
terminal and, with `--json`, the API's JSON. Exit codes: 0 on success, 1
when the daemon refuses or cannot be reached (`cannot reach nightshiftd at
<address>`), 2 for a usage error.

### Decisions written back as rules

When a decision is answered with `--record`, and only then, nightshift
starts a planner session in a new fork of the workspace that writes the
answer down as a rule: the question, the answer and the person's comment,
as a new ADR where the workspace has `docs/adr/`, else as a line appended
to `.specs/steering.md`, else as a new file under `docs/decisions/`. The
session changes no other file; a change that does is discarded and counts
as a failed session.

The change reaches the person as a decision of kind `rule` showing the
text; its options are `approve` (nightshift reports its three checks as
passed with the summary `rule approved by the owner` and lands it),
`revise` (with a comment) and `discard`. A rule change runs the health
command after landing and is published as any other. Planner and reviewer
sessions started after it landed find the rule in the repository.

### The daily brief

Once a day, at `[brief].at` (`08:00` in the daemon's time zone unless
configured), nightshift adds one entry to the brief thread and marks it
unread. It covers the time since the previous brief and lists, per
workspace: the items that reached `done`, with their changes and the files
each changed outside its plan; the items reverted or abandoned; the
decisions open, oldest first, with their age; the items waiting and what
for; and the tokens spent, per role and in total, against the daily limit.
A day with no item changed and no decision open gets no brief.
`nightshift brief` prints the same for the time since the last brief without
storing it.

### Writing back to the forge

For an item imported from an issue, and for no other, nightshift writes to
that issue through the hub's forge routes: one comment when the plan is
approved, naming the change; one when the item is `done`, naming the commit
on origin, after which it closes the issue; one when the item is reverted
or abandoned, saying which. It writes no labels. A forge call that fails is
tried again every 10 minutes for 24 hours, then noted once in the item's
thread; it never holds the item back.

### End-to-end flows

**A change request, from text to origin.** The person runs
`nightshift item new demo --text "Add a greeting endpoint"`. `nightshiftd`
stores the item, creates a task fork on the hub and starts the planner; the
planner pushes a package; nightshift opens the draft change; the reviewer
agrees; a `plan` decision appears in `nightshift inbox --action`. The person
runs `nightshift decide <id> --option approve`. nightshift opens the change
and starts the implementer; its push records a version; nightshift gates it
and reports `nightshift-gate`; the reviewer agrees and `nightshift-review` is
reported; the sync finds origin unchanged; `nightshift-release` is reported
and the hub lands the change; the health command exits 0; the hub pushes
the branch to origin; the item is `done` and its thread is in `Unread`.

**A change that touches a sensitive path.** The person ran `nightshift
workspace protect demo "deploy/**"`. An implementer's version touches
`deploy/config.yaml` and `api/greet.go`. The gate and the review pass; the
hub answers the land as pending on an approval under the rule; an
`approval` decision lists `deploy/config.yaml`. The person answers
`approve`; nightshift records the approval with the approver token, the hub
lands the change, and the item goes on to `verifying`. Had the rule been
written with `--block`, the item would have stopped at the version's push
with a `blocked` decision, before the gate ran.

**A landing that breaks the main branch.** Item 12 lands; the health
command exits 1. The workspace is `red`, nothing is pushed to origin, and
item 13, ready to land, waits. The repair lands and the health command
fails again. nightshift reverts both changes, the health command exits 0,
the workspace is `green`, item 13 lands and is published, and item 12 is
back in `Action` with a `plan` decision carrying the failure's output.
Origin never held item 12's commit.

**Origin moved.** Someone merges a pull request on the forge. Before item
14's landing nightshift syncs; the default branch is `fast_forward`; the
health command passes on the new tip; item 14 lands on top of it and is
published.

**The daily limit.** Workspace `demo` has used 50,000,000 tokens by 15:00.
Its two running sessions finish; its three `ready` items stay `ready`; an
item whose review had passed still lands and is published. The workspace's
thread says `daily limit reached`. After midnight the three items start.

**A restart in the middle.** `nightshiftd` is killed while two implementers
run. At the next start both items get a new session for the same step;
the open decisions are still in `Action` with their evidence; a client
that reconnects to the event stream with its last `seq` receives what it
missed.

## Design Decisions

1. **There is no mandate.** The owner decided it, on the finding that a
   plan's `touches` does not predict the diff (see *Background*): path
   limits per item would stop most items for the person, and widening them
   only hides that.
2. **Sensitive paths are the hub's policy rules, and everything else lands
   without the person.** The owner decided it. The policy already judges
   every change by its files, so nightshift keeps no path list of its own.
3. **The plan's approval is the one go-ahead.** The owner decided it. An
   imported spec package needs none: importing it is the choice.
4. **Files outside the plan are noted, not held.** The owner decided it:
   they appear in the thread and the brief.
5. **The spend limit is one daily token limit per workspace; at the limit,
   running sessions finish and none starts.** The owner decided it. There
   is no limit per item.
6. **Items and workspaces can be paused.** The owner decided it.
7. **nightshift offers `workspace protect`.** The owner decided it.
8. **Two personal access tokens, a worker and an approver, instead of the
   owner's API key.** The owner decided it. A sign-in elsewhere revokes an
   API key, and the hub counts an approval only from a credential that
   created no version of the change, which the worker does when it reverts.
9. **Sessions run on the daemon's machine without isolation, and the PRD
   says what that does not protect against.** The owner decided it.
10. **The `sandbox` runner is a later PRD.** The owner decided it: the hub's
    sandbox runtime client cannot yet start a main process or serve its
    logs.
11. **A landed commit is pushed to origin after the health command passed,
    and `done` means it is on origin.** The owner decided it.
12. **nightshift syncs from origin before each landing; only a divergence or
    a refused push reaches the person.** The owner decided it.
13. **On a revert nightshift reports its own checks as passed and asks the
    person only where policy requires an approval.** The owner decided it.
14. **The plan is a draft change from the planner's first push.** The owner
    decided it: the person reads the plan where the code will follow, and
    the hub never lands a draft.
15. **A fork may expire while its item waits on the person; nightshift
    restores the work when they answer.** The owner decided it.
16. **nightshift gives each planner its package number.** The owner decided
    it: two planners at once would otherwise take the same one.
17. **A rule is written back only when the person asks with `--record`.**
    The owner decided it.
18. **Items never wait for each other on files.** The owner decided it: the
    hub lands optimistically, and a conflict is resolved by the
    implementer.
19. **This PRD is a new document that supersedes PRD 01.** The owner
    decided it.
20. **The reviewer works in a fresh context, and may run on any provider
    and model, the writer's included.** The owner decided it, replacing PRD
    01's rule that the reviewer's provider must differ: what a review needs
    is a reader that does not share the writer's context; another provider
    is one way to set that up, not a requirement.
21. **The reviewer reviews the plan and every change; the implementer runs
    on `claude -p` with `af-impl`, the reviewer on `agentkit-go`.** Kept
    from PRD 01, where the owner decided it, as are: one daemon, one person and several repositories, an
    unanswered decision waits, fix forward once and then revert, one health
    command per workspace, API and command-line client in version 1, and
    forge write-back only for imported issues.
22. **A third check, `nightshift-release`, reported at the moment of the
    land.** Chosen here: it is what makes a pause, a `red` or a `held`
    workspace hold on the hub too, for a land asked by other means than
    nightshift.
23. **The policy must count the checks on the version itself
    (`fresh_checks`).** Chosen here: the hub otherwise carries a passed
    check over to a rebased version, which nightshift has not gated.
24. **A session's token holds `git:write` only, and nightshift opens the
    change.** Chosen here: a session then cannot land, abandon or retarget
    its own change, and the approver token never shares a credential with
    a version.
25. **A session is ended at 10,000,000 tokens.** Chosen here: with running
    sessions allowed to finish at the daily limit, one session needs a
    bound of its own.
26. **Repairs start although the daily limit is reached.** Chosen here: a
    `red` main branch should not wait for midnight.
27. **A blocked path stops the item at the push; an approval is asked after
    the gate and the review.** Chosen here: nothing can lift a block, so
    further spending is waste, while a person asked to approve should see
    reviewed work.
28. **A diverged origin has the one option `retry`.** Chosen here:
    resetting the hub's branch to origin would discard landed work, which
    a person should do by hand, knowing what is lost.
29. **Every work item is carried by a spec package, a `lite` one for a
    defect.** Kept from PRD 01: one shape gives every item a gate and a
    text the person can approve, and the plan lands with the code in one
    change.
30. **Open items a session reports become unplanned `defect` items.** Kept
    from PRD 01.
31. **The numbers** (3 parallel sessions, 3 review rounds, 2 attempts per
    step, a 30-minute stall, 10,000,000 tokens per session, 50,000,000 per
    workspace and day, 24-hour reminders) are chosen here as starting
    values; each that the text calls configured can be changed without a
    new release.
32. **Size.** This PRD covers 18 areas and is expected to be split into
    several packages: the daemon, its API and the credentials first; then
    workspaces with the policy commands; work items and the inbox;
    planning; sessions, scheduling, the daily limit and pausing;
    implementation and review; landing; the handling after a landing and
    publishing; the rules, the brief and the forge write-back.

## Dependencies

- **`docs/prd/01-drive-work-items-through-the-hub-under-mandates.md`**:
  superseded by this PRD. No package was generated from it.
- **Parley** (`agent-fox-dev/parley`), the hub. nightshift relies on these
  packages of its spec root, each with every task `done` on 2026-11-27:
  `06_api_keys_and_access_tokens`, `08_workspace_bound_tokens`,
  `12_workspace_sync_from_origin`, `13_workspace_push_to_origin`,
  `15_task_fork_creation`, `16_task_fork_lifetime`, `17_git_smart_http`,
  `19_git_push_to_change`, `22_change_opening_and_versions`,
  `23_change_lifecycle_and_mergeability`, `25_optimistic_landing`,
  `26_conflict_reports_and_resolution`, `27_landing_policy`,
  `28_approvals_and_checks`, `29_auto_land_revert_and_path_limits`,
  `30_awareness` (the overlaps of a change), `41_forge_reads`,
  `42_forge_writes` and `49_agent_sessions_and_usage`.
- **`afspec`** (`github.com/agent-fox-dev/spec/afspec`), to load, validate
  and render packages and to tell whether one is finished.
- **`af-spec` and `af-impl`** (`agent-fox-dev/spec`, `skills/`). `af-spec`
  chooses its package's number itself; nightshift needs it to take a given
  number. `af-impl` ends by merging into the base branch and pushing it,
  and opens its open items as forge issues; nightshift needs a mode of it
  that ends once the branch is pushed, merges nothing, and returns its
  open items in its result. Both are new and not yet specified: a PRD for
  `agent-fox-dev/spec`.
- **`speca`** (`agent-fox-dev/spec`, `cmd/speca`), for the gate on a
  version.
- **`specrun`** (`agent-fox-dev/spec`, `cmd/specrun`): superseded by
  nightshift for a project on a hub. Nothing in it changes.
- **Claude Code** (`claude`), installed on the daemon's machine.

## Verified External API

- **Parley's REST API**, read in `docs/api.md` of `agent-fox-dev/parley` at
  commit `7eabfb9`, and for the two rules marked so in the code:
  - `POST /api/v1/workspaces/{slug}/forks` with `name`, `ttl`,
    `auto_change`, `session_id` and `token` (`permissions`, `expires`);
    `PATCH /api/v1/workspaces/{slug}` with `ttl` extends a fork;
    `POST /api/v1/workspaces/{slug}/tokens` mints a token bound to a
    workspace.
  - `POST …/changes` with `source`, `title`, `intent`, `draft`,
    `session_id`; `PATCH …/changes/{number}` with `state`; `POST
    …/changes/{number}/abandon`; `GET …/changes/{number}` carries
    `landing` with `status` and `requirements` (`rule`, `paths`, `block`,
    `approvals`, `checks`); `GET …/changes/{number}/overlaps`. A push to a
    branch that is the source of a change records its next version; a
    version's head stays at `refs/parley/changes/<number>/<version>` in
    the target workspace.
  - `GET` and `PUT …/policy`, with `ETag` and `If-Match` (`412
    revision_mismatch`); `POST …/policy/evaluate` with `paths`. A
    requirement has `approvals`, `approvers`, `checks`, `fresh_checks` and
    `block`; the first rule whose paths match a file applies.
  - `PUT …/changes/{number}/checks/{name}` with `status`
    `pending|passed|failed`, `version`, `summary` of at most 2,000
    characters; scope `checks:write`.
  - `POST …/changes/{number}/land` with `expected_version`, answering the
    attempt `landed`, `409 land_conflict`, `409 policy_blocked` (with
    `blocked.status` `pending` or `blocked` and its `requirements`), `409
    version_mismatch` or `503 land_failed`; `POST
    …/changes/{number}/revert` (`409 revert_conflict`).
  - `POST …/changes/{number}/approvals` with `verdict` and `version`;
    scope `changes:approve`. A verdict counts only from a credential that
    created no version at or below its own (`internal/policy/counting.go`,
    `createdUpTo`); a revert records its caller as the creator
    (`internal/land/revert.go`).
  - `POST …/sync`, answering `branches` with `result` `up_to_date`,
    `fast_forward`, `ahead`, `diverged`; `POST …/branches/{name}/push`,
    answering `result` `pushed` or `up_to_date`, or `409
    non_fast_forward`, `422 origin_rejected`, `502 origin_unauthenticated`,
    `origin_forbidden`, `origin_unreachable`, `504 origin_timeout`.
  - `GET …/issues/{number}`, `GET` and `POST
    …/issues/{number}/comments`, `POST …/issues/{number}/close`.
  - `POST /api/v1/sessions` with `workspace_slug`, `archetype`, `model`,
    `metadata`; `POST /api/v1/sessions/{id}/usage`; `POST
    /api/v1/sessions/{id}/complete`.
  - The scopes named under *Credentials* are in the hub's scope list; one
    live API key per user, replaced at every sign-in.
- **`github.com/agent-fox-dev/spec/afspec`**: unverified in this revision
  (the repository was not available to read). PRD 01 read, in
  `docs/generated/afspec.txt`: `func LoadSpec(dir string) (*Spec, error)`,
  `func (s *Spec) IsFinished() bool`,
  `func ValidateInRoot(specsDir string, spec *Spec) (ValidationResult, error)`,
  `func DiscoverSpecs(root string) ([]SpecMeta, error)`; the field
  `touches` of a task is in `afspec/schemas/tasks.v3.json`, and is present
  in the `tasks.json` of Parley's packages.
- **`github.com/agent-fox-dev/agentkit-go`: unverified.** Parley's `go.mod`
  requires v0.11.0; its source was not available to read, so no signature
  is given. Assumed: it runs a session against a model of a named provider,
  Anthropic among them, and reports the session's token usage.
- **`claude -p`**: unverified in this revision. PRD 01 read, in
  `docs/specrun.md` of `agent-fox-dev/spec`: `claude -p "<prompt>"
  --output-format stream-json --verbose --permission-mode
  bypassPermissions`, whose last `result` event carries `is_error`.

## Glossary

- **work item**: one piece of work nightshift drives to a published change: a
  spec package, a defect or a change request.
- **plan**: the spec package a planner wrote for a defect or a change
  request, which the person approves before it is implemented.
- **sensitive path**: a path a rule of the workspace's landing policy marks
  with an approval or a block.
- **thread**: the record of one work item (or of a workspace, or of the
  briefs) as the inbox shows it, written by nightshift from what happened.
- **decision**: a question to the person with fixed options, kept in the
  inbox until answered.
- **Unread**: the view of threads with something new since the person last
  opened them.
- **Action**: the view of threads with an open decision.
- **role**: `planner`, `implementer` or `reviewer`; each has a model. The
  reviewer's session shares no context with the others'.
- **worker token, approver token**: the two Parley personal access tokens
  nightshift holds: one for its own actions, one for the person's
  approvals.
- **daily limit**: the tokens a workspace's sessions may use between two
  midnights before no further session starts.
- **health command**: the command a workspace configures that must exit 0
  on the main branch after a landing.
- **green, red, held**: a workspace whose last health command passed, one
  whose last health command failed, and one where an `origin` or `policy`
  decision is open.
- **origin**: the repository on a forge that a Parley workspace was cloned
  from and is pushed to.
- **rule**: a decision's answer written into the repository as an ADR, a
  steering line or a file under `docs/decisions/`. Not a policy rule, which
  is Parley's.
- **hub**: a Parley server (`parleyd`).
- **task fork, change, version, check, policy, sync**: as Parley defines
  them (`docs/api.md`).
- **package, gate, `touches`, `lite`**: as the spec format of
  `agent-fox-dev/spec` defines them.
