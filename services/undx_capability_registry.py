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
             "what alerts", "which alerts", "any alerts", "alerts do i have"),
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
    intents=("show alert", "alert details", "that alert"),
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
    intents=("pause alert", "stop alert", "mute alert", "turn off alert"),
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
    intents=("resume alert", "restart alert", "reactivate alert", "turn on alert"),
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
             "show who i follow", "find my followers"),
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
    intents=("follow user", "follow account", "follow member"),
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
             "open messenger conversations", "list my chats"),
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
