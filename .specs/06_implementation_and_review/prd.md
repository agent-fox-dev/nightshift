---
spec_id: "06"
spec_name: "implementation_and_review"
title: "Implement a ready item and review every version"
status: "active"
created_at: "2026-11-28T10:23:45Z"
intent_hash: "690ec44d1ff62d152a08e65d84d69c42953b0fbff32983908240b53a3e4889f7"
contract_hash:
  requirements.json: "1cca22281d05fa8d27fd1161652dfb59274e9c63df81c9e310cea94d23d7a0a2"
  tasks.json: "f9d5bf80fc79148507b573dbf80d0eb7e2e02e268126b618c7997925c794aebe"
schema_version: 3
source: "docs/prd/02-drive-work-items-through-the-hub.md"
---

# Implement a ready item and review every version

## Intent

A work item whose plan is settled has to become code that can be trusted
without a person reading every line. nightshift has an implementer session
implement the item's package in the item's task fork, takes as done only
what the hub and the fork show, runs the package's gate on every version of
the item's change, has a reviewer with a fresh context review every gated
version, sends work back to the implementer as findings until gate and
reviewer agree, and asks the person only where it cannot go on by itself: a
version that touches a blocked path, and a reviewer and an implementer who
do not come to terms.

## Goals

- **A `ready` item is implemented without the person.** nightshift opens the
  item's change, starts the implementer with `af-impl`, and moves the item
  through `implementing` and `reviewing` to `landing`.
- **A session is believed only as far as the hub confirms it.** A session
  counts when the change has a new version and the package on that version
  has no open task, whatever the session says of itself.
- **Every version is gated and reviewed.** The package's gate runs as the
  session user on a clone of the fork at the version and is reported to the
  hub as the check `nightshift-gate`; the reviewer's verdict is reported as
  `nightshift-review`. Neither check is carried over to another version by
  nightshift.
- **Work is sent back as findings.** A failed gate, a reviewer's objection,
  a person's comment and, from later packages, a conflict report or a failed
  health command start the implementer in rework mode; its replies are
  written to the thread.
- **A reviewer's verdict is a check, never an approval.** An approval a
  policy rule requires stays a person's.
- **A blocked path stops the item at the push.** The item waits on a
  `blocked` decision before the gate runs.
- **Disagreement ends with the person.** After 3 reviews without `agree` the
  item waits on a `review_disagreement` decision.
- **Files outside the plan are noted, not held.**
- **A fork does not expire under running work, and expired work is
  restored.** The fork's lifetime is extended while the item waits on no
  decision; a fork that expired while it did is replaced when the person
  answers.
- **An item waits for the packages it depends on.**

## Non-goals

This package is the sixth of ten written from
`docs/prd/02-drive-work-items-through-the-hub.md`. What belongs to the
others:

- **`01_daemon_api_and_credentials`**: the configuration loader, the store,
  the hub client and its monitor, the fake hub, the event log. This package
  adds one configuration key, hub client calls, fake hub routes and store
  migrations to them.
- **`02_workspaces_and_policy`**: workspaces, the policy and its three
  checks, `internal/command` and `internal/gitclone`. This package runs the
  gate through `command.Runner` and clones with `gitclone.Sync`.
- **`03_work_items_and_inbox`**: work items, their states and transitions,
  linked items, threads, the decision machinery. This package declares the
  kinds `blocked` and `review_disagreement` in its registry.
- **`04_sessions_and_scheduling`**: running a session (`claude -p`, the
  reviewer through `agentkit-go`), its token, its stream, the failed session
  and its retry, the `session_failed` and `session_limit` decisions, the
  start order, the daily limit, pausing. This package says what the
  implementer and the reviewer are told and which result is accepted, and
  registers its steps and its start guard there.
- **`05_planning`**: planning, the `plan` decision, the kept package text,
  and `internal/change`: creating the item's task fork, opening its draft
  change, abandoning it, forgetting a fork. This package calls those and
  adds what happens to a change after the plan: opening it, its versions,
  its checks, and the restore of a fork that expired.
- **`07_landing`**: everything in the state `landing`: the landing
  conditions, `nightshift-release`, the land and its outcomes, the
  `approval`, `conflict` and `policy` decisions. This package moves the item
  to `landing` and offers the rework entry point a conflict or a rejection
  starts, and the function that opens a `blocked` decision.
