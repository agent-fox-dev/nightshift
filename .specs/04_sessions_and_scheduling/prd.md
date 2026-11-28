---
spec_id: "04"
spec_name: "sessions_and_scheduling"
title: "Run agent sessions under limits and a schedule"
status: "active"
created_at: "2026-11-28T10:02:03Z"
intent_hash: "53244a341a963a0c31f5ce28fec7676ffa50e40df1d18676ed6e2279acd3d610"
contract_hash:
  requirements.json: "a508c43cca63e4451fad5d6d0c204678bcbb9b7985b3c027a69b133a7e421fb5"
  tasks.json: "1f5a5cb5b09587d2dca95eacef95b9d5a0c7f7dabc6ebddf235e4b29b8b6b357"
schema_version: 3
source: "docs/prd/02-drive-work-items-through-the-hub.md"
---

# Run agent sessions under limits and a schedule

## Intent

Every piece of work nightshift drives is done by agent sessions, and a
person who is not watching has to be able to trust what a session can
reach, what it may spend and when it runs. nightshift runs each session of
a planner, an implementer or a reviewer as a bounded process of an
operating-system user of its own, with a hub token that can only reach the
item's fork, records it on the hub and keeps its output; it starts
sessions in a fixed order under a limit of parallel sessions, a limit per
item and a daily token limit per workspace, asks the person when a step
keeps failing, and holds, resumes and survives a restart without losing an
item's place.

## Goals

- **A session cannot read nightshift's credentials or the person's
  files.** Sessions, and the gate and health commands that run the code
  they wrote, run as the operating-system user `[sessions].user` names; a
  daemon that cannot guarantee that does not start.
- **A reviewer with a fresh context.** Every review is a session of its
  own, on the configured provider and model, with a clone of its own and
  nothing of the writer's session.
- **Every session is on the hub's record.** It is registered with its
  role, model and item, its token usage is reported, and it ends
  `completed` or `failed`.
- **A session's token reaches the item's fork and nothing else**, and ends
  with the session.
- **A session is bounded.** One that writes nothing for 30 minutes, or
  uses 10,000,000 tokens, is ended.
- **A step that keeps failing reaches the person.** A failed session is
  tried once more; after the second failure in a row a `session_failed`
  decision asks.
- **One item cannot run without end.** After 12 implementer and reviewer
  sessions the item stops and a `session_limit` decision asks.
- **Independent items run at once, in a fixed order.** Up to 3 sessions
  run in parallel: repairs first, then items already `implementing` or
  `reviewing`, then `ready` items, oldest first.
- **Spending is bounded per workspace and day.** At the daily token limit
  no further session starts in the workspace until the next midnight.
- **The person can stop work without losing it.** An item or a whole
  workspace can be paused and resumed.
- **It survives a stop and a restart.** A session ended by a shutdown or
  lost in a crash is started again for the same step, and does not count
  as a failure.

## Non-goals

This package is the fourth of ten written from
`docs/prd/02-drive-work-items-through-the-hub.md`. What belongs to the
others:

- **`01_daemon_api_and_credentials`**: the configuration loader, the start
  checks `config`, `hub` and `credentials`, the store, the hub client and
  its monitor, the API server, the client's command table, the event log
  and the shutdown sequence. This package adds keys, checks, routes,
  commands, a status source and a shutdown step to them.
- **`02_workspaces_and_policy`**: workspaces, their state and their stored
  daily limit (`--daily-tokens`), `internal/command` and
  `internal/gitclone`. This package enforces the limit, sets the default
  from `[limits].daily_tokens`, supplies the count of tokens used today
  and the user commands run as.
- **`03_work_items_and_inbox`**: work items, their states and transitions,
  `item abandon`, threads, entries, the decision machinery and its
  routes, `nightshift decide`, reminders and the notification command.
  This package declares two decision kinds in its registry and registers
  an abandon hook and the `paused` marker.
- **`05_planning`**: what the planner is told (the `af-spec` arguments, the
  package number), which planner result is accepted, plan review rounds,
  the review brief for a plan, the item's task fork and change. It runs
  its sessions through this package.
- **`06_implementation_and_review`**: what the implementer is told
  (`af-impl`, rework), accepting a session from the hub's state, the gate,
  the review brief for a change and what a finding must name, the
  `blocked` and `review_disagreement` decisions, **the dependency start
  guard** (a `ready` item waits for the packages it depends on) and **the
  overlap note**; extending a fork's lifetime.
