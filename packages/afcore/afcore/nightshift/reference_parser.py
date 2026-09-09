"""Reference parser: explicit dependency extraction from issue text.

Requirements: 71-REQ-2.1, 71-REQ-2.3, 71-REQ-2.E1

Note: 71-REQ-2.2 (GitHub timeline-based dependency ordering) was removed
because no platform implementation provides the ``get_issue_timeline`` method.
See ``docs/errata/71_github_timeline_dependency.md`` for details.
"""

from __future__ import annotations

import logging
import re

from afissues.protocol import IssueResult

from afcore.nightshift.dep_graph import DependencyEdge

logger = logging.getLogger(__name__)

# Case-insensitive patterns: "depends on #N", "blocked by #N",
# "after #N", "requires #N".
_DEPENDENCY_PATTERN = re.compile(
    r"(?:depends\s+on|blocked\s+by|after|requires)\s+#(\d+)",
    re.IGNORECASE,
)


def parse_text_references(issues: list[IssueResult]) -> list[DependencyEdge]:
    """Extract dependency edges from issue body text.

    Matches case-insensitive patterns: "depends on #N", "blocked by #N",
    "after #N", "requires #N". Only returns edges where both endpoints
    are in the batch.

    Requirements: 71-REQ-2.1, 71-REQ-2.3, 71-REQ-2.E1
    """
    batch_numbers = {i.number for i in issues}
    edges: list[DependencyEdge] = []

    for issue in issues:
        if not issue.body:
            continue
        for match in _DEPENDENCY_PATTERN.finditer(issue.body):
            ref_number = int(match.group(1))
            # Only include edges where both endpoints are in the batch
            # (71-REQ-2.E1)
            if ref_number in batch_numbers and ref_number != issue.number:
                edges.append(
                    DependencyEdge(
                        from_issue=ref_number,
                        to_issue=issue.number,
                        source="explicit",
                        rationale=f"Issue #{issue.number} body: '{match.group(0)}'",
                    )
                )

    return edges
