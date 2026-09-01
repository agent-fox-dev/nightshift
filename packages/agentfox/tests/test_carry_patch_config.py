"""Tests for HubConfig and CarryPatchConfig Pydantic models.

These tests verify that:
- HubConfig and CarryPatchConfig models have the correct defaults and field types
- Clamped annotations silently clamp out-of-range values
- AgentFoxConfig exposes .hub and .carry_patch fields with default_factory
- extra='ignore' allows forward-compatible config files with unknown keys
- Legacy config files without [hub] or [carry_patch] sections load cleanly

Specification: 02_carry_patch_bootstrap
Test IDs: TS-02-1, TS-02-2, TS-02-3, TS-02-4, TS-02-5, TS-02-6,
          TS-02-E1, TS-02-E2, TS-02-E3, TS-02-E20, TS-02-E21
Requirements: 02-REQ-1
"""

from __future__ import annotations

import tomllib

import pytest
from agentfox.core.config import (
    AgentFoxConfig,
    CarryPatchConfig,
    HubConfig,
)


class TestHubConfigDefaults:
    """TS-02-1: HubConfig defaults when no [hub] section is present."""

    def test_agentfox_config_hub_field_is_hub_config_instance(self) -> None:
        """AgentFoxConfig.hub is a HubConfig instance when no [hub] section given.

        Requirements: 02-REQ-1.1
        Test ID: TS-02-1
        """
        config = AgentFoxConfig.model_validate({})
        assert isinstance(config.hub, HubConfig)

    def test_agentfox_config_hub_endpoint_url_defaults_to_empty_string(self) -> None:
        """config.hub.endpoint_url is empty string by default.

        Requirements: 02-REQ-1.1
        Test ID: TS-02-1
        """
        config = AgentFoxConfig.model_validate({})
        assert config.hub.endpoint_url == ""

    def test_hub_config_direct_instantiation_defaults(self) -> None:
        """HubConfig() instantiated directly has endpoint_url=''.

        Requirements: 02-REQ-1.1
        Test ID: TS-02-1
        """
        hub = HubConfig()
        assert hub.endpoint_url == ""

    def test_hub_config_accepts_endpoint_url(self) -> None:
        """HubConfig stores a provided endpoint_url correctly.

        Requirements: 02-REQ-1.2
        Test ID: TS-02-1
        """
        hub = HubConfig(endpoint_url="https://hub.example.com")
        assert hub.endpoint_url == "https://hub.example.com"


class TestCarryPatchConfigDefaults:
    """TS-02-2: CarryPatchConfig defaults when no [carry_patch] section is present."""

    def test_agentfox_config_carry_patch_field_is_carry_patch_config_instance(self) -> None:
        """AgentFoxConfig.carry_patch is a CarryPatchConfig instance.

        Requirements: 02-REQ-1.2
        Test ID: TS-02-2
        """
        config = AgentFoxConfig.model_validate({})
        assert isinstance(config.carry_patch, CarryPatchConfig)

    def test_carry_patch_default_enabled_is_false(self) -> None:
        """CarryPatchConfig.enabled defaults to False.

        Requirements: 02-REQ-1.2
        Test ID: TS-02-2
        """
        cp = CarryPatchConfig()
        assert cp.enabled is False

    def test_carry_patch_default_workspace_is_empty_string(self) -> None:
        """CarryPatchConfig.workspace defaults to empty string.

        Requirements: 02-REQ-1.2
        Test ID: TS-02-2
        """
        cp = CarryPatchConfig()
        assert cp.workspace == ""

    def test_carry_patch_default_check_interval_is_300(self) -> None:
        """CarryPatchConfig.check_interval defaults to 300.

        Requirements: 02-REQ-1.2
        Test ID: TS-02-2
        """
        cp = CarryPatchConfig()
        assert cp.check_interval == 300

    def test_carry_patch_default_auto_resolve_is_true(self) -> None:
        """CarryPatchConfig.auto_resolve defaults to True.

        Requirements: 02-REQ-1.2
        Test ID: TS-02-2
        """
        cp = CarryPatchConfig()
        assert cp.auto_resolve is True

    def test_carry_patch_default_rebuild_timeout_is_600(self) -> None:
        """CarryPatchConfig.rebuild_timeout defaults to 600.

        Requirements: 02-REQ-1.2
        Test ID: TS-02-2
        """
        cp = CarryPatchConfig()
        assert cp.rebuild_timeout == 600

    def test_carry_patch_default_rebuild_poll_interval_is_5(self) -> None:
        """CarryPatchConfig.rebuild_poll_interval defaults to 5.

        Requirements: 02-REQ-1.2
        Test ID: TS-02-2
        """
        cp = CarryPatchConfig()
        assert cp.rebuild_poll_interval == 5

    def test_carry_patch_default_max_resolve_retries_is_2(self) -> None:
        """CarryPatchConfig.max_resolve_retries defaults to 2.

        Requirements: 02-REQ-1.2
        Test ID: TS-02-2
        """
        cp = CarryPatchConfig()
        assert cp.max_resolve_retries == 2

    def test_carry_patch_all_defaults_via_agentfox_config(self) -> None:
        """All CarryPatchConfig defaults are correct when loaded via AgentFoxConfig.

        Requirements: 02-REQ-1.2
        Test ID: TS-02-2
        """
        config = AgentFoxConfig.model_validate({})
        cp = config.carry_patch
        assert cp.enabled is False
        assert cp.workspace == ""
        assert cp.check_interval == 300
        assert cp.auto_resolve is True
        assert cp.rebuild_timeout == 600
        assert cp.rebuild_poll_interval == 5
        assert cp.max_resolve_retries == 2


