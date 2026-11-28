---
spec_id: "07"
spec_name: "landing"
title: "Land a reviewed change under the hub's policy"
status: "active"
created_at: "2026-11-28T10:21:37Z"
intent_hash: "b618dfe6b602a8b44b7cdcc0447e1680c95b9462a1608b958552a543086b5f0a"
contract_hash:
  requirements.json: "047b78fee22dbe34ecaa0deb9843ee0fe694e994e6986aad4d110fd23ec5494c"
  tasks.json: "37b9713d7280f90481cc3baf0b94cbe0e6ffa9474516bdac27902bc6c4653873"
schema_version: 3
source: "docs/prd/02-drive-work-items-through-the-hub.md"
---

# Land a reviewed change under the hub's policy

## Intent

A change that nightshift's gate and its reviewer have passed must reach the
main branch without a person, except where the hub's landing policy says a
person is needed. nightshift lands such a change only while its item and
its workspace are in a state to take it, says so on the hub with a third
check, asks the hub to land exactly the version that was gated and
reviewed, and turns every answer of the hub into the item's next step: on
to verification, to the person as a decision, or back to the implementer.

## Goals

- **A reviewed version lands by itself.** With `nightshift-gate` and
  `nightshift-review` passed on the current version, nightshift reports
  `nightshift-release` and asks the hub to land that version, and the item
  is `verifying` when the hub landed it.
- **Nothing lands when it should not.** An item waits in `landing` while it
  or its workspace is paused, while the workspace is `red` or `held`, while
  the policy no longer requires nightshift's three checks, and while origin
  and the hub disagree about the default branch. `item show` names the
  first of these.
- **The hold is on the hub too.** `nightshift-release` is reported `failed`
  as soon as a condition stops holding, so that a land asked by other means
  than nightshift is refused by the policy.
- **The policy says where a person is needed.** A land the hub answers as
  pending on an approval becomes an `approval` decision; the person's
  approval is recorded with the approver token, never the worker token.
- **A conflict is the implementer's to resolve, twice at most.** After two
  conflicts in a row the person decides.
- **A policy that lost the three checks stops the workspace and asks.** The
  workspace is `held`, the person is told, and a `policy` decision offers
  to adopt the checks or to read the policy again.
- **nightshift never overrides the policy.** No land request carries the
  hub's `override`.

## Non-goals

This package is the seventh of ten written from
`docs/prd/02-drive-work-items-through-the-hub.md`. What belongs to the
others:

- **`01_daemon_api_and_credentials`**: the store, the hub client with its
  two credentials and `Available`, the fake hub, the API server and the
  event log. This package adds requests to the hub client and a migration
  to the store.
- **`02_workspaces_and_policy`**: the workspace's state `green`, `red`,
  `held` and its setter, the policy functions (`Missing`, `Adopt`, `Read`,
  `Write`), the hub client's `Sync`. This package decides when a workspace
  is `held` by its policy and when it is `green` again.
- **`03_work_items_and_inbox`**: items and their transitions, threads, the
  decision machinery, `nightshift decide`, `item show` and the
  notification command. This package declares three decision kinds,
  registers markers and calls `Notify` for `workspace_held`.
- **`04_sessions_and_scheduling`**: the scheduler that calls this
  package's step for an item in `landing`, pausing, and the
  `session_failed` decision, which this package opens for a land the hub
  keeps failing.
- **`05_planning`**: the item's task fork and its change on the hub. This
  package reads which change an item has.
- **`06_implementation_and_review`**: the implementer run and the rework,
  the gate with `nightshift-gate`, the review with `nightshift-review`,
  the move of an item to `landing`, the function that reports a check,
  the `blocked` decision with its options, and the restoring of a fork
  that expired. This package starts reworks through it, reports
  `nightshift-release` through its function and has it open its `blocked`
  decision when a land is answered as blocked.
