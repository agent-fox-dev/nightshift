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
| 1 | the daemon refused the request | the error's `message`; for a `workspace` command also each line of the error's `output` (with `--json`, stdout holds the error body) |
| 1 | the daemon cannot be reached | `cannot reach nightshiftd at <address>` |
| 1 | the token file cannot be read | `cannot read the client token at <path>` |
| 2 | a usage error: no command, an unknown command or flag, a missing or extra argument, a flag value that does not parse | the usage, starting `usage: nightshift`; no request is sent |
| 2 | `workspace protect`: the daemon refused a glob (`invalid_glob`) | the error's `message` |

### `nightshift status`

Prints the daemon's version and whether it reaches the hub:

```
nightshiftd dev
hub: https://hub.example reachable
```

While the hub is unreachable, the second line is `hub: unreachable since
<time>` (RFC 3339). A third line holds the credential error when there is
one, such as `worker token: 401 unauthenticated`. Then one line per
registered workspace, in the order of its slug:

```
workspace demo: green, 0 tokens today
```

With `--json` it prints the answer of `GET /api/v1/status`.

### `nightshift workspace`

```
nightshift workspace <command> [arguments] [flags]
```

The commands that register and manage the workspaces nightshift works in.
Their flags, the global ones included, are accepted before and after their
arguments. Each sends one request ([api.md](api.md#workspaces)); with
`--json` it prints the daemon's answer instead of the lines below
(`remove` prints nothing).

`workspace` with no command or an unknown one, a missing slug or other
argument, an argument too many, an unknown flag or a flag value that does
not parse is a usage error: exit code 2, the usage on stderr, no request.

#### `workspace add`

```
nightshift workspace add <slug> --health <command> [--health-timeout <duration>] [--daily-tokens <N>] [--adopt-policy]
```

Registers a Parley workspace the person owns, once its five checks pass
(the workspace is on the hub and theirs, sync from origin is on, the sync
reports the default branch in step with origin, the landing policy requires
nightshift's three checks, and the health command exits 0 on the default
branch). `--health-timeout` is a Go duration (default `30m`),
`--daily-tokens` an integer (default 50,000,000). `--adopt-policy` writes
the three checks into the policy where they are missing, once the health
command passed. The request has no deadline: the command waits for the
health command.

```
$ nightshift workspace add demo --health "make check" --adopt-policy
registered demo
policy adopted at revision 3
no path of demo is marked sensitive; every change lands without you
```

The second line appears only when the policy was written; the warning goes
to stderr and appears when no rule of the policy requires an approval or
blocks. A refused registration exits 1 with the reason, and for a failed
health command the last 50 lines of its output:

```
$ nightshift workspace add demo --health "echo broken; exit 3"
health command exited 3
broken
```

#### `workspace show`

```
$ nightshift workspace show demo
demo: green
health: make check (timeout 30m0s)
daily limit: 50000000 tokens, 0 used today
sensitive paths:
  nightshift-1 approval: deploy/**
  secrets block: deploy/secrets/**
  default approval: every other path
```

The sensitive paths are the rules of the hub's policy that require an
approval or block; `sensitive paths: none` when there is none.

#### `workspace list`

```
$ nightshift workspace list
demo  green  0/50000000 tokens  health: make check (30m0s)
```

One line per workspace, ordered by slug, the columns separated by two
spaces; `no workspaces` when none is registered. Other packages may add
columns at the end of a line.

#### `workspace set`

```
nightshift workspace set <slug> [--health <command>] [--health-timeout <duration>] [--daily-tokens <N>]
```

Changes the given settings from their next use on, and prints `updated
<slug>`. At least one flag is required.

#### `workspace remove`

```
nightshift workspace remove <slug>
```

Removes the workspace from nightshift, with its clone and its record of the
rules `protect` wrote, and prints `removed <slug>`. The workspace and its
policy stay on the hub.

#### `workspace protect`

```
nightshift workspace protect <slug> <glob>... [--block] [--name <rule>]
```

Adds a rule at the top of the workspace's landing policy for the paths the
globs match. Without `--block` a change touching them waits for one
approval of the owner; with `--block` it never lands. The rule is named
`--name`, or `nightshift-<n>` with the next free number.

```
$ nightshift workspace protect demo "deploy/**"
rule nightshift-1 (approval): deploy/**
policy revision 4
```

A refused glob exits 2. A taken name, or a policy replaced between the
daemon's read and its write (`policy changed meanwhile; run the command
again`), exits 1.

#### `workspace unprotect`

```
$ nightshift workspace unprotect demo nightshift-1
removed rule nightshift-1
policy revision 5
```

Removes a rule that `protect` wrote; any other rule exits 1 with `not a
rule nightshift wrote`.
