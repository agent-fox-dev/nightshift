"""Tests for nightshift_ai_call forwarding cache_policy from config.

Issue #40: cache_policy should be forwarded from config.caching.cache_policy
to the underlying ai_call() so that direct Anthropic API calls (knowledge
extraction, triage, staleness checks) respect the operator's caching preference.

TS-NS-3: nightshift_ai_call forwards config.caching.cache_policy to ai_call.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from afcore.core.config import CachePolicy


def _make_config(cache_policy: CachePolicy = CachePolicy.DEFAULT) -> MagicMock:
    """Build a minimal config with a caching section."""
    config = MagicMock()
    config.caching.cache_policy = cache_policy
    config.models = None
    config.pricing = MagicMock()
    config.pricing.models = {}
    return config


class TestNightshiftAiCallCachePolicy:
    """nightshift_ai_call forwards cache_policy from config to ai_call."""

    @pytest.mark.asyncio
    async def test_forwards_extended_policy(self) -> None:
        """EXTENDED in config → ai_call receives cache_policy=CachePolicy.EXTENDED."""
        config = _make_config(CachePolicy.EXTENDED)

        mock_response = SimpleNamespace(
            usage=SimpleNamespace(
                input_tokens=100,
                output_tokens=50,
                cache_read_input_tokens=0,
                cache_creation_input_tokens=0,
            ),
        )

        with (
            patch(
                "afcore.core.client.ai_call",
                new_callable=AsyncMock,
                return_value=("response text", mock_response),
            ) as mock_ai_call,
            patch("afcore.core.models.resolve_model", return_value="claude-sonnet-4-6"),
        ):
            from afcore.nightshift.cost_helpers import nightshift_ai_call

            await nightshift_ai_call(
                model_tier="STANDARD",
                max_tokens=4096,
                messages=[{"role": "user", "content": "test"}],
                context="test",
                cost_label="test",
                config=config,
            )

        # Verify ai_call was called with cache_policy=EXTENDED
        mock_ai_call.assert_called_once()
        call_kwargs = mock_ai_call.call_args
        assert call_kwargs.kwargs["cache_policy"] == CachePolicy.EXTENDED

    @pytest.mark.asyncio
    async def test_forwards_none_policy(self) -> None:
        """NONE in config → ai_call receives cache_policy=CachePolicy.NONE."""
        config = _make_config(CachePolicy.NONE)

        mock_response = SimpleNamespace(
            usage=SimpleNamespace(
                input_tokens=100,
                output_tokens=50,
                cache_read_input_tokens=0,
                cache_creation_input_tokens=0,
            ),
        )

        with (
            patch(
                "afcore.core.client.ai_call",
                new_callable=AsyncMock,
                return_value=("response text", mock_response),
            ) as mock_ai_call,
            patch("afcore.core.models.resolve_model", return_value="claude-sonnet-4-6"),
        ):
            from afcore.nightshift.cost_helpers import nightshift_ai_call

            await nightshift_ai_call(
                model_tier="STANDARD",
                max_tokens=4096,
                messages=[{"role": "user", "content": "test"}],
                context="test",
                cost_label="test",
                config=config,
            )

        mock_ai_call.assert_called_once()
        call_kwargs = mock_ai_call.call_args
        assert call_kwargs.kwargs["cache_policy"] == CachePolicy.NONE

    @pytest.mark.asyncio
    async def test_defaults_when_no_caching_config(self) -> None:
        """Config without caching section → ai_call receives DEFAULT."""
        config = MagicMock(spec=[])
        config.models = None
        config.pricing = MagicMock()
        config.pricing.models = {}

        mock_response = SimpleNamespace(
            usage=SimpleNamespace(
                input_tokens=100,
                output_tokens=50,
                cache_read_input_tokens=0,
                cache_creation_input_tokens=0,
            ),
        )

        with (
            patch(
                "afcore.core.client.ai_call",
                new_callable=AsyncMock,
                return_value=("response text", mock_response),
            ) as mock_ai_call,
            patch("afcore.core.models.resolve_model", return_value="claude-sonnet-4-6"),
        ):
            from afcore.nightshift.cost_helpers import nightshift_ai_call

            await nightshift_ai_call(
                model_tier="STANDARD",
                max_tokens=4096,
                messages=[{"role": "user", "content": "test"}],
                context="test",
                cost_label="test",
                config=config,
            )

        mock_ai_call.assert_called_once()
        call_kwargs = mock_ai_call.call_args
        assert call_kwargs.kwargs["cache_policy"] == CachePolicy.DEFAULT
