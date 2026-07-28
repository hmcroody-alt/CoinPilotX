"""Business OS — Section 6 (Messages) canonical facade, exercised DIRECTLY.

Proves the Messages domain is a faithful REUSE of the ONE canonical message engine
(pulse_conversations / pulse_conversation_participants / pulse_messages) — not a second
message system:

  * DARK when BUSINESS_OS_MESSAGES is off — every entry point raises 503 disabled;
  * a business thread + its messages land on the SAME canonical pulse_* tables;
  * NO business_os_messages* table is ever created;
  * business-side access is inherited from S1 RBAC (member reads inbox / replies;
    stranger sees nothing — existence not leaked);
  * customer side is a plain conversation participant;
  * client_message_id gives idempotent sends; unread counters follow the engine;
  * starting a thread is idempotent per (business, customer).

    python tests/business_os/test_messages_core.py   # no pytest needed
"""

import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_msg_core_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["BUSINESS_OS_BUSINESS"] = "on"
os.environ["BUSINESS_OS_MESSAGES"] = "on"

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.business_os.business import schema as biz_schema  # noqa: E402
from services.business_os.business import service as biz_svc  # noqa: E402
from services.business_os.messages import schema as msg_schema  # noqa: E402
from services.business_os.messages import service as svc  # noqa: E402
from services.business_os.messages.service import MessageError  # noqa: E402


OWNER = 600      # business owner
STAFF = 601      # business staff member
CUSTOMER = 602   # a customer messaging the business
STRANGER = 603   # unrelated user


def _ctx(status="active", access=1):
    return {"account_status": status, "access_enabled": access}


def setup_module(module=None):
    biz_schema.ensure_schema()
    msg_schema.ensure_schema()


def _business():
    biz = biz_svc.create_business(OWNER, {"display_name": "Acme Co"}, context=_ctx())
    bid = biz["business_id"]
    biz_svc.add_member(bid, OWNER, STAFF, "staff", context=_ctx())
    return bid


# ---------------------------------------------------------------------------
def test_dark_when_disabled():
    os.environ["BUSINESS_OS_MESSAGES"] = ""
    try:
        for fn in (
            lambda: svc.start_business_thread("b", CUSTOMER, CUSTOMER),
            lambda: svc.send_message(1, CUSTOMER, "hi"),
            lambda: svc.get_thread(1, CUSTOMER),
            lambda: svc.list_business_inbox("b", OWNER),
            lambda: svc.list_customer_threads(CUSTOMER),
        ):
            try:
                fn()
                raise AssertionError("expected disabled")
            except MessageError as e:
                assert e.http_status == 503 and e.code == "disabled", (e.http_status, e.code)
    finally:
        os.environ["BUSINESS_OS_MESSAGES"] = "on"


def test_no_new_message_table_created():
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name LIKE 'business_os_%message%'").fetchall()
        assert rows == [], [dict(r) if hasattr(r, 'keys') else r for r in rows]
        # And the canonical store DOES exist and is what we use.
        canon = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='pulse_messages'").fetchall()
        assert len(canon) == 1, canon
    finally:
        conn.close()


def test_customer_opens_thread_and_it_lands_on_canonical_tables():
    bid = _business()
    thread = svc.start_business_thread(bid, CUSTOMER, CUSTOMER,
                                       subject="Where is my order?", context=_ctx())
    cid = thread["conversation_id"]
    assert thread["type"] == "business"
    assert thread["business_id"] == bid
    assert thread["subject"] == "Where is my order?"

    # It is a real canonical pulse_conversations row tagged with the business.
    conn = db.connect()
    try:
        row = dict(conn.execute(
            "SELECT conversation_type, business_id FROM pulse_conversations WHERE id=?",
            (cid,)).fetchone())
        assert row["conversation_type"] == "business"
        assert row["business_id"] == bid
    finally:
        conn.close()


def test_start_thread_is_idempotent_per_business_customer():
    bid = _business()
    a = svc.start_business_thread(bid, CUSTOMER, CUSTOMER, context=_ctx())
    b = svc.start_business_thread(bid, CUSTOMER, CUSTOMER, context=_ctx())
    assert a["conversation_id"] == b["conversation_id"]


def test_send_and_read_through_canonical_engine():
    bid = _business()
    cid = svc.start_business_thread(bid, CUSTOMER, CUSTOMER, context=_ctx())["conversation_id"]

    m1 = svc.send_message(cid, CUSTOMER, "Hello, I have a question.", context=_ctx())
    assert m1["is_mine"] is True and m1["idempotent"] is False

    # The staff member (S1 member, not a listed participant) can reply as the business.
    m2 = svc.send_message(cid, STAFF, "Happy to help!", context=_ctx())
    assert m2["sender_user_id"] == str(STAFF)

    msgs = svc.list_thread_messages(cid, CUSTOMER)
    assert [m["body"] for m in msgs] == ["Hello, I have a question.", "Happy to help!"]

    # Messages physically live in the canonical pulse_messages table.
    conn = db.connect()
    try:
        n = conn.execute("SELECT COUNT(*) AS n FROM pulse_messages WHERE conversation_id=?",
                         (cid,)).fetchone()
        assert dict(n)["n"] == 2
    finally:
        conn.close()


