"""Independent read-back verification for every write capability.

A mutation's own response is the *claim*. This module produces the *evidence*. The
distinction matters because almost every way an agent lies to a user routes through
trusting the former: a service returns ``{"ok": true}`` while a filter silently
matched zero rows, a write lands on a replica that has not caught up, or a
capability updates an adjacent field and reports success for the one it was asked
about. In each case the mutation response is honest about what the code did and
wrong about what the user asked for.

So every verifier here re-reads state through a *separate* call and compares it to
what was requested. Verifiers never look at the tool's return value to decide
whether it worked; they take the requested arguments and go and look.

The four states are not interchangeable:

``verified``
    We read the state back and it matches. Only this permits "done".
``verification_failed``
    We read the state back and it did **not** match. Loud: something is wrong.
``verification_pending``
    We could not read it back right now, but a later read might succeed.
``impossible_to_verify``
    There is no read path for this property at all. Never a transient condition.
"""

from __future__ import annotations

from typing import Any, Callable

from services.undx_agent_contracts import (
    ToolResult,
    VerificationResult,
    VerificationState,
    clean,
    describe_alert,
)


def _alert_engine():
    from services import alert_engine

    return alert_engine


def _notifications():
    from services import pulsesoc_notification_system

    return pulsesoc_notification_system


def _unreadable(detail: str, expected: Any = None) -> VerificationResult:
    """A read-back that could not be performed, as distinct from one that failed."""
    return VerificationResult(
        state=VerificationState.PENDING,
        expected=expected,
        detail=clean(detail, 200),
    )


# ---------------------------------------------------------------------------
# Crypto alerts
# ---------------------------------------------------------------------------


#: Which persisted status each capability is expected to produce.
_EXPECTED_STATUS = {
    "crypto.alerts.pause": "paused",
    "crypto.alerts.resume": "active",
}


def crypto_alert_status(user_id: int, arguments: dict[str, Any], result: ToolResult) -> VerificationResult:
    """Confirm the alert really holds the status the capability requested."""
    expected = _EXPECTED_STATUS.get(result.capability_id, "")
    if not expected:
        return VerificationResult(
            state=VerificationState.IMPOSSIBLE,
            detail="No expected status is declared for this capability.",
        )
    alert_id = int(arguments.get("alert_id") or 0)
    try:
        rule = _alert_engine().get_alert_rule(alert_id, int(user_id))
    except Exception as exc:  # pragma: no cover - defensive
        return _unreadable(f"Alert read-back failed: {exc.__class__.__name__}", expected)
    if not rule:
        # The row is gone or was never ours. Either way the requested state is
        # demonstrably absent, which is a failure and not a pending read.
        return VerificationResult(
            state=VerificationState.FAILED,
            expected=expected,
            observed=None,
            detail="The alert could not be read back on this account.",
        )
    observed = clean(rule.get("status") or "active", 24)
    return VerificationResult(
        state=VerificationState.VERIFIED if observed == expected else VerificationState.FAILED,
        expected=expected,
        observed=observed,
        evidence={
            "canonical_resource_id": f"alert_rule:{alert_id}",
            "read_back": {"status": observed, "active": int(rule.get("active") or 0)},
            # Named from the row this function just read back, and from nothing else.
            # The prose layer has no other honest way to say *which* alert changed: it
            # sees an id and a status, and "the current value is paused" was what that
            # left on screen — true, unfalsifiable, and useless to somebody with four
            # alerts. Composed here rather than there because here is where the record
            # is, and because a label built from the request instead of the read-back
            # would describe the alert the person meant rather than the one that moved.
            "subject": describe_alert(rule),
            "source": "alert_engine.get_alert_rule",
        },
    )


