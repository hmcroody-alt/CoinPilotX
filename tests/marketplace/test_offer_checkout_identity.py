"""The offers lane cannot be made to charge twice for one logical attempt.

What was wrong
--------------
``offer_checkout`` derived its Stripe idempotency key from the
``seller_transactions`` row it had just inserted::

    POST -> INSERT -> lastrowid -> idempotency_key=f"marketplace-offer:{buyer}:{tx_id}"

A new row per attempt means a new key per attempt, so the provider key could not
deduplicate the one thing it existed to deduplicate. Five taps produced five
rows, five keys and five *payable* Stripe Checkout Sessions. Because the stock
decrement sits on the same path, it also produced five holds against one
listing — the inventory half of the defect, which is the half that strands real
units of a real seller's stock.

The lane had no replay cache at all, unlike cart and buy-now, which have had one
since they shipped.

What was wrong with the first fix
---------------------------------
The repair above over-corrected: it made the provider key *stable* across
attempts while leaving the provider parameters addressed by row. Stripe binds a
key to the parameters of the first request that used it, so that arrangement
turns every retry into a permanent HTTP 400 — which is the October 2026 cart
incident, mirror-imaged, and this file asserted it as the contract. See
``test_23b``, which replaces a test that had the rule backwards and would have
failed the correct fix.

The key now co-varies with the row the parameters name, and the deduplication it
used to be credited with is where it always belonged: the database claim, taken
before the ``seller_transactions`` INSERT, which deduplicates the row and the
stock hold as well as the payment.

How it is fixed
---------------
``services/marketplace_checkout_identity`` derives the identity of the *logical
payment attempt* from server-side truth only, and claims it in the database
before anything chargeable is built. The claim is the same row that later holds
the response, and its uniqueness is the pre-existing
``UNIQUE(user_id, idempotency_key)`` on ``marketplace_cart_checkout_keys``.

What this file is for
---------------------
Two kinds of assertion, deliberately separated.

*Behavioural.* The identity and claim functions are run against a real SQLite
database, including a genuinely concurrent five-tap race. These are facts about
what the code does.

*Structural.* The route's wiring is read off the **syntax tree**, not grepped.
The distinction matters here more than usual: the question is not whether the
string ``claim`` appears in the file, it is whether the claim is reached before
the ``INSERT`` and whether *every* exit after it either records a response or
hands the claim back. A grep cannot answer either, and a missed exit is a buyer
permanently unable to re-attempt an offer they are entitled to buy.

Why not post to the route
-------------------------
``offer_checkout`` reaches Stripe, the supplier gate, the commercial quote
engine and the notification system. Standing all of that up would test those
systems and report on this one. The identity mechanism is where the defect lived
and is exercised directly; the route is checked for correct *use* of it. The
end-to-end behaviour of the surrounding reservation lifecycle is already pinned
by ``test_reservation_sweeper.py`` and ``test_reservation_lifecycle.py``.

Note on `rowcount`
------------------
The whole claim rests on ``INSERT ... ON CONFLICT DO NOTHING`` reporting
``rowcount == 1`` only to the process that actually inserted. That was verified
empirically on SQLite 3.53.1 and on PostgreSQL 18.6, including the case that
matters most and cannot be reproduced on SQLite: a second connection racing an
*uncommitted* insert blocks on the index tuple and then reports 0. Cases 20 and
21 below pin the SQLite half permanently; the PostgreSQL half is recorded here
because this repo's test suite has no PostgreSQL to run against, and a test that
silently proved it only on SQLite would be the weaker claim dressed as the
stronger one.
"""

from __future__ import annotations

import ast
import json
import os
import sqlite3
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services import marketplace_checkout_identity as identity
from services import marketplace_reservation_policy as policy


ROUTES_PATH = (Path(__file__).resolve().parents[2]
               / "services" / "marketplace_offers_routes.py")
ROUTES_SRC = ROUTES_PATH.read_text(encoding="utf-8")
ROUTES_TREE = ast.parse(ROUTES_SRC)


#: One concrete logical attempt, used wherever the test needs "the same thing
#: the buyer is trying to do" twice.
ATTEMPT = dict(
    lane=identity.LANE_OFFER,
    buyer_user_id=41,
    offer_id=9001,
    listing_id=7,
    seller_user_id=12,
    amount_minor=2599,
    currency="USD",
    quantity=2,
    fulfillment="delivery",
    payment_mode="card",
)

NOW = "2026-10-02T12:00:00+00:00"


def _db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    identity.ensure_schema(cur)
    return conn, cur


# --------------------------------------------------------------------------
# 1-8 — the identity of a logical attempt
# --------------------------------------------------------------------------

def test_01_the_same_logical_attempt_yields_the_same_key():
    """The property the old key did not have.

    Nothing about the derivation can vary between two taps, because nothing in
    it comes from anything a tap creates.
    """
    assert identity.attempt_key(**ATTEMPT) == identity.attempt_key(**ATTEMPT)


@pytest.mark.parametrize("field,value", [
    ("buyer_user_id", 42),
    ("offer_id", 9002),
    ("listing_id", 8),
    ("seller_user_id", 13),
    ("amount_minor", 2600),
    ("currency", "EUR"),
    ("quantity", 3),
    ("fulfillment", "pickup"),
    ("payment_mode", "cash"),
])
def test_02_every_component_changes_the_key(field, value):
    """A different attempt must never replay a different attempt's answer.

    Parametrised one field at a time rather than asserted in bulk, because a
    derivation that silently dropped a component would still pass a test that
    only varied the components it happened to remember. A buyer who switches
    from delivery to pickup, or whose offer is re-accepted at another price, is
    not retrying — they are buying something else, and handing them the first
    session would charge them for the wrong thing.
    """
    other = {**ATTEMPT, field: value}
    assert identity.attempt_key(**other) != identity.attempt_key(**ATTEMPT)


