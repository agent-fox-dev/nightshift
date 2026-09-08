"""Unit tests for JSON extraction helpers.

Covers:
- extract_json_array: prose-bracket disambiguation (issue #212)
- extract_json_object: prose-prefixed JSON scanning (issue #57)
"""

from __future__ import annotations

import pytest
from afcore.core.json_extraction import extract_json_array, extract_json_object

# ---------------------------------------------------------------------------
# Option B — two-pass scan: prefer dict-containing arrays over string arrays
# ---------------------------------------------------------------------------


class TestTwoPassBracketScan:
    """Option B: _scan_bracket_arrays prefers arrays of dicts over string arrays."""

    def test_prose_string_array_before_findings_returns_findings(self) -> None:
        """Findings array is returned even when a prose string array appears first."""
        text = (
            'The following requirements were reviewed: ["72-REQ-1.1", "72-REQ-2.3"]. '
            "Here is the structured output: "
            '[{"severity": "major", "description": "Missing input validation"}]'
        )
        result = extract_json_array(text)
        assert result is not None
        assert len(result) == 1
        assert isinstance(result[0], dict)
        assert result[0]["severity"] == "major"

    def test_multiple_prose_arrays_before_findings_returns_findings(self) -> None:
        """Findings returned even when multiple prose string arrays precede it."""
        text = 'Section ["A", "B"] and ["C", "D"] reference IDs. Output: [{"severity": "minor", "description": "Nit"}]'
        result = extract_json_array(text)
        assert result is not None
        assert len(result) == 1
        assert result[0]["description"] == "Nit"

    def test_findings_only_no_prose_array(self) -> None:
        """Normal case: bare findings array without preceding prose array."""
        text = '[{"severity": "critical", "description": "Security hole"}]'
        result = extract_json_array(text)
        assert result == [{"severity": "critical", "description": "Security hole"}]

    def test_multiple_findings_in_array(self) -> None:
        """Multiple finding dicts are returned correctly."""
        text = (
            'Some prose with ref ["old-req-1"] then the real output: '
            '[{"severity": "major", "description": "A"}, '
            '{"severity": "minor", "description": "B"}]'
        )
        result = extract_json_array(text)
        assert result is not None
        assert len(result) == 2
        assert result[0]["severity"] == "major"
        assert result[1]["severity"] == "minor"

    def test_no_dict_array_falls_back_to_string_array(self) -> None:
        """When no dict-containing array exists, the first string array is returned."""
        text = 'Only refs here: ["req-1", "req-2"] and nothing else.'
        result = extract_json_array(text)
        assert result == ["req-1", "req-2"]

    def test_prose_string_array_after_findings_returns_findings(self) -> None:
        """Findings array is preferred when prose string array appears after it."""
        text = '[{"severity": "major", "description": "Real finding"}] See also: ["note-1", "note-2"]'
        result = extract_json_array(text)
        assert result is not None
        assert len(result) == 1
        assert result[0]["severity"] == "major"

    def test_empty_findings_array_returned(self) -> None:
        """Empty findings array (no items) is returned as an empty list."""
        # An empty array has no dicts, so it falls through to the fallback.
        # This is backward-compatible behaviour.
        text = "The output is: []"
        result = extract_json_array(text)
        assert result == []

    def test_wrapper_object_inner_array_with_dicts_returned(self) -> None:
        """The inner findings array inside a wrapper object is extracted."""
        text = '{"findings": [{"severity": "major", "description": "Foo"}]}'
        result = extract_json_array(text)
        assert result is not None
        assert len(result) == 1
        assert result[0]["severity"] == "major"

    def test_prose_array_then_wrapper_object_returns_inner_findings(self) -> None:
        """Inner dict array from wrapper is preferred over preceding prose string array."""
        text = (
            'I reviewed requirements ["72-REQ-1.1", "72-REQ-2.3"] and found: '
            '{"findings": [{"severity": "critical", "description": "Data leak"}]}'
        )
        result = extract_json_array(text)
        assert result is not None
        assert len(result) == 1
        assert result[0]["severity"] == "critical"
        assert result[0]["description"] == "Data leak"


# ---------------------------------------------------------------------------
# Option A — unwrap single-key wrapper objects from markdown fences
# ---------------------------------------------------------------------------