def crypto_alert_exists(user_id: int, arguments: dict[str, Any], result: ToolResult) -> VerificationResult:
    """Confirm a newly created alert exists and carries the requested terms."""
    alert_id = int((result.data or {}).get("alert_id") or 0)
    if not alert_id:
        return VerificationResult(
            state=VerificationState.FAILED,
            expected="a created alert",
            observed=None,
            detail="PulseSoc did not return a canonical alert id.",
        )
    try:
        rule = _alert_engine().get_alert_rule(alert_id, int(user_id))
    except Exception as exc:  # pragma: no cover - defensive
        return _unreadable(f"Alert read-back failed: {exc.__class__.__name__}")
    if not rule:
        return VerificationResult(
            state=VerificationState.FAILED,
            expected="a created alert",
            observed=None,
            detail="The new alert could not be read back on this account.",
        )
    expected = {
        "symbol": clean(arguments.get("symbol"), 24).upper(),
        "condition": clean(arguments.get("condition"), 40),
        "threshold": float(arguments.get("threshold") or 0),
    }
    observed = {
        "symbol": clean(rule.get("symbol") or rule.get("asset_symbol"), 24).upper(),
        "condition": clean(rule.get("condition"), 40),
        "threshold": float(rule.get("threshold_value") or 0),
    }
    matches = (
        observed["symbol"] == expected["symbol"]
        and observed["condition"] == expected["condition"]
        and abs(observed["threshold"] - expected["threshold"]) < 1e-9
    )
    return VerificationResult(
        state=VerificationState.VERIFIED if matches else VerificationState.FAILED,
        expected=expected,
        observed=observed,
        evidence={
            "canonical_resource_id": f"alert_rule:{alert_id}",
            "read_back": observed,
            "source": "alert_engine.get_alert_rule",
        },
    )


def crypto_alert_threshold(user_id: int, arguments: dict[str, Any], result: ToolResult) -> VerificationResult:
    """Confirm an edited alert now holds every value the edit requested.

    Named for the threshold because that is the field the capability requires, but it
    reads back the condition too whenever one was supplied. A verifier that checks a
    subset of what its capability can change is worse than no verifier at all: the
    receipt says "verified" with full confidence while the unchecked field may hold
    anything. ``CapabilitySpec.verified_fields`` now makes that coverage a declared,
    import-time-checked property, and this function is the other half of it — the
    registry refuses to register ``condition`` as verified unless this actually looks.
    """
    alert_id = int(arguments.get("alert_id") or 0)
    try:
        rule = _alert_engine().get_alert_rule(alert_id, int(user_id))
    except Exception as exc:  # pragma: no cover - defensive
        return _unreadable(f"Alert read-back failed: {exc.__class__.__name__}")
    if not rule:
        return VerificationResult(
            state=VerificationState.FAILED,
            expected=arguments.get("threshold"),
            observed=None,
            detail="The alert could not be read back on this account.",
        )
    expected: dict[str, Any] = {"threshold": float(arguments.get("threshold") or 0)}
    observed: dict[str, Any] = {"threshold": float(rule.get("threshold_value") or 0)}
    matches = abs(observed["threshold"] - expected["threshold"]) < 1e-9
    # Only assert on the condition when the edit actually asked for one. The field is
    # optional, and an omitted condition means "leave it alone", not "expect empty".
    if clean(arguments.get("condition"), 40):
        expected["condition"] = clean(arguments.get("condition"), 40)
        observed["condition"] = clean(rule.get("condition"), 40)
        matches = matches and observed["condition"] == expected["condition"]
    return VerificationResult(
        state=VerificationState.VERIFIED if matches else VerificationState.FAILED,
        expected=expected,
        observed=observed,
        evidence={
            "canonical_resource_id": f"alert_rule:{alert_id}",
            "read_back": {"threshold_value": observed["threshold"],
                          **({"condition": observed["condition"]} if "condition" in observed else {})},
            "source": "alert_engine.get_alert_rule",
        },
    )


def crypto_alert_deleted(user_id: int, arguments: dict[str, Any], result: ToolResult) -> VerificationResult:
    """Confirm the alert is really gone.

    Deletion is a soft delete, so "absent from the owner-scoped read" and "present
    with status deleted" are both correct outcomes; anything else is not.
    """
    alert_id = int(arguments.get("alert_id") or 0)
    try:
        rule = _alert_engine().get_alert_rule(alert_id, int(user_id))
    except Exception as exc:  # pragma: no cover - defensive
        return _unreadable(f"Alert read-back failed: {exc.__class__.__name__}", "deleted")
    observed = "absent" if not rule else clean(rule.get("status") or "active", 24)
    deleted = observed in {"absent", "deleted"}
    return VerificationResult(
        state=VerificationState.VERIFIED if deleted else VerificationState.FAILED,
        expected="deleted",
        observed=observed,
        evidence={
            "canonical_resource_id": f"alert_rule:{alert_id}",
            "read_back": {"status": observed},
            "source": "alert_engine.get_alert_rule",
        },
    )


