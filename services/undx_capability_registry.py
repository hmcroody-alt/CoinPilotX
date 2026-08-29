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
    PermissionScope,
    RiskLevel,
    clean,
)

#: Characters a substituted deep-link segment may contain. A route is a destination
#: the client navigates to, and every value substituted into one ultimately traces
#: back to something a language model proposed. Restricting the alphabet — rather
#: than trusting that today's fields happen to be integers and enums — is what stops
#: a future free-text field from putting ``/`` or ``..`` into a navigation target.
_ROUTE_SAFE = re.compile(r"[^A-Za-z0-9_.\-]")


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
    #: The field naming the thing this capability acts on. It identifies the resource
    #: in the confirmation card and is half of the idempotency key, so a capability
    #: that omits it gets an unnamed confirmation ("approve this change to *what*?")
    #: and an idempotency key that collides with every other call in the same request.
    #: Required for writes; the constructor refuses to register one without it.
    target_field: str = ""
    #: Which declared fields the verifier actually reads back. Every mutable field a
    #: capability accepts must appear here, because a field the verifier ignores is a
    #: field the user can change while being told the change was "verified".
    verified_fields: tuple[str, ...] = ()
    undo_capability_id: str = ""
    #: How to build the arguments for ``undo_capability_id`` from this call.
    #:
    #: Naming an undo capability is not the same as knowing how to invoke it, and
    #: the difference is not cosmetic. ``notifications.preference.update`` undoes
    #: itself: replaying the stored arguments re-applies the change rather than
    #: reversing it, so a card offering Undo would leave the setting exactly where
    #: the user just asked it not to be. ``crypto.alerts.create`` undoes with
    #: ``delete``, which needs the id of the row that was just created — a value
    #: that does not appear in the arguments at all.
    #:
    #: Each entry maps a field of the *undo* capability to a source:
    #:   ``"name"``    this call's ``name`` argument, unchanged
    #:   ``"!name"``   this call's ``name`` argument, logically negated
    #:   ``"@target"`` the canonical resource id the action produced
    #:
    #: An undo capability with no map is invoked with this call's arguments as they
    #: stand, which is right only when the two capabilities share a schema — pause
    #: and resume both take ``alert_id``. ``_validate_undo_graph`` checks that.
    undo_argument_map: tuple[tuple[str, str], ...] = ()
    requires_authentication: bool = True
    failure_behavior: str = "report_and_stop"
    idempotent: bool = True

    def __post_init__(self) -> None:
        # Fail at import time rather than at execution time. A malformed capability
        # is a deployment defect, and discovering it when a user asks for it means
        # discovering it in production.
        if self.risk not in RiskLevel.ALL:
            raise ValueError(f"{self.capability_id}: unknown risk class {self.risk!r}")
        if self.permission not in PermissionScope.ALL:
            raise ValueError(f"{self.capability_id}: unknown permission scope {self.permission!r}")
        if self.confirmation not in ConfirmationPolicy.ALL:
            raise ValueError(f"{self.capability_id}: unknown confirmation policy {self.confirmation!r}")
        if self.result_card not in CardType.ALL:
            raise ValueError(f"{self.capability_id}: unknown result card {self.result_card!r}")
        declared = {item.name for item in self.fields}
        if self.target_field and self.target_field not in declared:
            raise ValueError(f"{self.capability_id}: target_field {self.target_field!r} is not a declared field")
        if not self.is_write:
            return
        if not self.verifier:
            raise ValueError(f"{self.capability_id}: a write capability must declare a verifier")
        if not self.target_field:
            raise ValueError(f"{self.capability_id}: a write capability must name its target_field")
        if self.risk == RiskLevel.CONSEQUENTIAL_WRITE and self.confirmation != ConfirmationPolicy.ALWAYS:
            raise ValueError(f"{self.capability_id}: consequential writes require explicit confirmation")
        # Everything the capability can change, minus the identifier of the thing being
        # changed, must be read back. Declaring this per capability rather than inferring
        # it means adding a field to an existing write is a compile-time decision about
        # verification, not a silent widening of what "verified" covers.
        mutable = declared - {self.target_field}
        unverified = sorted(mutable - set(self.verified_fields))
        if unverified:
            raise ValueError(
                f"{self.capability_id}: fields {unverified} can be changed but are not "
                f"listed in verified_fields, so a change to them would be reported as verified"
            )

    @property
    def is_write(self) -> bool:
        return RiskLevel.is_write(self.risk)

    def canonical_target(self, arguments: dict[str, Any] | None = None) -> str:
        """The identifier of the resource this call acts on, as a bare string.

        Generic machinery — the confirmation card, the idempotency key — needs to name
        the target without knowing what kind of capability this is. Reading it from a
        declared field rather than from a hardcoded list of argument names is what lets
        a new pack be added without editing the gateway, and what stops a capability
        whose target happens to be called something else from silently reducing to an
        empty target that collides with every other call.
        """
        if not self.target_field:
            return ""
        return clean((arguments or {}).get(self.target_field), 200)

    def undo_arguments(
        self,
        arguments: dict[str, Any] | None = None,
        canonical_ids: list[str] | None = None,
    ) -> dict[str, Any] | None:
        """The argument set that would reverse this call, or ``None`` if there is none.

        ``None`` and ``{}`` mean different things here. ``None`` is "this call cannot
        be undone" and must clear the undo affordance entirely; an empty mapping would
        be a capability that takes no arguments, which no write currently is.

        The failure this returns ``None`` for is the one worth naming: a call whose
        undo needs the created resource's id, made against a result that has no
        canonical id because the write was not verified. Offering Undo there would
        send a delete with a blank target. Withholding the button is the honest
        outcome, and the receipt still records that an undo capability exists.
        """
        if not self.undo_capability_id:
            return None
        source = dict(arguments or {})
        if not self.undo_argument_map:
            return source
        resolved: dict[str, Any] = {}
        for field_name, token in self.undo_argument_map:
            if token == "@target":
                first = next((clean(item, 200) for item in (canonical_ids or []) if clean(item, 200)), "")
                if not first:
                    return None
                # Canonical ids are namespaced paths — ``alert_rule:42``, not ``42``.
                # The undo capability declares a typed field, so handing it the whole
                # path would fail coercion at the boundary. The local identifier is
                # the final segment, which is the only part the owning service knows.
                resolved[field_name] = first.rsplit(":", 1)[-1]
            elif token.startswith("!"):
                if token[1:] not in source:
                    return None
                resolved[field_name] = not bool(source[token[1:]])
            else:
                if token not in source:
                    return None
                resolved[field_name] = source[token]
        return resolved

    def deep_link(self, arguments: dict[str, Any] | None = None) -> str:
        """Resolve the native route, substituting ``:params`` from arguments."""
        route = self.native_route
        values = dict(arguments or {})
        if ":profileKey" in route and "profileKey" not in values and values.get("target_user_id"):
            values["profileKey"] = values["target_user_id"]
        for key, value in values.items():
            route = route.replace(f":{key}", _ROUTE_SAFE.sub("", clean(value, 60)))
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
_POST_ID = FieldSpec("post_id", "int", required=True, minimum=1)
_REEL_ID = FieldSpec("reel_id", "int", required=True, minimum=1)
_STATUS_ID = FieldSpec("status_id", "int", required=True, minimum=1)
_SAVED_VALUE = FieldSpec("saved", "bool", required=True)
_TARGET_USER_ID = FieldSpec("target_user_id", "int", required=True, minimum=1)
_CONVERSATION_ID = FieldSpec("conversation_id", "int", required=True, minimum=1)
_SAVED_CONTENT_TYPES = (
    "all", "post", "reel", "status", "marketplace", "video", "room", "group",
    "teacher", "image", "learning",
)
_CONVERSATION_TYPES = ("all", "direct", "group", "room", "community_channel")
_FEED_TYPES = ("for_you", "following", "trending", "my_posts", "crypto", "questions")

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
             "what alerts", "which alerts", "any alerts", "alerts do i have",
             "every alert", "is there an alert", "alerts i configured"),
    risk=RiskLevel.READ_ONLY,
    confirmation=ConfirmationPolicy.NEVER,
    tool_name="pulsesoc.crypto_alerts.list",
    permission=PermissionScope.SELF_ACCOUNT_ONLY,
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
    intents=("show alert", "alert details", "that alert", "open alert",
             "expand alert", "what does alert", "tell me about alert"),
    risk=RiskLevel.READ_ONLY,
    confirmation=ConfirmationPolicy.NEVER,
    tool_name="pulsesoc.crypto_alerts.get",
    permission=PermissionScope.SELF_ACCOUNT_ONLY,
    fields=(_ALERT_ID,),
    executor="crypto_alerts_get",
    verifier="",
    native_route="/pulse/alerts/:alert_id",
    result_card=CardType.CRYPTO_ALERT_CARD,
    audit_category="crypto_alerts_read",
    target_field="alert_id",
))

_register(CapabilitySpec(
    capability_id="crypto.alerts.pause",
    description="Pause one crypto alert so it stops triggering",
    intents=("pause alert", "stop alert", "mute alert", "turn off alert",
             "silence alert", "snooze alert", "alert on hold"),
    risk=RiskLevel.REVERSIBLE_WRITE,
    confirmation=ConfirmationPolicy.CONTEXTUAL,
    tool_name="pulsesoc.crypto_alerts.pause",
    permission=PermissionScope.SELF_ACCOUNT_ONLY,
    fields=(_ALERT_ID,),
    executor="crypto_alerts_pause",
    verifier="crypto_alert_status",
    native_route="/pulse/alerts/:alert_id",
    result_card=CardType.CRYPTO_ALERT_CARD,
    audit_category="crypto_alerts_write",
    target_field="alert_id",
    undo_capability_id="crypto.alerts.resume",
))

_register(CapabilitySpec(
    capability_id="crypto.alerts.resume",
    description="Resume one paused crypto alert",
    intents=("resume alert", "restart alert", "reactivate alert", "turn on alert",
             "switch alert back on", "alert running again", "bring alert back"),
    risk=RiskLevel.REVERSIBLE_WRITE,
    confirmation=ConfirmationPolicy.CONTEXTUAL,
    tool_name="pulsesoc.crypto_alerts.resume",
    permission=PermissionScope.SELF_ACCOUNT_ONLY,
    fields=(_ALERT_ID,),
    executor="crypto_alerts_resume",
    verifier="crypto_alert_status",
    native_route="/pulse/alerts/:alert_id",
    result_card=CardType.CRYPTO_ALERT_CARD,
    audit_category="crypto_alerts_write",
    target_field="alert_id",
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
    permission=PermissionScope.SELF_ACCOUNT_ONLY,
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
    # A creation has no pre-existing row to name, so the target is the thing being
    # created *about* — the symbol. That keeps two different "alert me on BTC" and
    # "alert me on ETH" requests in one message from sharing an idempotency key.
    target_field="symbol",
    verified_fields=("condition", "threshold"),
    undo_capability_id="crypto.alerts.delete",
    # Deleting the alert that was just created needs its row id, and the arguments
    # carry only the symbol. The id exists solely in the verified result, so an
    # unverified creation yields no undo rather than a delete aimed at nothing.
    undo_argument_map=(("alert_id", "@target"),),
    idempotent=False,
))

_register(CapabilitySpec(
    capability_id="crypto.alerts.update",
    description="Change the threshold or condition of an existing crypto alert",
    intents=("change alert", "update alert", "edit alert", "move alert"),
    risk=RiskLevel.CONSEQUENTIAL_WRITE,
    confirmation=ConfirmationPolicy.ALWAYS,
    tool_name="pulsesoc.crypto_alerts.update",
    permission=PermissionScope.SELF_ACCOUNT_ONLY,
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
    target_field="alert_id",
    verified_fields=("threshold", "condition"),
))

_register(CapabilitySpec(
    capability_id="crypto.alerts.delete",
    description="Delete one crypto alert owned by the authenticated user",
    intents=("delete alert", "remove alert", "get rid of alert"),
    risk=RiskLevel.CONSEQUENTIAL_WRITE,
    confirmation=ConfirmationPolicy.ALWAYS,
    tool_name="pulsesoc.crypto_alerts.delete",
    permission=PermissionScope.SELF_ACCOUNT_ONLY,
    fields=(_ALERT_ID,),
    executor="crypto_alerts_delete",
    verifier="crypto_alert_deleted",
    native_route="/pulse/crypto/alerts",
    result_card=CardType.CRYPTO_ALERT_CARD,
    audit_category="crypto_alerts_write",
    target_field="alert_id",
))


# --- Crypto intelligence ---------------------------------------------------
#
# Read-only premium crypto surfaces. The premium gate itself lives in the
# executors (services.crypto_premium_gate): a locked capability still resolves
# and runs, and returns the honest premium_required payload so the model can
# explain the upsell instead of pretending the feature does not exist.

_PORTFOLIO_PERIODS = ("24h", "7d", "30d", "90d", "1y", "all")

_register(CapabilitySpec(
    capability_id="crypto.portfolio.summary",
    description="Current valuation of the authenticated user's crypto portfolio "
                "(premium; locked accounts get an upgrade notice, never invented numbers)",
    # Every phrasing names the operation — "what is it worth", "summarize it",
    # "break it down". None of them is a bare noun phrase like "my portfolio",
    # and that omission is deliberate: "my portfolio" is a substring of most
    # sentences a member will ever write about their portfolio, including
    # "why is my portfolio down this week". That question is *causal and
    # historical*, and this capability cannot answer it — holdings are not
    # versioned and the observation series samples symbols rather than
    # portfolios, so there is no record of what was held when the week opened.
    # Claiming the turn anyway does not produce a wrong answer, it produces an
    # empty one: the agent handles it, the model never sees it, and the member
    # gets silence. Analytical questions must fall through to the provider.
    intents=("portfolio summary", "portfolio value",
             "what is my portfolio worth", "how is my portfolio doing",
             "portfolio breakdown", "my crypto holdings"),
    risk=RiskLevel.READ_ONLY,
    confirmation=ConfirmationPolicy.NEVER,
    tool_name="pulsesoc.crypto_portfolio.summary",
    permission=PermissionScope.SELF_ACCOUNT_ONLY,
    fields=(),
    executor="crypto_portfolio_summary",
    verifier="",
    # /pulse/portfolio, not /pulse/intelligence/:subsystem? — the latter is not
    # a route this app serves. The duplicate spec removed below carried the
    # right destination and the wrong tool name; this one was the reverse, so
    # each half is taken from whichever side was checkable. undx_knowledge_map
    # is what catches this: it declares Portfolio -> /pulse/portfolio and
    # refuses to build a capability whose native_route disagrees.
    native_route="/pulse/portfolio",
    result_card=CardType.CONTENT_RESULT,
    audit_category="crypto_portfolio_read",
))

