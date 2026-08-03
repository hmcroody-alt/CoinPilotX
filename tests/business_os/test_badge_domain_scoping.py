"""A commerce thread must not badge the social Messages tab.

## The defect

`pulse_conversation_summaries()` (bot.py:39198) takes an `include_types` filter,
and both social list endpoints pass `{"direct"}` — `api_pulse_message_conversations`
at bot.py:81341 and `api_pulse_communications_conversations` at bot.py:80701. So
the Messages screen has never shown a business thread.

The badge that sits on top of that screen did not get the memo.
`notification_service.pulse_badge_counts` summed `unread_count` across every row
of `pulse_conversation_participants` with no domain predicate at all, and
`business_os/messages/service.py:344` bumps exactly that counter when a business
replies to a customer. The result is an unread the user cannot clear: a number
on the Messages tab pointing at a conversation the Messages tab will not render.

Not a display bug in the sense that it costs nothing — an unclearable badge
trains people to ignore badges, which is the one thing a badge cannot survive.

## The fix these tests pin

The participants sum splits on `conversation_type`. Social keeps
`chat_unread_count`; business threads move to a new `commerce_unread_count`, so
the number is separated rather than discarded and the Commerce Inbox has
something to badge.

`total_unread_count` deliberately stays `alert + chat`. `totalUnreadCount()` in
`mobile-native/src/api/notifications.ts:131-135` falls back to
`alert_unread_count + chat_unread_count` whenever the explicit total is absent
or zero, so folding commerce in would make the client disagree with itself
depending on which branch it took.

`comm_v2_participants` and the legacy `private_messages` join are untouched:
neither carries a `business_id` or a `'business'` type, and the Business OS
messages facade writes only to `pulse_conversations`.

Executable two ways:

    python -m pytest tests/business_os/test_badge_domain_scoping.py
    python tests/business_os/test_badge_domain_scoping.py
"""

import os
import sys
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_badge_scope_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["BUSINESS_OS_BUSINESS"] = "on"
os.environ["BUSINESS_OS_MESSAGES"] = "on"

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services import notification_service as ns  # noqa: E402
from services.business_os.business import schema as biz_schema  # noqa: E402
from services.business_os.business import service as biz_svc  # noqa: E402
from services.business_os.messages import schema as msg_schema  # noqa: E402
from services.business_os.messages import service as msg_svc  # noqa: E402

OWNER = 800       # business owner; also the business side of every thread
CUSTOMER = 801    # the shopper
FRIEND = 802      # a plain social contact
_seq = [0]

_NOTIFICATIONS = """
CREATE TABLE IF NOT EXISTS pulse_notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    type TEXT,
    entity_type TEXT,
    deep_link TEXT,
    target_url TEXT,
    title TEXT,
    body TEXT,
    is_read INTEGER DEFAULT 0,
    read_at TEXT,
    created_at TEXT
)
"""


def _ctx(status="active", access=1):
    return {"account_status": status, "access_enabled": access}


def setup_module(module=None):
    biz_schema.ensure_schema()
    msg_schema.ensure_schema()
    conn = db.connect()
    try:
        conn.execute(_NOTIFICATIONS)
        conn.commit()
    finally:
        conn.close()


def _reset():
    """Empty every table the badge reads, so each test counts only its own rows."""
    conn = db.connect()
    try:
        for table in ("pulse_conversation_participants", "pulse_conversations",
                      "pulse_messages", "pulse_notifications"):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
    finally:
        conn.close()


def _business():
    _seq[0] += 1
    return biz_svc.create_business(
        OWNER, {"display_name": f"Shop {_seq[0]}"}, context=_ctx())["business_id"]


def _commerce_unread(n=1, customer=CUSTOMER):
    """A business thread with `n` unread messages waiting for the customer."""
    bid = _business()
    thread = msg_svc.start_business_thread(bid, customer, customer, context=_ctx())
    cid = thread["conversation_id"]
    for i in range(n):
        # Sent by the business owner, so the customer's counter is the one bumped.
        msg_svc.send_message(cid, OWNER, f"Your order update {i}", context=_ctx())
    return cid


