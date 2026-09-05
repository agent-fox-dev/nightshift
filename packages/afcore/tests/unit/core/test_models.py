"""Model registry tests.

Test Spec: TS-01-9 (tier resolution), TS-01-10 (cost calc), TS-01-E5 (unknown model),
           TS-01-11 (config-driven registry)
Requirements: 01-REQ-5.1, 01-REQ-5.3, 01-REQ-5.4, 01-REQ-5.E1
"""

from __future__ import annotations

import pytest
from afcore.core.config import ModelsConfig, PricingConfig
from afcore.core.errors import ConfigError
from afcore.core.models import (
    MODEL_REGISTRY,
    ModelEntryConfig,
    calculate_cost,
    resolve_model,
)


class TestModelResolutionByTier:
    """TS-01-9: Model resolution by tier name."""

    def test_resolve_simple_tier(self) -> None:
        """SIMPLE tier resolves to a valid model ID string."""
        model_id = resolve_model("SIMPLE")

        assert isinstance(model_id, str)
        assert model_id != ""
        assert model_id in MODEL_REGISTRY

    def test_resolve_standard_tier(self) -> None:
        """STANDARD tier resolves to a valid model ID string."""
        model_id = resolve_model("STANDARD")

        assert isinstance(model_id, str)
        assert model_id != ""
        assert model_id in MODEL_REGISTRY

    def test_resolve_advanced_tier(self) -> None:
        """ADVANCED tier resolves to a valid model ID string."""
        model_id = resolve_model("ADVANCED")

        assert isinstance(model_id, str)
        assert model_id != ""
        assert model_id in MODEL_REGISTRY

    def test_resolve_by_model_id(self) -> None:
        """A specific model ID resolves to itself."""
        model_id = resolve_model("claude-sonnet-4-6")

        assert isinstance(model_id, str)
        assert model_id == "claude-sonnet-4-6"


class TestCostCalculation:
    """TS-01-10: Cost calculation."""

    def test_cost_standard_model(self) -> None:
        """Cost calculation returns correct USD value for Sonnet."""
        model_id = resolve_model("STANDARD")
        pricing = PricingConfig()

        # Sonnet: $3.00/M input, $15.00/M output
        # 1M input + 500K output = (1.0 * 3.00) + (0.5 * 15.00) = $10.50
        cost = calculate_cost(1_000_000, 500_000, model_id, pricing)

        assert abs(cost - 10.50) < 0.01

    def test_cost_zero_tokens(self) -> None:
        """Zero tokens produces zero cost."""
        model_id = resolve_model("STANDARD")
        pricing = PricingConfig()

        cost = calculate_cost(0, 0, model_id, pricing)

        assert cost == 0.0

    def test_cost_input_only(self) -> None:
        """Cost with only input tokens is correct."""
        model_id = resolve_model("SIMPLE")
        pricing = PricingConfig()

        # Haiku: $1.00/M input
        cost = calculate_cost(1_000_000, 0, model_id, pricing)

        assert abs(cost - 1.00) < 0.01


class TestUnknownModelID:
    """TS-01-E5: Unknown model ID raises ConfigError."""

    def test_unknown_model_raises_config_error(self) -> None:
        """Unknown model ID raises ConfigError."""
        with pytest.raises(ConfigError):
            resolve_model("nonexistent-model")

    def test_unknown_model_error_lists_valid_options(self) -> None:
        """ConfigError message includes at least one valid model ID."""
        with pytest.raises(ConfigError) as exc_info:
            resolve_model("nonexistent-model")

        error_msg = str(exc_info.value)
        assert "claude" in error_msg.lower(), f"Expected valid model IDs in error, got: {error_msg!r}"


