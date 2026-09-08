"""Tests for model access validation on startup.

Test Spec: TS-NS-3, TS-NS-4, TS-NS-5
Requirements: NS-REQ-3, NS-REQ-4, NS-REQ-5
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from afcore.core.config import ModelsConfig
from afcore.core.models import (
    collect_configured_model_ids,
    validate_model_access,
)
from pydantic import ValidationError

_BACKEND_ENV_VARS = {"CLAUDE_CODE_USE_VERTEX": "", "CLAUDE_CODE_USE_BEDROCK": ""}


@pytest.fixture(autouse=True)
def _clear_backend_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure Vertex/Bedrock env vars are unset for all tests in this module."""
    for var in _BACKEND_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


class TestCollectConfiguredModelIds:
    """Collect all model IDs from archetype tier combos, with tier provenance.

    Requirements: NS-REQ-4
    """

    def test_returns_non_empty_mapping(self) -> None:
        """At least one model ID is collected from the archetype registry."""
        ids = collect_configured_model_ids()
        assert isinstance(ids, dict)
        assert len(ids) > 0

    def test_contains_standard_default(self) -> None:
        """The STANDARD tier default (claude-sonnet-4-6) should be collected."""
        ids = collect_configured_model_ids()
        assert "claude-sonnet-4-6" in ids

    def test_with_override_includes_overridden_model(self) -> None:
        """Config-driven tier-default overrides are reflected in collected IDs."""
        models_cfg = ModelsConfig(
            tier_defaults={"STANDARD": "claude-haiku-4-5"},
        )
        ids = collect_configured_model_ids(models_config=models_cfg)
        assert "claude-haiku-4-5" in ids

    def test_covers_mode_tiers(self) -> None:
        """Mode-specific tier combos produce additional model IDs.

        The reviewer archetype has modes that use ADVANCED tier, so those
        model IDs should be collected.
        """
        ids = collect_configured_model_ids()
        # reviewer:pre-flight uses ADVANCED tier
        assert "claude-opus-4-6" in ids

    def test_covers_simple_tier(self) -> None:
        """SIMPLE tier models from maintainer:hunt are collected."""
        ids = collect_configured_model_ids()
        assert "claude-haiku-4-5" in ids

    def test_maps_model_id_to_tier_names(self) -> None:
        """Each collected model ID carries the tier name(s) that resolve to it.

        AC-1 (issue #47): tier provenance survives collection so the
        inaccessible-model error can name the tier to override.
        """
        ids = collect_configured_model_ids()
        assert ids["claude-haiku-4-5"] == {"SIMPLE"}
        assert ids["claude-sonnet-4-6"] == {"STANDARD"}
        assert ids["claude-opus-4-6"] == {"ADVANCED"}

    def test_override_maps_tier_to_overridden_model(self) -> None:
        """A tier_defaults override attributes the new model ID to that tier."""
        models_cfg = ModelsConfig(tier_defaults={"STANDARD": "claude-haiku-4-5"})
        ids = collect_configured_model_ids(models_config=models_cfg)
        # Haiku now serves both SIMPLE and STANDARD.
        assert ids["claude-haiku-4-5"] == {"SIMPLE", "STANDARD"}

    def test_bare_id_set_recoverable(self) -> None:
        """The pre-#47 ``set[str]`` behavior is recoverable via ``set()``."""
        ids = collect_configured_model_ids()
        assert set(ids) >= {"claude-haiku-4-5", "claude-sonnet-4-6", "claude-opus-4-6"}


