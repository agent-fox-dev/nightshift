"""The CLI reports a non-recoverable API failure verbatim and exits 1."""

from __future__ import annotations

import pytest
from afcore.core.errors import FatalAPIError
from nightshift._startup import report_failure


def test_fatal_api_error_is_reported_verbatim(capsys) -> None:
    """The operator sees the reason, not a generic 'daemon failed'."""
    exc = FatalAPIError("Anthropic API credit balance is too low — add credits.")

    with pytest.raises(SystemExit) as excinfo:
        report_failure(exc)

    assert excinfo.value.code == 1
    err = capsys.readouterr().err
    assert "Error: Anthropic API credit balance is too low — add credits." in err
    assert "daemon failed" not in err


def test_other_failures_keep_the_generic_message(capsys) -> None:
    with pytest.raises(SystemExit) as excinfo:
        report_failure(RuntimeError("boom"))

    assert excinfo.value.code == 1
    assert "Error: nightshift daemon failed: boom" in capsys.readouterr().err
