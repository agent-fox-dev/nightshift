---
spec_id: "01"
spec_name: "daemon_api_and_credentials"
title: "Run the nightshift daemon with its API and hub credentials"
status: "active"
created_at: "2026-11-28T09:50:59Z"
intent_hash: "300e88a39f804538ea78756bdbef9ccf31da680532a44175d064c09401f84fc0"
contract_hash:
  requirements.json: "52a9abc36ae66ae8e202165c4a2eab66f347266d8490f375a866534731c2c655"
  tasks.json: "8d4d5d6111728360c5bd45c562e0e0993b1159740195c0a37a2d3f9ec100dce4"
schema_version: 3
source: "docs/prd/02-drive-work-items-through-the-hub.md"
---

# Run the nightshift daemon with its API and hub credentials

## Intent

nightshift is the process that owns a project's work on a Parley hub, and a
person steers it through an API. Before it can drive any work it has to
exist as a daemon a person can start, trust and ask: `nightshiftd` starts
only when its configuration, the hub and its two hub credentials are in
order, serves an authenticated HTTP API with a status and a resumable event
stream to the `nightshift` client, keeps running through a hub outage, and
stops cleanly on a signal.

## Goals

- **A daemon that refuses to start half-configured.** `nightshiftd` checks
  its configuration, the hub and the two credentials in that order, and a
  failed check ends it with exit code 1 and one line naming the check and
  the reason.
- **Two credentials with separate powers.** The worker token cannot approve
  and the approver token is another token; a start with anything else is
  refused.
- **Only the person's client reaches the API.** Every request under
  `/api/v1` carries the client token the daemon wrote at its first start.
- **A status a person can read.** `GET /api/v1/status` and `nightshift
  status` report the version, the hub's endpoint and whether it is
  reachable.
- **An event stream a client can resume.** `GET /api/v1/events` delivers
  every event with an increasing `seq`, replays from `Last-Event-ID`, and
  says `410 cursor_expired` when the cursor is older than what is kept (7
  days).
- **It survives a hub outage and a restart.** While the hub is unreachable
  the daemon keeps running, tries again every 30 seconds and continues by
  itself; events and their `seq` survive a restart.
- **It stops cleanly.** On `SIGTERM` or `SIGINT` it exits with code 0
  within 30 seconds.

## Non-goals

This package is the first of ten written from
`docs/prd/02-drive-work-items-through-the-hub.md`. The other scopes are
packages of their own, implemented in this order:

- **`02_workspaces_and_policy`**: `nightshift workspace …`, the policy
  commands, the states `green`, `red` and `held`, and the `workspaces`
  member of the status.
- **`03_work_items_and_inbox`**: work items, threads, decisions, the inbox
  routes and commands, `nightshift watch`, the notification command, and the
  `Unread` entries this PRD names for the hub's outage and for a token the
  hub stopped accepting. This package only keeps the facts those entries
  are written from.
- **`04_sessions_and_scheduling`**: the roles and the start checks `roles`
  and `sessions`, the session user, a session's own hub token, ending and
  recording sessions at shutdown, the `sessions` member of the status,
  scheduling, the daily limit and pausing.
- **`05_planning`**, **`06_implementation_and_review`**, **`07_landing`**,
  **`08_health_and_publishing`**, **`09_notes_and_take_over`**,
  **`10_rules_brief_and_forge`**.

Also out, for every package of that PRD: hosting code, landing or policy
(Parley's), working without a hub, a web or desktop client, an MCP facade,
and a team of several people.

No event is produced by this package itself: the stream carries the changes
of threads, decisions and work items, and the first of those arrive with
package 03. This package offers the log they are appended to.

## Background

The repository holds its `README.md`, the agent instructions, the steering
and two PRDs. There is no Go module, no `Makefile` and no code: every
command, route, file and package this document names is new.

Parley (`agent-fox-dev/parley`) is the hub. What this package uses of it,
as its `docs/api.md` states at commit `7eabfb9`:

- `GET /readyz` needs no credential and answers `200` when the hub can do
  its work.
- A personal access token (PAT) has the form
  `parley_pat_<token_id>_<secret>` and carries scopes in `permissions`.
  `GET /api/v1/user/tokens/{token_id}` answers the Token object of one of
  the caller's own tokens (`permissions`, `workspace`, `expires_at`,
  `revoked_at`) and needs the scope `tokens:read`, which `tokens:manage`
  satisfies.
- The hub checks a credential before a scope: a request with a token it
  does not accept is `401 unauthenticated`; one with an accepted token that
  lacks the route's scope is `403 insufficient_scope`; one with a token
  bound to a workspace on a route under `/api/v1/user` is `403
  token_bound`.