- **`07_landing`**: landing, `nightshift-release` and its failure when an
  item is paused, the `session_failed` decision it opens after five failed
  lands.
- **`08_health_and_publishing`**: the health command after a landing and
  repairs; this package only gives a repair its place in the start order
  and its exemption from the daily limit.
- **`09_notes_and_take_over`**: notes, taking over and the option
  `take_over`, which that package adds to `session_failed` and
  `session_limit`; this package declares both kinds without it.
- **`10_rules_brief_and_forge`**: rule sessions, the brief and the outcome
  numbers.

Also out, for every package of that PRD: isolation beyond a separate user
(no container, no limit on network, processor, memory or disk), running
sessions in hub sandboxes, and the agents' prompts.

That an answer to a decision of a paused item takes effect at the resume
is specified here for the two kinds this package declares; for any other
kind it is the declaring package's, with the functions this package
offers (`Paused`, `OnResume`).

## Background

Packages 01 to 03 leave a daemon with a store, an API, a hub client,
registered workspaces, work items in states and an inbox with decisions.
Nothing runs an agent yet. Every command, route, configuration key and Go
package this document names for sessions and scheduling is new:
`internal/session` and `internal/sched`.

What this package uses of Parley, as its `docs/api.md` states at commit
`7eabfb9`:

- A **session** is created with `POST /api/v1/sessions` (`workspace_slug`,
  an `id` the caller may choose, `archetype`, `model`, `metadata`), takes
  usage increments with `POST /api/v1/sessions/{id}/usage` (`model`,
  `input_tokens`, `output_tokens`, `cache_read_tokens`) while it is
  `active`, and ends with `POST /api/v1/sessions/{id}/complete` (`status`
  `completed`, `failed` or `timeout`, `error_message`,
  `cache_creation_input_tokens`). A create with an `id` the caller already
  used answers `200` with the stored session, so it is safe to retry.
- A push names its session with the push option `parley.session=<id>`; the
  hub accepts a session of the workspace for work on a task fork of it.
- `POST /api/v1/workspaces/{slug}/tokens` mints a personal access token
  bound to a workspace or fork, with `permissions` and `expires` in days
  (`0`, `30`, `60` or `90`). A calling token can grant only scopes it
  holds **by exact name**: one that holds `git:write` cannot mint one with
  `git:read`. `DELETE /api/v1/user/tokens/{token_id}` revokes a token.
- Git authenticates with HTTP Basic, the token as the password; `git:read`
  fetches and `git:write` fetches and pushes.

`claude -p` is started as `specrun` of `agent-fox-dev/spec` starts it
(`docs/specrun.md` at commit `e9081fa`): `--output-format stream-json
--verbose --permission-mode bypassPermissions --disallowedTools
ScheduleWakeup,CronCreate`, with `CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS`
set to the stall time; the last event of type `result` carries `is_error`.
`agentkit-go` v0.11.0 runs the reviewer: an `Agent` built from a `Config`
with a provider (`anthropic`, `openrouter` or `ollama`) and a model, whose
`Stream` yields the run's events and whose usage is a `core.Usage`.

## Requirements

Constraint: Linux and macOS; Windows is best effort. Constraint: `sudo` is
installed where a session user is configured, and the daemon's user may
run commands as the session user without a password (`sudo -n`).

### Configuration

This package adds these keys to the configuration file:

| Key | Meaning | Default |
|---|---|---|
| `[roles.planner].model` | the model the planner's `claude -p` runs | none, required |
| `[roles.implementer].model` | the model the implementer's `claude -p` runs | none, required |
| `[roles.reviewer].provider` | the `agentkit-go` provider of the reviewer | none, required |
| `[roles.reviewer].model` | the reviewer's model: a model id or a tier name | none, required |
| `[sessions].user` | the operating-system user sessions run as | none |
| `[sessions].unisolated` | `true`: run sessions as the daemon's user | `false` |
| `[sessions].dir` | where the items' clones and session files are kept | `nightshift` in the session user's home directory; `~/.nightshift/work` when unisolated |
| `[sessions].stall` | how long a session may write nothing | `30m` |
| `[sessions].max_tokens` | the tokens one session may use | `10000000` |
| `[sessions].max_parallel` | the sessions that run at once, 1 to 16 | `3` |
| `[items].max_sessions` | the implementer and reviewer sessions of one item between two answers of the person, 2 to 100 | `12` |
| `[limits].daily_tokens` | the daily token limit a workspace gets at registration | `50000000` |

