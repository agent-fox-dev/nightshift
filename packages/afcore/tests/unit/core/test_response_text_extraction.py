"""Tests for response text extraction across thinking-enabled models.

Models that think by default (Claude Opus 5, Sonnet 5, and the 4.6+ family
under adaptive thinking) return a ``thinking`` block before the text block.
Reading ``content[0].text`` returned None for those and callers reported
"AI response has no text content" for a perfectly good response.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from afcore.core.client import extract_response_text


def _text_block(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def _thinking_block(thinking: str = "") -> SimpleNamespace:
    """A thinking block carries .thinking — it has no .text attribute."""
    return SimpleNamespace(type="thinking", thinking=thinking, signature="sig")


def _tool_use_block() -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id="tu_1", name="t", input={})


class TestThinkingModels:
    """Text is found regardless of what precedes it in the content list."""

    def test_skips_leading_thinking_block(self) -> None:
        """The regression: thinking first, text second."""
        response = SimpleNamespace(content=[_thinking_block(), _text_block("hello")])

        assert extract_response_text(response) == "hello"

    def test_skips_summarized_thinking_block(self) -> None:
        """display='summarized' fills .thinking but still has no .text."""
        response = SimpleNamespace(content=[_thinking_block("Let me work through this..."), _text_block("answer")])

        assert extract_response_text(response) == "answer"

    def test_skips_multiple_leading_blocks(self) -> None:
        """Interleaved thinking and tool use before the text block."""
        response = SimpleNamespace(
            content=[_thinking_block(), _tool_use_block(), _thinking_block(), _text_block("final")]
        )

        assert extract_response_text(response) == "final"

    def test_text_first_still_works(self) -> None:
        """Non-thinking models (Haiku 4.5) are unaffected."""
        response = SimpleNamespace(content=[_text_block("plain")])

        assert extract_response_text(response) == "plain"

    def test_first_of_several_text_blocks_wins(self) -> None:
        """Citations split a response into several text blocks."""
        response = SimpleNamespace(content=[_text_block("first"), _text_block("second")])

        assert extract_response_text(response) == "first"

    def test_empty_text_block_is_returned_not_skipped(self) -> None:
        """An empty string is a real answer, not a missing one."""
        response = SimpleNamespace(content=[_thinking_block(), _text_block("")])

        assert extract_response_text(response) == ""


class TestNoTextAvailable:
    """None is still returned when there genuinely is no text."""

    def test_thinking_only_returns_none(self) -> None:
        """Hitting max_tokens during thinking yields no text block at all."""
        response = SimpleNamespace(content=[_thinking_block()])

        assert extract_response_text(response) is None

    def test_tool_use_only_returns_none(self) -> None:
        response = SimpleNamespace(content=[_tool_use_block()])

        assert extract_response_text(response) is None

    def test_empty_content_returns_none(self) -> None:
        assert extract_response_text(SimpleNamespace(content=[])) is None

    def test_missing_content_returns_none(self) -> None:
        assert extract_response_text(SimpleNamespace()) is None


class TestTestDoubleCompatibility:
    """Mocks without a usable `type` keep working."""

    def test_magicmock_block_without_type(self) -> None:
        """MagicMock auto-creates .type, so it can never equal "text"."""
        response = SimpleNamespace(content=[MagicMock(text="mocked")])

        assert extract_response_text(response) == "mocked"

    def test_plain_namespace_without_type(self) -> None:
        response = SimpleNamespace(content=[SimpleNamespace(text="bare")])

        assert extract_response_text(response) == "bare"
