# API

`nightshiftd` serves an HTTP API under `/api/v1` on its `listen` address
(default `127.0.0.1:7878`; see [configuration.md](configuration.md)). It
answers JSON, except for the event stream.

## Authentication

Every request carries the client token as a bearer token:

```
Authorization: Bearer <client token>
```

The client token is 64 lowercase hexadecimal characters from 32 random
bytes, written by the daemon at its first start to `token_file` (default
`~/.nightshift/token`) with mode `0600`, followed by a newline. A later
start keeps the token it finds. The token is compared in constant time, and
before the route is looked at, so the answer to a request without it is the
same for every path:

```
401 {"error_type": "unauthorized", "message": "the client token is missing or wrong"}
```

A token in a query parameter or a `Basic` header is not accepted.

## Errors

Every answer with a 4xx or 5xx status has `Content-Type: application/json`
and the body:

```json
{"error_type": "<type>", "message": "<text for a person>"}
```

| Status | `error_type` | When |
|---|---|---|
| 400 | `invalid_request` | a request the route cannot take |
| 400 | `invalid_glob` | a glob of `workspace protect` that is refused |
| 401 | `unauthorized` | the client token is missing or wrong |
| 404 | `not_found` | the path names no route, or a slug that is not registered |
| 405 | `method_not_allowed` | the route does not have the method |
| 409 | `conflict` | a registration, protection, unprotection or removal that is refused; the reason is the `message` |
| 410 | `cursor_expired` | the event stream's cursor is older than what is kept |
| 500 | `internal` | the request failed inside the daemon |
| 503 | `hub_unavailable` | a route that asks the hub, while the hub is unreachable or the worker token is refused |

## `GET /api/v1/status`

```json
{"version": "dev",
 "hub": {"endpoint": "https://hub.example", "reachable": true,
         "unreachable_since": null, "credential_error": null},
 "sessions": [],
 "workspaces": []}
```

- `version`: the daemon's version (`dev` unless set at build time).
- `hub.reachable`: false from the first failed request to the hub (no
  connection, or a timeout) until a `/readyz` probe answers `200`. The
  daemon probes every 30 seconds while the hub is unreachable.
- `hub.unreachable_since`: the time of the first failure, in RFC 3339 UTC,
  or `null` while the hub is reachable.
- `hub.credential_error`: `"worker token: 401 unauthenticated"` or
  `"approver token: 401 unauthenticated"` once the hub stops accepting that
  token while the daemon runs, until a reload takes new tokens; else `null`.
  When both tokens are refused, the worker token's error is shown.
- `sessions`: an empty list in this version.
- `workspaces`: one entry per registered workspace, ordered by slug:
  `{"slug": "demo", "state": "green", "tokens_used_today": 0}`. It is read
  from the store; the hub is not asked.

No token value appears in the body.

## `GET /api/v1/events`

A server-sent event stream of the daemon's event log. It answers `200` with
`Content-Type: text/event-stream` and stays open. Each event is sent as:

```
id: <seq>
event: <type>
data: {"seq": <seq>, "time": "<RFC 3339>", "type": "<type>", "data": {…}}

```

An event's `seq` is one greater than the one before it, starting at 1, and
never goes back, not after a restart and not after old events were removed.
The packages that produce events define their types; this version produces
none itself.

- **Without `Last-Event-ID`** the stream carries the events appended from
  then on.
- **With `Last-Event-ID: <seq>`** (the cursor) the stream first carries
  every kept event after that seq, in order, then the new ones, none missing
  and none twice.

Events are kept for **7 days**. The daemon removes older ones at start and
once an hour.

The stream is refused:

- with `400 invalid_request` when `Last-Event-ID` is empty, not a
  non-negative decimal integer, or greater than the newest seq ever given;
- with `410 cursor_expired` when an event after the cursor is no longer
  kept. The body carries `oldest_seq`, the seq of the oldest kept event, or
  the next seq to be given when none is kept:

  ```json
  {"error_type": "cursor_expired",
   "message": "events after 9 are no longer kept; the oldest kept is 11",
   "oldest_seq": 11}
  ```

