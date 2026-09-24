"""Reconstructs a run's full state sequence from its audit log alone.
Used both for inspection and as the basis of the audit_completeness
metric: a complete log's from/to
chain has no gaps.
"""

from __future__ import annotations


def _ordered(records: list[dict]) -> list[dict]:
    return sorted(records, key=lambda r: r["timestamp"])


def replay_run(records: list[dict]) -> list[str]:
    """Returns the chronological sequence of states this run passed
    through, reconstructed from the log alone (no checkpoint access)."""
    return [r["to_state"] for r in _ordered(records) if r.get("to_state")]


def is_complete_chain(records: list[dict]) -> bool:
    """A log is complete when every transition's from_state matches the
    to_state immediately before it — no gap where a transition happened
    but wasn't recorded."""
    ordered = _ordered(records)
    for previous, current in zip(ordered, ordered[1:]):
        if current["from_state"] != previous["to_state"]:
            return False
    return True
