"""Sentinel actor/service identity (Stage 4).

Every material action must be attributable to a concrete identity (SC12).
There is deliberately no super key or any other omnipotent credential —
least privilege is structural, not configurational (SC11).

Identity kinds:
- ``human``   — an operator; the only kind that can approve high-risk actions.
- ``service`` — a named platform component (worker, web, bridge).
- ``model``   — an AI system (UNDX). Model identities are ALWAYS advisory:
                the trust tier is capped at ``ADVISORY`` and cannot be raised
                (SC2 — model output is never authority).
- ``external``— a third-party signal source (vendor adapter). Signal ≠ guilt.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class TrustTier(IntEnum):
    UNTRUSTED = 0     # unauthenticated / unknown — fail closed
    ADVISORY = 1      # may submit observations & suggestions only
    OPERATIONAL = 2   # may run enabled low-risk runbooks
    PRIVILEGED = 3    # may approve sensitive actions (humans only in V1)
    OWNER = 4         # kill authority; never automated


KINDS = ("human", "service", "model", "external")

# Kind → maximum trust tier the identity system will ever grant. Attempting
# to register above the cap is a hard error, not a clamp — misconfiguration
# must be loud (SC15).
MAX_TIER_BY_KIND = {
    "human": TrustTier.OWNER,
    "service": TrustTier.OPERATIONAL,
    "model": TrustTier.ADVISORY,
    "external": TrustTier.ADVISORY,
}


@dataclass(frozen=True)
class Actor:
    actor_id: str
    kind: str
    trust_tier: TrustTier
    display_name: str = ""

    def __post_init__(self):
        if self.kind not in KINDS:
            raise ValueError(f"unknown actor kind: {self.kind!r}")
        cap = MAX_TIER_BY_KIND[self.kind]
        if self.trust_tier > cap:
            raise ValueError(
                f"trust tier {self.trust_tier.name} exceeds cap {cap.name} "
                f"for kind {self.kind!r} (SC11: least privilege)"
            )
        if not str(self.actor_id or "").strip():
            raise ValueError("actor_id is required (SC12: attribution)")


# Well-known service identities. Registered here, not minted at runtime, so
# an automation cannot invent a new privileged identity for itself (SC3).
_REGISTRY: dict[str, Actor] = {}


def register(actor: Actor) -> Actor:
    existing = _REGISTRY.get(actor.actor_id)
    if existing is not None and existing != actor:
        raise ValueError(f"actor {actor.actor_id!r} already registered with different attributes")
    _REGISTRY[actor.actor_id] = actor
    return actor


def get(actor_id: str) -> Actor:
    """Fail closed: unknown identity resolves to UNTRUSTED, never to a default
    service identity."""
    actor = _REGISTRY.get(str(actor_id or ""))
    if actor is None:
        return Actor(actor_id=str(actor_id or "unknown"), kind="external",
                     trust_tier=TrustTier.UNTRUSTED, display_name="unregistered")
    return actor


def all_actors() -> tuple[Actor, ...]:
    return tuple(_REGISTRY.values())


# Built-in identities for Sentinel's own components.
SENTINEL_INGEST = register(Actor("sentinel.ingest", "service", TrustTier.OPERATIONAL, "Sentinel ingest bridge"))
SENTINEL_CORRELATOR = register(Actor("sentinel.correlator", "service", TrustTier.OPERATIONAL, "Sentinel correlation engine"))
SENTINEL_INVARIANTS = register(Actor("sentinel.invariants", "service", TrustTier.OPERATIONAL, "Sentinel invariant checker"))
SENTINEL_VERIFIER = register(Actor("sentinel.verifier", "service", TrustTier.OPERATIONAL, "Sentinel independent verifier"))
UNDX_MODEL = register(Actor("undx.model", "model", TrustTier.ADVISORY, "UNDX reasoning layer"))
