"""Sentinel multidimensional authority (Stage 5).

Authority is a (dimension, level) matrix, not a single admin bit. A decision
is authorized only when EVERY required dimension is satisfied. Unknown
dimensions and unknown levels fail closed (SC15). Model output can request
but never grant (SC2). Decisions cite the constitution rule and policy
version that produced them (SC13).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum

from services.sentinel.constitution import CONSTITUTION_VERSION
from services.sentinel.identity import Actor, TrustTier

DIMENSIONS = ("OPERATIONAL", "SECURITY", "FINANCIAL", "PRIVACY", "COMPLIANCE")


class AuthorityLevel(IntEnum):
    READ = 0             # observe only
    SUGGEST = 1          # may propose; a different authority must act
    ACT_REVERSIBLE = 2   # bounded, reversible, automated action
    ACT_SENSITIVE = 3    # requires explicit human approval per action
    OWNER_ONLY = 4       # never autonomous, never delegated (SC6/SC7/SC10)


@dataclass(frozen=True)
class AuthorityDecision:
    allowed: bool
    reason: str
    rule_ids: tuple[str, ...]
    policy_version: str = CONSTITUTION_VERSION

    def __bool__(self) -> bool:  # pragma: no cover - convenience
        return self.allowed


@dataclass
class AuthorityGrant:
    """What an actor holds, per dimension. Missing dimension == READ only."""
    actor_id: str
    levels: dict[str, AuthorityLevel] = field(default_factory=dict)

    def level_for(self, dimension: str) -> AuthorityLevel:
        return self.levels.get(dimension, AuthorityLevel.READ)


def _deny(reason: str, *rule_ids: str) -> AuthorityDecision:
    return AuthorityDecision(False, reason, tuple(rule_ids))


def _allow(reason: str, *rule_ids: str) -> AuthorityDecision:
    return AuthorityDecision(True, reason, tuple(rule_ids))


def check(actor: Actor, grant: AuthorityGrant, dimension: str,
          required: AuthorityLevel, *, human_approved: bool = False) -> AuthorityDecision:
    """Deterministic authorization. No text path can influence the outcome."""
    if dimension not in DIMENSIONS:
        return _deny(f"unknown authority dimension {dimension!r}", "SC15")
    if not isinstance(required, AuthorityLevel):
        return _deny("unknown authority level", "SC15")
    if grant.actor_id != actor.actor_id:
        return _deny("grant does not belong to actor", "SC1")

    # Model identities can never act, regardless of grant contents (SC2).
    if actor.kind == "model" and required > AuthorityLevel.SUGGEST:
        return _deny("model identities may only read/suggest", "SC2")
    if actor.trust_tier == TrustTier.UNTRUSTED and required > AuthorityLevel.READ:
        return _deny("untrusted identity", "SC1", "SC15")

    # OWNER_ONLY is never satisfiable by automation at all.
    if required == AuthorityLevel.OWNER_ONLY:
        if actor.kind != "human" or actor.trust_tier < TrustTier.OWNER:
            return _deny("owner-only action", "SC6", "SC7", "SC10")

    held = grant.level_for(dimension)
    if held < required:
        return _deny(f"holds {held.name}, requires {required.name} in {dimension}", "SC1", "SC11")

    # ACT_SENSITIVE and above always needs a fresh human approval, and any
    # FINANCIAL action at ACT_SENSITIVE+ can never be autonomous (SC6).
    if required >= AuthorityLevel.ACT_SENSITIVE and not human_approved:
        return _deny("sensitive action requires explicit human approval", "SC6" if dimension == "FINANCIAL" else "SC7")

    return _allow(f"{actor.actor_id} holds {held.name} >= {required.name} in {dimension}", "SC11", "SC13")


def check_all(actor: Actor, grant: AuthorityGrant,
              requirements: dict[str, AuthorityLevel], *, human_approved: bool = False) -> AuthorityDecision:
    """Every required dimension must pass; first denial wins (fail closed)."""
    if not requirements:
        return _deny("empty requirement set — refusing to authorize nothing", "SC15")
    for dimension, level in requirements.items():
        decision = check(actor, grant, dimension, level, human_approved=human_approved)
        if not decision.allowed:
            return decision
    return _allow("all required dimensions satisfied", "SC11", "SC13")