The stream follows Parley's own event cursor and Pizza Bot's inbox
(`github.com/agent-fox-dev/pizza-bot`): thin clients over HTTP and
server-sent events, with work that goes on when no client is connected.

## Requirements

Constraint: Linux and macOS; Windows is best effort. Constraint: Go, built
as two programs, `nightshiftd` (the daemon) and `nightshift` (the client),
which speaks only the daemon's HTTP API. Constraint: `nightshiftd` is one
process holding the controller, its store and the API.

### Configuration and start

`nightshiftd` reads one configuration file, `~/.nightshift/config.toml`
unless `--config` names another. In this package it holds:

| Key | Meaning | Default |
|---|---|---|
| `listen` | the address the API listens on | `127.0.0.1:7878` |
| `token_file` | where the client token is kept | `~/.nightshift/token` |
| `[store].path` | the daemon's store, one SQLite file | `~/.nightshift/store.db` |
| `[hub].endpoint` | the hub's base URL | none, required |
| `[hub].worker_token` | the worker token | none, required |
| `[hub].approver_token` | the approver token | none, required |

A leading `~/` in a path is the home directory of the daemon's user.

At start it checks, in this order, that the configuration loads
(`config`), that the hub answers (`hub`) and that the credentials are
accepted and fit their purpose (`credentials`). A check that fails ends the
process with exit code 1 and one line on stderr naming the check and the
reason; no later check runs and nothing is started: no listener, no store,
no token file.

- `config` fails when the file is missing or unreadable, is not valid TOML,
  holds a key the daemon does not know (`config: unknown key
  "hub.endpont"`), or lacks a required key (`config: hub.endpoint is not
  set`).
- `hub` fails when `GET <endpoint>/readyz` does not answer `200` within 10
  seconds: `hub: connection refused` for a refused connection, `hub: readyz
  answered 503` for another status, `hub: timeout` for no answer.

After the checks the daemon opens its store, writes the client token where
none exists, and listens. A store that cannot be opened, a token file that
cannot be written or an address that cannot be bound ends the process the
same way (`store: …`, `token: …`, `listen: …`).

### Credentials

The configuration holds two personal access tokens of the person's Parley
account, which the person mints on the hub:

- the **worker token**, which nightshift uses for everything it does
  itself. It holds the scopes `workspaces:write`, `git:write`, `git:read`,
  `changes:write`, `checks:write`, `tokens:manage`, `forge:write`,
  `sessions:write` and `audit:read`, and is not bound to a workspace;
- the **approver token**, which nightshift uses only to record the person's
  verdict when they answer an `approval` decision. It holds `changes:read`
  and `changes:approve`.

The start check `credentials` fails, with these reasons after
`credentials: `:

- a token is not of the form `parley_pat_<token_id>_<secret>` (`worker
  token is not a personal access token`);
- the two are the same token (`worker and approver are the same token`);
- the hub answers either token `401` (`worker token: 401 unauthenticated`);
- the worker token is bound to a workspace (`worker token is bound to a
  workspace`);
- the worker token holds `changes:approve` (`worker token must not hold
  changes:approve`);
- a token lacks a scope named above, by its exact name in the token's
  `permissions` (`worker token lacks checks:write`); the first missing one
  in the order above is named.

A token that the hub stops accepting while the daemon runs (expired or
revoked: the hub answers it `401`) is treated as the hub being unreachable
for what needs that token, and `status` names it (`worker token: 401
unauthenticated`). The daemon reads the configuration's credentials again
when it receives `SIGHUP` and runs the `credentials` check on them: tokens
that pass replace the ones in use and clear the reported error; tokens that
fail are not taken, the ones in use stay, and the reason is written to the
daemon's log.

### The client token and the API

On its first start the daemon writes a client token, 64 hexadecimal
characters from 32 random bytes, to the token file, readable and writable
by its owner only (mode `0600`). A later start keeps the token it finds.

The API lives under `/api/v1` and answers JSON. Every request carries the
client token as a bearer token; without it, or with another, the answer is
`401` with `error_type: unauthorized`, before the route is looked at. An
error is `{"error_type", "message"}` with a 4xx or 5xx status: `404
not_found` for a path that names no route, `405 method_not_allowed` for a
method the route does not have.

### Status

`GET /api/v1/status` reports:

```json
{"version": "dev",
 "hub": {"endpoint": "https://hub.example", "reachable": true,
         "unreachable_since": null, "credential_error": null},
 "sessions": [],
 "workspaces": []}
```