# ---------------------------------------------------------------------------
# Notification preferences
# ---------------------------------------------------------------------------


def notification_preference_value(user_id: int, arguments: dict[str, Any], result: ToolResult) -> VerificationResult:
    """Confirm the stored push flag now equals the requested value."""
    from services.undx_agent_tools import read_push_value, resolve_category

    category = clean(arguments.get("category") or "global", 40)
    stored = resolve_category(category)
    expected = bool(arguments.get("push"))
    try:
        after = _notifications().get_preferences(int(user_id)) or {}
    except Exception as exc:  # pragma: no cover - defensive
        return _unreadable(f"Preference read-back failed: {exc.__class__.__name__}", expected)
    observed = read_push_value(after, category)
    return VerificationResult(
        state=VerificationState.VERIFIED if observed == expected else VerificationState.FAILED,
        expected=expected,
        observed=observed,
        evidence={
            "canonical_resource_id": f"user:{int(user_id)}:{stored}",
            "read_back": {"category": category, "stored_category": stored, "push": observed},
            "source": "pulsesoc_notification_system.get_preferences",
        },
    )


def saved_post_value(user_id: int, arguments: dict[str, Any], result: ToolResult) -> VerificationResult:
    """Independently confirm the caller's stored Saved state for one post."""
    from services.saved_content_service import get_post_saved

    post_id = int(arguments.get("post_id") or 0)
    expected = bool(arguments.get("saved"))
    try:
        state = get_post_saved(int(user_id), post_id)
    except Exception as exc:  # pragma: no cover - defensive
        return _unreadable(f"Saved-post read-back failed: {exc.__class__.__name__}", expected)
    observed = None if state is None else bool(state.get("saved"))
    return VerificationResult(
        state=VerificationState.VERIFIED if observed == expected else VerificationState.FAILED,
        expected=expected,
        observed=observed,
        detail="" if observed == expected else "The post's Saved state did not match the request.",
        evidence={
            "canonical_resource_id": f"post:{post_id}",
            "read_back": {"saved": observed},
            "source": "saved_content_service.get_post_saved",
        },
    )


def social_following_value(user_id: int, arguments: dict[str, Any], result: ToolResult) -> VerificationResult:
    """Read the directed edge back; the reverse relationship is irrelevant."""
    from services.social_relationship_service import is_following

    target_id = int(arguments.get("target_user_id") or 0)
    expected = result.capability_id == "social.follow"
    try:
        observed = is_following(int(user_id), target_id)
    except Exception as exc:  # pragma: no cover - defensive
        return _unreadable(f"Follow read-back failed: {exc.__class__.__name__}", expected)
    return VerificationResult(
        state=VerificationState.VERIFIED if observed == expected else VerificationState.FAILED,
        expected=expected,
        observed=observed,
        detail="" if observed == expected else "The directed follow state did not match the request.",
        evidence={
            "canonical_resource_id": f"follow:{int(user_id)}:{target_id}",
            "read_back": {"following": observed},
            "source": "social_relationship_service.is_following",
        },
    )

def feed_post_like_value(user_id: int, arguments: dict[str, Any], result: ToolResult) -> VerificationResult:
    """Read the caller's reaction edge back after an explicit like-state write."""
    from services.feed_intelligence_service import get_post_like

    post_id = int(arguments.get("post_id") or 0)
    expected = result.capability_id == "feed.posts.like"
    try:
        observed = get_post_like(int(user_id), post_id)
    except Exception as exc:  # pragma: no cover - defensive
        return _unreadable(f"Reaction read-back failed: {exc.__class__.__name__}", expected)
    return VerificationResult(
        state=VerificationState.VERIFIED if observed == expected else VerificationState.FAILED,
        expected=expected,
        observed=observed,
        detail="" if observed == expected else "The post's like state did not match the request.",
        evidence={
            "canonical_resource_id": f"post:{post_id}",
            "read_back": {"liked": observed},
            "source": "feed_intelligence_service.get_post_like",
        },
    )


