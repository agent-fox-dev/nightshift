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


class TestCollectConfiguredModelIds:
    """Collect all model IDs from archetype tier/variant combos.

    Requirements: NS-REQ-4
    """

    def test_returns_non_empty_set(self) -> None:
        """At least one model ID is collected from the archetype registry."""
        ids = collect_configured_model_ids()
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

    def test_covers_mode_variants(self) -> None:
        """Mode-specific tier/variant combos produce additional model IDs.

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

        TS-NS-4: Variant model ID is checked against API response.
        Requirements: NS-REQ-4
        """
        models_cfg = ModelsConfig()

        # Mock collect_configured_model_ids to return a custom model ID
        # that is NOT in the available API models.
        with (
            patch(
                "afcore.core.models.collect_configured_model_ids",
                return_value={"custom-model-1"},
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

        TS-NS-4: Variant-level model IDs are enumerated.
        Requirements: NS-REQ-4
        """
        # Add a custom model entry with a novel variant so it becomes a
        # unique (tier, variant) pair that no hardcoded entry shadows.
        models_cfg = ModelsConfig(
            registry={
                "custom-advanced-ext": {
                    "tier": "ADVANCED",
                    "variant": "turbo",
                },
            },
        )
        ids = collect_configured_model_ids(models_config=models_cfg)
        # The custom variant is never referenced by any archetype, so it
        # won't appear. But existing archetype models should be collected.
        assert "claude-opus-4-6" in ids  # reviewer:pre-flight uses ADVANCED/standard
        assert "claude-sonnet-4-6" in ids  # coder uses STANDARD/standard
        assert "claude-haiku-4-5" in ids  # maintainer:hunt uses SIMPLE/standard

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
