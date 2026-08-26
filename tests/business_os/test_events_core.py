"""Business OS — Section 9 (Events) service, exercised DIRECTLY (no pytest).

Proves the canonical events / ticketing domain end-to-end against the ONE canonical ledger:

  * DARK when BUSINESS_OS_EVENTS is off — the service raises ``disabled`` (503);
  * full lifecycle: create → add ticket types → publish → sell → check-in → settle;
  * money for PAID tickets flows through the canonical ledger (capture into escrow),
    settlement splits escrow into business-net + platform-fee using the marketplace
    take-rate, and a refund reverses the capture — no second payment system;
  * FREE tickets skip the ledger entirely (zero escrow);
  * idempotent purchase via (ticket_type, client_ref);
  * capacity + per-tier sold-out enforcement;
  * RBAC: a stranger managing an event sees 404 (existence not leaked); publishing with no
    ticket type is rejected.

    python tests/business_os/test_events_core.py
"""

import os
import tempfile
from datetime import datetime, timezone

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_events_core_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["BUSINESS_OS_BUSINESS"] = "on"
os.environ["BUSINESS_OS_EVENTS"] = "on"

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services.business_os.business import schema as biz_schema  # noqa: E402
from services.business_os.business import service as biz_svc  # noqa: E402
from services.business_os.ledger import ledger as ledger_mod  # noqa: E402
from services.business_os.events import schema as ev_schema  # noqa: E402
from services.business_os.events import service as ev  # noqa: E402
from services.business_os.marketplace.orders import DEFAULT_FEE_BPS, _fee_split  # noqa: E402


OWNER = 800
STAFF = 801
BUYER = 802
BUYER2 = 803
STRANGER = 804


def _ctx(status="active", access=1):
    return {"account_status": status, "access_enabled": access}


def setup_module(module=None):
    biz_schema.ensure_schema()
    ledger_mod.ensure_schema()
    ev_schema.ensure_schema()


def _business():
    biz = biz_svc.create_business(OWNER, {"display_name": "Acme Events"}, context=_ctx())
    bid = biz["business_id"]
    biz_svc.add_member(bid, OWNER, STAFF, "staff", context=_ctx())
    return bid


# ---------------------------------------------------------------------------
def test_dark_when_disabled_raises():
    os.environ["BUSINESS_OS_EVENTS"] = ""
    try:
        try:
            ev.create_event("b", OWNER, {"title": "X"}, context=_ctx())
            assert False, "expected disabled"
        except ev.EventError as e:
            assert e.http_status == 503 and e.code == "disabled", (e.http_status, e.code)
    finally:
        os.environ["BUSINESS_OS_EVENTS"] = "on"


def test_create_requires_title_and_manager():
    bid = _business()
    try:
        ev.create_event(bid, OWNER, {}, context=_ctx())
        assert False, "expected invalid"
    except ev.EventError as e:
        assert e.http_status == 400 and e.code == "invalid", (e.http_status, e.code)
    # Stranger cannot create — 404, existence not leaked.
    try:
        ev.create_event(bid, STRANGER, {"title": "Y"}, context=_ctx())
        assert False, "expected not_found"
    except ev.EventError as e:
        assert e.http_status == 404 and e.code == "not_found", (e.http_status, e.code)


def test_publish_requires_ticket_type():
    bid = _business()
    ev_ = ev.create_event(bid, OWNER, {"title": "Concert"}, context=_ctx())
    eid = ev_["event_id"]
    assert ev_["status"] == "draft"
    try:
        ev.publish_event(eid, OWNER, context=_ctx())
        assert False, "expected not_ready"
    except ev.EventError as e:
        assert e.http_status == 409 and e.code == "not_ready", (e.http_status, e.code)


