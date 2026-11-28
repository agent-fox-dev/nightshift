---
spec_id: "10"
spec_name: "rules_brief_and_forge"
title: "Write decisions back as rules, brief the person daily and answer the forge"
status: "active"
created_at: "2026-11-28T10:24:34Z"
intent_hash: "7ce0d1c96c5fff1cf58b097366fdc5208e5683f4cd59536bfb500a79ada18c98"
contract_hash:
  requirements.json: "321cb7fd37a223b28b93352ac3b1728e25a41ca03397faa8ff59eef1d164633b"
  tasks.json: "4b4aba0bb08ad4526f780979c773e0c129dcc7f35a5271f7f011c08bdd6862b8"
schema_version: 3
source: "docs/prd/02-drive-work-items-through-the-hub.md"
---

# Write decisions back as rules, brief the person daily and answer the forge

## Intent

nightshift works without the person watching, so what it did and what it
learned has to reach them and the places they already look. A decision the
person asks to keep is written into the repository as a rule that later
sessions find, and lands only after the person approved its text. Once a
day one brief says what finished, what waits for the person and what was
spent, and ends with the numbers that show how well the work goes; the same
numbers can be asked for at any time. The issue an item was imported from
is told when its plan is approved, when it is done and when it was reverted
or abandoned.

## Goals

- **A rule is written back only when the person asks.** A decision
  answered with `--record`, and no other, becomes a change that holds the
  question, the answer and the comment as an ADR, a steering line or a file
  under `docs/decisions/`, and nothing else.
- **The person approves the rule's text.** The change reaches them as a
  `rule` decision with the text; it lands only on `approve`, and is then
  verified and published as any other change.
- **One brief a day.** Once a day `Unread` gets one entry that says, per
  workspace, what reached `done`, what was reverted or abandoned, which
  decisions wait and for how long, which items wait and what for, and what
  was spent against the daily limit. A day with nothing to say gets none.
- **The outcome numbers are there at any time.** `nightshift stats` prints,
  per workspace and over a number of days, how many items finished, how
  many needed rework, a revert or the person's hands, how long decisions
  waited and what an item cost.
- **The issue hears what became of it.** An item imported from an issue
  comments on that issue when its plan is approved, when it is done (and
  closes it) and when it is reverted or abandoned; a forge that does not
  answer never holds the item back.

## Non-goals

This package is the tenth of ten written from
`docs/prd/02-drive-work-items-through-the-hub.md`. What its neighbourhood
holds that is another package's:

- **`01_daemon_api_and_credentials`**: the daemon, the API server, the
  client's command table, the configuration loader, the store, the event
  log and the hub client. This package adds one configuration key, two
  routes and two commands to them.
- **`02_workspaces_and_policy`**: workspaces, their state `green`, `red`
  or `held`, their daily limit and the health command. This package reads
  them.
- **`03_work_items_and_inbox`**: work items and their states, threads,
  entries, the decision machinery, `nightshift decide` with `--record`
  (that package stores the `record` member; this one acts on it), the brief
  thread, the notification command and importing an issue.
- **`04_sessions_and_scheduling`**: running a session as the session user
  with a token, a hub record and a stream, the start order, the daily
  limit and pausing. This package runs its rule sessions through it and
  extends it for a session that belongs to no item.
- **`05_planning`**: planning an item with `af-spec`, the `plan` decision
  and the item's task fork and change (`internal/change`). A rule is not
  planned and is no work item.
- **`06_implementation_and_review`**: the implementer, the gate, the
  review, the check results it reports for an item's change, and the
  files of a version outside its plan, which the brief lists.
- **`07_landing`**: the landing conditions, the land request and its
  outcomes for an item's change, the `approval`, `conflict` and `policy`
  decisions. This package lands a rule's change with its `LandChange`,
  under the same conditions, and asks for none of those decisions.
