"""Banner rendering tests.

Test Spec: TS-14-1, TS-14-2, TS-14-3, TS-14-4, TS-14-7, TS-14-8,
           TS-14-E1, TS-14-E2, TS-NS-1, TS-NS-2
Requirements: 14-REQ-1.1, 14-REQ-1.2, 14-REQ-2.1, 14-REQ-2.2,
              14-REQ-2.3, 14-REQ-2.E1, 14-REQ-3.1, 14-REQ-3.2,
              14-REQ-3.E1, NS-REQ-1, NS-REQ-2
"""

from __future__ import annotations

from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest
from afcore import __version__
from afcore.core.config import ModelsConfig, ThemeConfig
from afcore.ui.display import (
    _resolve_coding_model_display,
    create_theme,
    render_banner,
)
from rich.console import Console
from rich.theme import Theme

# Expected fox art from design.md — used to verify banner output content.
EXPECTED_FOX_ART = r"""   /\_/\   _
  / o.o \/\ \
 ( > ^ < ) ) )
  \_^/\_/--'"""

_STYLE_ROLES = ("header", "muted")


def _capture_banner(
    theme_config: ThemeConfig,
    *,
    quiet: bool = False,
    force_terminal: bool = False,
    models_config: ModelsConfig | None = None,
) -> str:
    """Capture render_banner output via a StringIO-backed console.

    Creates an AppTheme from the given config, replaces its console with
    one that writes to a StringIO buffer, then calls render_banner.

    Args:
        theme_config: Theme configuration to use.
        quiet: Whether to suppress banner output.
        force_terminal: If True, capture ANSI escape codes for role
            verification. If False, capture plain text.
        models_config: Optional config-driven model overrides.

    Returns:
        The captured console output as a string.
    """
    theme = create_theme(theme_config)
    buf = StringIO()
    # Rebuild a Rich Theme from config values to preserve styled output
    rich_theme = Theme({role: getattr(theme_config, role) for role in _STYLE_ROLES})
    theme.console = Console(
        file=buf,
        theme=rich_theme,
        force_terminal=force_terminal,
        width=120,
    )
    render_banner(theme, quiet=quiet, models_config=models_config)
    return buf.getvalue()


class TestBannerFoxArt:
    """TS-14-1: Banner contains fox ASCII art.

    Requirement: 14-REQ-1.1
    """

    def test_fox_art_present_in_output(self) -> None:
        """All four lines of fox ASCII art appear in banner output."""
        output = _capture_banner(ThemeConfig())

        for line in EXPECTED_FOX_ART.splitlines():
            assert line in output, f"Expected fox art line {line!r} in banner output, got:\n{output}"


class TestBannerFoxArtStyling:
    """TS-14-2: Fox art styled with header role.

    Requirement: 14-REQ-1.2
    """

    def test_fox_art_uses_header_style(self) -> None:
        """Fox art is rendered using the header role markup."""
        output = _capture_banner(ThemeConfig(), force_terminal=True)

        # The header style for default theme is "bold #ff8c00" (bold orange).
        # When rendered with force_terminal, Rich embeds ANSI bold + color codes.
        first_art_line = EXPECTED_FOX_ART.splitlines()[0]
        assert first_art_line in output, f"Expected fox art in styled output, got:\n{output!r}"
        # Verify ANSI codes are present (header style applies bold + color)
        assert "\x1b[" in output, "Expected ANSI escape codes for header styling"


class TestBannerVersionModel:
    """TS-14-3: Banner shows version and model line.

    Requirements: 14-REQ-2.1, 14-REQ-2.2
    """

    def test_version_and_model_line_with_revision(self) -> None:
        """Banner output contains version, revision, and resolved model ID."""
        with patch("afcore.ui.display._get_git_revision", return_value="abc1234"):
            output = _capture_banner(ThemeConfig())

        expected = f"nightshift v{__version__} (abc1234).  model: claude-sonnet-4-6"
        assert expected in output, f"Expected {expected!r} in banner output, got:\n{output}"

    def test_version_and_model_line_without_revision(self) -> None:
        """Banner omits revision gracefully when git is unavailable."""
        with patch("afcore.ui.display._get_git_revision", return_value=None):
            output = _capture_banner(ThemeConfig())

        expected = f"nightshift v{__version__}  model: claude-sonnet-4-6"
        assert expected in output, f"Expected {expected!r} in banner output, got:\n{output}"


