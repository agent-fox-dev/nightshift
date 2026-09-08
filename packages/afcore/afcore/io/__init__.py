"""Shared terminal IO module for agent-fox CLIs.

Re-exports curated public symbols for CLI output,
error formatting, progress display, and CLI group configuration.

``handle_cli_errors`` is intentionally NOT re-exported here;
import it directly from ``afcore.io.errors`` if needed.

Requirements: 03-REQ-1
"""

from __future__ import annotations

from afcore.io.cli import AgentFoxGroup, common_options
from afcore.io.errors import error_envelope
from afcore.io.json import emit, emit_error, emit_line, emit_ok, read_stdin
from afcore.io.output import OutputManager, format_table, get_output_manager
from afcore.io.progress import ProgressDisplay

__all__ = [
    "AgentFoxGroup",
    "OutputManager",
    "ProgressDisplay",
    "common_options",
    "emit",
    "emit_error",
    "emit_line",
    "emit_ok",
    "error_envelope",
    "format_table",
    "get_output_manager",
    "read_stdin",
]
