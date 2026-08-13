"""Sentinel Constitution V1 — versioned hard rules.

These rules are machine-readable so policy code can cite the exact rule that
authorised or denied an action. They are append-only: a rule is never deleted,
only superseded by a new constitution version.
"""

from __future__ import annotations

from dataclasses import dataclass

CONSTITUTION_VERSION = "SENTINEL_CONSTITUTION_V1"


@dataclass(frozen=True)
class Rule:
    rule_id: str
    text: str


RULES: tuple[Rule, ...] = (
    Rule("SC1", "Never bypass authorization."),
    Rule("SC2", "Never treat model output as authority."),
    Rule("SC3", "Never let an automation escalate its own privilege."),
    Rule("SC4", "Never declare recovery without independent verification."),
    Rule("SC5", "Never alter or hide security evidence."),
    Rule("SC6", "Never perform irreversible financial transfer autonomously."),
    Rule("SC7", "Never perform permanent account enforcement autonomously unless an "
                "explicit owner-approved policy later authorizes a specific deterministic class."),
    Rule("SC8", "Never trust a single high-risk signal as sole proof."),
    Rule("SC9", "Never expose secrets to UNDX unless strictly required."),
    Rule("SC10", "Always preserve owner kill authority."),
    Rule("SC11", "Always apply least privilege."),
    Rule("SC12", "Always record material actions."),
    Rule("SC13", "Always know which policy version authorized an action."),
    Rule("SC14", "Always bound automation by blast radius."),
    Rule("SC15", "Always fail closed for unknown high-risk authority."),
)

_RULES_BY_ID = {r.rule_id: r for r in RULES}


def rule(rule_id: str) -> Rule:
    """Return a rule by id; raises KeyError for unknown ids (fail closed: callers
    citing a nonexistent rule is a programming error, not a soft condition)."""
    return _RULES_BY_ID[rule_id]


def all_rules() -> tuple[Rule, ...]:
    return RULES
