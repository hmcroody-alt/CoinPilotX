"""The joint between an order arriving and a seller being payable.

Every piece of this chain already worked in isolation and none was joined to the
next, which is the whole reason no seller could ever be paid: fulfillment could
mark an order delivered, `mark_delivered` could open a protection hold,
`evaluate_eligibility` could clear one, and the payout scheduler could pay an
`eligible` row — but nothing unattended called the middle two.

These tests are about the joint, not the pieces. The pieces have their own
suites; what is checked here is that the cycle exists, that it runs in the order
the money travels, that one broken sweep does not stop the other, and — most
importantly — that hosting it did not turn anything on.
"""
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(
    tempfile.mkdtemp(prefix="marketplace_release_"), "test.db")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services.business_os.ledger import ledger  # noqa: E402
from services.business_os.marketplace import policy  # noqa: E402
from services import marketplace_order_fulfillment as fulfillment  # noqa: E402
from services import marketplace_release_cycle as cycle  # noqa: E402
from services import marketplace_settlement_service as settlement  # noqa: E402

ALL_FLAGS = (cycle.FULFILLMENT_SWEEP_ENABLED_ENV_VAR,
             cycle.SETTLEMENT_SWEEP_ENABLED_ENV_VAR,
             cycle.INTERVAL_ENV_VAR,
             fulfillment.AUTO_DELIVER_HOURS_ENV_VAR,
             fulfillment.AUTO_COMPLETE_HOURS_ENV_VAR,
             policy.SETTLEMENT_HOLD_HOURS_ENV_VAR)


@pytest.fixture(autouse=True)
def _unconfigured(monkeypatch):
    for name in ALL_FLAGS:
        monkeypatch.delenv(name, raising=False)


def _tx(tx_id):
    quote = {"quote_id": f"q{tx_id}", "fee_policy_version": "MARKETPLACE_LEGACY_CURRENT",
             "payout_policy_version": "MARKETPLACE_PAYOUTS_V1", "platform_fee_bps": 1000,
             "merchandise_net_minor": 10000, "shipping_minor": 0, "tax_minor": 0,
             "seller_shipping_credit_minor": 0, "buyer_total_minor": 10000}
    return {"id": tx_id, "seller_user_id": 22, "item_type": "marketplace_product",
            "amount_cents": 10000, "platform_fee_cents": 1000, "seller_net_cents": 9000,
            "currency": "USD", "metadata_json": json.dumps({"commercial_quote": quote})}


def _shipped_order(tx_id):
    """A paid, shipped order with both a fulfillment record and a settlement."""
    from services import db

    ledger.ensure_schema(); settlement.ensure_schema(); fulfillment.ensure_schema()
    settlement.settle_paid_transaction(_tx(tx_id), payout_ready=True)
    conn = db.connect()
    try:
        cur = conn.cursor()
        fulfillment.open_fulfillment(cur, seller_transaction_id=tx_id, seller_id=22,
                                     buyer_user_id=77, fulfillment_kind="shipping")
        fulfillment.transition(cur, tx_id, fulfillment.SHIPPED, actor_role=fulfillment.SELLER,
                               actor="seller:22", idempotency_key=f"ship:{tx_id}",
                               tracking_reference=f"TRK{tx_id}")
        conn.commit()
    finally:
        conn.close()


# --- hosting it did not switch it on ---------------------------------------

def test_an_unconfigured_deployment_runs_neither_sweep():
    assert not cycle.fulfillment_sweep_enabled()
    assert not cycle.settlement_sweep_enabled()
    assert cycle.run_release_cycle_if_due({}) is None


def test_a_cycle_with_both_flags_off_does_nothing_at_all(monkeypatch):
    called = []
    monkeypatch.setattr(fulfillment, "sweep_auto_advance",
                        lambda **k: called.append("fulfillment"))
    monkeypatch.setattr(settlement, "sweep_eligibility",
                        lambda **k: called.append("settlement"))
    assert cycle.run_cycle() == {"fulfillment": None, "settlement": None}
    assert called == []


@pytest.mark.parametrize("value", ["", "   ", "maybe", "2"])
def test_an_unclear_flag_leaves_the_sweep_off(monkeypatch, value):
    monkeypatch.setenv(cycle.SETTLEMENT_SWEEP_ENABLED_ENV_VAR, value)
    assert not cycle.settlement_sweep_enabled()


@pytest.mark.parametrize("value,expected", [("", 300), ("abc", 300), ("1", 60),
                                            ("99999", 3600), ("600", 600)])
