# Nightshift

nightshift owns a project's work on a [Parley](https://github.com/agent-fox-dev/parley)
hub, and a person steers it through an API. It is two programs:

- **`nightshiftd`**, the daemon. It starts only when its configuration, the
  hub and its two hub credentials are in order. It serves an authenticated
  HTTP API with a status and a resumable event stream, keeps running through
  a hub outage, and stops cleanly on a signal.
- **`nightshift`**, the command-line client of that API.

It runs on Linux and macOS.

## Quick start

Build both programs into `bin/` (Go 1.27):

```sh
make build
```

Mint two personal access tokens on the hub: a worker token and an approver
token, with the scopes [docs/configuration.md](docs/configuration.md) lists.
Then write `~/.nightshift/config.toml`:

```toml
[hub]
endpoint = "https://hub.example"
worker_token = "parley_pat_<token_id>_<secret>"
approver_token = "parley_pat_<token_id>_<secret>"
```

Start the daemon and ask it for its status:

```sh
bin/nightshiftd
bin/nightshift status
```

```
nightshiftd dev
hub: https://hub.example reachable
```

At its first start the daemon writes the client token to
`~/.nightshift/token`, which the client reads. Stop the daemon with
`SIGTERM` or `Ctrl-C`.

## Documentation

- [docs/cli.md](docs/cli.md): both programs, their flags, exit codes and signals
- [docs/configuration.md](docs/configuration.md): the configuration file and the start checks
- [docs/api.md](docs/api.md): the HTTP API
- [docs/architecture.md](docs/architecture.md): packages, the start sequence and extension points

## Development

```sh
make help    # list the targets
make check   # gofmt check, go vet, then every test
```