def feed_post_deleted(user_id: int, arguments: dict[str, Any], result: ToolResult) -> VerificationResult:
    """Confirm the owner's canonical post row is soft-deleted."""
    from services.pulse_feed_engine import get_owned_post_deletion_state

    post_id = int(arguments.get("post_id") or 0)
    try:
        state = get_owned_post_deletion_state(int(user_id), post_id)
    except Exception as exc:  # pragma: no cover - defensive
        return _unreadable(f"Post read-back failed: {exc.__class__.__name__}", True)
    observed = None if state is None else bool(state.get("deleted"))
    return VerificationResult(
        state=VerificationState.VERIFIED if observed is True else VerificationState.FAILED,
        expected=True,
        observed=observed,
        detail="" if observed is True else "The post was still visible after the deletion request.",
        evidence={
            "canonical_resource_id": f"post:{post_id}",
            "read_back": {"deleted": observed},
            "source": "pulse_feed_engine.get_owned_post_deletion_state",
        },
    )


def _reel_edge_value(user_id: int, arguments: dict[str, Any], result: ToolResult,
                     field: str) -> VerificationResult:
    from services.content_graph_intelligence_service import get_reel
    reel_id = int(arguments.get("reel_id") or 0)
    expected = result.capability_id in {f"reels.{field.removesuffix('d')}", f"reels.{field}"}
    if field == "saved":
        expected = result.capability_id == "reels.save"
    elif field == "liked":
        expected = result.capability_id == "reels.like"
    record = get_reel(int(user_id), reel_id)
    observed = None if not record else bool(record.get(field))
    return VerificationResult(
        state=VerificationState.VERIFIED if observed == expected else VerificationState.FAILED,
        expected=expected, observed=observed,
        detail="" if observed == expected else f"The Reel {field} state did not match the request.",
        evidence={"canonical_resource_id": f"reel:{reel_id}", "read_back": {field: observed},
                  "source": "content_graph_intelligence_service.get_reel"},
    )


def reel_saved_value(user_id, arguments, result):
    return _reel_edge_value(user_id, arguments, result, "saved")


def reel_liked_value(user_id, arguments, result):
    return _reel_edge_value(user_id, arguments, result, "liked")


def profile_preference_value(user_id, arguments, result):
    from services.content_graph_intelligence_service import get_profile_preferences
    expected = str(arguments.get("preferred_language") or "")
    record = get_profile_preferences(int(user_id))
    observed = str((record or {}).get("preferred_language") or "")
    return VerificationResult(
        state=VerificationState.VERIFIED if observed == expected else VerificationState.FAILED,
        expected=expected, observed=observed,
        detail="" if observed == expected else "The profile preference did not match the request.",
        evidence={"canonical_resource_id": f"user:{int(user_id)}",
                  "read_back": {"preferred_language": observed},
                  "source": "content_graph_intelligence_service.get_profile_preferences"},
    )


# ---------------------------------------------------------------------------
# Crypto watchlist and portfolio
# ---------------------------------------------------------------------------


def _portfolio():
    from services import portfolio_service

    return portfolio_service


def crypto_watchlist_contains(user_id: int, arguments: dict[str, Any], result: ToolResult) -> VerificationResult:
    """Confirm membership of one symbol, read from the watchlist tables directly.

    Presence and absence are the same question asked with opposite expectations, so
    add and remove share a verifier and derive the expectation from the capability
    rather than from a flag in the arguments — an argument could disagree with the
    capability that was actually run, and then a removal that silently did nothing
    would verify.
    """
    symbol = clean(arguments.get("symbol"), 16).upper()
    expected = result.capability_id == "crypto.watchlist.add"
    try:
        symbols = _portfolio().watchlist_symbols(int(user_id))
    except Exception as exc:  # pragma: no cover - defensive
        return _unreadable(f"Watchlist read-back failed: {exc.__class__.__name__}", expected)
    observed = symbol in symbols
    return VerificationResult(
        state=VerificationState.VERIFIED if observed == expected else VerificationState.FAILED,
        expected=expected,
        observed=observed,
        detail="" if observed == expected else (
            "The coin is still on the watchlist." if expected is False
            else "The coin is not on the watchlist."),
        evidence={
            "canonical_resource_id": f"watchlist:{symbol}",
            "read_back": {"symbol": symbol, "present": observed, "watchlist_size": len(symbols)},
            "subject": symbol,
            "source": "portfolio_service.watchlist_symbols",
        },
    )