- **`08_health_and_publishing`**: the health command, repairs and reverts.
  A repair's implementer is started through the rework entry point of this
  package, with the repair class.
- **`09_notes_and_take_over`**: notes, taking over, and the option
  `take_over`, which that package adds to `blocked` and
  `review_disagreement`; this package declares both kinds without it. A
  version a person pushed is gated and reviewed by this package as any
  other.
- **`10_rules_brief_and_forge`**: rules, the brief (which lists the files
  outside the plan this package records) and the forge write-back.

Also out, for every package of that PRD: stacked changes, several attempts
at one item side by side, and the agents' prompts: the wording of the
review brief is not specified, only what it must hold.

## Background

Packages 01 to 05 leave a daemon that registers workspaces, takes in work
items, plans a defect or a change request into a spec package on a task
fork with a draft change, and can run sessions under a scheduler. Nothing
implements a package yet. The Go packages `internal/impl`, `internal/gate`
and `internal/review` are new.

What this package uses of Parley, as its `docs/api.md` states at commit
`7eabfb9`:

- A **change** records a version for every head its source branch has while
  it is draft or open. `GET …/changes/{number}` answers the current
  `version`, `head_sha`, `base_sha`, `state`, `paths` (the files the current
  version touches, at most 2,000) and `landing` with its `requirements`,
  each with `rule`, `paths` (the first 50), `checks`, `block` and
  `satisfied`; `include=versions,overlaps` adds the versions and the overlap
  entries. A draft never lands; `PATCH …/changes/{number}` with
  `{"state":"open"}` marks it ready.
- A change cannot be opened for a branch that has no commit its target
  lacks (`400 nothing_to_land`), so an imported package's change exists only
  after the implementer's first push.
- `PUT …/changes/{number}/checks/{name}` reports a check on a `version`
  with `status` and a `summary` of at most 2,000 characters; it needs the
  scope `checks:write`, and a later report for the same version and name
  replaces the earlier one.
- `GET …/changes/{number}/diff?version=<n>&format=patch` answers a version's
  diff against its base as patch text, cut at 8 MiB with the header
  `X-Parley-Truncated: true`.
- The head of every version stays at
  `refs/parley/changes/<number>/<version>` in the target workspace's
  repository, also after the fork is gone, and a fork reads its parent's
  objects, so a branch of a new fork can be created at an earlier version's
  head with `POST …/scm/refs`.
- A fork lives until its `expires_at`; `PATCH /api/v1/workspaces/{fork}`
  with `ttl` sets it to now plus `ttl`. Removing a fork abandons its draft
  and open changes.
- `GET …/scm/file` and `GET …/scm/tree` read a file and a directory at a
  revision, with the scope `git:read`.
- Two changes **overlap** when both are draft or open, have the same target
  branch and touch a file in common; the hub tracks it about a second
  behind a push.

What it uses of `agent-fox-dev/spec`: `afspec.LoadSpec` and
`Spec.IsFinished`; a task's `touches` and the plan's `dependencies`;
`speca <package> --gate full`, which runs the package's `linter` and
`all_tests` and answers one JSON object whose `result.gate.verdict` is
`pass`, `fail` or `not_run`; and `af-impl` in the modes PRD 09 of that
repository specifies (`--no-merge`, `--rework`, `--result`), which its
skill does not have yet on 2026-11-28 (packages 30 and 31 of that
repository are active with every task pending).

## Requirements

Constraint: Linux and macOS; Windows is best effort. Constraint: `speca` is
installed on the daemon's machine where the session user finds it on
`PATH`.

### Configuration

| Key | Meaning | Default |
|---|---|---|
| `[gate].timeout` | how long one command of a gate may run | `30m` |

A value that is not greater than zero fails the start check `config`
(`config: gate.timeout must be greater than 0`).

### Scheduling: dependencies and overlaps

A `ready` item does not start while a package it depends on (the
`dependencies` of its `tasks.json`) is not finished on the default branch.
Items do not wait for each other because of the files they touch. When the
hub reports that an item's change overlaps another open change, nightshift
notes it once in the item's thread with the other change's number.

