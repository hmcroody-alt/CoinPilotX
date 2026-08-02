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
):
    _register(_spec)


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


# ---------------------------------------------------------------------------
# The authorization boundary
# ---------------------------------------------------------------------------
#
# Where a capability's authority begins and ends is written down three times.
#
#   1. This registry's ``CapabilitySpec`` — risk, confirmation, permission scope,
#      verifier, verified fields.
#   2. ``undx_policy.PRODUCTION_TOOL_REGISTRY`` — risk and a confirmation *boolean*,
#      keyed by tool name rather than capability id.
#   3. ``undx_knowledge_map`` — authorization scope, authentication, feature flag.
#
# The second is not documentation. ``undx_architecture.HIGH_IMPACT_TOOLS`` is built
# from record 2's confirmation boolean, and the planner removes those names from the
# allowlist it is offered. So a capability that this registry classes as
# ``consequential_write, always`` becomes reachable without confirmation if — and
# only if — someone edits a dict in a different file. No test today reads both.
#
# Three records of one boundary give three chances to disagree, and a disagreement
# does not look like a bug. It looks like permission.
#
# The fix is the oldest one there is. Deuteronomy 19:14 does not say "do not take
# your neighbour's field"; it says do not *move the marker* — the offence is making
# the boundary unreadable, committed before anything is taken. So: derive the
# boundary from all three records, refuse to resolve a disagreement (any resolution
# rule would be a fourth opinion), and pin the result so that widening it requires
# editing a baseline a human has to look at.
#
# The vocabularies differ, and that is itself part of the finding. Record 2 says
# ``high`` where this file says ``consequential_write``; record 3 says
# ``membership_scoped`` where this file says ``self_account_only``. A translation
# has to be declared for the check to mean anything, and declaring it is what makes
# the flattening visible: record 2's boolean cannot express ``contextual`` at all,
# so seven capabilities that this registry marks as needing situational confirmation
# are recorded there as needing none.


#: Which registry risk classes each policy-ledger risk word is allowed to name.
#:
#: Exact where the two vocabularies are exact. ``high`` covers two registry classes
#: because the ledger genuinely cannot tell them apart — recorded as a set rather
#: than papered over with a lossy mapping in one direction.
_POLICY_RISK_CLASSES: dict[str, frozenset[str]] = {
    "read_only": frozenset({RiskLevel.READ_ONLY}),
    "low": frozenset({RiskLevel.READ_ONLY}),
    "medium": frozenset({RiskLevel.REVERSIBLE_WRITE}),
    "high": frozenset({RiskLevel.CONSEQUENTIAL_WRITE, RiskLevel.HIGH_RISK}),
}

#: Which knowledge-map authorization scopes each registry permission may correspond
#: to. Absence is the point: ``unscoped_defect``, ``existence_oracle_defect`` and
#: ``privileged_role`` appear against no permission, so a map record that acquires
#: one while the registry still says ``self_account_only`` is a conflict rather than
#: a silently-accepted downgrade.
_PERMISSION_SCOPES: dict[str, frozenset[str]] = {
    PermissionScope.SELF_ACCOUNT_ONLY: frozenset(
        {"self_account_only", "membership_scoped", "public"}
    ),
    PermissionScope.OTHER_USER_TARGET: frozenset({"directed_at_other_user"}),
    PermissionScope.OWNED_CONTENT_TARGET: frozenset(
        {"self_account_only", "membership_scoped", "public"}
    ),
}

#: Confirmation policies the ledger's ``confirmation: False`` may stand for. A
#: capability this registry marks ``always`` and the ledger marks ``False`` is the
#: single most dangerous disagreement the two records can hold, because it is
#: exactly the edit that removes a name from ``HIGH_IMPACT_TOOLS``.
_UNCONFIRMED_POLICIES = frozenset({ConfirmationPolicy.NEVER, ConfirmationPolicy.CONTEXTUAL})