_register(CapabilitySpec(
    capability_id="crypto.portfolio.history",
    description="Portfolio value history over a chosen period "
                "(premium; locked accounts get an upgrade notice)",
    intents=("portfolio history", "portfolio over time", "portfolio performance",
             "how has my portfolio changed", "portfolio chart",
             "portfolio last week", "portfolio this month"),
    risk=RiskLevel.READ_ONLY,
    confirmation=ConfirmationPolicy.NEVER,
    tool_name="pulsesoc.crypto_portfolio.history",
    permission=PermissionScope.SELF_ACCOUNT_ONLY,
    fields=(
        FieldSpec("period", "enum", required=False,
                  choices=_PORTFOLIO_PERIODS, default="30d"),
    ),
    executor="crypto_portfolio_history",
    verifier="",
    native_route="/pulse/intelligence/:subsystem?",
    result_card=CardType.CONTENT_RESULT,
    audit_category="crypto_portfolio_read",
))

_register(CapabilitySpec(
    capability_id="crypto.alerts.activity",
    description="The user's crypto alert rules plus recent trigger history "
                "(rule list is free; trigger detail is premium)",
    intents=("alert history", "which alerts fired", "alert triggers",
             "recent alert activity", "did my alert trigger",
             "alert activity", "when did my alert go off"),
    risk=RiskLevel.READ_ONLY,
    confirmation=ConfirmationPolicy.NEVER,
    tool_name="pulsesoc.crypto_alerts.activity",
    permission=PermissionScope.SELF_ACCOUNT_ONLY,
    fields=(
        FieldSpec("alert_id", "int", required=False, minimum=1),
        FieldSpec("limit", "int", required=False, minimum=1, maximum=50, default=20),
    ),
    executor="crypto_alerts_activity",
    verifier="",
    native_route="/pulse/crypto/alerts",
    result_card=CardType.CRYPTO_ALERT_CARD,
    audit_category="crypto_alerts_read",
))

_register(CapabilitySpec(
    capability_id="crypto.market.observations",
    description="Recent sampled market observations (price, volume, market cap) "
                "for one crypto asset",
    intents=("recent price", "price history", "market observations",
             "how has bitcoin moved", "recent market data", "price samples",
             "volume history"),
    risk=RiskLevel.READ_ONLY,
    confirmation=ConfirmationPolicy.NEVER,
    tool_name="pulsesoc.crypto_market.observations",
    permission=PermissionScope.SELF_ACCOUNT_ONLY,
    fields=(
        FieldSpec("asset_id", "identifier", required=True, max_length=60),
        FieldSpec("limit", "int", required=False, minimum=1, maximum=100, default=24),
    ),
    executor="crypto_market_observations",
    verifier="",
    native_route="/pulse/intelligence/:subsystem?",
    result_card=CardType.CONTENT_RESULT,
    audit_category="crypto_market_read",
))


# --- Saved content ---------------------------------------------------------

_register(CapabilitySpec(
    capability_id="saved.items.list",
    description="List the authenticated user's private Saved library",
    intents=("find my saved posts", "show my saved posts", "find my saved reels",
             "my saved posts", "my saved items", "my saved library",
             "show my saved items", "what have i saved", "my saved content"),
    risk=RiskLevel.READ_ONLY,
    confirmation=ConfirmationPolicy.NEVER,
    tool_name="pulsesoc.saved_items.list",
    permission=PermissionScope.SELF_ACCOUNT_ONLY,
    fields=(
        FieldSpec("content_type", "enum", required=False,
                  choices=_SAVED_CONTENT_TYPES, default="all"),
        FieldSpec("query", "str", required=False, max_length=120, default=""),
        FieldSpec("limit", "int", required=False, minimum=1, maximum=50, default=20),
    ),
    executor="saved_items_list",
    verifier="",
    native_route="/pulse/saved",
    result_card=CardType.CONTENT_RESULT,
    audit_category="saved_content_read",
))

_register(CapabilitySpec(
    capability_id="saved.post.set",
    description="Save or unsave one PulseSoc post for the authenticated user",
    intents=("save post", "save this post", "unsave post", "remove post from saved"),
    risk=RiskLevel.REVERSIBLE_WRITE,
    confirmation=ConfirmationPolicy.NEVER,
    tool_name="pulsesoc.saved_posts.set",
    permission=PermissionScope.SELF_ACCOUNT_ONLY,
    fields=(_POST_ID, _SAVED_VALUE),
    executor="saved_post_set",
    verifier="saved_post_value",
    native_route="/pulse/post/:post_id",
    result_card=CardType.ACTION_SUCCESS_RECEIPT,
    audit_category="saved_content_write",
    target_field="post_id",
    verified_fields=("saved",),
    undo_capability_id="saved.post.set",
    undo_argument_map=(("post_id", "post_id"), ("saved", "!saved")),
))


# --- Social relationships -------------------------------------------------

_register(CapabilitySpec(
    capability_id="social.followers.list",
    description="List followers or followed accounts for the authenticated user",
    intents=("who follows me", "show my followers", "who am i following",
             "show who i follow", "find my followers", "my followers",
             "who i follow", "my follower list"),
    risk=RiskLevel.READ_ONLY,
    confirmation=ConfirmationPolicy.NEVER,
    tool_name="pulsesoc.relationships.list",
    permission=PermissionScope.SELF_ACCOUNT_ONLY,
    fields=(
        FieldSpec("direction", "enum", required=False,
                  choices=("followers", "following"), default="followers"),
        FieldSpec("query", "str", required=False, max_length=120, default=""),
        FieldSpec("limit", "int", required=False, minimum=1, maximum=50, default=20),
    ),
    executor="social_relationships_list",
    verifier="",
    native_route="/pulse/profile/:profileKey",
    result_card=CardType.PROFILE_RESULT,
    audit_category="social_relationships_read",
))

_register(CapabilitySpec(
    capability_id="social.follow",
    description="Follow one PulseSoc account",
    intents=("follow user", "follow account", "follow member", "start following"),
    risk=RiskLevel.REVERSIBLE_WRITE,
    confirmation=ConfirmationPolicy.NEVER,
    tool_name="pulsesoc.relationships.follow",
    permission=PermissionScope.OTHER_USER_TARGET,
    fields=(_TARGET_USER_ID,),
    executor="social_follow",
    verifier="social_following_value",
    native_route="/pulse/profile/:profileKey",
    result_card=CardType.RELATIONSHIP_CHANGE_RECEIPT,
    audit_category="social_relationships_write",
    target_field="target_user_id",
    verified_fields=(),
    undo_capability_id="social.unfollow",
))

_register(CapabilitySpec(
    capability_id="social.unfollow",
    description="Stop following one PulseSoc account",
    intents=("unfollow user", "unfollow account", "stop following user"),
    risk=RiskLevel.REVERSIBLE_WRITE,
    confirmation=ConfirmationPolicy.NEVER,
    tool_name="pulsesoc.relationships.unfollow",
    permission=PermissionScope.OTHER_USER_TARGET,
    fields=(_TARGET_USER_ID,),
    executor="social_unfollow",
    verifier="social_following_value",
    native_route="/pulse/profile/:profileKey",
    result_card=CardType.RELATIONSHIP_CHANGE_RECEIPT,
    audit_category="social_relationships_write",
    target_field="target_user_id",
    verified_fields=(),
    undo_capability_id="social.follow",
))


# --- Messenger read intelligence -----------------------------------------

_register(CapabilitySpec(
    capability_id="conversations.list",
    description="List the authenticated user's active Messenger conversations",
    intents=("show my chats", "show my conversations", "who messaged me",
             "open messenger conversations", "list my chats", "my conversations",
             "my chats", "what chats"),
    risk=RiskLevel.READ_ONLY,
    confirmation=ConfirmationPolicy.NEVER,
    tool_name="pulsesoc.conversations.list",
    permission=PermissionScope.SELF_ACCOUNT_ONLY,
    fields=(
        FieldSpec("conversation_type", "enum", required=False,
                  choices=_CONVERSATION_TYPES, default="all"),
        FieldSpec("limit", "int", required=False, minimum=1, maximum=50, default=20),
    ),
    executor="conversations_list",
    verifier="",
    native_route="/pulse/messages",
    result_card=CardType.CONVERSATION_RESULT,
    audit_category="messenger_read",
))

_register(CapabilitySpec(
    capability_id="messages.list",
    description="Read messages from one authenticated Messenger membership without marking them read",
    intents=("show messages in conversation", "read conversation", "show conversation messages",
             "read messages from conversation"),
    risk=RiskLevel.READ_ONLY,
    confirmation=ConfirmationPolicy.NEVER,
    tool_name="pulsesoc.messages.list",
    permission=PermissionScope.SELF_ACCOUNT_ONLY,
    fields=(
        _CONVERSATION_ID,
        FieldSpec("limit", "int", required=False, minimum=1, maximum=100, default=30),
    ),
    executor="messages_list",
    verifier="",
    native_route="/pulse/messages/:conversation_id",
    result_card=CardType.CONVERSATION_RESULT,
    audit_category="messenger_read",
))

_register(CapabilitySpec(
    capability_id="messages.search",
    description="Search approved messages inside the authenticated user's active conversations",
    intents=("find the message where", "search conversation for",
             "search this chat for", "in this conversation find",
             "messages in conversation", "message in chat"),
    risk=RiskLevel.READ_ONLY, confirmation=ConfirmationPolicy.NEVER,
    tool_name="pulsesoc.messages.search", permission=PermissionScope.SELF_ACCOUNT_ONLY,
    fields=(
        FieldSpec("query", "str", required=True, max_length=120),
        FieldSpec("conversation_id", "int", required=False, minimum=1, default=0),
        FieldSpec("limit", "int", required=False, minimum=1, maximum=50, default=30),
    ),
    executor="messages_search", verifier="", native_route="/pulse/messages/:conversation_id",
    result_card=CardType.CONVERSATION_RESULT, audit_category="messenger_read",
))

_register(CapabilitySpec(
    capability_id="conversations.summarize",
    description="Summarize a bounded window from one authenticated Messenger membership",
    intents=("summarize conversation", "summarize chat", "what did we discuss in conversation"),
    risk=RiskLevel.READ_ONLY, confirmation=ConfirmationPolicy.NEVER,
    tool_name="pulsesoc.conversations.summarize", permission=PermissionScope.SELF_ACCOUNT_ONLY,
    fields=(
        _CONVERSATION_ID,
        FieldSpec("limit", "int", required=False, minimum=1, maximum=100, default=50),
    ),
    executor="conversation_summarize", verifier="", native_route="/pulse/messages/:conversation_id",
    result_card=CardType.CONVERSATION_RESULT, audit_category="messenger_read",
))

_register(CapabilitySpec(
    capability_id="messages.suggest",
    description="Prepare unsent suggested responses for one authenticated conversation",
    intents=("suggest replies for conversation", "suggest a response", "what should i reply"),
    risk=RiskLevel.READ_ONLY, confirmation=ConfirmationPolicy.NEVER,
    tool_name="pulsesoc.messages.suggest", permission=PermissionScope.SELF_ACCOUNT_ONLY,
    fields=(_CONVERSATION_ID,),
    executor="messages_suggest", verifier="", native_route="/pulse/messages/:conversation_id",
    result_card=CardType.CONVERSATION_RESULT, audit_category="messenger_draft",
))

_register(CapabilitySpec(
    capability_id="messages.draft",
    description="Prepare an unsent reply draft bound to an authenticated conversation",
    intents=("prepare a reply to conversation", "draft a reply to conversation"),
    risk=RiskLevel.READ_ONLY, confirmation=ConfirmationPolicy.NEVER,
    tool_name="pulsesoc.messages.draft", permission=PermissionScope.SELF_ACCOUNT_ONLY,
    fields=(_CONVERSATION_ID, FieldSpec("body", "str", required=True, max_length=2000)),
    executor="message_draft", verifier="", native_route="/pulse/messages/:conversation_id",
    result_card=CardType.CONVERSATION_RESULT, audit_category="messenger_draft",
))


# --- Feed intelligence ----------------------------------------------------

_register(CapabilitySpec(
    capability_id="feed.posts.list",
    description="Find privacy-filtered PulseSoc posts visible to the authenticated user",
    intents=("show my feed", "what's new", "find posts", "search posts",
             "show my latest posts", "show trending posts"),
    risk=RiskLevel.READ_ONLY,
    confirmation=ConfirmationPolicy.NEVER,
    tool_name="pulsesoc.feed.posts.list",
    permission=PermissionScope.SELF_ACCOUNT_ONLY,
    fields=(
        FieldSpec("feed", "enum", required=False, choices=_FEED_TYPES, default="for_you"),
        FieldSpec("query", "str", required=False, max_length=80, default=""),
        FieldSpec("limit", "int", required=False, minimum=1, maximum=40, default=20),
    ),
    executor="feed_posts_list",
    verifier="",
    native_route="/pulse",
    result_card=CardType.CONTENT_RESULT,
    audit_category="feed_read",
))

