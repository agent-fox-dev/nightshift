---
spec_id: "08"
spec_name: "health_and_publishing"
title: "Verify every landing and publish it to origin"
status: "active"
created_at: "2026-11-28T10:22:02Z"
intent_hash: "bc00101c357c4300fb0585f1f83f0acabfb597069061c5218dcd62fc60f830a1"
contract_hash:
  requirements.json: "0593aed3ddb9f38869b91ecc9f536e7d69855ba4c61e26ee5c33493f1ceab99f"
  tasks.json: "31e48842473e4f054dcb14af00d4f1d5c03125e00ed24a0ef3c61497ff857753"
schema_version: 3
source: "docs/prd/02-drive-work-items-through-the-hub.md"
---

# Verify every landing and publish it to origin

## Intent

A change that landed on the hub is not finished: it may have broken the
main branch, and origin does not have it yet. nightshift runs the
workspace's health command on every landed commit and on every commit
origin brings in, repairs a broken main branch once and reverts the landing
when the repair does not help, and pushes the default branch to origin only
after the health command passed. A work item is `done` only then, and the
person is asked only where nightshift cannot go on by itself: a main branch
it could not make healthy, and an origin that refuses or has diverged.

## Goals

- **A landing that breaks the main branch is dealt with.** After every
  landing the health command runs on the landed commit. A failure makes
  the workspace `red`, starts one repair, and, when that does not make the
  branch healthy, reverts the landing.
- **Only verified work reaches origin.** The default branch is pushed to
  origin after the health command passed, and an item is `done` only when
  origin has the commit.
- **What origin brings in is verified too.** A sync that fast-forwards the
  default branch runs the health command on the new tip before anything
  lands.
- **Only what nightshift cannot settle reaches the person.** A main branch
  that stays unhealthy is a `main_red` decision; a refused push, a
  diverged origin and a sync that keeps failing are an `origin` decision.
  Each has one option, and the person is told when the workspace turns
  `red` or `held`.
- **It survives a restart.** A health run, a repair, a revert and a push
  that a stop interrupted are continued at the next start from what was
  stored, without a second repair and without a second revert.

## Non-goals

This package is the eighth of ten written from
`docs/prd/02-drive-work-items-through-the-hub.md`. Of this package's
neighbourhood, these belong to the others:

- **`01_daemon_api_and_credentials`**: the daemon, the store, the hub
  client and its monitor, the fake hub. This package adds the revert and
  the push to the hub client and to the fake hub.
- **`02_workspaces_and_policy`**: the workspace's state and its setter,
  the health command's settings, `RunHealth`, the health command at
  registration. This package is what sets `red`, and `held` by origin.
- **`03_work_items_and_inbox`**: items, their states and transitions,
  threads, the decision machinery and its routes, reminders, the
  notification command. This package declares the kinds `main_red` and
  `origin`, writes entries and calls `Notify`.
- **`04_sessions_and_scheduling`**: sessions, the scheduler, the repair
  class in the start order and its exemption from the daily limit,
  pausing. This package registers the steps of `verifying` and
  `publishing`.
- **`05_planning`**: the `plan` decision and what it shows, the item's
  fork and change. This package calls `Reopen` with the failure's output.
- **`06_implementation_and_review`**: the implementer run, the gate and the
  review of every item, a repair item among them, the checks
  `nightshift-gate` and `nightshift-review`, extending a fork's lifetime.
  This package creates the repair item and reads how it fares.
- **`07_landing`**: the landing conditions, that nothing else lands in a
  `red` or `held` workspace, `nightshift-release`, the land request and
  its outcomes, the `approval` decision and its answers, `held` by policy,
  the landing of a repair item, and running the sync before a landing.
  This package marks the repair item, asks for the land of a reverting
  change, and is handed every sync's result.
- **`09_notes_and_take_over`**: notes and taking over; both are refused
  for an item that is `verifying` or `publishing`.
- **`10_rules_brief_and_forge`**: the brief's list of reverted items, the
  outcome numbers, the issue comment when an item is `done` or reverted
  (it reads the item's state changes and this package's `revert` entry),
  and rule changes, which are verified and published by this package as
  any other landing.

