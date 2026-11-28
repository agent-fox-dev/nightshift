---
spec_id: "05"
spec_name: "planning"
title: "Plan a defect or a change request as a reviewed spec package"
status: "active"
created_at: "2026-11-28T10:21:39Z"
intent_hash: "fa40716a2e728b420b24135599ae515a0dd582d6c4b453effad1b880f32dcdf0"
contract_hash:
  requirements.json: "da82cf3932d7dbc26d72a7212b1d982aee2e7ec44ae1305d14c7f737c5ce8e00"
  tasks.json: "8738359755426bcdb661df77d7989aa3effce0ad5c0ed5dc8ea17456654fd1e2"
schema_version: 3
source: "docs/prd/02-drive-work-items-through-the-hub.md"
---

# Plan a defect or a change request as a reviewed spec package

## Intent

A defect or a change request is a few lines of text, and nobody should
implement from that. Before any code is written, nightshift turns the text
into a spec package: a planner session writes it in a task fork of the
workspace under a package number nightshift gives, a reviewer with a fresh
context reads it against the text, and the person approves, sends back or
abandons the plan in one decision that shows the package, its draft change
on the hub and the sensitive paths it is likely to touch. An approved
package is activated on the change's branch, and the item is ready to be
implemented.

## Goals

- **Every defect and change request is carried by a spec package.** A
  planner session (`af-spec`, unattended) writes one package per item, a
  `lite` one with one requirement for a defect.
- **nightshift gives each planner its package number**, so that two
  planners at work in one workspace never take the same one.
- **A plan is accepted from the fork, not from the session's account.** The
  result file says `written`, and the package on the fork has the number
  given and validates through `afspec` with no error.
- **The plan is a draft change from the planner's first push.** The person
  reads the plan where the code will follow, and the hub never lands a
  draft.
- **A reviewer with a fresh context reviews every plan**, up to 3 rounds,
  before the person is asked.
- **The plan's approval is the one go-ahead.** A `plan` decision shows the
  package, the draft change, the files its tasks expect to touch and the
  policy rules those files would select; `approve` activates the package.
- **A person can skip the approval, not the plan.** With
  `--no-plan-approval` the item is planned and reviewed in the same way and
  goes on without a decision when the reviewer agrees.
- **Work too large for one package is not lost.** Its remaining parts
  become new, unplanned items, which wait for `nightshift item plan`.
- **An imported spec package needs no plan.** It goes from `new` straight
  to `ready`.

## Non-goals

This package is the fifth of ten written from
`docs/prd/02-drive-work-items-through-the-hub.md`. What belongs to the
others:

- **`01_daemon_api_and_credentials`**: the configuration, the store, the
  hub client and its monitor, the API server, the client's command table
  and the event log. This package adds requests to the hub client, one
  route and one command.
- **`02_workspaces_and_policy`**: workspaces, the policy document and its
  sensitive rules, `internal/command` and `internal/gitclone`. This
  package asks the hub which rules a list of paths would select; it does
  not read or write the policy document.
- **`03_work_items_and_inbox`**: work items, their states and transitions,
  `item new`, `item import`, `item abandon`, the function that creates an
  item from another, threads and the decision machinery. This package
  declares the decision kind `plan` in its registry and registers an
  abandon hook.
- **`04_sessions_and_scheduling`**: running a session, the session's
  token, clone and stream, the reviewer's verdict as a result contract, a
  failed session, its retry and the `session_failed` decision, the
  scheduler, pausing. This package runs its sessions through it.
- **`06_implementation_and_review`**: everything after `ready`: opening
  the draft change (it is a draft no longer), the implementer, the gate,
  the review of a version, extending a fork's lifetime, and **restoring a
  fork that expired** while its item waited on the `plan` decision. This
  package keeps the package text that restoring needs and offers the hook
  it runs in.
- **`07_landing`**: landing and its decisions.
- **`08_health_and_publishing`**: the health command, the revert, and
  **returning an item to `awaiting_approval` after a revert** with the
  failure's output. This package offers the function that re-opens the
  `plan` decision with further evidence.