_register(CapabilitySpec(
    capability_id="feed.posts.get",
    description="Read one PulseSoc post after enforcing its visibility boundary",
    intents=("show post", "open post", "explain post", "post details"),
    risk=RiskLevel.READ_ONLY,
    confirmation=ConfirmationPolicy.NEVER,
    tool_name="pulsesoc.feed.posts.get",
    permission=PermissionScope.SELF_ACCOUNT_ONLY,
    fields=(_POST_ID,),
    executor="feed_posts_get",
    verifier="",
    native_route="/pulse/post/:post_id",
    result_card=CardType.CONTENT_RESULT,
    audit_category="feed_read",
))

_register(CapabilitySpec(
    capability_id="comments.list",
    description="Read comments on a PulseSoc post visible to the authenticated user",
    intents=("show comments on post", "what are people saying on post",
             "read comments on post", "who commented on post"),
    risk=RiskLevel.READ_ONLY,
    confirmation=ConfirmationPolicy.NEVER,
    tool_name="pulsesoc.feed.comments.list",
    permission=PermissionScope.SELF_ACCOUNT_ONLY,
    fields=(
        _POST_ID,
        FieldSpec("limit", "int", required=False, minimum=1, maximum=80, default=40),
    ),
    executor="feed_comments_list",
    verifier="",
    native_route="/pulse/post/:post_id",
    result_card=CardType.CONTENT_RESULT,
    audit_category="feed_read",
))

_register(CapabilitySpec(
    capability_id="feed.post.performance.summary",
    description="Summarize available engagement metrics for one post owned by the authenticated user",
    intents=("how did my post perform", "how did my post do", "show post performance",
             "post engagement summary"),
    risk=RiskLevel.READ_ONLY, confirmation=ConfirmationPolicy.NEVER,
    tool_name="pulsesoc.feed.post.performance.summary",
    permission=PermissionScope.SELF_ACCOUNT_ONLY,
    fields=(_POST_ID,),
    executor="feed_post_performance_summary", verifier="",
    native_route="/pulse/post/:post_id", result_card=CardType.CONTENT_RESULT,
    audit_category="feed_analytics_read",
))

_register(CapabilitySpec(
    capability_id="feed.comments.summary",
    description="Summarize visible comments on one post owned by the authenticated user",
    intents=("summarize comments on my post", "comment summary for post"),
    risk=RiskLevel.READ_ONLY, confirmation=ConfirmationPolicy.NEVER,
    tool_name="pulsesoc.feed.comments.summary",
    permission=PermissionScope.SELF_ACCOUNT_ONLY,
    fields=(
        _POST_ID,
        FieldSpec("limit", "int", required=False, minimum=1, maximum=80, default=40),
    ),
    executor="feed_comments_summary", verifier="",
    native_route="/pulse/post/:post_id", result_card=CardType.CONTENT_RESULT,
    audit_category="feed_analytics_read",
))

_register(CapabilitySpec(
    capability_id="feed.posts.like",
    description="Like one viewable PulseSoc post",
    intents=("like post", "like this post"),
    risk=RiskLevel.REVERSIBLE_WRITE,
    confirmation=ConfirmationPolicy.NEVER,
    tool_name="pulsesoc.feed.posts.like",
    permission=PermissionScope.SELF_ACCOUNT_ONLY,
    fields=(_POST_ID,),
    executor="feed_post_like",
    verifier="feed_post_like_value",
    # Not a declared field — the like state is implied by which capability ran, the
    # same arrangement ``reels.like`` uses. Naming it here is what lets the receipt say
    # "your like is on that post" instead of "that setting is on", which on a path where
    # the runtime chose the post is the difference between a checkable receipt and a
    # sentence that is true of any write at all.
    verified_fields=("liked",),
    native_route="/pulse/post/:post_id",
    result_card=CardType.ACTION_SUCCESS_RECEIPT,
    audit_category="feed_reactions_write",
    target_field="post_id",
    undo_capability_id="feed.posts.unlike",
))

_register(CapabilitySpec(
    capability_id="feed.posts.unlike",
    description="Remove the authenticated user's like from one PulseSoc post",
    intents=("unlike post", "unlike this post", "remove my like from post"),
    risk=RiskLevel.REVERSIBLE_WRITE,
    confirmation=ConfirmationPolicy.NEVER,
    tool_name="pulsesoc.feed.posts.unlike",
    permission=PermissionScope.SELF_ACCOUNT_ONLY,
    fields=(_POST_ID,),
    executor="feed_post_unlike",
    verifier="feed_post_like_value",
    verified_fields=("liked",),
    native_route="/pulse/post/:post_id",
    result_card=CardType.ACTION_SUCCESS_RECEIPT,
    audit_category="feed_reactions_write",
    target_field="post_id",
    undo_capability_id="feed.posts.like",
))

_register(CapabilitySpec(
    capability_id="feed.posts.delete",
    description="Soft-delete one post owned by the authenticated user",
    intents=("delete post", "delete my post"),
    risk=RiskLevel.CONSEQUENTIAL_WRITE,
    confirmation=ConfirmationPolicy.ALWAYS,
    tool_name="pulsesoc.feed.posts.delete",
    permission=PermissionScope.SELF_ACCOUNT_ONLY,
    fields=(_POST_ID,),
    executor="feed_post_delete",
    verifier="feed_post_deleted",
    native_route="/pulse/post/:post_id",
    result_card=CardType.ACTION_SUCCESS_RECEIPT,
    audit_category="feed_posts_write",
    target_field="post_id",
))

_register(CapabilitySpec(
    capability_id="feed.posts.hide",
    description="Hide one other account's post from the authenticated user's Home feed",
    intents=("hide this post", "hide post", "stop showing me this post",
             "don't show me this post"),
    risk=RiskLevel.REVERSIBLE_WRITE,
    # Contextual rather than never. The post is somebody else's, so the runtime
    # cannot fall back to "your most recent post" the way the like path does, and a
    # target the runtime resolved on its own is confirmed by the generic rule
    # regardless of what is written here.
    confirmation=ConfirmationPolicy.CONTEXTUAL,
    tool_name="pulsesoc.feed.posts.hide",
    permission=PermissionScope.SELF_ACCOUNT_ONLY,
    fields=(_POST_ID,),
    executor="feed_post_hide",
    verifier="feed_post_hidden_value",
    verified_fields=("hidden",),
    native_route="/pulse/post/:post_id",
    result_card=CardType.ACTION_SUCCESS_RECEIPT,
    audit_category="feed_visibility_write",
    target_field="post_id",
    # No ``undo_capability_id``: ``pulse_feed_engine`` has no ``unhide_post``, and
    # an undo that named a capability which cannot exist would be a promise the
    # receipt could not keep. The deep link opens the post; that is the honest
    # affordance until the service grows the inverse.
))


# --- Notification preferences ---------------------------------------------

_register(CapabilitySpec(
    capability_id="notifications.preference.read",
    description="Read the authenticated user's notification preferences",
    intents=("notification settings", "my notifications", "notification preference"),
    risk=RiskLevel.READ_ONLY,
    confirmation=ConfirmationPolicy.NEVER,
    tool_name="pulsesoc.notification_preferences.read",
    permission=PermissionScope.SELF_ACCOUNT_ONLY,
    fields=(FieldSpec("category", "enum", required=False,
                      choices=NOTIFICATION_CATEGORIES, default="global"),),
    executor="notification_preferences_read",
    verifier="",
    native_route="/pulse/settings/notifications",
    result_card=CardType.SETTING_CHANGE_RECEIPT,
    audit_category="notification_preferences_read",
    target_field="category",
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
    permission=PermissionScope.SELF_ACCOUNT_ONLY,
    fields=(_NOTIFICATION_CATEGORY, _PUSH_VALUE),
    executor="notification_preferences_update",
    verifier="notification_preference_value",
    native_route="/pulse/settings/notifications",
    result_card=CardType.SETTING_CHANGE_RECEIPT,
    audit_category="notification_preferences_write",
    target_field="category",
    verified_fields=("push",),
    # This capability is its own inverse only if the value is flipped. Replaying the
    # stored arguments would re-apply the change the user is trying to walk back, so
    # the map is what makes the Undo button on this receipt mean anything.
    undo_capability_id="notifications.preference.update",
    undo_argument_map=(("category", "category"), ("push", "!push")),
))

# Content Graph Intelligence: canonical Reels, Status, and Profile reads plus
# explicit, reversible Reel edges. Publishing and deletion remain unreachable.
for _spec in (
    CapabilitySpec("reels.search", "Find viewable PulseSoc Reels", ("find reels", "find my recent reels", "search reels"),
                   RiskLevel.READ_ONLY, ConfirmationPolicy.NEVER, "pulsesoc.reels.search",
                   PermissionScope.SELF_ACCOUNT_ONLY,
                   (FieldSpec("query", "str", required=False, max_length=80, default=""),
                    FieldSpec("limit", "int", required=False, minimum=1, maximum=40, default=20)),
                   "reels_search", "", "/pulse/reels", CardType.SEARCH_RESULTS, "reels_read"),
    CapabilitySpec("reels.get", "Explain one viewable PulseSoc Reel", ("show reel", "explain reel", "open reel"),
                   RiskLevel.READ_ONLY, ConfirmationPolicy.NEVER, "pulsesoc.reels.get",
                   PermissionScope.SELF_ACCOUNT_ONLY, (_REEL_ID,), "reels_get", "",
                   "/pulse/reels/:reel_id", CardType.CONTENT_RESULT, "reels_read"),
    CapabilitySpec("reels.performance.summary", "Summarize performance for one Reel owned by the user",
                   ("how did my reel perform", "how did my reel do", "reel performance",
                    "reel engagement summary"),
                   RiskLevel.READ_ONLY, ConfirmationPolicy.NEVER, "pulsesoc.reels.performance.summary",
                   PermissionScope.SELF_ACCOUNT_ONLY, (_REEL_ID,), "reels_performance", "",
                   "/pulse/reels/:reel_id", CardType.CONTENT_RESULT, "reels_analytics_read"),
    CapabilitySpec("reels.comments.summary", "Summarize visible comments on one owned Reel",
                   ("summarize reel comments", "who commented on my reel"),
                   RiskLevel.READ_ONLY, ConfirmationPolicy.NEVER, "pulsesoc.reels.comments.summary",
                   PermissionScope.SELF_ACCOUNT_ONLY,
                   (_REEL_ID, FieldSpec("limit", "int", required=False, minimum=1, maximum=80, default=40)),
                   "reels_comments_summary", "", "/pulse/reels/:reel_id", CardType.CONTENT_RESULT, "reels_read"),
    CapabilitySpec("status.list", "Show active statuses visible to the user", ("show active statuses", "show statuses"),
                   RiskLevel.READ_ONLY, ConfirmationPolicy.NEVER, "pulsesoc.status.list",
                   PermissionScope.SELF_ACCOUNT_ONLY,
                   (FieldSpec("limit", "int", required=False, minimum=1, maximum=40, default=20),),
                   "statuses_list", "", "/pulse/status", CardType.SEARCH_RESULTS, "status_read"),
    CapabilitySpec("status.get", "Read one visible PulseSoc Status", ("show status", "open status", "explain status"),
                   RiskLevel.READ_ONLY, ConfirmationPolicy.NEVER, "pulsesoc.status.get",
                   PermissionScope.SELF_ACCOUNT_ONLY, (_STATUS_ID,), "statuses_get", "",
                   "/pulse/status/:status_id", CardType.CONTENT_RESULT, "status_read"),
    CapabilitySpec("status.viewer.summary", "Summarize viewers for one owned Status",
                   ("who viewed my status", "status viewer summary"),
                   RiskLevel.READ_ONLY, ConfirmationPolicy.NEVER, "pulsesoc.status.viewer.summary",
                   PermissionScope.SELF_ACCOUNT_ONLY, (_STATUS_ID,), "status_viewers", "",
                   "/pulse/status/:status_id", CardType.CONTENT_RESULT, "status_analytics_read"),
    CapabilitySpec("status.reaction.summary", "Summarize reactions for one owned Status",
                   ("how did my status perform", "how did my status do", "status reaction summary"),
                   RiskLevel.READ_ONLY, ConfirmationPolicy.NEVER, "pulsesoc.status.reaction.summary",
                   PermissionScope.SELF_ACCOUNT_ONLY, (_STATUS_ID,), "status_reactions", "",
                   "/pulse/status/:status_id", CardType.CONTENT_RESULT, "status_analytics_read"),
    CapabilitySpec("profile.get", "Read the signed-in user's canonical profile", ("show my profile", "summarize my account"),
                   RiskLevel.READ_ONLY, ConfirmationPolicy.NEVER, "pulsesoc.profile.get",
                   PermissionScope.SELF_ACCOUNT_ONLY, (), "profile_get", "",
                   "/pulse/profile", CardType.PROFILE_RESULT, "profile_read"),
    CapabilitySpec("profile.activity.summary", "Summarize the user's content activity",
                   ("show my recent activity", "what happened on my account", "profile activity"),
                   RiskLevel.READ_ONLY, ConfirmationPolicy.NEVER, "pulsesoc.profile.activity.summary",
                   PermissionScope.SELF_ACCOUNT_ONLY, (), "profile_activity", "",
                   "/pulse/profile", CardType.PROFILE_RESULT, "profile_read"),
    CapabilitySpec("profile.relationship.summary", "Summarize follower and following counts",
                   ("profile relationship summary", "how many followers do i have"),
                   RiskLevel.READ_ONLY, ConfirmationPolicy.NEVER, "pulsesoc.profile.relationship.summary",
                   PermissionScope.SELF_ACCOUNT_ONLY, (), "profile_relationships", "",
                   "/pulse/profile", CardType.PROFILE_RESULT, "profile_read"),
):
    _register(_spec)