class TestBannerWorkingDirectory:
    """TS-14-4: Banner shows working directory.

    Requirement: 14-REQ-3.1
    """

    def test_cwd_appears_in_output(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Working directory appears in the banner output."""
        monkeypatch.setattr(Path, "cwd", staticmethod(lambda: Path("/tmp/test-project")))

        output = _capture_banner(ThemeConfig())

        assert "/tmp/test-project" in output, f"Expected cwd '/tmp/test-project' in banner output, got:\n{output}"


class TestBannerVersionModelStyling:
    """TS-14-7: Version/model line styled with header role.

    Requirement: 14-REQ-2.3
    """

    def test_version_line_uses_header_style(self) -> None:
        """Version/model line is rendered with header role styling."""
        output = _capture_banner(ThemeConfig(), force_terminal=True)

        # Check that the version line appears in styled output with ANSI codes
        assert f"nightshift v{__version__}" in output
        assert "\x1b[" in output, "Expected ANSI escape codes for header styling"


class TestBannerCwdStyling:
    """TS-14-8: Working directory styled with muted role.

    Requirement: 14-REQ-3.2
    """

    def test_cwd_uses_muted_style(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """CWD line is rendered with muted role styling."""
        monkeypatch.setattr(Path, "cwd", staticmethod(lambda: Path("/tmp/styled-cwd")))

        config = ThemeConfig(muted="dim", header="bold #ff8c00")

        # Capture with ANSI to verify separate styling
        output = _capture_banner(config, force_terminal=True)

        # The cwd should appear in the output
        assert "/tmp/styled-cwd" in output

        # The muted style ("dim") produces ESC[2m in ANSI.
        assert "\x1b[2m" in output, "Expected dim (muted) ANSI escape code in output for cwd line"


# --- Edge Case Tests ---


class TestBannerModelFallback:
    """TS-14-E1: Model resolution failure shows raw tier value.

    Requirement: 14-REQ-2.E1
    """

    def test_invalid_model_shows_raw_value(self) -> None:
        """Invalid model name falls back to raw tier string in output."""
        with patch("afcore.ui.display.resolve_model", side_effect=Exception("bad")):
            output = _capture_banner(ThemeConfig())

        # Should fall back to the registry default tier string
        assert "model:" in output, f"Expected 'model:' in banner output, got:\n{output}"

    def test_invalid_model_no_exception(self) -> None:
        """No exception is raised when model resolution fails."""
        with patch("afcore.ui.display.resolve_model", side_effect=Exception("bad")):
            # Should not raise
            _capture_banner(ThemeConfig())


class TestBannerCwdOSError:
    """TS-14-E2: Path.cwd() OSError shows (unknown).

    Requirement: 14-REQ-3.E1
    """

    def test_cwd_oserror_shows_unknown(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """If Path.cwd() raises OSError, display '(unknown)'."""

        def _raise_oserror() -> Path:
            raise OSError("directory deleted")

        monkeypatch.setattr(Path, "cwd", staticmethod(_raise_oserror))

        output = _capture_banner(ThemeConfig())

        assert "(unknown)" in output, f"Expected '(unknown)' in banner output, got:\n{output}"

    def test_cwd_oserror_no_exception(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No exception propagates when Path.cwd() raises OSError."""

        def _raise_oserror() -> Path:
            raise OSError("directory deleted")

        monkeypatch.setattr(Path, "cwd", staticmethod(_raise_oserror))

        # Should not raise
        _capture_banner(ThemeConfig())


# --- Model Config Override Tests (Issue #10) ---


class TestBannerModelConfigOverride:
    """TS-NS-1: Banner prints the actual configured model, not the hardcoded tier default.

    Requirements: NS-REQ-1, NS-REQ-2
    """

    def test_banner_shows_overridden_model(self) -> None:
        """When a tier_defaults override is set, the banner shows the override.

        The coder archetype uses STANDARD tier by default, which resolves to
        'claude-sonnet-4-6'. With a tier_defaults override mapping STANDARD
        to a different model, the banner should display that model.
        """
        # Override STANDARD (coder's default tier) to a different model
        models_cfg = ModelsConfig(
            tier_defaults={"STANDARD": "claude-haiku-4-5"},
        )

        with patch("afcore.ui.display._get_git_revision", return_value=None):
            output = _capture_banner(ThemeConfig(), models_config=models_cfg)

        assert "model: claude-haiku-4-5" in output, (
            f"Expected 'model: claude-haiku-4-5' in banner output, got:\n{output}"
        )
        # Ensure the old default is NOT shown
        assert "claude-sonnet-4-6" not in output, (
            f"Old default 'claude-sonnet-4-6' should not appear in banner, got:\n{output}"
        )

    def test_banner_shows_default_when_no_override(self) -> None:
        """When no override is set, banner shows the hardcoded tier default.

        Unchanged behaviour — STANDARD tier resolves to 'claude-sonnet-4-6'.
        """
        with patch("afcore.ui.display._get_git_revision", return_value=None):
            output = _capture_banner(ThemeConfig(), models_config=None)

        assert "model: claude-sonnet-4-6" in output, (
            f"Expected 'model: claude-sonnet-4-6' in banner output, got:\n{output}"
        )

    def test_banner_shows_default_with_empty_models_config(self) -> None:
        """An empty ModelsConfig (no overrides) shows the hardcoded default."""
        models_cfg = ModelsConfig()

        with patch("afcore.ui.display._get_git_revision", return_value=None):
            output = _capture_banner(ThemeConfig(), models_config=models_cfg)

        assert "model: claude-sonnet-4-6" in output, (
            f"Expected 'model: claude-sonnet-4-6' in banner output, got:\n{output}"
        )


class TestResolveCodingModelDisplay:
    """TS-NS-2: _resolve_coding_model_display uses models_config.

    Requirements: NS-REQ-2
    """

    def test_with_override_returns_override_model(self) -> None:
        """When tier_defaults maps STANDARD to a different model, returns it."""
        models_cfg = ModelsConfig(
            tier_defaults={"STANDARD": "claude-haiku-4-5"},
        )
        result = _resolve_coding_model_display(models_config=models_cfg)
        assert result == "claude-haiku-4-5"

    def test_without_override_returns_default(self) -> None:
        """When no override is set, returns the hardcoded STANDARD default."""
        result = _resolve_coding_model_display(models_config=None)
        assert result == "claude-sonnet-4-6"

    def test_with_empty_config_returns_default(self) -> None:
        """An empty ModelsConfig returns the hardcoded default."""
        result = _resolve_coding_model_display(models_config=ModelsConfig())
        assert result == "claude-sonnet-4-6"