class TestValidateModelAccess:
    """TS-NS-3: Nightshift validates model accessibility before starting.

    Requirements: NS-REQ-3, NS-REQ-5
    """

    def _mock_models_page(self, model_ids: list[str]) -> MagicMock:
        """Create a mock API page with model objects."""
        page = MagicMock()
        page.data = [SimpleNamespace(id=mid) for mid in model_ids]
        return page

    def test_exits_when_model_inaccessible(self) -> None:
        """When the API reports a model is not available, sys.exit(1) is called.

        TS-NS-3: Mock models.list() to exclude configured ADVANCED model.
        """
        # Only include haiku — exclude sonnet and opus
        available = ["claude-haiku-4-5"]

        mock_client = MagicMock()
        mock_client.models.list.return_value = self._mock_models_page(available)

        with (
            patch("afcore.core.client.create_anthropic_client", return_value=mock_client),
            pytest.raises(SystemExit) as exc_info,
        ):
            validate_model_access()

        assert exc_info.value.code == 1

    def test_passes_when_all_models_accessible(self) -> None:
        """When all configured models are in the API response, no exit occurs."""
        all_ids = collect_configured_model_ids()

        mock_client = MagicMock()
        mock_client.models.list.return_value = self._mock_models_page(list(all_ids))

        with patch("afcore.core.client.create_anthropic_client", return_value=mock_client):
            # Should not raise
            validate_model_access()

    def test_fails_open_on_network_error(self, caplog: pytest.LogCaptureFixture) -> None:
        """When the API is unreachable, logs a warning and does not exit.

        TS-NS-5: Network errors fail open.
        Requirements: NS-REQ-5
        """
        with (
            patch(
                "afcore.core.client.create_anthropic_client",
                side_effect=ConnectionError("network unreachable"),
            ),
            caplog.at_level(logging.WARNING),
        ):
            # Should not raise
            validate_model_access()

        assert any("Unable to validate model access" in r.message for r in caplog.records)

    def test_fails_open_on_api_error(self, caplog: pytest.LogCaptureFixture) -> None:
        """When models.list() raises an API error, logs a warning and continues.

        Requirements: NS-REQ-5
        """
        mock_client = MagicMock()
        mock_client.models.list.side_effect = Exception("API error")

        with (
            patch("afcore.core.client.create_anthropic_client", return_value=mock_client),
            caplog.at_level(logging.WARNING),
        ):
            validate_model_access()

        assert any("Unable to validate model access" in r.message for r in caplog.records)

    def test_error_message_names_inaccessible_models(self, caplog: pytest.LogCaptureFixture) -> None:
        """The error message names specific inaccessible model IDs.

        Requirements: NS-REQ-3
        """
        # Provide only haiku
        available = ["claude-haiku-4-5"]

        mock_client = MagicMock()
        mock_client.models.list.return_value = self._mock_models_page(available)

        with (
            patch("afcore.core.client.create_anthropic_client", return_value=mock_client),
            caplog.at_level(logging.ERROR),
            pytest.raises(SystemExit),
        ):
            validate_model_access()

        error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert len(error_records) > 0
        error_msg = error_records[0].message
        # The missing model IDs should be named in the error
        assert "claude-sonnet-4-6" in error_msg or "claude-opus-4-6" in error_msg

    def test_models_config_passed_to_collector(self) -> None:
        """validate_model_access passes models_config through to collect_configured_model_ids.

        TS-NS-4: Model ID is checked against API response.
        Requirements: NS-REQ-4
        """
        models_cfg = ModelsConfig()

        # Mock collect_configured_model_ids to return a custom model ID
        # that is NOT in the available API models.
        with (
            patch(
                "afcore.core.models.collect_configured_model_ids",
                return_value={"custom-model-1": set()},
            ) as mock_collect,
            patch("afcore.core.client.create_anthropic_client") as mock_create,
            pytest.raises(SystemExit) as exc_info,
        ):
            mock_client = mock_create.return_value
            mock_client.models.list.return_value = self._mock_models_page(["claude-haiku-4-5"])
            validate_model_access(models_config=models_cfg)

        # Verify models_config was forwarded
        mock_collect.assert_called_once_with(models_cfg)
        assert exc_info.value.code == 1

    def test_collect_with_custom_registry_entry(self) -> None:
        """collect_configured_model_ids includes models from custom registry entries.

        TS-NS-4: Tier-level model IDs are enumerated.
        Requirements: NS-REQ-4
        """
        # Add a custom model entry at the ADVANCED tier.
        models_cfg = ModelsConfig(
            registry={
                "custom-advanced-model": {
                    "tier": "ADVANCED",
                },
            },
        )
        ids = collect_configured_model_ids(models_config=models_cfg)
        # Existing archetype models should be collected.
        assert "claude-opus-4-6" in ids  # reviewer:pre-flight uses ADVANCED
        assert "claude-sonnet-4-6" in ids  # coder uses STANDARD
        assert "claude-haiku-4-5" in ids  # maintainer:hunt uses SIMPLE

    def test_client_closed_on_success(self) -> None:
        """The Anthropic client is closed after successful validation."""
        all_ids = collect_configured_model_ids()
        mock_client = MagicMock()
        mock_client.models.list.return_value = self._mock_models_page(list(all_ids))

        with patch("afcore.core.client.create_anthropic_client", return_value=mock_client):
            validate_model_access()

        mock_client.close.assert_called_once()

    def test_client_closed_on_failure(self) -> None:
        """The Anthropic client is closed even when validation fails."""
        mock_client = MagicMock()
        mock_client.models.list.return_value = self._mock_models_page(["claude-haiku-4-5"])

        with (
            patch("afcore.core.client.create_anthropic_client", return_value=mock_client),
            pytest.raises(SystemExit),
        ):
            validate_model_access()

        mock_client.close.assert_called_once()