_register(CapabilitySpec(
    capability_id="translation.content.translate",
    description="Translate authorized PulseSoc content without changing its canonical text",
    intents=("translate this post", "translate this content", "show this in another language"),
    risk=RiskLevel.READ_ONLY,
    confirmation=ConfirmationPolicy.NEVER,
    tool_name="pulsesoc.translation.content.translate",
    permission=PermissionScope.SELF_ACCOUNT_ONLY,
    fields=(
        FieldSpec("content_type", "enum", required=True,
                  choices=("post", "comment", "reply", "chat", "profile", "reel", "status")),
        FieldSpec("content_ref", "int", required=True, minimum=1),
        FieldSpec("target_language", "str", required=True, max_length=16),
        FieldSpec("source_language", "str", required=False, max_length=16, default="auto"),
    ),
    executor="translation_content_translate",
    verifier="",
    native_route="/pulse/undx/actions",
    result_card=CardType.CONTENT_RESULT,
    audit_category="translation_read",
    target_field="content_ref",
))

for _capability, _intent, _saved, _executor, _verifier, _undo in (
    ("reels.save", "save reel", True, "reels_save", "reel_saved_value", "reels.unsave"),
    ("reels.unsave", "unsave reel", False, "reels_unsave", "reel_saved_value", "reels.save"),
    ("reels.like", "like reel", True, "reels_like", "reel_liked_value", "reels.unlike"),
    ("reels.unlike", "unlike reel", False, "reels_unlike", "reel_liked_value", "reels.like"),
):
    _register(CapabilitySpec(
        _capability, f"Explicitly {_intent} without toggling", (_intent, f"{_intent} {1}"),
        RiskLevel.REVERSIBLE_WRITE, ConfirmationPolicy.CONTEXTUAL,
        f"pulsesoc.{_capability}", PermissionScope.SELF_ACCOUNT_ONLY, (_REEL_ID,),
        _executor, _verifier, "/pulse/reels/:reel_id", CardType.ACTION_SUCCESS_RECEIPT,
        "reels_write", target_field="reel_id", verified_fields=("saved" if "save" in _capability else "liked",),
        undo_capability_id=_undo,
    ))

_register(CapabilitySpec(
    "profile.preferences.update", "Update a bounded non-security profile preference",
    ("set my preferred language", "change my preferred language"),
    RiskLevel.REVERSIBLE_WRITE, ConfirmationPolicy.CONTEXTUAL,
    "pulsesoc.profile.preferences.update", PermissionScope.SELF_ACCOUNT_ONLY,
    (FieldSpec("preferred_language", "enum", required=True, choices=("en", "es", "fr")),),
    "profile_preferences_update", "profile_preference_value", "/pulse/settings",
    CardType.SETTING_CHANGE_RECEIPT, "profile_preferences_write",
    target_field="preferred_language", verified_fields=("preferred_language",),
))

# Phase 3B Personal Intelligence. These are read-only projections over canonical
# PulseSoc services. Their fields select/filter data; none can mutate product state.
_QUERY = FieldSpec("query", "str", required=True, max_length=120)
_READ_LIMIT = FieldSpec("limit", "int", required=False, minimum=1, maximum=100, default=20)
_NOTIFICATION_ID = FieldSpec("notification_id", "int", required=True, minimum=1)
_LISTING_ID = FieldSpec("listing_id", "int", required=True, minimum=1)
_ORDER_ID = FieldSpec("order_id", "int", required=True, minimum=1)
_LIVE_ID = FieldSpec("live_id", "int", required=True, minimum=1)
_DAYS = FieldSpec("days", "int", required=False, minimum=1, maximum=90, default=7)

# Imported rather than restated so the windows UNDX may ask for are exactly the
# windows the observation series can answer. A hardcoded copy here would drift the
# first time either list changed, and the failure would be silent: the registry
# would keep offering a window the series refuses, and the refusal reads like a
# statement about the market rather than about our own sampling.
# ``crypto_alert_conditions`` imports nothing from services, so this cannot cycle.
from services.crypto_alert_conditions import (  # noqa: E402
    WINDOW_CHOICES as _WINDOW_CHOICES,
    WINDOWABLE_METRICS as _WINDOWABLE_METRICS,
)

for _spec in (
    CapabilitySpec("activity.daily_summary", "Summarize authorized PulseSoc activity with provenance",
                   ("what happened today", "what changed since yesterday", "daily activity summary"),
                   RiskLevel.READ_ONLY, ConfirmationPolicy.NEVER, "pulsesoc.activity.daily_summary",
                   PermissionScope.SELF_ACCOUNT_ONLY,
                   (FieldSpec("days", "int", required=False, minimum=1, maximum=31, default=1),),
                   "activity_daily_summary", "", "/pulse/activity/:category?", CardType.CONTENT_RESULT, "activity_read"),
    CapabilitySpec("notifications.inbox.list", "List owner-scoped notification events",
                   ("show my notification inbox", "what needs my attention", "list unread notifications"),
                   RiskLevel.READ_ONLY, ConfirmationPolicy.NEVER, "pulsesoc.notifications.inbox.list",
                   PermissionScope.SELF_ACCOUNT_ONLY,
                   (_READ_LIMIT, FieldSpec("unread_only", "bool", required=False, default=False)),
                   "notifications_inbox_list", "", "/pulse/notifications", CardType.SEARCH_RESULTS, "notifications_read"),
    CapabilitySpec("notifications.explain", "Explain a notification from its stored source event",
                   ("why did i get this notification", "explain notification"),
                   RiskLevel.READ_ONLY, ConfirmationPolicy.NEVER, "pulsesoc.notifications.explain",
                   PermissionScope.SELF_ACCOUNT_ONLY, (_NOTIFICATION_ID,),
                   "notifications_explain", "", "/pulse/notifications", CardType.CONTENT_RESULT, "notifications_read"),
    CapabilitySpec("notifications.group_summary", "Group and summarize owner-scoped notifications",
                   ("summarize my notifications", "notification group summary"),
                   RiskLevel.READ_ONLY, ConfirmationPolicy.NEVER, "pulsesoc.notifications.group_summary",
                   PermissionScope.SELF_ACCOUNT_ONLY, (_READ_LIMIT,),
                   "notifications_group_summary", "", "/pulse/notifications", CardType.CONTENT_RESULT, "notifications_read"),
    CapabilitySpec("search.global", "Search authorized PulseSoc people, content, messages and activity",
                   ("find everything about", "search everything", "global search"),
                   RiskLevel.READ_ONLY, ConfirmationPolicy.NEVER, "pulsesoc.search.global",
                   PermissionScope.SELF_ACCOUNT_ONLY, (_QUERY, _READ_LIMIT),
                   "search_global", "", "/pulse/search", CardType.SEARCH_RESULTS, "search_read"),
    CapabilitySpec("search.people", "Search visible PulseSoc profiles", ("find people", "search people"),
                   RiskLevel.READ_ONLY, ConfirmationPolicy.NEVER, "pulsesoc.search.people",
                   PermissionScope.SELF_ACCOUNT_ONLY, (_QUERY, _READ_LIMIT),
                   "search_people", "", "/pulse/search", CardType.SEARCH_RESULTS, "search_read"),
    CapabilitySpec("search.content", "Search visible PulseSoc content", ("find content", "search posts and reels"),
                   RiskLevel.READ_ONLY, ConfirmationPolicy.NEVER, "pulsesoc.search.content",
                   PermissionScope.SELF_ACCOUNT_ONLY, (_QUERY, _READ_LIMIT),
                   "search_content", "", "/pulse/search", CardType.SEARCH_RESULTS, "search_read"),
    CapabilitySpec("search.messages", "Search messages only in joined conversations",
                   ("search my messages", "find messages about"),
                   RiskLevel.READ_ONLY, ConfirmationPolicy.NEVER, "pulsesoc.search.messages",
                   PermissionScope.SELF_ACCOUNT_ONLY, (_QUERY, _READ_LIMIT),
                   "search_messages", "", "/pulse/messages", CardType.SEARCH_RESULTS, "messages_read"),
    CapabilitySpec("search.activity", "Search authorized account activity", ("search my activity", "find activity about"),
                   RiskLevel.READ_ONLY, ConfirmationPolicy.NEVER, "pulsesoc.search.activity",
                   PermissionScope.SELF_ACCOUNT_ONLY, (_QUERY, _READ_LIMIT),
                   "search_activity", "", "/pulse/activity/:category?", CardType.SEARCH_RESULTS, "activity_read"),
    CapabilitySpec("settings.inspect", "Inspect current non-secret account settings",
                   ("show my privacy settings", "inspect my settings", "summarize my settings"),
                   RiskLevel.READ_ONLY, ConfirmationPolicy.NEVER, "pulsesoc.settings.inspect",
                   PermissionScope.SELF_ACCOUNT_ONLY, (), "settings_inspect", "",
                   "/pulse/settings", CardType.CONTENT_RESULT, "settings_read"),
    CapabilitySpec("settings.explain", "Explain one settings section without changing it",
                   ("explain my settings", "why am i getting so many",
                    "why do i keep getting", "why does pulsesoc keep"),
                   RiskLevel.READ_ONLY, ConfirmationPolicy.NEVER, "pulsesoc.settings.explain",
                   PermissionScope.SELF_ACCOUNT_ONLY,
                   (FieldSpec("section", "enum", required=False,
                              choices=("all", "privacy", "notifications", "language", "accessibility"), default="all"),),
                   "settings_explain", "", "/pulse/settings", CardType.CONTENT_RESULT, "settings_read"),
    CapabilitySpec("settings.recommend", "Recommend settings for review without mutating them",
                   ("what settings should i change", "recommend privacy settings"),
                   RiskLevel.READ_ONLY, ConfirmationPolicy.NEVER, "pulsesoc.settings.recommend",
                   PermissionScope.SELF_ACCOUNT_ONLY, (), "settings_recommend", "",
                   "/pulse/settings", CardType.CONTENT_RESULT, "settings_read"),
    CapabilitySpec("security.sessions.list", "List redacted active sessions for this account",
                   ("what devices are logged in", "show active sessions"),
                   RiskLevel.READ_ONLY, ConfirmationPolicy.NEVER, "pulsesoc.security.sessions.list",
                   PermissionScope.SELF_ACCOUNT_ONLY, (), "security_sessions_list", "",
                   "/pulse/settings/devices", CardType.SEARCH_RESULTS, "security_read"),
    CapabilitySpec("security.activity.summary", "Summarize owner-scoped security activity",
                   ("show my account activity", "summarize security activity", "show my account health"),
                   RiskLevel.READ_ONLY, ConfirmationPolicy.NEVER, "pulsesoc.security.activity.summary",
                   PermissionScope.SELF_ACCOUNT_ONLY, (), "security_activity_summary", "",
                   "/pulse/account-health", CardType.CONTENT_RESULT, "security_read"),
    CapabilitySpec("security.device.list", "List redacted devices associated with active sessions",
                   ("list my devices", "show logged in devices"),
                   RiskLevel.READ_ONLY, ConfirmationPolicy.NEVER, "pulsesoc.security.device.list",
                   PermissionScope.SELF_ACCOUNT_ONLY, (), "security_device_list", "",
                   "/pulse/settings/devices", CardType.SEARCH_RESULTS, "security_read"),
    CapabilitySpec("marketplace.search", "Search active Marketplace listings", ("search marketplace", "find a listing"),
                   RiskLevel.READ_ONLY, ConfirmationPolicy.NEVER, "pulsesoc.marketplace.search",
                   PermissionScope.SELF_ACCOUNT_ONLY, (_QUERY, _READ_LIMIT),
                   "marketplace_search", "", "/pulse/marketplace", CardType.SEARCH_RESULTS, "marketplace_read"),
    CapabilitySpec("marketplace.listing.summary", "Summarize one active Marketplace listing",
                   ("summarize listing", "explain marketplace listing"),
                   RiskLevel.READ_ONLY, ConfirmationPolicy.NEVER, "pulsesoc.marketplace.listing.summary",
                   PermissionScope.SELF_ACCOUNT_ONLY, (_LISTING_ID,),
                   "marketplace_listing_summary", "", "/pulse/marketplace/:listing_id",
                   CardType.CONTENT_RESULT, "marketplace_read"),
    CapabilitySpec("marketplace.order.status", "Read an owned Marketplace order status",
                   ("where is my order", "marketplace order status"),
                   RiskLevel.READ_ONLY, ConfirmationPolicy.NEVER, "pulsesoc.marketplace.order.status",
                   PermissionScope.SELF_ACCOUNT_ONLY, (_ORDER_ID,),
                   "marketplace_order_status", "", "/pulse/orders/:order_id",
                   CardType.CONTENT_RESULT, "marketplace_read"),
    CapabilitySpec("premium.status", "Read current Premium status", ("what plan am i on", "premium status"),
                   RiskLevel.READ_ONLY, ConfirmationPolicy.NEVER, "pulsesoc.premium.status",
                   PermissionScope.SELF_ACCOUNT_ONLY, (), "premium_status", "",
                   "/pulse/premium", CardType.CONTENT_RESULT, "premium_read"),
    CapabilitySpec("premium.entitlements", "List current feature entitlements",
                   ("what features do i have", "show my entitlements"),
                   RiskLevel.READ_ONLY, ConfirmationPolicy.NEVER, "pulsesoc.premium.entitlements",
                   PermissionScope.SELF_ACCOUNT_ONLY, (), "premium_entitlements", "",
                   "/pulse/premium", CardType.CONTENT_RESULT, "premium_read"),
    CapabilitySpec("ads.performance.summary", "Read owner-scoped advertising performance",
                   ("how did my ads perform", "summarize ad performance"),
                   RiskLevel.READ_ONLY, ConfirmationPolicy.NEVER, "pulsesoc.ads.performance.summary",
                   PermissionScope.SELF_ACCOUNT_ONLY, (_DAYS,), "ads_performance_summary", "",
                   "/pulse/intelligence/:subsystem?", CardType.CONTENT_RESULT, "ads_read"),
    CapabilitySpec("live.search", "Search visible Live sessions", ("find live sessions", "search live"),
                   RiskLevel.READ_ONLY, ConfirmationPolicy.NEVER, "pulsesoc.live.search",
                   PermissionScope.SELF_ACCOUNT_ONLY, (_QUERY, _READ_LIMIT),
                   "live_search", "", "/pulse/live", CardType.SEARCH_RESULTS, "live_read"),
    CapabilitySpec("live.summary", "Summarize one visible Live session", ("summarize live", "explain live session"),
                   RiskLevel.READ_ONLY, ConfirmationPolicy.NEVER, "pulsesoc.live.summary",
                   PermissionScope.SELF_ACCOUNT_ONLY, (_LIVE_ID,), "live_summary", "",
                   "/pulse/live/:live_id", CardType.CONTENT_RESULT, "live_read"),
    CapabilitySpec("live.performance", "Read performance for an owned Live session",
                   ("how did my live perform", "how did my live do", "live performance"),
                   RiskLevel.READ_ONLY, ConfirmationPolicy.NEVER, "pulsesoc.live.performance",
                   PermissionScope.SELF_ACCOUNT_ONLY, (_LIVE_ID,), "live_performance", "",
                   "/pulse/live/:live_id", CardType.CONTENT_RESULT, "live_read"),
    CapabilitySpec("learning.search", "Search the published learning catalog",
                   ("find a course", "search learning", "find a lesson"),
                   RiskLevel.READ_ONLY, ConfirmationPolicy.NEVER, "pulsesoc.learning.search",
                   PermissionScope.SELF_ACCOUNT_ONLY, (_QUERY, _READ_LIMIT),
                   "learning_search", "", "/pulse/courses", CardType.SEARCH_RESULTS, "learning_read"),
    CapabilitySpec("learning.progress", "Read the user's learning progress",
                   ("show my learning progress", "course progress"),
                   RiskLevel.READ_ONLY, ConfirmationPolicy.NEVER, "pulsesoc.learning.progress",
                   PermissionScope.SELF_ACCOUNT_ONLY, (), "learning_progress", "",
                   "/pulse/courses", CardType.CONTENT_RESULT, "learning_read"),
    CapabilitySpec("memory.activity.inspect", "Inspect source-backed activity known to UNDX without storing sensitive memory",
                   ("what do you know about my pulsesoc activity", "inspect my activity memory"),
                   RiskLevel.READ_ONLY, ConfirmationPolicy.NEVER, "pulsesoc.memory.activity.inspect",
                   PermissionScope.SELF_ACCOUNT_ONLY, (), "memory_activity_inspect", "",
                   "/pulse/undx/actions", CardType.CONTENT_RESULT, "memory_read"),
    CapabilitySpec("groups.list", "List public and joined PulseSoc groups",
                   ("show my groups", "what groups can i join", "list groups"),
                   RiskLevel.READ_ONLY, ConfirmationPolicy.NEVER, "pulsesoc.groups.list",
                   PermissionScope.SELF_ACCOUNT_ONLY, (_READ_LIMIT,), "groups_list", "",
                   "/pulse/groups", CardType.CONTENT_RESULT, "groups_read"),
    CapabilitySpec("groups.search", "Search authorized PulseSoc groups",
                   ("find a group", "search groups"),
                   RiskLevel.READ_ONLY, ConfirmationPolicy.NEVER, "pulsesoc.groups.search",
                   PermissionScope.SELF_ACCOUNT_ONLY, (_QUERY, _READ_LIMIT), "groups_search", "",
                   "/pulse/groups", CardType.SEARCH_RESULTS, "groups_read"),
    CapabilitySpec("events.upcoming", "List published upcoming PulseSoc business events",
                   ("what events are coming up", "show upcoming events"),
                   RiskLevel.READ_ONLY, ConfirmationPolicy.NEVER, "pulsesoc.events.upcoming",
                   PermissionScope.SELF_ACCOUNT_ONLY, (_READ_LIMIT,), "events_upcoming", "",
                   "/pulse/events", CardType.CONTENT_RESULT, "events_read"),
    CapabilitySpec("music.search", "Search creator-safe licensed PulseSoc music",
                   ("find music", "search music", "find a sound for my reel"),
                   RiskLevel.READ_ONLY, ConfirmationPolicy.NEVER, "pulsesoc.music.search",
                   PermissionScope.SELF_ACCOUNT_ONLY, (_QUERY, _READ_LIMIT), "music_search", "",
                   "/pulse/music", CardType.SEARCH_RESULTS, "music_read"),
    CapabilitySpec("account.health.summary", "Summarize owner-visible account health findings",
                   ("is my account healthy", "summarize my account health", "explain my account health"),
                   RiskLevel.READ_ONLY, ConfirmationPolicy.NEVER, "pulsesoc.account.health.summary",
                   PermissionScope.SELF_ACCOUNT_ONLY, (), "account_health_summary", "",
                   "/pulse/account-health", CardType.CONTENT_RESULT, "account_health_read"),
    CapabilitySpec("verification.status", "Read the user's verification request status",
                   ("what is my verification status", "show my verification requests"),
                   RiskLevel.READ_ONLY, ConfirmationPolicy.NEVER, "pulsesoc.verification.status",
                   PermissionScope.SELF_ACCOUNT_ONLY, (), "verification_status", "",
                   "/pulse/verification/:track?", CardType.CONTENT_RESULT, "verification_read"),
    CapabilitySpec("support.tickets.list", "List the user's support tickets without internal notes",
                   ("show my support tickets", "what support requests are open"),
                   RiskLevel.READ_ONLY, ConfirmationPolicy.NEVER, "pulsesoc.support.tickets.list",
                   PermissionScope.SELF_ACCOUNT_ONLY, (_READ_LIMIT,), "support_tickets_list", "",
                   "/pulse/support", CardType.CONTENT_RESULT, "support_read"),
    CapabilitySpec("creator.analytics.summary", "Summarize owned creator performance across posts, Reels, and Status",
                   ("how is my content performing", "summarize my creator analytics"),
                   RiskLevel.READ_ONLY, ConfirmationPolicy.NEVER, "pulsesoc.creator.analytics.summary",
                   PermissionScope.SELF_ACCOUNT_ONLY, (), "creator_analytics_summary", "",
                   "/pulse/creator-studio", CardType.CONTENT_RESULT, "creator_read"),
    CapabilitySpec("localization.preferences", "Read language, translation, region, timezone, and currency preferences",
                   ("show my language and region settings", "what localization settings do i use"),
                   RiskLevel.READ_ONLY, ConfirmationPolicy.NEVER, "pulsesoc.localization.preferences",
                   PermissionScope.SELF_ACCOUNT_ONLY, (), "localization_preferences", "",
                   "/pulse/settings/language-region", CardType.CONTENT_RESULT, "localization_read"),
    CapabilitySpec("presence.privacy.status", "Explain who can see the user's presence and last-seen state",
                   ("who can see me online", "show my presence privacy"),
                   RiskLevel.READ_ONLY, ConfirmationPolicy.NEVER, "pulsesoc.presence.privacy.status",
                   PermissionScope.SELF_ACCOUNT_ONLY, (), "presence_privacy_status", "",
                   "/pulse/settings/privacy", CardType.CONTENT_RESULT, "presence_read"),
    # Crypto intelligence (Premium). Read-only and never confirmed, like every
    # other personal read: the entitlement is enforced inside the read model, and
    # an unentitled member gets a grounded "this is part of Premium" answer rather
    # than a refusal, so there is nothing here for a confirmation step to guard.
    # `crypto.portfolio.summary` is NOT registered here. It is registered once,
    # above, under the tool name `pulsesoc.crypto_portfolio.summary` — the
    # spelling `undx_policy` routes and the executor at
    # `undx_agent_tools.crypto_portfolio_summary` announces. The second spec
    # that used to sit here named the tool `pulsesoc.crypto.portfolio.summary`,
    # which nothing else in the tree referenced, so it could only ever have
    # resolved to a tool that does not exist. `_register` rejecting the
    # duplicate outright is what surfaced it.
    CapabilitySpec("crypto.market.window",
                   "Report how one asset moved over a measured window, or why that window cannot be measured",
                   # No ticker appears in a phrasing. A coin name is the *subject* of
                   # almost every crypto sentence, including the ones about pausing a
                   # rule, so "how has bitcoin moved" makes this capability score on
                   # "bitcoin" alone and crowd the alert capabilities out of a bounded
                   # focus. The phrasings name the operation -- movement over a stated
                   # window -- and the asset arrives in ``symbol``, where it belongs.
                   ("how much has it moved", "price change over the last",
                    "how has it performed in the last", "movement in the last"),
                   RiskLevel.READ_ONLY, ConfirmationPolicy.NEVER, "pulsesoc.crypto.market.window",
                   PermissionScope.SELF_ACCOUNT_ONLY,
                   (
                       FieldSpec("symbol", "identifier", required=True, max_length=24),
                       # Constrained to the metrics the worker actually samples and
                       # the windows the series can answer. A free-form window would
                       # let the model ask for "the last week" against 72 hours of
                       # retention and read the refusal as a market fact.
                       FieldSpec("metric", "enum", required=False,
                                 choices=tuple(sorted(_WINDOWABLE_METRICS)), default="price"),
                       FieldSpec("minutes", "enum", required=False,
                                 choices=tuple(str(w) for w in _WINDOW_CHOICES), default="60"),
                   ),
                   "crypto_market_window", "",
                   "/pulse/crypto/alerts", CardType.CONTENT_RESULT, "crypto_read",
                   target_field="symbol"),
):
    _register(_spec)