def test_a_mistyped_interval_cannot_produce_a_hot_loop(monkeypatch, value, expected):
    monkeypatch.setenv(cycle.INTERVAL_ENV_VAR, value)
    assert cycle.interval_seconds() == expected


# --- the fulfillment sweep --------------------------------------------------

def test_an_unconfigured_timeout_means_the_sweep_advances_nothing(monkeypatch):
    # The timeout variables are the first of two conditions. With them blank no
    # order is ever stamped, so an operator who turns the sweep on and forgets
    # the timeouts gets a no-op rather than a policy nobody chose.
    monkeypatch.setenv(cycle.FULFILLMENT_SWEEP_ENABLED_ENV_VAR, "true")
    _shipped_order(801)
    out = cycle.run_cycle(now=datetime.now(timezone.utc) + timedelta(days=30))
    assert out["fulfillment"]["considered"] == 0
    assert out["fulfillment"]["advanced"] == 0


def test_a_shipped_order_nobody_confirmed_is_eventually_delivered(monkeypatch):
    monkeypatch.setenv(fulfillment.AUTO_DELIVER_HOURS_ENV_VAR, "48")
    monkeypatch.setenv(cycle.FULFILLMENT_SWEEP_ENABLED_ENV_VAR, "true")
    _shipped_order(802)
    out = cycle.run_cycle(now=datetime.now(timezone.utc) + timedelta(days=3))
    assert out["fulfillment"]["advanced"] == 1

    from services import db
    conn = db.connect()
    try:
        record = fulfillment.get_fulfillment(conn.cursor(), 802)
    finally:
        conn.close()
    assert record["state"] == fulfillment.DELIVERED


def test_the_sweeps_timeout_delivery_opens_the_settlement_hold(monkeypatch):
    """The join itself: an order advancing on a clock has to reach settlement.

    A sweep that moved the order and told nothing else would leave the
    settlement in pending_fulfillment forever, which is exactly the state this
    whole mission exists to get orders out of.
    """
    monkeypatch.setenv(fulfillment.AUTO_DELIVER_HOURS_ENV_VAR, "48")
    monkeypatch.setenv(cycle.FULFILLMENT_SWEEP_ENABLED_ENV_VAR, "true")
    _shipped_order(803)
    assert settlement.get_settlement(803)["payout_state"] == "pending_fulfillment"

    out = cycle.run_cycle(now=datetime.now(timezone.utc) + timedelta(days=3))
    assert out["fulfillment"]["settled"] == 1
    assert settlement.get_settlement(803)["payout_state"] == "protection_hold"


def test_a_swept_order_is_not_swept_again(monkeypatch):
    monkeypatch.setenv(fulfillment.AUTO_DELIVER_HOURS_ENV_VAR, "48")
    monkeypatch.setenv(cycle.FULFILLMENT_SWEEP_ENABLED_ENV_VAR, "true")
    _shipped_order(804)
    future = datetime.now(timezone.utc) + timedelta(days=3)
    first = cycle.run_cycle(now=future)
    second = cycle.run_cycle(now=future)
    assert first["fulfillment"]["advanced"] == 1
    # Delivered with no auto-complete configured leaves the queue entirely.
    assert second["fulfillment"]["considered"] == 0


# --- the two sweeps in one pass --------------------------------------------

def test_an_order_can_travel_the_whole_chain_in_one_cycle(monkeypatch):
    """Fulfillment runs before settlement so a delivery opened this pass can be
    released in the same pass. The reverse order also works and always costs an
    extra interval, which is a seller waiting for no reason."""
    monkeypatch.setenv(fulfillment.AUTO_DELIVER_HOURS_ENV_VAR, "1")
    monkeypatch.setenv(policy.SETTLEMENT_HOLD_HOURS_ENV_VAR, "0")
    monkeypatch.setenv(cycle.FULFILLMENT_SWEEP_ENABLED_ENV_VAR, "true")
    monkeypatch.setenv(cycle.SETTLEMENT_SWEEP_ENABLED_ENV_VAR, "true")
    _shipped_order(810)

    out = cycle.run_cycle(now=datetime.now(timezone.utc) + timedelta(days=3))
    assert out["fulfillment"]["advanced"] == 1
    assert out["settlement"]["released"] >= 1
    assert settlement.get_settlement(810)["payout_state"] == "eligible"


