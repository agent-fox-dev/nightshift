"""Tests for issue #23: fix-pipeline prompts reference tools and context
sections the pipeline never provides.

Validates:
- AC-1: grep is in both fix-triage and hunt allowlists AND the profile AND docs
- AC-2: No profile references ## Test Commands
- AC-3: Compact/SIMPLE path omits tasks subtask list reference
- AC-4: coder_fix.md instructs session summary emission (matches _post_harvest_ingest)
- AC-5: coder.md contains no .agent-fox paths
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from afcore.archetypes import ARCHETYPE_REGISTRY, resolve_effective_config
from afcore.nightshift.fix_pipeline import (
    AcceptanceCriterion,
    AssessedComplexity,
    FixPipeline,
    TriageResult,
)
from afcore.nightshift.spec_builder import InMemorySpec

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROFILES_DIR = Path(__file__).resolve().parents[2] / "afcore" / "_templates" / "profiles"
DOCS_DIR = Path(__file__).resolve().parents[4] / "docs"

SUBTASK_PHRASE = "Refer to the tasks subtask list in the context above"

RENDERED_SECTIONS = (
    "## Requirements\nRequirement content\n\n## Test Specification\nTest spec content\n\n## Tasks\n- [ ] Task 1\n"
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def pipeline() -> FixPipeline:
    config = MagicMock()
    platform = MagicMock()
    return FixPipeline(config=config, platform=platform)


@pytest.fixture()
def fake_spec() -> InMemorySpec:
    return InMemorySpec(
        issue_number=42,
        title="Fix the bug",
        task_prompt=(
            "Fix the issue: Fix the bug (#42)\n\n"
            "Refer to the issue description and acceptance criteria in the context above."
        ),
        system_context="Issue body text describing the bug",
        branch_name="fix/42-fix-the-bug",
    )


def _make_criteria(count: int = 2) -> list[AcceptanceCriterion]:
    return [
        AcceptanceCriterion(
            id=f"AC-{i + 1}",
            description=f"Criterion {i + 1} description",
            preconditions=f"Precondition {i + 1}",
            expected=f"Expected {i + 1}",
            assertion=f"Assertion {i + 1}",
        )
        for i in range(count)
    ]


# ---------------------------------------------------------------------------
# AC-1: grep command consistency
# ---------------------------------------------------------------------------


class TestAC1GrepAllowlist:
    """The grep command is in both allowlists, the profile, and the doc."""

    def test_fix_triage_allowlist_contains_grep(self) -> None:
        """fix-triage mode allowlist includes grep."""
        entry = ARCHETYPE_REGISTRY["maintainer"]
        resolved = resolve_effective_config(entry, mode="fix-triage")
        assert "grep" in resolved.default_allowlist

    def test_hunt_allowlist_contains_grep(self) -> None:
        """hunt mode allowlist includes grep."""
        entry = ARCHETYPE_REGISTRY["maintainer"]
        resolved = resolve_effective_config(entry, mode="hunt")
        assert "grep" in resolved.default_allowlist

    def test_profile_mentions_grep(self) -> None:
        """maintainer_fix-triage.md mentions grep in its tool list."""
        profile = (PROFILES_DIR / "maintainer_fix-triage.md").read_text()
        assert "grep" in profile

    def test_doc_mentions_grep(self) -> None:
        """docs/architecture/04-night-shift.md mentions grep in the triage section."""
        doc = (DOCS_DIR / "architecture" / "04-night-shift.md").read_text()
        # Find the triage section and check grep is mentioned
        assert "`grep`" in doc

    def test_allowlists_match_profile(self) -> None:
        """The set of tools in the allowlist matches what the profile names."""
        profile = (PROFILES_DIR / "maintainer_fix-triage.md").read_text()
        entry = ARCHETYPE_REGISTRY["maintainer"]
        resolved = resolve_effective_config(entry, mode="fix-triage")
        allowlist = set(resolved.default_allowlist)

        # Profile lists: cat, head, tail, ls, git log/diff/show/status, wc, grep
        # The allowlist uses command prefixes: ls, cat, git, wc, head, tail, grep
        for cmd in ["cat", "head", "tail", "ls", "wc", "grep"]:
            assert cmd in allowlist, f"{cmd} in profile but not in allowlist"
            assert cmd in profile, f"{cmd} in allowlist but not in profile"


# ---------------------------------------------------------------------------
# AC-2: No ## Test Commands references
# ---------------------------------------------------------------------------


class TestAC2NoTestCommandsReference:
    """No profile references ## Test Commands."""

    def test_coder_fix_no_test_commands_reference(self) -> None:
        """coder_fix.md does not reference ## Test Commands."""
        profile = (PROFILES_DIR / "coder_fix.md").read_text()
        assert "## Test Commands" not in profile
        assert "Test Commands" not in profile

    def test_reviewer_fix_review_no_test_commands_reference(self) -> None:
        """reviewer_fix-review.md does not reference ## Test Commands."""
        profile = (PROFILES_DIR / "reviewer_fix-review.md").read_text()
        assert "## Test Commands" not in profile
        assert "Test Commands" not in profile

    def test_coder_fix_has_concrete_test_instructions(self) -> None:
        """coder_fix.md has concrete test instructions."""
        profile = (PROFILES_DIR / "coder_fix.md").read_text()
        assert "make check" in profile

    def test_reviewer_fix_review_has_concrete_test_instructions(self) -> None:
        """reviewer_fix-review.md has concrete test instructions."""
        profile = (PROFILES_DIR / "reviewer_fix-review.md").read_text()
        assert "make check" in profile