# --- Stage 6 agentic actions ----------------------------------------------
#
# The pack that makes UNDX able to *do* things rather than only describe them.
# Three properties are common to all of it and none of them are incidental:
#
# Every capability is ``self_account_only`` and none declares a field naming whose
# account to act on. That combination is what the gateway checks; a capability that
# accepted a ``user_id`` would be refused at import rather than at request time.
#
# Every write names a verifier that re-reads the store it just wrote to, and lists
# in ``verified_fields`` every field it can change. A field left off that list is a
# field a user could change while being told the change was verified, so the
# constructor refuses the capability outright.
#
# Where an inverse capability exists it is declared, with an argument map when the
# inverse needs different arguments. Where none exists the field is left empty
# rather than pointed at something approximate: an Undo button that restores the
# wrong prior value is worse than no button.

_WATCHLIST_SYMBOL = FieldSpec("symbol", "identifier", required=True, max_length=16)
_HOLDING_ITEM_ID = FieldSpec("item_id", "int", required=True, minimum=1)
_HOLDING_AMOUNT = FieldSpec("amount", "float", required=True, minimum=0.0, maximum=1_000_000_000.0)
_HOLDING_PRICE = FieldSpec("average_buy_price", "float", required=False, minimum=0.0,
                           maximum=1_000_000_000.0, default=0.0)
_PRESENCE_SETTINGS = ("hide_last_seen", "invisible_mode")
_REGION_SETTINGS = ("locale", "time_zone", "currency", "date_format")
_TRANSLATION_POLICIES = ("ask", "always", "never")
_AUDIENCE_SETTINGS = ("lastSeen", "storyAudience", "liveAudience", "allowTagging",
                      "allowMentions", "allowDirectMessages")
_AUDIENCE_CHOICES = ("everyone", "followers", "nobody")
_THEME_CHOICES = ("system", "light", "dark")