- **`08_health_and_publishing`**: the health command after a landing, a
  `red` workspace, repair and revert, the push to origin and a `held`
  workspace. This package checks a landed rule's commit with
  its `VerifyTip` and adds one function, `PushTip`, to its
  `internal/publish`.
- **`09_notes_and_take_over`**: notes, taking an item over and the entry
  it writes when an item is taken over, which the outcome numbers count.

Also out, for every package of that PRD: a learning loop beyond the rules a
person asks for, labels on the forge, intake from forge webhooks, a web or
desktop client, and the wording of the prompt a rule's session is given.

## Background

Packages 01 to 09 leave a daemon that drives a work item from intake to
`done`, with an inbox, sessions under limits, and a hub client. Three
things the source PRD names are still missing, and every Go package this
document names for them is new: `internal/rule`, `internal/stats`,
`internal/brief` and `internal/forge`.

What this package uses of Parley, as its `docs/api.md` states at commit
`7eabfb9`:

- `POST /api/v1/workspaces/{slug}/forks` creates a task fork
  `<slug>--<name>`; with `auto_change: false` a push to it opens no change
  by itself. A name the parent already has a fork of is `409 conflict`.
- `GET …/scm/tree` lists a directory at a revision, `GET …/scm/file`
  answers one file (`404 path_not_found` when it is not there), and `GET
  …/scm/diff` with `base` and `head` answers what `head` changed since it
  parted from `base`: `files`, each with `path`, `status` (`added`,
  `modified`, `deleted`, `renamed`, `copied`, `type_changed`),
  `insertions`, `deletions` and `patch`.
- `POST …/changes` opens a change of a fork's branch into the workspace;
  a later push to the branch records the change's next version. `GET
  …/changes/{number}` answers the change with `state`, `version` and, once
  landed, `landed.sha`. `POST …/changes/{number}/abandon` abandons it.
- `PUT …/changes/{number}/checks/{name}` reports a check on a version with
  a `summary`.
- `POST …/changes/{number}/land` with `expected_version` answers the land
  attempt: `200` `landed`, or `409 land_conflict`, `409 policy_blocked`,
  `409 version_mismatch`, `503 land_failed`.
- `POST …/issues/{number}/comments` posts a comment and honours an
  `Idempotency-Key`: a replay sends nothing to the forge. `POST
  …/issues/{number}/close` closes an issue, and closing a closed issue
  succeeds. Both need `forge:write`; a forge failure is the hub's error
  envelope with a `forge_…` type.

Package 03 gives an answer its `record` member and appends the event
`decision.answered`; it writes an entry of kind `state` with the text
`<from> -> <to>` for every state change of an item and calls the hooks
registered with `OnChange`. Package 04 keeps one record per session with
its item, workspace, role, status, tokens and start and end time.

## Requirements

Constraint: Linux and macOS; Windows is best effort. Constraint: the
`nightshift` client speaks only the daemon's HTTP API, so every command
below has a route.

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

What this package adds to that text:

- **One rule per recorded answer.** The rule is a record of its own, not a
  work item: the decision it came from, the workspace, the thread of that
  decision, the question, the answered option, the comment, the target and
  a state: `writing`, `proposed` (its `rule` decision is open), `approved`,
  `landed`, `published`, `discarded` or `failed`. A `rule` decision
  answered with `--record` starts no further rule.
- **The target** is chosen by nightshift before the session, from the tip
  of the workspace's default branch: `adr` where `docs/adr/` holds at least
  one entry, else `steering` where `.specs/steering.md` exists, else
  `decisions`. The session is told the target.
- **The fork and the branch.** The fork is `<slug>--rule-<decision id>`,
  created with `auto_change: false`; the session of attempt n works on the
  branch `rule-<decision id>-<n>`.
- **What "changes no other file" means.** When the session ended, nightshift
  reads from the hub what the branch changed against the fork's default
  branch. For `adr`: exactly one file, added, directly in `docs/adr/`. For
  `steering`: exactly one file, `.specs/steering.md`, modified, with no
  line removed and every added line after the last unchanged one. For
  `decisions`: exactly one file, added, under `docs/decisions/`. Anything
  else is a failed session: no change is opened for that branch, and the
  next attempt works on a new branch.
- **Failed sessions.** After a failed session the rule's session is
  started once more. After the second failure in a row the rule is
  `failed`: its change, when it has one, is abandoned, and the thread of
  the decision it came from gets one `Unread` entry naming both reasons.
  An interrupted session is started again and counts as no failure.
- **The `rule` decision** is opened in the thread of the decision it came
  from, with the question `Add this rule to <slug>?`, the rule's text, its
  file and the change as evidence, `approve` recommended, a comment
  required for `revise`, and `nothing` as what it blocks.
- **Landing.** An approved rule lands when its workspace is `green` and not
  paused and the hub is available; until then it waits, and nothing is
  reported. nightshift then syncs from origin as before any landing,
  reports `nightshift-gate`, `nightshift-review` and `nightshift-release`
  as `passed` on the change's current version with the summary `rule
  approved by the owner`, and asks the hub to land exactly that version,
  through package 07's `LandChange`. A land that does not end `landed`
  makes the rule `failed`, as above.
- **After the landing** the health command runs on the landed commit
  through package 08's `VerifyTip`, its check of a commit no item is to
  blame for, and, when it is healthy, the default branch is pushed to
  origin through `PushTip`, a function this package adds to
  `internal/publish`: one push, sent only while the workspace is `green`,
  that opens no decision. When origin has the commit, the rule is
  `published`. What a failed health command starts (`red`, the `main_red`
  decision) is package 08's; after it, or after a push that origin did not
  take, the rule stays `landed`, and its commit reaches origin with the
  next push package 08 sends for the branch.
- **`revise`** starts the session again on the same branch with the
  comment, so that the change gets a new version, and a new `rule`
  decision opens for it. **`discard`** abandons the change.

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

The brief's text, with one block per registered workspace in the order of
the slugs; a list with nothing in it is left out with its heading, the
`tokens` line and the closing numbers are always there:

```text
brief 2026-11-27T07:00:00Z to 2026-11-28T07:00:00Z

