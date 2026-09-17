"""The push `badge` must carry the same combined figure the client reconciles to.

The app icon has two writers. The client reconciler writes
`badgeFor("combined", ...)` — alerts + social messages + commerce. Every server
push also stamps the icon, and it used to stamp something else entirely, so the
icon was correct only between a foreground reconcile and the next push.

Three writers had to agree, and the live one was the least obvious:

  * `pulsesoc_notification_system._push_payload()` — the path that actually
    runs, because `create_pulse_notification` delegates to the central OS
    whenever the bridge returns ok. It sent `badge: True`, which both wire
    adapters coerce to `int(True) == 1`.
  * the `notification_service` delivery path — scoped to chat OR alerts.
  * the `notification_service` legacy alert path — alerts only.

Commerce is the term that goes missing most quietly: business threads send no
push of their own (services/business_os/messages/service.py notifies nobody), so
a commerce unread only ever reaches the icon by riding along on another push.
"""

from __future__ import annotations

import os
import sys
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="pulsesoc_icon_badge_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["PULSESOC_NOTIFICATION_DELIVERY_AUTOPROCESS_ENABLED"] = "0"

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _ROOT)

from services import db, notification_service, push_service  # noqa: E402
from services import pulsesoc_notification_system as psn  # noqa: E402


def _seed_counts(alert=0, chat=0, commerce=0):
    """Build the smallest schema `pulse_badge_counts()` reads, with known unreads."""
    conn = db.connect()
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS pulse_notifications")
    cur.execute("DROP TABLE IF EXISTS pulse_conversations")
    cur.execute("DROP TABLE IF EXISTS pulse_conversation_participants")
    cur.execute(
        """
        CREATE TABLE pulse_notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, type TEXT,
            entity_type TEXT, deep_link TEXT, target_url TEXT,
            is_read INTEGER, read_at TEXT
        )
        """
    )
    cur.execute("CREATE TABLE pulse_conversations (id INTEGER PRIMARY KEY, conversation_type TEXT)")
    cur.execute(
        """
        CREATE TABLE pulse_conversation_participants (
            id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id INTEGER,
            user_id INTEGER, unread_count INTEGER, left_at TEXT
        )
        """
    )
    for _ in range(alert):
        # 'system_announcement' is not message-like, so it lands in the alert leg.
        cur.execute(
            "INSERT INTO pulse_notifications (user_id, type, entity_type, deep_link, target_url, is_read, read_at)"
            " VALUES (9, 'system_announcement', 'system', '/pulse/notifications', '/pulse/notifications', 0, NULL)"
        )
    cur.execute("INSERT INTO pulse_conversations (id, conversation_type) VALUES (1, 'direct')")
    cur.execute("INSERT INTO pulse_conversations (id, conversation_type) VALUES (2, 'business')")
    cur.execute(
        "INSERT INTO pulse_conversation_participants (conversation_id, user_id, unread_count, left_at)"
        " VALUES (1, 9, ?, '')",
        (int(chat),),
    )
    cur.execute(
        "INSERT INTO pulse_conversation_participants (conversation_id, user_id, unread_count, left_at)"
        " VALUES (2, 9, ?, '')",
        (int(commerce),),
    )
    conn.commit()
    conn.close()


def test_pulse_badge_counts_splits_the_three_terms():
    """Guards the fixture itself: if this drifts, every assertion below is vacuous."""
    _seed_counts(alert=4, chat=3, commerce=5)
    counts = notification_service.pulse_badge_counts(9)
    assert counts["alert_unread_count"] == 4
    assert counts["chat_unread_count"] == 3
    assert counts["commerce_unread_count"] == 5


def test_total_unread_count_still_excludes_commerce():
    """The contract this fix must NOT break.

    `totalUnreadCount()` in mobile-native/src/api/notifications.ts falls back to
    alert + chat whenever this key is absent or zero. Folding commerce in here
    would make the server disagree with the client depending on which branch the
    client took — which is why the icon badge got its own helper instead.
    """
    _seed_counts(alert=4, chat=3, commerce=5)
    counts = notification_service.pulse_badge_counts(9)
    assert counts["total_unread_count"] == 7, "total_unread_count must stay alert + chat"
    assert counts["total_unread_count"] != counts["alert_unread_count"] + counts["chat_unread_count"] + counts["commerce_unread_count"]


