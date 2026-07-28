"""Business OS — Section 6 (Messages) HTTP controller, exercised DIRECTLY.

Proves the framework-agnostic ``(status_code, body)`` controller over the canonical
business-messaging service:

  * DARK when BUSINESS_OS_MESSAGES is off — every handler returns 404 not_found
    (never 503; the controller leaks nothing about the feature's existence);
  * a customer opens a thread (201) and sends (201) / replays idempotently (200);
  * ownership + membership are enforced by the service — a stranger gets 404,
    existence not leaked; insufficient role ⇒ 403;
  * body always carries an ``ok`` bool and, on error, a stable machine ``code``.

    python tests/business_os/test_messages_api.py   # no pytest needed
"""

import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_msg_api_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["BUSINESS_OS_BUSINESS"] = "on"
os.environ["BUSINESS_OS_MESSAGES"] = "on"

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services.business_os.business import schema as biz_schema  # noqa: E402
from services.business_os.business import service as biz_svc  # noqa: E402
from services.business_os.messages import schema as msg_schema  # noqa: E402
from services.business_os.messages import api  # noqa: E402


OWNER = 700
STAFF = 701
CUSTOMER = 702
STRANGER = 703


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
def test_dark_when_disabled_all_handlers_404():
    os.environ["BUSINESS_OS_MESSAGES"] = ""
    try:
        calls = [
            lambda: api.start_thread(CUSTOMER, "b", {"customer_user_id": CUSTOMER}),
            lambda: api.business_inbox(OWNER, "b"),
            lambda: api.my_threads(CUSTOMER),
            lambda: api.get_thread(CUSTOMER, 1),
            lambda: api.list_messages(CUSTOMER, 1),
            lambda: api.send_message(CUSTOMER, 1, {"body": "hi"}),
            lambda: api.mark_read(CUSTOMER, 1),
            lambda: api.report_message(OWNER, 1, {"message_id": 1, "reason": "spam"}),
        ]
        for fn in calls:
            status, body = fn()
            assert status == 404, (fn, status, body)
            assert body["ok"] is False and body["code"] == "not_found", body
    finally:
        os.environ["BUSINESS_OS_MESSAGES"] = "on"


def test_start_thread_201_then_get_200():
    bid = _business()
    status, body = api.start_thread(CUSTOMER, bid,
                                    {"customer_user_id": CUSTOMER,
                                     "subject": "Where is my order?"}, context=_ctx())
    assert status == 201 and body["ok"] is True
    cid = body["thread"]["conversation_id"]
    assert body["thread"]["type"] == "business"
    assert body["thread"]["subject"] == "Where is my order?"

    status, body = api.get_thread(CUSTOMER, cid)
    assert status == 200 and body["thread"]["conversation_id"] == cid


def test_send_201_then_idempotent_replay_200():
    bid = _business()
    cid = api.start_thread(CUSTOMER, bid, {"customer_user_id": CUSTOMER},
                           context=_ctx())[1]["thread"]["conversation_id"]

    status, body = api.send_message(CUSTOMER, cid,
                                    {"body": "Hello", "client_message_id": "c-9"},
                                    context=_ctx())
    assert status == 201 and body["message"]["idempotent"] is False
    first_id = body["message"]["id"]

    status, body = api.send_message(CUSTOMER, cid,
                                    {"body": "Hello", "client_message_id": "c-9"},
                                    context=_ctx())
    assert status == 200 and body["message"]["idempotent"] is True
    assert body["message"]["id"] == first_id


def test_staff_replies_and_messages_list_in_order():
    bid = _business()
    cid = api.start_thread(CUSTOMER, bid, {"customer_user_id": CUSTOMER},
                           context=_ctx())[1]["thread"]["conversation_id"]
    api.send_message(CUSTOMER, cid, {"body": "Question?"}, context=_ctx())
    status, body = api.send_message(STAFF, cid, {"body": "Answer!"}, context=_ctx())
    assert status == 201

    status, body = api.list_messages(CUSTOMER, cid)
    assert status == 200
    assert [m["body"] for m in body["messages"]] == ["Question?", "Answer!"]


