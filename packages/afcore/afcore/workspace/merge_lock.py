"""File-based merge lock for serializing develop-branch operations.

Provides ``MergeLock``, an async context manager that serializes merge
operations across asyncio tasks (via ``asyncio.Lock``) and OS processes
(via atomic lock file creation with ``O_CREAT | O_EXCL``).

A background heartbeat task keeps the lock file's mtime fresh so that a
live holder is never falsely treated as stale by a competing waiter.

Requirements: 45-REQ-1.1 through 45-REQ-1.E3,
              45-REQ-2.1, 45-REQ-2.2, 45-REQ-2.E1
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import time
from datetime import UTC, datetime
from pathlib import Path

from afcore.core.errors import IntegrationError

logger = logging.getLogger(__name__)

# Merge-agent sessions can take up to one hour.  The stale timeout must be
# comfortably larger than that so a live holder is never broken mid-session.
_DEFAULT_STALE_TIMEOUT: float = 3600.0

_async_locks: dict[str, asyncio.Lock] = {}


def _get_async_lock(repo_root: Path) -> asyncio.Lock:
    """Return a shared asyncio.Lock for *repo_root*.

    All MergeLock instances that operate on the same repo share one
    asyncio lock so that in-process callers are serialized even when
    each call site constructs a fresh MergeLock.
    """
    key = os.path.realpath(str(repo_root))
    if key not in _async_locks:
        _async_locks[key] = asyncio.Lock()
    return _async_locks[key]


def _is_pid_alive(pid: int) -> bool:
    """Check whether a process with the given PID is alive."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OverflowError, OSError):
        return False
    return True


def _read_lock_owner(path: Path) -> tuple[int | None, str | None]:
    """Read PID and hostname from a merge lock file."""
    try:
        data = json.loads(path.read_text())
        return data.get("pid"), data.get("hostname")
    except (OSError, json.JSONDecodeError, ValueError):
        return None, None


