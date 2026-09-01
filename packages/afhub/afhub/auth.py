"""Authentication helpers for afhub.

Provides resolve_hub_pat and resolve_hub_url for resolving hub credentials
and endpoint URL from CLI flags, environment variables, and config values
in a defined priority order.
"""

from __future__ import annotations

import os


def resolve_hub_pat(
    *,
    token_flag: str | None = None,
    env_var: str = "AF_HUB_TOKEN",
) -> str | None:
    """Resolve the hub Personal Access Token.

    Priority order:
      1. token_flag (if not None)
      2. Environment variable named by *env_var* (if set)
      3. None

    Parameters
    ----------
    token_flag:
        Explicit PAT value, typically from a CLI ``--token`` flag.
    env_var:
        Name of the environment variable to check as a fallback.

    Returns
    -------
    str | None
        The resolved PAT string, or ``None`` if no source provides one.
    """
    if token_flag is not None:
        return token_flag

    env_value = os.environ.get(env_var)
    if env_value is not None:
        return env_value

    return None


def resolve_hub_url(
    *,
    hub_url_flag: str | None = None,
    config_url: str = "",
    env_var: str = "AF_HUB_URL",
) -> str | None:
    """Resolve the hub base URL.

    Priority order:
      1. hub_url_flag (if not None)
      2. Environment variable named by *env_var* (if set)
      3. config_url (if non-empty string)
      4. None

    This function does **not** read any config file directly; the caller is
    responsible for populating *config_url* from their own config source.

    Parameters
    ----------
    hub_url_flag:
        Explicit URL value, typically from a CLI ``--hub-url`` flag.
    config_url:
        Pre-resolved URL from a config file. Empty string is treated as absent.
    env_var:
        Name of the environment variable to check as a fallback.

    Returns
    -------
    str | None
        The resolved URL string, or ``None`` if no source provides one.
    """
    if hub_url_flag is not None:
        return hub_url_flag

    env_value = os.environ.get(env_var)
    if env_value is not None:
        return env_value

    if config_url:
        return config_url

    return None
