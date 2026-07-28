"""Business OS — Section 9 (Events) HTTP controller, exercised DIRECTLY.

Proves the framework-agnostic ``(status_code, body)`` controller over the events service:

  * DARK when BUSINESS_OS_EVENTS is off — every handler returns 404 not_found;
  * a manager runs the full lifecycle over HTTP: create (201) → add type (201) →
    publish (200) → purchase (201) → idempotent replay (200) → check-in (200) →
    settle (200) → summary (200);
  * a published event is publicly readable; a draft is 404 to a stranger;
  * access is enforced by the service — a stranger managing gets 404 (not leaked);
  * invalid input surfaces a curated 4xx code.

    python tests/business_os/test_events_api.py
"""

import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_events_api_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["BUSINESS_OS_BUSINESS"] = "on"
os.environ["BUSINESS_OS_EVENTS"] = "on"

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services.business_os.business import schema as biz_schema  # noqa: E402
from services.business_os.business import service as biz_svc  # noqa: E402
from services.business_os.ledger import ledger as ledger_mod  # noqa: E402
from services.business_os.events import schema as ev_schema  # noqa: E402
from services.business_os.events import api  # noqa: E402


OWNER = 850
STAFF = 851
BUYER = 852
STRANGER = 853


def _ctx(status="active", access=1):
    return {"account_status": status, "access_enabled": access}


def setup_module(module=None):
    biz_schema.ensure_schema()
    ledger_mod.ensure_schema()
    ev_schema.ensure_schema()


def _business():
    biz = biz_svc.create_business(OWNER, {"display_name": "Acme API"}, context=_ctx())
    bid = biz["business_id"]
    biz_svc.add_member(bid, OWNER, STAFF, "staff", context=_ctx())
    return bid


# ---------------------------------------------------------------------------
def test_dark_when_disabled_all_handlers_404():
    os.environ["BUSINESS_OS_EVENTS"] = ""
    try:
        calls = [
            lambda: api.create_event(OWNER, "b", {"title": "X"}),
            lambda: api.add_ticket_type(OWNER, "e", {"name": "GA"}),
            lambda: api.publish_event(OWNER, "e"),
            lambda: api.get_event(OWNER, "e"),
            lambda: api.list_business_events(OWNER, "b"),
            lambda: api.my_tickets(OWNER),
            lambda: api.purchase_ticket(OWNER, "e", "t"),
            lambda: api.check_in_ticket(OWNER, "t"),
            lambda: api.refund_ticket(OWNER, "t"),
            lambda: api.settle_event(OWNER, "e"),
            lambda: api.event_summary(OWNER, "e"),
        ]
        for fn in calls:
            status, body = fn()
            assert status == 404, (fn, status, body)
            assert body["ok"] is False and body["code"] == "not_found", body
    finally:
        os.environ["BUSINESS_OS_EVENTS"] = "on"


def test_full_lifecycle_over_http():
    bid = _business()
    ctx = _ctx()

    status, body = api.create_event(OWNER, bid, {"title": "Gala", "capacity": 50},
                                    context=ctx)
    assert status == 201 and body["ok"] is True
    eid = body["event"]["event_id"]
    assert body["event"]["status"] == "draft"

    status, body = api.add_ticket_type(STAFF, eid, {"name": "GA", "price_cents": 4000},
                                       context=ctx)
    assert status == 201
    ttid = body["ticket_type"]["ticket_type_id"]

    status, body = api.publish_event(OWNER, eid, context=ctx)
    assert status == 200 and body["event"]["status"] == "published"

    # Public read of a published event.
    status, body = api.get_event(STRANGER, eid)
    assert status == 200 and body["event"]["event_id"] == eid

    status, body = api.purchase_ticket(BUYER, eid, ttid, client_ref="a1", context=ctx)
    assert status == 201 and body["ticket"]["status"] == "confirmed"
    tid = body["ticket"]["ticket_id"]

    # Idempotent replay → 200.
    status, body = api.purchase_ticket(BUYER, eid, ttid, client_ref="a1", context=ctx)
    assert status == 200 and body["ticket"]["idempotent"] is True

    status, body = api.check_in_ticket(STAFF, tid, context=ctx)
    assert status == 200 and body["ticket"]["status"] == "checked_in"

    status, body = api.my_tickets(BUYER)
    assert status == 200 and len(body["tickets"]) >= 1

    status, body = api.settle_event(OWNER, eid, context=ctx)
    assert status == 200 and body["settlement"]["gross_cents"] == 4000

    status, body = api.event_summary(OWNER, eid)
    assert status == 200 and body["summary"]["tickets"]["checked_in"] == 1


def test_create_invalid_title_400():
    bid = _business()
    status, body = api.create_event(OWNER, bid, {}, context=_ctx())
    assert status == 400 and body["code"] == "invalid", body


def test_publish_without_type_409():
    bid = _business()
    _, body = api.create_event(OWNER, bid, {"title": "Bare"}, context=_ctx())
    eid = body["event"]["event_id"]
    status, body = api.publish_event(OWNER, eid, context=_ctx())
    assert status == 409 and body["code"] == "not_ready", body


def test_stranger_manage_404_not_leaked():
    bid = _business()
    status, body = api.create_event(STRANGER, bid, {"title": "X"}, context=_ctx())
    assert status == 404 and body["code"] == "not_found", body
    status, body = api.list_business_events(STRANGER, bid)
    assert status == 404 and body["code"] == "not_found", body


def test_draft_get_404_to_stranger():
    bid = _business()
    _, body = api.create_event(OWNER, bid, {"title": "Secret"}, context=_ctx())
    eid = body["event"]["event_id"]
    status, body = api.get_event(STRANGER, eid)
    assert status == 404 and body["code"] == "not_found", body


def test_purchase_unpublished_409():
    bid = _business()
    _, body = api.create_event(OWNER, bid, {"title": "Draft"}, context=_ctx())
    eid = body["event"]["event_id"]
    _, tbody = api.add_ticket_type(OWNER, eid, {"name": "GA"}, context=_ctx())
    ttid = tbody["ticket_type"]["ticket_type_id"]
    status, body = api.purchase_ticket(BUYER, eid, ttid, client_ref="p1", context=_ctx())
    assert status == 409 and body["code"] == "not_on_sale", body


def _run_standalone():
    setup_module()
    tests = [
        test_dark_when_disabled_all_handlers_404,
        test_full_lifecycle_over_http,
        test_create_invalid_title_400,
        test_publish_without_type_409,
        test_stranger_manage_404_not_leaked,
        test_draft_get_404_to_stranger,
        test_purchase_unpublished_409,
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