A `max_parallel` outside 1 to 16, a `max_sessions` outside 2 to 100, or a
`stall`, `max_tokens` or `daily_tokens` that is not greater than zero fails
the start check `config` (`config: sessions.max_parallel must be between 1
and 16`, `config: sessions.stall must be greater than 0`).

### Roles and sessions

The configuration names three roles. `planner` and `implementer` each name
a model run by the Claude Code command (`claude -p`); `reviewer` names a
provider and a model run through `agentkit-go`. The provider and the model
may be the same as the planner's or the implementer's, or others. A role
that names no model, or a reviewer that names no provider or one
`agentkit-go` does not know, is invalid: the daemon does not start. The
start check `roles` runs after `credentials` and fails with `roles:
<role> names no model` (the first of planner, implementer, reviewer),
`roles: reviewer names no provider` or `roles: unknown provider "<name>"`.

**The reviewer's context is fresh.** Every review is a session of its own.
It starts with nothing of the planner's or the implementer's session: not
its conversation, not its reasoning, not its working directory. It is given
only what the package that starts it names as its input, and reads the
repository through a clone of its own. A later review of the same item
starts fresh too.

Every agent session is registered on the hub as a session on the item's
workspace with its role as `archetype`, its model, and the item's number in
`metadata`; the item's pushes name it. nightshift reports each session's
token usage to the hub when the session ends, and marks it completed or
failed.

A session is a process on the daemon's machine, in a clone of the item's
fork. The clone is kept between the sessions of one item and removed when
the item is `done` or `abandoned`.

Every agent session gets a token of its own, minted by nightshift, bound to
the item's task fork and holding `git:write` only (the reviewer's:
`git:read`). It expires with the session. Such a token can fetch and push
in its fork and can do nothing to a change: not open, land, abandon or edit
one.

**The session user.** Sessions run as the operating-system user
`[sessions].user` names, not as the daemon's user. So do the gate and the
health command, which run code a session wrote. The clones belong to that
user. The start check `sessions` runs after `roles` and fails, with these
reasons, when the key is missing (`sessions: no user configured; set
[sessions].user or [sessions].unisolated`), when the user does not exist
or is the daemon's own (`sessions: user "<name>" does not exist`,
`sessions: user "<name>" is the daemon's user`), when the daemon cannot
start a process as that user (`sessions: cannot run as "<name>":
<error>`), when that user can read the configuration file, the client
token or the daemon's store (`sessions: user "<name>" can read <path>`),
or when `claude` does not run as that user (`sessions: claude is not
available to "<name>": <error>`).

With `[sessions].unisolated = true` and no user, sessions run as the
daemon's user. The daemon then prints `warning: sessions run as the
daemon's user and can read its credentials` at start, and `status` reports
`sessions: unisolated`.

**What a session is given, and what it must leave.** A package that needs
a session gives the role, the item, the step the session is for, the fork,
the branch to work on (a planner or an implementer) or the commit to read
(a reviewer), a prompt, input files, and optionally a function that accepts
or refuses the result.

- A **planner or implementer** session is `claude -p "<prompt>" --model
  <the role's model>` with the flags above, run in the item's clone. In
  the prompt, `{result}` stands for the path of the session's result file
  and `{input:<name>}` for the path of an input file. The session must
  leave a result file that is one JSON document (the convention of PRD 09
  of `agent-fox-dev/spec`).
- A **reviewer** session is an `agentkit-go` agent on the role's provider
  and model with the file navigation tools over its own clone and no shell
  tool, given the prompt as its only message. Its last message must hold
  its verdict as one JSON object: `{"verdict": "agree" | "object",
  "findings": [{"text", "file"?, "line"?, "criterion"?}]}`, with at least
  one finding for `object`.

A session is ended and counts as failed:

- when it writes nothing to its output for `[sessions].stall` (30 minutes
  unless configured), with the reason `stalled`;
- when its input and output tokens, cache reads excluded, reach
  `[sessions].max_tokens` (10,000,000 unless configured), with the reason
  `token limit`.

A session that ends without the result its step requires, or with an
error, counts as failed with that error's first line (`no result` for a
missing or unreadable result file, `no verdict` for a reviewer's last
message without one). After a failed session the step is started once
more. After the second failure in a row for the same step, the item waits
on a decision of kind `session_failed`, showing both reasons and the path
of each session's stream; its options are `retry` and `abandon` (package
09 adds `take_over`).

