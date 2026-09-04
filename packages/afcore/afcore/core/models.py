"""AI model registry.

Defines the model tier enum, model entry dataclass, a registry of known
models, and functions for model resolution and cost calculation.

Pricing has been moved to config.toml via PricingConfig (spec 34).

Requirements: 01-REQ-5.1, 01-REQ-5.2, 01-REQ-5.3, 01-REQ-5.4, 01-REQ-5.E1,
              34-REQ-2.3, 34-REQ-2.4, 34-REQ-5.2
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, model_validator

if TYPE_CHECKING:
    from afcore.core.config import ModelsConfig, PricingConfig

logger = logging.getLogger(__name__)


def content_hash(text: str) -> str:
    """Compute SHA-256 hex digest of a text string."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ModelTier(StrEnum):
    SIMPLE = "SIMPLE"
    STANDARD = "STANDARD"
    ADVANCED = "ADVANCED"


@dataclass(frozen=True)
class ModelEntry:
    model_id: str
    tier: ModelTier
    variant: str | None = None

    def __post_init__(self) -> None:
        if self.variant is not None and not isinstance(self.variant, str):
            raise TypeError(f"variant must be str or None, got {type(self.variant).__name__}")


# Canonical variant label ordering for upgrade comparisons.
# Models with variant=None do not participate in variant ordering.
VARIANT_ORDER: dict[str, int] = {"fast": 0, "standard": 1, "extended": 2}

MODEL_REGISTRY: dict[str, ModelEntry] = {
    "claude-haiku-4-5": ModelEntry("claude-haiku-4-5", ModelTier.SIMPLE, variant="standard"),
    "claude-sonnet-4-6": ModelEntry("claude-sonnet-4-6", ModelTier.STANDARD, variant="standard"),
    "claude-opus-4-6": ModelEntry("claude-opus-4-6", ModelTier.ADVANCED, variant="standard"),
    "claude-opus-4-6[1m]": ModelEntry("claude-opus-4-6[1m]", ModelTier.ADVANCED, variant="extended"),
}

TIER_DEFAULTS: dict[ModelTier, str] = {
    ModelTier.SIMPLE: "claude-haiku-4-5",
    ModelTier.STANDARD: "claude-sonnet-4-6",
    ModelTier.ADVANCED: "claude-opus-4-6",
}


class ModelEntryConfig(BaseModel):
    """User-configurable model registry entry for [models.registry.<id>] TOML tables.

    Pydantic-compatible twin of :class:`ModelEntry` that accepts unvalidated
    tier/variant strings from TOML and converts them on demand.

    Requirements: 01-REQ-5.1
    """

    model_config = ConfigDict(extra="ignore")

    tier: str = Field(description="Model tier: SIMPLE, STANDARD, or ADVANCED")
    variant: str | None = Field(default=None, description="Model variant: fast, standard, or extended")

    @model_validator(mode="after")
    def _validate_tier(self) -> ModelEntryConfig:
        try:
            ModelTier(self.tier)
        except ValueError:
            valid = [t.value for t in ModelTier]
            raise ValueError(f"Invalid model tier '{self.tier}'. Valid values: {valid}") from None
        return self

    def to_model_entry(self, model_id: str) -> ModelEntry:
        """Convert to a frozen :class:`ModelEntry` dataclass."""
        return ModelEntry(model_id=model_id, tier=ModelTier(self.tier), variant=self.variant)


def resolve_model(name: str, *, variant: str | None = None, models_config: ModelsConfig | None = None) -> str:
    """Resolve a tier name or model ID to a model ID string.

    Accepts either a tier name (e.g. "SIMPLE", "STANDARD", "ADVANCED")
    or a specific model ID (e.g. "claude-sonnet-4-6").

    When *variant* is ``None`` (the default), returns the model ID from
    the effective tier-defaults for the requested tier — identical to
    pre-variant behavior.

    When *variant* is provided, scans the effective registry for a
    ``(tier, variant)`` match.  If no match is found, falls back to the
    tier default and emits a DEBUG-level log.  No exception is ever raised
    for an unmatched or unrecognized variant string.

    When *models_config* is provided, user-supplied registry entries and
    tier-defaults from ``[models]`` in ``config.toml`` are overlaid on top
    of the hardcoded :data:`MODEL_REGISTRY` and :data:`TIER_DEFAULTS`.

    Args:
        name: A tier name (e.g. ``"ADVANCED"``) or a model ID string.
        variant: Optional variant label (e.g. ``"extended"``).
        models_config: Optional config-driven model overrides. When provided,
            user entries overlay the hardcoded registry and tier defaults.

    Returns:
        A model ID string (e.g. ``"claude-opus-4-6[1m]"``).

    Raises:
        ConfigError: If *name* is not a recognized tier or model ID.

    Requirements: 14-REQ-7.1, 14-REQ-7.2, 14-REQ-7.3, 14-REQ-7.4,
                  14-REQ-9.1, 14-REQ-9.2, 14-REQ-9.3, 01-REQ-5.1
    """
    from afcore.core.errors import ConfigError

    # Build effective registry and tier-defaults, overlaying user config.
    if models_config is not None and (models_config.registry or models_config.tier_defaults):
        effective_registry: dict[str, ModelEntry] = dict(MODEL_REGISTRY)
        for mid, entry_cfg in models_config.registry.items():
            effective_registry[mid] = entry_cfg.to_model_entry(mid)
        effective_tier_defaults: dict[ModelTier, str] = dict(TIER_DEFAULTS)
        for tier_name, mid in models_config.tier_defaults.items():
            try:
                effective_tier_defaults[ModelTier(tier_name)] = mid
            except ValueError:
                pass  # validated at config load time; skip silently here
    else:
        effective_registry = MODEL_REGISTRY
        effective_tier_defaults = TIER_DEFAULTS

    # Try as a tier name first
    try:
        tier = ModelTier(name)
    except ValueError:
        tier = None

    if tier is not None:
        if variant is None:
            # Backward-compatible path: return tier-default model ID.
            return effective_tier_defaults[tier]

        # Scan effective registry for an entry matching (tier, variant).
        for entry in effective_registry.values():
            if entry.tier == tier and entry.variant == variant:
                return entry.model_id

        # Fallback: no match found for (tier, variant).
        logger.debug(
            "No model found for tier=%s variant=%s; falling back to tier default %s",
            tier,
            variant,
            effective_tier_defaults[tier],
        )
        return effective_tier_defaults[tier]

    # Try as a direct model ID
    if name in effective_registry:
        return name

    valid_options = sorted(effective_registry.keys())
    raise ConfigError(
        f"Unknown model '{name}'. Valid options: {', '.join(valid_options)}",
        model=name,
        valid_options=valid_options,
    )


