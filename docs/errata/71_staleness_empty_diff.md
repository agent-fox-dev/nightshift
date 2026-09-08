# Errata: Staleness check computed on empty diff (spec 71)

**Issue:** #31 (closed without the fix landing), #53 (this fix)

## Background

Issue #31 reported that `check_staleness` was called with a hardcoded
empty string for `fix_diff`, causing the AI to evaluate issue
obsolescence based only on titles and truncated bodies.  Issue #31 was
closed but the fix never landed -- the code path remained unchanged.

## What was wrong

The call site in `NightShiftEngine._dispatch_parallel` passed `""` as the
`fix_diff` argument because `_process_fix` discarded everything the
pipeline produced except `FixMetrics` (token counts and cost).  The prompt
builder rendered `(no diff available)` and the model answered anyway,
closing issues without evidence.

## What changed (issue #53)

1. **Fail closed:** `check_staleness` now returns an empty
   `StalenessResult` immediately when `fix_diff` is empty or
   whitespace-only, without invoking the AI.

2. **Supply the diff:** `FixMetrics` gained a `fix_diff` field.  The
   direct-strategy harvest path captures a bounded (3 000-char) diff
   preview before harvest and carries it through `_process_fix` to
   `_dispatch_parallel`, which passes it to `check_staleness`.

3. **Tests updated:** All normal-path tests now supply a non-empty diff.
   A dedicated test verifies the fail-closed guard (AC-1).
