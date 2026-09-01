"""Shared fixtures for security unit tests."""

from __future__ import annotations

import pytest
from afcore.core.config import SecurityConfig


@pytest.fixture
def security_config() -> SecurityConfig:
    """Return a default SecurityConfig for testing."""
    return SecurityConfig()