- **`08_health_and_publishing`**: the health command after a landing, the
  state `red`, the repair and the reverts, the `approval` decision of a
  reverting change and its place before every other decision, the
  `main_red` and `origin` decisions, the push to origin, and what follows
  a sync that reports `fast_forward` or `diverged` or that fails. This
  package runs the sync before a landing, lets `up_to_date`, `ahead` and
  `fast_forward` pass, and offers the call points that package builds on
  (see *What this package offers the later ones*).
- **`09_notes_and_take_over`**: `nightshift item note`, `nightshift item
  take-over` and the option `take_over`, which it adds to the `conflict`
  decision this package declares with `retry` and `abandon`. This package
  offers the condition and the withdrawal of the release check it needs.
- **`10_rules_brief_and_forge`**: acting on the `record` member of an
  answer, the brief and the forge write-back.

Also out: landing a stack, automatic landing by the hub (`auto_land`),
and any change to how the hub lands. nightshift adds no command and no
route in this package.

## Background

Parley lands optimistically: a land replays a change onto the head of its
target branch and moves the branch with a compare-and-swap. Every request
to land is a **land attempt** that ends `landed`, `conflict`, `blocked` or
`failed`. Policy is judged at the version being landed, at the moment of
the land: a change whose landing is not `ready` ends the attempt `blocked`,
answered `409 policy_blocked`, with `blocked.status` `pending` (approvals
or checks are missing) or `blocked` (a rule blocks, or a structural cause)
and the list of `requirements`, each with its `rule`, the `paths` that
selected it, its `approvals` (`required`, `counted`), its `checks` and its
`block`. Approvals and check results attach to one version. An approval
counts only from a credential that created no version of the change. A
land moves only the hub's branch.

The owner of a workspace may land over a `pending` landing with an
`override` in the land request. nightshift's worker token is the owner's,
so the hub would accept it; this package never sends it.

Packages 01 to 06 are written and are drafts. Packages 05 and 06 were
written at the same time as this one, and this package was reconciled with
them once they validated. What it takes from them is listed under *Design
Decisions*.

## Requirements

### When an item lands

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
  `up_to_date`, `ahead` or `fast_forward`.

In a `red` or `held` workspace nothing lands; sessions go on. While the hub
is unreachable, or does not accept the worker token, nothing is landed.