def test_03_casing_and_whitespace_do_not_invent_a_second_attempt():
    """Normalisation is the difference between one session and two.

    Two requests that differ only in how a string was spelled are the same
    attempt. Without this, a currency arriving as ``usd`` on a retry would hash
    differently and build a second payable page.
    """
    noisy = {**ATTEMPT, "currency": "usd", "fulfillment": "  DELIVERY ",
             "payment_mode": "Card"}
    assert identity.attempt_key(**noisy) == identity.attempt_key(**ATTEMPT)


def test_04_numeric_components_are_compared_as_numbers():
    """``"2"`` and ``2`` are the same quantity, and must not be two attempts."""
    stringy = {**ATTEMPT, "quantity": "2", "amount_minor": "2599",
               "buyer_user_id": "41"}
    assert identity.attempt_key(**stringy) == identity.attempt_key(**ATTEMPT)


def test_05_no_client_supplied_token_participates():
    """The lane is strictly stronger than the cart lane, and that is structural.

    Cart and buy-now must accept a client ``idempotency_key`` because only the
    client knows that two carts are "the same cart"; they hash contents in the
    browser with FNV-1a and bind the result to ``user_id`` server-side. For an
    accepted offer the server already knows the entire attempt, so a client
    cannot vary it at all. Asserted on the signature so that adding a client
    token later is a deliberate, visible act rather than a quiet regression.
    """
    import inspect

    params = set(inspect.signature(identity.attempt_key).parameters)
    for forbidden in ("idempotency_key", "client_key", "token", "nonce",
                      "request_id"):
        assert forbidden not in params


def test_06_components_cannot_be_smuggled_across_the_separator():
    """A colon inside a component must not flatten two tuples onto one key.

    The join uses U+001F (unit separator), which cannot occur in any normalised
    component. With a ``":"`` separator these two distinct attempts would
    produce the identical string and therefore the identical key — one buyer
    replaying another's session.
    """
    a = {**ATTEMPT, "fulfillment": "a:b", "payment_mode": "c"}
    b = {**ATTEMPT, "fulfillment": "a", "payment_mode": "b:c"}
    assert identity.attempt_key(**a) != identity.attempt_key(**b)


def test_07_the_key_is_lane_namespaced_and_fits_the_column():
    """Namespaced so a future lane cannot collide inside the shared table, and
    short enough that the cart lane's 120-character truncation never touches it.
    """
    key = identity.attempt_key(**ATTEMPT)
    assert key.startswith(f"{identity.LANE_OFFER}:")
    assert len(key) < 120
    assert len(identity.stripe_idempotency_key(key)) < 255


def test_08_the_provider_key_derivation_is_pure_and_namespaced():
    """A property of the helper, not of the route's choice of argument.

    ``stripe_idempotency_key`` is a pure namespacing function: same input, same
    output, recognisable prefix. What the *route* feeds it is a separate
    question, and the answer is "the attempt plus the row the parameters name"
    — see ``test_23b``. This test deliberately stops at the helper, because the
    file used to conflate the two and concluded that a route-level stable key
    was the contract.
    """
    key = identity.attempt_key(**ATTEMPT)
    provider = identity.stripe_idempotency_key(key)
    assert provider == identity.stripe_idempotency_key(identity.attempt_key(**ATTEMPT))
    assert provider.startswith("marketplace-")
    # The sub-lane suffix the route uses for the native sheet must not collide
    # with the hosted session for the same attempt: they are two different
    # provider objects for one logical attempt.
    assert (identity.stripe_idempotency_key(f"{key}:sheet")
            != identity.stripe_idempotency_key(key))


# --------------------------------------------------------------------------
# 9-18 — claiming, replaying and releasing
# --------------------------------------------------------------------------

def test_09_the_first_caller_claims_it():
    conn, cur = _db()
    key = identity.attempt_key(**ATTEMPT)
    assert identity.claim(cur, user_id=41, key=key, now=NOW)["state"] == identity.CLAIMED


def test_10_a_second_caller_before_any_response_is_told_in_progress():
    """Two simultaneous requests: the loser must not build a second session.

    ``IN_PROGRESS`` rather than an error, and a distinct code, so the client can
    retry quietly instead of showing the buyer a failure for something that is
    merely still in flight.
    """
    conn, cur = _db()
    key = identity.attempt_key(**ATTEMPT)
    identity.claim(cur, user_id=41, key=key, now=NOW)

    second = identity.claim(cur, user_id=41, key=key, now=NOW)

    assert second["state"] == identity.IN_PROGRESS
    assert identity.IN_PROGRESS_CODE == "CHECKOUT_IN_PROGRESS"


def test_11_a_caller_after_the_response_replays_the_exact_answer():
    """Stripe session created, PulseSoc response lost in transit.

    The retry must recover the original session rather than produce another
    chargeable checkout. The payload is compared whole, because handing back a
    *similar* answer with a different ``checkout_url`` would be the defect.
    """
    conn, cur = _db()
    key = identity.attempt_key(**ATTEMPT)
    identity.claim(cur, user_id=41, key=key, now=NOW)
    payload = {"ok": True, "checkout_url": "https://checkout.stripe.com/c/pay/cs_test_1",
               "transaction_id": 500, "amount_cents": 2599}
    assert identity.remember(cur, user_id=41, key=key, payload=payload) is True

    replay = identity.claim(cur, user_id=41, key=key, now=NOW)

    assert replay["state"] == identity.REPLAY
    assert replay["payload"] == payload