for _spec in (
    CapabilitySpec("crypto.watchlist.list", "List the coins on the authenticated user's watchlist",
                   ("my watchlist", "what's on my watchlist", "coins i'm watching",
                    "show watchlist", "which coins am i tracking"),
                   RiskLevel.READ_ONLY, ConfirmationPolicy.NEVER, "pulsesoc.crypto.watchlist.list",
                   PermissionScope.SELF_ACCOUNT_ONLY, (),
                   "crypto_watchlist_list", "", "/pulse/watchlists",
                   CardType.CONTENT_RESULT, "crypto_read"),
    CapabilitySpec("crypto.watchlist.add", "Add one coin to the authenticated user's watchlist",
                   ("add to my watchlist", "watch this coin", "track this coin",
                    "start watching", "put on my watchlist"),
                   RiskLevel.REVERSIBLE_WRITE, ConfirmationPolicy.CONTEXTUAL,
                   "pulsesoc.crypto.watchlist.add", PermissionScope.SELF_ACCOUNT_ONLY,
                   (_WATCHLIST_SYMBOL,), "crypto_watchlist_add", "crypto_watchlist_contains",
                   "/pulse/watchlists", CardType.SETTING_CHANGE_RECEIPT, "crypto_watchlist_write",
                   target_field="symbol",
                   # Add and remove take the same single argument, so replaying it
                   # against the inverse capability is exactly the reversal. No map.
                   undo_capability_id="crypto.watchlist.remove"),
    CapabilitySpec("crypto.watchlist.remove", "Remove one coin from the authenticated user's watchlist",
                   ("remove from my watchlist", "stop watching", "untrack this coin",
                    "take off my watchlist", "drop from watchlist"),
                   RiskLevel.REVERSIBLE_WRITE, ConfirmationPolicy.CONTEXTUAL,
                   "pulsesoc.crypto.watchlist.remove", PermissionScope.SELF_ACCOUNT_ONLY,
                   (_WATCHLIST_SYMBOL,), "crypto_watchlist_remove", "crypto_watchlist_contains",
                   "/pulse/watchlists", CardType.SETTING_CHANGE_RECEIPT, "crypto_watchlist_write",
                   target_field="symbol", undo_capability_id="crypto.watchlist.add"),
    CapabilitySpec("crypto.portfolio.holdings.list", "List the authenticated user's stored portfolio holdings",
                   ("my holdings", "what do i hold", "list my portfolio",
                    "show my coins", "portfolio holdings"),
                   RiskLevel.READ_ONLY, ConfirmationPolicy.NEVER,
                   "pulsesoc.crypto.portfolio.holdings.list", PermissionScope.SELF_ACCOUNT_ONLY, (),
                   "crypto_portfolio_holdings_list", "", "/pulse/portfolio",
                   CardType.CONTENT_RESULT, "crypto_read"),
    CapabilitySpec("crypto.portfolio.holding.add", "Record a new holding in the authenticated user's portfolio",
                   ("add to my portfolio", "record a holding", "i bought",
                    "log this position", "add this coin to my portfolio"),
                   RiskLevel.REVERSIBLE_WRITE, ConfirmationPolicy.ALWAYS,
                   "pulsesoc.crypto.portfolio.holding.add", PermissionScope.SELF_ACCOUNT_ONLY,
                   (_WATCHLIST_SYMBOL, _HOLDING_AMOUNT, _HOLDING_PRICE),
                   "crypto_portfolio_holding_add", "crypto_holding_exists",
                   "/pulse/portfolio", CardType.ACTION_SUCCESS_RECEIPT, "crypto_portfolio_write",
                   # Nothing here executes a trade — this records a position the user
                   # already holds — but a wrong number silently distorts every
                   # valuation the product shows afterwards, so it is confirmed
                   # unconditionally and the amount is read back before "done".
                   target_field="symbol", verified_fields=("amount", "average_buy_price"),
                   undo_capability_id="crypto.portfolio.holding.delete",
                   undo_argument_map=(("item_id", "@target"),),
                   idempotent=False),
    CapabilitySpec("crypto.portfolio.holding.update", "Change the size or cost basis of one stored holding",
                   ("update my holding", "change my position", "correct my portfolio",
                    "fix the amount i hold", "edit my holding"),
                   RiskLevel.REVERSIBLE_WRITE, ConfirmationPolicy.ALWAYS,
                   "pulsesoc.crypto.portfolio.holding.update", PermissionScope.SELF_ACCOUNT_ONLY,
                   # Both fields are optional and *neither carries a default*, unlike
                   # the `add` spec which reuses `_HOLDING_PRICE` with `default=0.0`.
                   # On a create, "no cost basis given" genuinely means zero. On a
                   # partial update it means "leave it alone" — and a default would
                   # turn that into an explicit argument, which the executor would
                   # then put in the patch, zeroing a cost basis the user never
                   # mentioned. Worse, `verified_fields` lists the same column, so the
                   # verifier would read back the 0.0 it had just written and stamp the
                   # receipt "verified". Absent has to stay absent all the way down.
                   (_HOLDING_ITEM_ID,
                    FieldSpec("amount", "float", required=False, minimum=0.0, maximum=1_000_000_000.0),
                    FieldSpec("average_buy_price", "float", required=False, minimum=0.0,
                              maximum=1_000_000_000.0)),
                   "crypto_portfolio_holding_update", "crypto_holding_values",
                   "/pulse/portfolio", CardType.SETTING_CHANGE_RECEIPT, "crypto_portfolio_write",
                   target_field="item_id", verified_fields=("amount", "average_buy_price")),
    CapabilitySpec("crypto.portfolio.holding.delete", "Delete one holding from the authenticated user's portfolio",
                   ("remove from my portfolio", "delete this holding", "i sold",
                    "drop this position", "clear this holding"),
                   # Consequential rather than reversible, and deliberately without an
                   # undo: the row carries an amount and a cost basis that the delete
                   # does not preserve anywhere, so a restore would have to invent them.
                   RiskLevel.CONSEQUENTIAL_WRITE, ConfirmationPolicy.ALWAYS,
                   "pulsesoc.crypto.portfolio.holding.delete", PermissionScope.SELF_ACCOUNT_ONLY,
                   (_HOLDING_ITEM_ID,), "crypto_portfolio_holding_delete", "crypto_holding_deleted",
                   "/pulse/portfolio", CardType.ACTION_SUCCESS_RECEIPT, "crypto_portfolio_write",
                   target_field="item_id"),
    CapabilitySpec("notifications.mark_read", "Mark one of the user's notifications as read",
                   ("mark this notification read", "mark as read", "dismiss this notification",
                    "clear this alert"),
                   RiskLevel.REVERSIBLE_WRITE, ConfirmationPolicy.CONTEXTUAL,
                   "pulsesoc.notifications.mark_read", PermissionScope.SELF_ACCOUNT_ONLY,
                   (_NOTIFICATION_ID,), "notifications_mark_read", "notification_read_state",
                   "/pulse/notifications", CardType.SETTING_CHANGE_RECEIPT, "notifications_write",
                   # PulseSoc has no mark-unread anywhere — not in the web UI, not in the
                   # native app, not in the service. Naming an undo capability that does
                   # not exist would fail ``_validate_undo_graph``; naming an approximate
                   # one would put a button on the receipt that lies. So: none.
                   target_field="notification_id"),
    CapabilitySpec("notifications.mark_all_read", "Mark every notification in one category as read",
                   ("mark all read", "clear my notifications", "mark everything as read",
                    "clear all alerts", "empty my notifications"),
                   # Bulk and unreversible, so confirmed every time regardless of how
                   # confident the request looked. The blast radius is the whole
                   # category, and "clear my notifications" is one word away from
                   # "clear my messages notifications".
                   RiskLevel.REVERSIBLE_WRITE, ConfirmationPolicy.ALWAYS,
                   "pulsesoc.notifications.mark_all_read", PermissionScope.SELF_ACCOUNT_ONLY,
                   (_NOTIFICATION_CATEGORY,), "notifications_mark_all_read", "notifications_unread_count",
                   "/pulse/notifications", CardType.ACTION_SUCCESS_RECEIPT, "notifications_write",
                   target_field="category"),
    CapabilitySpec("presence.privacy.update", "Change whether the user's presence and last-seen are visible",
                   ("hide my last seen", "show my last seen", "go invisible",
                    "appear offline", "stop appearing offline", "hide when i'm online"),
                   RiskLevel.REVERSIBLE_WRITE, ConfirmationPolicy.ALWAYS,
                   "pulsesoc.presence.privacy.update", PermissionScope.SELF_ACCOUNT_ONLY,
                   (FieldSpec("setting", "enum", required=True, choices=_PRESENCE_SETTINGS),
                    FieldSpec("enabled", "bool", required=True)),
                   "presence_privacy_update", "presence_privacy_value",
                   "/pulse/settings/privacy", CardType.SETTING_CHANGE_RECEIPT, "presence_write",
                   target_field="setting", verified_fields=("enabled",),
                   undo_capability_id="presence.privacy.update",
                   undo_argument_map=(("setting", "setting"), ("enabled", "!enabled"))),
    CapabilitySpec("localization.region.update", "Set the user's locale, timezone, currency, or date format",
                   ("change my language", "set my timezone", "change my currency",
                    "set my region", "change my date format"),
                   RiskLevel.REVERSIBLE_WRITE, ConfirmationPolicy.CONTEXTUAL,
                   "pulsesoc.localization.region.update", PermissionScope.SELF_ACCOUNT_ONLY,
                   (FieldSpec("setting", "enum", required=True, choices=_REGION_SETTINGS),
                    FieldSpec("value", "str", required=True, max_length=64)),
                   "localization_region_update", "region_preference_value",
                   "/pulse/settings/language-region", CardType.SETTING_CHANGE_RECEIPT, "localization_write",
                   # Undoing needs the prior value, which the arguments do not carry and
                   # the write does not preserve. The receipt names the new value and the
                   # deep link opens the screen; that is the honest affordance.
                   target_field="setting", verified_fields=("value",)),
    CapabilitySpec("localization.translation.update", "Set the auto-translate policy for one target language",
                   ("always translate", "stop translating", "ask before translating",
                    "auto translate posts", "turn off auto translate"),
                   RiskLevel.REVERSIBLE_WRITE, ConfirmationPolicy.CONTEXTUAL,
                   "pulsesoc.localization.translation.update", PermissionScope.SELF_ACCOUNT_ONLY,
                   (FieldSpec("target_language", "identifier", required=True, max_length=16),
                    FieldSpec("policy", "enum", required=True, choices=_TRANSLATION_POLICIES)),
                   "localization_translation_update", "translation_preference_value",
                   "/pulse/settings/language-region", CardType.SETTING_CHANGE_RECEIPT, "localization_write",
                   target_field="target_language", verified_fields=("policy",)),
    CapabilitySpec("settings.privacy.audience.update", "Change who can see or reach the user on one privacy switch",
                   ("who can message me", "who can tag me", "make my story followers only",
                    "change my privacy", "restrict who can mention me", "who can see my lives"),
                   # Widening an audience makes content visible to people who could not
                   # see it a moment ago, and that is observable outside PulseSoc the
                   # instant it happens. Consequential, therefore always confirmed.
                   RiskLevel.CONSEQUENTIAL_WRITE, ConfirmationPolicy.ALWAYS,
                   "pulsesoc.settings.privacy.audience.update", PermissionScope.SELF_ACCOUNT_ONLY,
                   (FieldSpec("setting", "enum", required=True, choices=_AUDIENCE_SETTINGS),
                    FieldSpec("audience", "enum", required=True, choices=_AUDIENCE_CHOICES)),
                   "settings_privacy_audience_update", "settings_preference_value",
                   "/pulse/settings/privacy", CardType.SETTING_CHANGE_RECEIPT, "settings_write",
                   target_field="setting", verified_fields=("audience",)),
    CapabilitySpec("settings.appearance.theme.update", "Switch the user's app theme",
                   ("dark mode", "light mode", "switch to dark", "change my theme",
                    "use the system theme"),
                   RiskLevel.REVERSIBLE_WRITE, ConfirmationPolicy.CONTEXTUAL,
                   "pulsesoc.settings.appearance.theme.update", PermissionScope.SELF_ACCOUNT_ONLY,
                   (FieldSpec("theme", "enum", required=True, choices=_THEME_CHOICES),),
                   "settings_appearance_theme_update", "settings_preference_value",
                   "/pulse/settings/appearance", CardType.SETTING_CHANGE_RECEIPT, "settings_write",
                   # ``theme`` is both the target and the value, so the mutable set is
                   # empty and the verifier reads the one key the capability can write.
                   target_field="theme"),
):
    _register(_spec)


# ---------------------------------------------------------------------------
# Messaging writes
# ---------------------------------------------------------------------------
#
# Messenger has had read capabilities since the first pack — list, search,
# summarize, draft. Draft in particular has always stopped one step short on
# purpose: it composes and hands the words back, and a person sends them. These
# two capabilities are the first that touch the conversation itself, and they are
# deliberately unequal. Marking read changes a counter that only the caller sees.
# Sending puts words in front of another person under the caller's name, cannot be
# recalled, and is the one action in this expansion whose blast radius leaves the
# account entirely.

_register(CapabilitySpec(
    capability_id="messages.mark_read",
    description="Mark one of the authenticated user's conversations as read",
    intents=("mark this conversation read", "mark as read", "clear my unread",
             "mark this chat read"),
    risk=RiskLevel.REVERSIBLE_WRITE,
    confirmation=ConfirmationPolicy.CONTEXTUAL,
    tool_name="pulsesoc.messages.mark_read",
    permission=PermissionScope.SELF_ACCOUNT_ONLY,
    fields=(_CONVERSATION_ID,),
    executor="messages_mark_read",
    verifier="conversation_read_state",
    verified_fields=("unread_count",),
    native_route="/pulse/messages/:conversation_id",
    result_card=CardType.ACTION_SUCCESS_RECEIPT,
    audit_category="messages_read_state_write",
    target_field="conversation_id",
    # Read receipts go out to the other participants when this runs, so the
    # counter is private but the fact of having read is not. There is no
    # ``mark_unread`` in comm_v2 and inventing one here would not un-send those
    # receipts, so no undo is offered.
))

_register(CapabilitySpec(
    capability_id="messages.send",
    description="Send one text message to a conversation the authenticated user is already in",
    intents=("send this message", "send it", "reply to this conversation",
             "send that message now"),
    # Consequential, not reversible: comm_v2 has no delete-for-everyone on this
    # path, and even if it did, the message has already been delivered and pushed
    # by the time an undo could run. The recipient's copy is not ours to retract.
    risk=RiskLevel.CONSEQUENTIAL_WRITE,
    confirmation=ConfirmationPolicy.ALWAYS,
    tool_name="pulsesoc.messages.send",
    permission=PermissionScope.SELF_ACCOUNT_ONLY,
    fields=(_CONVERSATION_ID,
            FieldSpec("body", "str", required=True, max_length=2000)),
    executor="messages_send",
    verifier="message_exists",
    # ``body`` is mutable and therefore has to be verified, which is the point of
    # the rule: the confirmation card shows exact words, and the receipt may only
    # say "sent" once those exact words have been found in the conversation.
    verified_fields=("body", "message_id"),
    native_route="/pulse/messages/:conversation_id",
    result_card=CardType.ACTION_SUCCESS_RECEIPT,
    audit_category="messages_write",
    target_field="conversation_id",
    idempotent=False,
))


# ---------------------------------------------------------------------------
# Business OS
# ---------------------------------------------------------------------------
#
# Only the two READY advertising verbs and the seller profile's descriptive text.
# Budget, funding, payouts and the card-payment switch are all absent and stay
# absent: pausing a campaign stops it being eligible for a future delivery worker
# and moves no money, which is exactly why it is safe to expose while the money
# verbs beside it are not.

_BUSINESS_CAMPAIGN_ID = FieldSpec("campaign_id", "identifier", required=True, max_length=120)

#: Where a receipt for a Business OS action sends the person, and the reason it is
#: not a Business OS screen.
#:
#: The screens exist — ``BusinessProfile`` and ``BusinessOsAdvertising`` are both
#: registered in ``AppNavigator.tsx`` — but neither appears in ``linking.ts``, so
#: the whole Business OS surface is currently unreachable by URL. A capability may
#: not declare a route the client does not serve: the deep link on the receipt is a
#: promise that tapping it goes somewhere, and a dead link is a small lie told at
#: the exact moment the person is checking whether the action really happened.
#:
#: Per-campaign linking has a second obstacle even once a path is declared.
#: ``business_os.advertising.service`` mints campaign ids with ``uuid4().hex`` while
#: ``RootStackParamList.BusinessOsAdvertising`` types ``campaignId`` as ``number``,
#: so a correct id cannot be carried by the screen that would receive it. Both are
#: reported as MISSING APIs rather than patched from here — widening a native param
#: type is a client change, and inventing a URL prefix the web app does not serve
#: would break every one of these links the moment it left the app.
#:
#: Until then the UNDX action centre is the honest destination: it is declared, it
#: is reachable, and it is the screen whose actual job is showing what UNDX did.
#: Named for the screen rather than for Business OS: the same destination now
#: carries marketplace and moderation receipts, and a ``_BUSINESS_`` prefix would
#: read as a constraint that does not exist.
_UNDX_ACTION_CENTRE = "/pulse/undx/actions"

