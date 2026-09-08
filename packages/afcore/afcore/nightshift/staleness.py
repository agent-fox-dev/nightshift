"""Staleness check: post-fix evaluation of remaining issues.

Requirements: 71-REQ-5.1, 71-REQ-5.2, 71-REQ-5.3, 71-REQ-5.4,
              71-REQ-5.E1, 71-REQ-5.E2, 71-REQ-5.E3
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from afissues.labels import LABEL_FIX
from afissues.protocol import IssueResult

from afcore.core.errors import FatalAPIError
from afcore.core.json_extraction import extract_json_object

if TYPE_CHECKING:
    from afaudit.sink import SinkDispatcher

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StalenessResult:
    """Result of post-fix staleness evaluation."""

    obsolete_issues: list[int]  # issue numbers to close
    rationale: dict[int, str] = field(default_factory=dict)  # issue_number -> why


def _build_staleness_prompt(
    fixed_issue: IssueResult,
    remaining_issues: list[IssueResult],
    fix_diff: str,
) -> str:
    """Build the AI staleness evaluation prompt."""
    remaining_descriptions = []
    for issue in remaining_issues:
        body_preview = (issue.body or "")[:500]
        remaining_descriptions.append(f"- #{issue.number}: {issue.title}\n  Body: {body_preview}")

    diff_preview = fix_diff[:3000] if fix_diff else "(no diff available)"

    return f"""\
A fix was applied for issue #{fixed_issue.number}: \
{fixed_issue.title}

The fix diff:
{diff_preview}

Remaining issues to evaluate for obsolescence:
{chr(10).join(remaining_descriptions)}

For each remaining issue, determine if the fix above likely resolves it too.

Return a JSON object with:
- "obsolete": list of objects with "issue_number" and "rationale" for issues
  that are now obsolete due to the fix above.

