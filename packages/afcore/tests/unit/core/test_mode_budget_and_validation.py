"""Tests for mode-level max_budget_usd resolution and unknown mode name validation.

Issue: #21
Requirements: NS-REQ-1 through NS-REQ-5
Test Spec: TS-NS-1 through TS-NS-5
"""

from __future__ import annotations

import logging

import pytest

# ---------------------------------------------------------------------------
# TS-NS-1: Mode-level max_budget_usd is honoured
# Requirement: NS-REQ-1
# ---------------------------------------------------------------------------


class TestModeLevelMaxBudget:
    """Verify mode-level max_budget_usd is resolved by resolve_max_budget."""

    def test_mode_level_budget_resolved(self) -> None:
        """TS-NS-1: mode-level max_budget_usd = 5.0 is resolved."""
        from afcore.core.config import (
            AgentFoxConfig,
            ArchetypesConfig,
            PerArchetypeConfig,
        )
        from afcore.engine.sdk_params import resolve_max_budget

        config = AgentFoxConfig(
            archetypes=ArchetypesConfig(
                overrides={
                    "reviewer": PerArchetypeConfig(
                        modes={
                            "fix-review": PerArchetypeConfig(max_budget_usd=5.0),
                        }
                    )
                }
            )
        )
        result = resolve_max_budget(config, "reviewer", mode="fix-review")
        assert result == 5.0

    def test_mode_level_budget_via_session_params(self) -> None:
        """TS-NS-1: resolve_session_params surfaces mode-level max_budget_usd."""
        from afcore.core.config import (
            AgentFoxConfig,
            ArchetypesConfig,
            PerArchetypeConfig,
        )
        from afcore.engine.sdk_params import resolve_session_params

        config = AgentFoxConfig(
            archetypes=ArchetypesConfig(
                overrides={
                    "reviewer": PerArchetypeConfig(
                        modes={
                            "fix-review": PerArchetypeConfig(max_budget_usd=5.0),
                        }
                    )
                }
            )
        )
        params = resolve_session_params(config, "reviewer", mode="fix-review")
        assert params.max_budget_usd == 5.0

    def test_mode_level_budget_overrides_archetype_level(self) -> None:
        """Mode-level budget takes precedence over archetype-level."""
        from afcore.core.config import (
            AgentFoxConfig,
            ArchetypesConfig,
            PerArchetypeConfig,
        )
        from afcore.engine.sdk_params import resolve_max_budget

        config = AgentFoxConfig(
            archetypes=ArchetypesConfig(
                overrides={
                    "reviewer": PerArchetypeConfig(
                        max_budget_usd=8.0,
                        modes={
                            "fix-review": PerArchetypeConfig(max_budget_usd=5.0),
                        },
                    )
                }
            )
        )
        result = resolve_max_budget(config, "reviewer", mode="fix-review")
        assert result == 5.0

    def test_mode_level_zero_budget_unlimited(self) -> None:
        """Mode-level max_budget_usd=0 means unlimited (returns None)."""
        from afcore.core.config import (
            AgentFoxConfig,
            ArchetypesConfig,
            PerArchetypeConfig,
        )
        from afcore.engine.sdk_params import resolve_max_budget

        config = AgentFoxConfig(
            archetypes=ArchetypesConfig(
                overrides={
                    "reviewer": PerArchetypeConfig(
                        max_budget_usd=8.0,
                        modes={
                            "fix-review": PerArchetypeConfig(max_budget_usd=0.0),
                        },
                    )
                }
            )
        )
        result = resolve_max_budget(config, "reviewer", mode="fix-review")
        assert result is None


# ---------------------------------------------------------------------------
# TS-NS-2: Archetype-level max_budget_usd still wins when no mode-level value
# Requirement: NS-REQ-2
# ---------------------------------------------------------------------------