#: Kept in step with ``undx_agent_tools.BUSINESS_PROFILE_WRITABLE_FIELDS`` by a test
#: rather than by import, so that widening one without the other fails loudly.
_BUSINESS_PROFILE_FIELDS = (
    "about", "what_you_sell", "service_area", "shipping_summary",
    "return_summary", "response_expectations", "response_hours",
)

_register(CapabilitySpec(
    capability_id="business.campaign.pause",
    description="Pause one advertising campaign owned by the authenticated user",
    intents=("pause my campaign", "pause the campaign", "stop my campaign",
             "pause the summer campaign"),
    risk=RiskLevel.REVERSIBLE_WRITE,
    confirmation=ConfirmationPolicy.CONTEXTUAL,
    tool_name="pulsesoc.business.campaign.pause",
    permission=PermissionScope.SELF_ACCOUNT_ONLY,
    fields=(_BUSINESS_CAMPAIGN_ID,),
    executor="business_campaign_pause",
    verifier="campaign_operational_status",
    verified_fields=("operational_status",),
    native_route=_UNDX_ACTION_CENTRE,
    result_card=CardType.ACTION_SUCCESS_RECEIPT,
    audit_category="business_advertising_write",
    target_field="campaign_id",
    undo_capability_id="business.campaign.resume",
    undo_argument_map=(("campaign_id", "campaign_id"),),
))

_register(CapabilitySpec(
    capability_id="business.campaign.resume",
    description="Resume one paused advertising campaign owned by the authenticated user",
    intents=("resume my campaign", "unpause the campaign", "start my campaign again",
             "resume the summer campaign"),
    # Resuming re-enters ``active``, so ``resume_campaign`` re-runs the full
    # activation gate — a suspended advertiser or released funding refuses here
    # rather than in this layer, which is the correct place for it.
    risk=RiskLevel.REVERSIBLE_WRITE,
    confirmation=ConfirmationPolicy.CONTEXTUAL,
    tool_name="pulsesoc.business.campaign.resume",
    permission=PermissionScope.SELF_ACCOUNT_ONLY,
    fields=(_BUSINESS_CAMPAIGN_ID,),
    executor="business_campaign_resume",
    verifier="campaign_operational_status",
    verified_fields=("operational_status",),
    native_route=_UNDX_ACTION_CENTRE,
    result_card=CardType.ACTION_SUCCESS_RECEIPT,
    audit_category="business_advertising_write",
    target_field="campaign_id",
    undo_capability_id="business.campaign.pause",
    undo_argument_map=(("campaign_id", "campaign_id"),),
))

_register(CapabilitySpec(
    capability_id="business.profile.update",
    description="Update one descriptive text field on the user's business profile",
    intents=("update my business description", "change what my business sells",
             "update my shipping info", "change my return policy",
             "update my business about section"),
    # Public the moment it saves — the seller profile is what a buyer reads — so
    # the exact proposed text is shown and approved before it is written.
    risk=RiskLevel.CONSEQUENTIAL_WRITE,
    confirmation=ConfirmationPolicy.ALWAYS,
    tool_name="pulsesoc.business.profile.update",
    permission=PermissionScope.SELF_ACCOUNT_ONLY,
    fields=(FieldSpec("field", "enum", required=True, choices=_BUSINESS_PROFILE_FIELDS),
            FieldSpec("value", "str", required=True, max_length=600)),
    executor="business_profile_update",
    verifier="business_profile_field_value",
    verified_fields=("value",),
    native_route=_UNDX_ACTION_CENTRE,
    result_card=CardType.SETTING_CHANGE_RECEIPT,
    audit_category="business_profile_write",
    # Undo would need the text that was there before, which the arguments do not
    # carry and ``update_profile`` does not return. The audit table keeps it, but
    # reading a value out of an audit log to write it back is a different
    # capability than this one and should be built as such if it is wanted.
    target_field="field",
))


# ---------------------------------------------------------------------------
# Consumer social graph, profile, Reels and moderation
# ---------------------------------------------------------------------------
#
# Every capability below dispatches to a shared service that the HTTP routes also
# call. The permission scope declared here is not where ownership is decided —
# ``delete_owned_reel`` refuses a Reel it does not own whoever asks — it is the
# gateway's structural check that this capability cannot be pointed at somebody
# else's account by an argument.
#
# ``self_account_only`` on the Reels, comment and report capabilities may read
# oddly, since a comment can be deleted from a Reel the caller owns but did not
# write, and a report is filed *about* content that is not theirs. The scope
# describes which rows the caller may reach, and in each case the row that moves
# is one they own or moderate: their Reel, their comment, their report. The one
# exception is spelled out on ``feed.report``.
#
# ``owned_content_target`` would be the more descriptive scope for several of
# these, but ``undx_tool_gateway._enforce_permission_scope`` refuses it outright:
# it has no resolver for that scope and fails closed rather than executing under
# an ownership check that does not exist. Declaring it would take these
# capabilities off the air. ``feed.posts.hide`` — somebody else's post, scoped
# ``self_account_only`` — is the standing precedent.

_COMMENT_ID = FieldSpec("comment_id", "int", required=True, minimum=1)

#: 2200 characters, matching what ``pulse_feed_engine`` accepts for a comment body.
_COMMENT_BODY = FieldSpec("body", "str", required=True, max_length=2200)

#: Kept in step with ``pulse_feed_engine.REPORT_TARGET_TYPES`` by a test rather than
#: by import, following ``_BUSINESS_PROFILE_FIELDS``. Importing the feed engine here
#: would pull the whole media/moderation/notification graph into a module the
#: gateway loads on every request, and this file is deliberately import-light.
_REPORT_CONTENT_TYPES = ("post", "comment", "media", "user")


_register(CapabilitySpec(
    capability_id="profile.block",
    description="Block one PulseSoc account so it can no longer reach the user",
    intents=("block user", "block this account", "block them", "stop them contacting me",
             "block this person", "i want to block"),
    # Reversible in the strict sense — ``profile.unblock`` restores the prior state
    # exactly — but a block is observable to the other party through what they can
    # no longer do, so the confirmation is contextual rather than never.
    risk=RiskLevel.REVERSIBLE_WRITE,
    confirmation=ConfirmationPolicy.CONTEXTUAL,
    tool_name="pulsesoc.profile.block",
    permission=PermissionScope.OTHER_USER_TARGET,
    fields=(_TARGET_USER_ID,),
    executor="profile_block",
    verifier="profile_block_value",
    # Not a declared field: which of block/unblock ran is what sets the expected
    # value, the same arrangement ``feed.posts.like`` uses. Naming it is what lets
    # the receipt say "they are blocked" rather than "that setting is on".
    verified_fields=("blocked",),
    native_route="/pulse/profile/:profileKey",
    result_card=CardType.RELATIONSHIP_CHANGE_RECEIPT,
    audit_category="social_safety_write",
    target_field="target_user_id",
    undo_capability_id="profile.unblock",
))

_register(CapabilitySpec(
    capability_id="profile.unblock",
    description="Remove an existing block on one PulseSoc account",
    intents=("unblock user", "unblock this account", "unblock them", "remove my block",
             "let them contact me again"),
    risk=RiskLevel.REVERSIBLE_WRITE,
    confirmation=ConfirmationPolicy.CONTEXTUAL,
    tool_name="pulsesoc.profile.unblock",
    permission=PermissionScope.OTHER_USER_TARGET,
    fields=(_TARGET_USER_ID,),
    executor="profile_unblock",
    verifier="profile_block_value",
    verified_fields=("blocked",),
    native_route="/pulse/profile/:profileKey",
    result_card=CardType.RELATIONSHIP_CHANGE_RECEIPT,
    audit_category="social_safety_write",
    target_field="target_user_id",
    undo_capability_id="profile.block",
))

# The six descriptions below say "the caller" where the surrounding file says "the
# authenticated user", and the inconsistency is load-bearing. ``undx_brain.attention``
# builds its routing index from these strings and then drops any term appearing in more
# than ``len(RECORDS) * _COMMON_TERM_SHARE`` records, on the principle that a word in a
# quarter of the map names no subject. Before this capability pack "user" sat at 32
# postings against a ceiling of 38; writing "the authenticated user" six more times put
# it at 42 against 41 and the term was dropped outright, which cost "who is user 99" its
# routing entirely — it had nothing else to match on. Every write here is performed by
# the authenticated user, so saying so discriminates nothing; the word is kept for the
# two capabilities where a user is the *subject* (``profile.block``/``profile.unblock``,
# whose intents are "block user"/"unblock user"). Restoring house style in these six
# strings will silently re-break that routing, and no test in this file will notice —
# the guard is ``tests/undx_agent/test_question_framed_writes.py``.
_register(CapabilitySpec(
    capability_id="profile.bio.update",
    description="Replace the text of the caller's own profile bio",
    intents=("update my bio", "change my bio", "rewrite my bio", "set my bio",
             "edit my profile bio", "my bio should say"),
    # Public the moment it saves, so the exact proposed text is approved first —
    # the same reasoning as ``business.profile.update``.
    risk=RiskLevel.CONSEQUENTIAL_WRITE,
    confirmation=ConfirmationPolicy.ALWAYS,
    tool_name="pulsesoc.profile.bio.update",
    permission=PermissionScope.SELF_ACCOUNT_ONLY,
    # ``FieldSpec.coerce`` rejects an empty string, so this cannot clear a bio.
    # ``update_profile`` distinguishes "clear it" from "leave it alone" and would
    # honour a clear; reaching that through UNDX needs its own capability, because
    # "update my bio" arriving with an empty value is far more likely to be a
    # planner that lost the text than a person who meant to erase theirs.
    fields=(FieldSpec("bio", "str", required=True, max_length=500),),
    executor="profile_bio_update",
    verifier="profile_bio_value",
    verified_fields=("bio",),
    native_route="/pulse/settings",
    result_card=CardType.SETTING_CHANGE_RECEIPT,
    audit_category="profile_write",
    # No undo. Reversing this needs the previous text, which the arguments do not
    # carry and ``update_profile_bio`` does not hand back in a form
    # ``undo_argument_map`` can read. The before-state is in the profile audit
    # trail; reading a value out of an audit log to write it back is a different
    # capability and should be built as one.
    target_field="bio",
))

_register(CapabilitySpec(
    capability_id="reels.delete",
    description="Soft-delete one Reel the caller owns",
    intents=("delete my reel", "delete this reel", "remove my reel", "take down my reel"),
    risk=RiskLevel.CONSEQUENTIAL_WRITE,
    confirmation=ConfirmationPolicy.ALWAYS,
    tool_name="pulsesoc.reels.delete",
    permission=PermissionScope.SELF_ACCOUNT_ONLY,
    fields=(_REEL_ID,),
    executor="reels_delete",
    verifier="reel_deleted",
    native_route="/pulse/reels/:reel_id",
    result_card=CardType.ACTION_SUCCESS_RECEIPT,
    audit_category="reels_write",
    target_field="reel_id",
))

_register(CapabilitySpec(
    capability_id="reels.comment.create",
    description="Post a comment on one Reel the caller can see",
    intents=("comment on this reel", "reply to this reel", "leave a comment on the reel",
             "post a comment saying"),
    # Immediately public under somebody else's Reel, so the exact words are approved
    # before they are published rather than after.
    risk=RiskLevel.CONSEQUENTIAL_WRITE,
    confirmation=ConfirmationPolicy.ALWAYS,
    tool_name="pulsesoc.reels.comment.create",
    permission=PermissionScope.SELF_ACCOUNT_ONLY,
    fields=(_REEL_ID, _COMMENT_BODY),
    executor="reels_comment_create",
    verifier="reel_comment_body",
    verified_fields=("body",),
    native_route="/pulse/reels/:reel_id",
    result_card=CardType.ACTION_SUCCESS_RECEIPT,
    audit_category="reels_comments_write",
    target_field="reel_id",
    undo_capability_id="reels.comment.delete",
    # Deleting the comment just created needs its id, which appears nowhere in the
    # arguments — only in the verified result. An unverified creation therefore
    # offers no undo rather than a delete aimed at nothing.
    undo_argument_map=(("comment_id", "@target"),),
    # Two identical comments are two comments. Suppressing the second as a duplicate
    # would be the gateway deciding the person did not mean what they repeated.
    idempotent=False,
))

_register(CapabilitySpec(
    capability_id="reels.comment.update",
    description="Edit the text of a comment the caller wrote",
    intents=("edit my comment", "change my comment", "fix my comment", "reword my comment"),
    risk=RiskLevel.CONSEQUENTIAL_WRITE,
    confirmation=ConfirmationPolicy.ALWAYS,
    tool_name="pulsesoc.reels.comment.update",
    permission=PermissionScope.SELF_ACCOUNT_ONLY,
    fields=(_COMMENT_ID, _COMMENT_BODY),
    executor="reels_comment_update",
    verifier="reel_comment_body",
    verified_fields=("body",),
    # Only ``comment_id`` is declared, so the ``:reel_id`` placeholder is stripped
    # and the link lands on Reels rather than on a specific one. A comment has no
    # addressable screen of its own; this is the closest honest destination.
    native_route="/pulse/reels/:reel_id",
    result_card=CardType.ACTION_SUCCESS_RECEIPT,
    audit_category="reels_comments_write",
    target_field="comment_id",
))