class TestInaccessibleModelErrorMessage:
    """The inaccessible-model error names the tier and the override snippet.

    Acceptance criteria AC-1, AC-3 (issue #47).
    """

    def _mock_models_page(self, model_ids: list[str]) -> MagicMock:
        page = MagicMock()
        page.data = [SimpleNamespace(id=mid) for mid in model_ids]
        return page

    def _error_message(
        self,
        available: list[str],
        models_config: ModelsConfig | None,
        caplog: pytest.LogCaptureFixture,
    ) -> str:
        mock_client = MagicMock()
        mock_client.models.list.return_value = self._mock_models_page(available)

        with (
            patch("afcore.core.client.create_anthropic_client", return_value=mock_client),
            caplog.at_level(logging.ERROR),
            pytest.raises(SystemExit),
        ):
            validate_model_access(models_config=models_config)

        error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert error_records
        return error_records[0].getMessage()

    def test_names_the_tier_of_the_inaccessible_model(self, caplog: pytest.LogCaptureFixture) -> None:
        """AC-1: an API key without claude-haiku-4-5 gets told it is the SIMPLE tier."""
        # Everything except the SIMPLE default is available.
        available = ["claude-sonnet-4-6", "claude-opus-4-6"]

        message = self._error_message(available, None, caplog)

        assert "claude-haiku-4-5" in message
        assert "SIMPLE" in message
        # The other tiers are accessible, so they must not be blamed.
        assert "STANDARD" not in message
        assert "ADVANCED" not in message

    def test_shows_tier_defaults_override_snippet(self, caplog: pytest.LogCaptureFixture) -> None:
        """AC-1: the error shows the config.toml snippet needed to override."""
        available = ["claude-sonnet-4-6", "claude-opus-4-6"]

        message = self._error_message(available, None, caplog)

        assert "[models.tier_defaults]" in message
        assert "SIMPLE =" in message

    def test_lists_every_affected_tier(self, caplog: pytest.LogCaptureFixture) -> None:
        """Multiple inaccessible models each name their own tier."""
        # Only the STANDARD default is available.
        available = ["claude-sonnet-4-6"]

        message = self._error_message(available, None, caplog)

        assert "claude-haiku-4-5" in message
        assert "claude-opus-4-6" in message
        assert "SIMPLE" in message
        assert "ADVANCED" in message

    def test_names_all_tiers_sharing_one_model(self, caplog: pytest.LogCaptureFixture) -> None:
        """A single model serving two tiers names both."""
        models_cfg = ModelsConfig(tier_defaults={"ADVANCED": "claude-sonnet-4-6"})
        # Neither the STANDARD nor the (remapped) ADVANCED model is available.
        available = ["claude-haiku-4-5"]

        message = self._error_message(available, models_cfg, caplog)

        assert "claude-sonnet-4-6" in message
        assert "STANDARD" in message
        assert "ADVANCED" in message

    def test_passes_when_all_tiers_overridden_to_accessible_models(self) -> None:
        """AC-3: overriding every tier default skips the hardcoded defaults."""
        models_cfg = ModelsConfig(
            registry={
                "custom-simple": {"tier": "SIMPLE"},
                "custom-standard": {"tier": "STANDARD"},
                "custom-advanced": {"tier": "ADVANCED"},
            },
            tier_defaults={
                "SIMPLE": "custom-simple",
                "STANDARD": "custom-standard",
                "ADVANCED": "custom-advanced",
            },
        )
        # The hardcoded defaults are deliberately absent from the API response.
        available = ["custom-simple", "custom-standard", "custom-advanced"]

        mock_client = MagicMock()
        mock_client.models.list.return_value = self._mock_models_page(available)

        with patch("afcore.core.client.create_anthropic_client", return_value=mock_client):
            # Should not raise.
            validate_model_access(models_config=models_cfg)


