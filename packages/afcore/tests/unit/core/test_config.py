"""Configuration system tests.

Test Spec: TS-01-3 (defaults), TS-01-4 (overrides), TS-01-5 (invalid type),
           TS-01-E2 (missing file), TS-01-E3 (invalid TOML), TS-01-E7 (unknown keys)
Requirements: 01-REQ-2.1, 01-REQ-2.2, 01-REQ-2.3, 01-REQ-2.6, 01-REQ-2.E1,
              01-REQ-2.E2
"""

from __future__ import annotations

from pathlib import Path

import pytest
from afcore.core.config import AgentFoxConfig, load_config
from afcore.core.errors import ConfigError


class TestConfigDefaults:
    """TS-01-3: Config loads defaults from empty TOML."""

    def test_empty_toml_returns_defaults(self, tmp_path: Path) -> None:
        """An empty config file produces all default values."""
        config_file = tmp_path / "config.toml"
        config_file.write_text("")

        config = load_config(path=config_file)

        assert isinstance(config, AgentFoxConfig)
        assert config.orchestrator.max_retries == 2
        assert config.orchestrator.session_timeout == 45
        assert config.orchestrator.max_budget_usd == 20.0
        assert config.theme.header == "bold #ff8c00"

    def test_whitespace_only_toml_returns_defaults(self, tmp_path: Path) -> None:
        """A whitespace-only config file produces all default values."""
        config_file = tmp_path / "config.toml"
        config_file.write_text("   \n\n  \n")

        config = load_config(path=config_file)

        assert config.orchestrator.max_retries == 2


class TestConfigOverrides:
    """TS-01-4: Config loads overrides from TOML."""

    def test_toml_override_applied(self, tmp_path: Path) -> None:
        """Values in TOML override defaults."""
        config_file = tmp_path / "config.toml"
        config_file.write_text("[orchestrator]\nmax_retries = 5\n")

        config = load_config(path=config_file)

        assert config.orchestrator.max_retries == 5
        # Other fields remain at defaults
        assert config.orchestrator.session_timeout == 45

    def test_multiple_overrides(self, tmp_path: Path) -> None:
        """Multiple overrides are all applied."""
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            '[orchestrator]\nmax_retries = 5\nsession_timeout = 60\n\n[theme]\nheader = "bold blue"\n'
        )

        config = load_config(path=config_file)

        assert config.orchestrator.max_retries == 5
        assert config.orchestrator.session_timeout == 60
        assert config.theme.header == "bold blue"


class TestConfigInvalidType:
    """TS-01-5: Config rejects invalid type."""

    def test_string_for_int_raises_config_error(self, tmp_path: Path) -> None:
        """A string where an int is expected raises ConfigError."""
        config_file = tmp_path / "config.toml"
        config_file.write_text('[orchestrator]\nmax_retries = "not_a_number"\n')

        with pytest.raises(ConfigError) as exc_info:
            load_config(path=config_file)

        assert "max_retries" in str(exc_info.value).lower()


class TestConfigMissingFile:
    """TS-01-E2: Config file missing returns defaults."""

    def test_nonexistent_file_returns_defaults(self) -> None:
        """A non-existent config path returns all defaults without error."""
        config = load_config(path=Path("/tmp/nonexistent_config_12345.toml"))

        assert isinstance(config, AgentFoxConfig)
        assert config.orchestrator.max_retries == 2


class TestConfigInvalidTOML:
    """TS-01-E3: Config file invalid TOML raises ConfigError."""

    def test_broken_toml_raises_config_error(self, tmp_path: Path) -> None:
        """Malformed TOML raises ConfigError."""
        config_file = tmp_path / "config.toml"
        config_file.write_text("[broken toml }{")

        with pytest.raises(ConfigError):
            load_config(path=config_file)


class TestConfigUnrecognizedKeys:
    """TS-01-E7: Unrecognized config keys are ignored."""

    def test_unknown_section_ignored(self, tmp_path: Path) -> None:
        """Unknown keys in TOML are silently ignored."""
        config_file = tmp_path / "config.toml"
        config_file.write_text('[unknown_section]\nfoo = "bar"\n')

        config = load_config(path=config_file)

        assert isinstance(config, AgentFoxConfig)
        assert config.orchestrator.max_retries == 2  # defaults applied

    def test_unknown_field_in_known_section_ignored(self, tmp_path: Path) -> None:
        """Unknown fields within known sections are silently ignored."""
        config_file = tmp_path / "config.toml"
        config_file.write_text("[orchestrator]\nmax_retries = 3\ntotally_unknown_field = 42\n")

        config = load_config(path=config_file)

        assert config.orchestrator.max_retries == 3