class TestFenceWrapperObjectUnwrapping:
    """Option A: single-key wrapper objects inside fences are unwrapped."""

    def test_fenced_wrapper_object_unwrapped(self) -> None:
        """Fenced JSON with {findings: [...]} wrapper is unwrapped to the list."""
        text = (
            "Here is my analysis:\n"
            "```json\n"
            '{"findings": [{"severity": "major", "description": "Missing auth"}]}\n'
            "```\n"
            "Done."
        )
        result = extract_json_array(text)
        assert result is not None
        assert len(result) == 1
        assert result[0]["severity"] == "major"

    def test_fenced_wrapper_without_json_label_unwrapped(self) -> None:
        """Plain fence (no 'json' label) with wrapper object is unwrapped."""
        text = '```\n{"findings": [{"severity": "minor", "description": "Nit"}]}\n```'
        result = extract_json_array(text)
        assert result is not None
        assert len(result) == 1
        assert result[0]["description"] == "Nit"

    def test_fenced_bare_array_still_works(self) -> None:
        """Fenced bare JSON array continues to work (backward compat)."""
        text = '```json\n[{"severity": "major"}]\n```'
        result = extract_json_array(text)
        assert result == [{"severity": "major"}]

    def test_fenced_multi_key_wrapper_not_unwrapped(self) -> None:
        """Wrapper objects with more than one key are not unwrapped via Option A."""
        # Multi-key wrapper is not a valid single-key wrapper — should not be
        # unwrapped by Option A.  Strategy 1 (bracket scan) would still find
        # the inner array if reachable.
        text = '```json\n{"findings": [{"severity": "major", "description": "x"}], "count": 1}\n```'
        # Strategy 1 should find the inner [...] via bracket scan
        result = extract_json_array(text)
        assert result is not None
        assert any(isinstance(item, dict) for item in result)


# ---------------------------------------------------------------------------
# Combined scenario — prose array + wrapper object
# ---------------------------------------------------------------------------


class TestCombinedProseAndWrapper:
    """The full bug scenario: prose string arrays before a wrapper findings object."""

    def test_full_bug_scenario_prose_then_wrapper(self) -> None:
        """Reproduce the exact bug: prose refs then wrapper findings block."""
        # Mimics a skeptic response that writes requirement IDs before the JSON.
        text = (
            "I analyzed the following requirements: "
            '["72-REQ-1.1", "72-REQ-2.3", "72-REQ-4.1"]. '
            "Based on the analysis, here are my structured findings:\n"
            '{"findings": [\n'
            '  {"severity": "major", "description": "Validation missing", '
            '"requirement_ref": "72-REQ-1.1"},\n'
            '  {"severity": "minor", "description": "Doc gap", '
            '"requirement_ref": "72-REQ-2.3"}\n'
            "]}"
        )
        result = extract_json_array(text)
        assert result is not None, "Should extract findings, not the prose string array"
        assert len(result) == 2, f"Expected 2 findings, got {len(result)}: {result}"
        assert all(isinstance(item, dict) for item in result), "All items should be dicts, not strings"
        severities = {item["severity"] for item in result}
        assert "major" in severities
        assert "minor" in severities

    def test_full_bug_scenario_in_markdown_fence(self) -> None:
        """Prose refs followed by fenced wrapper object findings."""
        text = (
            "Reviewing: "
            '["72-REQ-1.1", "72-REQ-2.3"]. '
            "Structured output:\n"
            "```json\n"
            '{"findings": [{"severity": "critical", "description": "Auth bypass"}]}\n'
            "```"
        )
        result = extract_json_array(text)
        assert result is not None
        assert len(result) == 1
        assert result[0]["severity"] == "critical"


# ---------------------------------------------------------------------------
# Backward compatibility
# ---------------------------------------------------------------------------


class TestBackwardCompatibility:
    """Existing extract_json_array behaviours are preserved."""

    def test_empty_input_returns_none(self) -> None:
        result = extract_json_array("")
        assert result is None

    def test_whitespace_input_returns_none(self) -> None:
        result = extract_json_array("   \n\t  ")
        assert result is None

    def test_no_json_returns_none(self) -> None:
        result = extract_json_array("Just plain text with no JSON at all.")
        assert result is None

    def test_bare_dict_array_returned(self) -> None:
        text = '[{"severity": "major", "description": "test"}]'
        result = extract_json_array(text)
        assert result == [{"severity": "major", "description": "test"}]

    def test_fenced_array_returned(self) -> None:
        text = 'Some prose.\n```json\n[{"severity": "minor"}]\n```\nDone.'
        result = extract_json_array(text)
        assert result == [{"severity": "minor"}]

    def test_repair_truncated_still_works(self) -> None:
        text = '[{"a": 1}, {"b": 2}, {"c"'
        result = extract_json_array(text, repair_truncated=True)
        assert result is not None
        assert len(result) >= 1
        assert result[0] == {"a": 1}