class AuthorizationRecordConflict(AgentError):
    """Two records of one capability's authority disagree.

    Raised rather than resolved. Choosing which record wins would be a fourth
    opinion about the boundary, and the safe reading of "the records disagree" is
    that nobody currently knows where the boundary is.
    """

    def __init__(self, capability_id: str, field_name: str, detail: str) -> None:
        super().__init__(
            "authorization_record_conflict",
            "UNDX cannot act on that right now.",
            details={
                "capability_id": clean(capability_id, 120),
                "field": field_name,
                "detail": clean(detail, 300),
            },
        )
        self.capability_id = capability_id
        self.field_name = field_name
        self.detail = detail


@dataclass(frozen=True)
class AuthorizationBoundary:
    """Where one capability's authority ends, agreed across every record of it."""

    capability_id: str
    risk: str
    confirmation: str
    permission: str
    authorization_scope: str
    is_write: bool
    requires_authentication: bool
    policy_confirms: bool
    verifier: str
    verified_fields: tuple[str, ...]
    feature_flag: str

    def widenings_against(self, other: "AuthorizationBoundary") -> tuple[str, ...]:
        """How this boundary reaches further than ``other``.

        Named separately rather than reported as "changed", because narrowing a
        boundary needs no ceremony and only widening is the event that wants a
        receipt. A drift test that fires on both trains people to refresh the
        baseline without reading it, which costs more safety than it buys.
        """
        found: list[str] = []
        if RiskLevel.ORDER.get(self.risk, 0) < RiskLevel.ORDER.get(other.risk, 0):
            found.append(f"risk lowered {other.risk} -> {self.risk}")
        rank = {ConfirmationPolicy.NEVER: 0, ConfirmationPolicy.CONTEXTUAL: 1, ConfirmationPolicy.ALWAYS: 2}
        if rank.get(self.confirmation, 0) < rank.get(other.confirmation, 0):
            found.append(f"confirmation weakened {other.confirmation} -> {self.confirmation}")
        if other.policy_confirms and not self.policy_confirms:
            found.append("dropped out of HIGH_IMPACT_TOOLS")
        if self.permission != other.permission:
            found.append(f"permission scope changed {other.permission} -> {self.permission}")
        if self.authorization_scope != other.authorization_scope:
            found.append(
                f"authorization scope changed {other.authorization_scope} -> {self.authorization_scope}"
            )
        if other.requires_authentication and not self.requires_authentication:
            found.append("authentication no longer required")
        if other.verifier and not self.verifier:
            found.append(f"verifier dropped ({other.verifier})")
        dropped = tuple(sorted(set(other.verified_fields) - set(self.verified_fields)))
        if dropped:
            found.append("verified fields dropped: " + ", ".join(dropped))
        if other.feature_flag and not self.feature_flag:
            found.append(f"feature gate removed ({other.feature_flag})")
        return tuple(found)