def test_12_remember_is_first_writer_wins():
    """A slow writer must not overwrite the answer the buyer already has."""
    conn, cur = _db()
    key = identity.attempt_key(**ATTEMPT)
    identity.claim(cur, user_id=41, key=key, now=NOW)
    first = {"checkout_url": "https://checkout.stripe.com/c/pay/cs_test_FIRST"}
    identity.remember(cur, user_id=41, key=key, payload=first)

    wrote = identity.remember(cur, user_id=41, key=key,
                              payload={"checkout_url": "SECOND"})

    assert wrote is False
    assert identity.claim(cur, user_id=41, key=key, now=NOW)["payload"] == first


def test_13_releasing_an_unanswered_claim_lets_the_buyer_retry():
    """Server error or Stripe failure before anything chargeable existed.

    The buyer must not be permanently locked out of an offer they are entitled
    to buy.

    This docstring used to go on: "only safe because the provider key is
    attempt-derived: if the failed try did reach Stripe, the retry presents the
    same key and Stripe returns the original session instead of creating a
    second one." Not true. Stripe compares parameters, and this lane's
    parameters name the ``seller_transactions`` row — so the retry presented a
    burned key and got a permanent 400 rather than the original session. The
    release was the trigger for the lockout it was written to prevent.

    What makes it safe now is reachability. The key co-varies with the
    parameters (``test_23b``), so a retry does mint a second session — and the
    first one's url was never stored, never logged and never returned in any
    response, because the only path that could have returned it is the path
    that failed. The failure handler expires it outright as well.
    """
    conn, cur = _db()
    key = identity.attempt_key(**ATTEMPT)
    identity.claim(cur, user_id=41, key=key, now=NOW)

    assert identity.release(cur, user_id=41, key=key) is True
    assert identity.claim(cur, user_id=41, key=key, now=NOW)["state"] == identity.CLAIMED


def test_14_a_completed_attempt_is_never_released():
    """The guard that stops a failure path deleting a live answer.

    Without the ``response_json`` scope on the DELETE, a late error handler
    could drop a claim whose session already exists, and the next request would
    build a second charge surface for an attempt that already has one.
    """
    conn, cur = _db()
    key = identity.attempt_key(**ATTEMPT)
    identity.claim(cur, user_id=41, key=key, now=NOW)
    identity.remember(cur, user_id=41, key=key, payload={"checkout_url": "live"})

    assert identity.release(cur, user_id=41, key=key) is False
    assert identity.claim(cur, user_id=41, key=key, now=NOW)["state"] == identity.REPLAY


def test_15_one_buyer_cannot_deny_another_buyers_attempt():
    """Uniqueness is on the pair, so the claim is not a cross-tenant lock.

    Two buyers can hold accepted offers that normalise to the same digest only
    if every component matches including ``buyer_user_id``, so in practice this
    cannot arise — but the claim is bound by ``user_id`` as well, and that
    binding is what makes a stolen or guessed key useless.
    """
    conn, cur = _db()
    key = identity.attempt_key(**ATTEMPT)
    assert identity.claim(cur, user_id=41, key=key, now=NOW)["state"] == identity.CLAIMED
    assert identity.claim(cur, user_id=99, key=key, now=NOW)["state"] == identity.CLAIMED


def test_16_another_buyer_cannot_read_a_stored_response():
    """Replay is scoped to the owner, so a known key leaks nothing.

    The ``checkout_url`` in a stored payload is a payable page. If replay were
    keyed on the attempt alone, anyone who could derive or observe a key could
    fetch another buyer's checkout.
    """
    conn, cur = _db()
    key = identity.attempt_key(**ATTEMPT)
    identity.claim(cur, user_id=41, key=key, now=NOW)
    identity.remember(cur, user_id=41, key=key,
                      payload={"checkout_url": "https://private"})

    other = identity.claim(cur, user_id=99, key=key, now=NOW)

    assert other["state"] == identity.CLAIMED
    assert "payload" not in other


def test_16b_a_stored_response_is_not_readable_through_a_foreign_claim():
    """The owner scope on the *read*, reached by the only path that reaches it.

    Case 16 does not actually exercise this, and a mutation proved it: strip the
    ``user_id`` from ``_stored_response``'s WHERE clause and case 16 stays green,
    because a different buyer's INSERT succeeds on the composite key and returns
    ``CLAIMED`` without ever reading a stored response.

    The read is reachable when the foreign buyer already holds a blank claim
    under the same key. Then the INSERT conflicts, the lookup runs, and an
    unscoped query would hand buyer 99 the ``checkout_url`` from buyer 41's
    attempt — a payable page belonging to somebody else. Both the write binding
    and the read binding are load-bearing, for different callers.
    """
    conn, cur = _db()
    key = identity.attempt_key(**ATTEMPT)

    # Ordering matters, and getting it wrong hides the bug. The real buyer must
    # complete *first*, so that their answered row is the one an unscoped
    # ``LIMIT 1`` would reach. With the foreign buyer inserted first, the
    # unscoped query finds that buyer's own blank row, reads it as "no response
    # yet", and returns IN_PROGRESS -- the correct answer arrived at for the
    # wrong reason, and the test passes while the leak is wide open. A mutation
    # run is what exposed that; the first version of this case was written in
    # the order that conceals it.
    identity.claim(cur, user_id=41, key=key, now=NOW)
    identity.remember(cur, user_id=41, key=key,
                      payload={"checkout_url": "https://private-to-41"})

    # The foreign buyer now takes their own claim on the same key, and retries.
    assert identity.claim(cur, user_id=99, key=key, now=NOW)["state"] == identity.CLAIMED
    again = identity.claim(cur, user_id=99, key=key, now=NOW)

    # Their own attempt is unanswered, so the honest answer is in-flight. It is
    # never buyer 41's payable page.
    assert again["state"] == identity.IN_PROGRESS
    assert "private-to-41" not in json.dumps(again)