- **`09_notes_and_take_over`**: `nightshift item note`, which during
  planning reaches the planner's next round, and the `take_over` option.
  This package offers the two functions a note needs and specifies
  nothing of the command.
- **`10_rules_brief_and_forge`**: the comment on an imported issue when its
  plan is approved, the rule sessions and the brief.

Also out, for every package of that PRD: the agents' prompts (what
`af-spec` is told is the text of the skill, and the wording of the review
brief is the implementation's; this package says what each must be given
and must produce), stacked changes, and several attempts at one item side
by side (one fork and one change per item at a time).

## Background

Packages 01 to 04 are written and none is implemented: the code this
package names of them is what their packages specify.

- **Package 03** stores an item in `new` with its kind, its title, its
  source and `plan_approval` (false when it was created with
  `--no-plan-approval`), and offers `Transition`, `Abandon`,
  `CreateLinked`, `OnChange` and `OnAbandon` of `internal/item`, and
  `RegisterKind`, `OnAnswer`, `Open` and `Append` of `internal/inbox`. An
  item made from another has the source type `item` and a `parent`. It has
  no function that changes `plan_approval` after creation.
- **Package 04** offers `Scheduler.RegisterStep(state, StepFunc)` and
  `Scheduler.RunSession(ctx, Request)`. A step function is called for each
  item in its state that is not paused and waits on no decision, one call
  at a time per item, and again after it returned. A session is described
  by a `session.Spec` (`Item`, `Role`, `Step`, `Fork`, `Branch` or
  `Commit`, `Prompt`, `Inputs`, `Accept`); in the prompt `{result}` stands
  for the path of the result file and `{input:<name>}` for the path of an
  input file. A session whose `Accept` function returns an error counts as
  failed with the first line of that error; after the second failure in a
  row for one step the item waits on `session_failed`. A reviewer's result
  carries `Verdict` with `agree` or `object` and findings (`text`, and
  `file`, `line`, `criterion` where given). From that package on,
  `command.Runner` runs every command as the session user, and the clones
  belong to that user.
- **Package 02** offers `gitclone.Sync(ctx, Options{URL, Token, Dir,
  Commit, Runner})`, which brings a clone to a commit, detached, and
  `command.Runner`.

Parley (`agent-fox-dev/parley`) is the hub. What this package uses of it,
as its `docs/api.md` states at commit `7eabfb9`:

- A **task fork** has the slug `<parent>--<name>` and copies nothing: it
  reads the parent's history, so every commit of the parent is available
  in it. With `auto_change` (the default) a push of a new branch to the
  fork opens a change by itself; with `auto_change: false` it does not.
- A **change** is opened from a branch of a fork into the parent, as a
  draft when `draft` is true. A draft may have an empty intent and never
  lands. One branch is the source of at most one draft or open change
  (`409 change_exists`, with the existing change in `details`).
- `POST …/policy/evaluate` with `paths` answers the requirements those
  paths would select, each with its `rule`, its `paths`, `approvals` (with
  `required`) and `block`, and writes nothing.
- `GET …/scm/refs/{name}` answers a ref object with the `sha` a branch
  points at, or `404 ref_not_found`.

The spec toolchain (`agent-fox-dev/spec`): `afspec` loads, validates,
renders and activates packages. PRD 09 of that repository specifies the
modes of `af-spec` this package runs (`--unattended`, `--number`,
`--no-activate`, `--defect`, `--findings`, `--result`) and the result
file. No package was generated from that PRD on 2026-11-28 and the skill
does not take these arguments yet, so nightshift's planning cannot run
against the real skill before that PRD is implemented; this package is
built and tested against a fake `claude` that behaves as PRD 09 says.

## Requirements

Constraint: Linux and macOS; Windows is best effort. Constraint: the
`nightshift` client speaks only the daemon's HTTP API.

### When planning starts

An item of kind `spec` is planned already and chosen by the person who
imported it: it goes from `new` straight to `ready`.