A dependency is finished when the package with that number in `.specs/` at
the tip of the workspace's default branch loads and `IsFinished` says so; a
package that is not there is not finished. `nightshift item show` names
what the item waits for: `package 03 on main`.

### Implementation

For a `ready` item nightshift creates the task fork and the draft change,
unless planning created them, opens the change (it is a draft no longer),
and starts an implementer session that implements the item's package with
`af-impl`, told to stop at the pushed branch (`af-impl <package>
--unattended --no-merge --result <file>`). The session commits on the
change's branch and pushes it to the fork; it merges nothing and calls no
forge. Every push records a version of the change.

An imported package has no commit on its branch before the implementer's
first push, and the hub opens no change for such a branch. For such an
item nightshift creates the fork, and opens the change, not as a draft,
when the first session ended and its branch is on the fork.

The item is `ready` until its first implementer session starts, and
`implementing` from then on.

When the session ends, nightshift reads from the hub and the fork, not from
the session's account: the change has a version newer than at the session's
start, and the package on that version has no task `pending` or
`in_progress`. When either does not hold, the session counts as failed with
the reason (`no new version`, `tasks still open`). A session that left no
result file counts as failed with the reason `no result`.

**Sending work back.** Wherever the source PRD says the implementer is
started with something (a failed gate's verdict, the reviewer's findings,
the person's comment, a conflict report, a failed health command's output),
it is started as a rework of the package (`af-impl <package> --unattended
--rework <findings file> --result <file>`), each of those written as a
finding. A comment of the person, and a finding the person made binding,
must be fixed, not answered. The session's replies, one per finding, are
written to the thread. A rework that changed nothing, or failed, counts as
a failed session.

The findings file is a JSON array; a finding has `id`, `source` (`gate`,
`review`, `person`, `health` or `conflict`), `text` and, where there are
any, `file`, `line`, `criterion`, `output` and `binding`.

nightshift then runs the package's gate on the version (`speca`'s gate, on a
clone of the fork at that version) and reports it to the hub as the check
`nightshift-gate`, `passed` or `failed`, with the verdict's summary. On
`failed` the implementer is started again with the verdict; this counts
toward the two attempts of *Roles and sessions* of the source PRD.

After every new version nightshift reads the hub's evaluation of it:

- when a requirement of it blocks, the item waits at once on a decision of
  kind `blocked`, naming the rule and the files; its options are `revise`
  (with a comment, required; the implementer is started with it) and
  `abandon` (package 09 adds `take_over`);
- the files of the version that the package's tasks did not name in
  `touches` are listed in one thread entry, `outside the plan: <files>`
  (the first 50). Nothing is held back for them.

Open items the session's result file lists (what `af-impl` calls `unmet`,
the member `open_items`) each become a new work item of kind `defect` in
state `new`, linked to the item they came from, and are listed in the
thread. Such an item stays `new`, with no session, until the person runs
`nightshift item plan <N>` (package 05).

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
name a file and a line or a criterion. A later review of the same item is
given the earlier findings with the implementer's replies as well.

On `agree`, nightshift reports the check `nightshift-review` as `passed` on
that version, and the item is `landing`. On `object`, it reports `failed`,
and the implementer is started with the findings; its new version is gated
and reviewed again. After 3 reviews of one item without `agree`, the item
waits on a decision of kind `review_disagreement`, showing each open
finding with the implementer's reply; its options are `side_with_reviewer`
(the implementer gets one more session with the findings marked as
binding), `side_with_implementer` (`nightshift-review` is reported
`passed`, the summary naming the person's decision) and `abandon` (package
09 adds `take_over`).

nightshift never records an approval on the hub for a model's verdict: a
model's review is a check, so that an approval a policy rule requires stays
a person's.

### What this package offers the later ones

Packages 07 to 10 use these, by these names:

- **`internal/impl`, `(*impl.Service).Rework(ctx, item int, findings
  []impl.Finding) error`**: the rework entry point. It keeps the findings
  for the item's next implementer session and moves an item that is
  `reviewing` or `landing` to `implementing`; the version the rework
  produces is evaluated, gated and reviewed again. `impl.Finding` has `ID`,
  `Source`, `Text`, `File`, `Line`, `Criterion`, `Output` and `Binding`;
  an empty `ID` is given one.
