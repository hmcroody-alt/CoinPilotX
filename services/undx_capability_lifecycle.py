"""Runtime capability lifecycle: AVAILABLE / LIMITED / TRAINING / PLANNED / DISABLED.

The registry answers "what can UNDX execute"; the knowledge map answers "what
does PulseSoc contain and how proven is each piece"; agent policy answers "what
does the server currently permit". None of them, alone, answers the question a
user or investor actually asks: *what can UNDX do now, what is it being
integrated to do, and what is only planned?*

This module is that answer — as a pure projection. It holds no capability list
of its own, so it cannot drift from the three authoritative sources it reads:

* ``undx_capability_registry.REGISTRY`` — registered means executable.
* ``undx_knowledge_map.RECORDS`` — unregistered records carry an
  ``implementation_status`` that maps deterministically onto lifecycle status.
* ``undx_agent_policy`` — server environment flags can demote an executable
  capability to LIMITED (writes suspended) or DISABLED (explicit denylist /
  kill switch) without a deploy. The client never decides this.

Status semantics (mission Section 6):
* AVAILABLE — registered, permitted by current server policy, executable now.
* LIMITED   — registered and real, but current server policy suspends part of
  it (e.g. the write kill switch); UNDX may draft/prepare, not execute.
* TRAINING  — implementation exists (fully or partially) but is not registered
  as executable: being integrated. UNDX may explain and draft, never claim
  execution.
* PLANNED   — the product intends it, but the domain service does not exist
  yet (``service_missing``). UNDX may recommend and explain the roadmap.
* DISABLED  — deliberately kept out of reach (``intentionally_disabled``,
  ``unsupported``, or an explicit policy denylist entry).

Never collapse TRAINING or PLANNED into AVAILABLE; the false-completion claim
that would follow is precisely what the gateway exists to prevent.
"""

from __future__ import annotations

from typing import Any

from services import undx_agent_policy as policy
from services import undx_capability_registry as registry
from services import undx_knowledge_map as knowledge_map
from services.undx_agent_contracts import ConfirmationPolicy


class CapabilityStatus:
    AVAILABLE = "AVAILABLE"
    LIMITED = "LIMITED"
    TRAINING = "TRAINING"
    PLANNED = "PLANNED"
    DISABLED = "DISABLED"

    ALL = frozenset({AVAILABLE, LIMITED, TRAINING, PLANNED, DISABLED})


class ExecutionMode:
    READ = "READ"
    RECOMMEND = "RECOMMEND"
    DRAFT = "DRAFT"
    EXECUTE = "EXECUTE"

    ALL = frozenset({READ, RECOMMEND, DRAFT, EXECUTE})


# implementation_status (knowledge map) → lifecycle status for UNREGISTERED records.
# Deterministic and total: every knowledge-map status has exactly one mapping, so a
# new record cannot silently fall into AVAILABLE.
_UNREGISTERED_STATUS = {
    # Code exists but nothing proves it end-to-end, or a named piece is absent:
    # being integrated. (A verified record is registered by invariant, so it
    # never reaches this table.)
    knowledge_map.ImplementationStatus.IMPLEMENTED_UNVERIFIED: CapabilityStatus.TRAINING,
    knowledge_map.ImplementationStatus.PARTIALLY_IMPLEMENTED: CapabilityStatus.TRAINING,
    # No callable domain service yet: roadmap.
    knowledge_map.ImplementationStatus.SERVICE_MISSING: CapabilityStatus.PLANNED,
    # Deliberately out of reach, or not a product behaviour at all.
    knowledge_map.ImplementationStatus.INTENTIONALLY_DISABLED: CapabilityStatus.DISABLED,
    knowledge_map.ImplementationStatus.UNSUPPORTED: CapabilityStatus.DISABLED,
}

_MODE_FOR_STATUS = {
    CapabilityStatus.TRAINING: ExecutionMode.DRAFT,
    CapabilityStatus.PLANNED: ExecutionMode.RECOMMEND,
    CapabilityStatus.DISABLED: ExecutionMode.RECOMMEND,
    CapabilityStatus.LIMITED: ExecutionMode.DRAFT,
}

# Canonical capability language (mission Section 21), keyed by status. One place,
# so native, web, and the model grounding all speak the same sentence.
CANONICAL_STATUS_LANGUAGE = {
    CapabilityStatus.AVAILABLE: "I can complete that through PulseSoc.",
    CapabilityStatus.LIMITED: (
        "I can assist with part of that workflow, but final execution still "
        "requires the current PulseSoc interface."
    ),
    CapabilityStatus.TRAINING: (
        "I understand that workflow and I am being integrated to support it, "
        "but direct execution is not available yet."
    ),
    CapabilityStatus.PLANNED: (
        "That capability is part of the PulseSoc roadmap and is not currently "
        "available."
    ),
    CapabilityStatus.DISABLED: "That capability is currently disabled.",
}