def _holding_id(arguments: dict[str, Any], result: ToolResult) -> int:
    """The row this verifier should read.

    An update or a delete is addressed by ``item_id`` and the argument is the
    authority. A create has no such argument, so the id comes from the result — the
    same concession ``crypto_alert_exists`` makes, and it is narrow: the result is
    trusted to say *which row to go and look at*, never whether the row is correct.
    """
    if "item_id" in arguments:
        return int(arguments.get("item_id") or 0)
    tail = str(result.canonical_resource_id or "").rsplit(":", 1)[-1]
    return int(tail) if tail.isdigit() else 0


def crypto_holding_exists(user_id: int, arguments: dict[str, Any], result: ToolResult) -> VerificationResult:
    """Confirm a newly added holding exists on this account with the stated size."""
    item_id = _holding_id(arguments, result)
    symbol = clean(arguments.get("symbol"), 16).upper()
    expected = {"symbol": symbol, "amount": float(arguments.get("amount") or 0)}
    if not item_id:
        return VerificationResult(
            state=VerificationState.FAILED,
            expected=expected,
            observed=None,
            detail="The new holding did not come back with a row id to read.",
        )
    try:
        row = _portfolio().get_portfolio_item(int(user_id), item_id)
    except Exception as exc:  # pragma: no cover - defensive
        return _unreadable(f"Holding read-back failed: {exc.__class__.__name__}", expected)
    if not row:
        return VerificationResult(
            state=VerificationState.FAILED,
            expected=expected,
            observed=None,
            detail="The holding could not be read back on this account.",
        )
    observed = {"symbol": str(row.get("symbol") or "").upper(),
                "amount": float(row.get("amount") or 0)}
    matched = observed["symbol"] == expected["symbol"] and abs(
        observed["amount"] - expected["amount"]) < 1e-9
    return VerificationResult(
        state=VerificationState.VERIFIED if matched else VerificationState.FAILED,
        expected=expected,
        observed=observed,
        detail="" if matched else "The stored holding did not match what was requested.",
        evidence={
            "canonical_resource_id": f"portfolio_item:{item_id}",
            "read_back": observed,
            "subject": f"{observed['symbol']} holding",
            "source": "portfolio_service.get_portfolio_item",
        },
    )


def crypto_holding_values(user_id: int, arguments: dict[str, Any], result: ToolResult) -> VerificationResult:
    """Confirm each numeric field the update was asked to change now holds its value.

    Only the fields present in the request are compared. An update that supplied
    ``amount`` alone must not fail because ``average_buy_price`` is whatever it
    already was, and must not pass merely because *some* field moved.
    """
    item_id = _holding_id(arguments, result)
    requested = {key: float(arguments[key] or 0)
                 for key in ("amount", "average_buy_price") if key in arguments}
    if not requested:
        return VerificationResult(
            state=VerificationState.IMPOSSIBLE,
            detail="The update named no field whose value could be read back.",
        )
    try:
        row = _portfolio().get_portfolio_item(int(user_id), item_id)
    except Exception as exc:  # pragma: no cover - defensive
        return _unreadable(f"Holding read-back failed: {exc.__class__.__name__}", requested)
    if not row:
        return VerificationResult(
            state=VerificationState.FAILED,
            expected=requested,
            observed=None,
            detail="The holding could not be read back on this account.",
        )
    observed = {key: float(row.get(key) or 0) for key in requested}
    matched = all(abs(observed[key] - requested[key]) < 1e-9 for key in requested)
    return VerificationResult(
        state=VerificationState.VERIFIED if matched else VerificationState.FAILED,
        expected=requested,
        observed=observed,
        detail="" if matched else "The stored holding did not match what was requested.",
        evidence={
            "canonical_resource_id": f"portfolio_item:{item_id}",
            "read_back": observed,
            "subject": f"{str(row.get('symbol') or '').upper()} holding",
            "source": "portfolio_service.get_portfolio_item",
        },
    )


