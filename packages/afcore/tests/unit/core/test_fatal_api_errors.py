"""Tests for non-recoverable (fatal) Anthropic API error classification.

A rejected API key or an exhausted credit balance cannot be fixed by
retrying, so ``retry_api_call``/``retry_api_call_async`` translate those
responses into :class:`FatalAPIError` immediately instead of burning the
retry schedule and letting callers degrade silently.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from afcore.core.client import (
    classify_fatal_api_error,
    retry_api_call,
    retry_api_call_async,
)
from afcore.core.errors import FatalAPIError
from anthropic import APIStatusError, RateLimitError

CREDIT_MESSAGE = (
    "Your credit balance is too low to access the Anthropic API. "
    "Please go to Plans & Billing to upgrade or purchase credits."
)


def _make_status_error(status: int, message: str) -> APIStatusError:
    exc = APIStatusError.__new__(APIStatusError)
    exc.status_code = status
    exc.message = message
    exc.body = None
    exc.response = MagicMock(status_code=status, headers={})
    return exc


def _make_rate_limit_error() -> RateLimitError:
    exc = RateLimitError.__new__(RateLimitError)
    exc.status_code = 429
    exc.message = "rate limited"
    exc.body = None
    exc.response = MagicMock(status_code=429, headers={})
    return exc


class TestClassifyFatalApiError:
    """classify_fatal_api_error identifies only non-recoverable conditions."""

    def test_credit_balance_400_is_fatal(self) -> None:
        reason = classify_fatal_api_error(_make_status_error(400, CREDIT_MESSAGE))
        assert reason is not None
        assert "credit balance is too low" in reason
        # The operator sees what the API actually said.
        assert "Plans & Billing" in reason

    def test_payment_required_402_is_fatal(self) -> None:
        reason = classify_fatal_api_error(_make_status_error(402, "payment required"))
        assert reason is not None
        assert "credit balance is too low" in reason

    def test_unauthenticated_401_is_fatal(self) -> None:
        reason = classify_fatal_api_error(_make_status_error(401, "invalid x-api-key"))
        assert reason is not None
        assert "authentication failed" in reason
        assert "ANTHROPIC_API_KEY" in reason

    def test_forbidden_403_is_fatal(self) -> None:
        reason = classify_fatal_api_error(_make_status_error(403, "not permitted"))
        assert reason is not None
        assert "access denied" in reason

    def test_ordinary_400_is_not_fatal(self) -> None:
        """A malformed request is not a billing problem."""
        assert classify_fatal_api_error(_make_status_error(400, "max_tokens: too large")) is None

    def test_rate_limit_is_not_fatal(self) -> None:
        assert classify_fatal_api_error(_make_rate_limit_error()) is None

    def test_server_error_is_not_fatal(self) -> None:
        assert classify_fatal_api_error(_make_status_error(500, "overloaded")) is None

    def test_non_api_exception_is_not_fatal(self) -> None:
        assert classify_fatal_api_error(OSError("connection reset")) is None


class TestRetryRaisesFatalImmediately:
    """Fatal conditions abort on the first attempt, without sleeping."""

    @pytest.mark.asyncio
    async def test_async_raises_fatal_without_retrying(self) -> None:
        fn = AsyncMock(side_effect=_make_status_error(400, CREDIT_MESSAGE))

        with patch("afcore.core.client.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            with pytest.raises(FatalAPIError) as excinfo:
                await retry_api_call_async(fn, context="staleness check")

        assert fn.call_count == 1
        assert mock_sleep.call_count == 0
        assert "credit balance is too low" in str(excinfo.value)
        assert excinfo.value.context["call_context"] == "staleness check"
        assert isinstance(excinfo.value.__cause__, APIStatusError)

    def test_sync_raises_fatal_without_retrying(self) -> None:
        fn = MagicMock(side_effect=_make_status_error(401, "invalid x-api-key"))

        with patch("afcore.core.client.time.sleep") as mock_sleep:
            with pytest.raises(FatalAPIError):
                retry_api_call(fn, context="knowledge harvest")

        assert fn.call_count == 1
        assert mock_sleep.call_count == 0

    @pytest.mark.asyncio
    async def test_transient_errors_still_retry(self) -> None:
        """Non-fatal errors keep their existing retry behaviour."""
        fn = AsyncMock(side_effect=[_make_rate_limit_error(), "ok"])

        with patch("afcore.core.client.asyncio.sleep", new_callable=AsyncMock):
            result = await retry_api_call_async(fn, context="test")

        assert result == "ok"
        assert fn.call_count == 2