_register(CapabilitySpec(
    capability_id="reels.comment.delete",
    description="Soft-delete a comment the caller wrote, or one on a Reel the caller owns",
    intents=("delete my comment", "remove my comment", "delete this comment",
             "remove that comment from my reel"),
    risk=RiskLevel.CONSEQUENTIAL_WRITE,
    confirmation=ConfirmationPolicy.ALWAYS,
    tool_name="pulsesoc.reels.comment.delete",
    permission=PermissionScope.SELF_ACCOUNT_ONLY,
    fields=(_COMMENT_ID,),
    executor="reels_comment_delete",
    verifier="reel_comment_deleted",
    native_route="/pulse/reels/:reel_id",
    result_card=CardType.ACTION_SUCCESS_RECEIPT,
    audit_category="reels_comments_write",
    target_field="comment_id",
))

_register(CapabilitySpec(
    capability_id="feed.report",
    description="File a moderation report against a post, comment, media item or account",
    intents=("report this post", "report this comment", "report this account",
             "report this user", "flag this post", "report this to moderation"),
    # Consequential because it puts a human moderator's attention on another
    # person's content and, for an approved post, moves it into review. A report
    # filed by a planner that misread the conversation is not something an undo
    # can retract from the moderation queue.
    risk=RiskLevel.CONSEQUENTIAL_WRITE,
    confirmation=ConfirmationPolicy.ALWAYS,
    tool_name="pulsesoc.feed.report",
    permission=PermissionScope.SELF_ACCOUNT_ONLY,
    # ``content_id`` can name another account when ``content_type`` is ``user``, and
    # it is deliberately not called ``target_user_id``: that name is reserved by
    # ``_ACTOR_NAMING_FIELDS`` for fields that select *whose data is mutated*, and
    # this one selects what is being complained about. The row this writes is the
    # caller's own report. ``report_content`` refuses a self-report and checks the
    # target exists before filing.
    fields=(
        FieldSpec("content_type", "enum", required=True, choices=_REPORT_CONTENT_TYPES),
        FieldSpec("content_id", "int", required=True, minimum=1),
        FieldSpec("reason", "str", required=True, max_length=500),
    ),
    executor="feed_report",
    verifier="content_reported",
    verified_fields=("content_type", "reason"),
    native_route=_UNDX_ACTION_CENTRE,
    result_card=CardType.ACTION_SUCCESS_RECEIPT,
    audit_category="moderation_write",
    target_field="content_id",
    # No undo: withdrawing a report is a moderator-side state change on a row the
    # reporter no longer solely owns, and ``report_content`` exposes no retraction.
))


# ---------------------------------------------------------------------------
# Marketplace listings
# ---------------------------------------------------------------------------
#
# These act on the Business OS seller catalog — ``business_os_mkt_products``,
# whose primary key is a string like ``mktp_9f2c…`` — through
# ``services.business_os.marketplace.service`` exactly as it stands. That service
# already enforces the feature flag, the approved-seller requirement, the account
# hold, product ownership and the legal status transitions, and writes its own
# ``business_os_mkt_audit`` trail. Nothing here re-decides any of it.
#
# SHARP EDGE, and it is worth stating plainly: ``marketplace.search`` and
# ``marketplace.listing.summary`` above read a *different* table —
# ``marketplace_listings``, the older consumer marketplace, whose ids are
# integers. The two namespaces share the word "listing" and nothing else. An id
# obtained from the read capabilities cannot be used with the write capabilities:
# an integer fails the ``identifier`` field's own coercion or, if it were to pass,
# would miss every ``mktp_``-prefixed row and surface as "Product not found."
# That is a confusing failure but a safe one — ids cannot collide across the two
# key spaces, so no write can land on the wrong row. Unifying or renaming the two
# surfaces is a product decision and is reported as an open finding rather than
# quietly resolved by giving them the same field type here.
#
# ``marketplace.listing.delete`` maps to the service's ``archive`` transition.
# There is no hard delete in the product and this is not the place to invent one:
# orders reference products, and a row that vanishes from under a buyer's receipt
# is a support incident, not a feature. The capability keeps the word "delete"
# because that is the word a person uses; the receipt reports the archived status.

_MKT_LISTING_ID = FieldSpec("listing_id", "identifier", required=True, max_length=120)

#: Kept in step with the ``allowed`` set in ``marketplace.service.update_product``
#: by a test. ``status`` is absent on purpose — it is reachable only through the
#: lifecycle verbs, which is what makes the transition table enforceable.
_MKT_UPDATABLE_FIELDS = ("title", "description", "price_cents", "fulfillment_type", "inventory_qty")

_register(CapabilitySpec(
    capability_id="marketplace.listing.create",
    description="Create a draft Marketplace listing owned by the authenticated seller",
    intents=("create a listing", "list an item for sale", "add a marketplace listing",
             "sell this on marketplace", "new marketplace listing"),
    risk=RiskLevel.CONSEQUENTIAL_WRITE,
    confirmation=ConfirmationPolicy.ALWAYS,
    tool_name="pulsesoc.marketplace.listing.create",
    permission=PermissionScope.SELF_ACCOUNT_ONLY,
    fields=(
        FieldSpec("title", "str", required=True, max_length=160),
        FieldSpec("price_cents", "int", required=True, minimum=0, maximum=100_000_000),
        FieldSpec("description", "str", required=False, max_length=2000),
        FieldSpec("fulfillment_type", "enum", required=False,
                  choices=("physical", "digital"), default="physical"),
    ),
    executor="marketplace_listing_create",
    verifier="marketplace_listing_created",
    # No row exists yet to name, so the target is the title — the same choice
    # ``crypto.alerts.create`` makes with ``symbol``. It keeps two different
    # "list my bike" and "list my desk" requests in one message from sharing an
    # idempotency key.
    target_field="title",
    verified_fields=("price_cents", "description", "fulfillment_type"),
    native_route=_UNDX_ACTION_CENTRE,
    result_card=CardType.ACTION_SUCCESS_RECEIPT,
    audit_category="marketplace_listings_write",
    # No undo. The inverse of "create" here would be ``archive``, which leaves the
    # row in place with a different status — not a reversal, and offering it as one
    # would tell the seller their listing was undone while it still exists.
    idempotent=False,
))

_register(CapabilitySpec(
    capability_id="marketplace.listing.update",
    description="Change one mutable field on a Marketplace listing the caller owns",
    intents=("update my listing", "change my listing price", "edit my listing",
             "change the price of my listing", "update the listing description"),
    # Reversible: the previous value can be written back through this same
    # capability. Contextual confirmation still catches the case that matters —
    # a target the runtime resolved rather than the person naming it.
    risk=RiskLevel.REVERSIBLE_WRITE,
    confirmation=ConfirmationPolicy.CONTEXTUAL,
    tool_name="pulsesoc.marketplace.listing.update",
    permission=PermissionScope.SELF_ACCOUNT_ONLY,
    fields=(
        _MKT_LISTING_ID,
        FieldSpec("field", "enum", required=True, choices=_MKT_UPDATABLE_FIELDS),
        FieldSpec("value", "str", required=True, max_length=2000),
    ),
    executor="marketplace_listing_update",
    verifier="marketplace_listing_field_value",
    # Both, not just ``value``: the verifier reads back the field the call named,
    # so listing only ``value`` would leave "which field moved" unchecked.
    verified_fields=("field", "value"),
    native_route=_UNDX_ACTION_CENTRE,
    result_card=CardType.SETTING_CHANGE_RECEIPT,
    audit_category="marketplace_listings_write",
    target_field="listing_id",
    # No undo, for the reason ``business.profile.update`` gives: reversing needs
    # the previous value, which neither the arguments nor the service return.
))

_register(CapabilitySpec(
    capability_id="marketplace.listing.pause",
    description="Pause an active Marketplace listing so it stops being purchasable",
    intents=("pause my listing", "unlist my item", "take my listing off sale",
             "stop selling this", "pause the listing"),
    risk=RiskLevel.REVERSIBLE_WRITE,
    confirmation=ConfirmationPolicy.CONTEXTUAL,
    tool_name="pulsesoc.marketplace.listing.pause",
    permission=PermissionScope.SELF_ACCOUNT_ONLY,
    fields=(_MKT_LISTING_ID,),
    executor="marketplace_listing_pause",
    verifier="marketplace_listing_status",
    verified_fields=("status",),
    native_route=_UNDX_ACTION_CENTRE,
    result_card=CardType.ACTION_SUCCESS_RECEIPT,
    audit_category="marketplace_listings_write",
    target_field="listing_id",
    undo_capability_id="marketplace.listing.resume",
    undo_argument_map=(("listing_id", "listing_id"),),
))

_register(CapabilitySpec(
    capability_id="marketplace.listing.resume",
    description="Return a paused Marketplace listing to active",
    intents=("resume my listing", "relist my item", "put my listing back on sale",
             "start selling this again", "unpause the listing"),
    # ``transition_product`` re-runs the full activation gate on the way back to
    # ``active`` — a suspended seller, an account hold or zero inventory on a
    # physical item refuses there, which is the right place for it.
    risk=RiskLevel.REVERSIBLE_WRITE,
    confirmation=ConfirmationPolicy.CONTEXTUAL,
    tool_name="pulsesoc.marketplace.listing.resume",
    permission=PermissionScope.SELF_ACCOUNT_ONLY,
    fields=(_MKT_LISTING_ID,),
    executor="marketplace_listing_resume",
    verifier="marketplace_listing_status",
    verified_fields=("status",),
    native_route=_UNDX_ACTION_CENTRE,
    result_card=CardType.ACTION_SUCCESS_RECEIPT,
    audit_category="marketplace_listings_write",
    target_field="listing_id",
    undo_capability_id="marketplace.listing.pause",
    undo_argument_map=(("listing_id", "listing_id"),),
))

_register(CapabilitySpec(
    capability_id="marketplace.listing.delete",
    description="Retire a Marketplace listing by archiving it",
    intents=("delete my listing", "remove my listing", "archive my listing",
             "take down my listing permanently", "get rid of my listing"),
    risk=RiskLevel.CONSEQUENTIAL_WRITE,
    confirmation=ConfirmationPolicy.ALWAYS,
    tool_name="pulsesoc.marketplace.listing.delete",
    permission=PermissionScope.SELF_ACCOUNT_ONLY,
    fields=(_MKT_LISTING_ID,),
    executor="marketplace_listing_delete",
    verifier="marketplace_listing_status",
    verified_fields=("status",),
    native_route=_UNDX_ACTION_CENTRE,
    result_card=CardType.ACTION_SUCCESS_RECEIPT,
    audit_category="marketplace_listings_write",
    target_field="listing_id",
    # ``archived -> draft`` is a legal restore, but it is not this capability's
    # inverse: restoring returns the listing to draft, not to the active or paused
    # state it was archived from. An Undo button that silently changed which state
    # the seller ends in is worse than no button.
))


# ---------------------------------------------------------------------------
# Cross-capability validation
# ---------------------------------------------------------------------------


def _validate_undo_graph() -> None:
    """Check that every declared undo can actually be invoked.

    Undo is a promise made in a receipt and honoured somewhere else entirely, so
    nothing at the call site notices when the two ends disagree. The three ways
    they can disagree are all checked here, at import: an undo naming a capability
    that does not exist, an undo whose map does not supply every required argument
    of that capability, and an undo relying on argument pass-through between two
    capabilities that do not in fact share a schema.

    That last one is the reason this runs over the whole registry rather than in
    ``__post_init__`` — a spec cannot see its undo target while the registry is
    still being built.
    """
    for spec in REGISTRY.values():
        if not spec.undo_capability_id:
            continue
        target = REGISTRY.get(spec.undo_capability_id)
        if target is None:
            raise ValueError(
                f"{spec.capability_id}: undo_capability_id {spec.undo_capability_id!r} "
                f"is not a registered capability"
            )
        required = {item.name for item in target.fields if item.required}
        if spec.undo_argument_map:
            produced = {name for name, _ in spec.undo_argument_map}
            unknown = sorted(produced - {item.name for item in target.fields})
            if unknown:
                raise ValueError(
                    f"{spec.capability_id}: undo_argument_map produces {unknown}, which "
                    f"{target.capability_id} does not declare"
                )
            sources = {token.lstrip("!") for _, token in spec.undo_argument_map if token != "@target"}
            missing_sources = sorted(sources - {item.name for item in spec.fields})
            if missing_sources:
                raise ValueError(
                    f"{spec.capability_id}: undo_argument_map reads {missing_sources}, which "
                    f"this capability does not declare"
                )
        else:
            # No map means pass-through, which is only honest when this capability
            # supplies every argument the undo requires.
            produced = {item.name for item in spec.fields}
        unmet = sorted(required - produced)
        if unmet:
            raise ValueError(
                f"{spec.capability_id}: undoing with {target.capability_id} requires "
                f"{unmet}, which this capability's undo arguments do not supply"
            )


_validate_undo_graph()


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


def unregistered_tool_names() -> list[str]:
    """Capability tool names the production ledger does not know about.

    These two lists must agree. The registry decides what UNDX may *propose*;
    ``undx_policy.PRODUCTION_TOOL_REGISTRY`` decides what the audit ledger will
    *record*, and ``undx_architecture.prepare_tool_operation`` raises for anything
    missing from it. That raise happens deep inside the gateway, before any mutation,
    where the transport turns it into a fall-through to the language model — so a
    capability present here and absent there is not an error anyone sees. It is a
    capability that quietly answers every request with chit-chat, in production, with
    one WARNING line to show for it.

    Returning the divergence rather than raising at import keeps a misconfiguration
    from taking the whole app down; a test asserts the list is empty, which is where a
    deployment defect should be caught.
    """
    from services import undx_policy

    known = set(getattr(undx_policy, "PRODUCTION_TOOL_REGISTRY", {}) or {})
    return sorted({spec.tool_name for spec in REGISTRY.values()} - known)


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
    "unregistered_tool_names",
    "NOTIFICATION_CATEGORIES", "category_choices",
]