- **`(*impl.Service).SetClass(ctx, item int, class sched.Class) error`**:
  the class every later session of the item is asked with (a repair:
  `sched.ClassRepair`).
- **`(*impl.Service).ReportCheck(ctx, item int, name, status, summary
  string) error`**: reports a check on the current version of the item's
  change and writes the thread entry.
- **`(*impl.Service).Version(ctx, item int) (impl.Version, error)`**: the
  item's change as the hub has it now: `Workspace`, `Change`, `Number`,
  `HeadSHA`, `BaseSHA`, `State`, `Paths`, `Requirements` and `Overlaps`.
- **`(*impl.Service).OpenBlocked(ctx, item int, reqs
  []impl.Requirement) error`**: opens the `blocked` decision, for a block
  the hub reports at a land.
- **`(*impl.Service).RestoreFork(ctx, item int) error`**: restores an
  expired fork and does nothing for one the hub still has; a step of a
  later package calls it before it uses the fork.
- **`(*impl.Service).OnAnswer(kind, option, fn)`**: registers an answer's
  handler with the inbox so that, for a paused item, the answer is kept
  and carried out at the resume.
- **`(*impl.Service).OutsidePlan(ctx, item int) ([]string, error)`**: the
  files of the item's last evaluated version outside its plan.
- **`internal/gate`, `gate.Run(ctx, gate.Options) (gate.Verdict, error)`**
  and the names `gate.CheckName` (`nightshift-gate`) and
  `review.CheckName` (`nightshift-review`).
- **`internal/hub`**: `(*hub.Client).Change`, `SetChangeState`,
  `ReportCheck`, `ChangeDiff`, `File`, `Tree`, `CreateRef`, `Commit` and
  `SetForkTTL`.

### What this package uses of package 05