def test_paid_lifecycle_ledger_capture_and_settlement():
    bid = _business()
    ev_ = ev.create_event(bid, OWNER, {"title": "Gala", "capacity": 100}, context=_ctx())
    eid = ev_["event_id"]
    tt = ev.add_ticket_type(eid, STAFF, {"name": "GA", "price_cents": 5000},
                            context=_ctx())
    ttid = tt["ticket_type_id"]
    pub = ev.publish_event(eid, STAFF, context=_ctx())
    assert pub["status"] == "published"

    escrow = ev.escrow_account(eid)
    assert ledger_mod.get_balance(escrow, "usd") == 0

    tkt = ev.purchase_ticket(eid, ttid, BUYER, client_ref="ref-1", context=_ctx())
    assert tkt["status"] == "confirmed" and tkt["idempotent"] is False
    assert tkt["price_cents_paid"] == 5000 and tkt["capture_txn_ref"]
    assert ledger_mod.get_balance(escrow, "usd") == 5000

    # Idempotent replay — same client_ref returns the original, no new capture.
    again = ev.purchase_ticket(eid, ttid, BUYER, client_ref="ref-1", context=_ctx())
    assert again["idempotent"] is True and again["ticket_id"] == tkt["ticket_id"]
    assert ledger_mod.get_balance(escrow, "usd") == 5000

    # Second buyer, different ref.
    tkt2 = ev.purchase_ticket(eid, ttid, BUYER2, client_ref="ref-2", context=_ctx())
    assert tkt2["idempotent"] is False
    assert ledger_mod.get_balance(escrow, "usd") == 10000

    # Check-in is idempotent.
    ci = ev.check_in_ticket(tkt["ticket_id"], STAFF, context=_ctx())
    assert ci["status"] == "checked_in"
    ci2 = ev.check_in_ticket(tkt["ticket_id"], STAFF, context=_ctx())
    assert ci2["status"] == "checked_in"

    # Settlement splits escrow into net + fee via the marketplace take-rate.
    fee, net = _fee_split(10000, DEFAULT_FEE_BPS)
    res = ev.settle_event(eid, OWNER, context=_ctx())
    assert res["gross_cents"] == 10000
    assert res["platform_fee_cents"] == fee and res["business_net_cents"] == net
    assert ledger_mod.get_balance(escrow, "usd") == 0
    assert ledger_mod.get_balance(ev.business_payable_account(bid), "usd") == net
    assert ledger_mod.get_balance(ev.PLATFORM_REVENUE_ACCOUNT, "usd") == fee

    # Settling again is idempotent — no double payout.
    res2 = ev.settle_event(eid, OWNER, context=_ctx())
    assert res2.get("already_settled") is True
    assert ledger_mod.get_balance(ev.business_payable_account(bid), "usd") == net


def test_free_ticket_skips_ledger():
    bid = _business()
    ev_ = ev.create_event(bid, OWNER, {"title": "Meetup"}, context=_ctx())
    eid = ev_["event_id"]
    tt = ev.add_ticket_type(eid, OWNER, {"name": "Free"}, context=_ctx())
    ev.publish_event(eid, OWNER, context=_ctx())
    tkt = ev.purchase_ticket(eid, tt["ticket_type_id"], BUYER, client_ref="f1",
                             context=_ctx())
    assert tkt["price_cents_paid"] == 0 and tkt["capture_txn_ref"] is None
    assert ledger_mod.get_balance(ev.escrow_account(eid), "usd") == 0


def test_refund_reverses_capture():
    bid = _business()
    ev_ = ev.create_event(bid, OWNER, {"title": "Show"}, context=_ctx())
    eid = ev_["event_id"]
    tt = ev.add_ticket_type(eid, OWNER, {"name": "GA", "price_cents": 2500},
                            context=_ctx())
    ev.publish_event(eid, OWNER, context=_ctx())
    tkt = ev.purchase_ticket(eid, tt["ticket_type_id"], BUYER, client_ref="r1",
                             context=_ctx())
    escrow = ev.escrow_account(eid)
    assert ledger_mod.get_balance(escrow, "usd") == 2500
    ref = ev.refund_ticket(tkt["ticket_id"], STAFF, context=_ctx())
    assert ref["status"] == "refunded" and ref["refund_txn_ref"]
    assert ledger_mod.get_balance(escrow, "usd") == 0
    # Quantity sold decremented back.
    tt_after = ev.get_event(eid, OWNER)["ticket_types"][0]
    assert int(tt_after["quantity_sold"]) == 0


def test_per_tier_sold_out():
    bid = _business()
    ev_ = ev.create_event(bid, OWNER, {"title": "Tiny"}, context=_ctx())
    eid = ev_["event_id"]
    tt = ev.add_ticket_type(eid, OWNER, {"name": "One", "price_cents": 100,
                                         "quantity_total": 1}, context=_ctx())
    ttid = tt["ticket_type_id"]
    ev.publish_event(eid, OWNER, context=_ctx())
    ev.purchase_ticket(eid, ttid, BUYER, client_ref="s1", context=_ctx())
    try:
        ev.purchase_ticket(eid, ttid, BUYER2, client_ref="s2", context=_ctx())
        assert False, "expected sold_out"
    except ev.EventError as e:
        assert e.http_status == 409 and e.code == "sold_out", (e.http_status, e.code)