class MergeLock:
    """File-based merge lock for serializing develop-branch operations.

    Works across asyncio tasks (via asyncio.Lock) and OS processes
    (via lock file with atomic creation).

    A heartbeat task updates the lock file's mtime every
    ``stale_timeout / 2`` seconds while the lock is held, preventing a
    competing waiter from treating a live holder as stale.
    """

    def __init__(
        self,
        repo_root: Path,
        timeout: float = 3600.0,
        stale_timeout: float = _DEFAULT_STALE_TIMEOUT,
        poll_interval: float = 1.0,
    ) -> None:
        self._repo_root = repo_root
        self._timeout = timeout
        self._stale_timeout = stale_timeout
        self._poll_interval = poll_interval
        self._async_lock = _get_async_lock(repo_root)
        self._lock_dir = repo_root / ".nightshift"
        self._lock_file = self._lock_dir / "merge.lock"
        self._heartbeat_task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------
    # Public property so tests and callers can inspect the effective timeout
    # ------------------------------------------------------------------

    @property
    def stale_timeout(self) -> float:
        """Effective stale-timeout threshold in seconds."""
        return self._stale_timeout

    # ------------------------------------------------------------------
    # Acquire / Release
    # ------------------------------------------------------------------

    async def acquire(self) -> None:
        """Acquire the merge lock. Blocks until acquired or timeout.

        Raises:
            IntegrationError: If the lock cannot be acquired within timeout.
        """
        try:
            await asyncio.wait_for(
                self._async_lock.acquire(),
                timeout=self._timeout,
            )
        except TimeoutError:
            raise IntegrationError(
                f"Could not acquire merge lock within {self._timeout}s (lock timeout). Lock file: {self._lock_file}",
            )
        try:
            await self._acquire_file_lock()
        except BaseException:
            self._async_lock.release()
            raise

    async def _acquire_file_lock(self) -> None:
        """Acquire the file-based lock, handling stale detection."""
        # Ensure .nightshift/ directory exists (45-REQ-1.E2)
        self._lock_dir.mkdir(parents=True, exist_ok=True)

        deadline = time.monotonic() + self._timeout

        while True:
            # Try atomic creation
            if self._try_create_lock_file():
                self._start_heartbeat()
                return

            # Lock file exists — check if stale (45-REQ-1.E1)
            if self._try_break_stale_lock():
                # Stale lock removed, retry creation immediately
                if self._try_create_lock_file():
                    self._start_heartbeat()
                    return
                # Another process won the race (45-REQ-1.E3), fall through

            # Check timeout (45-REQ-1.3)
            if time.monotonic() >= deadline:
                raise IntegrationError(
                    f"Could not acquire merge lock within {self._timeout}s "
                    f"(lock timeout). Lock file: {self._lock_file}",
                )

            # Poll (45-REQ-1.2)
            await asyncio.sleep(self._poll_interval)

    def _try_create_lock_file(self) -> bool:
        """Attempt atomic lock file creation. Returns True if created."""
        try:
            fd = os.open(
                str(self._lock_file),
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o644,
            )
        except FileExistsError:
            return False
        except OSError:
            return False

        # Write diagnostic content
        try:
            content = json.dumps(
                {
                    "pid": os.getpid(),
                    "hostname": socket.gethostname(),
                    "acquired_at": datetime.now(UTC).isoformat(),
                }
            )
            os.write(fd, content.encode())
        finally:
            os.close(fd)

        logger.info("Acquired merge lock: %s (pid=%d)", self._lock_file, os.getpid())
        return True

    def _try_break_stale_lock(self) -> bool:
        """Check if lock is stale and remove it atomically.

        Checks staleness *in place* first (no rename) so the canonical
        path is never absent for a fresh lock.  Only renames to a temp
        path after the lock is judged stale/dead, then re-verifies to
        guard against a heartbeat refresh between the initial check and
        the rename.  Restore (if needed) uses ``os.link`` which never
        overwrites an existing file, preventing a fresh lock from being
        clobbered.

        Returns True if the stale lock was broken (or already gone).
        """
        # 1. Check lock in place — no rename, no window.
        try:
            stat = self._lock_file.stat()
        except FileNotFoundError:
            return True

        pid, hostname = _read_lock_owner(self._lock_file)

        is_dead = pid is not None and hostname == socket.gethostname() and not _is_pid_alive(pid)

        age = time.time() - stat.st_mtime
        if not is_dead and age < self._stale_timeout:
            return False

        # 2. Lock appears stale/dead — atomically claim by rename.
        tmp_path = self._lock_dir / f"merge.lock.breaking.{os.getpid()}"
        try:
            os.rename(str(self._lock_file), str(tmp_path))
        except FileNotFoundError:
            return True
        except OSError:
            return False

        # 3. Re-verify after claiming.  A heartbeat may have refreshed
        #    the mtime, or the lock may have been released and
        #    re-acquired by a new holder between our stat and rename.
        try:
            post_stat = tmp_path.stat()
        except FileNotFoundError:
            return True

        post_pid, post_hostname = _read_lock_owner(tmp_path)
        post_is_dead = post_pid is not None and post_hostname == socket.gethostname() and not _is_pid_alive(post_pid)
        post_age = time.time() - post_stat.st_mtime

        if not post_is_dead and post_age < self._stale_timeout:
            # Not actually stale — restore using link (never overwrites).
            try:
                os.link(str(tmp_path), str(self._lock_file))
            except (FileExistsError, OSError):
                pass
            tmp_path.unlink(missing_ok=True)
            return False

        # 4. Confirmed stale — delete.
        reason = f"dead process (pid={post_pid})" if post_is_dead else f"age={post_age:.1f}s"
        logger.info(
            "Breaking stale merge lock (%s, stale_timeout=%.1fs): %s",
            reason,
            self._stale_timeout,
            self._lock_file,
        )
        tmp_path.unlink(missing_ok=True)
        return True

    async def release(self) -> None:
        """Release the merge lock by removing the lock file.

        Only unlinks the lock file if it is still owned by this process
        (matching PID).  After a stale-break, the original holder's
        ``release()`` must not delete the new holder's lock file.

        If the lock file has already been removed (e.g., broken by another
        process as stale), logs a warning and continues without error.
        """
        await self._stop_heartbeat()

        try:
            pid, _ = _read_lock_owner(self._lock_file)
            if pid is not None and pid != os.getpid():
                logger.warning(
                    "Not releasing merge lock: owned by pid %d, we are pid %d: %s",
                    pid,
                    os.getpid(),
                    self._lock_file,
                )
            else:
                self._lock_file.unlink()
                logger.info("Released merge lock: %s", self._lock_file)
        except FileNotFoundError:
            # 45-REQ-2.E1: already removed
            logger.warning(
                "Merge lock file already removed on release: %s",
                self._lock_file,
            )
        finally:
            self._async_lock.release()

    # ------------------------------------------------------------------
    # Heartbeat: keep mtime fresh while the lock is held
    # ------------------------------------------------------------------

    def _start_heartbeat(self) -> None:
        """Start the background heartbeat task."""
        self._heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(),
            name="merge-lock-heartbeat",
        )

    async def _stop_heartbeat(self) -> None:
        """Cancel and await the heartbeat task."""
        if self._heartbeat_task is None:
            return
        self._heartbeat_task.cancel()
        try:
            await self._heartbeat_task
        except asyncio.CancelledError:
            pass
        self._heartbeat_task = None

    async def _heartbeat_loop(self) -> None:
        """Periodically touch the lock file to keep its mtime current.

        Fires every ``stale_timeout / 2`` seconds.  If the lock file
        disappears (broken externally), the loop exits silently — the
        release() path will log the warning.
        """
        interval = self._stale_timeout / 2
        try:
            while True:
                await asyncio.sleep(interval)
                try:
                    os.utime(str(self._lock_file), None)
                    logger.debug(
                        "Merge lock heartbeat: refreshed mtime on %s",
                        self._lock_file,
                    )
                except OSError:
                    # Lock file gone — stop heartbeating quietly
                    logger.debug(
                        "Merge lock heartbeat: lock file gone, stopping: %s",
                        self._lock_file,
                    )
                    return
        except asyncio.CancelledError:
            pass

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> MergeLock:
        await self.acquire()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.release()


def cleanup_stale_merge_lock(repo_root: Path) -> bool:
    """Remove merge.lock if it was left by a dead process.

    Intended for CLI startup to eagerly clear locks left by crashed
    processes, without waiting for the stale timeout.

    Returns True if a stale lock was cleaned up.
    """
    lock_file = repo_root / ".nightshift" / "merge.lock"
    if not lock_file.exists():
        return False

    pid, hostname = _read_lock_owner(lock_file)
    if pid is None:
        return False

    if hostname != socket.gethostname():
        return False

    if _is_pid_alive(pid):
        return False

    try:
        lock_file.unlink()
    except FileNotFoundError:
        pass

    logger.info(
        "Cleaned up stale merge lock left by dead process (pid=%d) at startup: %s",
        pid,
        lock_file,
    )
    return True