def crypto_holding_deleted(user_id: int, arguments: dict[str, Any], result: ToolResult) -> VerificationResult:
    """Confirm the holding is gone from this account.

    ``get_portfolio_item`` scopes by ``user_id``, so ``None`` here means "not yours
    or not there". After a delete this account owned and executed, those are the
    same state, and the read is the strongest evidence available.
    """
    item_id = _holding_id(arguments, result)
    try:
        row = _portfolio().get_portfolio_item(int(user_id), item_id)
    except Exception as exc:  # pragma: no cover - defensive
        return _unreadable(f"Holding read-back failed: {exc.__class__.__name__}", True)
    observed = row is None
    return VerificationResult(
        state=VerificationState.VERIFIED if observed else VerificationState.FAILED,
        expected=True,
        observed=observed,
        detail="" if observed else "The holding was still on the account after the deletion.",
        evidence={
            "canonical_resource_id": f"portfolio_item:{item_id}",
            "read_back": {"deleted": observed},
            "source": "portfolio_service.get_portfolio_item",
        },
    )


# ---------------------------------------------------------------------------
# Notification inbox state
# ---------------------------------------------------------------------------


def notification_read_state(user_id: int, arguments: dict[str, Any], result: ToolResult) -> VerificationResult:
    """Confirm one notification is now read on this account.

    The stored row carries both a ``read_at`` timestamp and a ``status``; the
    projection already reconciles them into ``read``, and that single derived field
    is what gets compared so the verifier cannot pass on a half-written row that set
    the status without the timestamp.
    """
    notification_id = int(arguments.get("notification_id") or 0)
    try:
        row = _notifications().get_notification(int(user_id), notification_id)
    except Exception as exc:  # pragma: no cover - defensive
        return _unreadable(f"Notification read-back failed: {exc.__class__.__name__}", True)
    if not row:
        return VerificationResult(
            state=VerificationState.FAILED,
            expected=True,
            observed=None,
            detail="The notification could not be read back on this account.",
        )
    observed = bool(row.get("read"))
    return VerificationResult(
        state=VerificationState.VERIFIED if observed else VerificationState.FAILED,
        expected=True,
        observed=observed,
        detail="" if observed else "The notification was still unread after the request.",
        evidence={
            "canonical_resource_id": f"notification:{notification_id}",
            "read_back": {"read": observed, "read_at": clean(row.get("read_at") or "", 40)},
            "source": "pulsesoc_notification_system.get_notification",
        },
    )


def notifications_unread_count(user_id: int, arguments: dict[str, Any], result: ToolResult) -> VerificationResult:
    """Confirm no unread notification remains inside the category that was cleared.

    Scoped by the same category the mutation used rather than by an account-wide
    badge count: clearing Mentions must not be reported as failed because a Payments
    notification arrived, and clearing Mentions must not be reported as verified
    because the badge happened to be zero for other reasons.
    """
    from services.undx_agent_tools import resolve_category

    category = resolve_category(clean(arguments.get("category") or "global", 24))
    scope = "all" if category == "global" else category
    try:
        payload = _notifications().list_notifications(
            int(user_id), limit=1, category=scope, unread_only=True) or {}
    except Exception as exc:  # pragma: no cover - defensive
        return _unreadable(f"Inbox read-back failed: {exc.__class__.__name__}", 0)
    observed = int(payload.get("count") or 0)
    return VerificationResult(
        state=VerificationState.VERIFIED if observed == 0 else VerificationState.FAILED,
        expected=0,
        observed=observed,
        detail="" if observed == 0 else "Unread notifications remain in that category.",
        evidence={
            "canonical_resource_id": f"user:{int(user_id)}:{category}",
            "read_back": {"category": category, "scope": scope, "unread_remaining": observed},
            "source": "pulsesoc_notification_system.list_notifications",
        },
    )


# ---------------------------------------------------------------------------
# Presence, localization and stored settings
# ---------------------------------------------------------------------------


