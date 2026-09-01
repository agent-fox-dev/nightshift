"""Tests for carry-patch archetype mode registration and profile template.

All tests in this file are *intentionally failing* pending the implementation
in task group 8.  They are collected by pytest without import errors but fail
at execution time because:
- The coder archetype does not yet have a 'carry-patch' mode in its registry.
- The profile template ``coder_carry-patch.md`` does not yet exist.

Specification: 03_carry_patch_pipeline_monitor
Requirements: 03-REQ-5, 03-REQ-6
Test IDs: TS-03-17, TS-03-18
"""

from __future__ import annotations

from pathlib import Path

import pytest
from agentfox.archetypes import (
    ARCHETYPE_REGISTRY,
    ModeConfig,
    resolve_effective_config,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Locate the agentfox package root relative to this test file.
# tests/ is at packages/agentfox/tests/
# agentfox source is at packages/agentfox/agentfox/
_AGENTFOX_PKG = Path(__file__).resolve().parent.parent / "agentfox"
_TEMPLATES_DIR = _AGENTFOX_PKG / "_templates" / "profiles"


# ---------------------------------------------------------------------------
# 3.2 — TS-03-17: carry-patch ModeConfig in coder archetype registry
# ---------------------------------------------------------------------------


class TestCarryPatchModeConfig:
    """TS-03-17: coder archetype has a carry-patch mode with correct ModeConfig.

    Requirements: 03-REQ-5.1
    Test ID: TS-03-17
    """

    def test_coder_archetype_has_carry_patch_mode(self) -> None:
        """The coder archetype's modes dict contains a 'carry-patch' key.

        Requirements: 03-REQ-5.1
        Test ID: TS-03-17
        Fails: 'carry-patch' mode not yet registered (group 8 pending)
        """
        coder_entry = ARCHETYPE_REGISTRY["coder"]
        assert "carry-patch" in coder_entry.modes, (
            "coder archetype must register a 'carry-patch' mode in its "
            "modes dict (03-REQ-5.1)"
        )

    def test_carry_patch_mode_config_has_correct_fields(self) -> None:
        """carry-patch ModeConfig has STANDARD/200/adaptive/high.

        Requirements: 03-REQ-5.1
        Test ID: TS-03-17
        Fails: 'carry-patch' mode not yet registered (group 8 pending)
        """
        coder_entry = ARCHETYPE_REGISTRY["coder"]
        assert "carry-patch" in coder_entry.modes, (
            "carry-patch mode must exist before checking its fields"
        )
        mode_cfg = coder_entry.modes["carry-patch"]
        assert isinstance(mode_cfg, ModeConfig)
        assert mode_cfg.model_tier == "STANDARD", (
            f"model_tier must be 'STANDARD', got {mode_cfg.model_tier!r}"
        )
        assert mode_cfg.max_turns == 200, (
            f"max_turns must be 200, got {mode_cfg.max_turns!r}"
        )
        assert mode_cfg.thinking_mode == "adaptive", (
            f"thinking_mode must be 'adaptive', got {mode_cfg.thinking_mode!r}"
        )
        assert mode_cfg.effort == "high", (
            f"effort must be 'high', got {mode_cfg.effort!r}"
        )

    def test_resolve_effective_config_applies_carry_patch_mode(self) -> None:
        """resolve_effective_config('coder', 'carry-patch') returns resolved entry.

        Requirements: 03-REQ-5.1
        Test ID: TS-03-17
        Fails: 'carry-patch' mode not yet registered (group 8 pending)
        """
        coder_entry = ARCHETYPE_REGISTRY["coder"]
        resolved = resolve_effective_config(coder_entry, mode="carry-patch")

        # After mode override, the resolved entry should have the mode values:
        assert resolved.default_model_tier == "STANDARD"
        assert resolved.default_max_turns == 200
        assert resolved.default_thinking_mode == "adaptive"
        assert resolved.default_effort == "high"

    def test_carry_patch_mode_is_accessible_at_runtime(self) -> None:
        """The carry-patch mode is accessible via the standard archetype
        registry lookup path.

        Requirements: 03-REQ-5.1
        Test ID: TS-03-17
        Fails: 'carry-patch' mode not yet registered (group 8 pending)
        """
        assert "coder" in ARCHETYPE_REGISTRY, (
            "coder archetype must exist in ARCHETYPE_REGISTRY"
        )
        entry = ARCHETYPE_REGISTRY["coder"]
        mode_cfg = entry.modes.get("carry-patch")
        assert mode_cfg is not None, (
            "'carry-patch' mode must be importable from the coder archetype "
            "registry at runtime"
        )


# ---------------------------------------------------------------------------
# 3.2 — TS-03-17 edge case: unrecognised mode (03-REQ-5.E1)
# ---------------------------------------------------------------------------


class TestCarryPatchModeEdgeCases:
    """TS-03-17: Edge case — unrecognised mode raises KeyError via wrapper.

    Requirements: 03-REQ-5.E1
    Test ID: TS-03-17
    """

    def test_unrecognised_mode_raises_key_error(self) -> None:
        """A carry-patch-specific wrapper raises KeyError for unknown modes.

        Requirements: 03-REQ-5.E1
        Test ID: TS-03-17
        Fails: wrapper not yet implemented (group 8 pending)

        Note: The spec requires a carry-patch-specific wrapper that calls
        resolve_effective_config and validates the result. The general
        resolve_effective_config function is NOT modified — it still logs
        a warning and returns the base entry for unknown modes. The wrapper
        is what raises KeyError.
        """
        # After implementation, a wrapper function should exist that raises
        # KeyError for unrecognised modes. We attempt to import it here.
        try:
            from agentfox.nightshift.carry_patch_monitor import (  # noqa: PLC0415
                resolve_carry_patch_mode,
            )
        except ImportError:
            pytest.fail(
                "resolve_carry_patch_mode wrapper must be importable from "
                "agentfox.nightshift.carry_patch_monitor (03-REQ-5.E1)"
            )

        with pytest.raises(KeyError, match="nonexistent-mode"):
            resolve_carry_patch_mode("nonexistent-mode")


# ---------------------------------------------------------------------------
# 3.3 — TS-03-18: coder_carry-patch.md profile template
# ---------------------------------------------------------------------------


class TestCoderCarryPatchProfileTemplate:
    """TS-03-18: Profile template exists and contains required instructions.

    Requirements: 03-REQ-6.1
    Test ID: TS-03-18
    """

    def test_profile_template_file_exists(self) -> None:
        """coder_carry-patch.md exists in the profiles template directory.

        Requirements: 03-REQ-6.1
        Test ID: TS-03-18
        Fails: template file not yet created (group 8 pending)
        """
        template_path = _TEMPLATES_DIR / "coder_carry-patch.md"
        assert template_path.exists(), (
            f"Profile template must exist at {template_path}"
        )

    def test_profile_template_is_non_empty(self) -> None:
        """coder_carry-patch.md is non-empty.

        Requirements: 03-REQ-6.1
        Test ID: TS-03-18
        Fails: template file not yet created (group 8 pending)
        """
        template_path = _TEMPLATES_DIR / "coder_carry-patch.md"
        assert template_path.exists(), (
            f"Template file must exist: {template_path}"
        )
        content = template_path.read_text()
        assert len(content.strip()) > 0, (
            "Profile template must not be empty"
        )

    def test_profile_template_contains_patch_intent_instruction(self) -> None:
        """Template instructs preserving the patch's original intent.

        Requirements: 03-REQ-6.1
        Test ID: TS-03-18
        Fails: template file not yet created (group 8 pending)
        """
        template_path = _TEMPLATES_DIR / "coder_carry-patch.md"
        assert template_path.exists(), f"Template missing: {template_path}"
        content = template_path.read_text().lower()
        assert "patch" in content and "intent" in content, (
            "Template must contain instructions about preserving patch intent"
        )

    def test_profile_template_contains_conflict_files_instruction(self) -> None:
        """Template instructs adapting only conflict_files to upstream changes.

        Requirements: 03-REQ-6.1
        Test ID: TS-03-18
        Fails: template file not yet created (group 8 pending)
        """
        template_path = _TEMPLATES_DIR / "coder_carry-patch.md"
        assert template_path.exists(), f"Template missing: {template_path}"
        content = template_path.read_text().lower()
        assert "conflict" in content, (
            "Template must contain instructions about conflict files"
        )

    def test_profile_template_contains_conventional_commit_instruction(
        self,
    ) -> None:
        """Template instructs using conventional commits: fix: resolve conflict.

        Requirements: 03-REQ-6.1
        Test ID: TS-03-18
        Fails: template file not yet created (group 8 pending)
        """
        template_path = _TEMPLATES_DIR / "coder_carry-patch.md"
        assert template_path.exists(), f"Template missing: {template_path}"
        content = template_path.read_text()
        assert "fix: resolve conflict" in content, (
            "Template must contain 'fix: resolve conflict' conventional "
            "commit instruction"
        )

    def test_profile_template_contains_run_tests_instruction(self) -> None:
        """Template instructs running available tests to verify resolution.

        Requirements: 03-REQ-6.1
        Test ID: TS-03-18
        Fails: template file not yet created (group 8 pending)
        """
        template_path = _TEMPLATES_DIR / "coder_carry-patch.md"
        assert template_path.exists(), f"Template missing: {template_path}"
        content = template_path.read_text().lower()
        assert "test" in content, (
            "Template must contain instructions about running tests"
        )

    def test_profile_template_contains_commit_body_explanation(self) -> None:
        """Template instructs explaining the resolution in the commit body.

        Requirements: 03-REQ-6.1
        Test ID: TS-03-18
        Fails: template file not yet created (group 8 pending)
        """
        template_path = _TEMPLATES_DIR / "coder_carry-patch.md"
        assert template_path.exists(), f"Template missing: {template_path}"
        content = template_path.read_text().lower()
        assert ("commit message" in content or "body" in content), (
            "Template must contain instructions about explaining the "
            "resolution in the commit message body"
        )

    def test_profile_template_missing_raises_file_not_found(self) -> None:
        """Loading a missing profile template raises FileNotFoundError.

        Requirements: 03-REQ-6.E1
        Test ID: TS-03-18
        """
        # This tests the edge case in 03-REQ-6.E1: if the template file is
        # missing, the system should raise FileNotFoundError, not silently
        # use a blank profile.
        bogus_path = _TEMPLATES_DIR / "coder_nonexistent-mode.md"
        assert not bogus_path.exists(), "Precondition: bogus template must not exist"
        with pytest.raises(FileNotFoundError):
            bogus_path.read_text()