demo
  done:
    #12 Add a greeting endpoint: change 41 is on origin as 7c1e9a2; 54200 tokens spent
      outside the plan: docs/api.md, internal/api/server.go
  reverted:
    #13 Cache the greeting
  abandoned:
    #14 Rename the endpoint
  decisions open:
    5 plan 1d 3h: Approve the plan for item 15?
  waiting:
    #15 awaiting_approval: decision 5 (plan)
    #16 ready: the daily limit
  tokens: planner 1200, implementer 50000, reviewer 3000, total 54200 of 50000000 a day
  last 30 days:
    done 4, abandoned 1
    first pass 75%, sessions per item 3
    reverted 0, taken over 1
    decisions per done item 1.5, decision wait 2h 10m
    creation to done 5h 3m
    tokens per done item 1200000
```

- **The period** runs from the time of the last entry of the brief thread,
  or from 24 hours before when there is none, to the moment the brief is
  made.
- **Done, reverted, abandoned**: an item is listed under `done` or
  `abandoned` when it reached that state in the period, and under
  `reverted` when it moved from `verifying` to `awaiting_approval` in it.
  A done item's line ends with the text of the entry package 08 wrote
  when the item was published (the change, the commit and the tokens
  spent); under it stand the files of the item's last evaluated version
  that were outside its plan, as package 06 keeps them, when there are
  any.
- **Decisions open**: every open decision of the workspace's threads,
  oldest first: its id, its kind, its age and its question.
- **Waiting**: every item that is neither `done` nor `abandoned` and waits:
  for its oldest open decision (`decision <id> (<kind>)`), else because it
  is `paused` or `taken over`, else for what the scheduler reports (`a free
  session`, `the hub`, `the daily limit`, a start guard's reason).
- **Tokens**: the tokens of the workspace's sessions that ended in the
  period, per role and in total, beside the workspace's daily limit.
- **An age and every other duration** is printed as `<d>d <h>h` from 24
  hours on, as `<h>h <m>m` from one hour on, else as `<m>m`, rounded down.

`GET /api/v1/brief` answers the brief for the time since the last one as
`{"since", "until", "text", "workspaces": […]}`, each workspace with the
members `workspace`, `done`, `reverted`, `abandoned`, `decisions`,
`waiting`, `tokens` and `outcomes`; `nightshift brief` prints `text`. It is
made also when the daily brief would be left out.

When a brief is written, the notification command is run with the event
`brief` and the brief thread's URL path.

**Outcome numbers.** The brief ends with these numbers per workspace, over
the last 30 days, and `nightshift stats [<slug>] [--days <N>]` (1 to 365,
default 30) prints them at any time:

- the items that reached `done`, and the items abandoned;
- of the `done` items, the share that needed no rework session (`first
  pass`), and the median number of sessions per item;
- the items reverted after a failed health command, and the items taken
  over;
- the decisions answered per `done` item, and the median time a decision
  waited for its answer;
- the median time from an item's creation to `done`;
- the tokens per `done` item, median.

A number with no item to count is printed as `-`.

How each number is counted, for one workspace and the days asked for, the
`done` items being those that reached `done` in that time:

| Number | Counted as |
|---|---|
| done, abandoned | the items in that state whose state began in that time |
| first pass | the share of the done items, in whole percent, for which no implementer session started after the end of the item's first completed implementer session |
| sessions per item | the median, over the done items, of the number of sessions started for the item, of every role and outcome |
| reverted | the items that moved from `verifying` to `awaiting_approval` in that time |
| taken over | the items taken over in that time |
| decisions per done item | the answered decisions of the done items' threads, divided by the number of done items, with one decimal |
| decision wait | the median, over the workspace's decisions answered in that time, of the time from opening to answer |
| creation to done | the median, over the done items, of the time from creation to `done` |
| tokens per done item | the median, over the done items, of the tokens of the item's sessions |

A median of an even number of values is the mean of the two in the middle.
The four counts are printed as `0` when nothing is counted; the six other
numbers are printed as `-` when there is no done item, or for the decision
wait no answered decision.

`GET /api/v1/stats` takes `workspace` and `days` and answers `{"days",
"workspaces": [{"workspace", "done", "abandoned", "first_pass",
"sessions_per_item", "reverted", "taken_over", "decisions_per_done_item",
"decision_wait_seconds", "creation_to_done_seconds",
"tokens_per_done_item"}]}`, with `null` where the text has `-`.
`nightshift stats` prints, per workspace, the line `<slug>, last <N> days`
and the six lines of the block above.

### Writing back to the forge

For an item imported from an issue, and for no other, nightshift writes to
that issue through the hub's forge routes: one comment when the plan is
approved, naming the change; one when the item is `done`, naming the commit
on origin, after which it closes the issue; one when the item is reverted
or abandoned, saying which. It writes no labels. A forge call that fails is
tried again every 10 minutes for 24 hours, then noted once in the item's
thread; it never holds the item back.

The comments:

| When | Comment |
|---|---|
| the item becomes `ready` from `planning` or `awaiting_approval` | `nightshift: the plan is approved. The work is change <number> of <slug> on <hub endpoint>.` |
| the item becomes `done` | `nightshift: done. Commit <sha> is on origin.` |
| the item moves from `verifying` to `awaiting_approval` | `nightshift: the change was reverted after a failed health command.` |
| the item becomes `abandoned` | `nightshift: the item was abandoned.` |

Each write is kept in the store until the forge took it or it is given up,
and the writes of one item are sent in the order they arose.

### End-to-end flows

**A recorded answer becomes a rule on origin.** The person answers a
decision with `nightshift decide <id> --option approve --comment "Never
cache greetings" --record`. `nightshiftd` creates the fork `demo--rule-<id>`
and starts a planner session in it; the session adds one file under
`docs/adr/` and pushes; nightshift reads the branch's diff, opens the change
and opens a `rule` decision with the file's text. The person answers
`approve`. nightshift reports the three checks with `rule approved by the
owner`, the hub lands the change, the health command exits 0 and the hub
pushes the branch to origin.