def test_17_a_corrupt_stored_response_is_neither_served_nor_ignored():
    """Two wrong answers to avoid, not one.

    Serving corrupt JSON as the buyer's answer is obviously wrong. Treating it
    as "no attempt here" is the dangerous one: it would build a second charge
    surface. ``IN_PROGRESS`` is the honest reading — something happened, and
    this request does not get to decide what.
    """
    conn, cur = _db()
    key = identity.attempt_key(**ATTEMPT)
    identity.claim(cur, user_id=41, key=key, now=NOW)
    cur.execute(
        f"UPDATE {identity.KEY_TABLE} SET response_json='{{not json' "
        "WHERE user_id=? AND idempotency_key=?", (41, key))

    assert identity.claim(cur, user_id=41, key=key, now=NOW)["state"] == identity.IN_PROGRESS


def test_18_an_unserialisable_payload_is_reported_not_swallowed():
    """"We created a session but failed to record it" is worth asserting about.

    If ``remember`` returned ``True`` here, the claim would stay blank, the next
    request would read ``IN_PROGRESS`` forever, and the buyer would never be
    handed the session that exists.
    """
    conn, cur = _db()
    key = identity.attempt_key(**ATTEMPT)
    identity.claim(cur, user_id=41, key=key, now=NOW)

    class Unserialisable:
        def __repr__(self):
            raise RuntimeError("nope")

    assert identity.remember(cur, user_id=41, key=key,
                             payload={"x": Unserialisable()}) is False


