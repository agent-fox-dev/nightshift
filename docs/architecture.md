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
| `internal/cli` | `nightshift`: global flags, the command table, the request function and its exit codes, and `nightshift workspace` |
| `internal/command` | runs a shell command line (`sh -c`) in a directory with a timeout, in a process group of its own, keeping the last lines of its output |
| `internal/gitclone` | brings a clone of a hub repository to a commit, the credential passed to git only through its environment |
| `internal/policy` | the hub's landing policy: its document, which requirements lack nightshift's three checks, adopting them, the sensitive rules, the next rule name, and reading and writing it on the hub |
| `internal/workspace` | the registered workspaces: registration through its five checks, settings, state, removal, protection of paths, the health run, and their API routes |
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
   Then build the API, one `command.Runner` and the `workspace.Service`
   (store, hub client, runner, `clones_dir`), and mount the workspace
   routes and the `workspaces` status source.
4. Prune the event log and remove the clones (directories holding `.git`)
   of no registered workspace
   (a failure prints `workspaces: <error>` and the daemon goes on), then
   start the hourly prune loop and the hub monitor's probe loop. Register
   the event stream and serve.
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

## Workspaces

A registration (`POST /api/v1/workspaces`) is one request that stays open
until it is decided. `Service.Register` holds a lock per slug and runs the
checks in order with the worker token: `hub.Client.Workspace` (the owner,
by `access`), the `sync_enabled` setting, `hub.Client.Sync` (the default
branch's result), `policy.Read` and `policy.Missing`, then the health
command: `gitclone.Sync` brings `<clones_dir>/<slug>` to the default
branch's `local_sha` and `command.Runner.Run` runs the command there with
the workspace's timeout, keeping 50 lines. With `adopt_policy`,
`policy.Adopt` and `policy.Write` follow the health command, so that a
refused registration leaves the hub's policy as it was. Only then is the
workspace stored, `green`. The request's context reaches the health
command, so a client that goes away ends it. A refused registration
removes its clone.

`protect` reads the policy, puts the new rule at index 0 and writes it with
`If-Match` set to the revision it read; only after the hub accepted it is
the rule's name recorded for the workspace. `unprotect` refuses a name not
recorded for that workspace before it asks the hub. A `412` from the hub is
`policy changed meanwhile; run the command again`, and no write is sent
twice.

The runner starts every command with `sh -c` in a process group of its own,
both output streams through one pipe into a ring of the last lines, each
cut at 4,096 bytes as it is read. On the timeout or a cancelled context it
kills the whole group, and once the shell has exited it kills the group
again, so that nothing the command left behind runs on. It waits for the
shell's exit without reaping it (`waitid` with `WNOWAIT` on Linux, a
kqueue's `NOTE_EXIT` on macOS and the BSDs) and reaps it only after that
kill: until then the shell's process id, which is the group's, cannot be
given to another group. git runs through the same runner; the token reaches
it only as an `http.extraHeader` in `GIT_CONFIG_*` variables of its
environment, never on a command line, in the remote's URL or in the clone's
configuration, and it is stripped from every error text. Every git command
runs with `core.hooksPath=/dev/null` and `core.fsmonitor=false`, and a kept
clone's `.git/config` is written anew and its hooks removed before git
reads them: a health command runs in the clone and may write into `.git`.

Each request to the hub is bounded by `Service.HubTimeout` (30 seconds); a
hub that gives no answer within it is unavailable (`503`). Once a
registration's health command passed, or a protection is checked, the
policy write and the store write after it run to their end without the
client. A workspace's clone is taken by one health run at a time, and its
record of rules by one protection, unprotection or removal at a time; a
removal waits for both.

## Data

The store is one SQLite file. Each package registers its migrations with
`store.Register(component, migrations...)`, and the store records the
applied versions per component in `schema_migrations`, so the order in which
packages register does not matter. The event log keeps `events(seq, time,
type, data)` and the one-row `event_counter(last_seq)`. The counter holds
the highest `seq` ever given, so that `seq` continues after a prune or a
restart. The workspace service keeps `workspaces(slug, git_url,
health_command, health_timeout_ns, daily_tokens, state, created_at)`, where
`git_url` is the `hub_url` the hub reported at registration, and
`workspace_rules(slug, name)`, the rules `protect` wrote, removed with
their workspace.

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
| run a command (the gate, a session's command) | `(*command.Runner).Run(ctx, command.Spec{Dir, Line, Timeout, TailLines, Env})`, on `Daemon.Runner()` |
| the operating-system user commands run as | `(*command.Runner).SetIdentity(command.Identity)`: called with each `*exec.Cmd` before it starts |
| bring a clone of a hub repository to a commit | `gitclone.Sync(ctx, gitclone.Options{URL, Token, Dir, Commit, Runner})` |
| the registered workspaces | `Daemon.Workspaces()`: `Get`, `List` (`workspace.ErrNotFound` for an unregistered slug) |
| a workspace's state | `(*workspace.Service).SetState(ctx, slug, workspace.Green|Red|Held)`; it appends no event |
| run a workspace's health command at a commit | `(*workspace.Service).RunHealth(ctx, slug, commit, tailLines)` |
| the default daily limit of a registration | `(*workspace.Service).DefaultDailyTokens` (50,000,000) |
| the tokens a workspace used today | `(*workspace.Service).SetTokensUsed(func(slug) int64)`; `0` without one |
| members of the workspace object | `(*workspace.Service).Decorate(func(slug, obj map[string]any))` |
| refuse a removal, or act on one | `(*workspace.Service).AddRemoveGuard(func(ctx, slug) error)` (the first error refuses it with its text), `OnRemoved(func(ctx, slug))` |
| a column of `nightshift workspace list` | `cli.AddWorkspaceListColumn(header, func(obj map[string]any) string)` from an `init` |