class TestArchetypeLevelBudgetFallback:
    """Verify archetype-level max_budget_usd is used when no mode-level value set."""

    def test_archetype_level_budget_used_when_no_mode_override(self) -> None:
        """TS-NS-2: archetype-level budget used when mode has no budget override."""
        from afcore.core.config import (
            AgentFoxConfig,
            ArchetypesConfig,
            PerArchetypeConfig,
        )
        from afcore.engine.sdk_params import resolve_max_budget

        config = AgentFoxConfig(
            archetypes=ArchetypesConfig(
                overrides={
                    "reviewer": PerArchetypeConfig(
                        max_budget_usd=8.0,
                        modes={
                            "fix-review": PerArchetypeConfig(max_turns=50),
                        },
                    )
                }
            )
        )
        result = resolve_max_budget(config, "reviewer", mode="fix-review")
        assert result == 8.0

    def test_archetype_level_budget_used_when_mode_absent(self) -> None:
        """TS-NS-2: archetype-level budget used when mode key is absent."""
        from afcore.core.config import (
            AgentFoxConfig,
            ArchetypesConfig,
            PerArchetypeConfig,
        )
        from afcore.engine.sdk_params import resolve_max_budget

        config = AgentFoxConfig(
            archetypes=ArchetypesConfig(overrides={"reviewer": PerArchetypeConfig(max_budget_usd=8.0)})
        )
        result = resolve_max_budget(config, "reviewer", mode="fix-review")
        assert result == 8.0

    def test_archetype_level_budget_via_session_params(self) -> None:
        """TS-NS-2: resolve_session_params surfaces archetype-level budget when no mode override."""
        from afcore.core.config import (
            AgentFoxConfig,
            ArchetypesConfig,
            PerArchetypeConfig,
        )
        from afcore.engine.sdk_params import resolve_session_params

        config = AgentFoxConfig(
            archetypes=ArchetypesConfig(overrides={"reviewer": PerArchetypeConfig(max_budget_usd=8.0)})
        )
        params = resolve_session_params(config, "reviewer", mode="fix-review")
        assert params.max_budget_usd == 8.0


# ---------------------------------------------------------------------------
# TS-NS-3: Unknown mode name emits warning
# Requirement: NS-REQ-3
# ---------------------------------------------------------------------------


class TestUnknownModeNameWarning:
    """Verify unknown mode names in built-in archetype overrides emit warnings."""

    def test_unknown_mode_emits_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """TS-NS-3: 'pre-review' triggers warning listing valid modes."""
        from afcore.core.config import ArchetypesConfig, PerArchetypeConfig

        with caplog.at_level(logging.WARNING, logger="afcore.core.config"):
            ArchetypesConfig(
                overrides={
                    "reviewer": PerArchetypeConfig(
                        modes={
                            "pre-review": PerArchetypeConfig(model_tier="STANDARD"),
                        }
                    )
                }
            )

        assert any("pre-review" in record.message for record in caplog.records)
        # Should mention valid modes
        assert any("pre-flight" in record.message for record in caplog.records)
        assert any("audit-review" in record.message for record in caplog.records)
        assert any("fix-review" in record.message for record in caplog.records)

    def test_valid_mode_no_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """Valid mode names produce no warning."""
        from afcore.core.config import ArchetypesConfig, PerArchetypeConfig

        with caplog.at_level(logging.WARNING, logger="afcore.core.config"):
            ArchetypesConfig(
                overrides={
                    "reviewer": PerArchetypeConfig(
                        modes={
                            "pre-flight": PerArchetypeConfig(model_tier="STANDARD"),
                        }
                    )
                }
            )

        config_warnings = [r for r in caplog.records if "Unknown mode" in r.message]
        assert len(config_warnings) == 0

    def test_custom_archetype_exempt_from_validation(self, caplog: pytest.LogCaptureFixture) -> None:
        """Custom archetypes (not in ARCHETYPE_REGISTRY) skip mode validation."""
        from afcore.core.config import ArchetypesConfig, PerArchetypeConfig

        with caplog.at_level(logging.WARNING, logger="afcore.core.config"):
            ArchetypesConfig(
                overrides={
                    "my-custom-archetype": PerArchetypeConfig(
                        modes={
                            "any-mode": PerArchetypeConfig(model_tier="STANDARD"),
                        }
                    )
                }
            )

        config_warnings = [r for r in caplog.records if "Unknown mode" in r.message]
        assert len(config_warnings) == 0

    def test_multiple_unknown_modes_emit_multiple_warnings(self, caplog: pytest.LogCaptureFixture) -> None:
        """Each unknown mode name produces its own warning."""
        from afcore.core.config import ArchetypesConfig, PerArchetypeConfig

        with caplog.at_level(logging.WARNING, logger="afcore.core.config"):
            ArchetypesConfig(
                overrides={
                    "reviewer": PerArchetypeConfig(
                        modes={
                            "pre-review": PerArchetypeConfig(),
                            "bad-mode": PerArchetypeConfig(),
                        }
                    )
                }
            )

        config_warnings = [r for r in caplog.records if "Unknown mode" in r.message]
        assert len(config_warnings) == 2

    def test_archetype_with_no_modes_in_registry_warns_on_any_mode(self, caplog: pytest.LogCaptureFixture) -> None:
        """Archetypes with no registered modes warn on any mode key."""
        from afcore.core.config import ArchetypesConfig, PerArchetypeConfig

        with caplog.at_level(logging.WARNING, logger="afcore.core.config"):
            ArchetypesConfig(
                overrides={
                    "gate": PerArchetypeConfig(
                        modes={
                            "some-mode": PerArchetypeConfig(),
                        }
                    )
                }
            )

        config_warnings = [r for r in caplog.records if "Unknown mode" in r.message]
        assert len(config_warnings) == 1