def test_stranger_get_and_list_404_not_leaked():
    bid = _business()
    cid = api.start_thread(CUSTOMER, bid, {"customer_user_id": CUSTOMER},
                           context=_ctx())[1]["thread"]["conversation_id"]
    status, body = api.get_thread(STRANGER, cid)
    assert status == 404 and body["code"] == "not_found", body
    status, body = api.list_messages(STRANGER, cid)
    assert status == 404 and body["code"] == "not_found", body
    status, body = api.send_message(STRANGER, cid, {"body": "intrude"}, context=_ctx())
    assert status == 404 and body["code"] == "not_found", body


def test_business_inbox_and_my_threads():
    bid = _business()
    api.start_thread(CUSTOMER, bid, {"customer_user_id": CUSTOMER}, context=_ctx())
    # Owner + staff can read the inbox.
    status, body = api.business_inbox(OWNER, bid)
    assert status == 200 and len(body["threads"]) == 1
    status, body = api.business_inbox(STAFF, bid)
    assert status == 200 and len(body["threads"]) == 1
    # Stranger cannot — existence not leaked (404).
    status, body = api.business_inbox(STRANGER, bid)
    assert status == 404 and body["code"] == "not_found", body
    # Customer sees their own thread; stranger sees none.
    status, body = api.my_threads(CUSTOMER)
    assert status == 200 and len(body["threads"]) >= 1
    status, body = api.my_threads(STRANGER)
    assert status == 200 and body["threads"] == []


def test_mark_read_zeroes_unread():
    bid = _business()
    cid = api.start_thread(CUSTOMER, bid, {"customer_user_id": CUSTOMER},
                           context=_ctx())[1]["thread"]["conversation_id"]
    api.send_message(CUSTOMER, cid, {"body": "ping"}, context=_ctx())
    assert api.get_thread(OWNER, cid)[1]["thread"]["unread_count"] >= 1
    status, body = api.mark_read(OWNER, cid)
    assert status == 200
    assert api.get_thread(OWNER, cid)[1]["thread"]["unread_count"] == 0


def test_report_requires_message_id_then_201():
    bid = _business()
    cid = api.start_thread(CUSTOMER, bid, {"customer_user_id": CUSTOMER},
                           context=_ctx())[1]["thread"]["conversation_id"]
    mid = api.send_message(CUSTOMER, cid, {"body": "spammy"},
                           context=_ctx())[1]["message"]["id"]
    # Missing message_id -> 400 invalid.
    status, body = api.report_message(OWNER, cid, {"reason": "spam"})
    assert status == 400 and body["code"] == "invalid", body
    # Full report -> 201.
    status, body = api.report_message(OWNER, cid,
                                      {"message_id": mid, "reason": "spam"})
    assert status == 201 and body["report"]["status"] == "open"


def test_account_hold_blocks_start():
    bid = _business()
    status, body = api.start_thread(CUSTOMER, bid, {"customer_user_id": CUSTOMER},
                                    context=_ctx(status="suspended"))
    assert status == 403 and body["code"] == "account_hold", body


def test_send_missing_body_is_rejected():
    bid = _business()
    cid = api.start_thread(CUSTOMER, bid, {"customer_user_id": CUSTOMER},
                           context=_ctx())[1]["thread"]["conversation_id"]
    status, body = api.send_message(CUSTOMER, cid, {"body": "   "}, context=_ctx())
    assert status == 400 and body["ok"] is False, body


def _run_standalone():
    setup_module()
    tests = [
        test_dark_when_disabled_all_handlers_404,
        test_start_thread_201_then_get_200,
        test_send_201_then_idempotent_replay_200,
        test_staff_replies_and_messages_list_in_order,
        test_stranger_get_and_list_404_not_leaked,
        test_business_inbox_and_my_threads,
        test_mark_read_zeroes_unread,
        test_report_requires_message_id_then_201,
        test_account_hold_blocks_start,
        test_send_missing_body_is_rejected,
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