A client that does not read falls behind. When 256 events wait for it, the
daemon closes its stream, and the client reconnects with the seq of the last
event it received. Appending an event never waits for a client. Every
stream ends when the daemon stops.

## Workspaces

A workspace is a Parley workspace registered with nightshift. Its routes
answer the workspace object:

```json
{"slug": "demo", "state": "green", "health_command": "make check",
 "health_timeout": "30m0s", "daily_tokens": 50000000,
 "tokens_used_today": 0, "created_at": "2026-11-28T10:00:00Z"}
```

- `state`: `green`, `red` (its last health command failed) or `held` (a
  decision about origin or the policy is open). A registered workspace is
  `green`; later packages set the other states.
- `health_timeout`: a Go duration as Go prints it.
- `tokens_used_today`: `0` until a package registers the source of the
  count.
- `created_at`: RFC 3339, UTC.

Other packages may add members to the object.

A route under `/api/v1/workspaces/{slug}` for a slug that is not
registered answers, before asking the hub:

```
404 {"error_type": "not_found", "message": "workspace <slug> is not registered"}
```

A route that asks the hub (registration, show, protect, unprotect) answers
`503 hub_unavailable` while the hub is unreachable or refuses the worker
token, and stores and writes nothing. `list`, `set`, `remove` and the
status never ask the hub. An error answer of the hub to a registration
check is refused as `hub answered <status> <error_type>: <message>`.

### `POST /api/v1/workspaces`

Registers a workspace. The body:

```json
{"slug": "demo", "health_command": "make check",
 "health_timeout": "30m", "daily_tokens": 50000000, "adopt_policy": false}
```

