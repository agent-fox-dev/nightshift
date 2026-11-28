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
| 401 | `unauthorized` | the client token is missing or wrong |
| 404 | `not_found` | the path names no route |
| 405 | `method_not_allowed` | the route does not have the method |
| 410 | `cursor_expired` | the event stream's cursor is older than what is kept |
| 500 | `internal` | the request failed inside the daemon |

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
- `sessions`, `workspaces`: empty lists in this version.

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