def _social_unread(n=1, user=CUSTOMER, other=FRIEND, conv_type="direct"):
    """A plain conversation with `n` unread messages waiting for `user`."""
    conn = db.connect()
    try:
        now = "2026-01-01T00:00:00.000000Z"
        cur = conn.execute(
            "INSERT INTO pulse_conversations "
            "(conversation_type, created_by_user_id, status, created_at, updated_at) "
            "VALUES (?, ?, 'active', ?, ?)", (conv_type, other, now, now))
        cid = int(cur.lastrowid)
        conn.execute(
            "INSERT INTO pulse_conversation_participants "
            "(conversation_id, user_id, unread_count, joined_at, created_at) "
            "VALUES (?, ?, ?, ?, ?)", (cid, user, n, now, now))
        conn.execute(
            "INSERT INTO pulse_conversation_participants "
            "(conversation_id, user_id, unread_count, joined_at, created_at) "
            "VALUES (?, ?, 0, ?, ?)", (cid, other, now, now))
        conn.commit()
        return cid
    finally:
        conn.close()


def _counts(user_id=CUSTOMER):
    return ns.pulse_badge_counts(user_id)


# --------------------------------------------------------------------------

def test_a_commerce_thread_does_not_badge_the_messages_tab():
    """The headline regression.

    The customer has one unread from a business and nothing else. The Messages
    screen filters business threads out, so a non-zero chat badge here is an
    unread the user has no way to reach.
    """
    _reset()
    _commerce_unread(1)
    c = _counts()
    assert c["chat_unread_count"] == 0, (
        f"a business thread put {c['chat_unread_count']} on the social Messages "
        "badge, which never renders business threads")


def test_the_commerce_unread_is_carried_not_discarded():
    """Separating the number is the fix; dropping it would be a different bug."""
    _reset()
    _commerce_unread(3)
    assert _counts()["commerce_unread_count"] == 3


def test_a_social_dm_still_badges_normally():
    """The whole point is a filter, not a mute."""
    _reset()
    _social_unread(4)
    c = _counts()
    assert c["chat_unread_count"] == 4
    assert c["commerce_unread_count"] == 0


def test_mixed_domains_are_split_not_summed():
    _reset()
    _social_unread(2)
    _commerce_unread(5)
    c = _counts()
    assert (c["chat_unread_count"], c["commerce_unread_count"]) == (2, 5), c


def test_groups_and_rooms_remain_social():
    """Only 'business' moves. Every other conversation type is still Messages.

    A filter written as "direct only" would have silently emptied the badge for
    group and room chats, which the social lists do fetch (bot.py:80704-80707).
    """
    _reset()
    _social_unread(2, conv_type="group")
    _social_unread(3, conv_type="room")
    c = _counts()
    assert c["chat_unread_count"] == 5, c
    assert c["commerce_unread_count"] == 0


def test_an_orphan_participant_row_stays_social():
    """A LEFT JOIN, so a participant whose conversation is gone still counts.

    This is what such a row counted as before the split. An INNER JOIN would
    have quietly deleted unreads from the badge as a side effect of a fix that
    was supposed to be about domains.
    """
    _reset()
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO pulse_conversation_participants "
            "(conversation_id, user_id, unread_count, created_at) "
            "VALUES (99999, ?, 7, '2026-01-01T00:00:00Z')", (CUSTOMER,))
        conn.commit()
    finally:
        conn.close()
    c = _counts()
    assert c["chat_unread_count"] == 7, c
    assert c["commerce_unread_count"] == 0


def test_a_null_conversation_type_stays_social():
    """COALESCE to 'direct'. The canonical table's own default is 'direct'."""
    _reset()
    cid = _social_unread(3)
    conn = db.connect()
    try:
        conn.execute("UPDATE pulse_conversations SET conversation_type = NULL "
                     "WHERE id = ?", (cid,))
        conn.commit()
    finally:
        conn.close()
    assert _counts()["chat_unread_count"] == 3


def test_a_left_thread_counts_for_neither_domain():
    """The pre-existing left_at guard survives the rewrite."""
    _reset()
    cid = _commerce_unread(2)
    conn = db.connect()
    try:
        conn.execute("UPDATE pulse_conversation_participants SET left_at = ? "
                     "WHERE conversation_id = ? AND user_id = ?",
                     ("2026-02-01T00:00:00Z", cid, str(CUSTOMER)))
        conn.commit()
    finally:
        conn.close()
    c = _counts()
    assert (c["chat_unread_count"], c["commerce_unread_count"]) == (0, 0), c