def test_capacity_sold_out():
    bid = _business()
    ev_ = ev.create_event(bid, OWNER, {"title": "Cap", "capacity": 1}, context=_ctx())
    eid = ev_["event_id"]
    tt = ev.add_ticket_type(eid, OWNER, {"name": "GA"}, context=_ctx())
    ttid = tt["ticket_type_id"]
    ev.publish_event(eid, OWNER, context=_ctx())
    ev.purchase_ticket(eid, ttid, BUYER, client_ref="c1", context=_ctx())
    try:
        ev.purchase_ticket(eid, ttid, BUYER2, client_ref="c2", context=_ctx())
        assert False, "expected sold_out"
    except ev.EventError as e:
        assert e.http_status == 409 and e.code == "sold_out", (e.http_status, e.code)


def test_purchase_requires_published_event():
    bid = _business()
    ev_ = ev.create_event(bid, OWNER, {"title": "Draft"}, context=_ctx())
    eid = ev_["event_id"]
    tt = ev.add_ticket_type(eid, OWNER, {"name": "GA"}, context=_ctx())
    try:
        ev.purchase_ticket(eid, tt["ticket_type_id"], BUYER, client_ref="d1",
                           context=_ctx())
        assert False, "expected not_on_sale"
    except ev.EventError as e:
        assert e.http_status == 409 and e.code == "not_on_sale", (e.http_status, e.code)


def test_draft_not_publicly_readable_but_manager_sees():
    bid = _business()
    ev_ = ev.create_event(bid, OWNER, {"title": "Secret"}, context=_ctx())
    eid = ev_["event_id"]
    assert ev.get_event(eid, STRANGER) is None
    assert ev.get_event(eid, OWNER)["event_id"] == eid


def test_my_tickets_lists_holder_tickets():
    bid = _business()
    ev_ = ev.create_event(bid, OWNER, {"title": "List"}, context=_ctx())
    eid = ev_["event_id"]
    tt = ev.add_ticket_type(eid, OWNER, {"name": "GA"}, context=_ctx())
    ev.publish_event(eid, OWNER, context=_ctx())
    ev.purchase_ticket(eid, tt["ticket_type_id"], BUYER, client_ref="m1", context=_ctx())
    mine = ev.list_my_tickets(BUYER)
    assert len(mine) >= 1 and all("event_title" in t for t in mine)


def test_summary_counts_and_escrow():
    bid = _business()
    ev_ = ev.create_event(bid, OWNER, {"title": "Sum"}, context=_ctx())
    eid = ev_["event_id"]
    tt = ev.add_ticket_type(eid, OWNER, {"name": "GA", "price_cents": 1000},
                            context=_ctx())
    ev.publish_event(eid, OWNER, context=_ctx())
    ev.purchase_ticket(eid, tt["ticket_type_id"], BUYER, client_ref="sm1", context=_ctx())
    summ = ev.event_summary(eid, OWNER)
    assert summ["tickets"]["confirmed"] == 1
    assert summ["escrow_balance_cents"] == 1000
    # Stranger cannot read the summary.
    try:
        ev.event_summary(eid, STRANGER)
        assert False, "expected not_found"
    except ev.EventError as e:
        assert e.http_status == 404 and e.code == "not_found", (e.http_status, e.code)