def authorization_surface() -> dict[str, AuthorizationBoundary]:
    """Every capability's boundary, cross-checked against all three records.

    Raises ``AuthorizationRecordConflict`` on the first disagreement. Imports are
    local because ``undx_knowledge_map`` imports this module — the map derives its
    operational fields from the registry rather than restating them, which is why
    the fields checked here are the ones it genuinely holds on its own.
    """
    from services import undx_knowledge_map, undx_policy

    ledger = getattr(undx_policy, "PRODUCTION_TOOL_REGISTRY", {}) or {}
    surface: dict[str, AuthorizationBoundary] = {}

    for capability_id, spec in sorted(REGISTRY.items()):
        entry = ledger.get(spec.tool_name)
        if entry is None:
            raise AuthorizationRecordConflict(
                capability_id, "tool_name",
                f"{spec.tool_name!r} is registered here but absent from the production ledger",
            )

        ledger_risk = str(entry.get("risk", ""))
        allowed = _POLICY_RISK_CLASSES.get(ledger_risk)
        if allowed is None:
            raise AuthorizationRecordConflict(
                capability_id, "risk", f"production ledger uses unknown risk word {ledger_risk!r}",
            )
        if spec.risk not in allowed:
            raise AuthorizationRecordConflict(
                capability_id, "risk",
                f"registry says {spec.risk!r}; production ledger says {ledger_risk!r}",
            )

        policy_confirms = bool(entry.get("confirmation"))
        if spec.confirmation == ConfirmationPolicy.ALWAYS and not policy_confirms:
            raise AuthorizationRecordConflict(
                capability_id, "confirmation",
                "registry requires confirmation but the production ledger does not, so "
                "undx_architecture.HIGH_IMPACT_TOOLS will offer this to the planner unguarded",
            )
        if policy_confirms and spec.confirmation not in (ConfirmationPolicy.ALWAYS,):
            if spec.confirmation not in _UNCONFIRMED_POLICIES:
                raise AuthorizationRecordConflict(
                    capability_id, "confirmation",
                    f"registry says {spec.confirmation!r}; production ledger requires confirmation",
                )

        record = undx_knowledge_map.BY_ID.get(capability_id)
        if record is None:
            raise AuthorizationRecordConflict(
                capability_id, "knowledge_map",
                "registered capability has no record in the product knowledge map",
            )

        permitted = _PERMISSION_SCOPES.get(spec.permission, frozenset())
        if record.authorization_scope not in permitted:
            raise AuthorizationRecordConflict(
                capability_id, "authorization_scope",
                f"registry permission {spec.permission!r} does not admit map scope "
                f"{record.authorization_scope!r}",
            )
        if record.authentication_required != spec.requires_authentication:
            raise AuthorizationRecordConflict(
                capability_id, "authentication",
                f"registry says requires_authentication={spec.requires_authentication}; "
                f"map says {record.authentication_required}",
            )

        surface[capability_id] = AuthorizationBoundary(
            capability_id=capability_id,
            risk=spec.risk,
            confirmation=spec.confirmation,
            permission=spec.permission,
            authorization_scope=record.authorization_scope,
            is_write=spec.is_write,
            requires_authentication=spec.requires_authentication,
            policy_confirms=policy_confirms,
            verifier=spec.verifier,
            verified_fields=tuple(sorted(spec.verified_fields)),
            feature_flag=record.feature_flag,
        )
    return surface


def boundary_tuple(boundary: AuthorizationBoundary) -> tuple[Any, ...]:
    """A boundary flattened for a baseline file, in a stable order."""
    return (
        boundary.capability_id,
        boundary.risk,
        boundary.confirmation,
        boundary.permission,
        boundary.authorization_scope,
        boundary.is_write,
        boundary.requires_authentication,
        boundary.policy_confirms,
        boundary.verifier,
        boundary.verified_fields,
        boundary.feature_flag,
    )


def surface_widenings(
    baseline: dict[str, tuple[Any, ...]],
    current: dict[str, AuthorizationBoundary] | None = None,
) -> list[str]:
    """Capabilities that now reach further than the baseline recorded.

    Deletions are deliberately not reported. Removing a capability cannot widen
    what UNDX may do, and a check that fails on removal teaches people to update
    the baseline without reading it — which is the failure mode this whole
    mechanism exists to prevent.
    """
    live = authorization_surface() if current is None else current
    fields = (
        "capability_id", "risk", "confirmation", "permission", "authorization_scope",
        "is_write", "requires_authentication", "policy_confirms", "verifier",
        "verified_fields", "feature_flag",
    )
    findings: list[str] = []
    for capability_id, boundary in sorted(live.items()):
        recorded = baseline.get(capability_id)
        if recorded is None:
            findings.append(f"{capability_id}: newly reachable, not in the recorded surface")
            continue
        if len(recorded) != len(fields):
            findings.append(f"{capability_id}: recorded boundary has the wrong shape")
            continue
        was = AuthorizationBoundary(**{
            name: (tuple(value) if name == "verified_fields" else value)
            for name, value in zip(fields, recorded)
        })
        for note in boundary.widenings_against(was):
            findings.append(f"{capability_id}: {note}")
    return findings


def authorization_baseline() -> list[tuple[Any, ...]]:
    """The current surface, in the exact form a baseline file records."""
    return [boundary_tuple(item) for _, item in sorted(authorization_surface().items())]


__all__ = [
    "CapabilitySpec", "REGISTRY", "get", "require",
    "capability_ids", "write_capability_ids", "describe_for_model",
    "unregistered_tool_names",
    "NOTIFICATION_CATEGORIES", "category_choices",
    "AuthorizationBoundary", "AuthorizationRecordConflict",
    "authorization_surface", "authorization_baseline",
    "boundary_tuple", "surface_widenings",
]