**The limit per item.** nightshift counts the implementer and reviewer
sessions started for an item since it became `ready`, or since the person
last answered a decision of that item, whichever is later. When the count
reaches `[items].max_sessions` (12 unless configured, 2 to 100), no further
session starts for the item and it waits on a decision of kind
`session_limit`, showing each session with its role, its outcome and its
tokens; its options are `continue` (the count starts again) and `abandon`
(package 09 adds `take_over`).

The full output stream of every session is kept on the daemon's machine for
30 days and can be read with `nightshift session log <session-id>`.

### Scheduling and the daily limit

At most `[sessions].max_parallel` sessions (3 unless configured, 1 to 16)
run at once, over all workspaces. When more items could start than
sessions are free, they start in this order: repairs first, then items
already `implementing` or `reviewing`, then `ready` items by the time they
became `ready`, oldest first.

The scheduler drives every item: a package registers, for an item state,
the function that carries out that state's step, and the scheduler calls
it for each item in that state that is not paused and waits on no
decision, one call at a time per item, again after it returned. A step
that needs a session asks the scheduler for it and waits for its turn. A
package can also register a start guard: while a guard refuses an item, no
session starts for it, and the scheduler reports the guard's reason as what
the item waits for.

While the hub is unreachable, or the worker token is not accepted, no
session is started; running sessions go on.

**The daily limit.** For each workspace nightshift counts the input and
output tokens, cache reads excluded, of every session that ran for it since
midnight in the daemon's time zone. When the count reaches the workspace's
daily limit, no further session starts for that workspace until the next
midnight or until the person raises the limit with `nightshift workspace
set <slug> --daily-tokens <N>`; sessions that are running finish, and
gating, landing, health commands and publishing go on. The workspace's
thread gets one `Unread` entry, `daily limit reached: <count> of <limit>
tokens`, once per day. Repair sessions start even when the limit is
reached, and are counted.

### Pausing

`nightshift item pause <N>` ends the item's running session, recorded as
interrupted, and holds the item: no session starts for it and it is not
landed or published. Its state is kept and `item show` and `item list` mark
it `paused`. `nightshift item resume <N>` continues it with the step it was
at. `nightshift workspace pause <slug>` and `nightshift workspace resume
<slug>` do the same for every item of the workspace, repairs included, and
for items created while it is paused; `workspace list` marks the workspace
`paused`.

A decision of a paused item can still be answered; the answer takes effect
at the resume. Pausing an item that is `done` or `abandoned`, or one
already paused, exits 1 with the reason; so does resuming one that is not
paused.

### Stopping and restarting

On `SIGTERM` or `SIGINT` the daemon stops starting sessions, ends the
running ones and records each as interrupted. At the next start, a work
item whose session was interrupted, or was running when the process died,
gets a new session for the same step; the interrupted one does not count
as a failed attempt.

### Status

`GET /api/v1/status` reports the running sessions in `sessions`, each with
`id`, `item`, `workspace`, `role`, `model`, `started_at` and `tokens`, and
the member `isolation`, `user` or `unisolated`. `nightshift status` prints
`sessions: unisolated` when it is so, and one line per running session.
Each workspace's tokens used today are in the `workspaces` member that
package 02 serves, from the count this package keeps.

### The daemon's API and the client

| Command | Route | Answer |
|---|---|---|
| `nightshift item pause <N>` | `POST /api/v1/items/{number}/pause` | `200` with the item; prints `paused item <N>` |
| `nightshift item resume <N>` | `POST /api/v1/items/{number}/resume` | `200` with the item; prints `resumed item <N>` |
| `nightshift workspace pause <slug>` | `POST /api/v1/workspaces/{slug}/pause` | `200` with the workspace; prints `paused <slug>` |
| `nightshift workspace resume <slug>` | `POST /api/v1/workspaces/{slug}/resume` | `200` with the workspace; prints `resumed <slug>` |
| `nightshift session log <session-id>` | `GET /api/v1/sessions/{id}/log` | `200`, `application/x-ndjson`: the stream as it was written |

A refusal is `409 conflict` with the reason as `message` (`item 12 is
done`, `item 12 is abandoned`, `item 12 is already paused`, `item 12 is
not paused`, `item 12 is paused with its workspace demo`, `workspace demo
is already paused`, `workspace demo is not paused`); an item, a workspace
or a session the daemon does not know, and a session whose stream is no
longer kept, is `404 not_found`.