**The morning's brief.** At 08:00 `nightshiftd` adds one entry to the brief
thread; the configured command receives `brief`; the thread is in
`nightshift inbox --unread`. The person runs `nightshift stats demo --days
7` and reads the numbers of the last week, and `nightshift brief` later in
the day for what happened since.

**An imported issue hears what became of it.** The person imported issue 7
as item 12. Its plan is approved: the issue gets a comment naming the
change. The forge is away when the item is `done`; ten minutes later it is
back, the issue gets the comment naming the commit and is closed. The item
was `done` all the while.

## Design Decisions

From the source PRD, by its numbers:

- **4. Files outside the plan are noted, not held.** The owner decided it:
  they appear in the thread and the brief.
- **17. A rule is written back only when the person asks with
  `--record`.** The owner decided it.
- **21. Forge write-back only for imported issues.** Kept from PRD 01,
  where the owner decided it.
- **37. The outcome numbers** are one of the seven changes the owner
  decided after the comparison with other internal coding agents.
- **42. Telling the person is one command.** Chosen there; this package
  calls it with the event `brief`.

Chosen here:

1. **This package is the tenth of ten.** The owner agreed to the split on
   2026-11-28; this package holds *Decisions written back as rules*, *The
   daily brief* with the outcome numbers, and *Writing back to the forge*.