Respond with ONLY the JSON object."""


def _parse_staleness_response(
    response_text: str,
    remaining_issues: list[IssueResult],
) -> StalenessResult:
    """Parse AI staleness response into a StalenessResult."""
    remaining_numbers = {i.number for i in remaining_issues}

    try:
        data = extract_json_object(response_text)
    except ValueError:
        return StalenessResult(obsolete_issues=[], rationale={})

    if not isinstance(data, dict):
        return StalenessResult(obsolete_issues=[], rationale={})

    obsolete_issues: list[int] = []
    rationale: dict[int, str] = {}

    for entry in data.get("obsolete", []):
        if not isinstance(entry, dict):
            continue
        issue_num = entry.get("issue_number")
        reason = entry.get("rationale", "AI-detected obsolescence")
        if isinstance(issue_num, int) and issue_num in remaining_numbers:
            obsolete_issues.append(issue_num)
            rationale[issue_num] = reason

    return StalenessResult(obsolete_issues=obsolete_issues, rationale=rationale)


async def _run_ai_staleness(
    fixed_issue: IssueResult,
    remaining_issues: list[IssueResult],
    fix_diff: str,
    config: object,
    *,
    sink: SinkDispatcher | None = None,
    run_id: str = "",
) -> StalenessResult:
    """Internal: run the AI staleness evaluation via maintainer:hunt archetype.

    Resolves model tier through the maintainer:hunt archetype identity so
    that ``[archetypes.overrides.maintainer]`` applies.  Falls back to the
    hardcoded maintainer:hunt default (SIMPLE) when no override is set.

    Requirements: 71-REQ-5.1
    """
    from afcore.nightshift.cost_helpers import AUX_CALL_MAX_TOKENS, nightshift_ai_call

    # Resolve tier via archetype identity rather than hardcoding a tier.
    # This makes staleness honour [archetypes.overrides.maintainer] the
    # same way batch triage already does.
    try:
        from afcore.engine.sdk_params import resolve_model_tier

        tier = resolve_model_tier(config, "maintainer", mode="hunt")  # type: ignore[arg-type]
    except Exception:
        tier = "SIMPLE"

    prompt = _build_staleness_prompt(fixed_issue, remaining_issues, fix_diff)

    response_text, _response = await nightshift_ai_call(
        model_tier=tier,
        max_tokens=AUX_CALL_MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
        context="staleness check",
        cost_label="staleness_check",
        config=config,
        sink=sink,
        run_id=run_id,
    )

    if response_text is None:
        return StalenessResult(obsolete_issues=[], rationale={})

    return _parse_staleness_response(response_text, remaining_issues)


async def check_staleness(
    fixed_issue: IssueResult,
    remaining_issues: list[IssueResult],
    fix_diff: str,
    config: object,
    platform: object,
    *,
    sink: SinkDispatcher | None = None,
    run_id: str = "",
    in_flight: set[int] | None = None,
) -> StalenessResult:
    """Evaluate remaining issues for obsolescence after a fix.

    Uses AI analysis + GitHub API verification. The GitHub API check is
    the authoritative source: an issue is only marked obsolete if the
    platform confirms it is no longer open with the af:fix label.

    Issues whose numbers are in ``in_flight`` are excluded from
    evaluation — they are currently being processed in parallel and
    must not be closed by a sibling fix's staleness check.

    When ``fix_diff`` is empty or whitespace-only, the function returns
    an empty result immediately without calling the AI.  Absence of
    evidence is not evidence of obsolescence — a diffless verdict would
    close issues based only on titles, which is unsafe for an unattended
    daemon (issue #53, #31).

    Fallback chain:
    - If fix_diff is empty: return empty immediately (NS-REQ-1)
    - If AI fails: verify via GitHub API only (71-REQ-5.E1)
    - If GitHub fails: log warning, return empty (71-REQ-5.E2)

    :class:`~afcore.core.errors.FatalAPIError` is never absorbed by the AI
    fallback — a rejected or unfunded API key is propagated so the daemon
    aborts with a clear message.

    Requirements: 71-REQ-5.1, 71-REQ-5.2, 71-REQ-5.E1, 71-REQ-5.E2, NS-REQ-1
    """
    # Fail closed: refuse to nominate anything without a real diff.
    # An empty diff means the AI would decide based on titles alone,
    # which is unsafe for an unattended daemon (issue #53, #31).
    if not fix_diff or not fix_diff.strip():
        logger.info(
            "Staleness check skipped for fix #%d: no diff available",
            fixed_issue.number,
        )
        return StalenessResult(obsolete_issues=[], rationale={})

    # Exclude in-flight issues from staleness evaluation
    if in_flight:
        remaining_issues = [i for i in remaining_issues if i.number not in in_flight]
        if not remaining_issues:
            return StalenessResult(obsolete_issues=[], rationale={})

    remaining_numbers = {i.number for i in remaining_issues}

    # Step 1: Try AI staleness evaluation
    ai_rationale: dict[int, str] = {}
    ai_failed = False
    try:
        ai_result = await _run_ai_staleness(fixed_issue, remaining_issues, fix_diff, config, sink=sink, run_id=run_id)
        ai_rationale = ai_result.rationale
    except FatalAPIError:
        # Non-recoverable API condition (no credit, rejected credentials).
        # Every later AI call would fail the same way, so propagate instead
        # of degrading to the GitHub-only fallback.
        raise
    except Exception:
        ai_failed = True
        logger.warning(
            "Staleness AI call failed for fix #%d, falling back to GitHub API",
            fixed_issue.number,
            exc_info=True,
        )

    # Step 2: Verify with GitHub API by re-fetching issues (71-REQ-5.2)
    try:
        still_open = await platform.list_issues_by_label(  # type: ignore[attr-defined]
            LABEL_FIX,
            state="open",
        )
        still_open_numbers = {i.number for i in still_open}
    except Exception:
        logger.warning(
            "GitHub re-fetch failed during staleness check for fix #%d, continuing without removing any issues",
            fixed_issue.number,
            exc_info=True,
        )
        return StalenessResult(obsolete_issues=[], rationale={})

    # Step 3: Determine obsolete issues
    obsolete_issues: list[int] = []
    rationale: dict[int, str] = {}

    if ai_failed:
        # AI failure fallback (71-REQ-5.E1, issue #54): without AI we
        # cannot determine which issues are obsolete.  The label-scoped
        # ``still_open_numbers`` conflates "closed" with "open but label
        # removed", so nominating absent issues risks closing live issues.
        # Return empty — genuinely superseded issues will be picked up on
        # the next cycle when the AI is available again.
        return StalenessResult(obsolete_issues=[], rationale={})
    else:
        # Normal path: an issue is obsolete when the AI says it is
        # resolved AND GitHub confirms it is still open (so our
        # close_issue() call is meaningful).
        for num in remaining_numbers:
            if num in ai_rationale and num in still_open_numbers:
                obsolete_issues.append(num)
                rationale[num] = ai_rationale[num]

    return StalenessResult(obsolete_issues=obsolete_issues, rationale=rationale)
