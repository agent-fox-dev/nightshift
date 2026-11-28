# Command line

nightshift is two programs: `nightshiftd`, the daemon, and `nightshift`,
its client, which speaks only the daemon's HTTP API ([api.md](api.md)).

## `nightshiftd`

```
nightshiftd [--config <file>]
```

| Flag | Meaning |
|---|---|
| `--config <file>` | the configuration file (default `~/.nightshift/config.toml`; see [configuration.md](configuration.md)) |

The daemon runs its start checks (`config`, `hub`, `credentials`), opens
its store, writes the client token where none exists, and serves the API
until it is stopped.

Exit codes:

| Code | When |
|---|---|
| 0 | stopped by `SIGTERM` or `SIGINT`, or `--help` |
| 1 | a start check failed, or the store, the token file or the listen address could not be used; one line on stderr names the step and the reason |
| 2 | a usage error: an unknown flag or an extra argument |

### Signals

- **`SIGTERM`, `SIGINT`**: the daemon stops accepting requests, ends the
  event streams, runs its shutdown steps, closes the store and exits with
  code 0 within 30 seconds. A shutdown step still running 25 seconds after
  the signal is abandoned, and stderr names it (`shutdown step "<name>"
  abandoned`).
- **`SIGHUP`**: the daemon reads `hub.worker_token` and `hub.approver_token`
  from its configuration file again and runs the `credentials` check on
  them. Tokens that pass replace the ones in use and clear the reported
  credential error. Otherwise the tokens in use stay, and one line goes to
  stderr: `config: <reason>` or `credentials: <reason>`. The daemon keeps
  running either way.

### The hub's outages

When a request to the hub gets no answer (no connection, or a timeout), the
daemon keeps running, reports the hub unreachable with the time of the first
failure, asks the hub's `/readyz` every 30 seconds, and continues by itself
at the first `200`. A token the hub answers `401` while the daemon runs is
reported as the status's credential error. Work that needs the hub, or that
token, is not started meanwhile.

## `nightshift`

```
nightshift [flags] <command> [arguments]
```

The global flags are accepted before or after the command:

| Flag | Meaning |
|---|---|
| `--addr <host:port>` | the daemon's address; else `$NIGHTSHIFT_ADDR`, else `127.0.0.1:7878` |
| `--token-file <file>` | the client token; else `~/.nightshift/token` |
| `--json` | print the daemon's JSON answer |

The client does not read the daemon's configuration file.

Exit codes, for every command:

| Code | When | stderr |
|---|---|---|
| 0 | success | |
| 1 | the daemon refused the request | the error's `message` (with `--json`, stdout holds the error body) |
| 1 | the daemon cannot be reached | `cannot reach nightshiftd at <address>` |
| 1 | the token file cannot be read | `cannot read the client token at <path>` |
| 2 | a usage error: no command, an unknown command or flag, a missing or extra argument | the usage, starting `usage: nightshift`; no request is sent |

### `nightshift status`

Prints the daemon's version and whether it reaches the hub:

```
nightshiftd dev
hub: https://hub.example reachable
```

While the hub is unreachable, the second line is `hub: unreachable since
<time>` (RFC 3339). A third line holds the credential error when there is
one, such as `worker token: 401 unauthenticated`. With `--json` it prints
the answer of `GET /api/v1/status`.
