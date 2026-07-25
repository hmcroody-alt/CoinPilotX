"""Business OS — Advertising slice 7 candidate-selection service.

The smallest durable, deterministic selection strategy (spec §4). Given the set of
request-eligible candidates, pick exactly ONE (or none). Explicitly NOT an auction:
no bids, no budget redistribution, no pacing, no lookalike/ML ranking, no
conversion prediction, no identity graph.

Selection is isolated behind the ``SelectionStrategy`` interface so a real auction
can replace ``DeterministicRotation`` later WITHOUT touching the delivery routes,
the delivery-instance store, or the event tables. The default strategy scores each
candidate by a STABLE hash of (subject_ref, creative_id) so:

  * the same viewer + candidate set deterministically yields the same winner
    (reproducible, testable without client state), and
  * different viewers spread across creatives (a simple, privacy-safe rotation),
    with no cross-user state and no stored counters.

Ties break on creative_id to keep the order total and deterministic.
"""

from __future__ import annotations

import hashlib
from typing import Any, Optional

from . import eligibility as _elig
from . import delivery_common as _c


class SelectionStrategy:
    """Replaceable selection contract. An auction implements the same method."""

    def select(self, candidates: list, *, subject_ref: str,
               placement: str) -> Optional[dict]:  # pragma: no cover - interface
        raise NotImplementedError


def _rotation_score(subject_ref: str, creative_id: str) -> str:
    """Stable, uniform per-(viewer,creative) score. Hex digest gives a total order
    that is deterministic and evenly distributed, with no persisted state."""
    raw = f"{subject_ref}:{creative_id}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class DeterministicRotation(SelectionStrategy):
    """Default MVP strategy: deterministic per-viewer rotation over eligibles."""

    def select(self, candidates: list, *, subject_ref: str,
               placement: str) -> Optional[dict]:
        eligibles = [c for c in candidates if c.get("eligible")]
        if not eligibles:
            return None
        # Lowest rotation score wins; creative_id breaks ties for a total order.
        eligibles.sort(key=lambda c: (
            _rotation_score(subject_ref, str(c.get("creative_id"))),
            str(c.get("creative_id")),
        ))
        return eligibles[0]


# Module-level default; callers may inject another strategy for tests/future work.
_DEFAULT_STRATEGY: SelectionStrategy = DeterministicRotation()


def select_candidate(conn, *, placement: str, subject_ref: str,
                     request_ctx: dict,
                     strategy: Optional[SelectionStrategy] = None) -> dict:
    """Full selection pass for one placement request.

    Enumerates approved+type-compatible creatives, evaluates each through the
    eligibility gates, then applies the strategy to pick one winner. Returns a
    structured result carrying the winner (or None) plus per-candidate decisions
    for observability — never raises for a no-candidate outcome.
    """
    strategy = strategy or _DEFAULT_STRATEGY
    rows = _elig.list_candidate_creatives(conn, placement)
    decisions = [
        _elig.evaluate(conn, row, placement=placement,
                       request_ctx=request_ctx, subject_ref=subject_ref)
        for row in rows
    ]
    eligible_count = sum(1 for d in decisions if d.get("eligible"))
    winner = strategy.select(decisions, subject_ref=subject_ref, placement=placement)
    return {
        "winner": winner,
        "candidate_count": len(decisions),
        "eligible_count": eligible_count,
        "decisions": decisions,
        "strategy": type(strategy).__name__,
    }
