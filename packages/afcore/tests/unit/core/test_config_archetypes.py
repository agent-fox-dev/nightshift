"""Tests for ArchetypesConfig pydantic model.

Test Spec: TS-26-24 through TS-26-26, TS-26-E9
Requirements: 26-REQ-6.3 through 26-REQ-6.5, 26-REQ-6.E1
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# TS-26-24: Model tier override per archetype
# Requirement: 26-REQ-6.3
# ---------------------------------------------------------------------------


class TestModelTierOverride:
    """Verify per-archetype model tier overrides in config via overrides table."""

    def test_model_override_stored(self) -> None:
        from afcore.core.config import ArchetypesConfig, PerArchetypeConfig

        cfg = ArchetypesConfig(overrides={"reviewer": PerArchetypeConfig(model_tier="SIMPLE")})
        assert cfg.overrides["reviewer"].model_tier == "SIMPLE"

    def test_empty_overrides_default(self) -> None:
        from afcore.core.config import ArchetypesConfig

        cfg = ArchetypesConfig()
        assert cfg.overrides == {}


# ---------------------------------------------------------------------------
# TS-26-25: Allowlist override per archetype
# Requirement: 26-REQ-6.4
# ---------------------------------------------------------------------------


class TestAllowlistOverride:
    """Verify per-archetype allowlist overrides in config via overrides table."""

    def test_allowlist_override_stored(self) -> None:
        from afcore.core.config import ArchetypesConfig, PerArchetypeConfig

        cfg = ArchetypesConfig(overrides={"reviewer": PerArchetypeConfig(allowlist=["ls", "cat"])})
        assert cfg.overrides["reviewer"].allowlist == ["ls", "cat"]

    def test_empty_overrides_default_for_allowlists(self) -> None:
        from afcore.core.config import ArchetypesConfig

        cfg = ArchetypesConfig()
        assert cfg.overrides == {}


# ---------------------------------------------------------------------------
# TS-26-E9: Missing archetypes config section
# Requirement: 26-REQ-6.E1
# ---------------------------------------------------------------------------


class TestMissingArchetypesSection:
    """Verify missing [archetypes] section uses all defaults."""

    def test_missing_section_uses_defaults(self) -> None:
        from afcore.core.config import AgentFoxConfig

        cfg = AgentFoxConfig()
        assert cfg.archetypes.overrides == {}

    def test_load_config_without_archetypes(
        self,
        tmp_path: pytest.TempPathFactory,
    ) -> None:
        from afcore.core.config import load_config

        config_path = tmp_path / "config.toml"  # type: ignore[operator]
        config_path.write_text("[orchestrator]\nmax_retries = 2\n")

        cfg = load_config(config_path)
        assert cfg.archetypes.overrides == {}
