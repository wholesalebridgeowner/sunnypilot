"""Fail-closed exact recorded-build pin check for private Ram R-log analysis.

Only the known full 40-hex Git commit is accepted. In particular, a short
matching prefix is not an independently verified source match. This module
never returns or logs a recorded value, route identifier or device token.
"""
from __future__ import annotations

import re


def require_exact_recorded_commits(commits, expected):
    """Return verified metadata count or raise a privacy-safe ValueError.

    `commits` contains `initData.gitCommit` strings from one completed R-log.
    Unknown working-tree dirtiness is a separate unclosed release gate.
    """
    if not isinstance(expected, str) or re.fullmatch(r"[0-9a-f]{40}", expected) is None:
        raise ValueError("invalid pinned source configuration")
    if not isinstance(commits, (tuple, list)) or not commits:
        raise ValueError("missing recorded build metadata")
    if any(type(commit) is not str or commit != expected for commit in commits):
        raise ValueError("recorded build metadata is not an exact source match")
    return len(commits)