### What this package offers the later ones

Packages 05 to 10 run nothing themselves; they use these, by these names:

- **`internal/sched`, `Scheduler.RegisterStep(state, StepFunc)`**: the
  function that carries out the step of an item state, `func(ctx, item)
  error`. Its context is cancelled when the item is paused or the daemon
  stops.
- **`Scheduler.RunSession(ctx, Request) (session.Result, error)`**: asks
  for a session and waits for its end. A `Request` is a `session.Spec`
  (`Item`, `Role`, `Step`, `Fork`, `Branch` or `Commit`, `Prompt`,
  `Inputs`, `Accept`) and a `Class`. The result's `Outcome` is
  `completed`, `failed` or `interrupted`; on anything but `completed` the
  step returns and leaves the item as it is. `sched.ErrNotStarted` says
  the limit per item refused the session.
- **`sched.ClassRepair`**: the class of a repair's sessions: first in the
  start order and exempt from the daily limit. Without a class a request
  takes its place from its item's state.
- **`Scheduler.RegisterGuard(name, guard)`** and **`Scheduler.Waiting(item)`**:
  a start guard, and the reason an item's session waits (`a free session`,
  `the hub`, `the daily limit`, or a guard's).
- **`Scheduler.RecordFailure(ctx, item, step, reason, streamPath)`** and
  **`Scheduler.OpenSessionFailed(ctx, item, step, failures)`**: a failure
  of a step that was not a session's, and the `session_failed` decision
  opened directly.
- **`Scheduler.Paused(item)`, `Scheduler.OnPause(fn)`,
  `Scheduler.OnResume(fn)`**.
- **`session.UserRunner`**: runs a command as the session user. It is the
  identity of package 02's `command.Runner` from this package on, so the
  gate and the health command need nothing more.
- **The decision kinds `session_failed`** (`retry`, `abandon`) **and
  `session_limit`** (`continue`, `abandon`), to which package 09 adds
  `take_over`.

### End-to-end flows

**A session from start to its record.** An item is `implementing` and its
step asks for an implementer session. `nightshiftd` registers the session
on the hub, mints a token bound to the item's fork, clones the fork as the
session user and starts `claude -p`. The session writes its stream and a
result file and exits. nightshift reports the usage, completes the session
on the hub, revokes the token and writes the thread entries. The person
runs `nightshift session log <id>` and reads the stream. The item's review
step then runs a reviewer session in a clone of its own and receives the
verdict `agree`.

**A step that fails twice.** The implementer session of item 21 ends with
an error; the step is started once more and fails again. A
`session_failed` decision appears in `nightshift inbox --action` with both
reasons and both stream paths. The person answers `retry`; the step runs
again and completes.

**The daily limit.** Workspace `demo` has used 50,000,000 tokens by 15:00.
Its two running sessions finish; its three `ready` items stay `ready`; an
item whose review had passed still lands and is published. The workspace's
thread says `daily limit reached`. After midnight the three items start.

**A pause and a resume.** The person runs `nightshift item pause 21` while
its implementer runs. The session ends, recorded as interrupted; `item
show 21` marks it `paused` and no session starts for it. The person runs
`nightshift item resume 21`; a new implementer session starts for the same
step, and no `session_failed` decision opens for the interruption.

**A restart in the middle.** `nightshiftd` receives `SIGTERM` while two
implementers run; both sessions are ended and recorded as interrupted, and
the daemon exits with code 0. At the next start both items get a new
session for the same step.

## Design Decisions

From the source PRD, by its numbers:

- **5. The spend limit is one daily token limit per workspace; at the
  limit, running sessions finish and none starts.** The owner decided it.
- **6. Items and workspaces can be paused.** The owner decided it.
- **9. Sessions run on the daemon's machine as an operating-system user of
  their own.** The owner decided it.
- **10. The `sandbox` runner is a later PRD.** The owner decided it.
- **18. Items never wait for each other on files.** The owner decided it.
- **20. The reviewer works in a fresh context, and may run on any provider
  and model, the writer's included.** The owner decided it.
- **21. The implementer runs on `claude -p`, the reviewer on
  `agentkit-go`.** Kept from PRD 01.
- **24. A session's token holds `git:write` only.** Chosen there.
- **25. A session is ended at 10,000,000 tokens.** Chosen there.
- **26. Repairs start although the daily limit is reached.** Chosen there.
- **31. The numbers** (3 parallel sessions, 2 attempts per step, 12
  sessions per item, a 30-minute stall, 10,000,000 tokens per session,
  50,000,000 per workspace and day) are starting values, each configured.
