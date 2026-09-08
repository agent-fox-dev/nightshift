"""CLI group and common options for AgentFoxGroup.

Requirements: 03-REQ-3, 03-REQ-9, 03-REQ-15
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

import click

logger = logging.getLogger(__name__)


class _UniqueParamList(list):
    """List subclass that prevents duplicate Click params by name.

    When a param whose ``name`` already exists in the list is appended,
    the append is silently skipped and a debug-level warning is logged.
    This prevents name collisions when ``common_options`` is the
    innermost decorator and a subsequent ``@click.option`` adds a
    conflicting flag.

    Requirements: 03-REQ-9.3
    """

    def append(self, param: Any) -> None:  # noqa: ANN401
        name = getattr(param, "name", None)
        if name:
            for existing in self:
                if getattr(existing, "name", None) == name:
                    logger.debug(
                        "Skipping --%s: name collision with existing flag",
                        name,
                    )
                    return
        super().append(param)


def common_options(fn: Any) -> Any:
    """Add --verbose and --quiet flags.

    Must be applied to a Click Group (the root group), not a subcommand.
    Raises ``TypeError`` if applied to a non-Group ``click.Command``.

    Detects name collisions with existing flags on the group and skips
    conflicting registrations, emitting a debug-level warning.

    Requirements: 03-REQ-3.6, 03-REQ-9.1, 03-REQ-9.2, 03-REQ-9.3
    """
    if isinstance(fn, click.Command) and not isinstance(fn, click.Group):
        raise TypeError("common_options must be applied to the root Click group, not to a subcommand")

    # Collect existing param names from both Click Command/Group .params
    # and raw-function __click_params__ (set by earlier decorators).
    existing_names: set[str] = set()
    if hasattr(fn, "params"):
        existing_names = {p.name for p in fn.params if p.name}
    if hasattr(fn, "__click_params__"):
        existing_names |= {p.name for p in fn.__click_params__ if p.name}

    def _quiet_verbose_callback(ctx, param, value):
        ctx.ensure_object(dict)
        if value is not None and value is not False:
            ctx.obj["_quiet_explicit"] = True
        return value

    if "quiet" not in existing_names:
        fn = click.option(
            "--quiet",
            "-q",
            is_flag=True,
            default=False,
            help="Suppress info messages",
            callback=_quiet_verbose_callback,
            expose_value=True,
            is_eager=False,
        )(fn)
    else:
        logger.debug("Skipping --quiet: name collision with existing flag")

    if "verbose" not in existing_names:
        fn = click.option(
            "--verbose",
            "-v",
            is_flag=True,
            default=False,
            help="Enable debug logging",
            callback=_quiet_verbose_callback,
            expose_value=True,
            is_eager=False,
        )(fn)
    else:
        logger.debug("Skipping --verbose: name collision with existing flag")

    # Replace __click_params__ with a dedup-aware list.  When
    # common_options is the innermost decorator, subsequent
    # @click.option decorators append to this list.  The custom list
    # silently skips params whose name already exists and logs a
    # debug warning, preventing duplicate flag registration.
    if hasattr(fn, "__click_params__"):
        fn.__click_params__ = _UniqueParamList(fn.__click_params__)

    return fn


class _CliKeyboardInterrupt(KeyboardInterrupt, Exception):
    """KeyboardInterrupt that is also an Exception subclass."""


class AgentFoxGroup(click.Group):
    """Custom Click group with agent-mode detection and unified error routing."""

    def _resolve_flags(self, ctx):
        obj = ctx.obj if isinstance(ctx.obj, dict) else {}
        af_agent = os.environ.get("AF_AGENT") == "1"

        quiet_explicit = obj.get("_quiet_explicit", False)
        if quiet_explicit:
            verbose_val = ctx.params.get("verbose", False)
            quiet_val = ctx.params.get("quiet", False)
            if verbose_val:
                quiet = False
            else:
                quiet = quiet_val or False
        elif af_agent:
            quiet = True
        else:
            quiet = ctx.params.get("quiet", False) or False

        verbose = ctx.params.get("verbose", False) or False

        return {
            "json_mode": False,
            "quiet": bool(quiet),
            "verbose": bool(verbose),
            "agent_mode": af_agent,
        }

    def invoke(self, ctx):
        from afcore.io.errors import cli_error_handler
        from afcore.io.output import OutputManager

        try:
            ctx.ensure_object(dict)
        except Exception:
            pass

        if not isinstance(ctx.obj, dict):
            logger.debug(
                "ctx.obj is a non-dict value (%s); falling back to defaults",
                type(ctx.obj).__name__,
            )
            ctx.obj = {}

        flags = self._resolve_flags(ctx)
        ctx.obj["agent_mode"] = flags["agent_mode"]

        om = OutputManager(
            json_mode=flags["json_mode"],
            quiet=flags["quiet"],
            verbose=flags["verbose"],
        )
        ctx.obj["output"] = om

        try:
            from afcore.core.logging import setup_logging

            setup_logging(verbose=flags["verbose"], quiet=flags["quiet"])
        except ImportError:
            pass

        try:
            super().invoke(ctx)
        except KeyboardInterrupt:
            self._pending_keyboard_interrupt = True
            raise
        except SystemExit:
            raise
        except click.exceptions.Exit:
            raise
        except click.ClickException as exc:
            if flags.get("agent_mode"):
                from afcore.io.json import emit as _emit

                _emit({"ok": False, "error": exc.format_message()})
                sys.exit(exc.exit_code)
            raise
        except Exception as exc:
            if flags.get("agent_mode"):
                from afcore.io.json import emit_error as _emit_error

                _emit_error(exc)
                sys.exit(1)
            cli_error_handler(ctx, exc)
            sys.exit(1)

        # 03-REQ-2.4: Detect if a group callback changed ctx.obj to a
        # non-dict value during invocation. This can happen when a parent
        # group callback overwrites ctx.obj after AgentFoxGroup has
        # already constructed and stored the OutputManager.
        # Use logging.debug() (root logger) because setup_logging may
        # have set the 'afcore' logger to WARNING, which would filter
        # this DEBUG diagnostic from the module logger.
        if not isinstance(ctx.obj, dict):
            logging.debug(
                "ctx.obj was changed to a non-dict value (%s) during invocation",
                type(ctx.obj).__name__,
            )

    def main(self, *args, **kwargs):
        self._pending_keyboard_interrupt = False
        try:
            return super().main(*args, **kwargs)
        except SystemExit:
            if getattr(self, "_pending_keyboard_interrupt", False):
                raise _CliKeyboardInterrupt() from None
            raise
        except KeyboardInterrupt:
            raise