- `change.Service.Get(ctx, item)`: the item's `Record` with `Workspace`,
  `Fork` (the fork's slug), `GitURL`, `Branch` (`item-<N>`), `Generation`,
  `Change` (0 while the item has no change), `ChangeRef` and `ChangeURL`;
  `change.ErrNotFound` for an item without a fork.
- `change.Service.EnsureFork(ctx, item)`: creates the item's task fork
  where it has none.
- `change.Service.OpenDraft(ctx, item, title, intent, sessionID)`: opens
  the draft change for the item's branch.
- `change.Service.Forget(ctx, item)`: after it, `EnsureFork` creates a new
  fork for the item (`<workspace>--i<N>-<g>`).
- `change.Service.Abandon(ctx, item, reason)`, which package 05 registers
  as the abandon hook.
- `plan.Service.Kept(ctx, item)`: for a planned item the package's
  directory (`Dir`, `.specs/NN_name`) and the kept text (`Files`, by path
  below `Dir`).
- `plan.Service.RegisterPrepare(name, fn)`: a function package 05 calls
  before it carries out an answer `approve` or `revise` to a `plan`
  decision; while it returns an error the answer waits. This package
  registers the fork's restore there.

### End-to-end flows

**A ready item reaches `landing`.** Item 12 is `ready` with a draft change
from its planning, and the package it depends on is finished on `main`.
nightshift opens the change and asks for an implementer session; when it
starts, the item is `implementing`. The implementer pushes, which records
version 2, and writes its result file. nightshift finds the new version and
no open task, creates one `defect` item from the result's open item, reads
the hub's evaluation, notes `outside the plan: docs/api.md`, runs the gate
and reports `nightshift-gate` `passed`; the item is `reviewing`. The
reviewer agrees; `nightshift-review` is reported `passed`; the item is
`landing`. The hub holds no approval.

**A reviewer who does not agree.** The reviewer objects to version 2 of
item 13; the implementer reworks it, and its replies are in the thread.
The reviewer objects to version 3 and to version 4. A
`review_disagreement` decision appears in `nightshift inbox --action` with
the findings and the replies. The person runs `nightshift decide <id>
--option side_with_implementer`; `nightshift-review` is reported `passed`
on version 4 with the summary naming the decision, and the item is
`landing`.

**A blocked path, and a fork that expired meanwhile.** The implementer's
version of item 14 touches `secrets/key.pem`, which a rule blocks. The item
waits on a `blocked` decision naming the rule and the file; no gate ran.
The fork expires on the hub. Days later the person answers `revise` with a
comment. nightshift creates a new fork and a new change at the last
version's head and names both changes in the thread; the implementer
reworks with the comment as a binding finding; the new version no longer
touches the file, is gated and goes to review.

## Design Decisions

From the source PRD, by its numbers:

- **4. Files outside the plan are noted, not held.** The owner decided it.
- **15. A fork may expire while its item waits on the person; nightshift
  restores the work when they answer.** The owner decided it.
- **18. Items never wait for each other on files.** The owner decided it;
  an overlap is a note.
- **20, 21. The reviewer works in a fresh context and reviews every
  change; the implementer runs on `claude -p` with `af-impl`.** The owner
  decided it.
- **24. nightshift opens the change; a session's token cannot.** Chosen
  there.
- **27. A blocked path stops the item at the push.** Chosen there.
- **30. Open items a session reports become unplanned `defect` items.**
  Kept from PRD 01.
- **31. 3 review rounds and 2 attempts per step** are starting values.
- **36. Work is sent back through `af-impl`'s rework mode, and every
  session reports through a result file.** The owner decided the first.
- **40. A person's pushed version is gated and reviewed as an agent's.**
  Chosen there.
- **41. The gate runs as the session user.** Chosen there.

Chosen here:

1. **Three Go packages: `internal/gate` (run `speca`'s gate in a directory
   and read its verdict), `internal/impl` (everything from `ready` to a
   gated version, the findings, the forks, the `blocked` decision) and
   `internal/review` (the review step and `review_disagreement`), which
   imports `impl`.** Why: the split's table puts `blocked` with the review,
   but the evaluation that opens it runs before the gate, in `impl`, and
   `review` needs `impl`'s rework entry point.
2. **What a step does is decided from the hub's state and the stored
   findings, in a fixed order, not from where the previous call stopped.**
   Why: a restart, a pause, a restored fork and a version a person pushed
   then need no case of their own.
3. **The item moves to `implementing` when its first implementer session
   starts, which nightshift learns from the event `session.started` of the
   event log.** Why: the source PRD keeps an item `ready` while it waits
   for a free session, the daily limit or a dependency, and package 04's
   start order and guards apply to a session asked for a `ready` item.
4. **The package of a version is read from the hub (`GET …/scm/tree` and
   `…/scm/file` at the version's head) into a temporary directory and
   loaded with `afspec.LoadSpec`; "no task `pending` or `in_progress`" is
   `IsFinished`.** Why: "from the hub, not from the session's account", and
   the daemon's user need not read the session user's files.
5. **A session's acceptance is package 04's accept function: `no new
   version`, `tasks still open` and `package does not load: <reason>` are
   its errors.** A rework that changed nothing or failed is recognised the
   same way: the change has no newer version, reason `no new version`.
6. **Replies are written to the thread from every rework's result file,
   also from a rework that is not accepted.** Why: a declined finding is an
   answer the person and the reviewer should see.
7. **Findings wait in the store until a rework session that was given them
   is accepted. While the package has an open task, the implementer is
   started with `--no-merge`, and the findings wait for the rework after
   it.** Why: `af-impl --rework` refuses a package with open tasks.
8. **Finding ids are `f<n>`, counted per item.**
9. **The gate is `speca <package> --gate full --timeout <gate.timeout>`,
   run with `sh -c` through `command.Runner` in a clone at
   `<sessions.dir>/items/<N>/gate`, with `XDG_STATE_HOME` set to an empty
   directory of that run, its JSON written to a file read through package
   04's file read.** Why: the session user owns the state directory in
   which `speca` records passes for reuse, so a pass recorded there is the
   session's account; a fresh directory makes every gate run its commands.
10. **Everything but the verdict `pass` is reported as `failed`**: `fail`,
    `not_run`, a `speca` that exits other than 0 or cannot be found, output
    that does not parse, and a run past twice the timeout plus one minute.
    Why: the check has two values, and a gate that did not run proves
    nothing.
11. **The check's summary is `speca`'s `summary`, then for a failure the
    failing command's name, its exit code and its `tail`, cut to 2,000
    characters.**
12. **A failed gate is recorded with `Scheduler.RecordFailure` for the step
    `implement`; when two gates in a row fail for an item with no passed
    gate between them, nightshift opens `session_failed` with
    `Scheduler.OpenSessionFailed`.** Why: package 04 sets its count to zero
    when the rework session completes, so two failed gates would otherwise
    never be "two attempts".
13. **A file is inside the plan when a `touches` entry equals its path or
    names a directory above it, or when it lies in the package's own
    directory.** Why: `touches` names files, packages and modules, and
    every implementation writes the package's `progress.json`.
14. **The `outside the plan` entry is written for a version when the list
    is not empty and differs from the one last written for the item.**
    Why: a rework that adds no file should not repeat it.
15. **Overlaps are read after each gate, and each other change is noted
    once per item** (`overlaps change demo#7`). Why: the hub tracks an
    overlap behind the push, and the gate gives it the time.
16. **A `blocked` decision is opened once per version.**
17. **An answer is carried out by its handler at once, and at the resume
    for a paused item** (`Scheduler.Paused`, `Scheduler.OnResume`).
18. **`revise` is the recommended option of `blocked`,
    `side_with_reviewer` of `review_disagreement`.**
19. **A reviewer's finding that names neither a file with a line nor a
    criterion fails the review session** (`finding names no file and line
    and no criterion`), through the accept function.
20. **The reviewer's prompt holds the package's directory, the list of
    changed files, the diff up to 400,000 characters (then a line saying
    how much was left out), the gate's summary, and the earlier findings
    with their replies.** Why: the reviewer has no shell and cannot produce
    a diff, and reads the rest in its clone at the version's head.
21. **The count of reviews without `agree` is the number of `object`
    verdicts since the item's last `agree`; an answer does not set it
    back.** Why: after the person sided with the reviewer once, a further
    objection should reach them at once.
22. **The fork's lifetime is set to `7d` every 6 hours** for every item
    that has a fork, is neither `done` nor `abandoned` and waits on no
    decision. Why: 7 days is the hub's default and inside every maximum it
    can be configured with.
23. **An expired fork is noticed by `GET /api/v1/workspaces/{fork}`
    answering `404`**, at three moments: when a decision of the item is
    answered (the event `decision.answered`, unless the option is
    `abandon`), before package 05 carries out an answer to a `plan`
    decision (`plan.Service.RegisterPrepare`), and at the start of every
    step of this package. Why: the answer of a kind another package
    declared has to restore the fork too, and a daemon that was stopped
    for longer than the lifetime has no answer to react to.
24. **The restored change is opened as the earlier one was: a draft for an
    item that is `new`, `planning` or `awaiting_approval`, open otherwise;
    an item that was `reviewing` or `landing` returns to `implementing`.**
    Why: "the gate and the review run again on the new one".
25. **An open item's text is its `title`, `missing`, `why` and `closes`,
    cut to 20,000 characters; an open item whose title a linked item of
    the same parent already has creates none.** Why: an accept that runs
    twice must not create two items.
26. **Thread entries: `check`, `reply`, `outside_plan`, `overlap` and
    `fork_restored`; none marks the thread unread.**
27. **No route and no command.** Everything of this package is reached
    through the inbox and the item commands of packages 03 and 04.

## Dependencies

- **`01_daemon_api_and_credentials`** (status `draft`): the configuration
  struct, the store's migrations, the hub client, `internal/hubtest`, the
  event log with `Subscribe`, the e2e helper.
- **`02_workspaces_and_policy`** (status `draft`): `command.Runner`,
  `gitclone.Sync`, `workspace.Service.Get`.
- **`03_work_items_and_inbox`** (status `draft`): `internal/item`
  (`Get`, `List`, `Transition`, `Abandon`, `CreateLinked`) and
  `internal/inbox` (`RegisterKind`, `OnAnswer`, `Open`, `Append`).
- **`04_sessions_and_scheduling`** (status `draft`): `session.Spec` and
  `session.Result`, the `UserRunner`'s file read, `Scheduler.RegisterStep`,
  `RunSession`, `RegisterGuard`, `RecordFailure`, `OpenSessionFailed`,
  `Paused`, `OnResume`, `Wake`.
- **`05_planning`** (status `draft`): `change.Service` (`Get`,
  `EnsureFork`, `OpenDraft`, `Forget`) and `plan.Service` (`Kept`,
  `RegisterPrepare`).
- **Parley** (`agent-fox-dev/parley`), the hub: its packages
  `15_task_fork_creation`, `16_task_fork_lifetime`,
  `19_git_push_to_change`, `22_change_opening_and_versions`,
  `23_change_lifecycle_and_mergeability`, `27_landing_policy`,
  `28_approvals_and_checks` and `30_awareness`, each with every task `done`
  on 2026-11-27.
- **`afspec`** (`github.com/agent-fox-dev/spec/afspec`) and **`speca`**
  (`agent-fox-dev/spec`, `cmd/speca`).
- **`af-impl`** in the modes of
  `docs/prd/09-run-af-spec-and-af-impl-under-a-controller.md` of
  `agent-fox-dev/spec`. Its packages 30 and 31 are not implemented on
  2026-11-28, so an implementer session cannot produce an accepted result
  before they are.

## Verified External API

- **Parley's REST API**, read in `docs/api.md` of `agent-fox-dev/parley` at
  commit `7eabfb9`: `GET …/changes/{number}` with `include`; `PATCH
  …/changes/{number}` with `state`; `PUT …/changes/{number}/checks/{name}`;
  `GET …/changes/{number}/diff`; `GET …/changes/{number}/overlaps` and the
  overlap entry; the version ref; `PATCH /api/v1/workspaces/{slug}` with
  `ttl`; `GET …/scm/file`, `GET …/scm/tree`, `POST …/scm/refs`, `POST
  …/scm/commits`; `POST …/changes` refusing `nothing_to_land`; that
  `changes:write` satisfies `changes:read` and `git:write` `git:read`.
  **Not read:** the status of `GET /api/v1/workspaces/{slug}` for a fork
  that was removed (assumed `404`), and the member names of a `scm/tree`
  list.
- **`github.com/agent-fox-dev/spec/afspec`**, read in
  `docs/generated/afspec.txt` of `agent-fox-dev/spec` at commit `8faad70`:
  `func LoadSpec(dir string) (*Spec, error)`, `func (s *Spec) IsFinished()
  bool`, `Spec.Tasks *TasksV3Json` with `Dependencies []Dependency`
  (`Spec`, `Reason`) and `Tasks []Task`, a task's `Touches []string`,
  `Spec.PRDBody`.
- **`speca`**, read in `docs/speca.md` of `agent-fox-dev/spec` at commit
  `8faad70`: the gate form, `--timeout`, the envelope's `summary`,
  `result.gate` and `$XDG_STATE_HOME`.
- **`af-impl` arguments: NOT FOUND in `skills/`** at commit `8faad70`.
  `--no-merge`, `--rework` and `--result`, the result file (`outcome`,
  `replies`, `open_items`) and the findings file are as PRD 09 of that
  repository specifies them; `af-impl --unattended` exists.

## Glossary

- **version**: one head of the source branch of an item's change, as the
  hub numbered it.
- **gate**: the package's `linter` and `all_tests`, run by `speca`.
- **finding**: one thing the implementer must fix or answer: from the gate,
  the reviewer, the person, a conflict or the health command.
- **binding**: a finding that must be fixed, not answered.
- **rework**: an implementer session in `af-impl`'s rework mode, given
  findings.
- **reply**: the implementer's answer to one finding: `fixed`, `declined`,
  `not_reproduced` or `failed`, with a text.
- **evaluation**: the hub's `landing` of a change's current version: which
  policy rules its files select and what each requires.
- **outside the plan**: a file of a version that no task's `touches`
  names.
- **open item** (`unmet`): something an implementation left open, listed in
  the session's result file.
- **start guard**: a function registered with the scheduler that can
  refuse a session's start for an item, with a reason (package 04).
- **task fork, change, check, policy, overlap**: as Parley defines them.
- **package, `touches`, `dependencies`**: as the spec format of
  `agent-fox-dev/spec` defines them.
