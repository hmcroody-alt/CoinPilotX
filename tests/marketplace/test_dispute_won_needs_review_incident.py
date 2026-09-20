"""A won dispute that cannot be released must become an incident, not a log line.

When Stripe closes a chargeback in PulseSoc's favour the handler releases the
settlement back to the payout state the hold interrupted. That is only possible
while the settlement is still short of payout. Two reachable shapes are not:

* The settlement was already ``paid`` when the chargeback opened.
  ``ALLOWED_TRANSITIONS["paid"] == {"reversed"}``, so ``place_hold`` is refused
  and no hold event is ever written — the won dispute then finds no origin state
  at all.
* The settlement was ``scheduled``. The hold lands (``disputed`` plus a
  ``blocker_code``), but ``scheduled`` is not a legal release target, so the row
  stays frozen mid-payout with the blocker still on it.

In both, PulseSoc has money back from Stripe and a settlement in a position
nothing tracks. The handler used to emit ``MARKETPLACE_DISPUTE_WON_NEEDS_REVIEW``
and ``continue``. A log line is not an operational state: nobody is paged by it,
nothing lists it, and it ages out of retention.

These tests assert on rows in ``financial_incidents`` — the canonical
append-only incident table the admin ``/api/pulse/finance/incidents`` surface
reads — and on the settlement rows, never on source text. They also assert that
no money moved: the authorisation for this fix is explicitly "do not
automatically move money unless reconciliation proves the correct action".

tests/marketplace/ files cannot share a pytest process; run this file alone.

    .venv/bin/python -m pytest tests/marketplace/test_dispute_won_needs_review_incident.py
"""

import hashlib
import hmac
import json
import os
import sys
import tempfile
import time

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(
    tempfile.mkdtemp(prefix="dispute_review_incident_"), "test.db")
os.environ.setdefault("STRIPE_WEBHOOK_SECRET", "whsec_dispute_review_tests_only")
os.environ.setdefault("STRIPE_SECRET_KEY", "sk_test_dispute_review_tests_only")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services import marketplace_settlement_service as settlement  # noqa: E402
from services.business_os.ledger import ledger  # noqa: E402
from services.business_os.payments import incidents  # noqa: E402


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------

def _tx(tx_id, *, seller=9000, fee=1000, total=10000):
    quote = {"quote_id": f"q{tx_id}", "fee_policy_version": "MARKETPLACE_LEGACY_CURRENT",
             "payout_policy_version": "MARKETPLACE_PAYOUTS_V1", "platform_fee_bps": 1000,
             "merchandise_net_minor": 10000, "shipping_minor": 0, "tax_minor": 0,
             "seller_shipping_credit_minor": 0, "buyer_total_minor": total}
    return {"id": tx_id, "seller_user_id": 22, "item_type": "marketplace_product",
            "amount_cents": total, "platform_fee_cents": fee, "seller_net_cents": seller,
            "currency": "USD", "metadata_json": json.dumps({"commercial_quote": quote})}


def _dispute(dispute_id, payment_intent, *, amount=10000, status="needs_response"):
    return {"id": dispute_id, "object": "dispute", "payment_intent": payment_intent,
            "charge": f"ch_{dispute_id}", "amount": amount, "status": status, "metadata": {}}


def _schema():
    ledger.ensure_schema()
    settlement.ensure_schema()
    incidents.ensure_schema()


def _advance(tx_id, states):
    for state in states:
        settlement.transition_payout(
            tx_id, state, actor="test", reason=f"advance to {state}",
            idempotency_key=f"advance:{tx_id}:{state}",
            provider_reference=f"po_{tx_id}" if state == "scheduled" else "")


def _incident_rows(tx_id):
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT * FROM financial_incidents WHERE related_object = ?",
            (f"marketplace_settlement:{tx_id}",)).fetchall()
    finally:
        conn.close()
    out = []
    for row in rows:
        item = dict(row)
        item["details"] = json.loads(item.get("details_json") or "{}")
        out.append(item)
    return out


def _ledger_snapshot():
    conn = db.connect()
    try:
        return sorted(tuple(dict(r).values()) for r in conn.execute(
            "SELECT account, currency, balance_cents FROM ledger_balances").fetchall())
    finally:
        conn.close()


def _seed_order_row(tx_id):
    """The buyer-facing order row the dispute handler updates after the loop."""
    import bot
    bot.init_db()
    conn = db.connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO seller_transactions "
            "(id, buyer_user_id, seller_user_id, item_type, amount_cents, "
            " currency, status, stripe_payment_intent_id) "
            "VALUES (?, 7, 22, 'marketplace_product', 10000, 'USD', 'paid', ?)",
            (tx_id, f"pi_{tx_id}"))
        conn.commit()
    finally:
        conn.close()


