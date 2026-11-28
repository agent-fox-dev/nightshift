# Configuration

`nightshiftd` reads one TOML file: `~/.nightshift/config.toml`, unless
`--config <file>` names another. The `nightshift` client reads no
configuration file; see [cli.md](cli.md) for its flags and environment.

## Keys

| Key | Meaning | Default |
|---|---|---|
| `listen` | the address the API listens on | `127.0.0.1:7878` |
| `token_file` | where the client token is kept | `~/.nightshift/token` |
| `clones_dir` | where the clones of the workspaces are kept, one directory per slug | `~/.nightshift/clones` |
| `[store] path` | the daemon's store, one SQLite file | `~/.nightshift/store.db` |
| `[hub] endpoint` | the Parley hub's base URL | none, required |
| `[hub] worker_token` | the worker token | none, required |
| `[hub] approver_token` | the approver token | none, required |

A leading `~/` in `token_file`, `clones_dir` and `[store] path` is the home
directory of the daemon's user. The parent directories of the store and the token file
are created with mode `0700` where they do not exist.

```toml
listen = "127.0.0.1:7878"

[store]
path = "~/.nightshift/store.db"

[hub]
endpoint = "https://hub.example"
worker_token = "parley_pat_<token_id>_<secret>"
approver_token = "parley_pat_<token_id>_<secret>"
```

## Clones of the workspaces

The daemon runs a workspace's health command in a clone of it under
`clones_dir/<slug>`, fetched from the hub's git endpoint with the worker
token and brought to the commit in question, detached, with untracked and
ignored files removed. The clone is kept between runs, and removed with the
workspace. At start the daemon removes the clones under `clones_dir` (the
directories holding a `.git` directory) that belong to no registered
workspace; anything else there is left as it is. Before git works in a kept
clone, its `.git/config` is written anew and its hooks are removed, so that
nothing a health command wrote into `.git` runs with the token. `git` and `sh` must be installed on
the daemon's machine.

## The two hub tokens

Both are personal access tokens of the person's Parley account
(`parley_pat_<token_id>_<secret>`), and they must be two different tokens.

- The **worker token** is what nightshift uses for everything it does
  itself. It holds `workspaces:write`, `git:write`, `git:read`,
  `changes:write`, `checks:write`, `tokens:manage`, `forge:write`,
  `sessions:write` and `audit:read`, by these exact names. It is not bound
  to a workspace and does not hold `changes:approve`.
- The **approver token** is used only to record the person's approval. It
  holds `changes:read` and `changes:approve`.

The worker token reads both tokens' scopes from the hub
(`GET /api/v1/user/tokens/{token_id}`). The approver token sends one request
on that route, and any answer but `401` is taken as the hub accepting it.

`SIGHUP` makes the daemon read the two tokens from the file again (see
[cli.md](cli.md#signals)).

## The start checks

At start the daemon runs these checks in order, and stops at the first one
that fails. A failure ends the process with exit code 1 and one line on
stderr, `<check>: <reason>`. Nothing has been started at that point: no
listener, no store, no token file.

**`config`** fails when:

| Cause | Line |
|---|---|
| the file is missing or unreadable | `config: open <path>: no such file or directory` (the error names the file) |
| the file is not valid TOML | `config: <path>: <the decoder's error>` |
| a key the daemon does not know | `config: unknown key "hub.endpont"` |
| a required key is missing | `config: hub.endpoint is not set` (then `hub.worker_token`, `hub.approver_token`) |

**`hub`** asks `GET <endpoint>/readyz` and fails unless it answers `200`
within 10 seconds: `hub: connection refused`, `hub: readyz answered <status>`
or `hub: timeout`.

**`credentials`** fails with the first of these reasons that applies:

1. `credentials: worker token is not a personal access token` (also `approver token …`)
2. `credentials: worker and approver are the same token`
3. `credentials: worker token: 401 unauthenticated`
4. `credentials: approver token: 401 unauthenticated`
5. `credentials: worker token is bound to a workspace`
6. `credentials: worker token lacks tokens:manage` (the hub refused its read of its own token as `insufficient_scope`)
7. `credentials: worker token must not hold changes:approve`
8. `credentials: worker token lacks <scope>`: the first missing scope, in the order listed above
9. `credentials: approver token lacks <scope>`: `changes:read`, then `changes:approve`

Any other answer to these reads fails as `credentials: <worker|approver>
token: <status> <error_type>`.

After the checks the daemon opens the store, writes the client token where
none exists and listens. A failure there ends it the same way, with
`store: <error>`, `token: <error>` or `listen: <error>`.
