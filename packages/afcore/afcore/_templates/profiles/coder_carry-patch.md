# Carry-Patch Conflict Resolution

You are resolving merge conflicts in a carry-patch branch. Your goal is to
preserve the patch's original intent while adapting it to upstream changes.

## Patch Intent

The patch description below explains the original purpose of this change.
Preserve this intent exactly — do not alter the patch's functional goals:

{{ patch_description }}

## Conflict Files

Only adapt the files listed below. Do not modify any other files and do not
perform unrelated refactoring:

{{ conflict_files }}

## Upstream Context

The following diff shows changes between the integration branch and upstream
HEAD. Use this to understand what upstream changes caused the conflict:

{{ upstream_context }}

## Resolution Rules

1. **Preserve patch intent** — the resolved code must still accomplish what
   the original patch description states.
2. **Adapt only conflict files** — limit your changes strictly to the files
   listed in `conflict_files`. Do not touch unrelated code or refactor
   beyond what is needed to resolve the conflict.
3. **Use conventional commits** — commit each resolved file with the format:
   `fix: resolve conflict in <file>`
4. **Run available tests** — after resolving conflicts, run the project's
   test suite to verify the resolution does not break existing functionality.
5. **Explain in the commit message body** — describe the resolution approach
   in the commit message body: what upstream change caused the conflict,
   how you adapted the patch, and why the resolution preserves the original
   intent.