# ---------------------------------------------------------------------------
# AC-3: Compact path omits tasks subtask list reference
# ---------------------------------------------------------------------------


class TestAC3CompactPathNoSubtaskRef:
    """Compact/SIMPLE path omits the tasks subtask list reference."""

    def test_simple_tier_omits_subtask_reference(
        self,
        pipeline: FixPipeline,
        fake_spec: InMemorySpec,
    ) -> None:
        """When use_compact is True (SIMPLE tier, ≤2 criteria), no subtask reference."""
        triage = TriageResult(
            summary="Root cause analysis",
            affected_files=["afcore/engine.py"],
            criteria=_make_criteria(1),
            assessed_complexity=AssessedComplexity(
                tier="SIMPLE",
                confidence=0.9,
                rationale="Trivial fix",
            ),
        )

        with patch(
            "afcore.session.prompt.build_system_prompt",
            return_value="mock",
        ):
            _, task_prompt = pipeline._build_coder_prompt(fake_spec, triage)

        assert SUBTASK_PHRASE not in task_prompt

    def test_simple_tier_two_criteria_omits_subtask_reference(
        self,
        pipeline: FixPipeline,
        fake_spec: InMemorySpec,
    ) -> None:
        """SIMPLE tier with exactly 2 criteria still omits subtask reference."""
        triage = TriageResult(
            summary="Root cause analysis",
            affected_files=["afcore/engine.py"],
            criteria=_make_criteria(2),
            assessed_complexity=AssessedComplexity(
                tier="SIMPLE",
                confidence=0.9,
                rationale="Trivial fix",
            ),
        )

        with patch(
            "afcore.session.prompt.build_system_prompt",
            return_value="mock",
        ):
            _, task_prompt = pipeline._build_coder_prompt(fake_spec, triage)

        assert SUBTASK_PHRASE not in task_prompt

    def test_standard_tier_includes_subtask_reference(
        self,
        pipeline: FixPipeline,
        fake_spec: InMemorySpec,
    ) -> None:
        """When assessed_complexity.tier is STANDARD, subtask reference is included."""
        triage = TriageResult(
            summary="Root cause analysis",
            affected_files=["afcore/engine.py"],
            criteria=_make_criteria(3),
            assessed_complexity=AssessedComplexity(
                tier="STANDARD",
                confidence=0.8,
                rationale="Moderate fix",
            ),
        )

        with (
            patch(
                "afcore.nightshift.fix_pipeline.build_afspec_from_triage",
                return_value=MagicMock(),
                create=True,
            ),
            patch(
                "afcore.nightshift.fix_pipeline.render_inmemory_spec_sections",
                return_value=RENDERED_SECTIONS,
                create=True,
            ),
            patch(
                "afcore.session.prompt.build_system_prompt",
                return_value="mock",
            ),
        ):
            _, task_prompt = pipeline._build_coder_prompt(fake_spec, triage)

        assert SUBTASK_PHRASE in task_prompt

    def test_no_assessed_complexity_includes_subtask_reference(
        self,
        pipeline: FixPipeline,
        fake_spec: InMemorySpec,
    ) -> None:
        """When assessed_complexity is None (old triage), subtask reference is present."""
        triage = TriageResult(
            summary="Root cause analysis",
            affected_files=["afcore/engine.py"],
            criteria=_make_criteria(2),
        )

        with (
            patch(
                "afcore.nightshift.fix_pipeline.build_afspec_from_triage",
                return_value=MagicMock(),
                create=True,
            ),
            patch(
                "afcore.nightshift.fix_pipeline.render_inmemory_spec_sections",
                return_value=RENDERED_SECTIONS,
                create=True,
            ),
            patch(
                "afcore.session.prompt.build_system_prompt",
                return_value="mock",
            ),
        ):
            _, task_prompt = pipeline._build_coder_prompt(fake_spec, triage)

        assert SUBTASK_PHRASE in task_prompt

    def test_simple_with_three_criteria_includes_subtask_reference(
        self,
        pipeline: FixPipeline,
        fake_spec: InMemorySpec,
    ) -> None:
        """SIMPLE tier but >2 criteria uses full rendering with subtask reference."""
        triage = TriageResult(
            summary="Root cause analysis",
            affected_files=["afcore/engine.py"],
            criteria=_make_criteria(3),
            assessed_complexity=AssessedComplexity(
                tier="SIMPLE",
                confidence=0.7,
                rationale="Misclassified as simple",
            ),
        )

        with (
            patch(
                "afcore.nightshift.fix_pipeline.build_afspec_from_triage",
                return_value=MagicMock(),
                create=True,
            ),
            patch(
                "afcore.nightshift.fix_pipeline.render_inmemory_spec_sections",
                return_value=RENDERED_SECTIONS,
                create=True,
            ),
            patch(
                "afcore.session.prompt.build_system_prompt",
                return_value="mock",
            ),
        ):
            _, task_prompt = pipeline._build_coder_prompt(fake_spec, triage)

        assert SUBTASK_PHRASE in task_prompt