`item show` and `item list` name the condition as a marker on the item:
`paused` (package 04's marker), `workspace red`, `workspace held` or
`origin sync`.

The conditions are evaluated in the order above each time the scheduler
calls the step of an item in `landing`, and the evaluation stops at the
first that does not hold. The first two are read from nightshift's own
store; the policy and the sync ask the hub. Of the items of one workspace,
one at a time is between its sync and the hub's answer to its land.

**The policy decision.** The `policy` decision is opened in the workspace's
thread, once: while one is open for a workspace, no second one is opened.
When the workspace turns `held`, its thread gets an entry and the
notification command is run with the event `workspace_held`. `adopt`
writes the policy with the three checks and `fresh_checks` added to
`default` and to every rule that does not block, as registration with
`--adopt-policy` does. `recheck` reads the policy again. After either, a
policy that requires the checks makes a `held` workspace `green`; one that
still lacks them opens a new `policy` decision with the rules it lacks now.

**The sync.** nightshift asks the hub to sync the workspace from origin
before every landing and reads the result of the default branch.
`up_to_date`, `ahead` and `fast_forward` pass. What a `fast_forward`
starts (a health command on the new tip), what `diverged` starts and what
a failed sync starts are package 08's: it registers one function, which is
called with the result of every sync before a landing, a failed one
included, and the landing goes on only when that function returns no
error. Without such a function every result but the three named, a default
branch the sync does not report, and a sync the hub answers with an error
do not pass. An item whose sync did not pass waits with `origin sync`, and
the workspace is not synced again for 1 minute.

### The release check and the land

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
  *Implementation* (package 06).
- **conflict**: the implementer is started with the hub's conflict report
  and resolves it in the fork; the new version is gated and reviewed
  again. After 2 conflicts in a row on one item, it waits on a decision of
  kind `conflict`, with the report; its options are `retry`, `take_over`
  (package 09) and `abandon`.
- **the version is no longer the current one**: the new version is gated
  and reviewed, as any version.
- **failed** for another reason: the land is asked again after 1 minute,
  at most 5 times, then the item waits on a decision of kind
  `session_failed` carrying the hub's reason.

nightshift never lands with the hub's override of a policy requirement.

Each land attempt is an entry of kind `land` in the item's thread, with the
version and the hub's answer.

A decision of a paused item can still be answered; the answer takes effect
at the resume.

### What this package offers the later ones

By these names, in `internal/land`:

- **`Service.LandChange(ctx, Request{Workspace, Change, Version,
  SessionID}) (Outcome, error)`**: one land request for one version of one
  change, sent at once: no condition is checked, no check is reported, no
  other landing of the workspace is waited for, and it never carries an
  override. `Outcome.Kind` is `landed`, `pending_approval`, `pending`,
  `blocked`, `conflict`, `version_mismatch` or `failed`, with the landed
  commit, the hub's requirements, the conflict report or the reason.
  Package 08 lands a reverting change with it, ahead of everything else
  and although the workspace is `red`, after reporting its own checks.
- **`Service.SetRepair(ctx, item, on)`**: marks an item as a repair. A
  marked item lands although its workspace is `red`, through the same step
  as any item. Nothing else is needed for "ahead of everything else":
  while the workspace is `red` no unmarked item lands, and the order of
  sessions is package 04's `sched.ClassRepair`.
- **`Service.OnSyncResult(func(ctx, SyncResult) error)`**: the function
  called with the default branch's result of every sync before a landing
  (`SyncResult{Workspace, Branch, Result, LocalSHA, OriginSHA, Err}`). The
  landing goes on when it returns nil and waits when it returns an error.
  **`Service.SyncOrigin(ctx, slug) (SyncResult, error)`** runs the sync
  and returns the default branch's result without calling that function.
- **`Service.Conditions(ctx, item) (Condition, error)`**: the first
  condition that does not hold for an item, or none; and
  **`Service.Waiting(item) string`**, the same from the store alone.
- **`Service.AddCondition(name, func(ctx, item) bool)`**: a further
  condition, evaluated after `paused` (package 09 registers `taken over`).
- **`Service.Withdraw(ctx, item, summary) error`**: waits until no land of
  the item is being answered, then reports `nightshift-release` as
  `failed` with the summary; `land.ErrLanded` when the change has landed
  (package 09 calls it for a note).
- **`Service.OpenApproval(ctx, ApprovalRequest{Item, Workspace, Change,
  Version, Requirements, Owner})`** and
  **`Service.OnApprovalAnswered(owner, fn)`**: an `approval` decision for
  a change that is not the item's own. The kind's handlers are this
  package's, so a package that asks the person to approve another change
  (package 08, a reverting change) opens the decision here and is called
  back with the answer once the verdict is recorded with the approver
  token; this package lands nothing for such a decision. Listing it before
  every other decision is that package's.
- **`Service.Landing(ctx, item) (Record, error)`**: what this package
  recorded for an item: the version released, the landed commit once
  landed, the counts.
- **The decision kinds `approval`** (`approve`, `reject`, `abandon`),
  **`conflict`** (`retry`, `abandon`) **and `policy`** (`adopt`,
  `recheck`).

### End-to-end flows

**A reviewed change lands.** Item 14 is `landing`: the gate and the review
passed on version 2 of its change. nightshift finds the item and the
workspace not paused, the workspace `green` and the policy requiring the
three checks; the sync finds origin unchanged; `nightshift-release` is
reported `passed` on version 2 and the hub lands version 2. The item is
`verifying` and its thread shows the check and the land.

**A change that touches a sensitive path.** The person ran `nightshift
workspace protect demo "deploy/**"`. An implementer's version touches
`deploy/config.yaml` and `api/greet.go`. The gate and the review pass; the
hub answers the land as pending on an approval under the rule; an
`approval` decision lists `deploy/config.yaml`. The person answers
`approve`; nightshift records the approval with the approver token, the hub
lands the change, and the item goes on to `verifying`.

**The policy lost the three checks.** Someone replaced the workspace's
policy on the hub with one whose `default` requires nothing. Item 15 is
ready to land; nightshift reads the policy, makes the workspace `held`,
opens a `policy` decision naming `default` and runs the notification
command. `item show 15` marks the item `workspace held`. The person
answers `adopt`; nightshift writes the policy, the workspace is `green`
and item 15 lands.

## Design Decisions

From the source PRD, by its numbers:

- **2. Sensitive paths are the hub's policy rules, and everything else
  lands without the person.** The owner decided it.
- **8. Two personal access tokens, a worker and an approver.** The owner
  decided it. The approval of an `approval` decision is recorded with the
  approver token, which created no version.
- **12. nightshift syncs from origin before each landing; only a
  divergence or a refused push reaches the person.** The owner decided it.
- **18. Items never wait for each other on files.** The owner decided it:
  the hub lands optimistically, and a conflict is resolved by the
  implementer.
- **22. A third check, `nightshift-release`, reported at the moment of the
  land.** Chosen there: it is what makes a pause, a `red` or a `held`
  workspace hold on the hub too.
- **23. The policy must count the checks on the version itself
  (`fresh_checks`).** Chosen there.
- **27. A blocked path stops the item at the push; an approval is asked
  after the gate and the review.** Chosen there. A land answered as
  blocked by a rule is the same decision, opened here for a policy that
  changed after the push.

Chosen here:

1. **The step of `landing` reads the change from the hub before it asks
   the hub anything else, and after the conditions that need no hub.**
   Why: the hub, not nightshift's memory, says which version is current
   and what passed on it, and an item that waits in a `red` workspace
   should cost no request. A change the hub reports as `landed` moves the
   item to `verifying` with no request to land. nightshift records that a
   land request is sent; a step that finds such a record without an answer
   (the daemon stopped, or the item was paused, while the hub answered)
   reads the change before any condition; a current version on which `nightshift-gate` or
   `nightshift-review` was not reported `passed` is handled as "the
   version is no longer the current one": the item moves to
   `implementing`, where package 06's step gates and reviews the version
   it finds (its decision 2), with no session before it.
2. **`item show` names the unmet condition as a marker** (`workspace red`,
   `workspace held`, `origin sync`; `paused` is package 04's). Why:
   package 03's item object has markers and no other member for a text,
   and the source PRD's summaries use these names.
3. **A workspace that turns `held` or `green` here gets an entry of kind
   `workspace_state` in its thread** (`green -> held: policy does not
   require nightshift's checks in: default, docs`). Why: package 02's
   setter writes none (its decision 24).
4. **A `held` workspace is made `green` only when its state is `held`,
   never when it is `red`.** Why: `red` is package 08's. Where package 08
   holds the workspace for origin at the same time, the conditions are
   evaluated again before the next landing and hold it again.
5. **A `policy` decision is carried out at once, also in a paused
   workspace.** Why: it is no item's decision, and it changes only the
   policy and the workspace's state.
6. **A policy that was replaced between the read and the adopting write
   fails the answer's handler**, which package 03 calls again after 30
   seconds and notes in the thread. Why: the next call reads the policy as
   it then is.
7. **One landing at a time per workspace, from the sync to the hub's
   answer.** Why: the sync belongs to one landing, and the hub's own
   optimistic landing still serves changes landed by other means.
8. **A sync that does not pass is not repeated for 1 minute** (a
   parameter). Why: the scheduler calls a waiting step every second, and
   the source PRD's own retry of a failed sync is 1 minute.
9. **A hub error while the change or the policy is read makes the item
   wait 1 minute**, with nothing reported. Why: the same bound.
10. **A land answered `202` (still running), or `409 land_in_progress`, is
    followed by reading the attempt every 2 seconds until it is final, for
    at most 10 minutes**, then handled as failed for another reason. Why:
    the hub ends an attempt after 5 minutes by itself.
11. **"Failed for another reason" is every answer the source PRD does not
    name**: `503 land_failed`, a `blocked` answer with no blocking rule (a
    structural cause), a `pending` answer in which no requirement lacks an
    approval (a check of the person's own that has not passed), a
    `pending` answer for a version on which nightshift recorded the
    person's approval already (the approval did not count, or more than
    one is required), and any other refusal. Why: each needs a person or
    time, and the source PRD gives one road for that.
12. **The `session_failed` decision is opened with package 04's
    `OpenSessionFailed` for the step `land`**, with the reason of each
    failed attempt; this package's count of failed lands starts again when
    it is opened, so that `retry` gives 1 attempt and 5 more.
13. **"2 conflicts in a row" counts the lands of an item answered
    `land_conflict` since the item was created or its `conflict` decision
    was last answered `retry`.** Answers that replayed nothing (a pending
    approval, a failure) do not break the row. `retry` sets the count to
    zero and starts the implementer with the report.
14. **The conflict report reaches the implementer as one finding**: the
    target head, the first commit that did not apply, each conflicted file
    with its kind, and each landed change that touched it with its number
    and title.
15. **`reject` records the verdict `reject` on the hub with the approver
    token and the comment, on the decision's version; the comment reaches
    the implementer as a binding finding of the source `person`.** Why: the source PRD
    says a comment of the person must be fixed, not answered.
16. **`approve` records the approval on the version the decision was
    opened for**, not on the current one. Why: the person approved what
    they were shown; a version pushed meanwhile is gated and reviewed
    first.
17. **Answers to `approval` and `conflict` decisions of a paused item are
    kept and carried out at the resume, in the order given.** Their
    handlers are registered through package 06's `(*impl.Service).OnAnswer`,
    which does this; this package keeps no answers of its own.
18. **Recommended options**: `approve`, `retry`, `adopt`. Why: package
    03 requires one; each is the option that lets reviewed work go on.
19. **The land request carries the `session_id` of the change's current
    version, and is sent once more without it when the hub answers `400
    unknown_session`.** Why: the source PRD has an item's lands name its
    session; a session the hub no longer links must not strand the item.
20. **The conditions of items whose release check passed are evaluated
    every 5 seconds (a parameter), and at once when an item is paused.**
    Why: "whenever a condition stops holding" needs a bound, and package
    02's state setter has no hook. A marked repair in a `red` workspace is
    not failed for `workspace red`.
21. **No configuration key.** The intervals (1 minute, 5 retries, 2
    seconds, 10 minutes, 5 seconds) are parameters of the service with
    these defaults, as package 01's conventions ask.

22. **The function registered with `OnSyncResult` is called with every
    result, and decides.** Why: package 08 owns what a `fast_forward`, a
    `diverged` and a failed sync start, and one function that answers "go
    on or wait" needs no list of results agreed between two packages.
23. **An `approval` decision can be opened for a change that is not the
    item's own (`OpenApproval` with an owner), and its answer is handed to
    the owner after the verdict is recorded.** Why: package 03's registry
    has one handler for a kind and an option, and the kind is this
    package's; package 08's reverting change needs the same decision.
24. **Before it uses an item's change, the step and the handlers of
    `approve` and `reject` call package 06's `RestoreFork`.** Why: a fork
    may expire while an `approval` decision waits (decision 15 of the
    source PRD); the restored change is another change, on which the
    person's verdict cannot be recorded, so `approve` then records
    nothing and `reject` still sends the comment to the implementer.

Taken from packages 05 and 06, read as a coder receives them on
2026-11-28. This package takes each as an interface of `internal/land`,
so that its tests stub them; `daemon.Run` passes the real services:

25. **`internal/change` (05)**: `(*change.Service).Get(ctx, item)
    (change.Record, error)`, a `Record` with `Workspace` and `Change`, the
    change's number.
26. **`internal/impl` (06)**: `(*impl.Service).Rework(ctx, item, findings
    []impl.Finding) error`, which moves an item that is `landing` to
    `implementing`; `ReportCheck(ctx, item, name, status, summary)`, which
    reports on the current version of the item's change and writes the
    thread entry; `OpenBlocked(ctx, item, reqs []impl.Requirement)`;
    `RestoreFork(ctx, item)`; `OnAnswer(kind, option, fn)`. Its step of
    `implementing` gates a current version that has no verdict, and it
    moves an item from `reviewing` to `landing` when `nightshift-review`
    passed. The fields of `impl.Requirement` are not given there.
27. **The release check is reported through `ReportCheck`, on the current
    version.** Why: the step reads the version just before, and the land
    request names that version as `expected_version`, so a version pushed
    in between is answered `version_mismatch` and lands nothing.
28. **This package adds to package 06's `(*hub.Client).Change` the members
    it does not read: `checks` (with `include=checks`), `session_id` and
    `landed`; and to the hub client `Land`, `LandAttempt` and `Verdict`.**

## Dependencies

- **`01_daemon_api_and_credentials`** (status `draft`): the store and its
  migrations, the hub client with the `worker` and `approver` credentials
  and `Available`, the fake hub `internal/hubtest`, the e2e helper.
- **`02_workspaces_and_policy`** (status `draft`): `workspace.Service`
  (`Get`, `SetState`), `internal/policy` (`Read`, `Write`, `Missing`,
  `Adopt`, `ErrRevisionMismatch`), the hub client's `Sync` and
  `Workspace`.
- **`03_work_items_and_inbox`** (status `draft`): `internal/item`
  (`Transition`, `Abandon`, `RegisterMarker`), `internal/inbox`
  (`RegisterKind`, `OnAnswer`, `Open`, `Append`, `WorkspaceThread`,
  `OpenDecisions`), `internal/notify` (`Notify`).
- **`04_sessions_and_scheduling`** (status `draft`): `internal/sched`
  (`Scheduler.RegisterStep`, `Paused`, `OnPause`, `OpenSessionFailed`).
- **`05_planning`** (status `draft`): `internal/change`, the item's
  change (`Get`).
- **`06_implementation_and_review`** (status `draft`): `internal/impl`
  (`Rework`, `ReportCheck`, `OpenBlocked`, `RestoreFork`, `OnAnswer`) and
  the hub client's `Change`.
- **Parley** (`agent-fox-dev/parley`), the hub: its packages
  `12_workspace_sync_from_origin`, `22_change_opening_and_versions`,
  `25_optimistic_landing`, `26_conflict_reports_and_resolution`,
  `27_landing_policy` and `28_approvals_and_checks`, each with every task
  `done` on 2026-11-27.

## Verified External API

Parley's REST API, read in `docs/api.md` of `agent-fox-dev/parley` at
commit `7eabfb9`:

- `POST /api/v1/workspaces/{slug}/changes/{number}/land`, scope
  `changes:write`: body `expected_version` (an integer of at least 1),
  `stack`, `override` (`{"reason"}`, never sent by nightshift) and
  `session_id`; query `wait` (0 to 60 seconds, 30 when absent). `200` with
  the attempt (`landed`); `202` with the attempt still `running`; `409
  land_conflict`, `409 policy_blocked` and `503 land_failed`, each with
  the attempt in `error.details`; `409 version_mismatch`; `409
  land_in_progress` with the running attempt in `error.details`; `409
  depends_on_unlanded`, `change_not_open`, `workspace_busy`,
  `workspace_not_ready`, `workspace_archived`; `403 forbidden`,
  `token_bound`, `path_not_allowed`, `override_not_allowed`; `400
  unknown_session`; `404 change_not_found`.
- The land attempt: `id`, `status` (`running`, `landed`, `conflict`,
  `blocked`, `failed`), `landed_sha`, `noop`, `conflict` (the conflict
  report), `blocked` (`change`, `status` `pending` or `blocked`,
  `requirements`), `error` (`contention`, `timeout`, `origin_rejected`,
  `internal`), `override`. `GET /api/v1/workspaces/{slug}/lands/{id}`
  answers one attempt, scope `changes:read`.
- The conflict report: `target_sha`, `head_sha`, `base_sha`, `commit_sha`,
  `files` (each `path`, `kind`, `base`, `ours`, `theirs`, `landed_by`),
  `landed_changes` (each `number`, `title`, `landed_sha`, `session_id`),
  `change`.
- A requirement: `rule`, `paths` (the first 50), `approvals` (`required`,
  `counted`, `approvers`), `checks` (each `name` and `status` `passed`,
  `failed` or `pending`), `block`, `satisfied`.
- `GET /api/v1/workspaces/{slug}/changes/{number}` with
  `include=checks`: the change object with `state` (`draft`, `open`,
  `landed`, `abandoned`), `version`, `session_id`, `landing` (`status`,
  `reason`, `requirements`), `landed` (`sha`, …) and `checks`, the results
  that apply to the current version, each with the `version` it was
  reported on.
- `POST /api/v1/workspaces/{slug}/changes/{number}/approvals`, scope
  `changes:approve`: body `verdict` (`approve` or `reject`), `comment` (at
  most 16,000 characters), `version`; `201` for the first verdict of a
  credential on a version, `200` for a replacement, with `counts_for`;
  `409 change_closed`; a verdict counts only from a credential that
  created no version at or below its own.
- `PUT /api/v1/workspaces/{slug}/changes/{number}/checks/{name}`, scope
  `checks:write`: body `status` (`pending`, `passed`, `failed`),
  `version`, `summary` (at most 2,000 characters); one result per version
  and name.
- `POST /api/v1/workspaces/{slug}/sync`: `200` with `branches`, each
  `name`, `result` (`created`, `up_to_date`, `fast_forward`, `ahead`,
  `diverged`, `reset`, `name_conflict`), `local_sha`, `origin_sha`; `400
  sync_disabled`, `502 upstream_error`, `500 internal`.
- `GET /api/v1/workspaces/{slug}/policy`: the document in normal form with
  `revision`; a requirement has `approvals`, `approvers`, `checks`,
  `fresh_checks` and `block`.
- The workspace setting `land.allow_override` (default `true`) lets the
  owner send `override`; nightshift does not read it.

Read in packages 05 and 06 with `specr`: the functions of decisions 25
and 26. Not verified: the fields of `impl.Requirement`.

## Glossary

- **landing condition**: one of the four things that must hold before
  nightshift asks the hub to land an item's change.
- **release check**: the check `nightshift-release`, which nightshift
  reports `passed` on a version at the moment it asks for its land and
  `failed` when a condition stops holding.
- **land attempt**: the hub's record of one request to land, with its
  final status.
- **pending on an approval**: a land the hub refused because a requirement
  of the policy has fewer counted approvals than it requires.
- **conflict report**: the hub's account of a commit that did not apply on
  the target head, with the files and the landed changes involved.
- **held**: the state of a workspace while a `policy` decision (this
  package) or an `origin` decision (package 08) is open.
- **repair**: an item package 08 marks so that it lands although its
  workspace is `red`.
- **override**: the member of the hub's land request with which a
  workspace's owner lands over a pending requirement. Never sent.
- **the three checks, adopt**: as package 02 defines them.
- **marker**: as package 03 defines it: a name `item show` and `item list`
  print for an item.
- **step**: as package 04 defines it: the function the scheduler calls for
  an item in a state.
- **hub, worker token, approver token**: as package 01 defines them.
- **change, version, check, policy, rule, requirement, sync**: as Parley
  defines them (`docs/api.md`).
