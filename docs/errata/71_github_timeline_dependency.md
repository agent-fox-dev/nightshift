# Errata: 71-REQ-2.2 — GitHub Timeline Dependency Ordering

**Spec:** 71 (Fix Issue Ordering)
**Requirement:** 71-REQ-2.2
**Status:** Intentionally removed
**Issue:** #91

## Summary

71-REQ-2.2 specified that `fetch_github_relationships` would query GitHub's
timeline API to discover cross-referenced events between issues, producing
`DependencyEdge` entries with `source="github"`. This would supplement the
text-based dependency extraction (`parse_text_references`, 71-REQ-2.1) with
GitHub-native relationship data.

## What happened

The function `fetch_github_relationships` was implemented with a duck-typing
probe (`getattr(platform, "get_issue_timeline", None)`) that returned an empty
list when the method was absent. No platform implementation in this repository
or in the `afissues` dependency ever provided `get_issue_timeline`, so the
function always returned an empty edge list. The `getattr` guard and a
surrounding `try/except` in `engine.py` masked this gap at DEBUG level,
making it invisible in normal operation.

The only evidence of the code path being exercised was a single test that
attached `get_issue_timeline` to a `MagicMock`.

## Decision

The function, its call site in `engine.py`, and the mock-based test were
removed (issue #91). Dependency ordering now relies solely on explicit text
references (`parse_text_references`), which matches the actual production
behaviour that has been in effect since the feature was introduced.

## Rationale

- **No behavioural change:** Removing dead code that never executed in
  production does not alter any observable behaviour.
- **Cost concern:** Implementing the timeline API call would add a per-issue
  HTTP request to every poll cycle, with rate-limit and error-handling
  implications that were not budgeted for.
- **Correctness:** The `getattr` duck-typing pattern silently degraded a
  missing capability to a no-op, preventing operators from learning that
  a documented ordering input was absent. Removing it is preferable to
  perpetuating the silent skip.

## Future work

If GitHub timeline-based dependency ordering is needed in the future, it
should be implemented as a first-class capability on the platform protocol
(e.g. an explicit `supports_timeline` flag or a `Protocol` method) rather
than a duck-typing probe, so that an unimplemented platform is a known,
logged state rather than a silent skip.