# ---------------------------------------------------------------------------
# extract_json_object — issue #57
# ---------------------------------------------------------------------------


class TestExtractJsonObject:
    """Tests for extract_json_object, including prose-prefixed scanning."""

    # -- AC-1: prose-prefixed JSON ----------------------------------------

    def test_prose_prefixed_json_returns_object(self) -> None:
        """Leading prose followed by a valid JSON object is extracted."""
        text = 'Here is my assessment:\n{"verdict": "PASS"}'
        result = extract_json_object(text)
        assert result == {"verdict": "PASS"}

    def test_prose_inline_prefix_returns_object(self) -> None:
        """Inline prose on the same line before JSON object is extracted."""
        text = 'Result: {"status": "ok"}'
        result = extract_json_object(text)
        assert result == {"status": "ok"}

    def test_multiline_prose_prefix(self) -> None:
        """Multiple lines of prose before JSON object are handled."""
        text = 'Let me analyze this.\nHere are my findings:\n{"score": 95, "pass": true}'
        result = extract_json_object(text)
        assert result == {"score": 95, "pass": True}

    # -- AC-2: invalid braces before real object --------------------------

    def test_skips_invalid_brace_finds_real_object(self) -> None:
        """Non-JSON braces in prose are skipped, real object is found."""
        text = 'cost {high} note\n{"k": 1}'
        result = extract_json_object(text)
        assert result == {"k": 1}

    def test_skips_multiple_invalid_braces(self) -> None:
        """Multiple non-JSON braces before the real object are skipped."""
        text = 'values {x} and {y} then\n{"actual": "data"}'
        result = extract_json_object(text)
        assert result == {"actual": "data"}

    # -- AC-3: no JSON object raises ValueError ---------------------------

    def test_no_json_raises_value_error(self) -> None:
        """Plain text with no JSON object raises ValueError."""
        with pytest.raises(ValueError, match="No JSON object found"):
            extract_json_object("just plain prose no json here")

    def test_empty_string_raises_value_error(self) -> None:
        """Empty string raises ValueError."""
        with pytest.raises(ValueError, match="No JSON object found"):
            extract_json_object("")

    def test_only_array_raises_value_error(self) -> None:
        """A JSON array (not object) raises ValueError."""
        with pytest.raises(ValueError, match="No JSON object found"):
            extract_json_object("[1, 2, 3]")

    # -- AC-4: existing behaviours unchanged ------------------------------

    def test_bare_json_object(self) -> None:
        """Bare JSON object (Strategy 1) is returned."""
        result = extract_json_object('{"a": 1}')
        assert result == {"a": 1}

    def test_fenced_json_object(self) -> None:
        """Fenced JSON object (Strategy 2) is returned."""
        text = 'Sure!\n```json\n{"a": 1}\n```'
        result = extract_json_object(text)
        assert result == {"a": 1}

    def test_fenced_json_without_label(self) -> None:
        """Fenced JSON without 'json' label (Strategy 2) is returned."""
        text = '```\n{"a": 1}\n```'
        result = extract_json_object(text)
        assert result == {"a": 1}

    def test_trailing_prose_returns_object(self) -> None:
        """JSON object followed by trailing prose is returned."""
        text = '{"a": 1}\ntrailing prose'
        result = extract_json_object(text)
        assert result == {"a": 1}

    # -- Edge cases -------------------------------------------------------

    def test_nested_object(self) -> None:
        """Nested JSON objects are correctly decoded."""
        text = 'prefix\n{"outer": {"inner": 42}}'
        result = extract_json_object(text)
        assert result == {"outer": {"inner": 42}}

    def test_object_with_array_value(self) -> None:
        """JSON object containing an array value is correctly decoded."""
        text = 'Here:\n{"items": [1, 2, 3]}'
        result = extract_json_object(text)
        assert result == {"items": [1, 2, 3]}

    def test_whitespace_only_raises(self) -> None:
        """Whitespace-only input raises ValueError."""
        with pytest.raises(ValueError, match="No JSON object found"):
            extract_json_object("   \n\t  ")