- **36. Every session reports through a result file.** Chosen in PRD 09 of
  `agent-fox-dev/spec`.
- **41. The gate and the health command run as the session user.** Chosen
  there.
- **43. The session limit counts from the person's last answer.** Chosen
  there.

Chosen here:

1. **A process is started as the session user with `sudo -n -H -u <user>
   --`, on Linux and on macOS, also when the daemon is root.** Why: one
   mechanism for both systems, no daemon that must be root, and the
   operating system's own rule (`sudoers`) says who may become whom.
2. **What `sudo` starts is `nightshiftd` itself in a helper mode
   (`nightshiftd session-helper`), which reads one JSON document from its
   standard input (the command, its directory and its environment), runs
   the command in a process group of its own and ends that group when its
   standard input closes.** Why: an unprivileged daemon cannot signal a
   process of another user, so it ends a session by closing a pipe; a
   daemon that dies closes the pipe too, so no session outlives it; and
   secrets travel on standard input, not on a command line another user
   can list. The helper runs in unisolated mode as well, without `sudo`.
3. **A session's process gets only the environment nightshift gives it**
   (`HOME`, `PATH`, `USER`, the git settings and what the role needs),
   never the daemon's own. Why: the daemon's environment may hold
   credentials.
4. **Ending a process is `SIGTERM` to its group, then `SIGKILL` after 10
   seconds.** Why: it fits twice into the 25 seconds package 01 gives a
   shutdown step.
5. **`[sessions].user` wins over `[sessions].unisolated`; in unisolated
   mode the `sessions` check still checks that `claude` runs, as the
   daemon's user.** Why: isolation is the safe reading of a contradictory
   file, and a daemon without `claude` cannot work either way.
6. **The `sessions` check asks its questions in the order of the source
   PRD and reports the first that fails; "can read" is tried by opening
   each of the three files for reading as the session user; "`claude`
   runs" is `claude --version` exiting 0 within 30 seconds.**
7. **The reviewer's provider credential is not checked at start.** The
   `roles` check has the three reasons the source PRD names. The reviewer's
   process is given the daemon's environment variables whose names begin
   with `ANTHROPIC_`, `OPENROUTER_` or `OLLAMA_`, and no others; a missing
   credential fails the review session with `agentkit-go`'s error. Why:
   the source PRD names no reason for it, and the key has to reach the
   session's process in any case.
8. **The reviewer session runs in the helper too, as the session user.**
   Why: the source PRD says sessions run as that user, and the reviewer's
   tools read a clone a session wrote.
9. **The reviewer gets the six file navigation tools and no shell or
   write tool.** Why: a review reads; a reviewer that can run the code it
   reviews is a second implementer.
10. **The reviewer's clone is made for each review session and removed
    when it ends; the item's clone is at `<sessions.dir>/items/<N>/clone`,
    a session's files (inputs, result file) at
    `<sessions.dir>/items/<N>/run/<session-id>/`.** Why: "a clone of its
    own" and "not its working directory"; the result file must be outside
    the working tree (PRD 09 of `agent-fox-dev/spec`).
11. **`[sessions].dir` defaults to `nightshift` in the session user's home
    directory.** Why: the clones belong to that user, and its home is the
    one place it can always write. The daemon reads and removes files
    there through the helper.
12. **The session's token reaches git through the process environment (an
    `http.extraHeader` for the hub's URL given with `GIT_CONFIG_COUNT`),
    as package 02 does, and the push option `parley.session=<id>` the same
    way (`push.pushOption`).** Why: nothing is left in the clone when the
    token ends, and every push names its session without the agent having
    to.
13. **A session's token is minted with `expires: 30`, the hub's shortest
    lifetime, and revoked when the session ends; tokens of sessions found
    running at a start are revoked then.** Why: `POST …/tokens` takes
    days, and "expires with the session" needs the revocation.
14. **The reviewer's token is minted with `git:read`, as the source PRD
    says. Parley's subset rule counts exact names, so the worker token
    must hold `git:read` beside `git:write` for that mint to succeed; a
    mint the hub refuses fails the session with the hub's message, like
    any error at a session's start.** Why: the source PRD and package 01
    name the worker token's scopes without `git:read`; this package does
    not change another package's start check, and says in
    `docs/configuration.md` that reviews need the scope. Reported to the
    owner as an open point of package 01.
