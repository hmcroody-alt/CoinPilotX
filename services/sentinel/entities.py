"""Sentinel actor types and canonical entity references (Mission 2).

Attribution discipline (SC12): every event names WHO acted using a closed
actor-type vocabulary — "everything is SYSTEM" is exactly the failure mode
this exists to prevent. Entities are referenced with stable, typed refs like
``user:42``, ``provider:stripe``, ``deployment:abc123`` so correlation can
join across domains without guessing.

Internal identifiers (including Pulse IDs) may appear in refs INSIDE
sentinel storage, which is INTERNAL-classified; they are never emitted by
public serializers (Stage 23 privacy invariant checks this).
"""

from __future__ import annotations

# Closed actor-type vocabulary. Unknown type → hard error (SC15).
ACTOR_TYPES = (
    "USER", "ADMIN", "SELLER", "ADVERTISER",
    "SERVICE", "WORKER", "PROVIDER", "WEBHOOK",
    "DEVICE", "SESSION",
    "UNDX_AGENT", "RUNBOOK", "DEPLOYMENT", "SYSTEM",
)

# Closed entity-type vocabulary for refs.
ENTITY_TYPES = (
    "user", "admin", "seller", "advertiser",
    "service", "worker", "provider", "webhook",
    "device", "session", "network",
    # Mission 3 identity entities. "ip" and "network" ids are HASHED network
    # refs (never raw addresses); auth_attempt / recovery_attempt reference
    # observed identity events, not credentials — no raw tokens ever become
    # entity ids (Stage 2, SC9).
    "ip", "asn", "auth_attempt", "recovery_attempt",
    "order", "payment", "payout", "refund", "settlement",
    "campaign", "wallet",
    "deployment", "route", "job", "incident", "event",
    "undx_agent", "runbook",
)


class EntityRefError(ValueError):
    """Malformed or unknown-typed entity reference (SC15)."""


def validate_actor_type(actor_type: str) -> str:
    if actor_type not in ACTOR_TYPES:
        raise EntityRefError(f"unknown actor_type {actor_type!r} (SC15)")
    return actor_type


def make_ref(entity_type: str, entity_id: str | int) -> str:
    """Build ``type:id``. Type must be known; id must be non-empty and must
    not itself contain a colon-delimited type (no nesting)."""
    if entity_type not in ENTITY_TYPES:
        raise EntityRefError(f"unknown entity type {entity_type!r} (SC15)")
    ident = str(entity_id).strip()
    if not ident:
        raise EntityRefError("entity id is required")
    return f"{entity_type}:{ident}"


def parse_ref(ref: str) -> tuple[str, str]:
    """Split ``type:id`` and validate the type. Fail closed on malformed refs."""
    raw = str(ref or "")
    if ":" not in raw:
        raise EntityRefError(f"malformed entity ref {raw!r}")
    entity_type, _, ident = raw.partition(":")
    if entity_type not in ENTITY_TYPES:
        raise EntityRefError(f"unknown entity type {entity_type!r} (SC15)")
    if not ident.strip():
        raise EntityRefError(f"empty entity id in ref {raw!r}")
    return entity_type, ident


def is_valid_ref(ref: str) -> bool:
    try:
        parse_ref(ref)
        return True
    except EntityRefError:
        return False