def presence_privacy_value(user_id: int, arguments: dict[str, Any], result: ToolResult) -> VerificationResult:
    """Confirm one presence privacy switch holds the requested value."""
    from services import db as db_service
    from services import presence_service

    setting = clean(arguments.get("setting"), 32)
    expected = bool(arguments.get("enabled"))
    conn = None
    try:
        conn = db_service.connect()
        stored = presence_service.get_privacy(conn.cursor(), int(user_id), conn=conn) or {}
    except Exception as exc:  # pragma: no cover - defensive
        return _unreadable(f"Presence read-back failed: {exc.__class__.__name__}", expected)
    finally:
        if conn is not None:
            conn.close()
    if setting not in stored:
        return VerificationResult(
            state=VerificationState.IMPOSSIBLE,
            expected=expected,
            detail="That presence setting has no read-back path.",
        )
    observed = bool(stored.get(setting))
    return VerificationResult(
        state=VerificationState.VERIFIED if observed == expected else VerificationState.FAILED,
        expected=expected,
        observed=observed,
        detail="" if observed == expected else "The presence setting did not match the request.",
        evidence={
            "canonical_resource_id": f"user:{int(user_id)}:{setting}",
            "read_back": {"setting": setting, "enabled": observed},
            "source": "presence_service.get_privacy",
        },
    )


#: The argument name the capability exposes, mapped to the key ``get_preferences``
#: actually returns. ``update_preferences`` accepts ``currency`` but reads back as
#: ``preferred_currency`` — the write and read vocabularies differ by design, and a
#: verifier that keyed the payload by the argument name would miss every time and
#: report ``impossible_to_verify`` for a preference that is perfectly readable.
#: Degrading a verifiable write to "I could not check" is a quiet failure: nothing
#: breaks, the receipt just stops carrying evidence.
_REGION_READ_KEYS = {
    "locale": "preferred_locale",
    "time_zone": "preferred_timezone",
    "currency": "preferred_currency",
    "date_format": "preferred_date_format",
}


def region_preference_value(user_id: int, arguments: dict[str, Any], result: ToolResult) -> VerificationResult:
    """Confirm one region preference holds the requested value after normalisation.

    ``update_preferences`` canonicalises what it stores — a locale casing, a
    recognised timezone name — so the comparison is case-insensitive on the string.
    A value the service rewrote into a different preference entirely still fails.
    """
    from services.pulse_region_preferences import get_preferences

    setting = clean(arguments.get("setting"), 32)
    expected = clean(arguments.get("value"), 64)
    try:
        stored = get_preferences(int(user_id)) or {}
    except Exception as exc:  # pragma: no cover - defensive
        return _unreadable(f"Region read-back failed: {exc.__class__.__name__}", expected)
    read_key = _REGION_READ_KEYS.get(setting, setting)
    if read_key not in stored:
        return VerificationResult(
            state=VerificationState.IMPOSSIBLE,
            expected=expected,
            detail="That region preference has no read-back path.",
        )
    observed = clean(stored.get(read_key) or "", 64)
    matched = observed.casefold() == expected.casefold()
    return VerificationResult(
        state=VerificationState.VERIFIED if matched else VerificationState.FAILED,
        expected=expected,
        observed=observed,
        detail="" if matched else "The stored region preference did not match the request.",
        evidence={
            "canonical_resource_id": f"user:{int(user_id)}:{setting}",
            "read_back": {"setting": setting, "value": observed},
            "source": "pulse_region_preferences.get_preferences",
        },
    )


def translation_preference_value(user_id: int, arguments: dict[str, Any], result: ToolResult) -> VerificationResult:
    """Confirm the auto-translate policy stored for one target language."""
    from services.content_translation import get_preference

    target = clean(arguments.get("target_language"), 16)
    expected = clean(arguments.get("policy"), 16)
    try:
        stored = get_preference(int(user_id), "auto", target) or {}
    except Exception as exc:  # pragma: no cover - defensive
        return _unreadable(f"Translation read-back failed: {exc.__class__.__name__}", expected)
    observed = clean(stored.get("policy") or "", 16)
    matched = observed.casefold() == expected.casefold()
    return VerificationResult(
        state=VerificationState.VERIFIED if matched else VerificationState.FAILED,
        expected=expected,
        observed=observed,
        detail="" if matched else "The stored translation policy did not match the request.",
        evidence={
            "canonical_resource_id": f"user:{int(user_id)}:{target}",
            "read_back": {"target_language": clean(stored.get("target_language") or target, 16),
                          "policy": observed},
            "source": "content_translation.get_preference",
        },
    )


#: Which stored preference group each settings capability writes into. Read here
#: rather than taken from an argument so a verifier cannot be pointed at a group the
#: capability is not allowed to touch.
_SETTINGS_GROUPS = {
    "settings.privacy.audience.update": ("privacy", "audience"),
    "settings.appearance.theme.update": ("appearance", "theme"),
}