def test_the_hold_still_applies_to_an_order_the_sweep_delivered(monkeypatch):
    """A timeout delivery must not be a way around the settlement hold.

    Two hours, not three days: the delivery timeout is one hour and the hold is
    forty-eight, so this is the only window where the sweep advances the order
    and the hold it opens is still standing. A clock far enough forward to clear
    both proves nothing about which of them did the refusing.
    """
    monkeypatch.setenv(fulfillment.AUTO_DELIVER_HOURS_ENV_VAR, "1")
    monkeypatch.setenv(policy.SETTLEMENT_HOLD_HOURS_ENV_VAR, "48")
    monkeypatch.setenv(cycle.FULFILLMENT_SWEEP_ENABLED_ENV_VAR, "true")
    monkeypatch.setenv(cycle.SETTLEMENT_SWEEP_ENABLED_ENV_VAR, "true")
    _shipped_order(811)

    out = cycle.run_cycle(now=datetime.now(timezone.utc) + timedelta(hours=2))
    assert out["fulfillment"]["advanced"] == 1
    assert settlement.get_settlement(811)["payout_state"] == "protection_hold"
    assert out["settlement"]["reasons"].get(settlement.HOLD_NOT_ELAPSED, 0) >= 1


def test_a_broken_fulfillment_sweep_does_not_strand_ready_settlements(monkeypatch):
    monkeypatch.setenv(policy.SETTLEMENT_HOLD_HOURS_ENV_VAR, "1")
    monkeypatch.setenv(cycle.FULFILLMENT_SWEEP_ENABLED_ENV_VAR, "true")
    monkeypatch.setenv(cycle.SETTLEMENT_SWEEP_ENABLED_ENV_VAR, "true")
    _shipped_order(812)
    settlement.mark_delivered(812, actor="buyer", idempotency_key="d:812")

    def _explode(**_kwargs):
        raise RuntimeError("fulfillment table is on fire")

    monkeypatch.setattr(fulfillment, "sweep_auto_advance", _explode)
    out = cycle.run_cycle(now=datetime.now(timezone.utc) + timedelta(days=3))
    assert out["fulfillment"]["error"]
    assert settlement.get_settlement(812)["payout_state"] == "eligible"


# --- the cycle never reaches a provider ------------------------------------

def test_the_release_cycle_moves_no_money(monkeypatch):
    """Both sweeps together must not be able to reach Stripe.

    They run unattended and are gated far more lightly than the payout worker,
    precisely because they cannot pay anyone. If that ever stopped being true
    the gating would be wrong, so it is asserted rather than assumed.
    """
    monkeypatch.setenv(fulfillment.AUTO_DELIVER_HOURS_ENV_VAR, "1")
    monkeypatch.setenv(policy.SETTLEMENT_HOLD_HOURS_ENV_VAR, "0")
    monkeypatch.setenv(cycle.FULFILLMENT_SWEEP_ENABLED_ENV_VAR, "true")
    monkeypatch.setenv(cycle.SETTLEMENT_SWEEP_ENABLED_ENV_VAR, "true")
    _shipped_order(820)

    from services.business_os.payments import seller_payouts
    calls = []
    monkeypatch.setattr(seller_payouts, "request_payout",
                        lambda *a, **k: calls.append(a))
    cycle.run_cycle(now=datetime.now(timezone.utc) + timedelta(days=3))
    assert calls == []
    assert settlement.get_settlement(820)["payout_state"] == "eligible"


# --- the host ---------------------------------------------------------------

def test_the_cycle_is_hosted_where_it_says_it_is():
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2]
    hosts = [p.name for p in list(root.glob("*_worker.py")) + [root / "bot.py"]
             if p.exists()
             and "marketplace_release_cycle" in p.read_text(encoding="utf-8", errors="ignore")]
    assert hosts == ["pulse_worker.py"], (
        "expected the release cycle to be hosted only by pulse_worker.py, found: "
        + (", ".join(hosts) or "no host at all"))


def test_the_heartbeat_says_which_gate_is_shut(monkeypatch):
    # A deployment that is paying nobody has to be distinguishable from one
    # that is broken, without reading the code.
    assert cycle.heartbeat_metadata({}) == {"release_cycle_enabled": False}
    monkeypatch.setenv(cycle.SETTLEMENT_SWEEP_ENABLED_ENV_VAR, "true")
    beat = cycle.heartbeat_metadata({})
    assert beat["release_cycle_enabled"]
    assert beat["settlement_sweep_enabled"]
    assert not beat["fulfillment_sweep_enabled"]