`slug` and `health_command` are required and not empty; `slug` is one path
element (not `.` or `..`, and without `/` or `\`). `health_timeout` is a Go
duration greater than zero, default `30m`. `daily_tokens` is an integer of 1
or more, default 50,000,000. `adopt_policy` defaults to false. Another body
is `400 invalid_request`, naming the member, before the hub is asked.

The daemon runs five checks with the worker token, in this order, and the
first that fails refuses the registration with `409 conflict` and stores
nothing:

1. **Owner.** `GET /api/v1/workspaces/{slug}` of the hub must answer the
   workspace with `access` `owner`: `workspace not found` for a `404`,
   `not the owner of <slug>` otherwise.
2. **Sync enabled.** Its setting `sync_enabled` must be on: `sync is
   disabled for <slug>`.
3. **Sync.** `POST /api/v1/workspaces/{slug}/sync` must report the default
   branch as `up_to_date`, `fast_forward` or `created`: `default branch is
   ahead of origin`, `default branch has diverged from origin`, `default
   branch: sync reported <result>`, `default branch <name> is not on
   origin`, or the hub's error.
4. **Policy.** The landing policy must require `nightshift-gate`,
   `nightshift-review` and `nightshift-release` with `fresh_checks` in
   `default` and in every rule that does not block: `policy of <slug> does
   not require nightshift's checks in: <names>; run again with
   --adopt-policy`.
5. **Health.** The health command (`sh -c`) must exit 0 within the timeout
   in a clone of the workspace at the commit the sync reported for the
   default branch: `health command exited <N>` or `health command timed
   out`, each with the member `output`, the last 50 lines the command wrote
   to standard output and standard error; `health command could not be
   started: <error>`, `cannot clone <slug>: <error>`, or `cannot clone
   <slug>: the hub reports no git URL`.

With `adopt_policy: true` a policy that lacks the checks does not refuse
the registration: once the health command passed, the daemon appends the
missing checks to `default` and every rule that does not block, sets their
`fresh_checks`, and writes the policy against the revision it read. When
the policy was replaced meanwhile the registration is refused with `policy
changed meanwhile; run the command again`.

A registration of a slug that is registered already is refused with `<slug>
is registered already`, and one sent while another of the same slug runs
with `registration of <slug> is in progress`; neither asks the hub. The
request stays open until the registration is decided; a client that goes
away ends the health command, and nothing is stored.

`201` answers the workspace object with two more members:

```json
{"slug": "demo", "state": "green", …,
 "policy_revision": 3,
 "warning": "no path of demo is marked sensitive; every change lands without you"}
```

`policy_revision` is the policy's revision after an adoption, `null` when
nothing was written. `warning` is that line when no rule of the policy, nor
`default`, requires an approval or blocks, else `null`.

### `GET /api/v1/workspaces`

`200 {"workspaces": [<workspace object>, …]}`, ordered by slug; an empty
list when none is registered.

### `GET /api/v1/workspaces/{slug}`

`200` with the workspace object and `sensitive_rules`, the rules of the
hub's policy as it is now that require an approval or block: the rules in
the policy's order, then `default` when it is one.

```json
{"slug": "demo", …,
 "sensitive_rules": [{"name": "nightshift-1", "paths": ["deploy/**"], "approvals": 1, "block": false},
                     {"name": "secrets", "paths": ["deploy/secrets/**"], "approvals": 0, "block": true}]}
```

### `PATCH /api/v1/workspaces/{slug}`

Changes one or more of `health_command`, `health_timeout` and
`daily_tokens`, validated as at registration, from the next use on. No
check runs and the hub is not asked. `200` answers the workspace object. A
body with none of the three, an unknown member or a value that does not
validate is `400 invalid_request`, and nothing changes.

### `DELETE /api/v1/workspaces/{slug}`

Removes the workspace from nightshift: its record, the names of the rules
`protect` wrote for it and its clone. Nothing on the hub changes. `204`. A
removal another package refuses (such as one for open work items) is `409
conflict` with that package's reason.

### `POST /api/v1/workspaces/{slug}/policy/rules`

Marks paths as sensitive with a rule at the top of the hub's policy, so
that it is the first a matching file selects.

```json
{"globs": ["deploy/**", "ops/*.yaml"], "block": false, "name": "deploy"}
```

Without `block` the rule requires one approval of the owner and the three
checks with `fresh_checks`; with `"block": true` it blocks. Without `name`
the rule is named `nightshift-<n>`, one above the highest number among the
policy's rules of that form, 1 when there is none. The daemon reads the
policy, writes it with the new rule against the revision it read, and
records the rule's name. `201`:

```json
{"rule": {"name": "nightshift-1", "paths": ["deploy/**", "ops/*.yaml"],
          "require": {"approvals": 1, "approvers": ["owner"],
                      "checks": ["nightshift-gate", "nightshift-review", "nightshift-release"],
                      "fresh_checks": true, "block": false}},
 "revision": 4}
```

Refused:

- `400 invalid_glob` with `glob "<glob>" is refused: <reason>` for a glob
  that is empty, longer than 255 characters or starts with `/` (before the
  hub is asked), or that the hub's policy format refuses (the hub's message
  as the reason); with `no glob given; give one or more` for an empty list;
- `400 invalid_request` for a name that is `default`, longer than 63
  characters, or holds a character other than a lower-case letter, a digit,
  `_` or `-`;
- `409 conflict` with `rule <name> exists already`, or `policy changed
  meanwhile; run the command again` when the policy was replaced between the
  read and the write. The write is not sent twice.

### `DELETE /api/v1/workspaces/{slug}/policy/rules/{name}`

Removes a rule that `protect` wrote for this workspace: the daemon reads the
policy, writes it without the rule against the revision it read, and forgets
the rule's name. A recorded rule the policy no longer holds is forgotten and
nothing is written. `200 {"revision": <the policy's revision>}`.

Refused with `409 conflict`: `not a rule nightshift wrote` for a name not
recorded for this workspace, before the hub is asked (also for a rule of
that name the person wrote); `policy changed meanwhile; run the command
again`.