def settings_preference_value(user_id: int, arguments: dict[str, Any], result: ToolResult) -> VerificationResult:
    """Re-read the stored settings document and compare one key inside one group.

    Loaded through ``load_preferences``, which is the same function the Settings
    screen renders from, so a value that verifies here is a value the person will
    see. Reading the raw row instead would verify against a document shape the
    product does not use.
    """
    from services import db as db_service
    from services.pulse_settings_routes import load_preferences

    group_field = _SETTINGS_GROUPS.get(result.capability_id or "")
    if not group_field:
        return VerificationResult(
            state=VerificationState.IMPOSSIBLE,
            detail="No settings group is declared for this capability.",
        )
    group, value_key = group_field
    # ``theme`` names its own key; the audience capability carries the key in an
    # argument because one capability covers seven switches.
    key = clean(arguments.get("setting") or value_key, 40)
    expected = clean(arguments.get(value_key) or "", 40)
    conn = None
    try:
        conn = db_service.connect()
        stored, revision, _ = load_preferences(conn.cursor(), int(user_id))
    except Exception as exc:  # pragma: no cover - defensive
        return _unreadable(f"Settings read-back failed: {exc.__class__.__name__}", expected)
    finally:
        if conn is not None:
            conn.close()
    observed = clean((stored.get(group) or {}).get(key) or "", 40)
    matched = observed == expected
    return VerificationResult(
        state=VerificationState.VERIFIED if matched else VerificationState.FAILED,
        expected=expected,
        observed=observed,
        detail="" if matched else "The stored setting did not match the request.",
        evidence={
            "canonical_resource_id": f"user:{int(user_id)}:{group}",
            "read_back": {"group": group, "setting": key, "value": observed,
                          "revision": int(revision)},
            "source": "pulse_settings_routes.load_preferences",
        },
    )


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

VERIFIERS: dict[str, Callable[[int, dict[str, Any], ToolResult], VerificationResult]] = {
    "crypto_alert_status": crypto_alert_status,
    "crypto_alert_exists": crypto_alert_exists,
    "crypto_alert_threshold": crypto_alert_threshold,
    "crypto_alert_deleted": crypto_alert_deleted,
    "notification_preference_value": notification_preference_value,
    "saved_post_value": saved_post_value,
    "social_following_value": social_following_value,
    "feed_post_like_value": feed_post_like_value,
    "feed_post_deleted": feed_post_deleted,
    "reel_saved_value": reel_saved_value,
    "reel_liked_value": reel_liked_value,
    "profile_preference_value": profile_preference_value,
    "crypto_watchlist_contains": crypto_watchlist_contains,
    "crypto_holding_exists": crypto_holding_exists,
    "crypto_holding_values": crypto_holding_values,
    "crypto_holding_deleted": crypto_holding_deleted,
    "notification_read_state": notification_read_state,
    "notifications_unread_count": notifications_unread_count,
    "presence_privacy_value": presence_privacy_value,
    "region_preference_value": region_preference_value,
    "translation_preference_value": translation_preference_value,
    "settings_preference_value": settings_preference_value,
}


def verify(name: str, user_id: int, arguments: dict[str, Any], result: ToolResult) -> VerificationResult:
    """Run one declared verifier.

    A missing verifier yields ``impossible_to_verify`` rather than an exception or a
    silent pass. The action still happened; what is unavailable is the evidence,
    and the receipt should say exactly that.
    """
    verifier = VERIFIERS.get(clean(name, 80))
    if verifier is None:
        return VerificationResult(
            state=VerificationState.IMPOSSIBLE,
            detail="No read-back path is available for this action.",
        )
    try:
        return verifier(int(user_id), arguments or {}, result)
    except Exception as exc:  # pragma: no cover - defensive
        # Verification runs *after* the mutation. An exception escaping from here
        # would propagate out of the gateway with the user's data already changed and
        # no receipt describing it, which is the single worst failure shape this
        # system has. Each verifier guards its own service call; this catches the
        # rest — coercion, a service returning an unexpected shape, anything.
        # Unverifiable is the honest verdict, and it never reads as success.
        return _unreadable(f"Read-back raised {exc.__class__.__name__}.")


__all__ = ["VERIFIERS", "verify"]