For a `defect` or a `change_request` created by a person (`item new`,
`item import --issue`), planning starts at once: the item goes to
`planning`. An item made from another (a part of work too large for one
package, an open item of an implementation) stays `new`, with no session
and no fork, until the person runs `nightshift item plan <N>
[--no-plan-approval]`.

`nightshift item plan <N> [--no-plan-approval]` is `POST
/api/v1/items/{number}/plan` with `{"no_plan_approval"?: bool}`. It
answers `200` with the item and the command prints `planning item <N>`.
With the option, the item's plan needs no approval, as if it had been
created with it. It is refused with `409 conflict` for an item that is not
`new` (`item 12 is ready, not new`) and for an item of kind `spec` (`item
12 is a spec package and needs no plan`), and with `404 not_found` for a
number that names no item.

### The fork, the package number and the planner

For a `defect` or a `change_request`, nightshift creates a task fork of the
workspace and starts a planner session in it. The fork is named `i<N>`
after the item's number (its slug is `<workspace>--i<N>`), is created with
`auto_change` off and the hub's default lifetime, and the item's branch in
it is `item-<N>`.

nightshift gives the planner the number of the package to write: one above
the highest package number on the default branch and above every number it
gave to an item of that workspace that is neither `done` nor `abandoned`.
The highest number on the default branch counts the directories under
`.specs/` and under `.specs/archive/` at the tip of the default branch. An
item keeps the number it was given through every round and every retry. A
number is written with two digits, three past 99.

The planner is `af-spec`, run unattended on a branch of the fork with the
item's text, the number, and the instruction to leave the package a draft
(`af-spec <text file> --unattended --number <NN> --no-activate --result
<file>`, with `--defect` for a `defect`). It writes one spec package: for a
`change_request`, a package of the profile the work needs; for a `defect`,
a `lite` package with one requirement, the behaviour as it should be. A
defect is not reproduced while planning: the failing test is written and
recorded when the package is implemented.

The item's text is the text it was created with; for an item imported from
an issue it is the issue's title, its body and its comments in order.

The session's result is accepted when its result file says `written`, and
the package on the fork has the number given and validates through
`afspec` with no finding of severity error. A result that is not accepted,
or a session that left no result file, counts as a failed session (package
04: the step is started once more, and after the second failure in a row
the item waits on `session_failed`). The reasons:

| Case | Reason |
|---|---|
| the result's `outcome` is not `written` | `planner: <outcome>: <reason>` (`planner: <outcome>` without a reason) |
| the result has no `outcome` | `planner: result has no outcome` |
| the fork has no branch `item-<N>` | `branch item-<N> is not on the fork` |
| the branch has no package directory with the number | `no package <NN> on the branch` |
| the package does not load | `package <NN> does not load: <error>` |
| the validation has an error | `package <NN> does not validate: <check>: <message>` (the first) |

When the planner reports that the work did not fit one package
(`remaining_scopes` of the result file), the package holds its first part.
Each remaining part becomes a new work item of the same kind in state
`new`, linked to the item it came from and carrying the text the planner
gave for it; like an open item of an implementation, it stays `new` until
the person runs `nightshift item plan <N>`. The `plan` decision lists them.

When the planner's branch is on the fork, nightshift opens the item's
change on the hub as a draft, with the item's title and the package's
intent (the text of the `## Intent` section of its `prd.md`).

### The review of a plan

The reviewer then reviews the package against the item's source text. It
is a session of its own (package 04) on the commit the planner pushed,
given the item's text, the package's directory and, from the second review
on, the earlier findings with the planner's replies. Its verdict is
`agree` or `object`, with findings.

On `object` the planner is started again on the draft with the findings
(`af-spec <package> --unattended --findings <file> --no-activate --result
<file>`) and revises, answering each finding; after 3 rounds without
`agree`, planning ends with the objections attached. A round is one review
and, on `object`, the revision that answers it: after the third objection
the planner revises and replies once more, and there is no fourth review.

The findings file is a JSON array; a finding has an `id`, a `source`
(`review` or `person`), a `text` and, where the reviewer named one, a
`criterion`. The planner's replies (`replies` of the result file: `id`,
`action`, `text`) are kept with the findings.

