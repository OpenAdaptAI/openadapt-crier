"""Commit filtering logic to skip uninteresting commits."""

from __future__ import annotations

import re

# Authors to always skip
_SKIP_AUTHORS = {
    "dependabot",
    "dependabot[bot]",
    "renovate",
    "renovate[bot]",
    "github-actions",
    "github-actions[bot]",
}

# Patterns in commit messages that indicate a skip
_SKIP_PATTERNS = [
    re.compile(r"^Merge branch\b", re.IGNORECASE),
    re.compile(r"^Merge pull request\b", re.IGNORECASE),
    re.compile(r"\[ci skip\]", re.IGNORECASE),
    re.compile(r"\[skip ci\]", re.IGNORECASE),
    re.compile(r"^chore\(release\)", re.IGNORECASE),
    re.compile(r"^bump version", re.IGNORECASE),
    re.compile(r"^chore: release\b", re.IGNORECASE),
    re.compile(r"^chore: bump\b", re.IGNORECASE),
    re.compile(r"^Merge remote-tracking branch\b", re.IGNORECASE),
    re.compile(r"^\d+\.\d+\.\d+$"),  # bare version number as message
]


def should_skip_commit(message: str, author: str) -> bool:
    """Determine whether a commit should be skipped based on its message and author.

    Returns True if the commit is a merge commit, from a bot author,
    contains CI skip markers, or matches version bump patterns.
    """
    # Check author against skip list
    author_lower = author.strip().lower()
    if author_lower in _SKIP_AUTHORS:
        return True

    # Check message against skip patterns
    message_stripped = message.strip()
    for pattern in _SKIP_PATTERNS:
        if pattern.search(message_stripped):
            return True

    return False