# ---------------------------------------------------------------------------
# TS-NS-4: config.toml loads without unknown mode warnings
# Requirement: NS-REQ-4
# ---------------------------------------------------------------------------


class TestConfigTomlFixedModeName:
    """Verify .nightshift/config.toml loads without unknown mode warnings."""

    def test_project_config_loads_without_mode_warnings(self, caplog: pytest.LogCaptureFixture) -> None:
        """TS-NS-4: load_config() on the project config emits no unknown mode warnings."""
        import tomllib
        from pathlib import Path

        from afcore.core.config import AgentFoxConfig

        # __file__ is packages/afcore/tests/unit/core/test_*.py
        # parents: [0]=core, [1]=unit, [2]=tests, [3]=afcore, [4]=packages, [5]=root
        config_path = Path(__file__).resolve().parents[5] / ".nightshift" / "config.toml"
        if not config_path.exists():
            pytest.skip("Project config.toml not found")

        data = tomllib.loads(config_path.read_text(encoding="utf-8"))

        with caplog.at_level(logging.WARNING, logger="afcore.core.config"):
            AgentFoxConfig.model_validate(data)

        config_warnings = [r for r in caplog.records if "Unknown mode" in r.message]
        assert len(config_warnings) == 0, f"Unexpected unknown mode warnings: {[r.message for r in config_warnings]}"


# ---------------------------------------------------------------------------
# TS-NS-5: Compaction field description accuracy
# Requirement: NS-REQ-5
# ---------------------------------------------------------------------------


class TestCompactionFieldDescription:
    """Verify compaction field description reflects per-archetype defaults."""

    def test_compaction_description_mentions_coder_default(self) -> None:
        """TS-NS-5: compaction description mentions True for coder."""
        from afcore.core.config import PerArchetypeConfig

        desc = PerArchetypeConfig.model_fields["compaction"].description
        assert desc is not None
        assert "True for coder" in desc

    def test_compaction_description_not_universally_false(self) -> None:
        """TS-NS-5: compaction description no longer claims default is universally False."""
        from afcore.core.config import PerArchetypeConfig

        desc = PerArchetypeConfig.model_fields["compaction"].description
        assert desc is not None
        # Should NOT say the default is just "(False)" without qualification
        assert "default (False)" not in desc