def _order_status(tx_id):
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT status FROM seller_transactions WHERE id = ?", (tx_id,)).fetchone()
    finally:
        conn.close()
    return str(dict(row).get("status")) if row else None


def _post_webhook(event_type, obj, *, event_id):
    import bot
    bot.init_db()
    event = {"id": event_id, "object": "event", "type": event_type, "livemode": False,
             "data": {"object": obj}}
    payload = json.dumps(event, separators=(",", ":")).encode("utf-8")
    timestamp = str(int(time.time()))
    digest = hmac.new(os.environ["STRIPE_WEBHOOK_SECRET"].encode("utf-8"),
                      f"{timestamp}.".encode("utf-8") + payload, hashlib.sha256).hexdigest()
    return bot.webhook_app.test_client().post(
        "/api/stripe/webhook", data=payload,
        headers={"Stripe-Signature": f"t={timestamp},v1={digest}",
                 "Content-Type": "application/json"})


# --------------------------------------------------------------------------
# the settlement really is unreleasable — the premise
# --------------------------------------------------------------------------

def test_a_paid_settlement_cannot_be_held_so_a_won_dispute_finds_no_origin():
    """The premise of shape one. If this fails the state machine changed."""
    import bot
    _schema()
    settlement.settle_paid_transaction(_tx(101), payout_ready=True, provider_payment_id="pi_101")
    _advance(101, ["protection_hold", "eligible", "scheduled", "paid"])

    bot.pulse_apply_marketplace_dispute(_dispute("dp_101", "pi_101"),
                                        "charge.dispute.created", "evt_101")

    assert settlement.get_settlement(101)["payout_state"] == "paid"
    assert settlement.hold_origin_state(101) == ""


# --------------------------------------------------------------------------
# shape one: already paid
# --------------------------------------------------------------------------

def test_a_won_dispute_on_an_already_paid_settlement_opens_an_incident():
    import bot
    _schema()
    settlement.settle_paid_transaction(_tx(102), payout_ready=True, provider_payment_id="pi_102")
    _advance(102, ["protection_hold", "eligible", "scheduled", "paid"])
    bot.pulse_apply_marketplace_dispute(_dispute("dp_102", "pi_102"),
                                        "charge.dispute.created", "evt_102")
    assert _incident_rows(102) == [], "nothing may be opened before the outcome is known"

    bot.pulse_apply_marketplace_dispute(_dispute("dp_102", "pi_102", status="won"),
                                        "charge.dispute.closed", "evt_102b")

    rows = _incident_rows(102)
    assert len(rows) == 1, "the won dispute left no operator-visible record"
    row = rows[0]
    assert row["incident_type"] == incidents.PAYOUT_STATE_CONFLICT
    assert row["domain"] == "seller_payments"
    assert row["severity"] == "critical"
    assert row["status"] == "open"
    assert row["stripe_ref"] == "dp_102"


def test_the_incident_carries_enough_to_act_on_without_a_log_dive():
    import bot
    _schema()
    settlement.settle_paid_transaction(_tx(103), payout_ready=True, provider_payment_id="pi_103")
    _advance(103, ["protection_hold", "eligible", "scheduled", "paid"])
    bot.pulse_apply_marketplace_dispute(_dispute("dp_103", "pi_103"),
                                        "charge.dispute.created", "evt_103")
    bot.pulse_apply_marketplace_dispute(_dispute("dp_103", "pi_103", status="won"),
                                        "charge.dispute.closed", "evt_103b")

    details = _incident_rows(103)[0]["details"]
    assert details["seller_transaction_id"] == 103
    assert details["dispute_id"] == "dp_103"
    assert details["stripe_event_id"] == "evt_103b"
    assert details["dispute_status"] == "won"
    assert details["payout_state"] == "paid"
    assert details["hold_origin_state"] == ""
    assert details["seller_id"] == "22"
    assert details["order_id"] == "marketplace_order:103"
    assert details["net_seller_earnings_minor"] == 9000
    # The operator has to be able to tell "we did nothing" from "we half did
    # something", and has to be told what the safe next move is.
    assert details["automatic_action_taken"] == "none"
    assert details["remediation"] == bot.MARKETPLACE_DISPUTE_WON_REMEDIATION
    assert "not transition this settlement automatically" in details["remediation"].lower()