def test_visitor_read_withholds_manager_identity_and_sales_figures():
    """A published event is readable by anyone. It is not the same object.

    Before the split there was one `_event_public` — `dict(event)` plus
    `SELECT *` on the ticket types — and `get_event` handed it to whoever
    asked. So any logged-in stranger could read who created the event and how
    many of each tier had sold.
    """
    bid = _business()
    ev_ = ev.create_event(bid, OWNER, {"title": "Open", "venue": "The Hall",
                                       "capacity": 50}, context=_ctx())
    eid = ev_["event_id"]
    tt = ev.add_ticket_type(eid, OWNER, {"name": "GA", "price_cents": 1500,
                                         "quantity_total": 10}, context=_ctx())
    ev.publish_event(eid, OWNER, context=_ctx())
    ev.purchase_ticket(eid, tt["ticket_type_id"], BUYER, client_ref="v1", context=_ctx())

    seen = ev.get_event(eid, STRANGER)
    assert seen["event_id"] == eid and seen["title"] == "Open"
    assert seen["venue"] == "The Hall"          # what a visitor came for
    for withheld in ("created_by_user_id", "business_id", "capacity"):
        assert withheld not in seen, withheld
    tier = seen["ticket_types"][0]
    assert tier["name"] == "GA" and tier["price_cents"] == 1500
    for withheld in ("quantity_sold", "quantity_total", "event_id"):
        assert withheld not in tier, withheld
    assert tier["sold_out"] is False

    # The manager still sees the stored row — the projection is about the
    # audience, not about hiding the data from its owner.
    mine = ev.get_event(eid, OWNER)
    assert mine["created_by_user_id"] and mine["business_id"] == bid
    assert int(mine["ticket_types"][0]["quantity_sold"]) == 1


def test_visitor_sold_out_is_derived_not_disclosed():
    bid = _business()
    ev_ = ev.create_event(bid, OWNER, {"title": "Two tiers"}, context=_ctx())
    eid = ev_["event_id"]
    limited = ev.add_ticket_type(eid, OWNER, {"name": "Front", "price_cents": 100,
                                              "quantity_total": 1}, context=_ctx())
    ev.add_ticket_type(eid, OWNER, {"name": "Back", "price_cents": 200}, context=_ctx())
    ev.publish_event(eid, OWNER, context=_ctx())
    ev.purchase_ticket(eid, limited["ticket_type_id"], BUYER, client_ref="d1",
                       context=_ctx())

    tiers = {t["name"]: t for t in ev.get_event(eid, STRANGER)["ticket_types"]}
    assert tiers["Front"]["sold_out"] is True
    # Unlimited supply: `quantity_total` is NULL, which is not zero. Reading it
    # as zero would mark every open tier sold out.
    assert tiers["Back"]["sold_out"] is False


def test_visitor_is_not_offered_a_withdrawn_ticket_tier():
    """`business_os_event_ticket_types.status` is written at insert and there is
    no service call that flips it yet, so this reaches past the API to set it.
    The column is real and `publish_event` already counts only active tiers; a
    visitor being offered a tier the business has withdrawn is the failure this
    pins, and it should not wait for the withdraw endpoint to be written."""
    from services import db as _db
    bid = _business()
    ev_ = ev.create_event(bid, OWNER, {"title": "Trimmed"}, context=_ctx())
    eid = ev_["event_id"]
    keep = ev.add_ticket_type(eid, OWNER, {"name": "Keep", "price_cents": 100},
                              context=_ctx())
    drop = ev.add_ticket_type(eid, OWNER, {"name": "Withdrawn", "price_cents": 200},
                              context=_ctx())
    ev.publish_event(eid, OWNER, context=_ctx())
    conn = _db.connect()
    try:
        conn.execute("UPDATE business_os_event_ticket_types SET status = 'inactive' "
                     "WHERE ticket_type_id = ?", (drop["ticket_type_id"],))
        conn.commit()
    finally:
        conn.close()

    names = [t["name"] for t in ev.get_event(eid, STRANGER)["ticket_types"]]
    assert names == ["Keep"], names
    # The manager still sees it — they are the one who withdrew it.
    manager_ids = [t["ticket_type_id"]
                   for t in ev.get_event(eid, OWNER)["ticket_types"]]
    assert drop["ticket_type_id"] in manager_ids
    assert keep["ticket_type_id"] in manager_ids


def _published(bid, title, **fields):
    e = ev.create_event(bid, OWNER, dict(title=title, **fields), context=_ctx())
    ev.add_ticket_type(e["event_id"], OWNER, {"name": "GA"}, context=_ctx())
    ev.publish_event(e["event_id"], OWNER, context=_ctx())
    return e["event_id"]