15. **The hub session's `id` is the UUID nightshift gives the session; the
    session is created on the item's workspace, not on its fork.** Why: a
    chosen id makes the create safe to retry, and the hub links work on a
    fork to a session of its parent.
16. **Usage is reported once, at the session's end, as one usage record
    (`input_tokens`, `output_tokens`, `cache_read_tokens`), with the
    cache-creation tokens on the complete; an interrupted, stalled or
    limited session is completed as `failed` with its reason as
    `error_message`.** Why: the source PRD says "when the session ends"
    and "completed or failed".
17. **Reports the hub did not take (unreachable, token not accepted) are
    kept in the store and sent again at each scheduler pass; they never
    hold an item back; `409 session_not_active` ends the tries.**
18. **"Input and output tokens, cache reads excluded" counts input tokens,
    cache-creation tokens and output tokens.** Why: a cache write is
    input that was not read from the cache. For `claude -p` the running
    count is summed over the stream's `assistant` events, one per message
    id, and the `usage` of the last `result` event replaces it when it is
    greater; for the reviewer it is `core.Usage` (`InputTokens +
    CacheWriteTokens + OutputTokens`).
19. **A workspace's daily count grows as usage is observed, at the time it
    is observed, so a running session counts and a session that crosses
    midnight is split between the two days.** Why: the flow "has used
    50,000,000 tokens by 15:00" counts sessions still running.
20. **The `Unread` entry is written when the count first reaches the limit
    on a day, and not again that day, also when the limit was raised and
    reached again.** Why: "once per day".
21. **The start order has a fourth class: a session asked for an item in
    any other state (planning among them) comes after the `ready` items,
    oldest item first.** Why: the source PRD orders three classes and
    planner sessions are in none of them; work in progress goes first.
22. **A session is asked for by an item's step and waits for its turn; the
    scheduler looks at the queue once a second and whenever something
    changed.** Why: a step such as "implement, then gate" is one function,
    and only its session takes one of the parallel places.
23. **The `session_limit` decision opens when a further implementer or
    reviewer session is asked for with the count at the limit.** Why: "no
    further session starts" is what the limit is for; an item whose last
    allowed session finished the work goes on without asking.
24. **Failures are counted per item and step, a step being the name the
    asking package gives (`plan`, `implement`, `review`); a completed
    session of that step, or an answer `retry`, sets the count to zero; an
    interrupted session changes nothing.** A package can also record a
    failure that was not a session's (package 06: a failed gate).
25. **A session that cannot be started (the hub refuses the registration
    or the token, the clone fails) counts as failed with that error's
    first line.** Why: it is "an error", and two of them should reach the
    person as any other failure does.
26. **`retry` is the recommended option of `session_failed`, `continue`
    of `session_limit`.**
27. **Pausing is two flags, one on the item and one on the workspace; an
    item is held while either is set. `item resume` for an item held only
    by its workspace is refused (`item <N> is paused with its workspace
    <slug>`).** Why: `workspace resume` must not release an item the
    person paused by itself.
28. **A pause cancels the item's running step, not only its session.**
    Why: "it is not landed or published"; the step is called again at the
    resume.
29. **The routes** of the table under *The daemon's API and the client*.
    Why: the client speaks only the daemon's API, and the source PRD names
    the commands alone.
30. **`nightshift session log` prints the stream's lines unchanged, with
    and without `--json`.** Why: the stream is JSON lines already.
31. **Streams are files, `<directory of store.path>/streams/<session-id>.jsonl`,
    with the session's standard error beside them as `.stderr`; files of
    sessions that ended more than 30 days ago are removed at start and
    once a day.**
32. **`status` gets the member `isolation`.** Why: `sessions` is a list in
    package 01's document, and `sessions: unisolated` needs a place in the
    JSON.
33. **Thread entries of kind `session_started` and `session_ended`** (role,
    session id, and at the end the outcome, the reason and the tokens);
    neither marks the thread unread.
34. **Events: `session.started` and `session.ended`, and `item.updated`
    and `workspace.updated` for a pause and a resume.**
35. **The item's clone follows the fork's branch and keeps everything
    else.** At a session's start the branch is created at the tip of the
    fork's default branch when neither side has it, set to the fork's head
    when the fork's branch has commits the local one lacks, and left as it
    is otherwise; untracked files are never removed. Why: the fork is what
    the hub and the person see (a person may push to it), and "kept between
    the sessions" is for what a session left behind.