def test_the_incident_is_reachable_through_the_operator_listing():
    """Opened into the table the admin finance surface actually reads."""
    import bot
    _schema()
    settlement.settle_paid_transaction(_tx(104), payout_ready=True, provider_payment_id="pi_104")
    _advance(104, ["protection_hold", "eligible", "scheduled", "paid"])
    bot.pulse_apply_marketplace_dispute(_dispute("dp_104", "pi_104"),
                                        "charge.dispute.created", "evt_104")
    bot.pulse_apply_marketplace_dispute(_dispute("dp_104", "pi_104", status="won"),
                                        "charge.dispute.closed", "evt_104b")

    listed = incidents.list_incidents(domain="seller_payments", status="open",
                                      incident_type=incidents.PAYOUT_STATE_CONFLICT)
    assert f"marketplace_settlement:104" in {
        str(i.get("related_object")) for i in listed["incidents"]}
    assert incidents.counts_by_status()["open"] >= 1


def test_the_incident_is_workflow_state_a_human_can_close_with_a_note():
    """Auditable, not just visible: it has a status an operator moves."""
    import bot
    _schema()
    settlement.settle_paid_transaction(_tx(105), payout_ready=True, provider_payment_id="pi_105")
    _advance(105, ["protection_hold", "eligible", "scheduled", "paid"])
    bot.pulse_apply_marketplace_dispute(_dispute("dp_105", "pi_105"),
                                        "charge.dispute.created", "evt_105")
    bot.pulse_apply_marketplace_dispute(_dispute("dp_105", "pi_105", status="won"),
                                        "charge.dispute.closed", "evt_105b")

    opened = _incident_rows(105)[0]
    try:
        incidents.update_incident_status(opened["id"], "resolved")
        assert False, "a resolution with no explanation was accepted"
    except incidents.IncidentError:
        pass
    resolved = incidents.update_incident_status(
        opened["id"], "resolved",
        resolution_note="Stripe returned the funds; seller keeps the payout.",
        actor="admin:1")
    assert resolved["status"] == "resolved"
    assert resolved["resolved_at"]
    assert "[by admin:1]" in resolved["resolution_note"]


# --------------------------------------------------------------------------
# shape two: frozen mid-payout
# --------------------------------------------------------------------------

def test_a_won_dispute_on_a_scheduled_settlement_opens_an_incident_too():
    """The hold lands here, but `scheduled` is not a legal release target."""
    import bot
    _schema()
    settlement.settle_paid_transaction(_tx(106), payout_ready=True, provider_payment_id="pi_106")
    _advance(106, ["protection_hold", "eligible", "scheduled"])
    bot.pulse_apply_marketplace_dispute(_dispute("dp_106", "pi_106"),
                                        "charge.dispute.created", "evt_106")
    held = settlement.get_settlement(106)
    assert held["payout_state"] == "disputed"
    assert held["blocker_code"] == "dispute"

    bot.pulse_apply_marketplace_dispute(_dispute("dp_106", "pi_106", status="won"),
                                        "charge.dispute.closed", "evt_106b")

    rows = _incident_rows(106)
    assert len(rows) == 1
    assert rows[0]["details"]["hold_origin_state"] == "scheduled"
    assert rows[0]["details"]["payout_state"] == "disputed"
    assert rows[0]["details"]["blocker_code"] == "dispute"


# --------------------------------------------------------------------------
# nothing moves on its own
# --------------------------------------------------------------------------

def test_opening_the_incident_moves_no_money_and_releases_no_hold():
    import bot
    _schema()
    settlement.settle_paid_transaction(_tx(107), payout_ready=True, provider_payment_id="pi_107")
    _advance(107, ["protection_hold", "eligible", "scheduled"])
    bot.pulse_apply_marketplace_dispute(_dispute("dp_107", "pi_107"),
                                        "charge.dispute.created", "evt_107")
    before_settlement = settlement.get_settlement(107)
    before_ledger = _ledger_snapshot()

    bot.pulse_apply_marketplace_dispute(_dispute("dp_107", "pi_107", status="won"),
                                        "charge.dispute.closed", "evt_107b")

    after = settlement.get_settlement(107)
    assert after["payout_state"] == before_settlement["payout_state"] == "disputed"
    assert after["blocker_code"] == "dispute", "the hold must stay until a human decides"
    assert after["seller_reversed_minor"] == before_settlement["seller_reversed_minor"]
    assert after["net_seller_earnings_minor"] == before_settlement["net_seller_earnings_minor"]
    assert _ledger_snapshot() == before_ledger, "an incident must not post a ledger entry"
    # Still unpayable, which is the point: the blocker both selectors filter on
    # is intact.
    assert not settlement.evaluate_eligibility(107)["eligible"]


# --------------------------------------------------------------------------
# it must not fire when the release path works
# --------------------------------------------------------------------------

