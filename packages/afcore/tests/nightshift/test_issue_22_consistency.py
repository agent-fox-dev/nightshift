"""Tests for issue #22: triage/coder prompt and schema consistency.

Verifies that assessed_complexity is described consistently as a complexity
hint (not a model-tier recommendation), that escalation wording is removed,
and that TaskEvent has no escalated_from/escalated_to fields.

Requirements: NS-REQ-1 through NS-REQ-5
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

# Resolve repo root: test file is at packages/afcore/tests/nightshift/test_*.py
# parents: [0]=nightshift, [1]=tests, [2]=afcore, [3]=packages, [4]=repo root
_REPO_ROOT = Path(__file__).resolve().parents[4]
_PKG_ROOT = _REPO_ROOT / "packages" / "afcore"


# ---------------------------------------------------------------------------
# TS-NS-1: Triage profile and task prompt agree on assessed_complexity schema
# Requirement: NS-REQ-1
# ---------------------------------------------------------------------------


class TestTriageSchemaAgreement:
    """Profile and task prompt both describe assessed_complexity with
    tier, confidence, rationale — as a complexity hint, not a model
    recommendation.
    """

    def test_profile_documents_assessed_complexity(self) -> None:
        """maintainer_fix-triage.md includes assessed_complexity in its schema."""
        profile = _PKG_ROOT / "afcore" / "_templates" / "profiles" / "maintainer_fix-triage.md"
        text = profile.read_text()
        assert "assessed_complexity" in text
        # All three fields must be mentioned
        assert '"tier"' in text
        assert '"confidence"' in text
        assert '"rationale"' in text

    def test_task_prompt_does_not_claim_model_recommendation(self) -> None:
        """Triage task prompt does not say 'recommend the model tier'."""
        src = _PKG_ROOT / "afcore" / "nightshift" / "fix_pipeline.py"
        text = src.read_text()
        assert "recommend the model tier" not in text


# ---------------------------------------------------------------------------
# TS-NS-2: No prompt text claims assessed_complexity drives model selection
# Requirement: NS-REQ-2
# ---------------------------------------------------------------------------


class TestNoModelSelectionClaims:
    """AssessedComplexity docstring and task prompt do not reference
    bypassing Haiku or model override.
    """

    def test_no_haiku_bypass_reference(self) -> None:
        """AssessedComplexity docstring does not mention 'bypasses the Haiku'."""
        src = _PKG_ROOT / "afcore" / "nightshift" / "fix_pipeline.py"
        text = src.read_text()
        assert "bypasses the Haiku" not in text

    def test_no_model_override_in_coder_session_docstring(self) -> None:
        """_run_coder_session docstring does not claim 'model override'."""
        src = _PKG_ROOT / "afcore" / "nightshift" / "fix_pipeline.py"
        text = src.read_text()
        # The phrase "model override for escalation" should be gone
        assert "model override" not in text


# ---------------------------------------------------------------------------
# TS-NS-3: TaskEvent has no escalated_from/escalated_to fields
# Requirement: NS-REQ-3
# ---------------------------------------------------------------------------


class TestNoEscalationFields:
    """TaskEvent and its renderer have no escalation machinery."""

    def test_task_event_has_no_escalated_fields(self) -> None:
        """TaskEvent dataclass has no escalated_from or escalated_to."""
        from afcore.ui.progress import TaskEvent

        field_names = {f.name for f in dataclasses.fields(TaskEvent)}
        assert "escalated_from" not in field_names
        assert "escalated_to" not in field_names

    def test_progress_source_has_no_escalated_references(self) -> None:
        """progress.py source contains no 'escalated_from' or 'escalated_to'."""
        src = _PKG_ROOT / "afcore" / "ui" / "progress.py"
        text = src.read_text()
        assert "escalated_from" not in text
        assert "escalated_to" not in text


# ---------------------------------------------------------------------------
# TS-NS-4: No escalation/ladder wording in backend comments
# Requirement: NS-REQ-4
# ---------------------------------------------------------------------------


class TestNoEscalationWording:
    """Backend comments use 'retry' not 'escalation ladder'."""

    def test_backends_no_escalation_ladder(self) -> None:
        """session/backends/ has no 'escalation ladder' or 'escalation retry'."""
        backends_dir = _PKG_ROOT / "afcore" / "session" / "backends"
        for py_file in backends_dir.glob("*.py"):
            text = py_file.read_text()
            # "privilege escalation" in security keyword lists is fine;
            # we check for the specific phrases about the ladder.
            assert "escalation ladder" not in text, f"{py_file.name} still references 'escalation ladder'"
            assert "escalation retry" not in text, f"{py_file.name} still references 'escalation retry'"

    def test_fix_pipeline_no_escalation_behaviour_claims(self) -> None:
        """fix_pipeline.py has no escalation/ladder wording describing pipeline behaviour.

        Note: 'escalat' may still appear in unrelated contexts (e.g. security
        keyword lists); this test checks the specific phrases from the issue.
        """
        src = _PKG_ROOT / "afcore" / "nightshift" / "fix_pipeline.py"
        text = src.read_text()
        # These specific phrases should be removed
        assert "escalation" not in text.lower(), "fix_pipeline.py still references 'escalation'"
        assert "Ladder" not in text, "fix_pipeline.py still references 'Ladder'"
        assert "ladder" not in text, "fix_pipeline.py still references 'ladder'"


# ---------------------------------------------------------------------------
# TS-NS-5: docs no longer document variant field
# Requirement: NS-REQ-5
# ---------------------------------------------------------------------------


class TestDocsNoVariant:
    """04-night-shift.md lists only tier, confidence, rationale."""

    def test_no_variant_in_nightshift_doc(self) -> None:
        """04-night-shift.md does not mention 'variant'."""
        doc = _REPO_ROOT / "docs" / "architecture" / "04-night-shift.md"
        text = doc.read_text()
        assert "variant" not in text

    def test_assessed_complexity_lists_three_fields(self) -> None:
        """04-night-shift.md assessed_complexity mentions tier, confidence, rationale."""
        doc = _REPO_ROOT / "docs" / "architecture" / "04-night-shift.md"
        text = doc.read_text()
        # Find the assessed_complexity bullet
        match = re.search(r"\*\*`assessed_complexity`\*\*.*?(?=\n-|\n\n|\Z)", text, re.DOTALL)
        assert match is not None, "assessed_complexity bullet not found in doc"
        bullet = match.group()
        assert "tier" in bullet
        assert "confidence" in bullet
        assert "rationale" in bullet