def test_icon_badge_count_is_the_combined_figure():
    _seed_counts(alert=4, chat=3, commerce=5)
    counts = notification_service.pulse_badge_counts(9)
    assert notification_service.pulse_icon_badge_count(counts) == 12
    # The distinction that matters: commerce is the term the old code dropped.
    assert notification_service.pulse_icon_badge_count(counts) > counts["total_unread_count"]


def test_icon_badge_count_tolerates_missing_commerce_key():
    """An older/partial counts dict must not raise; commerce simply reads as 0."""
    assert notification_service.pulse_icon_badge_count({"alert_unread_count": 2, "chat_unread_count": 1}) == 3
    assert notification_service.pulse_icon_badge_count({}) == 0
    assert notification_service.pulse_icon_badge_count(None) == 0


def test_central_icon_badge_count_merges_legacy_and_central(monkeypatch):
    """The central OS counts a different table and defaults chat to 0.

    Its own badge_counts() therefore cannot answer the icon question alone; the
    merge has to match `_pulse_notification_os_badge_counts()` in bot.py, which
    is what feeds the client snapshot.
    """
    monkeypatch.setattr(
        notification_service,
        "pulse_badge_counts",
        lambda user_id: {"alert_unread_count": 4, "chat_unread_count": 3, "commerce_unread_count": 5},
    )
    monkeypatch.setattr(psn, "badge_counts", lambda user_id, chat_unread_count=0: {"alert_unread_count": 2})
    # 2 central alerts + 4 legacy alerts + 3 chat + 5 commerce
    assert psn.icon_badge_count(9) == 14


def test_central_icon_badge_count_survives_a_legacy_failure(monkeypatch):
    """A badge is not worth failing a delivery over."""

    def _boom(user_id):
        raise RuntimeError("legacy counts unavailable")

    monkeypatch.setattr(notification_service, "pulse_badge_counts", _boom)
    monkeypatch.setattr(psn, "badge_counts", lambda user_id, chat_unread_count=0: {"alert_unread_count": 2})
    assert psn.icon_badge_count(9) == 2


def _patch_count_sources(monkeypatch):
    """Stub the two count sources, not `icon_badge_count`.

    Both of these exist before and after the fix, so a test built on them fails
    against the pre-fix code for the real reason — badge was a bool built from
    an alerts-only figure — rather than because the new helper is missing.
    """
    monkeypatch.setattr(
        notification_service,
        "pulse_badge_counts",
        lambda user_id: {"alert_unread_count": 4, "chat_unread_count": 3, "commerce_unread_count": 5},
    )
    monkeypatch.setattr(
        psn,
        "badge_counts",
        lambda user_id, chat_unread_count=0: {
            "alert_unread_count": 2,
            "chat_unread_count": int(chat_unread_count or 0),
            "total_unread_count": 2 + int(chat_unread_count or 0),
        },
    )
    # 2 central alerts + 4 legacy alerts + 3 chat + 5 commerce
    return 14


def test_push_payload_sends_the_number_not_a_flag(monkeypatch):
    """`badge: True` reached the phone as 1. It must now be the real integer.

    `isinstance(..., bool)` is load-bearing here: `True == 1`, so a plain
    equality assertion against 1 would pass for both the bug and a genuine
    one-unread badge.
    """
    expected = _patch_count_sources(monkeypatch)
    payload = psn._push_payload(
        {"id": 5, "recipient_user_id": 9, "type": "system_announcement", "title": "t", "body": "b"},
        {},
    )
    assert not isinstance(payload["badge"], bool), "badge must be an int, not a flag"
    assert payload["badge"] == expected
    assert payload["badge_count"] == expected


def test_push_payload_badge_reaches_the_expo_wire_as_the_combined_count(monkeypatch):
    """End to end through the adapter that actually talks to Expo.

    push_service._send_expo_push() reads `data["badge"]` and coerces it, so this
    is the value APNs sets on the icon.
    """
    expected = _patch_count_sources(monkeypatch)
    payload = psn._push_payload(
        {"id": 5, "recipient_user_id": 9, "type": "system_announcement", "title": "t", "body": "b"},
        {},
    )
    wire = push_service._payload("t", "b", payload, "system_announcement")["data"]
    assert wire.get("badge") is not None, "the PUSH_BADGE_ENABLED gate requires a non-None badge"
    assert int(wire["badge"]) == expected

    # Positive control: the pre-fix payload shape, through the same adapter,
    # collapses to 1. Without this the assertion above could pass on a wire
    # path that ignores `badge` entirely.
    legacy_wire = push_service._payload("t", "b", {**payload, "badge": True}, "system_announcement")["data"]
    assert int(legacy_wire["badge"]) == 1