def test_19_the_schema_is_owned_by_this_module():
    """A module that gates charges must be bootable on its own.

    The reservation sweep shipped broken in production for exactly the opposite
    reason: its columns were created by a cart route handler, and the worker
    that needed them never served an HTTP request. ``ensure_schema`` is
    idempotent and does not depend on any other module having run.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    assert identity.ensure_schema(cur) is True
    assert identity.ensure_schema(cur) is True
    cur.execute(f"SELECT COUNT(*) FROM {identity.KEY_TABLE}")
    assert cur.fetchone()[0] == 0
    # And it is the table the cart lane already owns, not a parallel one.
    assert identity.KEY_TABLE == "marketplace_cart_checkout_keys"


# --------------------------------------------------------------------------
# 20-21 — the races the defect actually produced
# --------------------------------------------------------------------------

def test_20_five_repeated_taps_claim_once():
    """The brief's five-tap case, run rather than argued.

    Under the old key this produced five rows, five provider keys, five payable
    sessions and five stock decrements. Exactly one caller may now proceed to
    build a charge surface.
    """
    conn, cur = _db()
    key = identity.attempt_key(**ATTEMPT)

    states = [identity.claim(cur, user_id=41, key=key, now=NOW)["state"]
              for _ in range(5)]

    assert states.count(identity.CLAIMED) == 1
    assert states.count(identity.IN_PROGRESS) == 4
    cur.execute(f"SELECT COUNT(*) FROM {identity.KEY_TABLE} "
                "WHERE user_id=? AND idempotency_key=?", (41, key))
    assert cur.fetchone()[0] == 1


def test_21_the_same_logical_request_from_two_workers_claims_once(tmp_path):
    """Two processes' worth of contention, on separate connections.

    The point of a file-backed database and two real threads is that neither
    connection can see the other's uncommitted state, which is the production
    shape: two gunicorn workers, two connections, one logical attempt. There is
    no lock, no in-memory registry and no coordination between the workers — the
    guarantee is the unique index, which is why it survives a restart and why it
    does not care which process wins.

    SQLite serialises writers at the file level, so this proves single-winner
    but cannot exhibit the uncommitted-contention window. That window was
    verified separately on PostgreSQL 18.6: the racing connection blocked on the
    index tuple for as long as the first held its transaction open, then
    reported ``rowcount == 0``. See this module's docstring.
    """
    path = tmp_path / "claims.db"
    boot = sqlite3.connect(path)
    boot.row_factory = sqlite3.Row
    identity.ensure_schema(boot.cursor())
    boot.commit()
    boot.close()

    key = identity.attempt_key(**ATTEMPT)
    gate = threading.Barrier(2)
    states = []
    lock = threading.Lock()

    def worker():
        conn = sqlite3.connect(path, timeout=10)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        gate.wait()
        state = identity.claim(cur, user_id=41, key=key, now=NOW)["state"]
        conn.commit()
        with lock:
            states.append(state)
        conn.close()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert len(states) == 2
    assert states.count(identity.CLAIMED) == 1

    check = sqlite3.connect(path)
    rows = check.execute(
        f"SELECT COUNT(*) FROM {identity.KEY_TABLE} "
        "WHERE user_id=? AND idempotency_key=?", (41, key)).fetchone()[0]
    check.close()
    assert rows == 1


# --------------------------------------------------------------------------
# 22-28 — how the route uses it, read off the syntax tree
# --------------------------------------------------------------------------

def _handler_fn():
    """The nested ``handler`` inside ``offer_checkout``, as a syntax tree.

    Found by walking rather than by slicing the source on line numbers, so that
    moving the function does not silently empty the assertions below.
    """
    for node in ast.walk(ROUTES_TREE):
        if isinstance(node, ast.FunctionDef) and node.name == "offer_checkout":
            for inner in ast.walk(node):
                if isinstance(inner, ast.FunctionDef) and inner.name == "handler":
                    return inner
    raise AssertionError("offer_checkout.handler not found")


def _calls(fn, dotted):
    """Every call to ``a.b`` inside ``fn``, with its line number."""
    found = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)
                and f"{f.value.id}.{f.attr}" == dotted):
            found.append(node.lineno)
    return found


def test_22_the_claim_is_taken_before_the_transaction_row_is_written():
    """Ordering is the entire concurrency control.

    Stripe is the *last* thing on this path. If the claim came after the
    ``INSERT``, two concurrent requests would both write a transaction row and
    both decrement stock before either reached the provider — so provider
    idempotency alone would not have been enough, and the stock half of the
    defect would survive a correct Stripe key.
    """
    fn = _handler_fn()
    claims = _calls(fn, "checkout_identity.claim")
    assert len(claims) == 1, "exactly one claim site expected"

    inserts = [
        node.lineno for node in ast.walk(fn)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and "INSERT INTO seller_transactions" in node.value
    ]
    assert inserts, "the transaction INSERT should still be here"
    assert claims[0] < min(inserts)


def test_23_the_stripe_key_goes_through_the_shared_derivation():
    """Every provider key in this module comes from one place.

    The original defect was each lane spelling its own key inline, so the rule
    lived in three files and was wrong in all three. Asserted over the keyword
    arguments on the syntax tree, so a new f-string fails here however it is
    spelled.

    This test used to additionally forbid ``tx_id`` from appearing inside the
    argument, under the heading "the stripe key is never derived from the row
    id". That ban was wrong, and it was wrong in the most expensive direction:
    it demanded precisely the arrangement that produced the October 2026
    incident. See ``test_23b``.
    """
    fn = _handler_fn()
    keyword_values = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg == "idempotency_key":
                keyword_values.append(kw.value)

    assert keyword_values, "the route should still send a provider key"
    for value in keyword_values:
        assert isinstance(value, ast.Call), ast.dump(value)
        f = value.func
        assert isinstance(f, ast.Attribute)
        assert f.attr == "stripe_idempotency_key"


def test_23b_the_stripe_key_covaries_with_the_row_the_parameters_name():
    """The contract this file previously had backwards.

    Stripe binds an idempotency key to the *parameters* of the first request
    that used it, for 24 hours; the same key presented with different
    parameters is answered with HTTP 400 ``idempotency_error`` for the rest of
    the window. So "stable key" is only a replay when the parameters are stable
    too — and in this lane they never are. ``success_url``, ``cancel_url``,
    ``transfer_group`` and ``metadata.seller_transaction_id`` all name ``tx_id``,
    which is a fresh ``lastrowid`` on every attempt.

    This lane therefore held the cart's bug with the halves swapped, and the
    release at the bottom of the failure path was its trigger: hand the claim
    back, let attempt 2 through, and attempt 2 presents the burned key. It never
    fired only because the lane has no UI callers.

    Which means the previous version of ``test_23`` was not merely inert — it
    was a green gate holding the defect in place, and it would have failed the
    fix. It passed after the fix only because the row id now arrives through a
    variable named ``provider_attempt``, i.e. by one accident of naming.

    So the assertion is inverted to match the contract: the key must name the
    row, and it is read through the assignment rather than off the call site,
    because an indirection is exactly what the old spelling check could not
    see.
    """
    fn = _handler_fn()

    bound = {}
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            bound[node.targets[0].id] = ast.dump(node.value)

    row_sources = ("tx_id", "lastrowid")
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg != "idempotency_key":
                continue
            # The argument as written, plus the definition of any local name it
            # mentions — one level is enough and more would stop being readable.
            text = ast.dump(kw.value)
            for name in sorted(bound):
                if f"id='{name}'" in text:
                    text += " || " + bound[name]
            assert any(src in text for src in row_sources), (
                "a provider key is sent that does not name the transaction its "
                "success_url, cancel_url, transfer_group and metadata are all "
                f"derived from. Stripe will refuse the retry: {text}")


def test_24_every_successful_exit_records_its_response():
    """Three money surfaces — cash, native sheet, hosted session — and each must
    be replayable. A success that returns without recording leaves the claim
    blank forever, so the buyer's retry reads ``IN_PROGRESS`` and they can never
    be handed the thing that was built for them.
    """
    fn = _handler_fn()
    assert len(_calls(fn, "checkout_identity.remember")) == 3


def test_25_every_failing_exit_after_the_claim_hands_it_back():
    """Counted against the release sites rather than asserted as "some exist".

    Three failure paths exist after the claim: Stripe not configured, the
    out-of-stock rollback, and the provider exception handler. Each must
    release, or a buyer hits a transient failure once and is locked out of that
    offer permanently.
    """
    fn = _handler_fn()
    assert len(_calls(fn, "checkout_identity.release")) == 3


def test_26_no_exit_after_the_claim_is_left_unaccounted_for():
    """The assertion that actually guards against a *future* edit.

    Rather than trusting the two counts above, every ``return`` statement
    positioned after the claim is enumerated and required to be preceded by a
    ``remember`` or a ``release``. Adding a new early return after the claim —
    the easy and silent mistake — fails here.
    """
    fn = _handler_fn()
    claim_line = _calls(fn, "checkout_identity.claim")[0]
    accounted = set(_calls(fn, "checkout_identity.remember")
                    + _calls(fn, "checkout_identity.release"))

    # The claim's own dispatch is excluded, and identified structurally rather
    # than by line offset. The REPLAY and IN_PROGRESS branches return *because*
    # this request did not take the claim, so there is nothing for them to
    # record or hand back; requiring them to would be requiring the loser of the
    # race to delete the winner's claim. Found by locating the `if` statements
    # whose test reads the claim result, so inserting another branch there stays
    # exempt while a new exit anywhere downstream does not.
    dispatch_end = claim_line
    for node in ast.walk(fn):
        if not isinstance(node, ast.If):
            continue
        names = {n.id for n in ast.walk(node.test) if isinstance(n, ast.Name)}
        if "claimed" in names:
            dispatch_end = max(dispatch_end,
                               max(c.lineno for c in ast.walk(node)
                                   if hasattr(c, "lineno")))
    assert dispatch_end > claim_line, "the claim result should be dispatched on"

    returns = sorted(
        node.lineno for node in ast.walk(fn)
        if isinstance(node, ast.Return) and node.lineno > dispatch_end
    )
    assert returns, "the route should still return after claiming"

    for line in returns:
        preceding = [a for a in accounted if a < line]
        assert preceding, f"return at line {line} neither records nor releases"
        # The nearest accounting call must be close enough to belong to this
        # exit rather than to an earlier branch. Ten lines covers a multi-line
        # payload literal and its call; a genuinely unaccounted return sits far
        # from any of them.
        assert line - max(preceding) <= 12, (
            f"return at line {line} is {line - max(preceding)} lines from the "
            "nearest remember/release — probably an unaccounted exit")


def test_27_the_in_progress_refusal_tells_the_buyer_they_were_not_charged():
    """A 409 that reads like a payment failure would be worse than useless.

    The buyer of a losing tap has not failed at anything, and the one thing they
    will want to know is whether their card was hit twice.
    """
    assert "not been charged twice" in identity.IN_PROGRESS_MESSAGE
    fn = _handler_fn()
    # Surfaced on both keys, because pulseApi reads `error_code` and ignores
    # `code`, while the web client reads `code`.
    src = ast.get_source_segment(ROUTES_SRC, fn) or ""
    assert "error_code=checkout_identity.IN_PROGRESS_CODE" in src
    assert "code=checkout_identity.IN_PROGRESS_CODE" in src


def test_28_the_reservation_is_written_with_a_durable_deadline():
    """The second half of the offers defect.

    The lane's hold was inserted without ``reserved_at`` or ``expires_at``, and
    the sweep's candidate predicate requires ``expires_at IS NOT NULL AND
    expires_at <> ''``. So every offers hold was invisible to expiry forever —
    not collected late, never collected. Production held four such rows, the
    oldest from 2026-08-13.
    """
    fn = _handler_fn()
    reservation_inserts = [
        node.value for node in ast.walk(fn)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and "marketplace_inventory_reservations" in node.value
        and "INSERT" in node.value
    ]
    assert reservation_inserts, "the hold INSERT should still be here"
    for sql in reservation_inserts:
        assert "expires_at" in sql
        assert "reserved_at" in sql

    assert _calls(fn, "reservation_policy.expires_at_for"), (
        "the deadline must come from the canonical policy, not a local literal")


def test_29_the_written_deadline_is_one_the_sweep_can_actually_see():
    """Closing the loop: the value the route writes satisfies the sweep's query.

    Asserted against the policy rather than against a copied format string,
    because the failure mode here is a deadline that is written, looks right,
    and still never compares ``<=`` an ISO cutoff.
    """
    deadline = policy.expires_at_for(NOW)
    assert deadline
    assert deadline.strip() != ""
    assert policy.parse_timestamp(deadline) is not None
    # Past its own deadline plus grace, the sweep must consider it expired.
    later = policy.parse_timestamp(deadline)
    assert policy.is_expired(deadline, now=later.isoformat()) is False
    from datetime import timedelta
    well_past = (later + timedelta(seconds=policy.EXPIRY_GRACE_SECONDS + 1))
    assert policy.is_expired(deadline, now=well_past.isoformat()) is True


def test_30_the_module_is_imported_under_a_stable_alias():
    """The structural assertions above all key on ``checkout_identity.``.

    If the import were renamed, every one of them would silently stop finding
    anything and go green. This is the guard on the guards.
    """
    aliases = set()
    for node in ast.walk(ROUTES_TREE):
        if isinstance(node, ast.ImportFrom) and node.module == "services":
            for alias in node.names:
                if alias.name == "marketplace_checkout_identity":
                    aliases.add(alias.asname or alias.name)
    assert aliases == {"checkout_identity"}


# --------------------------------------------------------------------------
# 31-37 — §24 security audit of the offers lane
#
# Each case below is one of the attack classes the brief names, asserted rather
# than asserted-about. Where the honest finding is "bounded, not zero", the
# bound is *computed* from the server's own allowlists (case 37) instead of
# being claimed in prose, so it cannot quietly grow.
# --------------------------------------------------------------------------

def test_31_the_handler_accepts_exactly_three_client_controlled_inputs():
    """Default-deny on the request body.

    The money-path question is not "is each input validated" but "how many
    inputs are there". Enumerated off the syntax tree so a fourth one cannot be
    added without this failing: the review that matters is the one that happens
    when the surface *grows*, and nobody re-reads a 350-line handler to notice
    a new ``payload.get``.

    All three survivors are narrowed by a server-owned allowlist before use:
    ``payment_mode`` collapses to exactly ``{card, cash}``,
    ``fulfillment`` is only consulted for a kind the *listing* declares
    undecided and must match ``_LANE_ANSWERS``, and ``fulfillment_details`` is
    field-validated per kind. None of them is an amount, a currency, a party,
    a quantity, or an idempotency token.
    """
    fn = _handler_fn()
    read_keys = set()
    for node in ast.walk(fn):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "payload"
                and node.args and isinstance(node.args[0], ast.Constant)):
            read_keys.add(node.args[0].value)

    assert read_keys == {"payment_mode", "fulfillment", "fulfillment_details"}, (
        f"client-controlled input surface changed: {sorted(read_keys)}")

    # And nothing else reaches into the request object. Collected as *attribute
    # access on the name*, not as calls on it. The first spelling here matched
    # only ``request.X()`` and a mutation walked straight through it: in
    # ``request.args.get("amount_minor")`` the call is on ``request.args``, so
    # the `request` name sits one level further down than the comprehension
    # looked. ``args``, ``form``, ``values``, ``headers`` and ``cookies`` are
    # all shaped exactly that way — the narrow version was blind to every
    # query-string and form input there is, which is the half of the request
    # surface a JSON-body audit is least likely to think about.
    request_attrs = {
        node.attr for node in ast.walk(fn)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name) and node.value.id == "request"
    }
    assert request_attrs <= {"get_json", "url_root"}, (
        f"handler reads request.{sorted(request_attrs)} outside the audited body")


def test_32_no_client_value_takes_part_in_the_attempt_identity():
    """§9: never trust a client token without server binding.

    The strongest version of that rule is that there is no client token at all.
    Every keyword handed to ``attempt_key`` is checked for any reference to
    ``payload`` or ``request`` anywhere in its expression tree — not just as a
    bare name, so ``payload.get("x") or seller_id`` would also fail.

    This is the assertion that makes the key *server-side truth* rather than
    merely *hard to guess*. A guessable key is fine; a buyer-chosen one is not,
    because a buyer who can vary the key can defeat their own deduplication,
    and a buyer who can *collide* one can read another buyer's stored response.
    """
    fn = _handler_fn()
    sites = [node for node in ast.walk(fn)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
             and node.func.attr == "attempt_key"]
    assert len(sites) == 1, "exactly one attempt_key call site expected"
    call = sites[0]

    for kw in call.keywords:
        names = {n.id for n in ast.walk(kw.value) if isinstance(n, ast.Name)}
        tainted = names & {"payload", "request"}
        assert not tainted, f"attempt_key({kw.arg}=...) reads {tainted}"

    # Every parameter the derivation declares must actually be supplied here.
    # Read off the identity module's own signature, so widening the identity
    # forces the route to feed it rather than silently defaulting to 0/"".
    ident_tree = ast.parse(
        (Path(identity.__file__)).read_text(encoding="utf-8"))
    declared = set()
    for node in ast.walk(ident_tree):
        if isinstance(node, ast.FunctionDef) and node.name == "attempt_key":
            declared = {a.arg for a in node.args.kwonlyargs}
    assert declared, "attempt_key signature not found"
    assert {kw.arg for kw in call.keywords} == declared, (
        "the route does not bind every component of the attempt identity")


def test_33_buyer_authorisation_precedes_the_claim_and_every_write():
    """IDOR, and the ordering that makes refusing it free.

    ``offer_id`` is a path parameter, so any logged-in account can name any
    offer. The 403 is not interesting by itself — what matters is that it is
    reached before the claim, before the transaction row and before the stock
    decrement. An authorisation check *after* a claim would let a stranger
    occupy the real buyer's attempt key, which is a denial of service on
    someone else's purchase that leaves no trace of a refusal.
    """
    fn = _handler_fn()
    guards = [node.lineno for node in ast.walk(fn)
              if isinstance(node, ast.Constant) and isinstance(node.value, str)
              and "Only the buyer can check out" in node.value]
    assert len(guards) == 1, "the buyer-identity guard should be here exactly once"

    claim_line = _calls(fn, "checkout_identity.claim")[0]
    writes = [node.lineno for node in ast.walk(fn)
              if isinstance(node, ast.Constant) and isinstance(node.value, str)
              and ("INSERT INTO" in node.value or "UPDATE marketplace_listings" in node.value)]
    assert writes, "the handler should still write something"
    assert guards[0] < claim_line < min(writes), (
        "authorisation must precede the claim, and the claim must precede every write")


def test_34_the_parties_and_the_money_come_from_the_offer_row():
    """Seller crossover, amount manipulation, currency manipulation.

    ``seller_user_id``, ``listing_id``, ``currency`` and ``qty`` are read out of
    the stored offer; ``amount`` is the commercial quote's buyer total, computed
    from the offer's accepted ``amount_minor``. Asserted here as "these names
    are assigned from ``offer``/``commercial_quote`` before the key is built",
    which is the property that makes the key uncounterfeitable rather than the
    property that it merely hashes ten things.
    """
    fn = _handler_fn()
    sources: dict[str, set[str]] = {}
    for node in ast.walk(fn):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        sources.setdefault(target.id, set()).update(
            n.id for n in ast.walk(node.value) if isinstance(n, ast.Name))
        sources[target.id].update(
            n.value.id for n in ast.walk(node.value)
            if isinstance(n, ast.Subscript) and isinstance(n.value, ast.Name))

    assert "offer" in sources.get("seller_id", set())
    assert "offer" in sources.get("listing_id", set())
    assert "offer" in sources.get("currency", set())
    assert "offer" in sources.get("qty", set())
    assert "commercial_quote" in sources.get("amount", set())
    # The buyer is the session, never the body.
    assert sources.get("buyer_id", set()) <= {"user", "int"}, (
        f"buyer_id derived from {sources.get('buyer_id')}")


def test_35_two_buyers_on_one_offer_cannot_block_or_read_each_other():
    """The inverse of IDOR: isolation must not become a shared lock.

    A key that did *not* include the buyer would let whoever taps first occupy
    the attempt for everyone else — and, worse, hand them the first buyer's
    stored response, which carries a Stripe URL that is payable by anyone
    holding it. Both halves are checked: the keys differ, and each buyer's
    stored response is invisible to the other.
    """
    conn, cur = _db()
    a_key = identity.attempt_key(**{**ATTEMPT, "buyer_user_id": 41})
    b_key = identity.attempt_key(**{**ATTEMPT, "buyer_user_id": 42})
    assert a_key != b_key

    assert identity.claim(cur, user_id=41, key=a_key, now=NOW)["state"] == identity.CLAIMED
    assert identity.claim(cur, user_id=42, key=b_key, now=NOW)["state"] == identity.CLAIMED
    identity.remember(cur, user_id=41, key=a_key,
                      payload={"ok": True, "checkout_url": "https://stripe/A"})

    # Buyer 42 replaying their own attempt must never see A's session.
    again = identity.claim(cur, user_id=42, key=b_key, now=NOW)
    assert again["state"] == identity.IN_PROGRESS
    replay_a = identity.claim(cur, user_id=41, key=a_key, now=NOW)
    assert replay_a["state"] == identity.REPLAY
    assert replay_a["payload"]["checkout_url"] == "https://stripe/A"
    conn.close()


def test_36_the_hold_cannot_be_pointed_at_a_foreign_transaction():
    """Reservation theft.

    The hold's identity is ``seller_transaction_id``, taken from the
    ``lastrowid`` of the INSERT three statements above it, and the INSERT is
    ``ON CONFLICT(seller_transaction_id) DO NOTHING``. There is no request-
    supplied reservation id anywhere on the path, so there is nothing to
    re-point: a second hold against the same transaction is a no-op, and a hold
    against someone else's transaction cannot be addressed at all.
    """
    fn = _handler_fn()
    holds = [node for node in ast.walk(fn)
             if isinstance(node, ast.Constant) and isinstance(node.value, str)
             and "marketplace_inventory_reservations" in node.value
             and "INSERT" in node.value]
    assert len(holds) == 1
    sql = holds[0].value
    assert "ON CONFLICT(seller_transaction_id) DO NOTHING" in sql

    # `tx_id` is assigned from lastrowid, and assigned exactly once.
    tx_assigns = [node for node in ast.walk(fn)
                  if isinstance(node, ast.Assign) and len(node.targets) == 1
                  and isinstance(node.targets[0], ast.Name)
                  and node.targets[0].id == "tx_id"]
    assert len(tx_assigns) == 1
    assert "lastrowid" in (ast.dump(tx_assigns[0].value))
    assert tx_assigns[0].lineno < holds[0].lineno

    # The stock decrement is itself conditional on stock existing, so a hold
    # can never be created against inventory the listing does not have.
    decrements = [node.value for node in ast.walk(fn)
                  if isinstance(node, ast.Constant) and isinstance(node.value, str)
                  and "UPDATE marketplace_listings SET quantity=quantity-" in node.value]
    assert len(decrements) == 1
    assert "AND quantity>=?" in decrements[0]


def test_37_the_residual_amplification_is_bounded_by_the_servers_own_allowlists():
    """The audited finding this lane is left with, stated as a number.

    Two components of the identity are *influenced* by the request:
    ``payment_mode`` and ``fulfillment``. Neither is taken as given — each is
    collapsed onto a server-owned allowlist first — but a buyer who varies them
    deliberately does produce genuinely distinct attempt keys, and therefore
    distinct claims, distinct transaction rows and distinct stock holds.

    That is correct, not a defect: a buyer switching from collection to delivery
    is buying something else at another price, and replaying the first answer
    would charge them for the wrong thing. What matters is that the resulting
    amplification is *bounded and small*, so it is computed here rather than
    asserted in prose.

    ``payment_mode`` has exactly two reachable values. ``fulfillment`` has one
    value for nine of the eleven kinds — ``resolve_choice`` returns the
    listing's own kind and ignores the request entirely — and two for the two
    the seller declared undecided. So the ceiling is 2 x 2 = 4, and only on a
    listing whose seller offers both lanes.

    Of those, ``service_choice`` resolves to two stockless kinds, so it holds no
    inventory at all. The one stock-amplifying shape is
    ``shipping_or_pickup``: at most four holds of ``qty``, each still gated by
    ``quantity>=?`` so none can exceed real inventory, and each now carrying a
    durable ``expires_at`` so the sweeper returns the losers. Before this work
    the bound was *unbounded* and nothing was ever returned.

    Also recorded here because it is the reason the ceiling is 4 and not 6: the
    Apple Pay / hosted-session split does **not** widen it. ``payment_sheet``
    normalises to ``card``, so both rendering surfaces share one claim and a
    buyer cannot mint two payable surfaces by switching sheet. The visible
    consequence is a client-side wart, not a money defect — the second request
    replays the *first* surface's payload — and ``checkoutOffer`` has no
    production UI callers to hit it.
    """
    from services import marketplace_fulfillment as ff
    from services import marketplace_payment_pause as pause

    modes = {pause.normalize_marketplace_payment_mode(raw) for raw in
             ["card", "cash", "payment_sheet", "stripe", "", None, "CASH",
              "cash-on-delivery", "wire", "bitcoin", "../../etc/passwd"]}
    assert modes == {"card", "cash"}, f"payment mode space widened: {modes}"

    per_kind = {}
    for kind in ff.KINDS:
        resolved = set()
        for answer in ["shipping", "pickup", "remote", "in_person",
                       "service_remote", "service_in_person", "digital", "", "x"]:
            got, err = ff.resolve_choice(kind, answer)
            if not err:
                resolved.add(got)
        per_kind[kind] = resolved

    undecided = {k: v for k, v in per_kind.items() if len(v) > 1}
    assert set(undecided) == set(ff.UNDECIDED_KINDS)
    assert all(len(v) == 2 for v in undecided.values()), undecided
    # Nine of eleven kinds ignore the request outright.
    assert sum(1 for v in per_kind.values() if len(v) == 1) == len(ff.KINDS) - 2

    ceiling = max(len(v) for v in per_kind.values()) * len(modes)
    assert ceiling == 4, f"amplification ceiling moved to {ceiling}"

    # And the only stock-holding undecided kind is the shipping/pickup one.
    assert per_kind["service_choice"] <= ff.STOCKLESS_KINDS
    assert not (per_kind["shipping_or_pickup"] & ff.STOCKLESS_KINDS)

    # Each of those four is a distinct key, which is the point.
    keys = {identity.attempt_key(**{**ATTEMPT, "fulfillment": f, "payment_mode": m})
            for f in per_kind["shipping_or_pickup"] for m in modes}
    assert len(keys) == 4
