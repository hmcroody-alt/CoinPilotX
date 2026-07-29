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
_ALERT_FIELDS = ("id", "symbol", "condition", "threshold_value", "status", "active",
                 "alert_type", "created_at", "updated_at", "trigger_count")


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
    metadata = rule.get("metadata")
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except (TypeError, ValueError):
            metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}
    record["display_name"] = clean(
        metadata.get("note") or f"{record.get('symbol') or 'Crypto'} alert",
        80,
    )
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
    """
    started = time.perf_counter()
    engine = _alert_engine()
    limit = int(arguments.get("limit") or 20)
    fetched = [_alert_record(rule)
               for rule in ((engine.list_alert_rules(int(user_id), limit=limit + 1) or {})
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
    "feed_posts_list": feed_posts_list,
    "feed_posts_get": feed_posts_get,
    "feed_comments_list": feed_comments_list,
    "feed_post_like": feed_post_like,
    "feed_post_unlike": feed_post_unlike,
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
