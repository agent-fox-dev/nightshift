"""Unit tests for the afcore.io package public API.

Verifies that the package re-exports the curated public symbols
specified by Spec 03, that internal symbols are not exposed,
and that the package structure contains the required files.

Spec 04 later extended the package with additional symbols
(format_table, ProgressDisplay) and files (progress.py).
Issue #99 removed StatusSpinner (dead code with no production
caller) and its module spinner.py.

See docs/errata/03_io_package_extended_by_spec_04.md.

Test Spec: TS-03-1, TS-03-2, TS-03-3, TS-03-E1
Requirements: 03-REQ-1.1, 03-REQ-1.2, 03-REQ-1.3, 03-REQ-1.E1
"""

from __future__ import annotations

import os

import pytest

# The original twelve Spec 03 symbols, minus StatusSpinner which was
# removed in #99 (dead code — no production caller).
SPEC_03_PUBLIC_SYMBOLS = [
    "OutputManager",
    "get_output_manager",
    "emit",
    "emit_ok",
    "emit_line",
    "emit_error",
    "read_stdin",
    "error_envelope",
    "AgentFoxGroup",
    "common_options",
    "exit_codes",
]

# Symbols removed from the public API (documented removals).
REMOVED_SYMBOLS = [
    "StatusSpinner",  # removed in #99 — dead code, no production caller
]

# Additional symbols added by Spec 04, documented in errata.
SPEC_04_EXTRA_SYMBOLS = [
    "ProgressDisplay",
    "format_table",
]


class TestPublicAPI:
    """TS-03-1: Verify all Spec 03 public symbols are importable from afcore.io."""

    def test_all_spec03_symbols_importable(self) -> None:
        """03-REQ-1.1: All surviving Spec 03 symbols are importable from afcore.io."""
        import afcore.io

        for sym in SPEC_03_PUBLIC_SYMBOLS:
            assert hasattr(afcore.io, sym), f"{sym} not found in afcore.io"

    def test_removed_symbols_absent(self) -> None:
        """Symbols removed in #99 are no longer in the public API."""
        import afcore.io

        for sym in REMOVED_SYMBOLS:
            assert sym not in afcore.io.__all__, f"{sym} should have been removed from afcore.io"

    def test_spec03_symbols_present(self) -> None:
        """03-REQ-1.1: Validate that all surviving Spec 03 symbols are present.

        The package may contain additional symbols added by later specs
        (documented in errata), but the surviving symbols must all be present
        and any extras must be from the known extension sets.
        """
        import afcore.io

        actual_public = set(afcore.io.__all__)
        spec_03_expected = set(SPEC_03_PUBLIC_SYMBOLS)
        spec_04_known = set(SPEC_04_EXTRA_SYMBOLS)

        # All surviving symbols must be present.
        missing = spec_03_expected - actual_public
        assert missing == set(), f"Missing Spec 03 symbols: {missing}"

        # Any extra symbols must be from the documented Spec 04 set.
        extras = actual_public - spec_03_expected
        undocumented = extras - spec_04_known
        assert undocumented == set(), (
            f"Undocumented extra symbols beyond Spec 03 and Spec 04 extensions: {undocumented}"
        )

    def test_handle_cli_errors_not_in_public_api(self) -> None:
        """03-REQ-1.1: handle_cli_errors is not among the public symbols."""
        import afcore.io

        assert "handle_cli_errors" not in afcore.io.__all__


class TestHandleCliErrorsExclusion:
    """TS-03-2: Verify handle_cli_errors is NOT importable from afcore.io."""

    def test_handle_cli_errors_not_in_package(self) -> None:
        """03-REQ-1.2: from afcore.io import handle_cli_errors raises ImportError."""
        with pytest.raises(ImportError):
            from afcore.io import handle_cli_errors  # noqa: F401

    def test_handle_cli_errors_importable_from_submodule(self) -> None:
        """03-REQ-1.2: from afcore.io.errors import handle_cli_errors succeeds."""
        from afcore.io.errors import handle_cli_errors

        assert callable(handle_cli_errors)


class TestPackageStructure:
    """TS-03-3: Verify the afcore/io/ directory contains the required files."""

    def test_spec03_files_exist(self) -> None:
        """03-REQ-1.3: Required Spec 03 files exist in afcore/io/.

        spinner.py was removed in #99 (dead code). Spec 04 added progress.py.
        """
        import afcore.io

        io_dir = os.path.dirname(afcore.io.__file__)
        files = set(os.listdir(io_dir))

        # The surviving Spec 03 files (spinner.py removed in #99).
        spec_03_files = {
            "__init__.py",
            "output.py",
            "json.py",
            "errors.py",
            "cli.py",
            "help.py",
        }

        # Known additions by Spec 04.
        spec_04_extra_files = {
            "progress.py",
        }

        # All surviving files must be present.
        missing = spec_03_files - files
        assert missing == set(), f"Missing required files: {missing}"

        # spinner.py should be gone.
        assert "spinner.py" not in files, "spinner.py should have been removed in #99"

        # Any extra .py files must be from the documented extension set.
        all_py_files = {f for f in files if f.endswith(".py")}
        extras = all_py_files - spec_03_files
        undocumented = extras - spec_04_extra_files
        assert undocumented == set(), f"Undocumented extra .py files beyond expected set: {undocumented}"


class TestSubmoduleInternalSymbol:
    """TS-03-E1: Importing submodule-internal symbol from afcore.io raises ImportError."""

    def test_handle_cli_errors_raises_import_error(self) -> None:
        """03-REQ-1.E1: ImportError raised for unlisted symbol from afcore.io."""
        with pytest.raises(ImportError):
            from afcore.io import handle_cli_errors  # noqa: F401
