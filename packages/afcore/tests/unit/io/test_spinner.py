"""Unit tests for afcore.io spinner unification.

Verifies that the legacy PlanSpinner has been removed and that
StatusSpinner is no longer part of the public API (deleted in #99).

Test Spec: TS-03-46
Requirements: 03-REQ-8.6
"""

from __future__ import annotations


class TestStatusSpinnerUnification:
    """TS-03-46: Legacy PlanSpinner is removed."""

    def test_plan_spinner_removed(self) -> None:
        """03-REQ-8.6: PlanSpinner no longer exists in progress module."""
        import afcore.ui.progress as progress_module

        assert not hasattr(progress_module, "PlanSpinner"), (
            "PlanSpinner should have been removed — it was dead code with no production caller"
        )

    def test_status_spinner_removed_from_public_api(self) -> None:
        """StatusSpinner is no longer re-exported from afcore.io (#99)."""
        import afcore.io as io_module

        assert "StatusSpinner" not in getattr(io_module, "__all__", []), (
            "StatusSpinner should not be in afcore.io.__all__ — module was deleted in #99"
        )
