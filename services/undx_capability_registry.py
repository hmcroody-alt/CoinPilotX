"""The canonical, machine-readable registry of what UNDX is allowed to do.

This registry is the allowlist. A capability that is not declared here cannot be
planned, cannot be confirmed, and cannot be executed — the gateway looks up every
call by ``capability_id`` and refuses anything it does not find. That is the single
property that makes "the model proposed a tool call" safe: proposing is free,
but only declared capabilities are reachable.

Each entry carries everything the runtime needs to make a decision without asking
a language model: the risk class, whether an approval is required, the exact
argument schema, how ownership is enforced, how the result is independently
verified, which native screen the user should land on, and which card renders it.

Registering a capability whose executor is not finished is worse than not
registering it, because the runtime would then promise the user an action it
cannot perform. Unfinished work stays out and surfaces as
``unsupported_capability``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from services.undx_agent_contracts import (
    AgentError,
    CardType,
    ConfirmationPolicy,
    FieldSpec,
    RiskLevel,
    clean,
)


@dataclass(frozen=True)
class CapabilitySpec:
    """One declared, executable PulseSoc action."""

    capability_id: str
    description: str
    intents: tuple[str, ...]
    risk: str
    confirmation: str
    tool_name: str
    permission: str
    fields: tuple[FieldSpec, ...]
    executor: str            # dotted name resolved lazily in services.undx_agent_tools
    verifier: str            # name resolved lazily in services.undx_verification ("" = read-only)
    native_route: str
    result_card: str
    audit_category: str
    undo_capability_id: str = ""
    requires_authentication: bool = True
    failure_behavior: str = "report_and_stop"
    idempotent: bool = True

    def __post_init__(self) -> None:
        # Fail at import time rather than at execution time. A malformed capability
        # is a deployment defect, and discovering it when a user asks for it means
        # discovering it in production.
        if self.risk not in RiskLevel.ALL:
            raise ValueError(f"{self.capability_id}: unknown risk class {self.risk!r}")
        if self.confirmation not in ConfirmationPolicy.ALL:
            raise ValueError(f"{self.capability_id}: unknown confirmation policy {self.confirmation!r}")
        if self.result_card not in CardType.ALL:
            raise ValueError(f"{self.capability_id}: unknown result card {self.result_card!r}")
        if RiskLevel.is_write(self.risk) and not self.verifier:
            raise ValueError(f"{self.capability_id}: a write capability must declare a verifier")
        if self.risk == RiskLevel.CONSEQUENTIAL_WRITE and self.confirmation != ConfirmationPolicy.ALWAYS:
            raise ValueError(f"{self.capability_id}: consequential writes require explicit confirmation")

    @property
    def is_write(self) -> bool:
        return RiskLevel.is_write(self.risk)

    def deep_link(self, arguments: dict[str, Any] | None = None) -> str:
        """Resolve the native route, substituting ``:params`` from arguments."""
        route = self.native_route
        for key, value in (arguments or {}).items():
            route = route.replace(f":{key}", clean(value, 60))
        # Drop any optional placeholder the caller did not supply.
        return re.sub(r"/:[A-Za-z_]+\??", "", route).rstrip("/") or "/"


# ---------------------------------------------------------------------------
# Shared field schemas
# ---------------------------------------------------------------------------

_ALERT_ID = FieldSpec("alert_id", "int", required=True, minimum=1)

# The vocabulary UNDX offers a person. These are app-surface words, not storage keys —
# ``undx_agent_tools.CATEGORY_ALIASES`` translates each one into the category the
# notification store actually keeps, and ``test_notification_categories_are_real``
# fails if any entry here has no real category behind it.
NOTIFICATION_CATEGORIES = ("global", "posts", "messages", "reels", "calls", "alerts")

_NOTIFICATION_CATEGORY = FieldSpec(
    "category", "enum", required=True, choices=NOTIFICATION_CATEGORIES,
)
_PUSH_VALUE = FieldSpec("push", "bool", required=True)

_ALERT_CONDITIONS = ("above", "below", "moves_up_percent", "moves_down_percent", "volatility_above")


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------

REGISTRY: dict[str, CapabilitySpec] = {}


def _register(spec: CapabilitySpec) -> CapabilitySpec:
    if spec.capability_id in REGISTRY:
        raise ValueError(f"duplicate capability {spec.capability_id}")
    REGISTRY[spec.capability_id] = spec
    return spec


# --- Crypto alerts ---------------------------------------------------------

_register(CapabilitySpec(
    capability_id="crypto.alerts.list",
    description="List the authenticated user's crypto alerts",
    # Question forms are listed alongside imperatives on purpose. "What alerts do I
    # have" is the single most common way this is actually asked, and an intent list
    # containing only commands recognises the phrasing of a test fixture rather than
    # the phrasing of a person.
    intents=("my alerts", "list alerts", "show alerts", "active alert", "crypto alert",
             "what alerts", "which alerts", "any alerts", "alerts do i have"),
    risk=RiskLevel.READ_ONLY,
    confirmation=ConfirmationPolicy.NEVER,
    tool_name="pulsesoc.crypto_alerts.list",
    permission="self_account_only",
    fields=(FieldSpec("limit", "int", required=False, minimum=1, maximum=50, default=20),),
    executor="crypto_alerts_list",
    verifier="",
    native_route="/pulse/crypto/alerts",
    result_card=CardType.CRYPTO_ALERT_CARD,
    audit_category="crypto_alerts_read",
))

_register(CapabilitySpec(
    capability_id="crypto.alerts.get",
    description="Retrieve one crypto alert owned by the authenticated user",
    intents=("show alert", "alert details", "that alert"),
    risk=RiskLevel.READ_ONLY,
    confirmation=ConfirmationPolicy.NEVER,
    tool_name="pulsesoc.crypto_alerts.get",
    permission="self_account_only",
    fields=(_ALERT_ID,),
    executor="crypto_alerts_get",
    verifier="",
    native_route="/pulse/alerts/:alert_id",
    result_card=CardType.CRYPTO_ALERT_CARD,
    audit_category="crypto_alerts_read",
))

_register(CapabilitySpec(
    capability_id="crypto.alerts.pause",
    description="Pause one crypto alert so it stops triggering",
    intents=("pause alert", "stop alert", "mute alert", "turn off alert"),
    risk=RiskLevel.REVERSIBLE_WRITE,
    confirmation=ConfirmationPolicy.CONTEXTUAL,
    tool_name="pulsesoc.crypto_alerts.pause",
    permission="self_account_only",
    fields=(_ALERT_ID,),
    executor="crypto_alerts_pause",
    verifier="crypto_alert_status",
    native_route="/pulse/alerts/:alert_id",
    result_card=CardType.CRYPTO_ALERT_CARD,
    audit_category="crypto_alerts_write",
    undo_capability_id="crypto.alerts.resume",
))

_register(CapabilitySpec(
    capability_id="crypto.alerts.resume",
    description="Resume one paused crypto alert",
    intents=("resume alert", "restart alert", "reactivate alert", "turn on alert"),
    risk=RiskLevel.REVERSIBLE_WRITE,
    confirmation=ConfirmationPolicy.CONTEXTUAL,
    tool_name="pulsesoc.crypto_alerts.resume",
    permission="self_account_only",
    fields=(_ALERT_ID,),
    executor="crypto_alerts_resume",
    verifier="crypto_alert_status",
    native_route="/pulse/alerts/:alert_id",
    result_card=CardType.CRYPTO_ALERT_CARD,
    audit_category="crypto_alerts_write",
    undo_capability_id="crypto.alerts.pause",
))

_register(CapabilitySpec(
    capability_id="crypto.alerts.create",
    description="Create a crypto price alert that can notify external channels",
    intents=("create alert", "new alert", "alert me when", "notify me when"),
    # Consequential rather than reversible: a new alert can fire push, email and
    # SMS to the user and is therefore observable outside PulseSoc.
    risk=RiskLevel.CONSEQUENTIAL_WRITE,
    confirmation=ConfirmationPolicy.ALWAYS,
    tool_name="pulsesoc.crypto_alerts.create",
    permission="self_account_only",
    fields=(
        FieldSpec("symbol", "identifier", required=True, max_length=24),
        FieldSpec("condition", "enum", required=True, choices=_ALERT_CONDITIONS),
        FieldSpec("threshold", "float", required=True, minimum=0.0000001, maximum=1_000_000_000.0),
    ),
    executor="crypto_alerts_create",
    verifier="crypto_alert_exists",
    native_route="/pulse/crypto/alerts",
    result_card=CardType.CRYPTO_ALERT_CARD,
    audit_category="crypto_alerts_write",
    undo_capability_id="crypto.alerts.delete",
    idempotent=False,
))

_register(CapabilitySpec(
    capability_id="crypto.alerts.update",
    description="Change the threshold or condition of an existing crypto alert",
    intents=("change alert", "update alert", "edit alert", "move alert"),
    risk=RiskLevel.CONSEQUENTIAL_WRITE,
    confirmation=ConfirmationPolicy.ALWAYS,
    tool_name="pulsesoc.crypto_alerts.update",
    permission="self_account_only",
    fields=(
        _ALERT_ID,
        FieldSpec("threshold", "float", required=True, minimum=0.0000001, maximum=1_000_000_000.0),
        FieldSpec("condition", "enum", required=False, choices=_ALERT_CONDITIONS),
    ),
    executor="crypto_alerts_update",
    verifier="crypto_alert_threshold",
    native_route="/pulse/alerts/:alert_id",
    result_card=CardType.CRYPTO_ALERT_CARD,
    audit_category="crypto_alerts_write",
))

_register(CapabilitySpec(
    capability_id="crypto.alerts.delete",
    description="Delete one crypto alert owned by the authenticated user",
    intents=("delete alert", "remove alert", "get rid of alert"),
    risk=RiskLevel.CONSEQUENTIAL_WRITE,
    confirmation=ConfirmationPolicy.ALWAYS,
    tool_name="pulsesoc.crypto_alerts.delete",
    permission="self_account_only",
    fields=(_ALERT_ID,),
    executor="crypto_alerts_delete",
    verifier="crypto_alert_deleted",
    native_route="/pulse/crypto/alerts",
    result_card=CardType.CRYPTO_ALERT_CARD,
    audit_category="crypto_alerts_write",
))


# --- Notification preferences ---------------------------------------------

_register(CapabilitySpec(
    capability_id="notifications.preference.read",
    description="Read the authenticated user's notification preferences",
    intents=("notification settings", "my notifications", "notification preference"),
    risk=RiskLevel.READ_ONLY,
    confirmation=ConfirmationPolicy.NEVER,
    tool_name="pulsesoc.notification_preferences.read",
    permission="self_account_only",
    fields=(FieldSpec("category", "enum", required=False,
                      choices=NOTIFICATION_CATEGORIES, default="global"),),
    executor="notification_preferences_read",
    verifier="",
    native_route="/pulse/settings/notifications",
    result_card=CardType.SETTING_CHANGE_RECEIPT,
    audit_category="notification_preferences_read",
))

_register(CapabilitySpec(
    capability_id="notifications.preference.update",
    description="Update one authenticated-user notification preference",
    intents=("turn off notifications", "turn on notifications",
             "enable notifications", "disable notifications"),
    risk=RiskLevel.REVERSIBLE_WRITE,
    # Kept at ALWAYS rather than CONTEXTUAL: this capability already shipped behind
    # an unconditional confirmation, and relaxing it here would silently remove an
    # approval step users have already been trained to expect.
    confirmation=ConfirmationPolicy.ALWAYS,
    tool_name="pulsesoc.notification_preferences.update",
    permission="self_account_only",
    fields=(_NOTIFICATION_CATEGORY, _PUSH_VALUE),
    executor="notification_preferences_update",
    verifier="notification_preference_value",
    native_route="/pulse/settings/notifications",
    result_card=CardType.SETTING_CHANGE_RECEIPT,
    audit_category="notification_preferences_write",
    undo_capability_id="notifications.preference.update",
))


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------


def get(capability_id: str) -> CapabilitySpec | None:
    return REGISTRY.get(clean(capability_id, 120))


def require(capability_id: str) -> CapabilitySpec:
    """Look up a capability or raise the typed unsupported outcome.

    Everything that executes goes through here, so an attacker who convinces the
    model to emit ``{"capability_id": "account.delete"}`` gets a polite refusal
    rather than a dispatch.
    """
    spec = get(capability_id)
    if spec is None:
        from services.undx_agent_contracts import AgentOutcome

        raise AgentError(
            "unsupported_capability",
            "UNDX cannot do that yet.",
            outcome=AgentOutcome.UNSUPPORTED_CAPABILITY,
            details={"capability_id": clean(capability_id, 120)},
        )
    return spec


def capability_ids() -> list[str]:
    return sorted(REGISTRY)


def write_capability_ids() -> list[str]:
    return sorted(cid for cid, spec in REGISTRY.items() if spec.is_write)


def category_choices() -> tuple[str, ...]:
    """The notification categories UNDX will accept from a person."""
    return NOTIFICATION_CATEGORIES


def describe_for_model() -> list[dict[str, Any]]:
    """The bounded description handed to the planner.

    Executors, verifiers and permission internals are omitted. The model needs to
    know what it may ask for, not how the request will be carried out.
    """
    return [
        {
            "capability_id": spec.capability_id,
            "description": spec.description,
            "risk": spec.risk,
            "confirmation": spec.confirmation,
            "arguments": [
                {
                    "name": item.name,
                    "type": item.kind,
                    "required": item.required,
                    **({"choices": list(item.choices)} if item.choices else {}),
                }
                for item in spec.fields
            ],
        }
        for spec in sorted(REGISTRY.values(), key=lambda item: item.capability_id)
    ]


__all__ = [
    "CapabilitySpec", "REGISTRY", "get", "require",
    "capability_ids", "write_capability_ids", "describe_for_model",
    "NOTIFICATION_CATEGORIES", "category_choices",
]