class TestBackendConfig:
    """Tests for BackendConfig model and the new [backend] config section."""

    def test_default_backend_provider_is_claude(self) -> None:
        """AgentFoxConfig.backend.provider defaults to 'claude'."""
        config = AgentFoxConfig()
        assert config.backend.provider == "claude"

    def test_backend_provider_google_is_valid(self) -> None:
        """BackendConfig accepts 'google' as a valid provider."""
        from afcore.core.config import BackendConfig

        config = BackendConfig(provider="google")
        assert config.provider == "google"

    def test_backend_provider_deepagents_is_valid(self) -> None:
        """BackendConfig accepts 'deepagents' as a valid provider."""
        from afcore.core.config import BackendConfig

        config = BackendConfig(provider="deepagents")
        assert config.provider == "deepagents"

    def test_backend_provider_invalid_raises_validation_error(self) -> None:
        """BackendConfig rejects invalid provider values."""
        from afcore.core.config import BackendConfig
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            BackendConfig(provider="invalid")

    def test_orchestrator_no_longer_has_backend_field(self) -> None:
        """OrchestratorConfig no longer has a 'backend' field."""
        from afcore.core.config import OrchestratorConfig

        assert "backend" not in OrchestratorConfig.model_fields

    def test_afcore_config_has_backend_field(self) -> None:
        """AgentFoxConfig has a 'backend' field keyed to BackendConfig."""
        from afcore.core.config import BackendConfig

        assert "backend" in AgentFoxConfig.model_fields
        assert AgentFoxConfig.model_fields["backend"].annotation is BackendConfig

    def test_toml_with_backend_section_loads_provider(self, tmp_path: Path) -> None:
        """A TOML with [backend] provider='deepagents' loads correctly."""
        config_file = tmp_path / "config.toml"
        config_file.write_text('[backend]\nprovider = "deepagents"\n')

        config = load_config(path=config_file)

        assert config.backend.provider == "deepagents"

    def test_old_orchestrator_backend_silently_ignored(self, tmp_path: Path) -> None:
        """An old TOML with [orchestrator] backend='deepagents' is silently ignored.

        The old key falls through extra='ignore' on OrchestratorConfig.
        The new config.backend.provider stays at its default 'claude'.
        """
        config_file = tmp_path / "config.toml"
        config_file.write_text('[orchestrator]\nbackend = "deepagents"\nmax_retries = 5\n')

        config = load_config(path=config_file)

        assert config.backend.provider == "claude"  # default, old key ignored
        assert config.orchestrator.max_retries == 5  # other fields still work


class TestConfigSymlinkRejection:
    """Security: load_config() rejects symlinked config files (CWE-59 mitigation)."""

    def test_symlink_config_raises_config_error(self, tmp_path: Path) -> None:
        """load_config() raises ConfigError when config path is a symlink.

        13-REQ-2.E1, 13-REQ-3.E1: Symlink rejection with CWE-59 message.
        """
        from afcore.core.errors import ConfigError

        target = tmp_path / "sensitive.toml"
        target.write_text("[orchestrator]\nmax_retries = 99\n")
        symlink = tmp_path / "config.toml"
        symlink.symlink_to(target)

        with pytest.raises(ConfigError) as exc_info:
            load_config(path=symlink)

        assert "symlink" in str(exc_info.value).lower()
        assert "CWE-59" in str(exc_info.value)

    def test_symlink_config_identifies_path(self, tmp_path: Path) -> None:
        """load_config() ConfigError identifies the symlinked path.

        13-REQ-2.E1: Error message identifies the config file path.
        """
        from afcore.core.errors import ConfigError

        target = tmp_path / "sensitive.toml"
        target.write_text("")
        symlink = tmp_path / "config.toml"
        symlink.symlink_to(target)

        with pytest.raises(ConfigError) as exc_info:
            load_config(path=symlink)

        assert str(symlink) in str(exc_info.value)

    def test_non_symlink_config_loads_normally(self, tmp_path: Path) -> None:
        """A regular (non-symlink) config file still loads correctly."""
        config_file = tmp_path / "config.toml"
        config_file.write_text("[orchestrator]\nmax_retries = 5\n")

        config = load_config(path=config_file)

        assert config.orchestrator.max_retries == 5


