"""Tests for afhub auth helpers.

Covers: TS-01-46, TS-01-47, TS-01-48 (spec 01, group 7).
Requirements: 01-REQ-7 (01-REQ-7.1 through 01-REQ-7.3, edge cases E1-E5).
Correctness property: 01-PROP-7.

These tests are written against the stub implementation and will FAIL until
group 11 provides the real implementation.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

from afhub.auth import resolve_hub_pat, resolve_hub_url

# ---------------------------------------------------------------------------
# TS-01-46: resolve_hub_pat returns token_flag when provided, then falls back
#           to AF_HUB_TOKEN env var, then returns None
# ---------------------------------------------------------------------------


class TestResolveHubPat:
    """TS-01-46 -- resolve_hub_pat returns token_flag when provided, then falls
    back to AF_HUB_TOKEN env var, then returns None.

    Requirements: 01-REQ-7.1, 01-REQ-7.E1, 01-REQ-7.E3
    Correctness property: 01-PROP-7
    """

    def test_returns_token_flag_when_provided(self) -> None:
        """resolve_hub_pat returns token_flag when it is not None."""
        result = resolve_hub_pat(token_flag="flag-token")
        assert result == "flag-token"

    def test_returns_env_var_when_token_flag_is_none(self) -> None:
        """resolve_hub_pat returns AF_HUB_TOKEN env var when token_flag is None."""
        with patch.dict(os.environ, {"AF_HUB_TOKEN": "env-token"}):
            result = resolve_hub_pat()
            assert result == "env-token"

    def test_returns_none_when_both_absent(self) -> None:
        """resolve_hub_pat returns None when token_flag is None and AF_HUB_TOKEN
        is unset (01-REQ-7.E1).
        """
        with patch.dict(os.environ, {}, clear=True):
            result = resolve_hub_pat()
            assert result is None

    def test_token_flag_takes_priority_over_env_var(self) -> None:
        """resolve_hub_pat returns token_flag, ignoring AF_HUB_TOKEN when both
        are set (01-REQ-7.E3, 01-PROP-7).
        """
        with patch.dict(os.environ, {"AF_HUB_TOKEN": "env-token"}):
            result = resolve_hub_pat(token_flag="flag-token")
            assert result == "flag-token"


# ---------------------------------------------------------------------------
# TS-01-47: resolve_hub_url returns hub_url_flag, then AF_HUB_URL env var,
#           then non-empty config_url, then None
# ---------------------------------------------------------------------------


class TestResolveHubUrl:
    """TS-01-47 -- resolve_hub_url returns hub_url_flag when provided, then
    AF_HUB_URL env var when flag is None, then non-empty config_url, then None.

    Requirements: 01-REQ-7.2, 01-REQ-7.E2, 01-REQ-7.E4, 01-REQ-7.E5
    """

    def test_returns_hub_url_flag_when_provided(self) -> None:
        """resolve_hub_url returns hub_url_flag when it is not None."""
        result = resolve_hub_url(hub_url_flag="https://flag.example.com")
        assert result == "https://flag.example.com"

    def test_returns_env_var_when_flag_is_none(self) -> None:
        """resolve_hub_url returns AF_HUB_URL env var when hub_url_flag is None."""
        with patch.dict(os.environ, {"AF_HUB_URL": "https://env.example.com"}):
            result = resolve_hub_url()
            assert result == "https://env.example.com"

    def test_returns_config_url_when_flag_and_env_absent(self) -> None:
        """resolve_hub_url returns config_url when flag and env var are absent
        (01-REQ-7.E4).
        """
        with patch.dict(os.environ, {}, clear=True):
            result = resolve_hub_url(config_url="https://config.example.com")
            assert result == "https://config.example.com"

    def test_returns_none_when_all_absent(self) -> None:
        """resolve_hub_url returns None when all sources are absent (01-REQ-7.E2)."""
        with patch.dict(os.environ, {}, clear=True):
            result = resolve_hub_url()
            assert result is None

    def test_env_var_takes_priority_over_config_url(self) -> None:
        """resolve_hub_url returns AF_HUB_URL, ignoring config_url when both
        are set (01-REQ-7.E5).
        """
        with patch.dict(os.environ, {"AF_HUB_URL": "https://env.example.com"}):
            result = resolve_hub_url(config_url="https://config.example.com")
            assert result == "https://env.example.com"

    def test_hub_url_flag_takes_priority_over_env_var(self) -> None:
        """resolve_hub_url returns hub_url_flag, ignoring env var when both
        are set.
        """
        with patch.dict(os.environ, {"AF_HUB_URL": "https://env.example.com"}):
            result = resolve_hub_url(hub_url_flag="https://flag.example.com")
            assert result == "https://flag.example.com"

    def test_empty_config_url_is_treated_as_absent(self) -> None:
        """resolve_hub_url returns None when config_url is empty string."""
        with patch.dict(os.environ, {}, clear=True):
            result = resolve_hub_url(config_url="")
            assert result is None


# ---------------------------------------------------------------------------
# TS-01-48: resolve_hub_url never reads any config file directly
# ---------------------------------------------------------------------------


class TestResolveHubUrlNoFileIO:
    """TS-01-48 -- resolve_hub_url never reads any config file directly; it
    only evaluates the pre-resolved config_url string parameter.

    Requirements: 01-REQ-7.3
    """

    def test_no_file_io_occurs(self) -> None:
        """resolve_hub_url does not open or read any files."""
        mock_open = MagicMock(side_effect=AssertionError("file I/O should not occur"))
        with patch("builtins.open", mock_open):
            with patch.dict(os.environ, {}, clear=True):
                result = resolve_hub_url()
                assert result is None
        mock_open.assert_not_called()
