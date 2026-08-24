"""Executors for the registered UNDX capabilities.

Every function here is the *only* code path between the agent and a real PulseSoc
service. They are deliberately dull: no confirmation logic, no auditing, no
retries, no policy. The gateway owns all of that, and keeping it out of here means
there is exactly one place to review when asking "can this action be reached
without approval?".

Two invariants hold throughout:

**Ownership is enforced by the service call, not by a preceding check.** Each
underlying function takes ``user_id`` and filters on it in SQL. A caller who
substitutes another account's ``alert_id`` gets "not found" from the database
rather than a permission check that could be forgotten or reordered.

**Nothing raw escapes.** Service responses are projected onto declared fields by
``_alert_record`` before travelling on. A crypto alert row can contain
user-authored strings, and if such a row were handed to the model verbatim then a
symbol named "ignore previous instructions" would arrive looking exactly like
system text. Whitelisting is what prevents that.
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable

from services.undx_agent_contracts import (
    AgentError,
    AgentOutcome,
    ToolResult,
    clean,
)


# ---------------------------------------------------------------------------
# Normalisation of untrusted service output
# ---------------------------------------------------------------------------

#: Fields of an alert rule that may be shown to a user or a model. Anything not
#: named here — metadata blobs, source refs, delivery logs, internal ids — is
#: dropped rather than filtered, so a newly added column cannot leak by default.
#:
#: The drop-by-default rule is the right one, and it has a cost that has to be paid
#: deliberately: a rule shape the projection has not been taught is not rejected here,
#: it is quietly described as something simpler than it is. Compound conditions and
#: watchlist/portfolio scoping were added to ``alert_engine`` without this list being
#: extended, so every scoped compound rule reached the model as ``symbol: ""``,
#: ``threshold: None`` and the legacy fallback condition — a rule that reads as broken,
#: and that ``resolve_alert_reference`` then had to identify from an empty description.
_ALERT_FIELDS = ("id", "symbol", "condition", "threshold_value", "status", "active",
                 "alert_type", "created_at", "updated_at", "trigger_count",
                 # What the rule actually watches. ``condition_summary`` is rendered
                 # once, server-side, by ``alert_engine._public_rule`` precisely so the
                 # web UI, the native UI, the notification copy and this projection
                 # cannot describe one rule four different ways; it is carried, never
                 # re-derived. It is empty for a basic rule, whose ``condition`` and
                 # ``threshold_value`` already say everything there is to say.
                 "condition_summary", "is_advanced",
                 # Scope. A scoped rule has no symbol on purpose — it is about a list or
                 # about everything held, and naming one coin would be read as a claim
                 # that it watches only that coin.
                 "watchlist_id", "is_watchlist_rule", "is_portfolio_rule")


def _alert_record(rule: dict[str, Any] | None) -> dict[str, Any]:
    """Project one alert rule onto its safe, bounded public shape."""
    if not isinstance(rule, dict) or not rule:
        return {}
    channels = rule.get("channels") if isinstance(rule.get("channels"), dict) else {}
    record: dict[str, Any] = {}
    for key in _ALERT_FIELDS:
        value = rule.get(key)
        if isinstance(value, bool):
            record[key] = value
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            record[key] = value
        else:
            record[key] = clean(value, 80)
    record["alert_id"] = int(rule.get("id") or 0)
    record["threshold"] = rule.get("threshold_value")
    record["paused"] = clean(rule.get("status") or "active", 24) == "paused"
    # ``clean`` turns None into "", which is right for prose and wrong for a nullable
    # id: an unscoped rule would arrive carrying an empty watchlist rather than no
    # watchlist, and "" is a value a model will try to say something about.
    if rule.get("watchlist_id") is None:
        record["watchlist_id"] = None
    metadata = rule.get("metadata")
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except (TypeError, ValueError):
            metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}
    # A member's own note wins. Failing that the name is built from what the rule
    # watches, which for a scoped rule is not a coin: falling back to the symbol alone
    # named every portfolio and watchlist rule "Crypto alert", so an account holding
    # several of them offered the model a set of identically-named things to choose
    # between — and ``resolve_alert_reference`` is required to find exactly one.
    if record.get("is_portfolio_rule"):
        fallback = "Portfolio alert"
    elif record.get("is_watchlist_rule"):
        fallback = "Watchlist alert"
    else:
        fallback = f"{record.get('symbol') or 'Crypto'} alert"
    record["display_name"] = clean(metadata.get("note") or fallback, 80)
    record["channels"] = {
        name: bool(channels.get(name))
        for name in ("in_app", "push", "email", "sms", "telegram")
    }
    return record


def _timed(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _fail(tool: str, capability: str, code: str, message: str, *, retryable: bool = False,
          started: float = 0.0) -> ToolResult:
    return ToolResult(
        ok=False, tool_name=tool, capability_id=capability,
        error_code=code, error_message=message, retryable=retryable,
        latency_ms=_timed(started) if started else 0,
    )


# ---------------------------------------------------------------------------
# Crypto alerts — backed by services.alert_engine
# ---------------------------------------------------------------------------


def _alert_engine():
    from services import alert_engine

    return alert_engine


def crypto_alerts_list(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    """List the caller's alerts, and say plainly when the list is not all of them.

    One row more than the limit is requested on purpose. Without it, a full page and a
    complete set are indistinguishable — and that distinction is load-bearing a long
    way from here. ``undx_agent_runtime.resolve_alert_reference`` decides "exactly one
    of your alerts matches this description" from this list, and a *truncated* list can
    contain exactly one match while the account holds several. The user would then be
    shown, and would approve, a change to whichever alert happened to fall on page one.

    ``symbol`` narrows the page itself. Passing it means ``truncated`` describes the
    coin the person named rather than the account as a whole, which is the difference
    between "you have more Bitcoin alerts than I can compare" and "you have more alerts
    than I can compare" — the second having been said, until this argument existed, to
    people holding exactly one Bitcoin alert and fifty of something else.
    """
    started = time.perf_counter()
    engine = _alert_engine()
    limit = int(arguments.get("limit") or 20)
    symbol = str(arguments.get("symbol") or "").strip().upper()
    fetched = [_alert_record(rule)
               for rule in ((engine.list_alert_rules(int(user_id), limit=limit + 1,
                                                    symbol=symbol or None) or {})
                            .get("alerts") or [])]
    records = fetched[:limit]
    return ToolResult(
        ok=True,
        tool_name="pulsesoc.crypto_alerts.list",
        capability_id="crypto.alerts.list",
        records=records,
        data={"count": len(records), "truncated": len(fetched) > limit},
        latency_ms=_timed(started),
    )


def crypto_alerts_get(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    started = time.perf_counter()
    engine = _alert_engine()
    alert_id = int(arguments["alert_id"])
    # Owner-scoped read. A rule belonging to another account is indistinguishable
    # from one that does not exist, which is the correct disclosure boundary.
    rule = engine.get_alert_rule(alert_id, int(user_id))
    if not rule:
        return _fail("pulsesoc.crypto_alerts.get", "crypto.alerts.get",
                     "resource_not_found", "UNDX could not find that alert on your account.",
                     started=started)
    record = _alert_record(rule)
    return ToolResult(
        ok=True,
        tool_name="pulsesoc.crypto_alerts.get",
        capability_id="crypto.alerts.get",
        canonical_resource_id=f"alert_rule:{alert_id}",
        records=[record],
        data=record,
        latency_ms=_timed(started),
    )


def _set_alert_state(user_id: int, alert_id: int, *, capability: str, tool: str,
                     call: Callable[[int, int], dict[str, Any]]) -> ToolResult:
    started = time.perf_counter()
    engine = _alert_engine()
    existing = engine.get_alert_rule(alert_id, int(user_id))
    if not existing:
        return _fail(tool, capability, "resource_not_found",
                     "UNDX could not find that alert on your account.", started=started)
    if clean(existing.get("status") or "active", 24) == "deleted":
        return _fail(tool, capability, "resource_deleted",
                     "That alert has already been deleted.", started=started)
    outcome = call(alert_id, int(user_id)) or {}
    if not outcome.get("ok"):
        return _fail(tool, capability, "write_rejected",
                     clean(outcome.get("message") or "PulseSoc did not accept that change.", 200),
                     retryable=True, started=started)
    return ToolResult(
        ok=True,
        tool_name=tool,
        capability_id=capability,
        canonical_resource_id=f"alert_rule:{alert_id}",
        data={"alert_id": alert_id, "requested_status": clean(outcome.get("status"), 24)},
        latency_ms=_timed(started),
    )


def crypto_alerts_pause(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    engine = _alert_engine()
    return _set_alert_state(
        user_id, int(arguments["alert_id"]),
        capability="crypto.alerts.pause", tool="pulsesoc.crypto_alerts.pause",
        call=engine.pause_alert,
    )


def crypto_alerts_resume(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    engine = _alert_engine()
    return _set_alert_state(
        user_id, int(arguments["alert_id"]),
        capability="crypto.alerts.resume", tool="pulsesoc.crypto_alerts.resume",
        call=engine.resume_alert,
    )


def crypto_alerts_delete(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    engine = _alert_engine()
    return _set_alert_state(
        user_id, int(arguments["alert_id"]),
        capability="crypto.alerts.delete", tool="pulsesoc.crypto_alerts.delete",
        call=engine.delete_alert,
    )


def crypto_alerts_create(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    started = time.perf_counter()
    engine = _alert_engine()
    condition = clean(arguments["condition"], 40)
    alert_type = "move_24h" if condition in {"moves_up_percent", "moves_down_percent", "volatility_above"} else "coin_price"
    outcome = engine.create_alert_rule(
        int(user_id),
        alert_type=alert_type,
        symbol=clean(arguments["symbol"], 24),
        condition=condition,
        threshold=float(arguments["threshold"]),
        channels={"in_app": True, "push": True},
        source="undx_agent",
        # The idempotency key travels into the row so a duplicate submission is
        # detectable after the fact, not only at the gateway.
        source_ref=clean(arguments.get("_idempotency_key") or "", 160),
    ) or {}
    if not outcome.get("ok"):
        return _fail("pulsesoc.crypto_alerts.create", "crypto.alerts.create",
                     "write_rejected",
                     clean(outcome.get("message") or "PulseSoc did not accept that alert.", 200),
                     started=started)
    alert_id = int(outcome.get("alert_id") or 0)
    record = _alert_record(outcome.get("alert") or {})
    return ToolResult(
        ok=True,
        tool_name="pulsesoc.crypto_alerts.create",
        capability_id="crypto.alerts.create",
        canonical_resource_id=f"alert_rule:{alert_id}",
        records=[record] if record else [],
        data={"alert_id": alert_id},
        latency_ms=_timed(started),
    )


def crypto_alerts_update(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    started = time.perf_counter()
    engine = _alert_engine()
    alert_id = int(arguments["alert_id"])
    existing = engine.get_alert_rule(alert_id, int(user_id))
    if not existing:
        return _fail("pulsesoc.crypto_alerts.update", "crypto.alerts.update",
                     "resource_not_found", "UNDX could not find that alert on your account.",
                     started=started)
    payload: dict[str, Any] = {"targetValue": float(arguments["threshold"])}
    if arguments.get("condition"):
        payload["condition"] = clean(arguments["condition"], 40)
    outcome = engine.update_alert_rule(alert_id, int(user_id), payload) or {}
    if not outcome.get("ok"):
        return _fail("pulsesoc.crypto_alerts.update", "crypto.alerts.update",
                     "write_rejected",
                     clean(outcome.get("message") or "PulseSoc did not accept that change.", 200),
                     started=started)
    return ToolResult(
        ok=True,
        tool_name="pulsesoc.crypto_alerts.update",
        capability_id="crypto.alerts.update",
        canonical_resource_id=f"alert_rule:{alert_id}",
        data={"alert_id": alert_id, "threshold": float(arguments["threshold"])},
        latency_ms=_timed(started),
    )


# ---------------------------------------------------------------------------
# Notification preferences — backed by services.pulsesoc_notification_system
# ---------------------------------------------------------------------------


def _notifications():
    from services import pulsesoc_notification_system

    return pulsesoc_notification_system


# The words a person uses are not the column names PulseSoc stores. "Reels" is a
# surface in the app; the notification category behind it is ``likes``. Without this
# map a write to "reels" creates a category the notification pipeline never consults,
# and — worse — the read-back of a category that does not exist returns False, so
# UNDX would report reel notifications as already off for a user who never touched
# them. Every category the registry offers must appear here, and
# ``test_notification_categories_are_real`` asserts each target really exists.
CATEGORY_ALIASES: dict[str, str] = {
    "global": "global",     # the master switch, stored under ``experience``
    "posts": "social",
    "reels": "likes",
    "messages": "messages",
    "calls": "calls",
    "alerts": "crypto",
}


def resolve_category(category: str) -> str:
    """Translate a UNDX-facing category into the one the notification store uses."""
    name = clean(category, 40).lower()
    return CATEGORY_ALIASES.get(name, name)


def read_push_value(preferences: dict[str, Any], category: str) -> bool:
    """Extract one push flag from the preferences document.

    The global switch lives under ``experience`` while per-category switches live
    under ``preferences``; both the executor and the verifier read through this one
    function so a mutation and its read-back can never disagree merely because they
    parsed the same document differently.
    """
    resolved = resolve_category(category)
    if resolved == "global":
        return bool((preferences.get("experience") or {}).get("enable_push_notifications"))
    return bool(((preferences.get("preferences") or {}).get(resolved) or {}).get("push"))


def notification_preferences_read(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    started = time.perf_counter()
    system = _notifications()
    category = clean(arguments.get("category") or "global", 40)
    preferences = system.get_preferences(int(user_id)) or {}
    value = read_push_value(preferences, category)
    return ToolResult(
        ok=True,
        tool_name="pulsesoc.notification_preferences.read",
        capability_id="notifications.preference.read",
        canonical_resource_id=f"user:{int(user_id)}:{resolve_category(category)}",
        data={"category": category, "push": value},
        records=[{"category": category, "push": value}],
        latency_ms=_timed(started),
    )


def notification_preferences_update(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    started = time.perf_counter()
    system = _notifications()
    category = clean(arguments["category"], 40)
    proposed = bool(arguments["push"])
    before = system.get_preferences(int(user_id)) or {}
    observed_before = read_push_value(before, category)

    expected_before = arguments.get("_expected_current_push")
    if expected_before is not None and bool(expected_before) != observed_before:
        # The world moved between approval and execution. Applying the write anyway
        # would silently overwrite whatever changed it, so the operation stops and
        # asks for a fresh decision instead.
        return _fail("pulsesoc.notification_preferences.update",
                     "notifications.preference.update",
                     "stale_state",
                     "That setting changed after UNDX prepared this action. Review it and confirm again.",
                     started=started)

    resolved = resolve_category(category)
    if resolved == "global":
        payload: dict[str, Any] = {"enable_push_notifications": proposed}
    else:
        current_category = dict((before.get("preferences") or {}).get(resolved) or {})
        payload = {resolved: {**current_category, "push": proposed}}
    system.update_preferences(int(user_id), payload)
    return ToolResult(
        ok=True,
        tool_name="pulsesoc.notification_preferences.update",
        capability_id="notifications.preference.update",
        canonical_resource_id=f"user:{int(user_id)}:{resolved}",
        data={"category": category, "push": proposed, "previous": observed_before},
        latency_ms=_timed(started),
    )


# ---------------------------------------------------------------------------
# Saved content — backed by services.saved_content_service
# ---------------------------------------------------------------------------


def saved_items_list(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    started = time.perf_counter()
    from services import saved_content_service

    records = saved_content_service.list_saved_items(
        int(user_id),
        content_type=clean(arguments.get("content_type") or "all", 40),
        query=clean(arguments.get("query"), 120),
        limit=int(arguments.get("limit") or 20),
    )
    return ToolResult(
        ok=True,
        tool_name="pulsesoc.saved_items.list",
        capability_id="saved.items.list",
        canonical_resource_id=f"user:{int(user_id)}:saved",
        data={"count": len(records), "content_type": clean(arguments.get("content_type") or "all", 40)},
        records=records,
        latency_ms=_timed(started),
    )


def saved_post_set(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    started = time.perf_counter()
    from services.saved_content_service import set_post_saved

    post_id = int(arguments.get("post_id") or 0)
    desired = bool(arguments.get("saved"))
    outcome = set_post_saved(int(user_id), post_id, saved=desired)
    if not outcome.get("ok"):
        return _fail(
            "pulsesoc.saved_posts.set",
            "saved.post.set",
            clean(outcome.get("error") or "write_rejected", 80),
            "UNDX could not find that post or change its Saved state.",
            started=started,
        )
    return ToolResult(
        ok=True,
        tool_name="pulsesoc.saved_posts.set",
        capability_id="saved.post.set",
        canonical_resource_id=f"post:{int(outcome['post_id'])}",
        data={
            "post_id": int(outcome["post_id"]),
            "saved": bool(outcome["saved"]),
            "changed": bool(outcome.get("changed")),
        },
        latency_ms=_timed(started),
    )


def social_relationships_list(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    started = time.perf_counter()
    from services.social_relationship_service import list_relationships

    direction = clean(arguments.get("direction") or "followers", 20).lower()
    records = list_relationships(
        int(user_id),
        direction=direction,
        query=clean(arguments.get("query"), 120),
        limit=int(arguments.get("limit") or 20),
    )
    return ToolResult(
        ok=True,
        tool_name="pulsesoc.relationships.list",
        capability_id="social.followers.list",
        canonical_resource_id=f"user:{int(user_id)}:{direction}",
        records=records,
        data={"direction": direction, "record_count": len(records)},
        latency_ms=_timed(started),
    )


def _set_following(
    user_id: int,
    arguments: dict[str, Any],
    *,
    following: bool,
    capability_id: str,
    tool_name: str,
) -> ToolResult:
    started = time.perf_counter()
    from services.social_relationship_service import set_following

    target_id = int(arguments.get("target_user_id") or 0)
    outcome = set_following(int(user_id), target_id, following=following)
    if not outcome.get("ok"):
        return _fail(
            tool_name,
            capability_id,
            clean(outcome.get("error") or "write_rejected", 80),
            "UNDX could not change that follow relationship.",
            started=started,
        )
    return ToolResult(
        ok=True,
        tool_name=tool_name,
        capability_id=capability_id,
        canonical_resource_id=f"follow:{int(user_id)}:{target_id}",
        data={
            "target_user_id": target_id,
            "following": bool(outcome["following"]),
            "changed": bool(outcome.get("changed")),
        },
        latency_ms=_timed(started),
    )


def social_follow(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    return _set_following(
        user_id, arguments, following=True,
        capability_id="social.follow", tool_name="pulsesoc.relationships.follow",
    )


def social_unfollow(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    return _set_following(
        user_id, arguments, following=False,
        capability_id="social.unfollow", tool_name="pulsesoc.relationships.unfollow",
    )


def conversations_list(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    started = time.perf_counter()
    from services.messenger_intelligence_service import list_my_conversations

    records = list_my_conversations(
        int(user_id),
        conversation_type=clean(arguments.get("conversation_type") or "all", 40),
        limit=int(arguments.get("limit") or 20),
    )
    return ToolResult(
        ok=True,
        tool_name="pulsesoc.conversations.list",
        capability_id="conversations.list",
        canonical_resource_id=f"user:{int(user_id)}:conversations",
        records=records,
        data={"record_count": len(records)},
        latency_ms=_timed(started),
    )


def messages_list(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    started = time.perf_counter()
    from services.messenger_intelligence_service import list_conversation_messages

    conversation_id = int(arguments.get("conversation_id") or 0)
    records = list_conversation_messages(
        int(user_id), conversation_id, limit=int(arguments.get("limit") or 30),
    )
    return ToolResult(
        ok=True,
        tool_name="pulsesoc.messages.list",
        capability_id="messages.list",
        canonical_resource_id=f"conversation:{conversation_id}:messages",
        records=records,
        data={"record_count": len(records), "conversation_id": conversation_id},
        latency_ms=_timed(started),
    )

def messages_search(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    started = time.perf_counter()
    from services.messenger_intelligence_service import search_messages

    conversation_id = int(arguments.get("conversation_id") or 0)
    records = search_messages(
        int(user_id), clean(arguments.get("query"), 120),
        conversation_id=conversation_id, limit=int(arguments.get("limit") or 30),
    )
    return ToolResult(
        ok=True, tool_name="pulsesoc.messages.search", capability_id="messages.search",
        canonical_resource_id=f"user:{int(user_id)}:message-search",
        records=records,
        data={"record_count": len(records), "conversation_id": conversation_id},
        latency_ms=_timed(started),
    )


def conversation_summarize(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    started = time.perf_counter()
    from services.messenger_intelligence_service import summarize_conversation

    conversation_id = int(arguments.get("conversation_id") or 0)
    record = summarize_conversation(
        int(user_id), conversation_id, limit=int(arguments.get("limit") or 50),
    )
    if not record:
        return _fail(
            "pulsesoc.conversations.summarize", "conversations.summarize", "not_found",
            "UNDX could not summarize a conversation you are allowed to view.", started=started,
        )
    return ToolResult(
        ok=True, tool_name="pulsesoc.conversations.summarize",
        capability_id="conversations.summarize",
        canonical_resource_id=f"conversation:{conversation_id}",
        records=[record], data=record, latency_ms=_timed(started),
    )


def messages_suggest(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    started = time.perf_counter()
    from services.messenger_intelligence_service import suggested_responses

    conversation_id = int(arguments.get("conversation_id") or 0)
    records = suggested_responses(int(user_id), conversation_id)
    return ToolResult(
        ok=True, tool_name="pulsesoc.messages.suggest", capability_id="messages.suggest",
        canonical_resource_id=f"conversation:{conversation_id}:suggestions",
        records=records, data={"conversation_id": conversation_id, "record_count": len(records)},
        latency_ms=_timed(started),
    )


def message_draft(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    started = time.perf_counter()
    from services.messenger_intelligence_service import prepare_reply_draft

    conversation_id = int(arguments.get("conversation_id") or 0)
    record = prepare_reply_draft(int(user_id), conversation_id, clean(arguments.get("body"), 2000))
    if not record:
        return _fail(
            "pulsesoc.messages.draft", "messages.draft", "not_found",
            "UNDX could not prepare a draft for that conversation.", started=started,
        )
    return ToolResult(
        ok=True, tool_name="pulsesoc.messages.draft", capability_id="messages.draft",
        canonical_resource_id=clean(record.get("draft_id"), 100),
        records=[record], data=record, latency_ms=_timed(started),
    )


def feed_posts_list(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    started = time.perf_counter()
    from services.feed_intelligence_service import list_posts

    records = list_posts(
        int(user_id),
        feed=clean(arguments.get("feed") or "for_you", 40),
        query=clean(arguments.get("query"), 80),
        limit=int(arguments.get("limit") or 20),
    )
    return ToolResult(
        ok=True, tool_name="pulsesoc.feed.posts.list", capability_id="feed.posts.list",
        canonical_resource_id=f"user:{int(user_id)}:feed", records=records,
        data={"record_count": len(records)}, latency_ms=_timed(started),
    )


def feed_posts_get(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    started = time.perf_counter()
    from services.feed_intelligence_service import get_post

    post_id = int(arguments.get("post_id") or 0)
    record = get_post(int(user_id), post_id)
    if not record:
        return _fail("pulsesoc.feed.posts.get", "feed.posts.get", "not_found",
                     "UNDX could not find a post you are allowed to view.", started=started)
    return ToolResult(
        ok=True, tool_name="pulsesoc.feed.posts.get", capability_id="feed.posts.get",
        canonical_resource_id=f"post:{post_id}", records=[record], data=record,
        latency_ms=_timed(started),
    )


def feed_comments_list(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    started = time.perf_counter()
    from services.feed_intelligence_service import list_post_comments

    post_id = int(arguments.get("post_id") or 0)
    records = list_post_comments(
        int(user_id), post_id, limit=int(arguments.get("limit") or 40),
    )
    return ToolResult(
        ok=True, tool_name="pulsesoc.feed.comments.list", capability_id="comments.list",
        canonical_resource_id=f"post:{post_id}:comments", records=records,
        data={"post_id": post_id, "record_count": len(records)}, latency_ms=_timed(started),
    )

def feed_post_performance_summary(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    started = time.perf_counter()
    from services.feed_intelligence_service import post_performance_summary

    post_id = int(arguments.get("post_id") or 0)
    record = post_performance_summary(int(user_id), post_id)
    if not record:
        return _fail(
            "pulsesoc.feed.post.performance.summary", "feed.post.performance.summary",
            "not_found", "UNDX could not find one of your posts with that ID.", started=started,
        )
    return ToolResult(
        ok=True, tool_name="pulsesoc.feed.post.performance.summary",
        capability_id="feed.post.performance.summary",
        canonical_resource_id=f"post:{post_id}", records=[record], data=record,
        latency_ms=_timed(started),
    )


def feed_comments_summary(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    started = time.perf_counter()
    from services.feed_intelligence_service import summarize_post_comments

    post_id = int(arguments.get("post_id") or 0)
    record = summarize_post_comments(
        int(user_id), post_id, limit=int(arguments.get("limit") or 40),
    )
    if not record:
        return _fail(
            "pulsesoc.feed.comments.summary", "feed.comments.summary",
            "not_found", "UNDX could not summarize comments for a post you own.", started=started,
        )
    return ToolResult(
        ok=True, tool_name="pulsesoc.feed.comments.summary",
        capability_id="feed.comments.summary",
        canonical_resource_id=f"post:{post_id}:comment-summary",
        records=[record], data=record, latency_ms=_timed(started),
    )

def _set_post_like(
    user_id: int,
    arguments: dict[str, Any],
    *,
    liked: bool,
    capability_id: str,
    tool_name: str,
) -> ToolResult:
    started = time.perf_counter()
    from services.feed_intelligence_service import set_post_like

    post_id = int(arguments.get("post_id") or 0)
    outcome = set_post_like(int(user_id), post_id, liked=liked)
    if not outcome.get("ok"):
        return _fail(
            tool_name, capability_id, clean(outcome.get("error") or "write_rejected", 80),
            "UNDX could not change your reaction on that post.", started=started,
        )
    return ToolResult(
        ok=True, tool_name=tool_name, capability_id=capability_id,
        canonical_resource_id=f"post:{post_id}",
        data={
            "post_id": post_id,
            "liked": bool(outcome["liked"]),
            "changed": bool(outcome.get("changed")),
        },
        latency_ms=_timed(started),
    )


def feed_post_like(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    return _set_post_like(
        user_id, arguments, liked=True,
        capability_id="feed.posts.like", tool_name="pulsesoc.feed.posts.like",
    )


def feed_post_unlike(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    return _set_post_like(
        user_id, arguments, liked=False,
        capability_id="feed.posts.unlike", tool_name="pulsesoc.feed.posts.unlike",
    )


def feed_post_delete(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    started = time.perf_counter()
    from services.pulse_feed_engine import delete_owned_post

    post_id = int(arguments.get("post_id") or 0)
    outcome = delete_owned_post(int(user_id), post_id)
    if not outcome.get("ok"):
        return _fail(
            "pulsesoc.feed.posts.delete", "feed.posts.delete",
            clean(outcome.get("error") or "write_rejected", 80),
            "UNDX could not delete a matching post owned by your account.",
            started=started,
        )
    return ToolResult(
        ok=True,
        tool_name="pulsesoc.feed.posts.delete",
        capability_id="feed.posts.delete",
        canonical_resource_id=f"post:{post_id}",
        data={"post_id": post_id, "deleted": True, "changed": bool(outcome.get("changed"))},
        latency_ms=_timed(started),
    )


def _content_read(user_id: int, arguments: dict[str, Any], capability: str, function: str,
                  canonical_key: str) -> ToolResult:
    started = time.perf_counter()
    from services import content_graph_intelligence_service as graph
    call = getattr(graph, function)
    kwargs = {key: value for key, value in arguments.items() if not key.startswith("_")}
    value = call(int(user_id), **kwargs)
    records = value if isinstance(value, list) else ([value] if value else [])
    if value is None:
        return _fail(f"pulsesoc.{capability}", capability, "not_found",
                     "UNDX could not find an authorized matching item.", started=started)
    target = 0
    if records and canonical_key:
        target = records[0].get(canonical_key) or 0
    return ToolResult(
        ok=True, tool_name=f"pulsesoc.{capability}", capability_id=capability,
        canonical_resource_id=f"{canonical_key.removesuffix('_id')}:{target}" if target else f"user:{int(user_id)}",
        records=records, data=value if isinstance(value, dict) else {"count": len(records)},
        latency_ms=_timed(started),
    )


def reels_search(u, a): return _content_read(u, a, "reels.search", "list_reels", "reel_id")
def reels_get(u, a): return _content_read(u, a, "reels.get", "get_reel", "reel_id")
def reels_performance(u, a): return _content_read(u, a, "reels.performance.summary", "reel_performance", "reel_id")
def reels_comments_summary(u, a): return _content_read(u, a, "reels.comments.summary", "reel_comment_summary", "reel_id")
def statuses_list(u, a): return _content_read(u, a, "status.list", "list_statuses", "status_id")
def statuses_get(u, a): return _content_read(u, a, "status.get", "get_status", "status_id")
def status_viewers(u, a): return _content_read(u, a, "status.viewer.summary", "status_viewer_summary", "status_id")
def status_reactions(u, a): return _content_read(u, a, "status.reaction.summary", "status_reaction_summary", "status_id")
def profile_get(u, a): return _content_read(u, a, "profile.get", "get_profile", "user_id")
def profile_activity(u, a): return _content_read(u, a, "profile.activity.summary", "profile_activity_summary", "user_id")
def profile_relationships(u, a): return _content_read(u, a, "profile.relationship.summary", "profile_relationship_summary", "user_id")


def profile_preferences_update(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    started = time.perf_counter()
    from services.content_graph_intelligence_service import update_profile_preferences
    outcome = update_profile_preferences(
        int(user_id), preferred_language=clean(arguments.get("preferred_language"), 8),
    )
    if not outcome.get("ok"):
        return _fail("pulsesoc.profile.preferences.update", "profile.preferences.update",
                     clean(outcome.get("error"), 80), "UNDX could not update that preference.",
                     started=started)
    return ToolResult(
        ok=True, tool_name="pulsesoc.profile.preferences.update",
        capability_id="profile.preferences.update", canonical_resource_id=f"user:{int(user_id)}",
        data=outcome, latency_ms=_timed(started),
    )


def _reel_write(user_id: int, arguments: dict[str, Any], capability: str, function: str,
                desired: bool) -> ToolResult:
    started = time.perf_counter()
    from services import content_graph_intelligence_service as graph
    reel_id = int(arguments.get("reel_id") or 0)
    outcome = getattr(graph, function)(int(user_id), reel_id, **{
        "saved" if "save" in capability else "liked": desired,
    })
    if not outcome.get("ok"):
        return _fail(f"pulsesoc.{capability}", capability, clean(outcome.get("error"), 80),
                     "UNDX could not update that Reel.", started=started)
    return ToolResult(
        ok=True, tool_name=f"pulsesoc.{capability}", capability_id=capability,
        canonical_resource_id=f"reel:{reel_id}",
        data={"reel_id": reel_id, "saved" if "save" in capability else "liked": desired,
              "changed": bool(outcome.get("changed"))}, latency_ms=_timed(started),
    )


def reels_save(u, a): return _reel_write(u, a, "reels.save", "set_reel_saved", True)
def reels_unsave(u, a): return _reel_write(u, a, "reels.unsave", "set_reel_saved", False)
def reels_like(u, a): return _reel_write(u, a, "reels.like", "set_reel_liked", True)
def reels_unlike(u, a): return _reel_write(u, a, "reels.unlike", "set_reel_liked", False)


# ---------------------------------------------------------------------------
# Phase 3B personal intelligence (read-only)
# ---------------------------------------------------------------------------

def _personal_read(user_id: int, arguments: dict[str, Any], capability: str,
                   function: str, canonical_key: str = "") -> ToolResult:
    started = time.perf_counter()
    from services import undx_personal_intelligence_service as personal
    kwargs = {key: value for key, value in arguments.items() if not key.startswith("_")}
    # Every personal read runs inside a degradation collector, not only the read
    # models that opened one of their own. A read whose SQL raised returns [] and is
    # otherwise indistinguishable from a genuinely empty result, so without this the
    # gateway would stamp a broken query 'verified' and UNDX would report an
    # authoritative nothing. The names collected here travel on the result and are
    # what stop the gateway calling a degraded read verified.
    with personal.collecting() as degraded:
        value = getattr(personal, function)(int(user_id), **kwargs)
        degraded_sources = sorted(degraded)
    if value is None:
        return _fail(f"pulsesoc.{capability}", capability, "not_found",
                     "UNDX could not find an authorized matching item.", started=started)
    records = value if isinstance(value, list) else (
        list(value.get("items") or value.get("facts") or value.get("campaigns") or [])
        if isinstance(value, dict) else []
    )
    canonical = f"user:{int(user_id)}"
    if canonical_key and isinstance(value, dict):
        target = value.get(canonical_key) or (value.get("data") or {}).get(canonical_key)
        if target:
            canonical = f"{canonical_key.removesuffix('_id')}:{target}"
    data = value if isinstance(value, dict) else {"count": len(records), "items": records}
    if degraded_sources:
        # Written unconditionally rather than merged, so a read model that computed
        # its own optimistic 'complete' cannot outvote an observed failure.
        data = dict(data)
        data["complete"] = False
        data["degraded_sources"] = degraded_sources
    return ToolResult(
        ok=True, tool_name=f"pulsesoc.{capability}", capability_id=capability,
        canonical_resource_id=canonical, records=records,
        data=data, degraded_sources=degraded_sources,
        latency_ms=_timed(started),
    )


def activity_daily_summary(u, a): return _personal_read(u, a, "activity.daily_summary", "activity_daily_summary")
def notifications_inbox_list(u, a): return _personal_read(u, a, "notifications.inbox.list", "notifications_inbox")
def notifications_explain(u, a): return _personal_read(u, a, "notifications.explain", "notification_explain", "notification_id")
def notifications_group_summary(u, a): return _personal_read(u, a, "notifications.group_summary", "notification_group_summary")
def search_global(u, a): return _personal_read(u, a, "search.global", "search_global")
def search_people(u, a): return _personal_read(u, a, "search.people", "search_people")
def search_content(u, a): return _personal_read(u, a, "search.content", "search_content")
def search_messages(u, a): return _personal_read(u, a, "search.messages", "search_messages")
def search_activity(u, a): return _personal_read(u, a, "search.activity", "search_activity")
def settings_inspect(u, a): return _personal_read(u, a, "settings.inspect", "settings_inspect")
def settings_explain(u, a): return _personal_read(u, a, "settings.explain", "settings_explain")
def settings_recommend(u, a): return _personal_read(u, a, "settings.recommend", "settings_recommend")
def security_sessions_list(u, a): return _personal_read(u, a, "security.sessions.list", "security_sessions")
def security_activity_summary(u, a): return _personal_read(u, a, "security.activity.summary", "security_activity_summary")
def security_device_list(u, a): return _personal_read(u, a, "security.device.list", "security_devices")
def marketplace_search(u, a): return _personal_read(u, a, "marketplace.search", "marketplace_search")
def marketplace_listing_summary(u, a): return _personal_read(u, a, "marketplace.listing.summary", "marketplace_listing_summary", "listing_id")
def marketplace_order_status(u, a): return _personal_read(u, a, "marketplace.order.status", "marketplace_order_status", "order_id")
def premium_status(u, a): return _personal_read(u, a, "premium.status", "premium_status")
def premium_entitlements(u, a): return _personal_read(u, a, "premium.entitlements", "premium_entitlements")
def ads_performance_summary(u, a): return _personal_read(u, a, "ads.performance.summary", "ads_performance_summary")
def live_search(u, a): return _personal_read(u, a, "live.search", "live_search")
def live_summary(u, a): return _personal_read(u, a, "live.summary", "live_summary", "live_id")
def live_performance(u, a): return _personal_read(u, a, "live.performance", "live_performance", "live_id")
def learning_search(u, a): return _personal_read(u, a, "learning.search", "learning_search")
def learning_progress(u, a): return _personal_read(u, a, "learning.progress", "learning_progress")
def memory_activity_inspect(u, a): return _personal_read(u, a, "memory.activity.inspect", "memory_activity_inspect")
def groups_list(u, a): return _personal_read(u, a, "groups.list", "groups_list")
def groups_search(u, a): return _personal_read(u, a, "groups.search", "groups_search")
def events_upcoming(u, a): return _personal_read(u, a, "events.upcoming", "events_upcoming")
def music_search(u, a): return _personal_read(u, a, "music.search", "music_search")
def account_health_summary(u, a): return _personal_read(u, a, "account.health.summary", "account_health_summary")
def verification_status(u, a): return _personal_read(u, a, "verification.status", "verification_status")
def support_tickets_list(u, a): return _personal_read(u, a, "support.tickets.list", "support_tickets_list")
def creator_analytics_summary(u, a): return _personal_read(u, a, "creator.analytics.summary", "creator_analytics_summary")
def localization_preferences(u, a): return _personal_read(u, a, "localization.preferences", "localization_preferences")
def crypto_portfolio_summary(u, a): return _personal_read(u, a, "crypto.portfolio.summary", "crypto_portfolio_summary")
def crypto_market_window(u, a): return _personal_read(u, a, "crypto.market.window", "crypto_market_window")


def translation_content_translate(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    started = time.perf_counter()
    from services.content_translation import TranslationError, translate_content

    content_ref = int(arguments.get("content_ref") or 0)
    try:
        outcome = translate_content(
            int(user_id),
            content_type=clean(arguments.get("content_type"), 24),
            content_ref=content_ref,
            text="",  # The service resolves canonical text and ignores caller text.
            source_language=clean(arguments.get("source_language") or "auto", 16),
            target_language=clean(arguments.get("target_language"), 16),
            force=True,
        )
    except TranslationError as exc:
        return _fail(
            "pulsesoc.translation.content.translate", "translation.content.translate",
            clean(exc.code, 80), clean(str(exc), 200), started=started,
        )
    return ToolResult(
        ok=True,
        tool_name="pulsesoc.translation.content.translate",
        capability_id="translation.content.translate",
        canonical_resource_id=f"{clean(arguments.get('content_type'), 24)}:{content_ref}",
        data={key: outcome.get(key) for key in (
            "status", "original_text", "translated_text", "source_language",
            "target_language", "provider", "provider_model", "content_version", "cached",
        )},
        latency_ms=_timed(started),
    )
def presence_privacy_status(u, a): return _personal_read(u, a, "presence.privacy.status", "presence_privacy_status")


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

EXECUTORS: dict[str, Callable[[int, dict[str, Any]], ToolResult]] = {
    "crypto_alerts_list": crypto_alerts_list,
    "crypto_alerts_get": crypto_alerts_get,
    "crypto_alerts_pause": crypto_alerts_pause,
    "crypto_alerts_resume": crypto_alerts_resume,
    "crypto_alerts_create": crypto_alerts_create,
    "crypto_alerts_update": crypto_alerts_update,
    "crypto_alerts_delete": crypto_alerts_delete,
    "notification_preferences_read": notification_preferences_read,
    "notification_preferences_update": notification_preferences_update,
    "saved_items_list": saved_items_list,
    "saved_post_set": saved_post_set,
    "social_relationships_list": social_relationships_list,
    "social_follow": social_follow,
    "social_unfollow": social_unfollow,
    "conversations_list": conversations_list,
    "messages_list": messages_list,
    "messages_search": messages_search,
    "conversation_summarize": conversation_summarize,
    "messages_suggest": messages_suggest,
    "message_draft": message_draft,
    "feed_posts_list": feed_posts_list,
    "feed_posts_get": feed_posts_get,
    "feed_comments_list": feed_comments_list,
    "feed_post_performance_summary": feed_post_performance_summary,
    "feed_comments_summary": feed_comments_summary,
    "feed_post_like": feed_post_like,
    "feed_post_unlike": feed_post_unlike,
    "feed_post_delete": feed_post_delete,
    "reels_search": reels_search,
    "reels_get": reels_get,
    "reels_performance": reels_performance,
    "reels_comments_summary": reels_comments_summary,
    "reels_save": reels_save,
    "reels_unsave": reels_unsave,
    "reels_like": reels_like,
    "reels_unlike": reels_unlike,
    "statuses_list": statuses_list,
    "statuses_get": statuses_get,
    "status_viewers": status_viewers,
    "status_reactions": status_reactions,
    "profile_get": profile_get,
    "profile_activity": profile_activity,
    "profile_relationships": profile_relationships,
    "profile_preferences_update": profile_preferences_update,
    "activity_daily_summary": activity_daily_summary,
    "notifications_inbox_list": notifications_inbox_list,
    "notifications_explain": notifications_explain,
    "notifications_group_summary": notifications_group_summary,
    "search_global": search_global,
    "search_people": search_people,
    "search_content": search_content,
    "search_messages": search_messages,
    "search_activity": search_activity,
    "settings_inspect": settings_inspect,
    "settings_explain": settings_explain,
    "settings_recommend": settings_recommend,
    "security_sessions_list": security_sessions_list,
    "security_activity_summary": security_activity_summary,
    "security_device_list": security_device_list,
    "marketplace_search": marketplace_search,
    "marketplace_listing_summary": marketplace_listing_summary,
    "marketplace_order_status": marketplace_order_status,
    "premium_status": premium_status,
    "premium_entitlements": premium_entitlements,
    "ads_performance_summary": ads_performance_summary,
    "live_search": live_search,
    "live_summary": live_summary,
    "live_performance": live_performance,
    "learning_search": learning_search,
    "learning_progress": learning_progress,
    "memory_activity_inspect": memory_activity_inspect,
    "groups_list": groups_list,
    "groups_search": groups_search,
    "events_upcoming": events_upcoming,
    "music_search": music_search,
    "account_health_summary": account_health_summary,
    "verification_status": verification_status,
    "support_tickets_list": support_tickets_list,
    "creator_analytics_summary": creator_analytics_summary,
    "localization_preferences": localization_preferences,
    "crypto_portfolio_summary": crypto_portfolio_summary,
    "crypto_market_window": crypto_market_window,
    "translation_content_translate": translation_content_translate,
    "presence_privacy_status": presence_privacy_status,
}


def resolve(name: str) -> Callable[[int, dict[str, Any]], ToolResult]:
    executor = EXECUTORS.get(clean(name, 80))
    if executor is None:
        # A registry entry naming a non-existent executor is a deployment defect.
        # Surfacing it as "unsupported" keeps the user safe while the audit trail
        # records the real cause.
        raise AgentError(
            "executor_missing",
            "UNDX cannot do that yet.",
            outcome=AgentOutcome.UNSUPPORTED_CAPABILITY,
            details={"executor": clean(name, 80)},
        )
    return executor


__all__ = ["EXECUTORS", "resolve", "read_push_value"]
