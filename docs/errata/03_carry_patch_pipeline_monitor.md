# Errata: `_run_carry_patch_monitor` delegation removed (spec 03)

**Spec:** 03_carry_patch_pipeline_monitor
**Affected requirements:** 03-REQ-7.4
**Affected test specs:** TS-03-22, TS-03-27
**Severity:** Low (no runtime effect)

## Summary

Spec 03 required adding `_run_carry_patch_monitor(self, slug: str) ->
MonitorCycleResult` to `NightShiftEngine` as a delegation method that
forwarded to `CarryPatchMonitor.run_cycle()`.  The stated purpose was to
ensure the same `CarryPatchMonitor` instance is reused across all calls,
preserving the in-memory session retry counter (03-PROP-3).

The method was implemented but never used by any production code path.
`build_streams()` wires the carry-patch `EngineWorkStream` to call
`CarryPatchMonitor.run_cycle()` directly by passing the monitor instance
as the stream engine (`engine=monitor, method_name="run_cycle"`).  The
`slug` parameter on the delegation method was incompatible with the
stream contract — `EngineWorkStream.run_once()` calls the target method
with zero arguments.

## Changes

- **Removed:** `NightShiftEngine._run_carry_patch_monitor()` method and the
  `self._carry_patch_monitor` attribute it depended on.
- **Removed:** The `engine._carry_patch_monitor = monitor` assignment in
  `build_streams()` and the associated misleading comment.
- **Replaced:** `TestEngineRunCarryPatchMonitor` (which tested the dead
  delegation method) with `TestCarryPatchStreamBehaviour`, which asserts
  that `EngineWorkStream.run_once()` reaches `CarryPatchMonitor.run_cycle()`
  on the same monitor instance across cycles.

## Why 03-PROP-3 is still satisfied

The instance-reuse property (03-PROP-3) is preserved because
`build_streams()` creates a single `CarryPatchMonitor` and passes it
directly as the `EngineWorkStream` target.  Every `run_once()` call
invokes `run_cycle()` on that same object — the monitor is never
re-instantiated between cycles, so the in-memory retry counter survives.

## Reference

- Issue: #56
- Spec artifacts: `.specs/03_carry_patch_pipeline_monitor/requirements.json`,
  `test_spec.json`, `tasks.json`