### The plan decision

Planning ends in a decision of kind `plan` in `Action`. It shows the
package's rendered text, a link to the draft change, the files its tasks
expect to touch, each policy rule those files would select that requires
an approval or blocks (`likely to need your approval under rule <name>`,
`blocked by rule <name>`), the reviewer's last verdict and, when it
objected to the end, each open finding with the planner's reply. It also
lists the items made from the parts that did not fit.

Its options are `approve`, `revise` (with a comment, required; planning
runs once more with it, the comment as a finding the planner must follow)
and `abandon`. On `approve` nightshift activates the package on the
change's branch, which freezes its requirements and tasks, and the item is
`ready`. On `abandon` the item is abandoned, as `nightshift item abandon`
does it, which abandons its change on the hub.

While the decision is open the item is `awaiting_approval`. An answer to
the decision of a paused item takes effect at the resume.

### Without a plan approval

An item created with `--no-plan-approval` is planned and its plan reviewed
in the same way, but when the reviewer agrees, no decision is opened:
nightshift activates the package, the item is `ready`, and the thread gets
the entry `plan approved at creation` with a link to the draft change.
When the reviewer objected to the end, the `plan` decision is opened all
the same. `nightshift item plan <N>` takes the same option for an item
that waits in `new`. A plan approved this way is still subject to
everything after it: the gate, the review and the hub's policy.

### The kept package text

nightshift keeps the text of every package a planner wrote in its own
store until the item is `done` or `abandoned`: every file of the package's
directory as it was on the fork when the result was accepted.

### What this package offers the later ones

- **`internal/change`**, the item's task fork and change on the hub:
  `Service.EnsureFork(ctx, item)`, `Service.OpenDraft(ctx, item, title,
  intent, sessionID)`, `Service.Head(ctx, item)` (the head of the item's
  branch on the fork), `Service.Get(ctx, item)` (a `Record` with
  `Workspace`, `Fork`, `GitURL`, `Branch`, `Generation`, `Change`,
  `ChangeRef` and `ChangeURL`), `Service.Abandon(ctx, item, reason)`,
  which is the hook registered with `item.OnAbandon`, and
  `Service.Forget(ctx, item)`, after which `EnsureFork` creates a new fork
  for the item (package 06 restores an expired fork with it).
- **`plan.Service.Reopen(ctx, item, reason, extra)`**: moves an item to
  `awaiting_approval` and has a `plan` decision opened that shows what the
  first one showed and the evidence given (package 08: the failure's
  output after a revert).
- **`plan.Service.Kept(ctx, item)`**: the last package text kept for the
  item (`Package` with `Number`, `Dir`, `Head` and `Files`).
- **`plan.Service.RegisterPrepare(name, fn)`**: a function called before
  an answer `approve` or `revise` is carried out; while it returns an
  error the answer waits (package 06 restores the fork there).
- **`plan.Service.AddFinding(ctx, item, text)`** and
  **`plan.Service.Revise(ctx, item, comment)`**: a finding of the person
  for the planner's next run, and the return of an item from
  `awaiting_approval` to `planning` (package 09: a note).
- **The decision kind `plan`** (`approve`, `revise`, `abandon`), to which
  no package adds an option.

### End-to-end flows

**A change request is planned, reviewed and approved.** The person runs
`nightshift item new demo --text "Add a greeting endpoint"`. `nightshiftd`
moves the item to `planning`, creates the task fork `demo--i1` on the hub,
takes the next package number and starts the planner; the planner pushes a
package to `item-1`; nightshift accepts it, keeps its text and opens the
draft change; the reviewer agrees; the item is `awaiting_approval` and a
`plan` decision appears in `nightshift inbox --action`. The person runs
`nightshift decide <id> --option approve`. nightshift activates the
package on the branch and the item is `ready`.

