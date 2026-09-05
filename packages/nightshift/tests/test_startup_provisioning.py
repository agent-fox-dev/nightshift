"""Tests for startup label provisioning and config auto-creation.

Requirements: NS-REQ-2, NS-REQ-3, NS-REQ-4, NS-REQ-5
Test Spec: TS-NS-2, TS-NS-3, TS-NS-4, TS-NS-5
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from afissues.errors import IntegrationError
from afissues.labels import REQUIRED_LABELS


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

    def test_ensure_labels_skips_existing_labels(self) -> None:
        """Labels that already exist (422) are silently skipped."""
        from nightshift._label_provisioning import ensure_labels

        mock_platform = AsyncMock()
        # Simulate GitHub 422 "already exists" for all labels
        mock_platform.create_label.side_effect = IntegrationError("Validation Failed (422)")
        # Should not raise or exit
        ensure_labels(mock_platform)

    def test_ensure_labels_skips_already_exists_error(self) -> None:
        """Labels that already exist (already_exists) are silently skipped."""
        from nightshift._label_provisioning import ensure_labels

        mock_platform = AsyncMock()
        mock_platform.create_label.side_effect = IntegrationError("already_exists")
        ensure_labels(mock_platform)

    def test_ensure_labels_mixed_create_and_existing(self) -> None:
        """Some labels are created, others already exist — no error."""
        from nightshift._label_provisioning import ensure_labels

        call_count = 0

        async def _side_effect(name, color, description=""):
            nonlocal call_count
            call_count += 1
            # Alternate: first call succeeds, second raises 422
            if call_count % 2 == 0:
                raise IntegrationError("Validation Failed (422)")

        mock_platform = AsyncMock()
        mock_platform.create_label.side_effect = _side_effect
        ensure_labels(mock_platform)

        assert call_count == len(REQUIRED_LABELS)


# ===================================================================
# TS-NS-3: Label creation failure exits with clear explanation
# ===================================================================
class TestLabelCreationFailure:
    """TS-NS-3: If labels cannot be created, nightshift exits with explanation.

    Requirements: NS-REQ-3
    """

    def test_permission_error_exits_nonzero(self) -> None:
        """IntegrationError (non-422) causes sys.exit(1) with explanation."""
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
# TS-NS-4: Auto-creation of global config
# ===================================================================
class TestGlobalConfigAutoCreation:
    """TS-NS-4: No config anywhere -> global config auto-created.

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
# TS-NS-5: Existing config not modified
# ===================================================================
class TestExistingConfigNotModified:
    """TS-NS-5: Existing local or global config not modified on startup.

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
