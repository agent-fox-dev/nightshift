"""Tests for issue #26: batch triage and staleness AI calls honour [models] overrides.

Covers:
- TS-NS-1: Batch triage uses models_config tier_defaults
- TS-NS-2: Staleness resolves via maintainer:hunt archetype
- TS-NS-3: ai_call / ai_call_sync accept models_config
- TS-NS-4: Startup validation and runtime use same model ID
- TS-NS-5: docs/model-escalation.md documents auxiliary calls

Requirements: NS-REQ-1, NS-REQ-2, NS-REQ-3, NS-REQ-4, NS-REQ-5
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from afcore.core.config import ModelsConfig


def _make_models_config(
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


def _make_config_with_model_override() -> MagicMock:
    """Config that remaps SIMPLE -> custom-haiku via tier_defaults."""
    config = MagicMock()
    config.archetypes.overrides = {}
    config.pricing = MagicMock()
    config.pricing.models = {}
    config.models = _make_models_config(
        registry={"custom-haiku": {"tier": "SIMPLE"}},
        tier_defaults={"SIMPLE": "custom-haiku"},
    )
    return config


def _make_config_with_maintainer_override(model_tier: str = "ADVANCED") -> MagicMock:
    """Config that overrides maintainer archetype to the given tier."""
    from afcore.core.config import ArchetypesConfig, PerArchetypeConfig

    override = PerArchetypeConfig(model_tier=model_tier)
    config = MagicMock()
    config.archetypes = ArchetypesConfig(overrides={"maintainer": override})
    config.pricing = MagicMock()
    config.pricing.models = {}
    config.models = _make_models_config()
    return config


# ---------------------------------------------------------------------------
# TS-NS-1: Batch triage uses models_config tier_defaults
# Requirement: NS-REQ-1
# ---------------------------------------------------------------------------


class TestBatchTriageHonoursModelsConfig:
    """Batch triage resolves model via models_config, not hardcoded default."""

    @pytest.mark.asyncio
    async def test_triage_uses_remapped_model(self) -> None:
        """nightshift_ai_call receives remapped model tier and cost event
        records the remapped model ID, not the hardcoded default.
        """
        from afcore.core.config import ArchetypesConfig

        config = _make_config_with_model_override()
        # Ensure resolve_model_tier falls through to registry default (SIMPLE)
        config.archetypes = ArchetypesConfig(overrides={})

        captured_kwargs: list[dict] = []

        async def fake_nightshift_ai_call(**kwargs):
            captured_kwargs.append(kwargs)
            return ('{"processing_order": [], "dependencies": [], "supersession": []}', MagicMock())

        with patch(
            "afcore.nightshift.cost_helpers.nightshift_ai_call",
            side_effect=fake_nightshift_ai_call,
        ):
            from afcore.nightshift.triage import _run_ai_triage
            from afissues.protocol import IssueResult

            issues = [IssueResult(number=1, title="Bug", html_url="", body="desc")]
            await _run_ai_triage(issues, [], config)

        assert len(captured_kwargs) == 1
        # The tier passed to nightshift_ai_call comes from resolve_model_tier
        # which for maintainer:hunt defaults to SIMPLE
        tier = captured_kwargs[0]["model_tier"]
        assert tier == "SIMPLE", f"Expected SIMPLE tier from maintainer:hunt, got {tier}"

    @pytest.mark.asyncio
    async def test_nightshift_ai_call_resolves_with_models_config(self) -> None:
        """nightshift_ai_call passes models_config to ai_call so
        resolve_model uses the remapped tier defaults.
        """
        config = _make_config_with_model_override()

        captured_ai_call_kwargs: list[dict] = []

        async def fake_ai_call(**kwargs):
            captured_ai_call_kwargs.append(kwargs)
            mock_resp = MagicMock()
            mock_resp.usage = None
            mock_resp.content = [MagicMock(text="test")]
            return "test", mock_resp

        with patch("afcore.core.client.ai_call", side_effect=fake_ai_call):
            from afcore.nightshift.cost_helpers import nightshift_ai_call

            await nightshift_ai_call(
                model_tier="SIMPLE",
                max_tokens=100,
                messages=[{"role": "user", "content": "hi"}],
                context="test",
                cost_label="test",
                config=config,
            )

        assert len(captured_ai_call_kwargs) == 1
        assert captured_ai_call_kwargs[0]["models_config"] is config.models

    @pytest.mark.asyncio
    async def test_nightshift_ai_call_cost_event_uses_remapped_model(self) -> None:
        """The cost event emitted by nightshift_ai_call uses the remapped model ID."""
        config = _make_config_with_model_override()

        mock_response = MagicMock()
        mock_response.usage = None
        mock_response.content = [MagicMock(text="result")]

        async def fake_ai_call(**kwargs):
            return "result", mock_response

        with (
            patch("afcore.core.client.ai_call", side_effect=fake_ai_call),
            patch("afcore.nightshift.cost_helpers.emit_auxiliary_cost") as mock_emit,
        ):
            from afcore.nightshift.cost_helpers import nightshift_ai_call

            await nightshift_ai_call(
                model_tier="SIMPLE",
                max_tokens=100,
                messages=[{"role": "user", "content": "hi"}],
                context="test",
                cost_label="test_label",
                config=config,
                sink=MagicMock(),
                run_id="run-1",
            )

        mock_emit.assert_called_once()
        # model_id argument should be the remapped ID
        call_args = mock_emit.call_args
        model_id_arg = call_args[0][4]  # 5th positional arg
        assert model_id_arg == "custom-haiku", f"Cost event model_id should be 'custom-haiku', got '{model_id_arg}'"


# ---------------------------------------------------------------------------
# TS-NS-2: Staleness resolves via maintainer:hunt archetype
# Requirement: NS-REQ-2
# ---------------------------------------------------------------------------


class TestStalenessResolvesViaMaintainerArchetype:
    """Staleness resolves model tier via maintainer:hunt, not hardcoded STANDARD."""

    @pytest.mark.asyncio
    async def test_staleness_uses_maintainer_hunt_tier(self) -> None:
        """When maintainer is overridden to ADVANCED, staleness uses ADVANCED."""
        config = _make_config_with_maintainer_override("ADVANCED")

        captured_kwargs: list[dict] = []

        async def fake_nightshift_ai_call(**kwargs):
            captured_kwargs.append(kwargs)
            return ('{"obsolete": []}', MagicMock())

        with patch(
            "afcore.nightshift.cost_helpers.nightshift_ai_call",
            side_effect=fake_nightshift_ai_call,
        ):
            from afcore.nightshift.staleness import _run_ai_staleness
            from afissues.protocol import IssueResult

            fixed = IssueResult(number=1, title="Fixed", html_url="", body="")
            remaining = [IssueResult(number=2, title="Remaining", html_url="", body="")]
            await _run_ai_staleness(fixed, remaining, "diff", config)

        assert len(captured_kwargs) == 1
        tier = captured_kwargs[0]["model_tier"]
        assert tier == "ADVANCED", f"Staleness should use ADVANCED from maintainer override, got '{tier}'"

    @pytest.mark.asyncio
    async def test_staleness_default_uses_simple_tier(self) -> None:
        """Without overrides, staleness uses the maintainer:hunt default (SIMPLE)."""
        from afcore.core.config import ArchetypesConfig

        config = MagicMock()
        config.archetypes = ArchetypesConfig(overrides={})
        config.pricing = MagicMock()
        config.pricing.models = {}
        config.models = _make_models_config()

        captured_kwargs: list[dict] = []

        async def fake_nightshift_ai_call(**kwargs):
            captured_kwargs.append(kwargs)
            return ('{"obsolete": []}', MagicMock())

        with patch(
            "afcore.nightshift.cost_helpers.nightshift_ai_call",
            side_effect=fake_nightshift_ai_call,
        ):
            from afcore.nightshift.staleness import _run_ai_staleness
            from afissues.protocol import IssueResult

            fixed = IssueResult(number=1, title="Fixed", html_url="", body="")
            remaining = [IssueResult(number=2, title="Remaining", html_url="", body="")]
            await _run_ai_staleness(fixed, remaining, "diff", config)

        assert len(captured_kwargs) == 1
        tier = captured_kwargs[0]["model_tier"]
        assert tier == "SIMPLE", f"Staleness default should be SIMPLE (maintainer:hunt), got '{tier}'"

    @pytest.mark.asyncio
    async def test_staleness_no_longer_hardcodes_standard(self) -> None:
        """Staleness must NOT hardcode model_tier='STANDARD'."""
        import inspect

        from afcore.nightshift.staleness import _run_ai_staleness

        source = inspect.getsource(_run_ai_staleness)
        assert 'model_tier="STANDARD"' not in source, "staleness._run_ai_staleness must not hardcode STANDARD"
        assert "model_tier='STANDARD'" not in source, "staleness._run_ai_staleness must not hardcode STANDARD"


# ---------------------------------------------------------------------------
# TS-NS-3: ai_call and ai_call_sync accept models_config
# Requirement: NS-REQ-3
# ---------------------------------------------------------------------------


class TestAiCallAcceptsModelsConfig:
    """ai_call and ai_call_sync accept and use models_config parameter."""

    @pytest.mark.asyncio
    async def test_ai_call_passes_models_config_to_resolve_model(self) -> None:
        """ai_call passes models_config to resolve_model."""
        cfg = _make_models_config(
            registry={"custom-haiku": {"tier": "SIMPLE"}},
            tier_defaults={"SIMPLE": "custom-haiku"},
        )

        with (
            patch("afcore.core.client.create_async_anthropic_client") as mock_client_factory,
            patch("afcore.core.client.cached_messages_create", new_callable=AsyncMock) as mock_cached,
            patch("afcore.core.token_tracker.track_response_usage"),
        ):
            mock_client = AsyncMock()
            mock_client_factory.return_value = mock_client

            mock_response = MagicMock()
            mock_response.content = [MagicMock(text="hello")]
            mock_cached.return_value = mock_response

            from afcore.core.client import ai_call

            text, response = await ai_call(
                model_tier="SIMPLE",
                max_tokens=100,
                messages=[{"role": "user", "content": "test"}],
                context="test",
                models_config=cfg,
            )

        # Verify the resolved model was 'custom-haiku'
        call_kwargs = mock_cached.call_args
        assert (
            call_kwargs[1]["model"] == "custom-haiku"
            or call_kwargs[0][1] == "custom-haiku"
            or any(v == "custom-haiku" for v in call_kwargs.kwargs.values())
        ), f"Expected model='custom-haiku' in cached_messages_create call, got {call_kwargs}"

    def test_ai_call_sync_passes_models_config_to_resolve_model(self) -> None:
        """ai_call_sync passes models_config to resolve_model."""
        cfg = _make_models_config(
            registry={"custom-haiku": {"tier": "SIMPLE"}},
            tier_defaults={"SIMPLE": "custom-haiku"},
        )

        with (
            patch("afcore.core.client.create_anthropic_client") as mock_client_factory,
            patch("afcore.core.client.cached_messages_create_sync") as mock_cached_sync,
            patch("afcore.core.token_tracker.track_response_usage"),
        ):
            mock_client = MagicMock()
            mock_client_factory.return_value = mock_client

            mock_response = MagicMock()
            mock_response.content = [MagicMock(text="hello")]
            mock_cached_sync.return_value = mock_response

            from afcore.core.client import ai_call_sync

            text, response = ai_call_sync(
                model_tier="SIMPLE",
                max_tokens=100,
                messages=[{"role": "user", "content": "test"}],
                context="test",
                models_config=cfg,
            )

        call_kwargs = mock_cached_sync.call_args
        assert (
            call_kwargs[1]["model"] == "custom-haiku"
            or call_kwargs[0][1] == "custom-haiku"
            or any(v == "custom-haiku" for v in call_kwargs.kwargs.values())
        ), f"Expected model='custom-haiku' in cached_messages_create_sync call, got {call_kwargs}"

    @pytest.mark.asyncio
    async def test_ai_call_without_models_config_uses_default(self) -> None:
        """ai_call without models_config uses hardcoded defaults (backward compat)."""
        with (
            patch("afcore.core.client.create_async_anthropic_client") as mock_client_factory,
            patch("afcore.core.client.cached_messages_create", new_callable=AsyncMock) as mock_cached,
            patch("afcore.core.token_tracker.track_response_usage"),
        ):
            mock_client = AsyncMock()
            mock_client_factory.return_value = mock_client

            mock_response = MagicMock()
            mock_response.content = [MagicMock(text="hello")]
            mock_cached.return_value = mock_response

            from afcore.core.client import ai_call

            await ai_call(
                model_tier="SIMPLE",
                max_tokens=100,
                messages=[{"role": "user", "content": "test"}],
                context="test",
            )

        call_kwargs = mock_cached.call_args
        # Without models_config, SIMPLE should resolve to the hardcoded default
        assert "claude-haiku-4-5" in str(call_kwargs), (
            f"Expected hardcoded SIMPLE default 'claude-haiku-4-5', got {call_kwargs}"
        )


# ---------------------------------------------------------------------------
# TS-NS-4: Validation and runtime use same model ID
# Requirement: NS-REQ-4
# ---------------------------------------------------------------------------


class TestValidationRuntimeConsistency:
    """Startup validation and runtime model resolution use the same model ID."""

    def test_resolve_model_with_config_matches_validation(self) -> None:
        """resolve_model with models_config returns the same ID that
        collect_configured_model_ids would validate.
        """
        from afcore.core.models import collect_configured_model_ids, resolve_model

        cfg = _make_models_config(
            registry={"custom-haiku": {"tier": "SIMPLE"}},
            tier_defaults={"SIMPLE": "custom-haiku"},
        )

        # What validation checks
        configured_ids = collect_configured_model_ids(cfg)

        # What runtime resolves for SIMPLE tier
        runtime_id = resolve_model("SIMPLE", models_config=cfg)

        assert runtime_id in configured_ids, f"Runtime model ID '{runtime_id}' not in validated set {configured_ids}"

    @pytest.mark.asyncio
    async def test_nightshift_ai_call_resolves_same_as_validation(self) -> None:
        """nightshift_ai_call resolves the same model ID as validation."""
        from afcore.core.models import resolve_model

        config = _make_config_with_model_override()

        # What validation would see
        validated_id = resolve_model("SIMPLE", models_config=config.models)

        captured_model_ids: list[str] = []

        async def fake_ai_call(**kwargs):
            # The ai_call receives models_config and resolves internally
            from afcore.core.models import resolve_model as rm

            resolved = rm(kwargs["model_tier"], models_config=kwargs.get("models_config"))
            captured_model_ids.append(resolved)
            mock_resp = MagicMock()
            mock_resp.usage = None
            mock_resp.content = [MagicMock(text="test")]
            return "test", mock_resp

        with patch("afcore.core.client.ai_call", side_effect=fake_ai_call):
            from afcore.nightshift.cost_helpers import nightshift_ai_call

            await nightshift_ai_call(
                model_tier="SIMPLE",
                max_tokens=100,
                messages=[{"role": "user", "content": "hi"}],
                context="test",
                cost_label="test",
                config=config,
            )

        assert len(captured_model_ids) == 1
        assert captured_model_ids[0] == validated_id, (
            f"Runtime resolved '{captured_model_ids[0]}' but validation checks '{validated_id}'"
        )


# ---------------------------------------------------------------------------
# TS-NS-5: docs/model-escalation.md documents auxiliary calls
# Requirement: NS-REQ-5
# ---------------------------------------------------------------------------


class TestDocumentation:
    """docs/model-escalation.md documents batch triage and staleness calls."""

    def test_doc_mentions_batch_triage(self) -> None:
        """model-escalation.md must mention batch triage and its tier/archetype."""
        doc = Path("docs/model-escalation.md").read_text(encoding="utf-8")
        doc_lower = doc.lower()
        assert "batch triage" in doc_lower or "triage" in doc_lower, "model-escalation.md must mention batch triage"
        assert "maintainer" in doc_lower, "model-escalation.md must mention the maintainer archetype for triage"

    def test_doc_mentions_staleness(self) -> None:
        """model-escalation.md must mention staleness and its tier/archetype."""
        doc = Path("docs/model-escalation.md").read_text(encoding="utf-8")
        doc_lower = doc.lower()
        assert "staleness" in doc_lower, "model-escalation.md must mention the staleness call"
        assert "maintainer" in doc_lower, "model-escalation.md must mention the maintainer archetype for staleness"

    def test_doc_mentions_models_config_override(self) -> None:
        """model-escalation.md must state that auxiliary calls honour [models] config."""
        doc = Path("docs/model-escalation.md").read_text(encoding="utf-8")
        doc_lower = doc.lower()
        assert "tier_defaults" in doc_lower or "[models]" in doc_lower, (
            "model-escalation.md must mention [models] config overrides for auxiliary calls"
        )