**A defect without a plan approval, sent back once, with a part split
off.** The person runs `nightshift item new demo --kind defect --text
"Greeting drops the name" --no-plan-approval`. The planner, started with
`--defect`, writes a one-requirement package and reports one remaining
scope; nightshift makes it a linked `defect` item in `new`. The reviewer
objects with one finding; the planner is started with `--findings`,
revises and replies; the reviewer agrees. No decision is opened: the
package is activated, the item is `ready` and its thread says `plan
approved at creation`. The linked item is still `new`; the person runs
`nightshift item plan <M>`, and its planner is started with the next
package number.

**A plan the reviewer objects to, sent back by the person and abandoned.**
The reviewer objects three times; after the third revision a `plan`
decision opens with the verdict `object` and each open finding with the
planner's reply. The person answers `revise` with a comment; the planner
is started with the comment as a finding of the person; the reviewer
agrees and a new `plan` decision opens. The person answers `abandon`; the
item is `abandoned` and its change is abandoned on the hub.

## Design Decisions

From the source PRD, by its numbers:

- **3. The plan's approval is the one go-ahead; an imported spec package
  needs none.** The owner decided it.
- **14. The plan is a draft change from the planner's first push.** The
  owner decided it.
- **16. nightshift gives each planner its package number.** The owner
  decided it: two planners at once would otherwise take the same one.
- **20, 21. The reviewer works in a fresh context and reviews the plan.**
  The owner decided it.
- **24. nightshift opens the change; a session's token cannot.** Chosen
  there.
- **29. Every work item is carried by a spec package, a `lite` one for a
  defect.** Kept from PRD 01.
- **31. 3 review rounds** is a starting value.
- **33. A defect is proven when it is implemented, not when it is
  planned.** The owner decided it.
- **34. A planned package is a draft until the plan is approved;
  nightshift activates it then.** The owner decided it.
- **35. The parts of work too large for one package become new, unplanned
  items, which wait for `item plan`.** The owner decided the first; the
  second is chosen there.
- **36. Every session reports through a result file.** Chosen in PRD 09 of
  `agent-fox-dev/spec`.
- **38. `--no-plan-approval` still plans and still has the plan
  reviewed.** Chosen there.

Chosen here:

1. **This package is the fifth of ten** and holds *Planning* of the source
   PRD with the item's fork and change. The owner agreed to the split on
   2026-11-28.
2. **Two Go packages: `internal/change` (the item's fork and change on the
   hub) and `internal/plan` (planning, which imports `change`).** Why:
   packages 06, 07 and 08 need the fork and the change and nothing of
   planning.
3. **Planning is driven by three step functions registered with the
   scheduler, for `new`, `planning` and `awaiting_approval`; an answer to
   a `plan` decision is stored by its handler and carried out by the step
   of `awaiting_approval`.** Why: the scheduler calls no step of a paused
   item, so an answer takes effect at the resume without further code, and
   a handler that package 03 may call twice does nothing twice.
4. **"At once" is the scheduler's next pass.** Why: the scheduler wakes on
   `item.created`, and an item created in a paused workspace must not
   start.
5. **Whether an item is planned at once is read from its source: an item
   of source type `item` waits for `item plan`, every other `defect` or
   `change_request` is planned.** Why: package 03 gives exactly the items
   made from another that source.
6. **`item plan` is `POST /api/v1/items/{number}/plan`; it is accepted for
   any `defect` or `change_request` in `new`, also one whose planning is
   due anyway, and refused with `409 conflict` otherwise.** Why: the source
   PRD names the command and no route, and gives it to "an item that waits
   in `new`".
7. **`--no-plan-approval` of `item plan` is kept with the plan, not in the
   item's `plan_approval`.** A plan needs an approval when the item's
   `plan_approval` is true and `item plan` was not run with the option.
   Why: package 03 offers no function that changes the member.
8. **The fork is named `i<N>` and the branch `item-<N>`; a fork made after
   `Forget` is `i<N>-<g>` with the generation `g` from 2.** Why: package
   04's fixtures use these names, and an expired fork may still hold its
   name when its successor is created.