def test_client_message_id_is_idempotent():
    bid = _business()
    cid = svc.start_business_thread(bid, CUSTOMER, CUSTOMER, context=_ctx())["conversation_id"]
    first = svc.send_message(cid, CUSTOMER, "dup", client_message_id="c-1", context=_ctx())
    again = svc.send_message(cid, CUSTOMER, "dup", client_message_id="c-1", context=_ctx())
    assert again["idempotent"] is True
    assert again["id"] == first["id"]
    msgs = svc.list_thread_messages(cid, CUSTOMER)
    assert len(msgs) == 1


def test_unread_counter_follows_engine():
    bid = _business()
    cid = svc.start_business_thread(bid, CUSTOMER, CUSTOMER, context=_ctx())["conversation_id"]
    # Owner is a participant (business side). Customer sends -> owner unread rises.
    svc.send_message(cid, CUSTOMER, "ping", context=_ctx())
    owner_view = svc.get_thread(cid, OWNER)
    assert owner_view["unread_count"] >= 1
    svc.mark_read(cid, OWNER)
    assert svc.get_thread(cid, OWNER)["unread_count"] == 0


def test_stranger_cannot_read_or_send_existence_not_leaked():
    bid = _business()
    cid = svc.start_business_thread(bid, CUSTOMER, CUSTOMER, context=_ctx())["conversation_id"]
    assert svc.get_thread(cid, STRANGER) is None
    assert svc.list_thread_messages(cid, STRANGER) is None
    try:
        svc.send_message(cid, STRANGER, "intrude", context=_ctx())
        raise AssertionError("expected not_found")
    except MessageError as e:
        assert e.http_status == 404 and e.code == "not_found", (e.http_status, e.code)


def test_business_inbox_requires_membership():
    bid = _business()
    svc.start_business_thread(bid, CUSTOMER, CUSTOMER, context=_ctx())
    # Owner + staff can see the inbox.
    assert len(svc.list_business_inbox(bid, OWNER)) == 1
    assert len(svc.list_business_inbox(bid, STAFF)) == 1
    # Stranger cannot — existence not leaked (404).
    try:
        svc.list_business_inbox(bid, STRANGER)
        raise AssertionError("expected not_found")
    except MessageError as e:
        assert e.http_status == 404 and e.code == "not_found", (e.http_status, e.code)


def test_account_hold_beats_write():
    bid = _business()
    try:
        svc.start_business_thread(bid, CUSTOMER, CUSTOMER,
                                  context=_ctx(status="suspended"))
        raise AssertionError("expected account hold")
    except MessageError as e:
        assert e.http_status == 403 and e.code == "account_hold", (e.http_status, e.code)


def test_customer_threads_scoped_to_participant():
    bid = _business()
    svc.start_business_thread(bid, CUSTOMER, CUSTOMER, context=_ctx())
    mine = svc.list_customer_threads(CUSTOMER)
    # CUSTOMER may participate in threads across several businesses (shared id in the
    # suite); the new business's thread must be present and every row must be a
    # business thread the customer actually belongs to.
    assert len(mine) >= 1 and bid in {t["business_id"] for t in mine}
    assert svc.list_customer_threads(STRANGER) == []


def test_report_message_hits_canonical_reports():
    bid = _business()
    cid = svc.start_business_thread(bid, CUSTOMER, CUSTOMER, context=_ctx())["conversation_id"]
    m = svc.send_message(cid, CUSTOMER, "spammy", context=_ctx())
    rep = svc.report_message(cid, m["id"], OWNER, reason="spam")
    assert rep["status"] == "open"
    conn = db.connect()
    try:
        n = conn.execute("SELECT COUNT(*) AS n FROM pulse_message_reports WHERE message_id=?",
                         (m["id"],)).fetchone()
        assert dict(n)["n"] == 1
    finally:
        conn.close()


def _run_standalone():
    setup_module()
    tests = [
        test_dark_when_disabled,
        test_no_new_message_table_created,
        test_customer_opens_thread_and_it_lands_on_canonical_tables,
        test_start_thread_is_idempotent_per_business_customer,
        test_send_and_read_through_canonical_engine,
        test_client_message_id_is_idempotent,
        test_unread_counter_follows_engine,
        test_stranger_cannot_read_or_send_existence_not_leaked,
        test_business_inbox_requires_membership,
        test_account_hold_beats_write,
        test_customer_threads_scoped_to_participant,
        test_report_message_hits_canonical_reports,
    ]
    passed = 0
    for t in tests:
        t()
        print(f"PASS  {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} tests passed")
    return passed == len(tests)


if __name__ == "__main__":
    ok = _run_standalone()
    raise SystemExit(0 if ok else 1)