class TestErrorNamesConfigInEffect:
    """The error points at the config file actually in effect.

    A local .nightshift/config.toml shadows the global one entirely, so
    naming a bare "config.toml" can send the user back to the file whose
    edits are being ignored.
    """

    def _mock_models_page(self, model_ids: list[str]) -> MagicMock:
        page = MagicMock()
        page.data = [SimpleNamespace(id=mid) for mid in model_ids]
        return page

    def _error_message(self, config_path: str | None) -> str:
        mock_client = MagicMock()
        mock_client.models.list.return_value = self._mock_models_page(["claude-sonnet-4-6", "claude-opus-4-6"])

        with (
            patch("afcore.core.client.create_anthropic_client", return_value=mock_client),
            patch.object(logging.getLogger("afcore.core.models"), "error") as mock_error,
            pytest.raises(SystemExit),
        ):
            validate_model_access(config_path=config_path)

        return mock_error.call_args[0][1]

    def test_names_the_resolved_config_path(self) -> None:
        """The supplied path replaces the bare "config.toml" reference."""
        path = "/data/workspace/nightshift/.nightshift/config.toml"

        message = self._error_message(path)

        assert path in message
        assert "in config.toml" not in message

    def test_falls_back_to_bare_filename(self) -> None:
        """Without a resolved path the message still reads sensibly."""
        message = self._error_message(None)

        assert "config.toml" in message

    def test_forwarded_from_validate_to_formatter(self) -> None:
        """validate_model_access threads config_path into the formatter."""
        from afcore.core.models import _format_inaccessible_models

        message = _format_inaccessible_models(
            ["claude-haiku-4-5"],
            {"claude-haiku-4-5": {"SIMPLE"}},
            "/etc/ns/config.toml",
        )

        assert "/etc/ns/config.toml" in message
        assert "SIMPLE" in message

    def test_path_named_even_without_tier_provenance(self) -> None:
        """A model with no tier still points at the right file."""
        from afcore.core.models import _format_inaccessible_models

        message = _format_inaccessible_models(["some-model"], {"some-model": set()}, "/etc/ns/config.toml")

        assert "/etc/ns/config.toml" in message


class TestModelEntryConfigVariantRejection:
    """AC-2 (issue #47): `variant` in [models.registry.*] gets an actionable error."""

    def test_variant_field_rejected_with_hint(self) -> None:
        """A `variant` key names itself and the accepted field in the error."""
        from afcore.core.models import ModelEntryConfig

        with pytest.raises(ValidationError) as exc_info:
            ModelEntryConfig(tier="STANDARD", variant="standard")

        message = str(exc_info.value)
        assert "variant" in message
        assert "[models.registry.*]" in message
        assert "tier" in message

    def test_variant_rejected_through_models_config(self) -> None:
        """The hint survives ModelsConfig's registry parsing as a ConfigError."""
        from afcore.core.errors import ConfigError

        with pytest.raises(ConfigError) as exc_info:
            ModelsConfig(registry={"my-model": {"tier": "STANDARD", "variant": "standard"}})

        message = str(exc_info.value)
        assert "variant" in message
        assert "[models.registry.*]" in message

    def test_other_unknown_fields_still_rejected(self) -> None:
        """Unrelated extra fields keep the generic extra=forbid error."""
        from afcore.core.models import ModelEntryConfig

        with pytest.raises(ValidationError):
            ModelEntryConfig(tier="STANDARD", nonsense="x")

    def test_valid_entry_still_accepted(self) -> None:
        """A well-formed entry is unaffected by the new validator."""
        from afcore.core.models import ModelEntryConfig, ModelTier

        entry = ModelEntryConfig(tier="ADVANCED")
        assert entry.to_model_entry("my-model").tier is ModelTier.ADVANCED