9. **The fork is created with `auto_change: false`, the hub's default
   lifetime, no token and no session.** Why: nightshift opens the change
   itself, as a draft with the package's intent; the lifetime is extended
   by package 06; the session's token is minted by package 04; and the
   fork exists before its first session.
10. **A fork or a change the hub says exists already (`409 conflict`,
    `409 change_exists`) is taken as the item's.** Why: it is what a
    daemon that died between the hub's answer and its own store finds at
    the next start.
11. **The highest package number on the default branch counts the
    directories under `.specs/` and `.specs/archive/`, whether or not they
    load.** Why: `af-spec --number` refuses a number that is not above
    every one of them (PRD 09 of `agent-fox-dev/spec`).
12. **nightshift reads the fork through a clone of its own per item,
    `<clones directory>/<fork slug>.plan`, brought to a commit with
    `gitclone.Sync`.** Why: the session's clone holds what a session left
    behind, and "the package on the fork" is what the hub holds.
13. **The head of the item's branch is read with `GET …/scm/refs/{name}`
    on the fork.** Why: the planner's first push has no change yet whose
    version would name it.
14. **The planner's prompt is `/af-spec {input:text} --unattended --number
    <NN> --no-activate --result {result}` (with ` --defect`), and for a
    revision `/af-spec <package directory> --unattended --findings
    {input:findings} --no-activate --result {result}`.** Why: `specrun`
    starts a skill as a slash command, and PRD 09's own flow of a revision
    passes `--no-activate`.
15. **Planner sessions carry the step name `plan`, plan reviews
    `plan_review`.** Why: package 04 counts failures per item and step, and
    a failing reviewer is not a failing planner.
16. **The text of an imported issue is `# <title>`, the body, and each
    comment under `## Comment by <author>`.** Why: the planner takes one
    text file.
17. **The acceptance runs in the session's `Accept` function, with the
    reasons of the table under *The fork, the package number and the
    planner*.** Why: package 04 then counts a refused result as a failed
    session with that reason.
18. **A branch that already holds a valid package with the item's number
    when a first planner session would start is taken without a session.**
    Why: it is what a daemon that died after the session and before its
    own record finds; a second `af-spec --number` would refuse the number.
19. **A split part is made once per scope `name`.** Why: a revision's
    result may list the remaining scopes again.
20. **A round is one review and the revision that answers it; after the
    third objection the planner still revises, and no fourth review
    runs.** Why: the decision shows each open finding with the planner's
    reply, which exists only when the planner answered the last review.
21. **A finding's `id` is `r<round>.<n>` for the reviewer's and `p<n>` for
    the person's; a finding without a reply is shown with `no reply`.**
22. **After `revise`, the rounds are counted again from zero, and a plan
    that needs no approval goes on without a decision when the reviewer
    then agrees.** Why: "planning runs once more", and the source PRD opens
    a decision for such an item only when the reviewer objected to the
    end.
23. **A finding of the person that is pending when the reviewer agrees
    starts one more revision and review.** Why: `revise` and package 09's
    notes are the same thing to the planner, a finding it must follow.
24. **The link to the draft change is `<hub.endpoint>/api/v1/workspaces/
    <slug>/changes/<number>`, shown with the change's `ref`
    (`demo#3`).** Why: the hub's change object carries no URL of a page.
25. **The paths sent to `policy/evaluate` are the tasks' `touches` as they
    are written, each once, the first 2,000; with no `touches` no request
    is sent.** Why: the hub takes 1 to 2,000 paths. A requirement is shown
    as blocking when its `block` is true, else as needing an approval when
    `approvals.required` is 1 or more.
26. **`approve` is recommended when the reviewer agreed, `revise` when it
    objected to the end or an activation failed.**
27. **The package is activated by `afspec`'s `Transition`, run by
    `nightshiftd` itself in a mode `nightshiftd activate-package
    <directory>` through `command.Runner`, then committed (`chore:
    activate package <NN_name>`, author `nightshift
    <nightshift@localhost>`) and pushed to the item's branch with the
    worker token.** Why: from package 04 on the clones belong to the
    session user, for whom the daemon's user cannot write; package 04's
    helper mode is the precedent; and the push by the worker is a version
    the approver token did not create.