`sessions` and `workspaces` are empty lists in this package; packages 04
and 02 fill them.

`nightshift status` prints it for a terminal:

```text
nightshiftd dev
hub: https://hub.example reachable
```

with `hub: unreachable since <time>` on the second line while the hub is
unreachable, and a third line `<credential_error>` when there is one. With
`--json` it prints the API's JSON.

The client finds the daemon at `--addr`, else at `$NIGHTSHIFT_ADDR`, else
at `127.0.0.1:7878`, and reads the client token from `--token-file`, else
from `~/.nightshift/token`. Exit codes of every `nightshift` command: 0 on
success; 1 when the daemon refuses (the error's `message` on stderr) or
cannot be reached (`cannot reach nightshiftd at <address>`), or the token
file cannot be read (`cannot read the client token at <path>`); 2 for a
usage error (an unknown command or flag, a missing argument), with the
usage on stderr and no request sent.

### The event stream

The daemon keeps an event log in its store. An event has a `seq`, one
greater than the event before it and starting at 1, a `time`, a `type` and
a `data` object; the packages that produce events define their types.

`GET /api/v1/events` is a server-sent event stream: each event is sent as
`id: <seq>`, `event: <type>` and `data: <the event as JSON>`. A client that
connects without `Last-Event-ID` receives the events appended from then on.
A client that connects with `Last-Event-ID: <seq>` first receives every
event after that `seq`, in order, then the new ones, none missing and none
twice.

Events are kept for 7 days. When an event after the given `seq` is no
longer kept, the answer is `410` with `error_type: cursor_expired` and the
member `oldest_seq`. A `Last-Event-ID` that is not a non-negative integer,
or is greater than the newest `seq`, is `400` with `error_type:
invalid_request`.

A client that does not read falls behind: when 256 events wait for it, the
daemon closes its connection, and the client reconnects with its last
`seq`. Appending an event never waits for a client.

The log survives a restart: after it, `seq` goes on from the highest value
ever given, and a client that reconnects with its last `seq` receives what
it missed.

### Stopping, and the hub's outages

On `SIGTERM` or `SIGINT` the daemon stops accepting requests, closes the
event streams, runs the steps other packages registered for a shutdown
(package 04 ends its sessions there), closes the store and exits with code
0 within 30 seconds. A step that has not returned 25 seconds after the
signal is abandoned; the daemon exits with code 0 all the same.

**When the hub is unreachable** (a request to it fails with a connection
error or a timeout, or `/readyz` does not answer `200`), the daemon keeps
running. `status` reports `hub: unreachable` with the time of the first
failure. The daemon asks `/readyz` again every 30 seconds and continues by
itself when it answers `200`. Until then it answers "not available" to any
part of nightshift that asks whether work needing the hub may start, which
is how later packages start no session and land nothing meanwhile.

### End-to-end flows

**A first start and a status query.** The person writes a configuration and
starts `nightshiftd`. It reads the file, asks the hub's `/readyz`, reads
both tokens' scopes from the hub, opens the store, writes
`~/.nightshift/token` and listens. The person runs `nightshift status` and
reads the version and `hub: … reachable`.

**A client resumes the event stream after a restart.** A client is
connected to `/api/v1/events` and has received events up to a `seq`.
`nightshiftd` receives `SIGTERM` and exits with code 0. More events are
appended after the next start. The client reconnects with `Last-Event-ID`
and receives every event after its `seq`.

**The hub goes away and comes back.** The hub stops answering. The daemon
marks it unreachable; `nightshift status` prints `hub: unreachable since
<time>`. The hub answers again; at its next probe the daemon marks it
reachable and `nightshift status` prints `hub: … reachable`, with no
restart.

## Design Decisions

1. **Two personal access tokens, a worker and an approver, instead of the
   owner's API key.** The owner decided it (decision 8 of the source PRD).
2. **API and command-line client in version 1; one daemon, one person.**
   The owner decided it (decision 21 of the source PRD).
3. **This package is the first of ten.** The owner agreed to the split on
   2026-11-28: the source PRD's decision 32 with sessions ahead of planning
   and notes and taking over behind landing.
4. **The start checks `roles` and `sessions` belong to package 04.**
   Chosen here: they check the roles and the session user, which that
   package introduces; it adds them to the sequence after `credentials`.
5. **The hub "answers" when `GET /readyz` is `200` within 10 seconds.**
   Chosen here: it needs no credential, so the `hub` check does not
   overlap the `credentials` check.
6. **A token's scopes are read with the worker token from `GET
   /api/v1/user/tokens/{token_id}`; the approver token is accepted when the
   hub answers it anything but `401` on that route.** Chosen here: the
   approver token holds no scope that reads tokens, and the hub checks the
   credential before the scope.
7. **Scopes are compared by exact name.** Chosen here: the source PRD
   names the scopes each token holds, and the hub's own subset rule for
   minting counts exact names only.
8. **A token that is not a PAT, and a worker token bound to a workspace,
   fail the `credentials` check.** Chosen here: the source PRD requires
   both properties and names no reason for them.
9. **Credentials that fail the check at `SIGHUP` are not taken.** Chosen
   here: a daemon that is running should not lose working credentials to a
   typo.
10. **An unknown configuration key fails the `config` check.** Chosen here:
    a misspelled limit would otherwise be ignored silently.
11. **The store is one SQLite file, `~/.nightshift/store.db` unless
    configured, through `modernc.org/sqlite`.** Chosen here: one process,
    one person, no server to run; Parley uses the same driver.
12. **`token_file` is a configuration key.** Chosen here: a daemon started
    with `--config` elsewhere, and every test, needs the token somewhere
    other than the home directory.
13. **The client takes the daemon's address from `--addr` or
    `$NIGHTSHIFT_ADDR`, and does not read the daemon's configuration.**
    Chosen here: the configuration holds the hub credentials, which the
    client has no need to read.
14. **The API checks the client token before the route.** Chosen here: a
    caller without the token learns nothing about which routes exist.
15. **A stream without `Last-Event-ID` starts at the present.** Chosen
    here: a client that wants the past reads the threads; one that wants a
    replay sends a cursor.
16. **A client 256 events behind is disconnected.** Chosen here: appending
    an event must never wait for a reader, and a reconnect with the cursor
    loses nothing.
17. **Shutdown steps are abandoned 25 seconds after the signal.** Chosen
    here: it leaves 5 seconds to close the store inside the 30 the source
    PRD allows.
18. **Command lines are parsed with the standard library's `flag`
    package.** Chosen here: the steering prefers the standard library, and
    the client's commands are a fixed table.
20. **The worker token also holds `git:read`, which the source PRD does not
    list.** Chosen here: the hub lets a PAT mint only scopes it holds by
    exact name, and nightshift mints the reviewer's session token with
    `git:read` (package 04). Without it every review would fail.
19. **The `Unread` entries for an outage of 15 minutes and for a token the
    hub stopped accepting are written by package 03.** Chosen here:
    threads are that package's; this one keeps the time of the first
    failure and the credential error they are written from.

## Dependencies

- **`docs/prd/02-drive-work-items-through-the-hub.md`**: the source PRD,
  of which this package specifies *The daemon and its API* and
  *Credentials*, without the session token.
- **Parley** (`agent-fox-dev/parley`), the hub: its packages
  `06_api_keys_and_access_tokens` and `08_workspace_bound_tokens`, each
  with every task `done` on 2026-11-27.

## Verified External API

- **Parley's REST API**, read in `docs/api.md` of `agent-fox-dev/parley`
  at commit `7eabfb9`: `GET /readyz` (`200 {"status":"ready"}`, `503`); `GET
  /api/v1/user/tokens/{token_id}` answering a Token object with
  `token_id`, `permissions`, `workspace`, `expires_at`, `revoked_at`, scope
  `tokens:read`; the credential forms; the order of checks (`401
  unauthenticated` before `403 token_bound` before `403
  insufficient_scope`); the error envelope `{"error": {"code", "message",
  "error_type"}}`.
- **`github.com/BurntSushi/toml` v1.6.0**, read with `go doc`: `func
  DecodeFile(path string, v any) (MetaData, error)`, `func (md *MetaData)
  Undecoded() []Key`.
- **`modernc.org/sqlite` v1.60.1**, read with `go doc`: a `database/sql`
  driver; its registered driver name `sqlite` is assumed, not read.

## Glossary

- **hub**: a Parley server (`parleyd`).
- **worker token, approver token**: the two Parley personal access tokens
  nightshift holds: one for its own actions, one for the person's
  approvals.
- **client token**: the secret `nightshiftd` writes at its first start and
  the `nightshift` client sends with every request.
- **start check**: one of the named checks `nightshiftd` runs before it
  starts anything; a failure ends the process.
- **event**: one entry of the daemon's event log, with a `seq`, a `time`, a
  `type` and `data`.
- **cursor**: the `seq` a client sends as `Last-Event-ID` to receive the
  events after it.
- **store**: the daemon's SQLite file.