def test_the_business_side_sees_its_own_commerce_unread():
    """Scoping is per-domain, not per-role. The owner's inbox badges too."""
    _reset()
    cid = _commerce_unread(1)
    msg_svc.send_message(cid, CUSTOMER, "Where is my order?", context=_ctx())
    owner = _counts(OWNER)
    assert owner["commerce_unread_count"] == 1, owner
    assert owner["chat_unread_count"] == 0, owner


def test_total_stays_alert_plus_chat_and_excludes_commerce():
    """Pinned against the client's own fallback arithmetic.

    `totalUnreadCount()` computes alert + chat whenever the explicit total is
    absent or zero. If the server folded commerce into the total, the number the
    user saw would depend on which of those two branches ran.
    """
    _reset()
    _social_unread(2)
    _commerce_unread(6)
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO pulse_notifications (user_id, type, title, is_read, created_at) "
            "VALUES (?, 'follow', 'New follower', 0, '2026-01-01T00:00:00Z')",
            (CUSTOMER,))
        conn.commit()
    finally:
        conn.close()
    c = _counts()
    assert c["alert_unread_count"] == 1, c
    assert c["total_unread_count"] == c["alert_unread_count"] + c["chat_unread_count"]
    assert c["total_unread_count"] == 3, c


def test_the_existing_keys_keep_their_shape():
    """Adding a key is safe; changing one is not.

    mobile-native reads alert_unread_count / chat_unread_count /
    total_unread_count / count / unread_count. All five must still be present
    integers, and the two alert aliases must still mirror alert_unread_count.
    """
    _reset()
    _social_unread(2)
    _commerce_unread(4)
    c = _counts()
    for key in ("ok", "alert_unread_count", "chat_unread_count",
                "total_unread_count", "count", "unread_count",
                "commerce_unread_count"):
        assert key in c, f"missing {key}"
    assert c["ok"] is True
    for key in ("alert_unread_count", "chat_unread_count", "total_unread_count",
                "count", "unread_count", "commerce_unread_count"):
        assert isinstance(c[key], int), (key, type(c[key]))
    assert c["count"] == c["alert_unread_count"] == c["unread_count"]


def test_pulse_unread_count_is_scoped_too():
    """The alias must not be a back door to the unscoped number."""
    _reset()
    _commerce_unread(3)
    assert ns.pulse_unread_count(CUSTOMER)["chat_unread_count"] == 0


def test_mark_all_read_returns_the_scoped_counts():
    """Every wrapper that re-reads the badge inherits the scoping.

    `mark_all_pulse_read` spreads `pulse_badge_counts` into its own response, so
    a caller that trusts the mark-read reply gets the same number as a caller
    that polls.
    """
    _reset()
    _commerce_unread(2)
    _social_unread(1)
    out = ns.mark_all_pulse_read(CUSTOMER)
    assert out["chat_unread_count"] == 1, out
    assert out["commerce_unread_count"] == 2, out
    assert out["badge_counts"]["chat_unread_count"] == 1


def test_the_split_query_is_one_round_trip():
    """Both totals come from a single scan, not two.

    Cheap to assert and worth pinning: this function runs on every push, every
    mark-read and every app foreground, so a well-meant refactor into two
    queries would double the cost of the hottest read in the app.
    """
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(ns.pulse_badge_counts))
    joined = [ast.unparse(n) for n in ast.walk(tree)
              if isinstance(n, ast.Constant) and isinstance(n.value, str)
              and "LEFT JOIN pulse_conversations" in n.value]
    assert len(joined) == 1, f"expected one joined participants query, found {len(joined)}"
    assert joined[0].count("conversation_type") == 2, (
        "both branches of the split must read conversation_type from the same scan")


# --------------------------------------------------------------------------

def _main():
    setup_module()
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failures = 0
    for fn in tests:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"FAIL  {fn.__name__}\n      {type(exc).__name__}: {exc}")
        else:
            print(f"PASS  {fn.__name__}")
    print(f"\n{len(tests) - failures}/{len(tests)} tests passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(_main())