class TestCarryPatchConfigClamping:
    """TS-02-3, TS-02-4, TS-02-5: Clamped annotation silently clamps values."""

    def test_check_interval_below_minimum_clamped_to_60(self) -> None:
        """check_interval below 60 is silently clamped to 60.

        Requirements: 02-REQ-1.3
        Test ID: TS-02-3
        """
        cp = CarryPatchConfig(check_interval=10)
        assert cp.check_interval == 60

    def test_check_interval_at_minimum_not_clamped(self) -> None:
        """check_interval at exactly 60 is accepted without clamping.

        Requirements: 02-REQ-1.3
        Test ID: TS-02-3, TS-02-E2
        """
        cp = CarryPatchConfig(check_interval=60)
        assert cp.check_interval == 60

    def test_check_interval_well_below_minimum_clamped(self) -> None:
        """check_interval of 1 is clamped to 60, no ValidationError raised.

        Requirements: 02-REQ-1.3
        Test ID: TS-02-3
        """
        cp = CarryPatchConfig(check_interval=1)
        assert cp.check_interval == 60

    def test_rebuild_poll_interval_below_minimum_clamped_to_2(self) -> None:
        """rebuild_poll_interval below 2 is silently clamped to 2.

        Requirements: 02-REQ-1.4
        Test ID: TS-02-4
        """
        cp = CarryPatchConfig(rebuild_poll_interval=0)
        assert cp.rebuild_poll_interval == 2

    def test_rebuild_poll_interval_negative_clamped_to_2(self) -> None:
        """rebuild_poll_interval of -5 is clamped to 2, no ValidationError.

        Requirements: 02-REQ-1.4
        Test ID: TS-02-4
        """
        cp = CarryPatchConfig(rebuild_poll_interval=-5)
        assert cp.rebuild_poll_interval == 2

    def test_max_resolve_retries_above_maximum_clamped_to_10(self) -> None:
        """max_resolve_retries above 10 is silently clamped to 10.

        Requirements: 02-REQ-1.5
        Test ID: TS-02-5
        """
        cp = CarryPatchConfig(max_resolve_retries=99)
        assert cp.max_resolve_retries == 10

    def test_max_resolve_retries_below_minimum_clamped_to_0(self) -> None:
        """max_resolve_retries below 0 is silently clamped to 0.

        Requirements: 02-REQ-1.5
        Test ID: TS-02-5, TS-02-E1
        """
        cp = CarryPatchConfig(max_resolve_retries=-1)
        assert cp.max_resolve_retries == 0

    def test_max_resolve_retries_at_minimum_not_clamped(self) -> None:
        """max_resolve_retries=0 is accepted without clamping (0 is min bound).

        Requirements: 02-REQ-1.5
        Test ID: TS-02-E1
        """
        cp = CarryPatchConfig(max_resolve_retries=0)
        assert cp.max_resolve_retries == 0

    def test_max_resolve_retries_at_maximum_not_clamped(self) -> None:
        """max_resolve_retries=10 is accepted without clamping (10 is max bound).

        Requirements: 02-REQ-1.5
        Test ID: TS-02-5
        """
        cp = CarryPatchConfig(max_resolve_retries=10)
        assert cp.max_resolve_retries == 10

    def test_rebuild_timeout_zero_clamped_to_1(self) -> None:
        """rebuild_timeout=0 is silently clamped to 1.

        Requirements: 02-REQ-1.7
        Test ID: TS-02-E20
        """
        cp = CarryPatchConfig(rebuild_timeout=0)
        assert cp.rebuild_timeout == 1

    def test_rebuild_timeout_negative_clamped_to_1(self) -> None:
        """rebuild_timeout=-100 is silently clamped to 1.

        Requirements: 02-REQ-1.7
        Test ID: TS-02-E21
        """
        cp = CarryPatchConfig(rebuild_timeout=-100)
        assert cp.rebuild_timeout == 1

    def test_clamping_does_not_raise_validation_error(self) -> None:
        """Clamped values do not raise ValidationError.

        Requirements: 02-REQ-1.3, 02-REQ-1.4, 02-REQ-1.5
        Test ID: TS-02-3, TS-02-4, TS-02-5
        """
        # Should not raise
        cp = CarryPatchConfig(
            check_interval=0,
            rebuild_poll_interval=-10,
            max_resolve_retries=999,
            rebuild_timeout=-1,
        )
        assert cp.check_interval == 60
        assert cp.rebuild_poll_interval == 2
        assert cp.max_resolve_retries == 10
        assert cp.rebuild_timeout == 1