def _registered_status(capability_id: str, is_write: bool) -> tuple[str, str]:
    """Lifecycle status for a registered capability under CURRENT server policy.

    Policy is read live on every call — a Railway flag flip changes the answer
    without a deploy, which is what makes the client's rendering of capability
    state server-authoritative rather than baked in.
    """
    if not policy.capability_enabled(capability_id):
        return CapabilityStatus.DISABLED, "disabled by server policy"
    if is_write and not policy.writes_available():
        return CapabilityStatus.LIMITED, "writes suspended by server policy"
    if not is_write and not policy.reads_available():
        return CapabilityStatus.LIMITED, "reads suspended by server policy"
    return CapabilityStatus.AVAILABLE, ""


def _view(
    capability_id: str,
    description: str,
    domain: str,
    status: str,
    mode: str,
    *,
    requires_confirmation: bool,
    is_write: bool,
    reason: str = "",
) -> dict[str, Any]:
    return {
        "capability_id": capability_id,
        "description": description,
        "domain": domain,
        "status": status,
        "executionMode": mode,
        "requiresConfirmation": requires_confirmation,
        "requiresVerification": is_write and status == CapabilityStatus.AVAILABLE,
        "receiptRequired": is_write and status == CapabilityStatus.AVAILABLE,
        "statusReason": reason,
        "canonicalLanguage": CANONICAL_STATUS_LANGUAGE[status],
    }


def lifecycle_inventory() -> list[dict[str, Any]]:
    """Every known capability with its live lifecycle status.

    Union of the registry and the knowledge map, registry fields winning for
    registered ids (the map reads them from the registry anyway). Bounded and
    client-safe: ids, descriptions, and status only — never executors,
    verifiers, schemas, or internals.
    """
    views: dict[str, dict[str, Any]] = {}

    for spec in registry.REGISTRY.values():
        is_write = spec.is_write
        status, reason = _registered_status(spec.capability_id, is_write)
        mode = (
            (ExecutionMode.EXECUTE if is_write else ExecutionMode.READ)
            if status == CapabilityStatus.AVAILABLE
            else _MODE_FOR_STATUS[status]
        )
        views[spec.capability_id] = _view(
            spec.capability_id,
            spec.description,
            str(spec.capability_id or "").split(".", 1)[0] or "system",
            status,
            mode,
            requires_confirmation=spec.confirmation != ConfirmationPolicy.NEVER,
            is_write=is_write,
            reason=reason,
        )

    for record in knowledge_map.RECORDS:
        if record.capability_id in views:
            continue
        status = _UNREGISTERED_STATUS[record.implementation_status]
        views[record.capability_id] = _view(
            record.capability_id,
            record.description,
            record.product_area or str(record.capability_id).split(".", 1)[0],
            status,
            _MODE_FOR_STATUS[status],
            requires_confirmation=record.confirmation_policy != ConfirmationPolicy.NEVER,
            is_write=record.is_write,
            reason=record.implementation_status,
        )

    return sorted(views.values(), key=lambda v: v["capability_id"])


def lifecycle_counts(views: list[dict[str, Any]] | None = None) -> dict[str, int]:
    counts = {status: 0 for status in sorted(CapabilityStatus.ALL)}
    for view in views if views is not None else lifecycle_inventory():
        counts[view["status"]] += 1
    return counts


def capability_lifecycle_block() -> str:
    """Short grounding paragraph: live capability state + the canonical sentences.

    Deliberately counts, not the full inventory — the model needs the honest
    frame, not 100 lines of ids. Clients that need the list call
    ``lifecycle_inventory()`` through the self-knowledge surface.
    """
    counts = lifecycle_counts()
    return (
        "UNDX capability state (live, server-authoritative — reflects current "
        "policy flags):\n"
        f"- AVAILABLE (executable and verified): {counts[CapabilityStatus.AVAILABLE]}\n"
        f"- LIMITED (real but partially suspended by policy): {counts[CapabilityStatus.LIMITED]}\n"
        f"- TRAINING (being integrated; draft/explain only): {counts[CapabilityStatus.TRAINING]}\n"
        f"- PLANNED (roadmap; recommend/explain only): {counts[CapabilityStatus.PLANNED]}\n"
        f"- DISABLED (deliberately unavailable): {counts[CapabilityStatus.DISABLED]}\n"
        "When describing what you can do, use exactly this framing: for AVAILABLE "
        f"say \"{CANONICAL_STATUS_LANGUAGE[CapabilityStatus.AVAILABLE]}\"; for actions "
        "needing confirmation say you can prepare the action but need confirmation "
        f"before it is executed; for LIMITED say \"{CANONICAL_STATUS_LANGUAGE[CapabilityStatus.LIMITED]}\"; "
        f"for TRAINING say \"{CANONICAL_STATUS_LANGUAGE[CapabilityStatus.TRAINING]}\"; "
        f"for PLANNED say \"{CANONICAL_STATUS_LANGUAGE[CapabilityStatus.PLANNED]}\". "
        "Never present a TRAINING or PLANNED capability as complete, and never "
        "claim an execution succeeded unless the PulseSoc backend verified it."
    )
