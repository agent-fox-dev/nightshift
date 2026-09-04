# Model Tiers, Variants, and Retry Behavior

This document describes how Night Shift selects models for each archetype and
how failed sessions are retried.

## Model Tiers and Variants

Three tiers are defined, ordered lowest to highest:

| Tier | Default Model |
|------|---------------|
| SIMPLE | claude-haiku-4-5 |
| STANDARD | claude-sonnet-4-6 |
| ADVANCED | claude-opus-4-6 |

These defaults are hardcoded in `afcore.core.models` but can be overridden
without a release via the `[models]` section in `config.toml` — see
[docs/config-reference.md#models](config-reference.md#models).

The ADVANCED tier supports two variants, ordered by capability:

| Variant | Model ID |
|---------|----------|
| standard | claude-opus-4-6 |
| extended | claude-opus-4-6[1m] |

Variant ordering across all tiers: `fast (0) < standard (1) < extended (2)`.
Tiers with no variant (variant = `None`) never participate in variant upgrades.

## Archetype Default Assignments

Each archetype/mode pair has a default tier and variant configured in
`ARCHETYPE_REGISTRY`. These defaults are the starting point for every session.

| Agent / Mode | Default Tier / Variant | Effort |
|---|---|---|
| coder | STANDARD / standard | xhigh |
| coder (fix) | STANDARD / standard | xhigh |
| reviewer (pre-flight) | ADVANCED / standard | high |
| reviewer (audit-review) | ADVANCED / standard | high |
| reviewer (fix-review) | ADVANCED / standard | high |
| verifier | STANDARD / standard | high |
| gate | STANDARD / standard | low |
| maintainer (hunt) | SIMPLE / standard | medium |
| maintainer (fix-triage) | STANDARD / standard | medium |
| maintainer (extraction) | SIMPLE / standard | medium |

## Resolution Priority

Model tier and variant are resolved through three layers, highest priority first:

```
1. Mode-level config override        [archetypes.overrides.<name>.modes.<mode>]
2. Per-archetype config override      [archetypes.overrides.<name>]
3. Archetype registry default         (ARCHETYPE_REGISTRY in archetypes.py)
```

When any of layers 1–2 is set, it takes precedence over the registry default.
The first non-null value encountered wins.

### Configuration Example

```toml
# Override the coder to use ADVANCED with extended variant
[archetypes.overrides.coder]
model_tier = "ADVANCED"
model_variant = "extended"

# Override only the fix mode of coder
[archetypes.overrides.coder.modes.fix]
model_tier = "STANDARD"
```

## Adopting a New Model

Two config surfaces compose to control model selection:

| Surface | What it controls |
|---------|-----------------|
| `[models.registry]` + `[models.tier_defaults]` | Which model ID backs each tier |
| `[archetypes.overrides]` | Which tier and variant each archetype requests |

### Step 1 — Register the new model

Models not in the built-in registry must be declared before they can be used
as tier defaults or in archetype overrides. Each entry needs a `tier` and an
optional `variant`:

```toml
[models.registry.claude-sonnet-5]
tier    = "SIMPLE"
variant = "standard"

[models.registry.claude-opus-5]
tier    = "STANDARD"
variant = "standard"

[models.registry.claude-fable-5-1]
tier    = "ADVANCED"
variant = "standard"
```

Valid `tier` values: `SIMPLE`, `STANDARD`, `ADVANCED`.
Valid `variant` values: `fast`, `standard`, `extended` (ordered by capability).

### Step 2 — Remap tier defaults (broadest scope)

After registration, redirect a whole tier so that every archetype at that
tier picks up the new model automatically:

```toml
[models.tier_defaults]
SIMPLE   = "claude-sonnet-5"
STANDARD = "claude-opus-5"
ADVANCED = "claude-fable-5-1"
```

Because archetypes select by tier (e.g., `coder` requests `STANDARD`), this
single change upgrades every archetype in that tier with no further config.

### Step 3 — Per-archetype override (medium scope)

Override the tier or variant for one archetype only, leaving others unchanged:

```toml
[archetypes.overrides.coder]
model_tier    = "ADVANCED"
model_variant = "standard"
effort        = "xhigh"

[archetypes.overrides.reviewer]
model_tier    = "ADVANCED"
model_variant = "standard"
```

### Step 4 — Per-mode override (finest grain)

Override a specific mode of an archetype without touching the base archetype
config or other modes:

```toml
# Coder base stays on STANDARD, but the fix mode uses ADVANCED
[archetypes.overrides.coder.modes.fix]
model_tier    = "ADVANCED"
model_variant = "standard"
effort        = "max"

# reviewer's audit-review mode is pinned to ADVANCED
[archetypes.overrides.reviewer.modes.audit-review]
model_tier = "ADVANCED"
```

### All available knobs

Every `[archetypes.overrides.<name>]` table (and its nested
`[archetypes.overrides.<name>.modes.<mode>]` table) accepts these fields.
All fields are optional; omitting one inherits the archetype registry default.

| Field | Valid values | Notes |
|-------|-------------|-------|
| `model_tier` | `SIMPLE`, `STANDARD`, `ADVANCED` | Selects the capability tier |
| `model_variant` | `fast`, `standard`, `extended` | Selects the variant within the tier |
| `effort` | `low`, `medium`, `high`, `xhigh`, `max` | Controls thinking depth and token spend — independent of model selection |
| `max_turns` | integer ≥ 0 (0 = unlimited) | |
| `thinking_mode` | `adaptive`, `disabled` | |
| `compaction` | `true`, `false` | Enable server-side context compaction |
| `max_budget_usd` | float ≥ 0.0 (0 = unlimited) | Per-archetype spend cap; inherits `orchestrator.max_budget_usd` if omitted |
| `allowlist` | list of command strings | Replaces the archetype's default bash allowlist |

`effort` and `model_*` are orthogonal: `effort` controls how hard the model
thinks within a session; `model_tier`/`model_variant` control which model
binary is selected.

### Complete worked example

Self-contained config for adopting a new model generation across the whole pipeline:

```toml
# 1. Register the new models
[models.registry.claude-sonnet-5]
tier    = "SIMPLE"
variant = "standard"

[models.registry.claude-opus-5]
tier    = "STANDARD"
variant = "standard"

[models.registry.claude-fable-5-1]
tier    = "ADVANCED"
variant = "standard"

# 2. Redirect tier defaults — every archetype inherits the new models
[models.tier_defaults]
SIMPLE   = "claude-sonnet-5"
STANDARD = "claude-opus-5"
ADVANCED = "claude-fable-5-1"

# 3. Optional: tune individual archetypes or modes
[archetypes.overrides.coder]
effort = "max"              # maximum thinking depth for the coder

[archetypes.overrides.coder.modes.fix]
model_tier = "ADVANCED"    # escalate the fix mode to Fable

[archetypes.overrides.gate]
effort = "low"             # keep the gating check cheap
```

No per-archetype `model_tier`/`model_variant` lines are needed in step 3 if
the tier-default remapping in step 2 already gives you the right model — only
add them when one archetype needs to differ from the tier default.

## Retry Behavior

When a session fails, the orchestrator applies a simple retry counter.

### How It Works

Each task node tracks its attempt count. On each failure:

1. If the attempt count is within the `max_retries` limit (default: 2), the
   task is reset to `pending` for another attempt **at the same model tier**.
2. If retries are exhausted, the task is marked `blocked` and all transitive
   dependents are cascade-blocked.

There is no automatic tier escalation — a task retries at its configured model
tier until it either succeeds or exhausts its retries.

### Timeout Retries

Timeout failures are handled separately with dedicated settings:

| Setting | Default | Description |
|---------|---------|-------------|
| `routing.max_timeout_retries` | 2 | Maximum timeout retries before falling through to failure |
| `routing.timeout_multiplier` | 1.5 | Factor by which max_turns and session_timeout are extended |
| `routing.timeout_ceiling_factor` | 2.0 | Maximum session_timeout as multiple of original value |

On each timeout retry, the session parameters (max turns and timeout) are
extended by the multiplier and clamped to the ceiling. The model tier remains
unchanged. Only after timeout retries are exhausted does the task fall through
to the normal failure path.

### Budget Exhaustion

When a session's cost approaches the per-session budget cap (≥90% of the limit),
the session is classified as budget-exhausted and is **not retried** — repeating
the same work would burn the same budget again.

### Transport Errors

Transient connection errors are retried internally by the Claude backend with
exponential backoff (up to 3 retries). If the error surfaces to the
orchestrator, the task is reset to `pending` without consuming a retry attempt.

### Review-Triggered Retries

Two archetype modes have `retry_predecessor = true`:

- **audit-review**: When test quality findings indicate MISSING or MISALIGNED
  tests, the preceding coder session is re-run with the findings injected as
  context. This is tracked by a separate `audit_max_retries` counter
  (default: 2).
- **verifier**: When verification fails, the preceding coder session is re-run
  with the verification results as context. Uses the standard `max_retries`
  counter.