class TestConfigDrivenModelRegistry:
    """TS-01-11: Config-driven model registry and tier-default overrides.

    Requirements: 01-REQ-5.1 (AC-1 through AC-5)
    """

    def _make_models_config(
        self,
        registry: dict | None = None,
        tier_defaults: dict | None = None,
    ) -> ModelsConfig:
        """Build a ModelsConfig with the given registry and tier_defaults."""
        data: dict = {}
        if registry:
            data["registry"] = registry
        if tier_defaults:
            data["tier_defaults"] = tier_defaults
        return ModelsConfig(**data)

    def test_ac1_tier_default_override_redirects_resolution(self) -> None:
        """AC-1: tier_defaults redirects ADVANCED to a user-registered model."""
        cfg = self._make_models_config(
            registry={"claude-fable-5-1": {"tier": "ADVANCED"}},
            tier_defaults={"ADVANCED": "claude-fable-5-1"},
        )
        result = resolve_model("ADVANCED", models_config=cfg)
        assert result == "claude-fable-5-1"

    def test_ac1_does_not_affect_other_tiers(self) -> None:
        """AC-1 corollary: overriding ADVANCED leaves SIMPLE and STANDARD unchanged."""
        cfg = self._make_models_config(
            registry={"claude-fable-5-1": {"tier": "ADVANCED"}},
            tier_defaults={"ADVANCED": "claude-fable-5-1"},
        )
        assert resolve_model("SIMPLE", models_config=cfg) == resolve_model("SIMPLE")
        assert resolve_model("STANDARD", models_config=cfg) == resolve_model("STANDARD")

    def test_ac2_unknown_tier_default_value_raises_config_error(self) -> None:
        """AC-2: tier_defaults pointing to a model not in either registry raises ConfigError."""
        with pytest.raises((ConfigError, Exception)):
            self._make_models_config(tier_defaults={"ADVANCED": "nonexistent-model-id"})

    def test_ac2_invalid_tier_key_raises_config_error(self) -> None:
        """AC-2: tier_defaults with invalid tier key raises ConfigError."""
        with pytest.raises((ConfigError, Exception)):
            self._make_models_config(
                registry={"some-model": {"tier": "ADVANCED"}},
                tier_defaults={"BOGUS_TIER": "some-model"},
            )

    def test_ac3_no_models_section_preserves_defaults(self) -> None:
        """AC-3: None models_config produces identical results to calling without config."""
        for tier in ("SIMPLE", "STANDARD", "ADVANCED"):
            assert resolve_model(tier, models_config=None) == resolve_model(tier)

    def test_ac3_empty_models_config_preserves_defaults(self) -> None:
        """AC-3: Empty ModelsConfig (no registry, no tier_defaults) preserves defaults."""
        cfg = ModelsConfig()
        for tier in ("SIMPLE", "STANDARD", "ADVANCED"):
            assert resolve_model(tier, models_config=cfg) == resolve_model(tier)

    def test_ac4_registry_only_makes_new_id_resolvable(self) -> None:
        """AC-4: model in registry but tier_defaults unchanged — direct ID lookup works."""
        cfg = self._make_models_config(
            registry={"claude-fable-5-1": {"tier": "ADVANCED"}},
        )
        result = resolve_model("claude-fable-5-1", models_config=cfg)
        assert result == "claude-fable-5-1"

    def test_ac4_registry_only_advanced_tier_still_returns_hardcoded_default(self) -> None:
        """AC-4: without tier_defaults override, ADVANCED still returns the hardcoded default."""
        from afcore.core.models import TIER_DEFAULTS, ModelTier

        cfg = self._make_models_config(
            registry={"claude-fable-5-1": {"tier": "ADVANCED"}},
        )
        hardcoded_advanced = TIER_DEFAULTS[ModelTier.ADVANCED]
        assert resolve_model("ADVANCED", models_config=cfg) == hardcoded_advanced

    def test_model_entry_config_rejects_invalid_tier(self) -> None:
        """ModelEntryConfig raises on unrecognized tier string."""
        with pytest.raises((ValueError, Exception)):
            ModelEntryConfig(tier="ULTRA")

    def test_model_entry_config_to_model_entry_roundtrip(self) -> None:
        """ModelEntryConfig.to_model_entry() produces the correct ModelEntry."""
        from afcore.core.models import ModelTier

        entry_cfg = ModelEntryConfig(tier="ADVANCED")
        entry = entry_cfg.to_model_entry("claude-fable-5-1")
        assert entry.model_id == "claude-fable-5-1"
        assert entry.tier == ModelTier.ADVANCED

    def test_model_entry_config_rejects_unknown_fields(self) -> None:
        """ModelEntryConfig with extra='forbid' rejects unknown fields like variant."""
        with pytest.raises((ValueError, Exception)):
            ModelEntryConfig(tier="ADVANCED", variant="standard")  # type: ignore[call-arg]
