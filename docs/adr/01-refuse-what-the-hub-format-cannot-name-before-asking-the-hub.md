# 01. Refuse what the hub's format cannot name before asking the hub

## Status

Accepted, 2026-11-28, with the workspaces and their protected paths.

## Context

`nightshift workspace add` and `workspace protect` take values a person
types: a workspace's slug and the globs of a rule. The daemon checks some of
them itself before any request to the hub (an empty glob, one longer than
255 characters, one starting with `/`), so that a mistake costs no round
trip and leaves the hub's policy as it was. Two inputs fall outside the
forms the specification gives for those refusals:

- The slug names a directory: a workspace's clone lives at
  `<clones_dir>/<slug>`. A slug of `.` or `..`, or one holding `/` or `\`,
  would put the clone outside its own directory, or onto another's, and
  `workspace remove` deletes that directory. No Parley slug has that form.
- A protection with an empty list of globs has no glob to name, while every
  other refused glob is answered `glob "<glob>" is refused: <reason>`.

## Decision

- A registration whose `slug` is `.` or `..`, or holds `/` or `\`, is
  refused with `400 invalid_request` and the message `slug must be a
  workspace's slug`, before the hub is asked
  (`internal/workspace/routes.go`, `TestRegister_RejectsAnInvalidBody`).
- A protection with no glob is refused with `400 invalid_glob` and the
  message `no glob given; give one or more`
  (`internal/workspace/protect.go`, `TestProtect_RefusesAGlob/no_glob`). The
  error type stays `invalid_glob`, so the client's exit code is 2 as for
  every refused glob; the client itself never sends an empty list, which is
  a usage error there.

## Consequences

The clone directory of a workspace is always one directory directly under
`clones_dir`, and removing a workspace can delete nothing else. A script
reading the message of a refused protection sees one text that does not
follow the `glob "<glob>" is refused` form, for the case where there is no
glob.
