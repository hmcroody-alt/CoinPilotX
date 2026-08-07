"""Server-authoritative answer to "what is UNDX, and what can it do right now?"

The native and web clients must not decide capability availability from local
hard-coded metadata (mission Section 6/24). This module composes that answer on
the server from two authoritative sources:

* ``services.undx_company_identity`` — who builds PulseSoc and the honesty rules.
* ``services.undx_capability_registry`` — the allowlist of executable actions.

The capability registry is an allowlist whose stated invariant is that a
capability whose executor is not finished is *not registered* (it surfaces as
``unsupported_capability`` instead). So every entry here is genuinely executable:
its status is ``AVAILABLE``. Capabilities that are only planned or being
integrated are, by construction, absent from the registry — which is exactly why
UNDX must never claim to perform them. This module reports what is real, and the
company grounding block instructs UNDX to be honest about everything that is not.
"""

from __future__ import annotations

from typing import Any

from services import undx_capability_lifecycle as lifecycle
from services import undx_capability_registry as registry
from services import undx_company_identity as company
from services.undx_agent_contracts import ConfirmationPolicy, RiskLevel


def _domain_of(capability_id: str) -> str:
    """Derive a stable domain from the dotted capability id (e.g. crypto.alerts.create)."""
    head = str(capability_id or "").split(".", 1)[0].strip()
    return head or "system"


def _capability_view(spec: registry.CapabilitySpec) -> dict[str, Any]:
    is_write = spec.is_write
    return {
        "capability_id": spec.capability_id,
        "description": spec.description,
        "domain": _domain_of(spec.capability_id),
        # Every registered capability is executable by the registry's own invariant.
        "status": "AVAILABLE",
        "executionMode": "EXECUTE" if is_write else "READ",
        "requiresConfirmation": spec.confirmation != ConfirmationPolicy.NEVER,
        # Writes are independently verified; reads have no verifier by design.
        "requiresVerification": is_write,
        "receiptRequired": is_write,
    }


def capability_inventory() -> list[dict[str, Any]]:
    """Every executable capability, in a bounded, client-safe shape."""
    return [
        _capability_view(spec)
        for spec in sorted(registry.REGISTRY.values(), key=lambda s: s.capability_id)
    ]


def _counts(views: list[dict[str, Any]]) -> dict[str, Any]:
    by_domain: dict[str, int] = {}
    read = write = confirm = 0
    for view in views:
        by_domain[view["domain"]] = by_domain.get(view["domain"], 0) + 1
        if view["executionMode"] == "READ":
            read += 1
        else:
            write += 1
        if view["requiresConfirmation"]:
            confirm += 1
    return {
        "total": len(views),
        "read_only": read,
        "write": write,
        "requires_confirmation": confirm,
        "by_domain": dict(sorted(by_domain.items())),
    }


def self_knowledge() -> dict[str, Any]:
    """The bootstrap self-description UNDX/clients can render without hard-coding.

    Server-authoritative. Safe to return to an authenticated client: it exposes
    capability *ids and shapes*, never executors, verifiers, permissions internals,
    secrets, or metrics.
    """
    views = capability_inventory()
    return {
        "assistant": {
            "name": "UNDX",
            "description": (
                "UNDX is PulseSoc's native intelligence companion. It helps you "
                "understand and navigate the ecosystem and can perform a growing "
                "set of governed, verified PulseSoc actions."
            ),
        },
        "company": company.facts(),
        "canonical": {
            "company_explanation": company.CANONICAL_COMPANY_EXPLANATION,
            "pulsesoc_definition": company.CANONICAL_PULSESOC_DEFINITION,
        },
        "capabilities": {
            "counts": _counts(views),
            "available": views,
            # Full lifecycle projection (AVAILABLE/LIMITED/TRAINING/PLANNED/
            # DISABLED) over registry + knowledge map + live server policy.
            # Server-authoritative; supersedes the flat "available" list for
            # clients that render capability state.
            "lifecycle": lifecycle.lifecycle_inventory(),
            "lifecycle_counts": lifecycle.lifecycle_counts(),
            "canonical_language": dict(lifecycle.CANONICAL_STATUS_LANGUAGE),
        },
        "honesty": {
            "never_fabricates": list(company.UNVERIFIABLE_WITHOUT_SOURCE),
            "capability_rule": (
                "UNDX only reports actions this registry can actually execute and "
                "verify. Anything not listed here is not executable yet, and UNDX "
                "will say so rather than claim it was done."
            ),
        },
        "version": {
            "company_identity": company.COMPANY_IDENTITY_VERSION,
        },
    }