def test_public_list_offers_only_published_upcoming_events():
    bid = _business()
    soon = _published(bid, "Soon", starts_at="2030-06-01T20:00:00Z")
    later = _published(bid, "Later", starts_at="2030-09-01T20:00:00Z")
    _published(bid, "Gone", starts_at="2020-01-01T20:00:00Z")
    cancelled = _published(bid, "Called off", starts_at="2030-07-01T20:00:00Z")
    ev.cancel_event(cancelled, OWNER, context=_ctx())
    draft = ev.create_event(bid, OWNER, {"title": "Not announced",
                                         "starts_at": "2030-05-01T20:00:00Z"},
                            context=_ctx())

    listed = ev.list_public_events(bid)
    ids = [e["event_id"] for e in listed]
    assert ids == [soon, later], [e["title"] for e in listed]
    assert draft["event_id"] not in ids and cancelled not in ids
    # And it is the visitor projection, not the row.
    assert all("business_id" not in e and "created_by_user_id" not in e for e in listed)


def test_public_list_uses_the_end_and_keeps_the_undated():
    """A festival mid-run is still on; a date we cannot read is not a past date."""
    bid = _business()
    running = _published(bid, "Festival", starts_at="2030-06-01T10:00:00Z",
                         ends_at="2030-06-04T23:00:00Z")
    undated = _published(bid, "Date TBA")
    unreadable = _published(bid, "Sometime", starts_at="next spring")
    _published(bid, "Finished", starts_at="2030-05-01T10:00:00Z",
               ends_at="2030-05-02T10:00:00Z")

    midway = datetime(2030, 6, 2, 12, 0, tzinfo=timezone.utc)
    ids = [e["event_id"] for e in ev.list_public_events(bid, now=midway)]
    assert ids[0] == running, ids
    # Dateless events sort last rather than first — one should not head the
    # list — but they are still offered.
    assert set(ids[1:]) == {undated, unreadable}
    assert len(ids) == 3


def test_public_list_reads_a_zoneless_date_as_utc_and_honours_the_limit():
    """`starts_at` is free text, so plenty of it arrives without a zone.

    A zoneless date must be *assumed* UTC rather than compared as-is: mixing a
    naive and an aware datetime raises, and an exception here would empty a
    page's whole events tab because one row was typed without a `Z`.
    """
    bid = _business()
    zoneless = _published(bid, "No zone", starts_at="2030-06-03T10:00:00")
    _published(bid, "Much later", starts_at="2031-01-01T10:00:00Z")

    midway = datetime(2030, 6, 2, 12, 0, tzinfo=timezone.utc)
    ids = [e["event_id"] for e in ev.list_public_events(bid, now=midway)]
    assert ids[0] == zoneless and len(ids) == 2, ids
    assert [e["event_id"]
            for e in ev.list_public_events(bid, limit=1, now=midway)] == [zoneless]
    assert ev.list_public_events(bid, limit=0, now=midway) == []


def test_public_list_needs_no_actor_and_leaks_nothing_for_an_unknown_business():
    assert ev.list_public_events("biz_does_not_exist") == []
    assert ev.list_public_events(None) == []


def test_public_list_is_dark_when_events_are_disabled():
    bid = _business()
    prev = os.environ.get("BUSINESS_OS_EVENTS")
    os.environ["BUSINESS_OS_EVENTS"] = "off"
    try:
        ev.list_public_events(bid)
        assert False, "expected disabled"
    except ev.EventError as e:
        assert e.http_status == 503 and e.code == "disabled", (e.http_status, e.code)
    finally:
        if prev is None:
            os.environ.pop("BUSINESS_OS_EVENTS", None)
        else:
            os.environ["BUSINESS_OS_EVENTS"] = prev


def _run_standalone():
    setup_module()
    tests = [
        test_dark_when_disabled_raises,
        test_create_requires_title_and_manager,
        test_publish_requires_ticket_type,
        test_paid_lifecycle_ledger_capture_and_settlement,
        test_free_ticket_skips_ledger,
        test_refund_reverses_capture,
        test_per_tier_sold_out,
        test_capacity_sold_out,
        test_purchase_requires_published_event,
        test_draft_not_publicly_readable_but_manager_sees,
        test_my_tickets_lists_holder_tickets,
        test_summary_counts_and_escrow,
        test_visitor_read_withholds_manager_identity_and_sales_figures,
        test_visitor_sold_out_is_derived_not_disclosed,
        test_visitor_is_not_offered_a_withdrawn_ticket_tier,
        test_public_list_offers_only_published_upcoming_events,
        test_public_list_uses_the_end_and_keeps_the_undated,
        test_public_list_reads_a_zoneless_date_as_utc_and_honours_the_limit,
        test_public_list_needs_no_actor_and_leaks_nothing_for_an_unknown_business,
        test_public_list_is_dark_when_events_are_disabled,
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