# ---------------------------------------------------------------------------
# AC-4: Session summary mechanism is consistent
# ---------------------------------------------------------------------------


class TestAC4SessionSummaryConsistency:
    """Either coder_fix.md instructs summary emission and _post_harvest_ingest
    extracts it, or both are removed."""

    def test_coder_fix_instructs_session_summary(self) -> None:
        """coder_fix.md contains Session Summary section with JSON schema."""
        profile = (PROFILES_DIR / "coder_fix.md").read_text()
        assert "## Session Summary" in profile
        assert '"summary"' in profile
        assert '"rejected_approaches"' in profile
        assert '"gotchas"' in profile
        assert '"assumptions"' in profile

    def test_coder_fix_does_not_forbid_summary(self) -> None:
        """coder_fix.md no longer says 'do not create session summary files'."""
        profile = (PROFILES_DIR / "coder_fix.md").read_text()
        assert "session summary files" not in profile.lower()

    def test_post_harvest_ingest_uses_extract_session_summary(self) -> None:
        """_post_harvest_ingest calls extract_session_summary (both sides present)."""
        import inspect

        source = inspect.getsource(FixPipeline._post_harvest_ingest)
        assert "extract_session_summary" in source

    def test_extract_session_summary_can_parse_coder_output(self) -> None:
        """extract_session_summary can parse the JSON schema documented in coder_fix.md."""
        from afcore.knowledge.extraction import extract_session_summary

        response = """Here is my session summary:

```json
{
  "summary": "Fixed the null pointer by adding a guard clause in engine.py.",
  "rejected_approaches": [{"approach": "Wrapping in try/except", "reason": "Hides the root cause"}],
  "gotchas": ["The engine.py module is imported lazily"],
  "assumptions": ["The input is always a dict"]
}
```"""
        summary, rejected, gotchas, assumptions = extract_session_summary(response)
        assert summary is not None
        assert "null pointer" in summary
        assert len(rejected) == 1
        assert len(gotchas) == 1
        assert len(assumptions) == 1


# ---------------------------------------------------------------------------
# AC-5: coder.md contains no .agent-fox references
# ---------------------------------------------------------------------------


class TestAC5NoAgentFoxPaths:
    """coder.md contains no .agent-fox paths or removed spec-pipeline artifacts."""

    def test_no_agent_fox_path(self) -> None:
        """coder.md contains no .agent-fox path."""
        profile = (PROFILES_DIR / "coder.md").read_text()
        assert ".agent-fox" not in profile
        assert "agent-fox" not in profile.lower()

    def test_no_json_spec_artifacts(self) -> None:
        """coder.md does not reference tasks.json or test_spec.json."""
        profile = (PROFILES_DIR / "coder.md").read_text()
        assert "tasks.json" not in profile
        assert "test_spec.json" not in profile
        assert "requirements.json" not in profile

    def test_session_summary_uses_nightshift_or_inline(self) -> None:
        """coder.md session summary doesn't reference .agent-fox path."""
        profile = (PROFILES_DIR / "coder.md").read_text()
        # Must not write to a file path under .agent-fox
        assert ".agent-fox/session-summary" not in profile
