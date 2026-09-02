"""Theme system tests.

Test Spec: TS-01-E6 (invalid color fallback)
Requirements: 01-REQ-7.1, 01-REQ-7.3, 01-REQ-7.4, 01-REQ-7.E1
"""

from __future__ import annotations

from afcore.core.config import ThemeConfig
from afcore.ui.display import AppTheme, create_theme


class TestThemeColorRoles:
    """Theme exposes required color roles."""

    def test_theme_has_color_roles(self) -> None:
        """Theme exposes header and muted roles."""
        theme = create_theme(ThemeConfig())

        assert isinstance(theme, AppTheme)
        for role in ("header", "muted"):
            styled = theme.styled("test", role)
            assert isinstance(styled, str)


class TestThemeInvalidColor:
    """TS-01-E6: Invalid Rich style falls back to default."""

    def test_invalid_style_creates_theme(self) -> None:
        """Theme is created without error even with invalid style value."""
        theme = create_theme(ThemeConfig(header="not_a_valid_style"))

        assert theme is not None
        assert isinstance(theme, AppTheme)

    def test_invalid_style_still_functions(self) -> None:
        """Theme with invalid style can still render output."""
        theme = create_theme(ThemeConfig(header="not_a_valid_style"))

        # Should not raise — falls back to default
        theme.print("test text", role="header")