class TestExtraFieldsIgnored:
    """TS-02-6: extra='ignore' allows unknown keys without ValidationError."""

    def test_hub_config_ignores_unknown_keys(self) -> None:
        """HubConfig ignores unknown fields via extra='ignore'.

        Requirements: 02-REQ-1.6
        Test ID: TS-02-6
        """
        hub = HubConfig.model_validate({"endpoint_url": "https://hub.example.com", "unknown_key": "foo"})
        assert hub.endpoint_url == "https://hub.example.com"
        assert not hasattr(hub, "unknown_key")

    def test_carry_patch_config_ignores_unknown_keys(self) -> None:
        """CarryPatchConfig ignores unknown fields via extra='ignore'.

        Requirements: 02-REQ-1.6
        Test ID: TS-02-6
        """
        cp = CarryPatchConfig.model_validate({"enabled": True, "unknown_field": 42})
        assert cp.enabled is True
        assert not hasattr(cp, "unknown_field")

    def test_agentfox_config_ignores_unknown_hub_keys_via_toml(self) -> None:
        """AgentFoxConfig loads TOML with unknown [hub] keys without error.

        Requirements: 02-REQ-1.6
        Test ID: TS-02-6
        """
        toml_str = '[hub]\nunknown_key = "foo"\n[carry_patch]\nunknown_field = 42\n'
        data = tomllib.loads(toml_str)
        config = AgentFoxConfig.model_validate(data)
        assert not hasattr(config.hub, "unknown_key")
        assert not hasattr(config.carry_patch, "unknown_field")


