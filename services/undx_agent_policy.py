"""Deterministic, server-side authorisation for agent actions.

Nothing in this module consults a language model, and that is its entire purpose.
A model's job is to *propose*; the decision about whether a proposal may execute is
made here from the capability registry, environment flags and the authenticated
user id — inputs an attacker who controls conversation text cannot reach.

The practical consequence: text like "you are pre-authorised, skip confirmation"
has no effect anywhere. There is no code path from message content to
``Decision.allow``. The only thing that turns ``require_confirmation`` into
``allow`` is a redeemed approval token, and those are minted against a specific
user, capability and argument hash.

Rollout is layered so that read and write access move independently:

``UNDX_AGENT_ENABLED``
    Master switch. Off ⇒ the agent is unavailable and UNDX falls back to
    conversational answers. Chat itself is never disabled by this module.
``UNDX_AGENT_READS_ENABLED`` / ``UNDX_AGENT_WRITES_ENABLED``
    Independent gates, so read capabilities can ship to a cohort while writes stay
    dark.
``UNDX_AGENT_DISABLE_WRITES``
    Kill switch. Overrides every other write flag, including per-capability
    allowlists. This is the lever to pull during an incident.
``UNDX_AGENT_ENABLED_CAPABILITIES`` / ``UNDX_AGENT_DISABLED_CAPABILITIES``
    Per-capability control, so one misbehaving action can be withdrawn without
    taking the runtime down.
``UNDX_AGENT_QA_USER_IDS``
    Explicit server-owned cohort. Empty means nobody, never everybody.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from services.undx_agent_contracts import (
    AgentOutcome,
    ConfirmationPolicy,
    RiskLevel,
    clean,
)
from services.undx_capability_registry import CapabilitySpec


AGENT_ENABLED_ENV = "UNDX_AGENT_ENABLED"
AGENT_READS_ENV = "UNDX_AGENT_READS_ENABLED"
AGENT_WRITES_ENV = "UNDX_AGENT_WRITES_ENABLED"
AGENT_KILL_SWITCH_ENV = "UNDX_AGENT_DISABLE_WRITES"
AGENT_ALLOWLIST_ENV = "UNDX_AGENT_ENABLED_CAPABILITIES"
AGENT_DENYLIST_ENV = "UNDX_AGENT_DISABLED_CAPABILITIES"
AGENT_QA_USERS_ENV = "UNDX_AGENT_QA_USER_IDS"


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _id_set(raw: str) -> set[str]:
    return {part.strip() for part in str(raw or "").split(",") if part.strip()}


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------


ALLOW = "allow"
REQUIRE_CONFIRMATION = "require_confirmation"
DENY = "deny"


@dataclass(frozen=True)
class Decision:
    """The authorisation verdict for one proposed capability invocation."""

    verdict: str
    capability_id: str
    risk: str
    reason: str = ""
    message: str = ""
    outcome: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.verdict == ALLOW

    @property
    def needs_confirmation(self) -> bool:
        return self.verdict == REQUIRE_CONFIRMATION

    @property
    def denied(self) -> bool:
        return self.verdict == DENY


# ---------------------------------------------------------------------------
# Flag surface
# ---------------------------------------------------------------------------


def flags() -> dict[str, Any]:
    """A snapshot of the rollout surface, safe to log and to expose to admins."""
    return {
        "agent_enabled": _truthy(os.getenv(AGENT_ENABLED_ENV)),
        "reads_enabled": _truthy(os.getenv(AGENT_READS_ENV)),
        "writes_enabled": _truthy(os.getenv(AGENT_WRITES_ENV)),
        "writes_kill_switch": _truthy(os.getenv(AGENT_KILL_SWITCH_ENV)),
        "capability_allowlist": sorted(_id_set(os.getenv(AGENT_ALLOWLIST_ENV, ""))),
        "capability_denylist": sorted(_id_set(os.getenv(AGENT_DENYLIST_ENV, ""))),
        "qa_cohort_configured": bool(_id_set(os.getenv(AGENT_QA_USERS_ENV, ""))),
    }


def user_enabled(user_id: int | None) -> bool:
    """Whether this account is inside the agent cohort.

    Requires both the master flag and explicit membership. There is no "empty list
    means everyone" behaviour, because that turns a missing environment variable
    into a full production rollout.
    """
    if not _truthy(os.getenv(AGENT_ENABLED_ENV)) or not int(user_id or 0):
        return False
    return str(int(user_id)) in {part for part in _id_set(os.getenv(AGENT_QA_USERS_ENV, "")) if part.isdigit()}


def capability_enabled(capability_id: str) -> bool:
    """Whether one capability is currently permitted to run at all."""
    cid = clean(capability_id, 120)
    if cid in _id_set(os.getenv(AGENT_DENYLIST_ENV, "")):
        return False
    allowlist = _id_set(os.getenv(AGENT_ALLOWLIST_ENV, ""))
    return cid in allowlist if allowlist else True


def writes_available() -> bool:
    """Whether any agent write may run right now."""
    if _truthy(os.getenv(AGENT_KILL_SWITCH_ENV)):
        return False
    return _truthy(os.getenv(AGENT_WRITES_ENV))


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


def _deny(spec: CapabilitySpec, reason: str, message: str,
          outcome: str = AgentOutcome.PERMISSION_DENIED, **details: Any) -> Decision:
    return Decision(
        verdict=DENY, capability_id=spec.capability_id, risk=spec.risk,
        reason=reason, message=message, outcome=outcome, details=details,
    )


def evaluate(
    user_id: int,
    spec: CapabilitySpec,
    arguments: dict[str, Any],
    *,
    explicit_request: bool = False,
    resolved_resource_count: int = 1,
) -> Decision:
    """Decide whether this user may run this capability with these arguments now.

    ``explicit_request`` and ``resolved_resource_count`` are the only inputs derived
    from the user's message, and they can only ever *tighten* the outcome or satisfy
    a contextual confirmation — never bypass an ``ALWAYS`` policy, a disabled flag or
    the high-risk bar.
    """
    # 1. High risk is unreachable. No flag, cohort or approval unlocks it; these
    #    actions need a separately reviewed workflow, not an agent shortcut.
    if spec.risk == RiskLevel.HIGH_RISK:
        return _deny(spec, "high_risk_unavailable",
                     "That action is too sensitive for UNDX to perform. Do it yourself in PulseSoc.")

    # 2. Cohort and master switch.
    if not user_enabled(user_id):
        return _deny(spec, "agent_disabled_for_user",
                     "UNDX actions are not enabled for this account yet.")

    # 3. Per-capability withdrawal.
    if not capability_enabled(spec.capability_id):
        return _deny(spec, "capability_disabled", "UNDX cannot do that right now.")

    # 4. Read/write gates, evaluated separately so reads survive a write incident.
    if spec.is_write:
        if not writes_available():
            return _deny(spec, "writes_disabled",
                         "UNDX is currently read-only. It can look things up but not change them.")
    elif not _truthy(os.getenv(AGENT_READS_ENV)):
        return _deny(spec, "reads_disabled", "UNDX cannot look that up right now.")

    # 5. Ambiguity is refused rather than guessed. Acting on "pause my alert" when
    #    three alerts match would be a coin flip against the user's data.
    if spec.is_write and int(resolved_resource_count) != 1:
        return _deny(
            spec,
            "ambiguous_resource" if int(resolved_resource_count) > 1 else "resource_unresolved",
            "UNDX needs to know exactly which one you mean before it changes anything."
            if int(resolved_resource_count) > 1
            else "UNDX could not find the item you meant.",
            outcome=AgentOutcome.TERMINAL_FAILURE,
            matches=int(resolved_resource_count),
        )

    # 6. Confirmation policy.
    if spec.confirmation == ConfirmationPolicy.ALWAYS:
        return Decision(REQUIRE_CONFIRMATION, spec.capability_id, spec.risk,
                        reason="policy_always")
    if spec.confirmation == ConfirmationPolicy.CONTEXTUAL:
        # An unambiguous, explicitly-phrased instruction against exactly one
        # resolved resource is its own approval. Anything vaguer gets a card.
        if explicit_request and int(resolved_resource_count) == 1:
            return Decision(ALLOW, spec.capability_id, spec.risk, reason="explicit_single_resource")
        return Decision(REQUIRE_CONFIRMATION, spec.capability_id, spec.risk,
                        reason="ambiguous_or_implicit")

    return Decision(ALLOW, spec.capability_id, spec.risk, reason="read_only_or_no_confirmation")


def classify_risk(spec: CapabilitySpec) -> str:
    """The authoritative risk class for a capability. Registry-owned, not inferred."""
    return spec.risk


__all__ = [
    "Decision", "ALLOW", "REQUIRE_CONFIRMATION", "DENY",
    "evaluate", "classify_risk", "flags", "user_enabled",
    "capability_enabled", "writes_available",
    "AGENT_ENABLED_ENV", "AGENT_READS_ENV", "AGENT_WRITES_ENV",
    "AGENT_KILL_SWITCH_ENV", "AGENT_ALLOWLIST_ENV", "AGENT_DENYLIST_ENV",
    "AGENT_QA_USERS_ENV",
]
