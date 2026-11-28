# Architecture

nightshift owns a project's work on a Parley hub, and a person steers it
through an API. It is written in Go and built as two programs:

- **`nightshiftd`** (`cmd/nightshiftd`), the daemon. It is one process
  holding the controller, its store and the API.
- **`nightshift`** (`cmd/nightshift`), the command-line client. It speaks
  only the daemon's HTTP API.

Each `main` only calls a run function, `daemon.Run` or `cli.Run`, which
takes the arguments, the environment and the output streams and returns the
exit code. That is how the tests run both programs in-process.

## Packages

| Package | What it does |
|---|---|
| `internal/config` | loads the TOML configuration: defaults, `~/` expansion, refusal of unknown and missing keys |
| `internal/store` | the SQLite store (`modernc.org/sqlite`): opens the file and applies each package's numbered migrations |
| `internal/store/storetest` | test helper: a store migrated once per test process and copied per test |
| `internal/hub` | the client of the Parley hub with the two credentials, and its monitor of reachability and refused tokens |
| `internal/hubtest` | test helper: the fake Parley hub every package's tests use |
| `internal/daemon` | `Run`: the start checks, the composition of the parts, signals and shutdown |
| `internal/api` | the HTTP API server: the client token, the error body, the status and the event stream |
| `internal/event` | the event log: gapless `seq`, 7-day retention, subscribers with a bounded buffer |
| `internal/cli` | `nightshift`: global flags, the command table, the request function and its exit codes |
| `internal/buildinfo` | the version, set at link time by `make build` |

## The start sequence

`daemon.Run` (`internal/daemon/run.go`) is the composition root:

1. Parse `--config`.
2. Run the start checks in order, from one list in
   `internal/daemon/checks.go`: `config` (loads the file and makes the hub
   client), `hub` (`GET /readyz`, 10 seconds), `credentials`
   (`CheckCredentials`). The first failure prints `<check>: <reason>` and
   returns 1. Nothing has been opened yet.
3. Open the store, open the event log over it, ensure the client token, and
   listen. A failure prints `store: `, `token: ` or `listen: ` and returns 1.
4. Prune the event log, then start the hourly prune loop and the hub
   monitor's probe loop. Register the event stream and serve.
5. Wait for signals. `SIGHUP` runs `Daemon.Reload` in the background.
   `SIGTERM` or `SIGINT` starts the shutdown: end the streams,
   `http.Server.Shutdown`, run the shutdown steps under a context cancelled
   25 seconds after the signal, stop the loops, close the store and return
   0, at the latest 30 seconds after the signal.

## The hub and its outages

Every request the `hub.Client` sends reports its outcome to the client's
`hub.Monitor`. A request that gets no answer marks the hub unreachable at
the time of that first failure. While the hub is unreachable the monitor
probes `/readyz` every 30 seconds, and the first `200` marks it reachable
again. A `401` answer records that credential's error. `Client.Available(cred)`
is false while the hub is unreachable or while that credential has an
error. The status reads the monitor's state.

The two tokens sit behind a lock in the client. A reload checks new tokens
on a separate client without a monitor, so a refusal of the new tokens is
not recorded against the ones in use, and swaps them in with `SetTokens`
only when they pass.

## Data

The store is one SQLite file. Each package registers its migrations with
`store.Register(component, migrations...)`, and the store records the
applied versions per component in `schema_migrations`, so the order in which
packages register does not matter. The event log keeps `events(seq, time,
type, data)` and the one-row `event_counter(last_seq)`. The counter holds
the highest `seq` ever given, so that `seq` continues after a prune or a
restart.

## Extension points

A later package wires its part in `internal/daemon/run.go` through these:

| Extension point | Where |
|---|---|
| a start check after `credentials` | append to `startChecks()` in `internal/daemon/checks.go` |
| an API route | `(*api.Server).Handle(pattern, handler)` with a method pattern; the helpers `api.WriteJSON`, `api.WriteError`, `api.WriteErrorWith` |
| a status member (`sessions`, `workspaces`) | `(*api.Server).SetStatusSource(member, source)` |
| a client command | `cli.Register(cli.Command{…})` from an `init`, using `(*cli.Call).Request` |
| tables in the store | `store.Register(component, migrations...)` |
| a shutdown step | `(*daemon.Daemon).OnShutdown(name, fn)` |
| an event | `(*event.Log).Append(ctx, type, data)`, on `Daemon.Log()` |
| whether work needing the hub may start | `(*hub.Client).Available(hub.Worker)`, on `Daemon.Hub()` |
