"""Tests for afhub polling helpers.

Covers: TS-01-49, TS-01-50, TS-01-51 (spec 01, group 7).
Requirements: 01-REQ-8 (01-REQ-8.1 through 01-REQ-8.3, edge cases E1-E6).
Correctness property: 01-PROP-8.

These tests are written against the stub implementation and will FAIL until
group 15 provides the real implementation.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from afhub.errors import HubConnectionError, HubError
from afhub.models import RebuildJob, Workspace
from afhub.polling import poll_clone_ready, poll_rebuild

# ---------------------------------------------------------------------------
# TS-01-49: poll_rebuild polls get_rebuild until terminal status
# ---------------------------------------------------------------------------


class TestPollRebuild:
    """TS-01-49 -- poll_rebuild polls get_rebuild until status is a terminal
    value and returns the terminal RebuildJob.

    Requirements: 01-REQ-8.1
    Correctness property: 01-PROP-8
    """

    async def test_returns_terminal_rebuild_job_completed(self) -> None:
        """poll_rebuild returns the RebuildJob when status is 'completed'."""
        non_terminal = RebuildJob(
            id="job-1", status="running", created_at="2026-01-01T00:00:00Z"
        )
        terminal = RebuildJob(
            id="job-1", status="completed", created_at="2026-01-01T00:00:00Z"
        )
        client = MagicMock()
        client.get_rebuild = AsyncMock(side_effect=[non_terminal, terminal])
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await poll_rebuild(
                client, "ws1", "job-1", timeout=600.0, interval=5.0
            )
            assert result.status == "completed"
            mock_sleep.assert_called_with(5.0)

    async def test_sleeps_between_non_terminal_polls(self) -> None:
        """poll_rebuild calls asyncio.sleep(interval) between non-terminal polls."""
        non_terminal = RebuildJob(
            id="job-1", status="running", created_at="2026-01-01T00:00:00Z"
        )
        terminal = RebuildJob(
            id="job-1", status="completed", created_at="2026-01-01T00:00:00Z"
        )
        client = MagicMock()
        client.get_rebuild = AsyncMock(
            side_effect=[non_terminal, non_terminal, terminal]
        )
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await poll_rebuild(
                client, "ws1", "job-1", timeout=600.0, interval=5.0
            )
            assert mock_sleep.call_count == 2

    async def test_terminal_status_failed(self) -> None:
        """poll_rebuild returns when status is 'failed'."""
        terminal = RebuildJob(
            id="job-1", status="failed", created_at="2026-01-01T00:00:00Z"
        )
        client = MagicMock()
        client.get_rebuild = AsyncMock(return_value=terminal)
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await poll_rebuild(
                client, "ws1", "job-1", timeout=600.0, interval=5.0
            )
            assert result.status == "failed"
            mock_sleep.assert_not_called()

    async def test_terminal_status_dead_letter(self) -> None:
        """poll_rebuild returns when status is 'dead_letter'."""
        terminal = RebuildJob(
            id="job-1", status="dead_letter", created_at="2026-01-01T00:00:00Z"
        )
        client = MagicMock()
        client.get_rebuild = AsyncMock(return_value=terminal)
        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await poll_rebuild(
                client, "ws1", "job-1", timeout=600.0, interval=5.0
            )
            assert result.status == "dead_letter"

    async def test_terminal_status_cancelled(self) -> None:
        """poll_rebuild returns when status is 'cancelled'."""
        terminal = RebuildJob(
            id="job-1", status="cancelled", created_at="2026-01-01T00:00:00Z"
        )
        client = MagicMock()
        client.get_rebuild = AsyncMock(return_value=terminal)
        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await poll_rebuild(
                client, "ws1", "job-1", timeout=600.0, interval=5.0
            )
            assert result.status == "cancelled"

    async def test_first_poll_is_immediate_no_preceding_sleep(self) -> None:
        """poll_rebuild calls get_rebuild immediately without sleeping first
        (01-REQ-8.1).
        """
        terminal = RebuildJob(
            id="job-1", status="completed", created_at="2026-01-01T00:00:00Z"
        )
        client = MagicMock()
        client.get_rebuild = AsyncMock(return_value=terminal)
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await poll_rebuild(
                client, "ws1", "job-1", timeout=600.0, interval=5.0
            )
            # Terminal on first poll -> no sleep at all
            mock_sleep.assert_not_called()
            client.get_rebuild.assert_called_once()

    async def test_no_further_get_rebuild_after_terminal(self) -> None:
        """poll_rebuild does not issue further get_rebuild calls after
        observing a terminal status (01-PROP-8).
        """
        non_terminal = RebuildJob(
            id="job-1", status="running", created_at="2026-01-01T00:00:00Z"
        )
        terminal = RebuildJob(
            id="job-1", status="completed", created_at="2026-01-01T00:00:00Z"
        )
        client = MagicMock()
        client.get_rebuild = AsyncMock(side_effect=[non_terminal, terminal])
        with patch("asyncio.sleep", new_callable=AsyncMock):
            await poll_rebuild(
                client, "ws1", "job-1", timeout=600.0, interval=5.0
            )
            assert client.get_rebuild.call_count == 2


# ---------------------------------------------------------------------------
# 01-REQ-8.E1: poll_rebuild raises TimeoutError on timeout
# ---------------------------------------------------------------------------


class TestPollRebuildTimeout:
    """01-REQ-8.E1 -- poll_rebuild raises TimeoutError when the timeout elapses
    before the RebuildJob reaches a terminal status.

    Requirements: 01-REQ-8.E1, 01-REQ-8.E6
    """

    async def test_raises_timeout_error_when_no_terminal_status(self) -> None:
        """poll_rebuild raises TimeoutError when timeout elapses."""
        non_terminal = RebuildJob(
            id="job-1", status="running", created_at="2026-01-01T00:00:00Z"
        )
        client = MagicMock()
        client.get_rebuild = AsyncMock(return_value=non_terminal)
        with patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(TimeoutError):
                await poll_rebuild(
                    client, "ws1", "job-1", timeout=0.0, interval=5.0
                )

    async def test_timeout_zero_returns_terminal_immediately(self) -> None:
        """poll_rebuild with timeout=0 returns terminal RebuildJob if first
        poll is terminal (01-REQ-8.E6).
        """
        terminal = RebuildJob(
            id="job-1", status="completed", created_at="2026-01-01T00:00:00Z"
        )
        client = MagicMock()
        client.get_rebuild = AsyncMock(return_value=terminal)
        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await poll_rebuild(
                client, "ws1", "job-1", timeout=0.0, interval=5.0
            )
            assert result.status == "completed"


# ---------------------------------------------------------------------------
# 01-REQ-8.E3: poll_rebuild surfaces HubConnectionError immediately
# ---------------------------------------------------------------------------


class TestPollRebuildConnectionError:
    """01-REQ-8.E3 -- poll_rebuild aborts immediately and surfaces
    HubConnectionError from get_rebuild without further retries.

    Requirements: 01-REQ-8.E3
    """

    async def test_surfaces_hub_connection_error(self) -> None:
        """poll_rebuild raises HubConnectionError without further retries."""
        client = MagicMock()
        client.get_rebuild = AsyncMock(
            side_effect=HubConnectionError(
                status_code=0,
                message="connection refused",
                error_type="connection_error",
            )
        )
        with patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(HubConnectionError):
                await poll_rebuild(
                    client, "ws1", "job-1", timeout=600.0, interval=5.0
                )


# ---------------------------------------------------------------------------
# TS-01-50: poll_clone_ready polls get_workspace until clone_status='ready'
# ---------------------------------------------------------------------------


class TestPollCloneReady:
    """TS-01-50 -- poll_clone_ready polls get_workspace until clone_status is
    'ready' and returns the Workspace.

    Requirements: 01-REQ-8.2
    """

    async def test_returns_workspace_when_clone_ready(self) -> None:
        """poll_clone_ready returns Workspace when clone_status is 'ready'."""
        pending_ws = Workspace(
            slug="ws1",
            git_url="https://git.example.com/repo.git",
            workspace_mode="carry",
            status="active",
            clone_status="pending",
            sync_status="ok",
        )
        ready_ws = Workspace(
            slug="ws1",
            git_url="https://git.example.com/repo.git",
            workspace_mode="carry",
            status="active",
            clone_status="ready",
            sync_status="ok",
        )
        client = MagicMock()
        client.get_workspace = AsyncMock(side_effect=[pending_ws, ready_ws])
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await poll_clone_ready(
                client, "ws1", timeout=300.0, interval=5.0
            )
            assert result.clone_status == "ready"
            mock_sleep.assert_called_with(5.0)

    async def test_sleeps_between_polls(self) -> None:
        """poll_clone_ready calls asyncio.sleep(interval) between polls."""
        pending_ws = Workspace(
            slug="ws1",
            git_url="https://git.example.com/repo.git",
            workspace_mode="carry",
            status="active",
            clone_status="pending",
            sync_status="ok",
        )
        ready_ws = Workspace(
            slug="ws1",
            git_url="https://git.example.com/repo.git",
            workspace_mode="carry",
            status="active",
            clone_status="ready",
            sync_status="ok",
        )
        client = MagicMock()
        client.get_workspace = AsyncMock(
            side_effect=[pending_ws, pending_ws, ready_ws]
        )
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await poll_clone_ready(
                client, "ws1", timeout=300.0, interval=5.0
            )
            assert mock_sleep.call_count == 2


# ---------------------------------------------------------------------------
# TS-01-51: poll_clone_ready raises HubError on clone_status='failed'
# ---------------------------------------------------------------------------


class TestPollCloneReadyFailed:
    """TS-01-51 -- poll_clone_ready raises HubError with status_code=0,
    error_type='clone_failed', and message from clone_error when
    clone_status is 'failed'.

    Requirements: 01-REQ-8.3
    """

    async def test_raises_hub_error_with_clone_error_message(self) -> None:
        """poll_clone_ready raises HubError with message='disk full' from
        clone_error when clone_status is 'failed'.
        """
        failed_ws = Workspace(
            slug="ws1",
            git_url="https://git.example.com/repo.git",
            workspace_mode="carry",
            status="active",
            clone_status="failed",
            sync_status="ok",
            clone_error="disk full",
        )
        client = MagicMock()
        client.get_workspace = AsyncMock(return_value=failed_ws)
        with patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(HubError) as exc_info:
                await poll_clone_ready(client, "ws1")
            assert exc_info.value.status_code == 0
            assert exc_info.value.error_type == "clone_failed"
            assert exc_info.value.message == "disk full"

    async def test_raises_hub_error_with_default_message_when_no_clone_error(self) -> None:
        """poll_clone_ready raises HubError with default message when
        clone_error is absent.
        """
        failed_ws = Workspace(
            slug="ws1",
            git_url="https://git.example.com/repo.git",
            workspace_mode="carry",
            status="active",
            clone_status="failed",
            sync_status="ok",
        )
        client = MagicMock()
        client.get_workspace = AsyncMock(return_value=failed_ws)
        with patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(HubError) as exc_info:
                await poll_clone_ready(client, "ws1")
            assert exc_info.value.status_code == 0
            assert exc_info.value.error_type == "clone_failed"
            assert exc_info.value.message == "Workspace clone failed"

    async def test_clone_failed_after_pending_poll(self) -> None:
        """poll_clone_ready raises HubError when clone_status transitions
        from 'pending' to 'failed' (01-PATH-5).
        """
        pending_ws = Workspace(
            slug="ws1",
            git_url="https://git.example.com/repo.git",
            workspace_mode="carry",
            status="active",
            clone_status="pending",
            sync_status="ok",
        )
        failed_ws = Workspace(
            slug="ws1",
            git_url="https://git.example.com/repo.git",
            workspace_mode="carry",
            status="active",
            clone_status="failed",
            sync_status="ok",
            clone_error="disk full",
        )
        client = MagicMock()
        client.get_workspace = AsyncMock(side_effect=[pending_ws, failed_ws])
        with patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(HubError) as exc_info:
                await poll_clone_ready(client, "ws1", timeout=300.0, interval=5.0)
            assert exc_info.value.error_type == "clone_failed"
            assert exc_info.value.message == "disk full"


# ---------------------------------------------------------------------------
# 01-REQ-8.E2: poll_clone_ready raises TimeoutError on timeout
# ---------------------------------------------------------------------------


class TestPollCloneReadyTimeout:
    """01-REQ-8.E2 -- poll_clone_ready raises TimeoutError when the timeout
    elapses before clone_status reaches 'ready' or 'failed'.

    Requirements: 01-REQ-8.E2
    """

    async def test_raises_timeout_error_when_still_pending(self) -> None:
        """poll_clone_ready raises TimeoutError when timeout elapses."""
        pending_ws = Workspace(
            slug="ws1",
            git_url="https://git.example.com/repo.git",
            workspace_mode="carry",
            status="active",
            clone_status="pending",
            sync_status="ok",
        )
        client = MagicMock()
        client.get_workspace = AsyncMock(return_value=pending_ws)
        with patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(TimeoutError):
                await poll_clone_ready(
                    client, "ws1", timeout=0.0, interval=5.0
                )


# ---------------------------------------------------------------------------
# 01-REQ-8.E4: poll_clone_ready surfaces HubConnectionError immediately
# ---------------------------------------------------------------------------


class TestPollCloneReadyConnectionError:
    """01-REQ-8.E4 -- poll_clone_ready aborts immediately and surfaces
    HubConnectionError from get_workspace without further retries.

    Requirements: 01-REQ-8.E4
    """

    async def test_surfaces_hub_connection_error(self) -> None:
        """poll_clone_ready raises HubConnectionError without further retries."""
        client = MagicMock()
        client.get_workspace = AsyncMock(
            side_effect=HubConnectionError(
                status_code=0,
                message="connection refused",
                error_type="connection_error",
            )
        )
        with patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(HubConnectionError):
                await poll_clone_ready(
                    client, "ws1", timeout=300.0, interval=5.0
                )


# ---------------------------------------------------------------------------
# 01-REQ-8.E5: asyncio.sleep is patched during tests
# ---------------------------------------------------------------------------


class TestPollSleepPatching:
    """01-REQ-8.E5 -- Both polling helpers use asyncio.sleep in a way that is
    patchable via unittest.mock.AsyncMock so no real delays occur during tests.

    Requirements: 01-REQ-8.E5
    """

    async def test_poll_rebuild_sleep_call_count_verifiable(self) -> None:
        """asyncio.sleep call count is verifiable during poll_rebuild."""
        non_terminal = RebuildJob(
            id="job-1", status="running", created_at="2026-01-01T00:00:00Z"
        )
        terminal = RebuildJob(
            id="job-1", status="completed", created_at="2026-01-01T00:00:00Z"
        )
        client = MagicMock()
        client.get_rebuild = AsyncMock(side_effect=[non_terminal, terminal])
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await poll_rebuild(
                client, "ws1", "job-1", timeout=600.0, interval=5.0
            )
            assert mock_sleep.call_count == 1
            mock_sleep.assert_called_with(5.0)

    async def test_poll_clone_ready_sleep_call_count_verifiable(self) -> None:
        """asyncio.sleep call count is verifiable during poll_clone_ready."""
        pending_ws = Workspace(
            slug="ws1",
            git_url="https://git.example.com/repo.git",
            workspace_mode="carry",
            status="active",
            clone_status="pending",
            sync_status="ok",
        )
        ready_ws = Workspace(
            slug="ws1",
            git_url="https://git.example.com/repo.git",
            workspace_mode="carry",
            status="active",
            clone_status="ready",
            sync_status="ok",
        )
        client = MagicMock()
        client.get_workspace = AsyncMock(side_effect=[pending_ws, ready_ws])
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await poll_clone_ready(
                client, "ws1", timeout=300.0, interval=5.0
            )
            assert mock_sleep.call_count == 1
            mock_sleep.assert_called_with(5.0)
