"""Tests for startup label provisioning and config auto-creation.

Requirements: NS-REQ-2, NS-REQ-3, NS-REQ-4, NS-REQ-5
Test Spec: TS-NS-1, TS-NS-2, TS-NS-3, TS-NS-4, TS-NS-5
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from afissues.errors import IntegrationError
from afissues.labels import REQUIRED_LABELS


# ===================================================================
# TS-NS-1: Non-already-exists errors are fatal
# ===================================================================
class TestLabelCreationErrorIsFatal:
    """TS-NS-1: A non-already-exists IntegrationError aborts startup.

    Requirements: NS-REQ-1
    """

    def test_validation_422_causes_exit(self, capsys) -> None:
        """A 422 validation error (not already-exists) causes sys.exit(1)."""
        from nightshift._label_provisioning import ensure_labels

        mock_platform = AsyncMock()
        mock_platform.create_label.side_effect = IntegrationError("GitHub label creation failed (422)")

        with pytest.raises(SystemExit) as exc_info:
            ensure_labels(mock_platform)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        first_label = REQUIRED_LABELS[0].name
        assert first_label in captured.err
        assert "422" in captured.err

    def test_generic_integration_error_causes_exit(self, capsys) -> None:
        """Any IntegrationError from create_label causes sys.exit(1)."""
        from nightshift._label_provisioning import ensure_labels

        mock_platform = AsyncMock()
        mock_platform.create_label.side_effect = IntegrationError("Server error (500)")

        with pytest.raises(SystemExit) as exc_info:
            ensure_labels(mock_platform)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        first_label = REQUIRED_LABELS[0].name
        assert first_label in captured.err
        assert "500" in captured.err


# ===================================================================
# TS-NS-2: Startup label provisioning — missing labels created
# ===================================================================
class TestStartupLabelProvisioning:
    """TS-NS-2: On startup, nightshift creates missing labels.

    Requirements: NS-REQ-2
    """

    def test_ensure_labels_creates_all_required(self) -> None:
        """ensure_labels calls create_label for every required label."""
        from nightshift._label_provisioning import ensure_labels

        mock_platform = AsyncMock()
        ensure_labels(mock_platform)

        assert mock_platform.create_label.call_count == len(REQUIRED_LABELS)
        called_names = {call.args[0] for call in mock_platform.create_label.call_args_list}
        for spec in REQUIRED_LABELS:
            assert spec.name in called_names, f"create_label not called for '{spec.name}'"

    def test_ensure_labels_normalizes_color(self) -> None:
        """Colors with a leading '#' are normalized before being passed to the platform."""
        from nightshift._label_provisioning import ensure_labels

        mock_platform = AsyncMock()
        ensure_labels(mock_platform)

        # Verify no color passed to create_label starts with '#'
        for call in mock_platform.create_label.call_args_list:
            color_arg = call.args[1]
            assert not color_arg.startswith("#"), f"Color '{color_arg}' should not start with '#'"
            assert re.fullmatch(r"[0-9a-fA-F]{6}", color_arg), f"Color '{color_arg}' is not a valid 6-char hex"

    def test_platform_handles_idempotency(self) -> None:
        """When create_label returns normally (platform handles existing labels), no error."""
        from nightshift._label_provisioning import ensure_labels

        mock_platform = AsyncMock()
        # All calls return None — simulating platform silently handling existing labels
        mock_platform.create_label.return_value = None
        ensure_labels(mock_platform)

        assert mock_platform.create_label.call_count == len(REQUIRED_LABELS)


# ===================================================================
# TS-NS-3: Label creation failure exits with clear explanation
# ===================================================================
class TestLabelCreationFailure:
    """TS-NS-3: If labels cannot be created, nightshift exits with explanation.

    Requirements: NS-REQ-3
    """

    def test_permission_error_exits_nonzero(self) -> None:
        """IntegrationError causes sys.exit(1) with explanation."""
        from nightshift._label_provisioning import ensure_labels

        mock_platform = AsyncMock()
        mock_platform.create_label.side_effect = IntegrationError("Forbidden (403): insufficient permissions")

        with pytest.raises(SystemExit) as exc_info:
            ensure_labels(mock_platform)

        assert exc_info.value.code == 1

    def test_error_message_contains_label_name(self, capsys) -> None:
        """Exit message includes the failing label name."""
        from nightshift._label_provisioning import ensure_labels

        mock_platform = AsyncMock()
        mock_platform.create_label.side_effect = IntegrationError("Forbidden (403): insufficient permissions")

        with pytest.raises(SystemExit):
            ensure_labels(mock_platform)

        captured = capsys.readouterr()
        # The first required label name should appear in stderr
        first_label = REQUIRED_LABELS[0].name
        assert first_label in captured.err

    def test_error_message_contains_reason(self, capsys) -> None:
        """Exit message includes the reason for failure."""
        from nightshift._label_provisioning import ensure_labels

        mock_platform = AsyncMock()
        mock_platform.create_label.side_effect = IntegrationError("Forbidden (403): insufficient permissions")

        with pytest.raises(SystemExit):
            ensure_labels(mock_platform)

        captured = capsys.readouterr()
        assert "403" in captured.err or "insufficient permissions" in captured.err


# ===================================================================
# TS-NS-4: LabelSpec color validation
# ===================================================================
class TestLabelColorValidation:
    """TS-NS-4: Every LabelSpec.color matches the documented format.

    After normalization (stripping leading '#'), all colors must be
    exactly 6 hex characters.

    Requirements: NS-REQ-4
    """

    def test_all_required_label_colors_valid_after_normalization(self) -> None:
        """Every REQUIRED_LABELS color is valid 6-char hex after stripping '#'."""
        from nightshift._label_provisioning import _normalize_color

        for spec in REQUIRED_LABELS:
            color = _normalize_color(spec.color)
            assert re.fullmatch(r"[0-9a-fA-F]{6}", color), (
                f"Label '{spec.name}' has invalid color '{spec.color}' (normalized to '{color}')"
            )

    def test_invalid_color_causes_exit(self, capsys, monkeypatch) -> None:
        """A label with a completely invalid color causes sys.exit(1)."""
        from afissues.labels import LabelSpec
        from nightshift._label_provisioning import ensure_labels

        bad_labels = [LabelSpec(name="bad:label", color="not-hex", description="bad color")]

        mock_platform = AsyncMock()

        # Patch REQUIRED_LABELS where it's imported inside the function
        import afissues.labels

        original_labels = afissues.labels.REQUIRED_LABELS
        afissues.labels.REQUIRED_LABELS = bad_labels
        try:
            with pytest.raises(SystemExit) as exc_info:
                ensure_labels(mock_platform)
            assert exc_info.value.code == 1
            captured = capsys.readouterr()
            assert "bad:label" in captured.err
            assert "not-hex" in captured.err
        finally:
            afissues.labels.REQUIRED_LABELS = original_labels


# ===================================================================
# TS-NS-5: Auto-creation of global config (unchanged)
# ===================================================================
class TestGlobalConfigAutoCreation:
    """TS-NS-5: No config anywhere -> global config auto-created.

    Requirements: NS-REQ-4
    """

    def test_global_config_created_when_both_absent(self, tmp_path, monkeypatch) -> None:
        """When neither local nor global config exists, ~/.nightshift/config.toml is created."""
        from afcore.core.config import load_config

        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

        repo = tmp_path / "repo"
        repo.mkdir()
        monkeypatch.chdir(repo)

        load_config()

        global_config = fake_home / ".nightshift" / "config.toml"
        local_config = repo / ".nightshift" / "config.toml"
        assert global_config.exists(), "Global config should be created"
        assert not local_config.exists(), "No local config should be created"

    def test_global_config_uses_default_template(self, tmp_path, monkeypatch) -> None:
        """Auto-created global config uses the default template content."""
        from afcore.core.config import load_config

        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

        repo = tmp_path / "repo"
        repo.mkdir()
        monkeypatch.chdir(repo)

        load_config()

        global_config = fake_home / ".nightshift" / "config.toml"
        content = global_config.read_text()
        assert "Night Shift" in content
        assert "platform" in content.lower()


# ===================================================================
# TS-NS-6: Existing config not modified (unchanged)
# ===================================================================
class TestExistingConfigNotModified:
    """TS-NS-6: Existing local or global config not modified on startup.

    Requirements: NS-REQ-5
    """

    def test_existing_local_config_not_modified(self, tmp_path, monkeypatch) -> None:
        """With a local config, no config is created or modified."""
        from afcore.core.config import load_config

        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

        repo = tmp_path / "repo"
        repo.mkdir()
        local_dir = repo / ".nightshift"
        local_dir.mkdir()
        local_config = local_dir / "config.toml"
        local_config.write_text("# existing local\n")
        mtime_before = local_config.stat().st_mtime

        monkeypatch.chdir(repo)
        load_config()

        assert local_config.stat().st_mtime == mtime_before
        global_config = fake_home / ".nightshift" / "config.toml"
        assert not global_config.exists(), "No global config should be created when local exists"

    def test_existing_global_config_not_modified(self, tmp_path, monkeypatch) -> None:
        """With only a global config, it is not modified and no local config is created."""
        from afcore.core.config import load_config

        fake_home = tmp_path / "home"
        fake_home.mkdir()
        global_dir = fake_home / ".nightshift"
        global_dir.mkdir()
        global_config = global_dir / "config.toml"
        global_config.write_text("[orchestrator]\nmax_retries = 3\n")
        mtime_before = global_config.stat().st_mtime

        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

        repo = tmp_path / "repo"
        repo.mkdir()
        monkeypatch.chdir(repo)

        load_config()

        assert global_config.stat().st_mtime == mtime_before
        local_config = repo / ".nightshift" / "config.toml"
        assert not local_config.exists(), "No local config should be created when global exists"