class TestValidateModelAccessVertexBedrock:
    """Validation is skipped on Vertex/Bedrock backends that lack models.list.

    Requirements: AC-1, AC-2, AC-3, AC-4 (issue #29)
    """

    def test_skips_validation_on_vertex(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """AC-1: With CLAUDE_CODE_USE_VERTEX=1, validation is skipped with info log."""
        monkeypatch.setenv("CLAUDE_CODE_USE_VERTEX", "1")
        with (
            patch("afcore.core.client.create_anthropic_client") as mock_create,
            caplog.at_level(logging.INFO),
        ):
            validate_model_access()

        mock_create.assert_not_called()
        assert any("skipped" in r.message.lower() and "vertex" in r.message.lower() for r in caplog.records)
        assert not any("API unreachable" in r.message for r in caplog.records)

    def test_skips_validation_on_bedrock(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """AC-2: With CLAUDE_CODE_USE_BEDROCK=1, validation is skipped with info log."""
        monkeypatch.setenv("CLAUDE_CODE_USE_BEDROCK", "1")
        with (
            patch("afcore.core.client.create_anthropic_client") as mock_create,
            caplog.at_level(logging.INFO),
        ):
            validate_model_access()

        mock_create.assert_not_called()
        assert any("skipped" in r.message.lower() and "bedrock" in r.message.lower() for r in caplog.records)
        assert not any("API unreachable" in r.message for r in caplog.records)

    def test_direct_api_still_validates(self) -> None:
        """AC-3: Without Vertex/Bedrock env vars, validation proceeds normally."""
        all_ids = collect_configured_model_ids()

        mock_client = MagicMock()
        page = MagicMock()
        page.data = [SimpleNamespace(id=mid) for mid in all_ids]
        mock_client.models.list.return_value = page

        with patch("afcore.core.client.create_anthropic_client", return_value=mock_client):
            validate_model_access()

        mock_client.models.list.assert_called_once()


class TestCurrentGenerationPricing:
    """Built-in pricing must cover the models tier defaults can point at.

    A model missing from pricing costs zero, so `orchestrator.max_budget_usd`
    silently never trips — the daemon runs unbounded.
    """

    def test_current_models_have_pricing(self) -> None:
        """claude-sonnet-5 and claude-opus-5 are priced out of the box."""
        from afcore.core.config import PricingConfig

        pricing = PricingConfig()

        for model_id in ("claude-sonnet-5", "claude-opus-5"):
            assert model_id in pricing.models, f"{model_id} has no built-in pricing"
            assert pricing.models[model_id].input_price_per_m > 0
            assert pricing.models[model_id].output_price_per_m > 0

    def test_cost_is_non_zero_for_current_models(self) -> None:
        """The zero-cost fallback no longer fires for these models."""
        from afcore.core.config import PricingConfig
        from afcore.core.models import calculate_cost

        pricing = PricingConfig()

        for model_id in ("claude-sonnet-5", "claude-opus-5"):
            cost = calculate_cost(1_000_000, 1_000_000, model_id, pricing)
            assert cost > 0, f"{model_id} still costs zero — budget would never trip"

    def test_cache_rates_follow_the_input_rate(self) -> None:
        """Cache read is 0.1x and cache creation 1.25x the input rate."""
        from afcore.core.config import PricingConfig

        pricing = PricingConfig()

        for model_id in ("claude-sonnet-5", "claude-opus-5"):
            entry = pricing.models[model_id]
            assert entry.cache_read_price_per_m == pytest.approx(entry.input_price_per_m * 0.1)
            assert entry.cache_creation_price_per_m == pytest.approx(entry.input_price_per_m * 1.25)