2. **A rule is a record of its own, not a work item.** Why: an item moves
   through planning, implementation and review, which a rule has none of,
   and the transition table of package 03 allows no way round them.
3. **A rule's session belongs to no item. This package extends
   `internal/session` and `internal/sched` for it:** a session asked with
   no item and a scope (a workspace, a thread and a key) is recorded on the
   hub with `metadata {"scope": <key>}`, works in
   `<sessions.dir>/scopes/<key>/clone`, writes its entries to the scope's
   thread, counts toward the workspace's daily tokens, is started after the
   sessions of `ready` items in the order asked, and is not started while
   the hub is unavailable, the workspace is paused or its daily limit is
   reached. Why: a session of package 04 is bound to an item's clone and
   thread, and the item of the recorded decision is at work in its own
   clone, or there is none (a decision of a workspace's thread).
4. **Recorded answers are found through the event `decision.answered` and,
   at every start, through the list of answered decisions; one rule per
   decision.** Why: package 03 has one handler for a kind and an option,
   which belongs to the kind's owner, and an answer must not be lost to a
   restart.
5. **A `rule` decision answered with `--record` starts no rule.** Why: the
   rule it approves is the record already.
6. **nightshift chooses the target before the session and tells it.** Why:
   it has to check the session's change against the target afterwards, and
   the source PRD's order (ADR, steering, `docs/decisions/`) is a fact of
   the repository, not a choice of the agent.
7. **The session's change is checked on the fork before a change is
   opened, with the hub's diff of the branch against the fork's default
   branch.** Why: the source PRD reads results "from the hub and the fork,
   not from the session's account", and a branch that fails the check is
   then discarded by never becoming a change.
8. **"A line appended" is checked as: no line removed, and no unchanged
   line after the first added one.** Why: it is what the diff shows, and it
   lets a rule take more than one line.
9. **After the second failed session in a row the rule is `failed`, with
   one `Unread` entry `rule for decision <id> not written: <reason>;
   <reason>`; no decision is opened.** Why: the `session_failed` decision of
   package 04 belongs to an item, and its `abandon` would end that item; a
   rule that could not be written holds nothing back.
10. **A land of a rule's change that does not end `landed` makes the rule
    `failed`, with the entry `rule for decision <id> not landed: <the kind
    of package 07's outcome>`, and its reason where it has one.** Why: the source PRD names no conflict, approval or
    block for a rule, and the `approval`, `conflict` and `blocked`
    decisions belong to items; the person can record the answer anew.
11. **An approved rule waits while its workspace is `red`, `held` or
    paused, and reports no check meanwhile.** Why: nothing lands in a red
    or held workspace (source PRD, *Workspaces*), and a passed
    `nightshift-release` would let the hub land it by other means.
12. **After the landing the commit is checked with package 08's
    `health.Service.VerifyTip` and pushed with `publish.Service.PushTip`,
    which this package adds to `internal/publish`; a failure of either
    leaves the rule `landed`.** Why: "as any other"; package 08 verifies a
    commit without an item (`VerifyTip`) but pushes only for an item
    (`PublishStep`) or an answered `origin` decision (`Retry`), and what a
    `red` or `held` workspace starts stays that package's: `PushTip` opens
    no decision and tries once.
13. **Entries of a rule, written to the thread of the decision it came
    from: `rule_landed` and `rule_published` (with the commit),
    `rule_discarded`, and `rule_failed`, which alone marks the thread
    unread.** Why: the person asked for the rule and should learn that
    there is none.
14. **The session's prompt is nightshift's own and is not specified here;
    it names the target, an input file `rule` (the question, the answer
    and the comment as JSON) and the result file.** Why: the source PRD
    leaves the agents' prompts out.
15. **The brief's period starts at the last brief written, or 24 hours
    before when there is none; a brief left out does not move it.** Why:
    "since the previous brief".
16. **"No item changed" means: no item of any workspace was created or
    changed its state in the period.** Why: a state change is what the
    brief lists.
17. **At most one brief is made for a local day; one whose time passed
    while the daemon was stopped is made at the next start on that day, and
    a day that passed entirely is not made up.** Why: "once a day".
18. **`nightshift brief` is `GET /api/v1/brief`, and `nightshift stats` is
    `GET /api/v1/stats`.** Why: the client speaks only the daemon's API,
    and the source PRD names the commands alone.
19. **`nightshift brief` prints a brief also when the daily one would be
    left out.** Why: the person asked.
20. **Every registered workspace has a block in the brief, and its empty
    lists are left out.** Why: the tokens and the closing numbers are
    there for each.
21. **"Rework" is read from the session records: an implementer session
    started after the item's first completed implementer session ended.**
    Why: package 06 asks a rework session with the step name `implement`,
    as a first one, so the records do not name a rework, and a first
    session that failed and was started once more is not one.
22. **"Reverted" is read from the item's thread: an entry of kind `state`
    whose text begins `verifying -> awaiting_approval`.** Why: package 03
    allows that transition for the revert alone and writes that entry;
    package 08's entry `revert` marks the start of a revert, which can
    also end in `main_red` without the item coming back.
23. **"Taken over" is read from the item's thread: an entry of kind
    `taken_over`, which package 09 writes for each take-over, by the
    command or by a decision's option.** Why: the marker `taken over` shows
    only the present.
24. **The four counts print `0`; the six derived numbers print `-` when
    their base is empty.** Why: zero done items is a number; a median of
    nothing is not.
25. **A workspace that is not registered is `404 not_found` for `stats`;
    `days` outside 1 to 365 is exit code 2 at the client and `400
    invalid_request` at the API.**
26. **`[brief].at` is `HH:MM` on the 24-hour clock; anything else fails the
    `config` check with `config: brief.at must be HH:MM`.**
27. **"The plan is approved" is the item becoming `ready` from `planning`
    or `awaiting_approval`, each time it does.** Why: it covers an approval
    at creation, and a plan approved again after a revert names a new
    change.
28. **The comment and the close are two calls: the comment with an
    `Idempotency-Key`, then the close without a comment.** Why: the hub's
    close route posts its comment again when it is repeated; a comment with
    a key is posted once, and closing a closed issue succeeds.
29. **The commit named at `done` is the commit the item's change landed
    as, `LandedSHA` of package 07's `land.Service.Landing`; the change
    named at the approval is `Change` of package 05's
    `change.Service.Get`.** Why: `done` means that commit is on origin, and
    both are recorded already.
30. **A forge call is tried when it arises and then every 10 minutes; one
    that still fails 24 hours after its first try is given up with the
    entry `forge_failed`, `could not write to issue #<N>: <error>`, which
    does not mark the thread unread. A call that cannot be sent because the
    hub is unavailable counts as failed.** Why: the source PRD gives the
    numbers and "noted once".
31. **The writes of one item go in order: a later one is sent only when
    the earlier ones were taken or given up.** Why: a `done` comment before
    the approval's reads wrong.
32. **At a start, an item imported from an issue that is `done` or
    `abandoned` and has no write recorded for that state gets one.** Why:
    a hook that was lost to a crash must not lose the comment.

33. **The brief's line of a done item repeats package 08's `published`
    entry, and its files outside the plan come from package 06's
    `OutsidePlan`.** Why: the entry holds the change, the commit and the
    tokens; package 05's record of an item's change can be forgotten, and
    the `outside_plan` entry is not written for every version.
34. **The sync before a rule's landing is package 08's
    `publish.Service.Sync`.** Why: it runs the sync and does what its
    result starts (the health command for `fast_forward`, `held` for
    `diverged`), returning an error while nothing may land.

## Dependencies

- **`docs/prd/02-drive-work-items-through-the-hub.md`**: the source PRD,
  of which this package specifies *Decisions written back as rules*, *The
  daily brief* and *Writing back to the forge*.
- **`01_daemon_api_and_credentials`** (status `draft`): the configuration
  struct, the store's migrations and test template, the API server's
  `Handle`, the client's `Register`, the event log, the hub client with
  `Available`, `internal/hubtest` and the end-to-end helper.
- **`02_workspaces_and_policy`** (status `draft`): `workspace.Service`
  with `Get` and `List` (the state, the daily limit).
- **`03_work_items_and_inbox`** (status `draft`): `internal/item` (`List`,
  the item's source, `OnChange`), `internal/inbox` (`RegisterKind`,
  `OnAnswer`, `Open`, `Append`, `BriefThread`, the entries of a thread, the
  decisions and their answers with `record`), `internal/notify`
  (`Notify`).
- **`04_sessions_and_scheduling`** (status `draft`): `session.Spec` and
  `session.Result`, `Scheduler.RunSession`, `Scheduler.Waiting`, the pause
  flags, the session records.
- **`05_planning`** (status `draft`): `change.Service.Get` (an item's
  `Record` with `Change`), and the hub client's `CreateFork`, `OpenChange`
  and `AbandonChange`. Its `change.Service` is bound to an item and is not
  used for a rule.
- **`06_implementation_and_review`** (status `draft`): the hub client's
  `Change`, `ReportCheck`, `File` and `Tree`, and
  `(*impl.Service).OutsidePlan`.
- **`07_landing`** (status `draft`): `land.Service.LandChange` and
  `land.Service.Landing`.
- **`08_health_and_publishing`** (status `draft`):
  `health.Service.VerifyTip`, `health.TipPending`, `publish.Service.Sync`,
  the hub client's `Push`, and the thread entry `published`. This package
  adds `publish.Service.PushTip`.
- **`09_notes_and_take_over`** (status `draft`): the marker `taken over`
  and the thread entry of kind `taken_over`.
- **Parley** (`agent-fox-dev/parley`), the hub: its packages
  `15_task_fork_creation`, `22_change_opening_and_versions`,
  `23_change_lifecycle_and_mergeability`, `25_optimistic_landing`,
  `28_approvals_and_checks` and `42_forge_writes`, each with every task
  `done` on 2026-11-27.

## Verified External API

- **Parley's REST API**, read in `docs/api.md` of `agent-fox-dev/parley`
  at commit `7eabfb9`:
  - `POST /api/v1/workspaces/{slug}/forks` with `name`, `ttl`,
    `auto_change`, `purpose`, `session_id`; `201` with `workspace` and
    `remote`; `409 conflict` for a name the parent has a fork of; scope
    `workspaces:write`.
  - `GET …/scm/tree` (`ref`, `path`; entries with `path`, `type`), `GET
    …/scm/file` (`path`, `ref`; `404 path_not_found`), `GET …/scm/diff`
    (`base`, `head`, `mode`; `files` of file changes with `path`,
    `status`, `insertions`, `deletions`, `patch`); scope `git:read` for
    the tree. **Not read:** what the tree route answers for a `path` that
    does not exist.
  - `POST …/changes` with `source {workspace_slug, branch}`, `title`,
    `intent`, `draft`; `201` with the change object (`number`, `version`,
    `head_sha`, `state`, `paths`, `landed`); `409 change_exists`. `GET
    …/changes/{number}`; `GET …/changes/{number}/diff` (the same file
    changes for a version); `POST …/changes/{number}/abandon` (`409
    change_closed`).
  - `PUT …/changes/{number}/checks/{name}` with `status`, `version`,
    `summary` (at most 2,000 characters); scope `checks:write`.
  - `POST …/changes/{number}/land` with `expected_version`; `200` with
    the attempt (`status`, `landed_sha`), `202` while it runs, `409
    land_conflict`, `409 policy_blocked`, `409 version_mismatch`, `409
    change_not_open`, `503 land_failed`.
  - `POST …/issues/{number}/comments` with `{"body"}`, `201` with
    `{"url"}`, honouring `Idempotency-Key` (a replay answers `200`); `POST
    …/issues/{number}/close` with an optional `{"comment"}`, `200` with
    the issue, `Idempotency-Key` ignored, closing a closed issue succeeds;
    scope `forge:write`; the forge errors `forge_unknown`,
    `forge_credential_missing`, `forge_rejected` (`422`),
    `forge_not_found` (`404`), `forge_unauthenticated`, `forge_forbidden`,
    `forge_unreachable`, `forge_error` (`502`), `forge_rate_limited`
    (`429`), `forge_timeout` (`504`).
- **Packages 01 to 09**, read in `.specs/` as drafts, as a coder receives
  them: the names under *Dependencies*. Two things this package needs do
  not exist there and are added by its tasks, each said to be new: the hub
  client's `Diff` for `GET …/scm/diff`, and `publish.Service.PushTip`.

## Glossary

- **rule**: a decision's answer written into the repository as an ADR, a
  steering line or a file under `docs/decisions/`. Not a policy rule, which
  is Parley's.
- **recorded answer**: an answer to a decision given with `--record`.
- **target**: where a rule is written: `adr`, `steering` or `decisions`.
- **scope**: what a session that belongs to no item is asked with: a
  workspace, a thread and a key.
- **brief**: the one entry a day in the brief thread that says what
  finished, what waits and what was spent.
- **period**: the time a brief covers, from the previous brief to the
  moment it is made.
- **outcome numbers**: the ten numbers per workspace that `nightshift
  stats` prints and the brief ends with.
- **first pass**: a done item that needed no rework session.
- **rework session**: an implementer session started after the item's
  first completed implementer session ended.
- **write-back**: a comment on, or the closing of, the issue an item was
  imported from.
- **hub**: a Parley server (`parleyd`).
- **task fork, change, version, check, land**: as Parley defines them
  (`docs/api.md`).