def test_a_releasable_won_dispute_still_releases_and_opens_nothing():
    """Positive control. An incident on every won dispute would be noise."""
    import bot
    _schema()
    settlement.settle_paid_transaction(_tx(108), payout_ready=True, provider_payment_id="pi_108")
    settlement.mark_delivered(108, actor="carrier", idempotency_key="delivery:108")
    bot.pulse_apply_marketplace_dispute(_dispute("dp_108", "pi_108"),
                                        "charge.dispute.created", "evt_108")

    bot.pulse_apply_marketplace_dispute(_dispute("dp_108", "pi_108", status="won"),
                                        "charge.dispute.closed", "evt_108b")

    released = settlement.get_settlement(108)
    assert released["payout_state"] == "protection_hold"
    assert not released["blocker_code"]
    assert _incident_rows(108) == []


# --------------------------------------------------------------------------
# Stripe redelivers
# --------------------------------------------------------------------------

def test_a_redelivered_dispute_closure_refreshes_one_incident_not_two():
    import bot
    _schema()
    settlement.settle_paid_transaction(_tx(109), payout_ready=True, provider_payment_id="pi_109")
    _advance(109, ["protection_hold", "eligible", "scheduled", "paid"])
    bot.pulse_apply_marketplace_dispute(_dispute("dp_109", "pi_109"),
                                        "charge.dispute.created", "evt_109")

    for event_id in ("evt_109b", "evt_109b", "evt_109c"):
        bot.pulse_apply_marketplace_dispute(_dispute("dp_109", "pi_109", status="won"),
                                            "charge.dispute.closed", event_id)

    rows = _incident_rows(109)
    assert len(rows) == 1, f"Stripe redeliveries duplicated the incident: {len(rows)}"
    assert rows[0]["status"] == "open"


def test_a_second_chargeback_on_the_same_order_is_a_second_incident():
    """Keyed on the dispute, not the row: a new fact deserves a new incident."""
    import bot
    _schema()
    settlement.settle_paid_transaction(_tx(110), payout_ready=True, provider_payment_id="pi_110")
    _advance(110, ["protection_hold", "eligible", "scheduled", "paid"])
    for dispute_id in ("dp_110a", "dp_110b"):
        bot.pulse_apply_marketplace_dispute(_dispute(dispute_id, "pi_110"),
                                            "charge.dispute.created", f"evt_{dispute_id}")
        bot.pulse_apply_marketplace_dispute(_dispute(dispute_id, "pi_110", status="won"),
                                            "charge.dispute.closed", f"evt_{dispute_id}_b")

    assert {r["stripe_ref"] for r in _incident_rows(110)} == {"dp_110a", "dp_110b"}


# --------------------------------------------------------------------------
# Stripe must still get a 200
# --------------------------------------------------------------------------

def test_the_webhook_returns_200_even_if_the_incident_write_fails(monkeypatch):
    """Raising out of here would make Stripe retry this event forever."""
    import bot
    _schema()
    settlement.settle_paid_transaction(_tx(111), payout_ready=True, provider_payment_id="pi_111")
    _advance(111, ["protection_hold", "eligible", "scheduled", "paid"])
    _seed_order_row(111)
    assert _post_webhook("charge.dispute.created", _dispute("dp_111", "pi_111"),
                         event_id="evt_111").status_code == 200
    assert _order_status(111) == "dispute_opened"

    def explode(*args, **kwargs):
        raise RuntimeError("incident table is on fire")

    monkeypatch.setattr(incidents, "open_incident", explode)
    response = _post_webhook("charge.dispute.closed",
                             _dispute("dp_111", "pi_111", status="won"),
                             event_id="evt_111b")

    assert response.status_code == 200
    assert _incident_rows(111) == []
    # The rest of the handler still ran — the failure was contained to the
    # incident write, not allowed to abort the loop before the order row.
    #
    # And the order row still tells the truth. This is the case where it counts
    # most: the incident did not open, so the row is the only surviving trace
    # that this settlement needs a human. "dispute_resolved" here would erase
    # the last signal.
    assert _order_status(111) == "dispute_won_review"


def test_the_webhook_delivers_a_won_dispute_all_the_way_to_the_incident():
    """The wiring, not the handler: end to end from a signed Stripe event."""
    _schema()
    settlement.settle_paid_transaction(_tx(112), payout_ready=True, provider_payment_id="pi_112")
    _advance(112, ["protection_hold", "eligible", "scheduled", "paid"])
    assert _post_webhook("charge.dispute.created", _dispute("dp_112", "pi_112"),
                         event_id="evt_112").status_code == 200

    response = _post_webhook("charge.dispute.closed",
                             _dispute("dp_112", "pi_112", status="won"),
                             event_id="evt_112b")

    assert response.status_code == 200
    rows = _incident_rows(112)
    assert len(rows) == 1
    assert rows[0]["severity"] == "critical"