28. **A package that is `active` already is not activated again.** Why:
    an answer may be carried out twice, and a daemon may die between the
    push and the item's move.
29. **An activation `afspec` refuses opens a `plan` decision that carries
    the refusal; a push or a hub request that fails is tried again at the
    scheduler's next pass.** Why: the first needs the planner, the second
    ends by itself.
30. **`abandon` calls the item service's `Abandon`.** Why: one way to end
    an item, with the hooks of packages 04 and 05.
31. **Thread entries of the kinds `fork_created`, `change_opened`,
    `change_abandoned`, `plan_written`, `plan_review`, `plan_approved`
    and `activation_failed`; none marks the thread unread.** Why: the
    decision's opening marks it, through package 03.
32. **While the hub is unreachable or the worker token is not accepted,
    the steps do nothing and are called again.** Why: package 01's
    `Available`.
33. **The kept text is every regular file of the package's directory, by
    its path below the directory, with the commit it was read at; all
    kept texts of an item are removed when it becomes `done` or
    `abandoned`, with its clone.**
34. **No configuration key is added.** The 3 rounds are a parameter of the
    service with that default.
35. **A re-opened `plan` decision recommends `revise`, and is opened also
    for a plan that needed no approval.** Why: it comes back because what
    was approved did not hold (package 08: a revert), which the person
    should see before it is tried again.
36. **Functions registered with `RegisterPrepare` run before `approve` and
    `revise` are carried out, not before `abandon`.** Why: they restore a
    fork the answer needs (package 06), and an abandoned item needs none.
37. **An item moves from `new` to `planning` whether or not the hub is
    reachable; the fork, the number and the sessions wait for the hub.**
    Why: `planning` says that a plan is due, and package 04 starts no
    session without the hub.

## Dependencies

- **`docs/prd/02-drive-work-items-through-the-hub.md`**: the source PRD,
  of which this package specifies *Planning*, and of *Implementation* the
  creation of the task fork and the draft change.
- **`01_daemon_api_and_credentials`** (status `draft`): the hub client
  with `Available` and its typed error, the fake hub `internal/hubtest`,
  the API server's `Handle`, the client's `Register`, the store's
  migrations and test template, the configuration and the e2e helper.
- **`02_workspaces_and_policy`** (status `draft`): `workspace.Service`
  with `Get`, the hub client's `Workspace`, `command.Runner`,
  `gitclone.Sync` and the clones directory.
- **`03_work_items_and_inbox`** (status `draft`): `internal/item`
  (`Get`, `Transition`, `Abandon`, `CreateLinked`, `OnChange`,
  `OnAbandon`) and `internal/inbox` (`RegisterKind`, `OnAnswer`, `Open`,
  `Append`).
- **`04_sessions_and_scheduling`** (status `draft`):
  `Scheduler.RegisterStep`, `Scheduler.RunSession`, `session.Spec` and
  `session.Result` with the reviewer's verdict, and the helper mode's
  precedent.
- **Parley** (`agent-fox-dev/parley`), the hub: its packages
  `15_task_fork_creation`, `22_change_opening_and_versions`,
  `23_change_lifecycle_and_mergeability` and `27_landing_policy`, each
  with every task `done` on 2026-11-27.
- **`afspec`** (`github.com/agent-fox-dev/spec/afspec`), to find, load,
  validate, render and activate packages.
- **`af-spec`** (`agent-fox-dev/spec`, `skills/`), in the modes that
  `docs/prd/09-run-af-spec-and-af-impl-under-a-controller.md` of that
  repository specifies. No package was generated from that PRD on
  2026-11-28.

## Verified External API

