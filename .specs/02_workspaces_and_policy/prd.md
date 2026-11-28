---
spec_id: "02"
spec_name: "workspaces_and_policy"
title: "Register workspaces and mark their sensitive paths"
status: "active"
created_at: "2026-11-28T10:00:31Z"
intent_hash: "044f353a6695ce79fc237c2fb168b7917b116760ff9b9fe0611109aecee531c3"
contract_hash:
  requirements.json: "4be196f8f2e46818d626b700a74ffa960f5b3b11add4ae2f4f778e4c44e10fab"
  tasks.json: "14de0df993c39d19696d992d55a545838360152722fd878e09173b0bf8deb54a"
schema_version: 3
source: "docs/prd/02-drive-work-items-through-the-hub.md"
---

# Register workspaces and mark their sensitive paths

## Intent

nightshift works only in Parley workspaces the person has handed to it, and
it lets a change land without the person only where the hub's landing
policy says so. A person registers a workspace they own after nightshift
has checked that it can be worked in (it follows origin, its policy counts
nightshift's three checks, its health command passes), marks the paths that
need them as rules of that policy, and reads and changes each workspace's
settings and state through the `nightshift` client.

## Goals

- **A workspace is registered only when it can be worked in.** Five checks
  run in a fixed order; each failure refuses the registration with the
  reason and stores nothing.
- **The policy counts nightshift's checks.** `nightshift-gate`,
  `nightshift-review` and `nightshift-release` are required, on the version
  itself, wherever a change may land; `--adopt-policy` writes that.
- **The person marks sensitive paths with one command.** `workspace
  protect` writes a rule at the top of the hub's policy that requires an
  approval of the owner or blocks; `workspace unprotect` removes a rule
  nightshift wrote and no other.
- **The person is told when nothing is marked.** A registration prints one
  warning line when no rule requires an approval or blocks.
- **Settings and state are readable and changeable.** `workspace show`,
  `set`, `list` and `remove`, and the `workspaces` member of the status.
- **A workspace has a state, `green`, `red` or `held`,** that is stored,
  shown, survives a restart and is set through one function.
- **Commands and clones other packages reuse.** One way to run a shell
  command in a directory with a timeout and a kept tail of output, and one
  way to bring a clone of a hub repository to a commit.

## Non-goals

This package is the second of ten written from
`docs/prd/02-drive-work-items-through-the-hub.md`. Of this package's
neighbourhood, these belong to the others:

- **`01_daemon_api_and_credentials`**: the daemon, its configuration, the
  API server, the client's base, the hub client and its monitor, the event
  log. This package builds on them.
- **`03_work_items_and_inbox`**: the counts of open work items in
  `workspace list`; that `workspace remove` is refused while a work item is
  neither `done` nor `abandoned`, and that the workspace's finished threads
  are removed. This package offers the guard and the hook they are made
  with. Threads, decisions and the notification command are that
  package's.
- **`04_sessions_and_scheduling`**: `workspace pause` and `resume`, the
  `paused` marker in `workspace list`, the count of tokens used today (this
  package shows `0` until a source is registered), the enforcement of the
  daily limit, the configuration key `[limits].daily_tokens`, and the
  session user that commands run as.
- **`05_planning`**: the policy rules a plan's files would select.
- **`06_implementation_and_review`**: the gate, which runs through this
  package's `command`.
- **`07_landing`**: that nothing lands in a `red` or `held` workspace, the
  recheck of the policy before a landing, the `policy` decision, and what
  turns a workspace `held` by policy.
- **`08_health_and_publishing`**: the health command after a landing, what
  turns a workspace `red`, and `held` by origin.
- **`09_notes_and_take_over`** and **`10_rules_brief_and_forge`**: nothing
  of this neighbourhood.

Also out: creating a workspace on the hub, changing a workspace's settings
on the hub, and any event on the event stream (the stream carries changes
of threads, decisions and work items; a workspace is none of them).

## Background

Package 01 leaves a daemon that starts, serves an authenticated API under
`/api/v1`, and holds a hub client with the worker and the approver token.
No workspace is known to it. Every command, route, file and Go package this
document names for workspaces is new.

What this package uses of Parley, as its `docs/api.md` states at commit
`7eabfb9`:

- `GET /api/v1/workspaces/{slug}` answers the workspace object, with
  `kind`, `default_branch`, `settings`, `hub_url` and `access`, the
  caller's level: `owner`, `member` or `admin`. A workspace the caller
  cannot read is `404 not_found`, the same as one that does not exist.
- `POST /api/v1/workspaces/{slug}/sync` brings in origin's commits and
  answers the workspace object plus `branches`, each with a `result`.
- The landing policy is one JSON document with a `default` requirement and
  ordered `rules`; `GET` answers it with its revision in `ETag`, `PUT`
  replaces it and honours `If-Match`.
- A workspace is a git remote over smart HTTP at its `hub_url`; git
  authenticates with HTTP Basic, the credential as the password.
- `GET /api/v1/user` needs the scope `users:read`, which the worker token
  does not hold.

## Requirements

Constraint: Linux and macOS; Windows is best effort. Constraint: the
client speaks only the daemon's HTTP API. Constraint: `git` and `sh` are
installed on the daemon's machine.

### Registering a workspace

`nightshift workspace add <slug> --health "<command>"` registers a Parley
workspace the person owns. Options: `--health-timeout <duration>` (default
`30m`), `--daily-tokens <N>` (default 50,000,000 in this package; package
04 makes the default `[limits].daily_tokens`) and `--adopt-policy`.

Registration checks these, in this order, and each failure refuses the
registration with exit code 1, the reason on stderr, and nothing stored:

1. the workspace exists on the hub and the worker token's user owns it
   (`workspace not found`, `not the owner of <slug>`);
2. its setting `sync_enabled` is on (`sync is disabled for <slug>`);
3. a sync from origin succeeds and reports the default branch as
   `up_to_date`, `fast_forward` or `created` (`default branch is ahead of
   origin`, `default branch has diverged from origin`, or the hub's
   error);
4. its policy requires the three checks nightshift reports,
   `nightshift-gate`, `nightshift-review` and `nightshift-release`, counted
   only on the version itself (`fresh_checks`), in `default` and in every
   rule that does not block. When it does not, the refusal lists the rules
   that lack them and names `--adopt-policy`; with that option nightshift
   adds the three names and `fresh_checks` to those rules, writes the
   policy, and reports the new revision;
5. the health command exits 0 on the tip of the default branch, within the
   timeout (`health command exited <N>` or `health command timed out`, with
   the last 50 lines of its output).

A registration that succeeds prints one warning line when the policy has no
rule that requires an approval or blocks: `no path of <slug> is marked
sensitive; every change lands without you`.

A registered workspace is `green`.

### Marking paths as sensitive

`nightshift workspace protect <slug> <glob>…` adds one rule to the top of
the workspace's policy, so that it is the first a matching file selects.
Without an option the rule requires one approval of the owner and the three
checks: a change touching the paths waits for the person. With `--block`
the rule blocks: such a change never lands. `--name <rule>` names the rule;
otherwise it is `nightshift-<n>` with the next free number. The command
prints the rule and the policy's new revision. It exits 2 for a glob the
hub's policy format refuses, and 1 with `policy changed meanwhile; run the
command again` when the policy was replaced between nightshift's read and
its write. `nightshift workspace unprotect <slug> <rule>` removes a rule
that `protect` wrote, and exits 1 with `not a rule nightshift wrote` for
another.

### Reading and changing a workspace

`nightshift workspace show <slug>` prints the workspace's state, health
command, daily limit, tokens used today and the policy's rules that require
an approval or block, each with its paths. `nightshift workspace set <slug>
[--health "<command>"] [--health-timeout <duration>] [--daily-tokens <N>]`
changes those values from the next use on.

A workspace is `green`, `red` (its last health command failed) or `held`
(an `origin` or `policy` decision is open). This package stores the state,
shows it and offers the function that sets it; packages 07 and 08 call it.

`nightshift workspace list` shows each workspace with its state, its tokens
used today against its limit and its health settings. `nightshift workspace
remove <slug>` removes the workspace from nightshift, and nothing on the
hub is changed; it is refused with exit code 1 while a guard another
package registered refuses it.

`GET /api/v1/status` reports each workspace's state and tokens used today
in its `workspaces` member, and `nightshift status` prints one line for
each.

### The daemon's routes

| Route | Command | Answer |
|---|---|---|
| `POST /api/v1/workspaces` | `workspace add` | `201` with the workspace object, `policy_revision` and `warning` |
| `GET /api/v1/workspaces` | `workspace list` | `200` `{"workspaces": […]}`, ordered by slug |
| `GET /api/v1/workspaces/{slug}` | `workspace show` | `200` with the workspace object and `sensitive_rules` |
| `PATCH /api/v1/workspaces/{slug}` | `workspace set` | `200` with the workspace object |
| `DELETE /api/v1/workspaces/{slug}` | `workspace remove` | `204` |
| `POST /api/v1/workspaces/{slug}/policy/rules` | `workspace protect` | `201` `{"rule", "revision"}` |
| `DELETE /api/v1/workspaces/{slug}/policy/rules/{name}` | `workspace unprotect` | `200` `{"revision"}` |

The workspace object:

```json
{"slug": "demo", "state": "green", "health_command": "make check",
 "health_timeout": "30m0s", "daily_tokens": 50000000,
 "tokens_used_today": 0, "created_at": "2026-11-28T10:00:00Z"}
```

Errors are `{"error_type", "message"}`: `400 invalid_request` for a body
the route cannot use, `400 invalid_glob` for a refused glob, `404
not_found` for a slug that is not registered, `409 conflict` for a refused
registration, protection or removal (the reason as `message`; a failed
health command adds the member `output`, its last 50 lines), and `503
hub_unavailable` when the hub cannot be asked.

### What the client prints

```text
$ nightshift workspace add demo --health "make check" --adopt-policy
registered demo
policy adopted at revision 3
no path of demo is marked sensitive; every change lands without you

$ nightshift workspace protect demo "deploy/**"
rule nightshift-1 (approval): deploy/**
policy revision 4

$ nightshift workspace show demo
demo: green
health: make check (timeout 30m0s)
daily limit: 50000000 tokens, 0 used today
sensitive paths:
  nightshift-1 approval: deploy/**

$ nightshift workspace list
demo  green  0/50000000 tokens  health: make check (30m0s)
```

The warning line goes to stderr. `show` prints `sensitive paths: none`
when no rule requires an approval or blocks, and `default approval: every
other path` for a `default` that does. `list` prints `no workspaces` when
none is registered. `set` prints `updated <slug>`, `remove` prints
`removed <slug>`, `unprotect` prints `removed rule <name>` and `policy
revision <N>`. `nightshift status` adds `workspace <slug>: <state>, <N>
tokens today` per workspace. With `--json` every command prints the API's
JSON.

### Running commands and cloning

The health command is a shell command line. nightshift runs it with `sh
-c` in a clone of the workspace, taken from the hub's git endpoint and
brought to the commit in question, and keeps the last lines of what the
command wrote to its standard output and standard error together. A
command that runs longer than its timeout is ended with every process it
started. The operating-system user a command runs as is the daemon's in
this package; package 04 plugs in the session user.

### End-to-end flows

**Registering a workspace and adopting the policy.** The person runs
`nightshift workspace add demo --health "test -f README.md"
--adopt-policy`. `nightshiftd` reads the workspace from the hub and finds
the worker token's user its owner, finds `sync_enabled` on, syncs and reads
the default branch as `up_to_date`, finds that `default` lacks the three
checks, clones the workspace at the branch's tip and runs the health
command, which exits 0, then writes the policy with the three checks and
`fresh_checks` and stores the workspace as `green`. The client prints
`registered demo`, the new revision and the warning line. `nightshift
status` shows `workspace demo: green, 0 tokens today`.

**Marking a path, reading it back and unmarking it.** The person runs
`nightshift workspace protect demo "deploy/**"`. `nightshiftd` reads the
policy, puts the rule `nightshift-1` at its top and writes it with the
revision it read. `nightshift workspace show demo` lists `nightshift-1`
with `deploy/**`. `nightshift workspace unprotect demo nightshift-1`
removes the rule; `show` prints `sensitive paths: none`.

**A registration that is refused, and a removal.** The person registers
`demo` with a health command that exits 3. The client exits 1 with `health
command exited 3` and the command's output; `nightshift workspace list`
prints `no workspaces`, and the hub's policy is unchanged. The person
registers it again with a passing command, changes the limit with
`workspace set`, and removes it with `workspace remove`; the hub still
holds the workspace and its policy.

## Design Decisions

From the source PRD, by its numbers:

- **2. Sensitive paths are the hub's policy rules, and everything else
  lands without the person.** The owner decided it. nightshift keeps no
  path list of its own.
- **5. The spend limit is one daily token limit per workspace.** The owner
  decided it. This package stores the limit; package 04 enforces it.
- **7. nightshift offers `workspace protect`.** The owner decided it.
- **21. One health command per workspace.** Kept from PRD 01.
- **22. A third check, `nightshift-release`.** Chosen there; registration
  demands it with the other two.
- **23. The policy must count the checks on the version itself
  (`fresh_checks`).** Chosen there.
- **41. The gate and the health command run as the session user.** Chosen
  there; this package makes the user pluggable and package 04 plugs it in.

Chosen here:

1. **The owner is recognised by `access: "owner"` on the hub's workspace
   object, read with the worker token.** Why: `GET /api/v1/user` needs
   `users:read`, which the worker token does not hold, and `access` is the
   hub's own answer to the question. `404` is `workspace not found`; any
   other `access` is `not the owner of <slug>`.
2. **A task fork or a workspace that is not ready is refused by the sync,
   with the hub's error.** Why: the hub answers `400 fork_has_no_origin`
   and `409 workspace_busy` there, and the source PRD names "the hub's
   error" for that check. A hub error is printed as `hub answered <status>
   <error_type>: <message>`.
3. **A default branch the sync reports with another result (`reset`,
   `name_conflict`), or not at all, is refused with `default branch: sync
   reported <result>` or `default branch <name> is not on origin`.** Why:
   the source PRD names three results that pass and two that fail.
4. **With `--adopt-policy` the policy is written after the health command
   passed, as the last step before the workspace is stored.** Why: a
   refused registration then leaves the hub's policy as it was. The check
   itself keeps its fourth place.
5. **Adopting keeps the checks a rule already names, appends the missing
   ones in the order gate, review, release, and sets `fresh_checks`.** Why:
   the person's own checks stay required.
6. **A policy replaced between the read and the adopting write refuses the
   registration with `policy changed meanwhile; run the command again`.**
   Why: the source PRD's answer for `protect`, for the same race.
7. **A rule is sensitive when its requirement has `approvals` of 1 or more
   or `block`; `default` counts, and is shown as `default`.** Why: a
   `default` that requires an approval makes every other path wait for the
   person.
8. **nightshift records the names of the rules `protect` wrote in its
   store, per workspace.** Why: a policy rule has no member that says who
   wrote it, and `--name` allows any name. `unprotect` of a recorded rule
   the policy no longer holds forgets the record, writes nothing and exits
   0.
9. **The next free number of `nightshift-<n>` is one above the highest `n`
   among the policy's rules of that form, 1 when there is none.**
10. **A glob is checked by the daemon before the hub is asked (empty, over
    255 characters, a leading `/`), and a `400 invalid_policy` of the hub
    whose `details.field` begins with `paths` is the same refusal.** Both
    are `400 invalid_glob`, which the client turns into exit code 2. Why:
    the source PRD's exit code for a refused glob, with the hub's format as
    the authority.
11. **A rule name that is taken, or that the policy format refuses, is
    refused with exit code 1** (`409 conflict`, `rule <name> exists
    already`; `400 invalid_request`). Why: the source PRD names exit code 2
    for globs only.
12. **Every command has a route** (the table under *The daemon's
    routes*). A refusal is `409 conflict`; the hub being unavailable is
    `503 hub_unavailable`.
13. **A registration is one request that stays open until it is decided;
    the client sets no deadline for it.** When the client goes away the
    health command is ended and nothing is stored. Why: the health command
    may run for 30 minutes, and a job to poll would be a second mechanism.
14. **Two registrations of one slug at once: the second is refused with
    `registration of <slug> is in progress`.**
15. **`show`, `protect` and `unprotect` need the hub and answer `503
    hub_unavailable` without it; `list`, `set`, `remove` and the status do
    not ask the hub.**
16. **`workspace set` runs no check.** Why: the source PRD says the values
    apply "from the next use on". `--daily-tokens` is 1 or more, a timeout
    is greater than zero.
17. **`--daily-tokens` defaults to the service's `DefaultDailyTokens`,
    50,000,000 here, resolved and stored at registration.** Why:
    `[limits].daily_tokens` is package 04's key; it sets the value from it.
18. **Tokens used today come from a source another package registers, and
    are `0` without one.**
19. **Removal asks registered guards in order and is refused by the first
    that returns an error; after it, registered hooks are called.** It
    removes the workspace's row, its rule records and its clone. Why: the
    refusal for open work items and the removal of threads are package
    03's.
20. **Workspace clones live under the new configuration key `clones_dir`,
    default `~/.nightshift/clones`, one directory per slug, kept and
    fetched at the next use; the git URL is the hub's `hub_url`, stored at
    registration, and a `null` one refuses the registration (`cannot clone
    <slug>: the hub reports no git URL`).** Why: a clone per health run would fetch the
    whole repository each time.
21. **A clone is brought to a commit detached, with untracked and ignored
    files removed.** Why: a health command must see the commit and nothing
    an earlier run left.
22. **The credential reaches git through the process environment (an
    `http.extraHeader` given with `GIT_CONFIG_COUNT`), never the command
    line, the remote's URL or the clone's configuration.**
23. **A command's kept output is its last N lines of standard output and
    standard error together, each line cut at 4,096 bytes; on a timeout the
    process group is killed.**
24. **The state setter appends no event.** Why: the source PRD's stream
    carries changes of threads, decisions and work items; packages 07 and
    08 write the thread entry when they set a state.
25. **`nightshift status` prints one line per workspace.** Why: the status
    member exists for a person to read.

## Dependencies

- **`01_daemon_api_and_credentials`** (status `draft`): the configuration
  struct, the store and its migrations, the hub client with the worker
  credential and `Available`, the fake hub `internal/hubtest`, the API
  server's `Handle` and helpers, the client's `Register` and its request
  function, the `workspaces` status source, and the e2e helper.
- **Parley** (`agent-fox-dev/parley`), the hub: its packages
  `12_workspace_sync_from_origin`, `17_git_smart_http` and
  `27_landing_policy`, each with every task `done` on 2026-11-27.
- **`git`** and **`sh`** on the daemon's machine.

## Verified External API

Parley's REST API, read in `docs/api.md` of `agent-fox-dev/parley` at
commit `7eabfb9`:

- `GET /api/v1/workspaces/{slug}`, scope `workspaces:read` (satisfied by
  `workspaces:write`): the workspace object with `slug`, `kind`
  (`repository` or `fork`), `default_branch`, `owner_id`, `org_slug`,
  `status`, `head_sha`, `settings` (every setting with its effective
  value, `sync_enabled` among them), `hub_url` (`null` when the hub has no
  external URL) and `access` (`owner`, `member` or `admin`); `404
  not_found` for a workspace the caller cannot read.
- `POST /api/v1/workspaces/{slug}/sync`, scope `workspaces:write`, write
  access: `200` with the workspace object plus `branches`, each `{name,
  result, local_sha, origin_sha}` with `result` one of `created`,
  `up_to_date`, `fast_forward`, `ahead`, `diverged`, `reset`,
  `name_conflict`; `400 fork_has_no_origin`, `400 sync_disabled`, `502
  upstream_error`, `500 internal`, `409 workspace_busy`.
- `GET /api/v1/workspaces/{slug}/policy`, scope `changes:read` (satisfied
  by `changes:write`): `{revision, default, rules, updated_at,
  updated_by}` in normal form, the revision in `ETag` in double quotes.
- `PUT /api/v1/workspaces/{slug}/policy`, scope `workspaces:write`, owner
  only: the document in the body, `If-Match: <revision>`; `200` with the
  policy of the new revision; `400 invalid_policy` with `error.details`
  `{rule, index, field}`; `412 revision_mismatch`.
- The policy document: `default` and `rules` (at most 200); a rule has
  `name` (lower-case letters, digits, `_`, `-`, at most 63 characters,
  unique, not `default`), `paths`, `exclude`, `targets` and `require`; a
  requirement has `approvals` (0 to 10), `approvers` (`owner`, `member`,
  `user:<username>`), `checks`, `fresh_checks` and `block`. A path glob is
  1 to 255 characters and does not start with `/`; `*`, `**` and `?` are
  its wildcards. The first rule a file matches applies.
- Git over HTTP: the remote is `hub_url`,
  `<hub>/git/<org slug>/<workspace slug>.git`; HTTP Basic with the
  credential as the password and any user name; a PAT needs `git:read` to
  fetch, which `git:write` includes.
- `GET /api/v1/user` needs `users:read`.

Not read: the exact text of `error.details.field` for a refused glob
(`paths[0]` is assumed from the document's example `approvers[0]`).

## Glossary

- **workspace**: a Parley workspace of kind `repository` that is
  registered with nightshift.
- **registration**: the five checks and the stored record that make a
  workspace nightshift's to work in.
- **the three checks**: `nightshift-gate`, `nightshift-review` and
  `nightshift-release`, which nightshift reports on a version of a change.
- **adopt**: to add the three checks and `fresh_checks` to the policy's
  `default` and to every rule that does not block.
- **sensitive path**: a path a rule of the workspace's landing policy
  marks with an approval or a block.
- **health command**: the command a workspace configures that must exit 0
  on the default branch.
- **daily limit**: the tokens a workspace's sessions may use between two
  midnights.
- **green, red, held**: the stored state of a workspace.
- **guard**: a function another package registers that may refuse the
  removal of a workspace.
- **tail**: the last lines of a command's output that nightshift keeps.
- **hub, worker token**: as package 01 defines them.
- **policy, rule, requirement, revision, sync**: as Parley defines them
  (`docs/api.md`).