# --------------------------------------------------------------------------
# the order row must not contradict the incident
#
# `row_status` used to be computed from the Stripe event alone, so every won
# dispute wrote "dispute_resolved" regardless of what the handler had actually
# managed to do with the settlement. A stranded settlement therefore rendered
# as "Dispute resolved" to the seller and in the admin panel -- the one reading
# that stops anybody from going to look at it.
# --------------------------------------------------------------------------

def test_a_stranded_won_dispute_does_not_render_as_resolved():
    import bot
    _schema()
    settlement.settle_paid_transaction(_tx(120), payout_ready=True, provider_payment_id="pi_120")
    _advance(120, ["protection_hold", "eligible", "scheduled", "paid"])
    _seed_order_row(120)

    bot.pulse_apply_marketplace_dispute(_dispute("dp_120", "pi_120", status="won"),
                                        "charge.dispute.closed", "evt_120")

    assert _order_status(120) != "dispute_resolved"
    assert _order_status(120) == "dispute_won_review"


def test_a_releasable_won_dispute_still_reads_as_resolved():
    """Positive control. The new status must not swallow the normal case."""
    import bot
    _schema()
    settlement.settle_paid_transaction(_tx(121), payout_ready=True, provider_payment_id="pi_121")
    settlement.mark_delivered(121, actor="carrier", idempotency_key="delivery:121")
    _seed_order_row(121)
    bot.pulse_apply_marketplace_dispute(_dispute("dp_121", "pi_121"),
                                        "charge.dispute.created", "evt_121")

    bot.pulse_apply_marketplace_dispute(_dispute("dp_121", "pi_121", status="won"),
                                        "charge.dispute.closed", "evt_121b")

    assert _incident_rows(121) == []
    assert _order_status(121) == "dispute_resolved"


def test_a_lost_dispute_is_still_marked_lost():
    """The other branch of the same dict — not collateral damage."""
    import bot
    _schema()
    settlement.settle_paid_transaction(_tx(122), payout_ready=True, provider_payment_id="pi_122")
    settlement.mark_delivered(122, actor="carrier", idempotency_key="delivery:122")
    _seed_order_row(122)
    bot.pulse_apply_marketplace_dispute(_dispute("dp_122", "pi_122"),
                                        "charge.dispute.created", "evt_122")

    bot.pulse_apply_marketplace_dispute(_dispute("dp_122", "pi_122", status="lost"),
                                        "charge.dispute.closed", "evt_122b")

    assert _order_status(122) == "dispute_lost"


def test_a_dispute_closure_splits_a_mixed_batch_by_outcome():
    """Two orders on one charge, only one of them stranded.

    The write used to be a single UPDATE over every id in the batch, so one
    status had to cover all of them. A settlement that released cleanly and one
    that could not are different facts and must not share a row status.
    """
    import bot
    _schema()
    settlement.settle_paid_transaction(_tx(123), payout_ready=True, provider_payment_id="pi_shared")
    settlement.settle_paid_transaction(_tx(124), payout_ready=True, provider_payment_id="pi_shared")
    # 123 stays releasable; 124 is driven all the way to paid so it strands.
    settlement.mark_delivered(123, actor="carrier", idempotency_key="delivery:123")
    _advance(124, ["protection_hold", "eligible", "scheduled", "paid"])
    _seed_order_row(123)
    _seed_order_row(124)
    bot.pulse_apply_marketplace_dispute(_dispute("dp_shared", "pi_shared"),
                                        "charge.dispute.created", "evt_shared")

    bot.pulse_apply_marketplace_dispute(_dispute("dp_shared", "pi_shared", status="won"),
                                        "charge.dispute.closed", "evt_shared_b")

    assert _order_status(123) == "dispute_resolved"
    assert _order_status(124) == "dispute_won_review"


def test_a_warning_closed_dispute_strands_the_same_way():
    """`warning_closed` takes the same release branch, so it strands alike."""
    import bot
    _schema()
    settlement.settle_paid_transaction(_tx(125), payout_ready=True, provider_payment_id="pi_125")
    _advance(125, ["protection_hold", "eligible", "scheduled", "paid"])
    _seed_order_row(125)

    bot.pulse_apply_marketplace_dispute(
        _dispute("dp_125", "pi_125", status="warning_closed"),
        "charge.dispute.closed", "evt_125")

    assert _order_status(125) == "dispute_won_review"