36. **With a session user configured and `clones_dir` (package 02) not
    set, the workspace clones go to `<sessions.dir>/workspaces`.** Why:
    from this package on git, the gate and the health command run as the
    session user, who cannot write in the daemon's home directory.

## Dependencies

- **`01_daemon_api_and_credentials`** (status `draft`): the configuration
  struct, the ordered start checks, the store and its migrations, the hub
  client with `Available`, the fake hub `internal/hubtest`, the API
  server's `Handle`, the client's `Register`, the status source
  `sessions`, the named shutdown steps, the event log and the e2e helper.
- **`02_workspaces_and_policy`** (status `draft`): `workspace.Service`
  with each workspace's daily limit, `DefaultDailyTokens`,
  `SetTokensUsed` and `Decorate`; `AddWorkspaceListColumn` of
  `internal/cli`; `command.Runner` with `SetIdentity`; `gitclone.Sync`.
- **`03_work_items_and_inbox`** (status `draft`): `internal/item` (items,
  states, `Transition`, `Abandon`, `OnAbandon`, `OnChange`,
  `RegisterMarker`) and `internal/inbox` (`Append`, `WorkspaceThread`,
  `RegisterKind`, `OnAnswer`, `Open`).
- **Parley** (`agent-fox-dev/parley`), the hub: its packages
  `06_api_keys_and_access_tokens`, `08_workspace_bound_tokens`,
  `17_git_smart_http` and `49_agent_sessions_and_usage`, each with every
  task `done` on 2026-11-27.
- **`agentkit-go`** (`github.com/agent-fox-dev/agentkit-go` v0.11.0), for
  reviewer sessions.
- **Claude Code** (`claude`) and **`sudo`** on the daemon's machine.

## Verified External API

- **Parley's REST API**, read in `docs/api.md` of `agent-fox-dev/parley`
  at commit `7eabfb9`: `POST /api/v1/sessions`, `POST
  /api/v1/sessions/{id}/usage`, `POST /api/v1/sessions/{id}/complete`,
  `POST /api/v1/workspaces/{slug}/tokens` (the body of the account's token
  route, the subset rule, `expires` in days), `DELETE
  /api/v1/user/tokens/{token_id}`, the push option `parley.session=<id>`,
  and git's HTTP Basic credential.
- **`github.com/agent-fox-dev/agentkit-go` v0.11.0**, read in its
  `docs/generated/`: `agentkit.Config`, `agentkit.New`, `Agent.Run`,
  `Agent.Stream`, `Agent.Usage`, `catalog.Providers`, `catalog.Select`
  with `catalog.ErrUnknownProvider`, `core.Usage`, `core.RunResult`,
  `tools.For`, `tools.FileNavigationTools`, `workspace.Open`,
  `provider/faux`.
- **`claude -p`**, read in `docs/specrun.md` of `agent-fox-dev/spec` at
  commit `e9081fa`: the command line, `CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS`
  and `is_error` on the last `result` event. **Not found there:** the
  flag `--model`, and where the stream's events carry token usage; both
  are assumed (`usage` with `input_tokens`, `output_tokens`,
  `cache_creation_input_tokens` and `cache_read_input_tokens` on an
  `assistant` event's `message` and on the `result` event).
- **`sudo`**: `-n` (never prompt), `-H` (set `HOME`), `-u <user>`; assumed
  from its manual, not read here.

## Glossary

- **session**: one run of an agent for one step of a work item, as a
  process on the daemon's machine.
- **role**: `planner`, `implementer` or `reviewer`; each has a model, the
  reviewer a provider too.
- **session user**: the operating-system user that sessions, the gate and
  the health command run as.
- **helper**: `nightshiftd` started in the mode that runs one command as
  the session user and ends it when its standard input closes.
- **step**: what an item's state calls for next; a package registers the
  function that carries it out, and names the step of each session it
  asks for.
- **start guard**: a function a package registers that can refuse a
  session's start for an item, with a reason.
- **stream**: everything a session wrote to its standard output, kept as
  a file.
- **result file**: the JSON document a planner or implementer session
  writes at the path it was given.
- **verdict**: a reviewer's `agree` or `object`, with findings.
- **daily limit**: the tokens a workspace's sessions may use between two
  midnights before no further session starts.
- **interrupted**: a session ended by a pause, an abandon, a shutdown or
  the daemon's death; it is not a failure.
- **paused**: an item, or every item of a workspace, held by the person
  until they resume it.
