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


# The order the status sentences are offered to the model in, and the clause each one
# is wrapped in. DISABLED is absent on purpose: there is no sentence a model should
# reach for on its own about a deliberately unreachable capability, and the refusal for
# one comes from the gateway rather than from prose.
_LANGUAGE_ORDER = (
    CapabilityStatus.AVAILABLE,
    CapabilityStatus.LIMITED,
    CapabilityStatus.TRAINING,
    CapabilityStatus.PLANNED,
)

_LANGUAGE_CLAUSE = {
    CapabilityStatus.AVAILABLE: (
        'for AVAILABLE say "{sentence}", and where an action needs confirmation say '
        "you can prepare it but need confirmation before it is executed"
    ),
    CapabilityStatus.LIMITED: 'for LIMITED say "{sentence}"',
    CapabilityStatus.TRAINING: 'for TRAINING say "{sentence}"',
    CapabilityStatus.PLANNED: 'for PLANNED say "{sentence}"',
}


def capability_lifecycle_block() -> str:
    """Short grounding paragraph: live capability state + the canonical sentences.

    Deliberately counts, not the full inventory — the model needs the honest
    frame, not 100 lines of ids. Clients that need the list call
    ``lifecycle_inventory()`` through the self-knowledge surface.

    A status's sentence is offered **only when something currently holds that status**.
    Every count line is still printed, including the zeroes, because a zero is
    information; but a fluent, ready-made sentence is not neutral information. This
    block is prepended to every conversational system prompt
    (:func:`services.pulse_ai_provider_router.prepare_undx_model_request`), and the
    version that handed over all five sentences unconditionally handed a model the
    words "final execution still requires the current PulseSoc interface" on turns
    where nothing was LIMITED at all. A model reaching for the most available excuse
    in its context is not a surprising failure; it is the predictable one, and it
    produced a refusal for an action the gateway would have executed.

    Suppressing the AVAILABLE sentence when the count is zero follows from the same
    rule and is not collateral damage: if policy has suspended reads and writes then
    nothing is executable, and "I can complete that through PulseSoc" is a sentence the
    model should not have either.
    """
    counts = lifecycle_counts()
    lines = [
        "UNDX capability state (live, server-authoritative — reflects current "
        "policy flags):",
        f"- AVAILABLE (executable and verified): {counts[CapabilityStatus.AVAILABLE]}",
        f"- LIMITED (real but partially suspended by policy): {counts[CapabilityStatus.LIMITED]}",
        f"- TRAINING (being integrated; draft/explain only): {counts[CapabilityStatus.TRAINING]}",
        f"- PLANNED (roadmap; recommend/explain only): {counts[CapabilityStatus.PLANNED]}",
        f"- DISABLED (deliberately unavailable): {counts[CapabilityStatus.DISABLED]}",
    ]
    present = [status for status in _LANGUAGE_ORDER if counts[status]]
    if present:
        clauses = "; ".join(
            _LANGUAGE_CLAUSE[status].format(sentence=CANONICAL_STATUS_LANGUAGE[status])
            for status in present
        )
        lines.append(f"When describing what you can do, use exactly this framing: {clauses}.")
    # The prohibition is stated without quoting the wording it forbids. Naming the
    # LIMITED sentence here in order to ban it would put the sentence back into the
    # context window, which is the entire mechanism this function was changed to close;
    # a negative instruction is not a reliable defence against the availability of a
    # fluent phrase.
    lines.append(
        "Only the framings listed immediately above are available to you. Do not "
        "invent a restriction for a status whose count is zero, and do not tell the "
        "person an action must be completed somewhere else unless a listed framing "
        "says so. Never present a TRAINING or PLANNED capability as complete, and "
        "never claim an execution succeeded unless the PulseSoc backend verified it."
    )
    return "\n".join(lines)