Also out: resetting the hub's branch to origin, a forced push, and any
repair of a commit origin brought in.

## Background

Packages 01 to 07 leave an item in `verifying` when the hub landed its
change: the change's `landed.sha` is on the workspace's default branch on
the hub, and nowhere else. Nothing has run on that commit, and origin does
not have it.

What this package uses of Parley, as its `docs/api.md` states at commit
`7eabfb9`:

- A **land** moves only the hub's branch. Origin gets it through `POST
  …/branches/{name}/push`, which pushes the branch's head, whatever it is:
  the route takes no commit.
- A **revert** (`POST …/changes/{number}/revert`) builds, on the head of
  the target branch, one commit per landed commit of the original, each
  undoing it, and opens an ordinary change from the branch
  `revert/<number>`. Policy judges that change like any other; it lands
  through `POST …/land`. The caller of the revert is the creator of its
  version, so an approval of it counts only from another credential: the
  approver token.
- A **sync** (`POST …/sync`) answers one entry per branch of origin with
  `result`, `local_sha` (the hub's head after the sync) and `origin_sha`.
- A check result is reported per version with `PUT
  …/changes/{number}/checks/{name}`; a report replaces the earlier one
  whole.

## Requirements

Constraint: Linux and macOS; Windows is best effort. Constraint: this
package adds no command and no route; what it does is read through the
inbox and the item commands of package 03.

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

Repair sessions start even when the workspace's daily limit is reached,
and are counted.

The thread entries of this package:

| Kind | Text | Unread |
|---|---|---|
| `health` | `health command passed on <commit>` | no |
| `health_failed` | `health command exited <N> on <commit>` or `health command timed out on <commit>` | no |
| `repair` | `workspace <slug> is red; repair started for change <n>` | yes |
| `revert` | `repair did not help (<reason>); reverting change <n>` with ` and change <m>` for a landed repair | yes |
| `workspace_green` | `workspace <slug> is green again` | no |
| `push` | `push of <branch> to origin: <result or error type>` | no |
| `published` | `change <n> is on origin as <commit>; <N> tokens spent` | yes |

The step *Still unhealthy* is marked by the entry the `main_red` decision
writes when it opens.

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

**Telling the person.** When a workspace turns `red` or `held`, nightshift
calls the notification command of package 03 with the event
`workspace_red` or `workspace_held`, the workspace, the item where there is
one, and the thread's URL path.

### What this package uses of packages 05, 06 and 07

Read in those packages as drafts on 2026-11-28, under their names:

| What | Whose | Used for |
|---|---|---|
| `change.Service.Get(ctx, item)`, a `Record` with `Workspace` and `Change` | package 05, `internal/change` | the number of an item's change |
| `plan.Service.Reopen(ctx, item, reason, extra)` | package 05, `internal/plan` | moves a reverted item to `awaiting_approval` and has its `plan` decision opened with the failure's output; `plan.ErrNotFound` for an imported package |
| `(*impl.Service).Rework(ctx, item, findings)` and `SetClass(ctx, item, class)` | package 06, `internal/impl` | the repair item's finding (source `health`) and its repair class |
| `(*hub.Client).Change` and `ReportCheck` | package 06, `internal/hub` | a change's `landed.sha`; the three checks on a reverting change |
| `land.Service.SetRepair(ctx, item, on)` | package 07, `internal/land` | the repair item lands although the workspace is `red` |
| `land.Service.LandChange(ctx, Request{Workspace, Change, Version, SessionID})` | package 07 | one land request for a reverting change; `Outcome.Kind` `landed`, `pending_approval`, `pending`, `blocked`, `conflict`, `version_mismatch` or `failed` |
| `land.Service.OpenApproval(ctx, ApprovalRequest{…, Owner})` and `OnApprovalAnswered(owner, fn)` | package 07 | the `approval` decision of a reverting change, owner `revert` |
| `land.Service.OnSyncResult(fn)` and `SyncOrigin(ctx, slug)` | package 07 | the result of every sync before a landing; this package's own syncs |

**New, added by this package to the Go packages of others** (nothing of
the kind exists there):

- **`(*impl.Service).UsePackageOf(ctx, item, of)`** in `internal/impl`
  (package 06): the item is implemented, gated and reviewed with the
  package of the item `of`. Package 06 resolves an item's package from its
  imported source or from package 05's kept text; a repair item has
  neither.
- **`Urgent` on `inbox.OpenRequest`, `inbox.SetUrgent(ctx, decision,
  urgent)`, the member `urgent` of a decision and the order of `GET
  /api/v1/decisions`** in `internal/inbox` (package 03).

**How a repair runs.** A repair is a work item: a linked item of the kind
`defect`, created from the item whose landing failed, given that item's
package, the repair class and the repair mark, handed the health command's
output as a finding, and moved to `ready`. Packages 06 and 07 then
implement it as a rework in a fork of its own, gate, review and land it as
any item. When it reaches `verifying`, this package runs the health
command on its landed commit. A repair ends without having helped when the
repair item waits on any decision but an `approval`, or is abandoned.

### End-to-end flows

**A healthy landing is published.** Item 12's change landed. `nightshiftd`
clones the workspace at the landed commit and runs the health command,
which exits 0; the item is `publishing`. It asks the hub to push the
default branch; the hub answers `pushed`; the item is `done` and its thread
is in `Unread` with the change, the commit and the tokens spent.

**A landing that breaks the main branch.** Item 12 lands; the health
command exits 1. The workspace is `red`, nothing is pushed to origin, and
item 13, ready to land, waits. The repair lands and the health command
fails again. nightshift reverts both changes, the health command exits 0,
the repair item is abandoned,
the workspace is `green`, item 13 lands and is published, and item 12 is
back in `Action` with a `plan` decision carrying the failure's output.
Origin never held item 12's commit as the head of its branch.

**Origin refuses, and the person retries.** Item 14 is `publishing`; the
hub answers the push `422 origin_rejected` with origin's reason. The
workspace is `held`, the notification command receives `workspace_held`,
and an `origin` decision with the reason is in `Action`. The person fixes
the branch rule on the forge and answers `retry`. nightshift syncs, pushes,
the hub answers `pushed`, the workspace is `green` and item 14 is `done`.

## Design Decisions

From the source PRD, by its numbers:

- **11. A landed commit is pushed to origin after the health command
  passed, and `done` means it is on origin.** The owner decided it.
- **12. nightshift syncs from origin before each landing; only a
  divergence or a refused push reaches the person.** The owner decided it.
- **13. On a revert nightshift reports its own checks as passed and asks
  the person only where policy requires an approval.** The owner decided
  it.
- **21. Fix forward once and then revert; one health command per
  workspace.** Kept from PRD 01.
- **26. Repairs start although the daily limit is reached.** Chosen there.
- **28. A diverged origin has the one option `retry`.** Chosen there.
- **41. The health command runs as the session user.** Chosen there;
  `RunHealth` of package 02 with the identity of package 04 does it.

Chosen here:

1. **Two Go packages: `internal/health` (the health command after a
   landing, `red`, repair, revert, `main_red`) and `internal/publish` (the
   push, the sync's results, `origin`, `held`).** Why: the ownership the
   ten packages agreed on.
2. **A repair is a work item of its own: a linked `defect` item of the
   item whose landing failed, with that item's package.** Why: packages 06
   and 07 implement, gate, review and land items and nothing else
   (`Rework`, `SetClass` and `SetRepair` take an item), and a pause of the
   workspace then holds the repair as the source PRD asks. The item whose
   landing failed stays `verifying`; "both changes go on to `publishing`"
   is both items moving there. This replaces an earlier draft of this
   package, in which the repair belonged to the failed item.
3. **One incident is stored per failed health command, with its phase
   (`repairing`, `reverting`, `main_red`, `closed`), the output, the
   commits tried and the changes it opened.** Why: a step function is
   called again after a pause or a restart and must go on where it was.
4. **A health command that could not be run (the clone failed, the hub is
   unreachable, the command could not be started) decides nothing: the
   step ends and is called again.** Why: the source PRD makes a workspace
   `red` for an exit code or the timeout, and a failed fetch says nothing
   about the branch.
5. **While a workspace is `red` for another item, an item in `verifying`
   waits, and is then verified on the head of the default branch, not on
   its own landed commit.** Why: its own commit holds the broken change
   without the repair or the revert, and would fail for the other item's
   fault.
6. **A repair ends, and the landing is reverted, when the repair item
   waits on any decision but an `approval` (`session_failed`,
   `review_disagreement`, `session_limit`, `blocked`, `conflict`) or is
   abandoned; this package closes that decision and abandons the repair
   item.** Why: the source PRD names a session that failed twice and a
   review that ended in disagreement, and both reach this package as
   decisions packages 04 and 06 open by themselves; one more round for
   the others is not "one repair". The decision is open for at most one
   pass of the scheduler, and its notification is sent: accepted, since
   suppressing it would mean changing packages 04 and 06.
7. **The repair's change is reverted before the item's, and the item's
   revert is asked only when the repair's has landed.** Why: the hub
   builds a revert on the target's head, and the repair sits on top of the
   item's change.
8. **`409 already_reverted` is taken as the reverting change it names.**
   Why: a daemon stopped between the hub's answer and its own store asks
   again at the next start.
9. **"A revert cannot be built or landed" is every other refusal of the
   revert (`revert_conflict`, `nothing_to_land`, `target_not_found`, any
   other error answer) and a land of a reverting change that is refused,
   an `approval` answered `reject` or `abandon` among them. A land answered
   `failed` is not asked again.**
10. **The summary's `<commit>` is the item's landed commit, on both
    reverting changes; the three checks are reported on version 1 of the
    reverting change before its land is asked.**
11. **"Listed before every other decision" is a member `urgent` of a
    decision: `GET /api/v1/decisions` answers the urgent open decisions
    first, then the others, each group oldest first.** This package adds
    `Urgent` and `SetUrgent` to `internal/inbox`, and marks the decision
    package 07's `OpenApproval` returns. Why: package 03 orders decisions
    by their age alone, and package 07 leaves the listing to this package.
12. **A `main_red` decision answered `resume` runs the health command on
    the head of the default branch. Healthy: the workspace is `green`, and
    the item goes to `awaiting_approval` with the `plan` decision when its
    change's revert landed, else to `publishing`. Unhealthy: a new
    `main_red` decision opens with the new output.** Why: the source PRD
    says what `resume` runs and not what follows; the branch the person
    repaired either still holds the item's change or does not, and a `red`
    workspace with nothing in `Action` would wait for nobody.
13. **The `main_red` decision of an item is in the item's thread; the one
    after a `fast_forward` is in the workspace's thread. An `origin`
    decision for a refused push is in the thread of the item whose push it
    was; one for a sync's result is in the workspace's thread.** Why: an
    item with an open decision names it in `waiting_on`, and the source
    PRD puts what has no item to blame in the workspace's thread.
14. **A workspace has at most one open `origin` decision and one open
    `main_red` decision.** Why: one push serves every item, and a second
    question about the same branch says nothing new.
15. **The push is asked only while the workspace is `green`, no item of
    it is `verifying` and no health run on a fast-forwarded tip is
    pending.** Why: the hub pushes the branch's head, whatever it is, so
    a push while a later landing is unverified would publish unverified
    work.
16. **One push, and after a `non_fast_forward` one sync and one more push,
    per call of an item's step; an item still not on origin is tried again
    at the next call.** Why: a loop over an origin that keeps moving needs
    a bound.
17. **Refused: `origin_rejected`, `origin_unauthenticated`,
    `origin_forbidden`, `origin_not_found`, and every error answer of the
    push this document does not name. Tried again: `origin_unreachable`,
    `upstream_error` and `origin_timeout`.** A push the hub itself could
    not be asked for (the hub unreachable, the worker token not accepted)
    is neither and is not counted.
18. **"At most 5 times" is 5 further tries: the sixth failure in a row is
    handled as a refused push.** The count is kept per workspace in the
    store and starts again at any other answer.
19. **A sync that fails is tried again by this package, on a timer of its
    own; a failure package 07 hands over while such a try is pending is
    not counted.** Why: package 07's landing step is called every second,
    and the source PRD counts tries a minute apart.
20. **A sync that reports the default branch with another result (`reset`,
    `name_conflict`), or not at all, is a failed sync with the reason
    `sync reported <result>`; `created` is as `up_to_date`.**
21. **The fork of a `done` item is neither deleted nor extended.** Why:
    "left to expire".
22. **The tokens spent are the input, cache-creation and output tokens of
    every session of the item, a repair's among them.** Why: package 04's
    count, cache reads excluded.
23. **An answer to a decision of this package for an item that is paused
    takes effect at the resume.** Why: the source PRD's rule for every
    decision of a paused item; a decision in the workspace's thread is
    carried out at once.
24. **No event of its own.** The entries, decisions and state changes this
    package writes append `thread.updated`, `decision.*` and
    `item.updated` through package 03; the workspace's state setter
    appends none (package 02).
25. **The intervals (1 minute between tries, 5 tries) are parameters with
    these defaults, not configuration keys.** Why: the source PRD does not
    call them configured.
26. **A `retry` that does not succeed opens the `origin` decision again at
    once, without tries a minute apart.** Why: the person is there and has
    just said that origin is in order; five more minutes tell them less
    than the new reason does.
27. **The commit of the `published` entry is the commit the item's change
    landed as.** Why: after a repair the pushed head is another commit,
    and the entry is about the item's change.
28. **The repair item gets its package through a new function of
    package 06's `internal/impl`, `UsePackageOf`.** Why: building a fork,
    a change, a rework, a gate and a review a second time in this package
    would copy package 06; one function that points an item at another
    item's package lets its steps do all of it.
29. **After a revert the repair item, when it had landed, is moved to
    `awaiting_approval` and abandoned at once.** Why: `verifying` allows
    only `publishing` and `awaiting_approval`, and a reverted repair is
    neither published nor planned again.
30. **A reverted item whose package was imported gets no `plan` decision:
    it is moved to `awaiting_approval` with an unread `revert` entry that
    says so.** Why: package 05 keeps no text of an imported package and
    refuses `Reopen` for it; the person abandons the item and imports a
    corrected package. Reported as an open point.
31. **This package's own syncs go through package 07's `SyncOrigin`.**
    Why: one reader of the default branch's result.

## Dependencies

- **`docs/prd/02-drive-work-items-through-the-hub.md`**: the source PRD,
  of which this package specifies *After a landing* and *Publishing to
  origin*.
- **`01_daemon_api_and_credentials`** (status `draft`): the store and its
  migrations, the hub client with its typed error and `Available`, the
  fake hub `internal/hubtest`, the named shutdown steps and the e2e
  helper.
- **`02_workspaces_and_policy`** (status `draft`): `workspace.Service`
  with `Get`, `RunHealth` and `SetState`, the hub client's `Workspace` and
  `Sync`.
- **`03_work_items_and_inbox`** (status `draft`): `internal/item`
  (`Get`, `List`, `Transition`), `internal/inbox` (`RegisterKind`,
  `OnAnswer`, `Open`, `Close`, `Append`, `WorkspaceThread`, the decision list) and
  `CreateLinked` and `Abandon` of `internal/item`, and
  `internal/notify` (`Notify`).
- **`04_sessions_and_scheduling`** (status `draft`):
  `Scheduler.RegisterStep`, `Paused`, `OnResume`, `Wake`,
  `sched.ClassRepair`, and the session table.
- **`05_planning`** (status `draft`): `change.Service.Get` and
  `plan.Service.Reopen`.
- **`06_implementation_and_review`** (status `draft`): `Rework`,
  `SetClass`, its steps for the repair item, and the hub client's `Change`
  and `ReportCheck`; this package adds `UsePackageOf` to `internal/impl`.
- **`07_landing`** (status `draft`): `SetRepair`, `LandChange`,
  `OpenApproval`, `OnApprovalAnswered`, `OnSyncResult` and `SyncOrigin`.
- **Parley** (`agent-fox-dev/parley`), the hub: its packages
  `12_workspace_sync_from_origin`, `13_workspace_push_to_origin`,
  `25_optimistic_landing`, `28_approvals_and_checks` and
  `29_auto_land_revert_and_path_limits`, each with every task `done` on
  2026-11-27.

## Verified External API

Parley's REST API, read in `docs/api.md` of `agent-fox-dev/parley` at
commit `7eabfb9`:

- `POST /api/v1/workspaces/{slug}/changes/{number}/revert`, scope
  `changes:write`, the owner of `{slug}`: optional body `title`, `intent`,
  `draft`, `auto_land`, `session_id`; `201` with the new change (its
  `number`, `version`, `reverts` the original's number), opened from the
  branch `revert/<number>` into the original's target; `409
  change_not_landed`, `409 already_reverted` with
  `error.details.reverted_by`, `400 nothing_to_land`, `409
  revert_conflict` with `error.details.conflict`, `400 target_not_found`.
  A refusal leaves no branch and no change.
- `GET …/changes/{number}`: `state` (`draft`, `open`, `landed`,
  `abandoned`), `version`, `landed` (`null` until landed: `sha`, `at`,
  `by`, `land_id`, `commits`), `reverts`, `reverted_by`.
- `PUT …/changes/{number}/checks/{name}`, scope `checks:write`: `status`
  `pending|passed|failed`, `version`, `summary` of at most 2,000
  characters; `200` with the check; one result per version and name.
- `POST …/changes/{number}/land`, scope `changes:write`: optional
  `expected_version`; `200` with the attempt `landed` (`landed_sha`),
  `202` still `running`, `409 land_conflict`, `409 policy_blocked` (the
  attempt's `blocked.status` `pending` or `blocked`, with
  `requirements`), `409 version_mismatch`, `503 land_failed`.
- `POST …/branches/{name}/push`, scope `workspaces:write`: `200`
  `{branch, sha, remote, remote_url, result}` with `result` `pushed`,
  `forced` or `up_to_date`; `409 non_fast_forward`, `422
  origin_rejected`, `502 origin_unauthenticated`, `502 origin_forbidden`,
  `502 origin_not_found`, `502 origin_unreachable`, `502 upstream_error`,
  `504 origin_timeout`, `404 branch_not_found`, `400 fork_has_no_origin`.
  A push that fails changes nothing on origin.
- `POST …/sync`, scope `workspaces:write`: `200` with the workspace
  object plus `branches`, each `{name, result, local_sha, origin_sha}`,
  `result` one of `created`, `up_to_date`, `fast_forward`, `ahead`,
  `diverged`, `reset`, `name_conflict`; for `diverged` `local_sha` is the
  hub's head and `origin_sha` origin's; `400 sync_disabled`, `502
  upstream_error`, `500 internal`.
- The workspace object's `default_branch` and `head_sha`.

Packages 05, 06 and 07, read as drafts with `specr` on 2026-11-28: the
functions of *What this package uses of packages 05, 06 and 07*, with the
signatures given there. Not verified, because this package adds them:
`UsePackageOf` and `SetUrgent`.

## Glossary

- **health command**: the command a workspace configures that must exit 0
  on the default branch.
- **landed commit**: the commit a change landed as on the hub's default
  branch (`landed.sha` of the change).
- **incident**: one failed health command and everything nightshift did
  about it, until the workspace is `green` again.
- **repair item**: the linked work item that is given a failed health
  command's output and whose change is to make the branch healthy.
- **reverting change**: the change the hub opens to undo a landed one.
- **tip**: the head of the default branch after a sync moved it.
- **green, red, held**: the stored state of a workspace (package 02).
- **origin**: the repository on a forge that a Parley workspace was cloned
  from and is pushed to.
- **urgent**: a decision the inbox lists before the others.
- **hub, worker token, approver token**: as package 01 defines them.
- **step function, repair class**: as package 04 defines them.
- **change, version, check, land, sync**: as Parley defines them
  (`docs/api.md`).