- **Parley's REST API**, read in `docs/api.md` of `agent-fox-dev/parley`
  at commit `7eabfb9`:
  - `POST /api/v1/workspaces/{slug}/forks`, scope `workspaces:write`, with
    `name` (1 to 32 characters of `a`–`z`, digits and hyphens), `ref`,
    `ttl`, `auto_change`, `purpose`, `session_id` and `token`; `201` with
    `workspace` (the fork's workspace object) and `remote` (its git URL);
    `409 conflict` when the parent has a fork of that name.
  - `GET /api/v1/workspaces/{slug}/scm/refs/{name}`, scope `git:read`: a
    ref object with `name`, `kind` and `sha`; `404 ref_not_found`.
  - `POST /api/v1/workspaces/{slug}/changes`, scope `changes:write`, with
    `source` (`workspace_slug`, `branch`), `title` (at most 200
    characters), `intent` (at most 16,000), `draft` and `session_id`;
    `201` with the change object (`number`, `ref`, `state`, `version`,
    `head_sha`); `409 change_exists` with `details` `{workspace_slug,
    number, ref}`; `400 source_not_found`, `400 nothing_to_land`.
  - `POST /api/v1/workspaces/{slug}/changes/{number}/abandon` with
    `reason`; `200` with the change object; `409 change_closed` for one
    abandoned or landed already.
  - `POST /api/v1/workspaces/{slug}/policy/evaluate`, scope
    `changes:read` (satisfied by `changes:write`), with `paths` (1 to
    2,000, not starting with `/`); `200` with `policy_revision`, `status`
    and `requirements`, each `{rule, paths, approvals {required, counted,
    approvers}, checks, block, satisfied}`.
  - The workspace object's `head_sha` and `hub_url`; the scope rule that
    `R:write` satisfies `R:read`.
- **`github.com/agent-fox-dev/spec/afspec`**, read in
  `docs/generated/afspec.txt` of `agent-fox-dev/spec` at commit `8faad70`:
  `func ListSpecDirs(root string) ([]string, error)`, `func
  ParseSpecDirName(name string) (string, string, error)`, `func
  LoadSpec(dir string) (*Spec, error)`, `func ValidateInRoot(specsDir
  string, spec *Spec) (ValidationResult, error)` (`Errors
  []ValidationEntry` with `Check` and `Message`), `func (s *Spec)
  RenderCombined(opts ...RenderOption) string`, `func (s *Spec)
  Transition(target, dir string) (*Spec, error)` (a `LifecycleError`, an
  `IntentError` or a `SaveError`), the fields `Spec.Status`,
  `Spec.PRDBody` and `Spec.Tasks`, and `Task.Touches`.
- **`af-spec`'s controller modes: NOT FOUND in the skill.** `--unattended`,
  `--number`, `--no-activate`, `--defect`, `--findings` and `--result` do
  not exist in `skills/af-spec` at commit `8faad70`; assumed as PRD 09 of
  that repository specifies them, the result file with `outcome`,
  `reason`, `package`, `branch`, `head`, `pushed`, `remaining_scopes`
  (`name`, `summary`, `text`) and `replies` (`id`, `action`, `text`)
  among them.
- **Packages 01 to 04**, read in `.specs/` as drafts: the names under
  *Dependencies*.

## Glossary

- **plan**: the spec package a planner wrote for a defect or a change
  request, which the person approves before it is implemented.
- **planner, reviewer**: the roles of package 04; the planner runs
  `af-spec`, the reviewer reads the package with a fresh context.
- **package number**: the numeric prefix of a spec package's directory,
  which gives the order packages are implemented in.
- **round**: one review of a plan and, when the reviewer objects, the
  planner's revision that answers it.
- **finding**: one objection of the reviewer, or one instruction of the
  person, that the planner answers with a reply.
- **split part**: a part of the work the planner left out of the package
  because it did not fit; it becomes a linked item.
- **linked item**: an item made from another; it stays `new` until the
  person asks for its plan.
- **kept text**: the files of a package as the planner pushed them, held
  in nightshift's store until the item is `done` or `abandoned`.
- **activation**: the move of a package from `draft` to `active`, which
  freezes its intent, requirements and tasks.
- **task fork, change, draft, version, policy**: as Parley defines them
  (`docs/api.md`).
- **package, `lite`, `touches`, `active`**: as the spec format of
  `agent-fox-dev/spec` defines them.
