"""Mechanical verification gate for the fix pipeline.

Runs a configurable check command (e.g. ``make check``) in the worktree
after each coder session and reads the exit code.  Integration requires
the gate to pass — a reviewer PASS verdict alone is not sufficient.

When no gate command is configured, the gate is a no-op and the pipeline
behaves exactly as before.

Requirements: NS-REQ-1 (issue #35)
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Maximum bytes of combined stdout+stderr to capture.  Output beyond
# this limit is truncated with an ellipsis marker so the prompt stays
# within reasonable token budgets.
_MAX_OUTPUT_BYTES: int = 16_384


@dataclass(frozen=True)
class GateResult:
    """Result of running the verification gate command.

    Attributes:
        passed: True when the command exited with code 0.
        exit_code: The process exit code (-1 when the command timed out).
        output: Captured combined stdout+stderr, truncated to
            ``_MAX_OUTPUT_BYTES``.
        command: The command string that was executed.
    """

    passed: bool
    exit_code: int
    output: str
    command: str


async def run_gate(
    command: str,
    worktree_path: Path,
    *,
    timeout: int = 600,
) -> GateResult:
    """Run the gate command in *worktree_path* and return the result.

    The command is executed via ``/bin/sh -c`` so shell features (pipes,
    ``&&``, etc.) work as expected.  stdout and stderr are merged into a
    single stream and truncated to ``_MAX_OUTPUT_BYTES``.

    On timeout the process is killed and a ``GateResult`` with
    ``exit_code=-1`` and ``passed=False`` is returned.

    Args:
        command: Shell command string to execute.
        worktree_path: Working directory for the subprocess.
        timeout: Maximum seconds to wait before killing the process.

    Returns:
        A :class:`GateResult` describing the outcome.
    """
    logger.info("Running gate command in %s: %s", worktree_path, command)

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=str(worktree_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

        try:
            stdout, _ = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout,
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            logger.warning(
                "Gate command timed out after %ds in %s",
                timeout,
                worktree_path,
            )
            return GateResult(
                passed=False,
                exit_code=-1,
                output=f"[gate command timed out after {timeout}s]",
                command=command,
            )

        raw_output = (stdout or b"").decode("utf-8", errors="replace")

        # Truncate to keep prompt injection bounded.
        if len(raw_output) > _MAX_OUTPUT_BYTES:
            raw_output = raw_output[:_MAX_OUTPUT_BYTES] + "\n… [output truncated]"

        exit_code = proc.returncode or 0
        passed = exit_code == 0

        if passed:
            logger.info("Gate command passed (exit code 0) in %s", worktree_path)
        else:
            logger.warning(
                "Gate command failed (exit code %d) in %s",
                exit_code,
                worktree_path,
            )

        return GateResult(
            passed=passed,
            exit_code=exit_code,
            output=raw_output,
            command=command,
        )

    except Exception as exc:
        logger.error(
            "Gate command raised an exception in %s: %s",
            worktree_path,
            exc,
        )
        return GateResult(
            passed=False,
            exit_code=-1,
            output=f"[gate command failed to execute: {exc}]",
            command=command,
        )


def format_gate_feedback(gate_result: GateResult) -> str:
    """Format a failed gate result as context for the next coder attempt.

    Returns a markdown section that can be prepended to the coder's task
    prompt so it knows what went wrong in the previous attempt's build.
    """
    lines = [
        "## Previous Gate Output",
        "",
        f"The verification command `{gate_result.command}` failed with exit code {gate_result.exit_code}.",
        "",
        "```",
        gate_result.output.rstrip(),
        "```",
        "",
        "Fix the issues reported above before proceeding.",
        "",
    ]
    return "\n".join(lines)


def log_ungated_warning() -> None:
    """Log a single WARNING that the daemon is running without a gate.

    Called once at startup when no ``[gate] command`` is configured.
    """
    logger.warning(
        "No gate command configured — the fix pipeline will rely solely "
        "on the reviewer model's verdict to decide whether to merge. "
        "Configure [gate] command in config.toml to add a mechanical "
        "verification step (e.g. 'make check')."
    )