def collect_configured_model_ids(models_config: ModelsConfig | None = None) -> set[str]:
    """Collect all unique model IDs that nightshift archetypes will use.

    Iterates over every archetype and its mode variants, resolving each
    tier+variant pair to a concrete model ID. Returns the deduplicated
    set of model IDs.

    Args:
        models_config: Optional config-driven model overrides from
            ``[models]`` in config.toml.

    Returns:
        A set of model ID strings that will be used at runtime.

    Requirements: NS-REQ-4
    """
    from afcore.archetypes import ARCHETYPE_REGISTRY, resolve_effective_config

    model_ids: set[str] = set()
    for entry in ARCHETYPE_REGISTRY.values():
        # Base archetype tier/variant
        try:
            model_ids.add(
                resolve_model(
                    entry.default_model_tier,
                    variant=entry.default_model_variant,
                    models_config=models_config,
                )
            )
        except Exception:
            pass  # resolve_model already logs; skip gracefully

        # Mode-specific overrides
        for mode_name in entry.modes:
            resolved = resolve_effective_config(entry, mode=mode_name)
            try:
                model_ids.add(
                    resolve_model(
                        resolved.default_model_tier,
                        variant=resolved.default_model_variant,
                        models_config=models_config,
                    )
                )
            except Exception:
                pass
    return model_ids


def validate_model_access(models_config: ModelsConfig | None = None) -> None:
    """Validate that all configured model IDs are accessible via the API key.

    Calls the Anthropic models API to list available models, then checks
    that every model ID nightshift will use is present in the response.

    **Fail-open on network errors**: if the API is unreachable (connection
    error, timeout, etc.) a warning is logged and startup continues.

    **Exit on inaccessible models**: if one or more configured model IDs
    are not in the API response, logs an error naming each inaccessible
    model and calls ``sys.exit(1)``.

    Args:
        models_config: Optional config-driven model overrides from
            ``[models]`` in config.toml.

    Requirements: NS-REQ-3, NS-REQ-4, NS-REQ-5
    """
    import sys

    configured_ids = collect_configured_model_ids(models_config)
    if not configured_ids:
        return

    try:
        from afcore.core.client import create_anthropic_client

        client = create_anthropic_client()
        try:
            available: set[str] = set()
            # Anthropic SDK models.list() returns a paginated SyncPage.
            page = client.models.list(limit=1000)
            for model in page.data:
                available.add(model.id)
        finally:
            client.close()
    except Exception:
        logger.warning(
            "Unable to validate model access — API unreachable; continuing startup",
            exc_info=True,
        )
        return

    inaccessible = sorted(configured_ids - available)
    if inaccessible:
        logger.error(
            "The following model(s) are not accessible with the current API key: %s. "
            "Check your API key permissions or update [models] in config.toml.",
            ", ".join(inaccessible),
        )
        sys.exit(1)

    logger.info(
        "Model access validated: %d model(s) confirmed accessible",
        len(configured_ids),
    )


def calculate_cost(
    input_tokens: int,
    output_tokens: int,
    model_id: str,
    pricing: PricingConfig,
    *,
    cache_read_input_tokens: int = 0,
    cache_creation_input_tokens: int = 0,
) -> float:
    """Calculate estimated cost in USD using config-based pricing.

    Falls back to zero cost if model not found in pricing config.

    Args:
        input_tokens: Number of input tokens consumed.
        output_tokens: Number of output tokens produced.
        model_id: The model identifier string.
        pricing: The pricing configuration with per-model rates.
        cache_read_input_tokens: Number of cache-read input tokens.
        cache_creation_input_tokens: Number of cache-creation input tokens.

    Returns:
        Estimated cost in USD as a float.

    Requirements: 34-REQ-2.3, 34-REQ-2.4
    """
    model_pricing = pricing.models.get(model_id)
    if model_pricing is None:
        logger.warning(
            "Model '%s' not found in pricing config; using zero cost",
            model_id,
        )
        return 0.0

    input_cost = (input_tokens / 1_000_000) * model_pricing.input_price_per_m
    output_cost = (output_tokens / 1_000_000) * model_pricing.output_price_per_m
    cache_read_cost = (cache_read_input_tokens / 1_000_000) * model_pricing.cache_read_price_per_m
    cache_creation_cost = (cache_creation_input_tokens / 1_000_000) * model_pricing.cache_creation_price_per_m
    return input_cost + output_cost + cache_read_cost + cache_creation_cost