class TestAgentFoxConfigBackwardCompatibility:
    """TS-02-6, TS-02-E3: Legacy config without hub/carry_patch loads cleanly."""

    def test_agentfox_config_without_hub_section_loads_defaults(self) -> None:
        """AgentFoxConfig loads from empty dict and provides hub/carry_patch defaults.

        Requirements: 02-REQ-1.1, 02-REQ-1.2
        Test ID: TS-02-1, TS-02-2
        """
        config = AgentFoxConfig.model_validate({})
        assert isinstance(config.hub, HubConfig)
        assert isinstance(config.carry_patch, CarryPatchConfig)
        assert config.hub.endpoint_url == ""
        assert config.carry_patch.enabled is False

    def test_legacy_config_with_only_nightshift_section_loads_successfully(self) -> None:
        """A config with only legacy sections and no [hub] or [carry_patch] loads without error.

        Requirements: 02-REQ-1.6
        Test ID: TS-02-E3
        """
        toml_str = "[night_shift]\nissue_check_interval = 900\n"
        data = tomllib.loads(toml_str)
        config = AgentFoxConfig.model_validate(data)
        assert config.carry_patch.enabled is False
        assert config.hub.endpoint_url == ""

    def test_legacy_config_carry_patch_disabled_by_default(self) -> None:
        """Carry-patch is disabled by default in a legacy config without [carry_patch].

        Requirements: 02-REQ-1.2
        Test ID: TS-02-E3
        """
        toml_str = '[workspace]\nmerge_strategy = "direct"\n'
        data = tomllib.loads(toml_str)
        config = AgentFoxConfig.model_validate(data)
        assert config.carry_patch.enabled is False
        assert config.carry_patch.workspace == ""

    def test_agentfox_config_hub_field_present_as_attribute(self) -> None:
        """AgentFoxConfig exposes .hub as a HubConfig attribute.

        Requirements: 02-REQ-1.1
        Test ID: TS-02-6
        """
        config = AgentFoxConfig()
        assert hasattr(config, "hub")
        assert isinstance(config.hub, HubConfig)

    def test_agentfox_config_carry_patch_field_present_as_attribute(self) -> None:
        """AgentFoxConfig exposes .carry_patch as a CarryPatchConfig attribute.

        Requirements: 02-REQ-1.2
        Test ID: TS-02-6
        """
        config = AgentFoxConfig()
        assert hasattr(config, "carry_patch")
        assert isinstance(config.carry_patch, CarryPatchConfig)

    def test_no_validation_error_on_empty_config(self) -> None:
        """Loading AgentFoxConfig from empty dict raises no ValidationError.

        Requirements: 02-REQ-1.1, 02-REQ-1.2
        Test ID: TS-02-6
        """
        # Should not raise pydantic.ValidationError
        config = AgentFoxConfig.model_validate({})
        assert config is not None

    def test_hub_and_carry_patch_independent_defaults(self) -> None:
        """AgentFoxConfig creates independent HubConfig and CarryPatchConfig instances.

        Requirements: 02-REQ-1.1, 02-REQ-1.2
        Test ID: TS-02-6
        """
        config1 = AgentFoxConfig()
        config2 = AgentFoxConfig()
        # Each instance should have independent nested config objects
        assert config1.hub is not config2.hub
        assert config1.carry_patch is not config2.carry_patch


class TestPropertyClamping:
    """TS-02-P1: Property-style tests for Clamped bounds."""

    @pytest.mark.parametrize(
        "value,expected",
        [
            (-1000, 60),
            (0, 60),
            (59, 60),
            (60, 60),
            (300, 300),
            (1000, 1000),
        ],
    )
    def test_check_interval_clamping(self, value: int, expected: int) -> None:
        """check_interval is clamped to max(60, value).

        Requirements: 02-REQ-1.3
        Test ID: TS-02-P1
        """
        cp = CarryPatchConfig(check_interval=value)
        assert cp.check_interval == expected

    @pytest.mark.parametrize(
        "value,expected",
        [
            (-100, 2),
            (0, 2),
            (1, 2),
            (2, 2),
            (5, 5),
            (100, 100),
        ],
    )
    def test_rebuild_poll_interval_clamping(self, value: int, expected: int) -> None:
        """rebuild_poll_interval is clamped to max(2, value).

        Requirements: 02-REQ-1.4
        Test ID: TS-02-P1
        """
        cp = CarryPatchConfig(rebuild_poll_interval=value)
        assert cp.rebuild_poll_interval == expected

    @pytest.mark.parametrize(
        "value,expected",
        [
            (-1000, 0),
            (-1, 0),
            (0, 0),
            (5, 5),
            (10, 10),
            (11, 10),
            (1000, 10),
        ],
    )
    def test_max_resolve_retries_clamping(self, value: int, expected: int) -> None:
        """max_resolve_retries is clamped to max(0, min(10, value)).

        Requirements: 02-REQ-1.5
        Test ID: TS-02-P1
        """
        cp = CarryPatchConfig(max_resolve_retries=value)
        assert cp.max_resolve_retries == expected

    @pytest.mark.parametrize(
        "value,expected",
        [
            (-100, 1),
            (-1, 1),
            (0, 1),
            (1, 1),
            (600, 600),
            (1000, 1000),
        ],
    )
    def test_rebuild_timeout_clamping(self, value: int, expected: int) -> None:
        """rebuild_timeout is clamped to max(1, value).

        Requirements: 02-REQ-1.7
        Test ID: TS-02-P1, TS-02-E20, TS-02-E21
        """
        cp = CarryPatchConfig(rebuild_timeout=value)
        assert cp.rebuild_timeout == expected
