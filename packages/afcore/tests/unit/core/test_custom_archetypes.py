"""Unit tests for custom archetype extensibility (spec 99).

Covers: TS-99-8, TS-99-9, TS-99-10, TS-99-E4, TS-99-E5
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest


def test_custom_profile_detection(tmp_path: Path) -> None:
    """TS-99-8: Custom archetype detected from profile file existence.

    Requirement: 99-REQ-4.1
    """
    from afcore.session.profiles import has_custom_profile

    profiles_dir = tmp_path / ".nightshift" / "profiles"
    profiles_dir.mkdir(parents=True)
    (profiles_dir / "deployer.md").write_text("# Deployer")

    assert has_custom_profile("deployer", project_dir=tmp_path) is True
    assert has_custom_profile("unknown", project_dir=tmp_path) is False


def test_permission_preset(tmp_path: Path) -> None:
    """TS-99-9: Custom archetype defaults to coder permissions.

    Requirement: 99-REQ-4.2, 99-REQ-4.4
    """
    from afcore.archetypes import ARCHETYPE_REGISTRY, get_archetype

    profiles_dir = tmp_path / ".nightshift" / "profiles"
    profiles_dir.mkdir(parents=True)
    (profiles_dir / "deployer.md").write_text("# Deployer Profile")

    entry = get_archetype("deployer", project_dir=tmp_path, config=None)
    coder = ARCHETYPE_REGISTRY["coder"]

    assert entry.default_allowlist == coder.default_allowlist


def test_custom_in_task_group(tmp_path: Path) -> None:
    """TS-99-10: Custom archetype prompt contains the custom profile content.

    Requirement: 99-REQ-4.3
    """
    from afcore.session.prompt import build_system_prompt

    profiles_dir = tmp_path / ".nightshift" / "profiles"
    profiles_dir.mkdir(parents=True)
    (profiles_dir / "deployer.md").write_text("# Deployer Profile CONTENT")

    prompt = build_system_prompt(
        "task context",
        archetype="deployer",
        project_dir=tmp_path,
    )

    assert "Deployer Profile CONTENT" in prompt


def test_no_preset(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """TS-99-E4: Custom archetype without permission preset defaults to coder.

    Requirement: 99-REQ-4.E1
    """
    from afcore.archetypes import ARCHETYPE_REGISTRY, get_archetype

    profiles_dir = tmp_path / ".nightshift" / "profiles"
    profiles_dir.mkdir(parents=True)
    (profiles_dir / "deployer.md").write_text("# Deployer Profile")

    with caplog.at_level(logging.WARNING):
        entry = get_archetype("deployer", project_dir=tmp_path, config=None)

    coder = ARCHETYPE_REGISTRY["coder"]
    assert entry.default_allowlist == coder.default_allowlist
    assert any("deployer" in record.message for record in caplog.records)


def test_unknown_custom_defaults_to_coder(tmp_path: Path) -> None:
    """Custom archetype always defaults to coder permissions.

    Custom permission presets are no longer configurable.
    """
    from afcore.archetypes import ARCHETYPE_REGISTRY, get_archetype

    profiles_dir = tmp_path / ".nightshift" / "profiles"
    profiles_dir.mkdir(parents=True)
    (profiles_dir / "deployer.md").write_text("# Deployer Profile")

    entry = get_archetype("deployer", project_dir=tmp_path, config=None)
    coder = ARCHETYPE_REGISTRY["coder"]
    assert entry.name == "deployer"
    assert entry.default_allowlist == coder.default_allowlist
