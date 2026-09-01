# Errata: SyncResult.patches_merged type conflict (spec 01)

**Spec:** 01_afhub_client  
**Affected test:** TS-01-3  
**Severity:** Critical (would cause incorrect implementation if unaddressed)

## Summary

The `test_spec.json` pseudocode for TS-01-3 asserts:

```python
assert result.patches_merged == 2
```

This implies `SyncResult.patches_merged` is an **integer** (count of merged patches).

However, the authoritative hub API reference
(`docs/proposals/carry_patch_support.md`) explicitly defines this field as a
**list of branch-name strings**:

```json
{
  "patches_merged": ["feature/already-merged"],
  ...
}
```

The proposal model declaration is `patches_merged: list[str]`.

## Resolution

The implementation and tests in `packages/afhub/` use `list[str]` as the type
for `SyncResult.patches_merged`, consistent with the hub REST API contract.
The integer value `2` in the test_spec pseudocode appears to be a draft
artifact — it represents the *count* of merged patches, not the actual API
response shape.

**Affected files:**
- `packages/afhub/afhub/models.py` — `SyncResult.patches_merged: list[str]`
- `packages/afhub/tests/test_client.py` — TS-01-3 tests use `list[str]`

## Reference

- Authoritative API doc: `docs/proposals/carry_patch_support.md` (sync
  response section)
- Memory finding: `[REVIEW] [critical] security: SyncResult.patches_merged
  type conflict between test_spec and source API documentation`
