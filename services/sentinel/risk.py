"""Sentinel risk & blast-radius budgets (Stage 6).

Every automated capability must declare a bounded blast radius (SC14).
Unbounded budgets are rejected at construction — there is no "unlimited"
value, no sentinel zero, no None-means-infinity (SC15).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


class UnboundedBudgetError(ValueError):
    """Raised when a budget field is missing, non-positive, or absurd."""


# Hard ceilings: even an explicitly configured budget cannot exceed these in
# V1. Raising them is a constitution-visible change, not a config tweak.
MAX_ACTIONS_PER_HOUR = 100
MAX_AFFECTED_ENTITIES = 500


@dataclass(frozen=True)
class RiskBudget:
    """Declared bounds for one automated capability."""
    actions_per_hour: int
    max_affected_entities: int

    def __post_init__(self):
        for name, value, ceiling in (
            ("actions_per_hour", self.actions_per_hour, MAX_ACTIONS_PER_HOUR),
            ("max_affected_entities", self.max_affected_entities, MAX_AFFECTED_ENTITIES),
        ):
            if not isinstance(value, int) or isinstance(value, bool):
                raise UnboundedBudgetError(f"{name} must be a positive int, got {value!r}")
            if value <= 0:
                raise UnboundedBudgetError(f"{name} must be > 0 (no unbounded budgets, SC14)")
            if value > ceiling:
                raise UnboundedBudgetError(f"{name}={value} exceeds V1 ceiling {ceiling}")


@dataclass
class BudgetTracker:
    """In-process consumption tracker. Deny-by-default: once exhausted, every
    further spend is refused until the window rolls."""
    budget: RiskBudget
    _window_start: float | None = None
    _actions_in_window: int = 0
    _entities_in_window: set = field(default_factory=set)

    def _roll(self, now: float) -> None:
        if self._window_start is None:
            self._window_start = now
            return
        if now - self._window_start >= 3600:
            self._window_start = now
            self._actions_in_window = 0
            self._entities_in_window = set()

    def try_spend(self, entity_ids: tuple[str, ...] = (), *, now: float | None = None) -> bool:
        """Atomically check-and-consume one action touching ``entity_ids``.
        Returns False (and consumes nothing) if any bound would be exceeded."""
        now = time.time() if now is None else now
        self._roll(now)
        if self._actions_in_window + 1 > self.budget.actions_per_hour:
            return False
        prospective = self._entities_in_window | set(entity_ids)
        if len(prospective) > self.budget.max_affected_entities:
            return False
        self._actions_in_window += 1
        self._entities_in_window = prospective
        return True

    def snapshot(self) -> dict:
        return {
            "actions_used": self._actions_in_window,
            "actions_limit": self.budget.actions_per_hour,
            "entities_used": len(self._entities_in_window),
            "entities_limit": self.budget.max_affected_entities,
            "window_start": self._window_start,
        }
