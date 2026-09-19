"""Who may move a Marketplace order, and what a seller still cannot do.

The owner decision this file exists to enforce: *a seller alone must not be able
to release money by pressing "Delivered."* Every other rule here is downstream of
that one.

Driven through a bare in-memory SQLite cursor, using the module's own
``create_schema`` rather than a hand-copied DDL — a fixture that built its own
imitation of the tables would keep passing after a column changed underneath it.
The bare cursor also keeps this file off the shared application database, which
``tests/marketplace/`` cannot share a pytest process over.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from services import marketplace_order_fulfillment as fulfillment

TX = 9001
SELLER_ID = "77"
BUYER_ID = 31


@pytest.fixture
def cur():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    fulfillment.create_schema(cursor)
    fulfillment.open_fulfillment(
        cursor, seller_transaction_id=TX, seller_id=SELLER_ID,
        buyer_user_id=BUYER_ID, fulfillment_kind="shipped", order_id="ord_1")
    try:
        yield cursor
    finally:
        conn.close()


def _move(cur, to_state, *, role, actor="a", key=None, **kwargs):
    return fulfillment.transition(
        cur, TX, to_state, actor_role=role, actor=actor,
        idempotency_key=key or f"{role}:{to_state}", **kwargs)


def _ship(cur):
    return _move(cur, fulfillment.SHIPPED, role=fulfillment.SELLER,
                 tracking_reference="1Z999")


def _state(cur) -> str:
    return fulfillment.get_fulfillment(cur, TX)["state"]


# --- the rule the module exists for ------------------------------------------


def test_a_seller_cannot_mark_their_own_order_delivered(cur):
    _ship(cur)

    with pytest.raises(fulfillment.FulfillmentError) as caught:
        _move(cur, fulfillment.DELIVERED, role=fulfillment.SELLER)

    assert caught.value.code == fulfillment.ACTOR_NOT_AUTHORIZED
    # And the refusal left nothing behind: no state change, and no event that a
    # later reader could mistake for a delivery.
    assert _state(cur) == fulfillment.SHIPPED
    cur.execute("SELECT COUNT(*) FROM marketplace_order_fulfillment_events WHERE to_state=?",
                (fulfillment.DELIVERED,))
    assert cur.fetchone()[0] == 0


def test_a_seller_cannot_confirm_a_pickup_either(cur):
    """The pickup lane is the one a seller could most plausibly claim.

    Both parties are standing in the same room, so there is no tracking number
    and no carrier event — which is exactly why the confirmation has to come
    from the buyer rather than from whoever is holding the phone.
    """
    _move(cur, fulfillment.READY_FOR_PICKUP, role=fulfillment.SELLER)

    with pytest.raises(fulfillment.FulfillmentError) as caught:
        _move(cur, fulfillment.PICKED_UP, role=fulfillment.SELLER)

    assert caught.value.code == fulfillment.ACTOR_NOT_AUTHORIZED
    assert _state(cur) == fulfillment.READY_FOR_PICKUP


def test_no_state_a_seller_can_reach_is_a_delivering_state():
    """Asserted over the authority map, not over the two moves above.

    A later transition added with ``SELLER`` in its authority set would pass
    every scenario in this file and still hand the seller the release.
    """
    for (_, to_state), roles in fulfillment.TRANSITION_AUTHORITY.items():
        if fulfillment.SELLER in roles:
            assert to_state not in fulfillment.DELIVERING_STATES


def test_a_pickup_has_no_unattended_path_out_of_it():
    """``SYSTEM`` is the timeout sweeper, and it is not allowed to confirm a pickup.

    Giving the sweeper this move would restore exactly what the seller was just
    denied: the seller marks it ready, nobody disputes it, and the clock releases
    the money on the seller's unaided word.
    """
    for (frm, _), roles in fulfillment.TRANSITION_AUTHORITY.items():
        if frm == fulfillment.READY_FOR_PICKUP:
            assert fulfillment.SYSTEM not in roles


def test_every_legal_move_had_to_name_its_authority():
    """``ALLOWED_TRANSITIONS`` is derived, so the two cannot disagree.

    Pinned because the derivation is the safety property: a hand-written graph
    beside a hand-written authority map is one edit away from a move nobody
    decided who owns.
    """
    derived = {(frm, to) for frm, tos in fulfillment.ALLOWED_TRANSITIONS.items() for to in tos}
    assert derived == set(fulfillment.TRANSITION_AUTHORITY)
    for roles in fulfillment.TRANSITION_AUTHORITY.values():
        assert roles, "a move with an empty authority set is unreachable, not open"
        assert roles <= fulfillment.ACTOR_ROLES


# --- the paths that do work ---------------------------------------------------


def test_the_buyer_confirming_receipt_is_what_starts_settlement(cur):
    _ship(cur)
    result = _move(cur, fulfillment.DELIVERED, role=fulfillment.BUYER)

    assert result["settles_delivery"] is True
    assert result["fulfillment"]["delivered_at"]


def test_a_carrier_can_confirm_delivery_without_either_party(cur):
    _ship(cur)
    assert _move(cur, fulfillment.DELIVERED, role=fulfillment.CARRIER)["settles_delivery"] is True


def test_the_pickup_lane_reaches_settlement_through_the_buyer(cur):
    _move(cur, fulfillment.READY_FOR_PICKUP, role=fulfillment.SELLER)
    result = _move(cur, fulfillment.PICKED_UP, role=fulfillment.BUYER)

    assert result["settles_delivery"] is True
    # Recorded in `delivered_at` rather than a second column: downstream, a
    # pickup and a delivery are the same fact — the buyer has the goods.
    assert result["fulfillment"]["delivered_at"]


def test_completing_an_order_does_not_settle_a_second_time(cur):
    _ship(cur)
    _move(cur, fulfillment.DELIVERED, role=fulfillment.BUYER)
    assert _move(cur, fulfillment.COMPLETED, role=fulfillment.BUYER)["settles_delivery"] is False


def test_shipping_without_a_tracking_reference_is_refused(cur):
    with pytest.raises(fulfillment.FulfillmentError) as caught:
        _move(cur, fulfillment.SHIPPED, role=fulfillment.SELLER)
    assert caught.value.code == fulfillment.TRACKING_REQUIRED
    assert _state(cur) == fulfillment.PAID

    _ship(cur)
    assert fulfillment.get_fulfillment(cur, TX)["tracking_reference"] == "1Z999"


def test_a_skipped_step_is_refused_rather_than_inferred(cur):
    with pytest.raises(fulfillment.FulfillmentError) as caught:
        _move(cur, fulfillment.DELIVERED, role=fulfillment.BUYER)
    assert caught.value.code == fulfillment.ILLEGAL_TRANSITION


def test_an_order_with_no_record_is_not_an_order(cur):
    with pytest.raises(fulfillment.FulfillmentError) as caught:
        fulfillment.transition(cur, 424242, fulfillment.PROCESSING,
                               actor_role=fulfillment.SELLER, actor="a", idempotency_key="k")
    assert caught.value.code == fulfillment.NOT_FOUND


# --- replays and races --------------------------------------------------------


def test_a_replayed_confirmation_is_answered_not_reapplied(cur):
    _ship(cur)
    first = _move(cur, fulfillment.DELIVERED, role=fulfillment.BUYER, key="confirm-1")
    again = _move(cur, fulfillment.DELIVERED, role=fulfillment.BUYER, key="confirm-1")

    assert first["duplicate"] is False and again["duplicate"] is True
    # The second call must not re-trigger settlement, and must not restart the
    # buyer's protection window by re-stamping the delivery.
    assert again["settles_delivery"] is False
    assert again["fulfillment"]["delivered_at"] == first["fulfillment"]["delivered_at"]


def test_a_replay_is_recognised_before_the_state_is_read(cur):
    """A retry arriving after the order moved on must still read as a duplicate.

    Otherwise a network retry of a delivery confirmation, landing after the
    order completed, would be rejected as an illegal transition and the client
    would report a failure for something that succeeded.
    """
    _ship(cur)
    _move(cur, fulfillment.DELIVERED, role=fulfillment.BUYER, key="confirm-1")
    _move(cur, fulfillment.COMPLETED, role=fulfillment.BUYER)

    replay = _move(cur, fulfillment.DELIVERED, role=fulfillment.BUYER, key="confirm-1")
    assert replay["duplicate"] is True
    assert replay["fulfillment"]["state"] == fulfillment.COMPLETED


def test_the_first_ready_timestamp_is_the_one_that_stands(cur):
    first = _move(cur, fulfillment.READY_FOR_PICKUP, role=fulfillment.SELLER, key="r1")["fulfillment"]
    cur.execute("UPDATE marketplace_order_fulfillment SET state=? WHERE seller_transaction_id=?",
                (fulfillment.PROCESSING, TX))
    second = _move(cur, fulfillment.READY_FOR_PICKUP, role=fulfillment.SELLER, key="r2")["fulfillment"]

    assert second["ready_at"] == first["ready_at"]


def test_opening_the_same_order_twice_does_not_reset_it(cur):
    _ship(cur)
    fulfillment.open_fulfillment(cur, seller_transaction_id=TX, seller_id=SELLER_ID,
                                 buyer_user_id=BUYER_ID, fulfillment_kind="shipped")
    assert _state(cur) == fulfillment.SHIPPED


# --- role resolution ----------------------------------------------------------


def test_the_role_comes_from_the_record_not_from_the_caller(cur):
    """So a seller-facing route cannot simply pass ``"buyer"``.

    The authority map is only worth anything if the role reaching it is a fact
    about the order rather than a string the endpoint chose.
    """
    record = fulfillment.get_fulfillment(cur, TX)

    assert fulfillment.role_of(record, SELLER_ID) == fulfillment.SELLER
    assert fulfillment.role_of(record, BUYER_ID) == fulfillment.BUYER
    assert fulfillment.role_of(record, 999) == ""
    assert fulfillment.role_of(record, None) == ""
    assert fulfillment.role_of(record, "") == ""


def test_a_stranger_cannot_move_an_order_at_all(cur):
    record = fulfillment.get_fulfillment(cur, TX)
    with pytest.raises(fulfillment.FulfillmentError) as caught:
        _move(cur, fulfillment.PROCESSING, role=fulfillment.role_of(record, 999))
    assert caught.value.code == fulfillment.ACTOR_NOT_AUTHORIZED


# --- the timeout --------------------------------------------------------------


def test_an_unconfigured_deployment_never_advances_an_order_on_its_own(cur, monkeypatch):
    monkeypatch.delenv(fulfillment.AUTO_DELIVER_HOURS_ENV_VAR, raising=False)
    _ship(cur)

    assert fulfillment.get_fulfillment(cur, TX)["auto_advance_at"] is None
    later = datetime.now(timezone.utc) + timedelta(days=365)
    assert fulfillment.due_for_auto_advance(cur, now=later) == []


@pytest.mark.parametrize("value", ["", "   ", "0", "-5", "abc", "2.5"])
def test_an_unusable_timeout_means_no_timeout(cur, monkeypatch, value):
    """Fail inert.

    The realistic way this gets a wrong value is a mistyped Railway variable.
    Every unreadable spelling has to mean *wait for a human*, because the
    failure in the other direction is an order auto-confirming itself on a
    schedule nobody chose.
    """
    monkeypatch.setenv(fulfillment.AUTO_DELIVER_HOURS_ENV_VAR, value)
    _ship(cur)
    assert fulfillment.get_fulfillment(cur, TX)["auto_advance_at"] is None


def test_a_configured_timeout_puts_the_order_in_the_sweeper_queue(cur, monkeypatch):
    monkeypatch.setenv(fulfillment.AUTO_DELIVER_HOURS_ENV_VAR, "72")
    _ship(cur)

    assert fulfillment.due_for_auto_advance(cur) == []
    due = fulfillment.due_for_auto_advance(
        cur, now=datetime.now(timezone.utc) + timedelta(hours=73))
    assert [row["seller_transaction_id"] for row in due] == [TX]
    assert fulfillment.next_auto_state(fulfillment.SHIPPED) == fulfillment.DELIVERED


def test_the_sweeper_is_allowed_to_finish_what_it_picked_up(cur, monkeypatch):
    """The queue and the authority map have to agree, or the sweep is a no-op loop.

    Read separately from the map test above because this is the pairing that
    breaks quietly: a state that gets an ``auto_advance_at`` but no ``SYSTEM``
    authority would be selected on every cycle, refused on every cycle, and
    report nothing wrong.
    """
    monkeypatch.setenv(fulfillment.AUTO_DELIVER_HOURS_ENV_VAR, "1")
    monkeypatch.setenv(fulfillment.AUTO_COMPLETE_HOURS_ENV_VAR, "1")
    _ship(cur)

    for _ in range(2):
        due = fulfillment.due_for_auto_advance(
            cur, now=datetime.now(timezone.utc) + timedelta(hours=2))
        assert due, "the sweeper found nothing to do"
        row = due[0]
        to_state = fulfillment.next_auto_state(row["state"])
        assert to_state, f"{row['state']} is queued for a move that does not exist"
        _move(cur, to_state, role=fulfillment.SYSTEM, key=f"sweep:{to_state}")

    assert _state(cur) == fulfillment.COMPLETED


def test_a_completed_order_leaves_the_sweeper_queue(cur, monkeypatch):
    monkeypatch.setenv(fulfillment.AUTO_COMPLETE_HOURS_ENV_VAR, "1")
    _ship(cur)
    _move(cur, fulfillment.DELIVERED, role=fulfillment.BUYER)
    _move(cur, fulfillment.COMPLETED, role=fulfillment.BUYER)

    far = datetime.now(timezone.utc) + timedelta(days=30)
    assert fulfillment.due_for_auto_advance(cur, now=far) == []


def test_the_timeouts_are_read_per_call_not_captured_at_import(cur, monkeypatch):
    monkeypatch.setenv(fulfillment.AUTO_DELIVER_HOURS_ENV_VAR, "24")
    assert fulfillment._hours(fulfillment.AUTO_DELIVER_HOURS_ENV_VAR) == 24
    monkeypatch.setenv(fulfillment.AUTO_DELIVER_HOURS_ENV_VAR, "")
    assert fulfillment._hours(fulfillment.AUTO_DELIVER_HOURS_ENV_VAR) == 0


# --- the settlement boundary --------------------------------------------------


def test_the_transition_itself_never_touches_the_settlement_layer(cur, monkeypatch):
    """``mark_delivered`` opens its own connection, so it must run after the commit.

    Calling it from inside :func:`transition` would have it read a delivery that
    has not landed yet, and on Postgres block on the caller's own locks. The
    contract is therefore a returned instruction, not a call.
    """
    from services import marketplace_settlement_service

    called = []
    monkeypatch.setattr(marketplace_settlement_service, "mark_delivered",
                        lambda *a, **k: called.append((a, k)))
    _ship(cur)
    result = _move(cur, fulfillment.DELIVERED, role=fulfillment.BUYER)

    assert called == []
    assert result["settles_delivery"] is True

    assert fulfillment.settle_delivery(TX, actor="buyer:31", idempotency_key="k1") is True
    assert called and called[0][1]["idempotency_key"] == "fulfillment:k1"


def test_a_settlement_failure_is_not_the_buyers_problem(monkeypatch):
    """The buyer's order really did arrive and the record saying so is committed.

    Raising here would turn a settlement-side inconsistency into a 500 on the
    buyer's "I got it" button, and the retry would find the fulfillment
    transition already applied — so the buyer could never succeed.
    """
    from services import marketplace_settlement_service

    def explode(*_args, **_kwargs):
        raise RuntimeError("settlement not found")

    monkeypatch.setattr(marketplace_settlement_service, "mark_delivered", explode)
    assert fulfillment.settle_delivery(TX, actor="buyer:31", idempotency_key="k2") is False