class TestModelsConfig:
    """TS-01-12: Config-driven model registry loading and validation.

    Requirements: 01-REQ-5.1
    """

    def test_models_defaults_to_empty(self, tmp_path: Path) -> None:
        """An empty config produces an empty ModelsConfig (no user overrides)."""
        config_file = tmp_path / "config.toml"
        config_file.write_text("")
        config = load_config(path=config_file)
        assert config.models.registry == {}
        assert config.models.tier_defaults == {}

    def test_registry_entry_parsed_from_toml(self, tmp_path: Path) -> None:
        """A [models.registry.<id>] table is parsed into the config."""
        config_file = tmp_path / "config.toml"
        config_file.write_text('[models.registry.claude-fable-5-1]\ntier = "ADVANCED"\n')
        config = load_config(path=config_file)
        assert "claude-fable-5-1" in config.models.registry

    def test_tier_defaults_override_parsed_from_toml(self, tmp_path: Path) -> None:
        """[models.tier_defaults] is parsed and cross-validated successfully."""
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            "[models.registry.claude-fable-5-1]\n"
            'tier = "ADVANCED"\n'
            "\n"
            "[models.tier_defaults]\n"
            'ADVANCED = "claude-fable-5-1"\n'
        )
        config = load_config(path=config_file)
        assert config.models.tier_defaults["ADVANCED"] == "claude-fable-5-1"

    def test_registry_entry_with_variant_raises_config_error(self, tmp_path: Path) -> None:
        """A config.toml containing model_variant raises ConfigError (unknown field)."""
        config_file = tmp_path / "config.toml"
        config_file.write_text('[models.registry.claude-fable-5-1]\ntier = "ADVANCED"\nvariant = "standard"\n')
        with pytest.raises((ConfigError, Exception)):
            load_config(path=config_file)

    def test_tier_default_pointing_to_unknown_model_raises(self, tmp_path: Path) -> None:
        """tier_defaults pointing to an unregistered model ID raises ConfigError."""
        config_file = tmp_path / "config.toml"
        config_file.write_text('[models.tier_defaults]\nADVANCED = "claude-does-not-exist"\n')
        with pytest.raises((ConfigError, Exception)):
            load_config(path=config_file)

    def test_invalid_tier_key_in_tier_defaults_raises(self, tmp_path: Path) -> None:
        """tier_defaults with unrecognized tier key raises ConfigError."""
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            '[models.registry.some-model]\ntier = "ADVANCED"\n\n[models.tier_defaults]\nBOGUS = "some-model"\n'
        )
        with pytest.raises((ConfigError, Exception)):
            load_config(path=config_file)

    def test_invalid_registry_entry_tier_raises(self, tmp_path: Path) -> None:
        """A registry entry with an unrecognized tier raises on load."""
        config_file = tmp_path / "config.toml"
        config_file.write_text('[models.registry.some-model]\ntier = "ULTRA"\n')
        with pytest.raises((ConfigError, Exception)):
            load_config(path=config_file)

    def test_tier_default_pointing_to_hardcoded_model_needs_no_registry(self, tmp_path: Path) -> None:
        """tier_defaults can point to a hardcoded model ID without a registry entry."""
        config_file = tmp_path / "config.toml"
        config_file.write_text('[models.tier_defaults]\nSIMPLE = "claude-haiku-4-5"\n')
        config = load_config(path=config_file)
        assert config.models.tier_defaults["SIMPLE"] == "claude-haiku-4-5"


# ---------------------------------------------------------------------------
# Issue #11: SecurityConfig.permission_mode
# ---------------------------------------------------------------------------


class TestSecurityPermissionMode:
    """Verify SecurityConfig exposes a configurable permission_mode field."""

    def test_default_permission_mode(self) -> None:
        """Default permission_mode is 'bypassPermissions' (NS-REQ-4)."""
        config = AgentFoxConfig()
        assert config.security.permission_mode == "bypassPermissions"

    def test_permission_mode_override_from_toml(self, tmp_path: Path) -> None:
        """permission_mode can be set via config.toml (NS-REQ-1)."""
        config_file = tmp_path / "config.toml"
        config_file.write_text('[security]\npermission_mode = "acceptEdits"\n')
        config = load_config(path=config_file)
        assert config.security.permission_mode == "acceptEdits"

    def test_permission_mode_plan(self, tmp_path: Path) -> None:
        """permission_mode='plan' is accepted."""
        config_file = tmp_path / "config.toml"
        config_file.write_text('[security]\npermission_mode = "plan"\n')
        config = load_config(path=config_file)
        assert config.security.permission_mode == "plan"

    def test_permission_mode_default_value(self, tmp_path: Path) -> None:
        """permission_mode='default' is accepted."""
        config_file = tmp_path / "config.toml"
        config_file.write_text('[security]\npermission_mode = "default"\n')
        config = load_config(path=config_file)
        assert config.security.permission_mode == "default"

    def test_invalid_permission_mode_raises(self, tmp_path: Path) -> None:
        """Invalid permission_mode value raises ConfigError."""
        config_file = tmp_path / "config.toml"
        config_file.write_text('[security]\npermission_mode = "invalid"\n')
        with pytest.raises(ConfigError):
            load_config(path=config_file)
