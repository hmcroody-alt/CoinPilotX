"""A failed pre-charge attempt must not permanently poison the buyer's retry.

The production incident
-----------------------
A buyer on pulsesoc.com tapped "Continue to secure payment" on a $6.25 cart nine
times over five hours and was told *"Payments are temporarily unavailable. No
card was charged. You can try again."* every time. Stripe was healthy
throughout. The nine attempts are ``seller_transactions`` 33-41, all
``checkout_failed``, all with a NULL session id and a NULL intent id.

Attempt 1 (tx 33) is the only one that behaved differently, and it is the first
cause::

    error         = "get"
    provider_error= {"type": "AttributeError", ...}

That is ``.get`` on a stripe-15 ``checkout.Session`` resource — a bug fixed on
main in 9b52560fb, which landed 1h36m *after* this buyer's attempt. But the
session Stripe created for attempt 1 is real and still open, so Stripe had
already bound the idempotency key ``marketplace-cart:19:s1-n1-t625-ce279c52``
to attempt 1's parameters before our process died.

Attempts 2-9 (tx 34-41) then every single one of them got::

    IdempotencyError: Keys for idempotent requests can only be used with the
    same parameters they were first used with.

and that is the *second* cause, which the fix to the first does nothing about.

The invariant that was violated
-------------------------------
Stripe binds a key to the parameters of the first request that uses it. So a
provider idempotency key and the parameters it is sent with **must change
together**. The cart lane broke that in one expression::

    idempotency_key=f"marketplace-cart:{buyer_id}:{idempotency_key or primary_tx}"

When the client supplies a key — which the web and native carts always do, as a
content hash of the cart — the ``or`` drops ``primary_tx`` from the key. But
``primary_tx`` stays in the *parameters*: ``success_url``, ``cancel_url``,
``metadata.seller_transaction_ids``, ``payment_intent_data.metadata`` and
``payment_intent_data.transfer_group`` are all derived from it, and a new
``seller_transactions`` row is inserted on every attempt.

So the key was constant across attempts and the parameters were not. The first
attempt to reach Stripe wins the key forever (24h), and every retry of that same
cart is refused — not because anything is wrong with the cart, the seller, the
stock or the card, but because our own key management guaranteed a collision.

A content-addressed key is the right idea and is kept. What is wrong is pairing
a content-addressed key with row-addressed parameters.

Why these tests use a stub that can *refuse*
--------------------------------------------
Every pre-existing cart test replaces ``bot.stripe.checkout`` with something
that always succeeds, which is why this was invisible: a provider that never
enforces idempotency cannot fail an idempotency contract. The stub here keeps a
``key -> parameters`` ledger and raises the real
``stripe.error.IdempotencyError`` when a key comes back with different
parameters, exactly as Stripe does. No key, no account, no card, no network, no
charge.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# `import bot` connects and runs init_db() at module scope, so the env has to be
# bound before the import rather than in a fixture.
os.environ["DATABASE_URL"] = "sqlite:///" + tempfile.mkstemp(suffix=".db")[1]
os.environ["COINPILOTX_INIT_DB_ON_IMPORT"] = "1"
os.environ.setdefault("FLASK_SECRET_KEY", "checkout-idempotency-tests")
os.environ.setdefault("MARKETPLACE_CARD_PAYMENTS_ENABLED", "1")

stripe = pytest.importorskip("stripe")

import bot  # noqa: E402
from services import marketplace_checkout_identity as checkout_identity  # noqa: E402

HTTPS = {"X-Forwarded-Proto": "https"}
CART_API = "/api/pulse/marketplace/cart"
CHECKOUT_API = CART_API + "/checkout"

#: The real buyer's key, so a reader can line these tests up against the rows.
PRODUCTION_CLIENT_KEY = "s1-n1-t625-ce279c52"


# --------------------------------------------------------------------------
# A Stripe stub that honours the one contract the real one enforces
# --------------------------------------------------------------------------

#: Parameters that do not participate in Stripe's idempotency comparison.
_NOT_A_PARAMETER = {"idempotency_key"}


def _canonical(kwargs: dict) -> str:
    """The request body as Stripe would compare it, minus the key itself."""
    body = {k: v for k, v in kwargs.items() if k not in _NOT_A_PARAMETER}
    return json.dumps(body, sort_keys=True, default=str)


class IdempotentStripeStub:
    """Records every call and enforces Stripe's key/parameter binding.

    ``fail_first`` models the production first cause without reproducing the
    specific ``AttributeError``: the session *is* created and the key *is*
    bound, and only then does the attempt die. Any post-create failure — the
    ``.get`` bug, a dropped connection, a worker restart, a DB error on the
    ``UPDATE`` that stores the session id — leaves exactly this state, which is
    why the retry defect is general and not a residue of one fixed bug.
    """

    def __init__(self, *, fail_first_after_create: bool = False,
                 refuse_expire: bool = False):
        self.bound: dict[str, str] = {}
        self.responses: dict[str, dict] = {}
        self.calls: list[dict] = []
        self.created: list[dict] = []
        #: Session ids the lane asked Stripe to expire.
        self.expired: list[str] = []
        self.fail_first_after_create = fail_first_after_create
        self.refuse_expire = refuse_expire
        self._creates = 0

    def create(self, **kwargs):
        key = kwargs.get("idempotency_key")
        params = _canonical(kwargs)
        self.calls.append({"idempotency_key": key, "params": params, "kwargs": kwargs})

        if key in self.bound:
            if self.bound[key] != params:
                raise stripe.error.IdempotencyError(
                    "Keys for idempotent requests can only be used with the same "
                    f"parameters they were first used with. Try using a key other "
                    f"than '{key}' if you meant to execute a different request."
                )
            # Same key, same parameters: Stripe replays its original answer
            # rather than creating a second session. This is the behaviour the
            # key exists for and it must survive the fix.
            return self.responses[key]

        self._creates += 1
        session = {"id": f"cs_test_stub_{self._creates}",
                   "url": f"https://checkout.stripe.test/c/pay/stub-{self._creates}"}
        self.bound[key] = params
        self.responses[key] = session
        self.created.append(session)

        if self.fail_first_after_create and self._creates == 1:
            # Stripe has committed. We have not. Precisely tx 33.
            raise RuntimeError("get")
        return session

    def expire(self, session_id):
        """Stripe's own `Session.expire`, which the failure path calls.

        Without this the stub raises ``AttributeError: expire`` into the
        failure handler, which swallows it and logs ``STRIPE_SESSION_ORPHANED``
        — so the expiry would look untested *and* appear to work.
        """
        self.expired.append(session_id)
        if self.refuse_expire:
            raise stripe.error.InvalidRequestError(
                "No such checkout.session", param="session")
        return {"id": session_id, "status": "expired"}

    # -- reading the ledger -------------------------------------------------

    @property
    def keys_used(self) -> list[str]:
        return [call["idempotency_key"] for call in self.calls]

    @property
    def distinct_keys(self) -> set[str]:
        return set(self.keys_used)


@contextlib.contextmanager
def card_rail(stub: IdempotentStripeStub):
    """Open the card rail for a seller and route Session.create at ``stub``.

    `marketplace_card_capability.evaluate` answers STRIPE_UNAVAILABLE with no
    secret key configured, and the lane returns 503 from that check long before
    it reaches a provider — so without this every assertion here would silently
    be an assertion about a missing API key.
    """
    from services import marketplace_card_capability

    class _Sessions:
        @staticmethod
        def create(**kwargs):
            return stub.create(**kwargs)

        @staticmethod
        def expire(session_id):
            return stub.expire(session_id)

    class _Checkout:
        Session = _Sessions

    real_evaluate = marketplace_card_capability.evaluate
    real_key = bot.STRIPE_SECRET_KEY
    real_checkout = bot.stripe.checkout
    marketplace_card_capability.evaluate = lambda *a, **k: {
        "card_payments_available": True, "reason_code": "", "message": "", "badge": ""}
    bot.STRIPE_SECRET_KEY = "sk_test_stub_not_a_real_key"
    bot.stripe.checkout = _Checkout
    try:
        yield stub
    finally:
        marketplace_card_capability.evaluate = real_evaluate
        bot.STRIPE_SECRET_KEY = real_key
        bot.stripe.checkout = real_checkout


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def buyer():
    """A buyer with a digital listing to buy from one approved seller.

    Digital because it is the one fulfilment kind that asks the buyer nothing,
    so a failing assertion below is failing about idempotency rather than about
    an address the fixture forgot.
    """
    with bot.webhook_app.app_context():
        bot.init_db()
    conn = bot.db()
    cur = conn.cursor()
    cur.execute("INSERT INTO users (username, email, password_hash) VALUES (?,?,?)",
                ("idembuyer", "idembuyer@example.com", "x"))
    buyer_id = cur.lastrowid
    cur.execute("INSERT INTO users (username, email, password_hash) VALUES (?,?,?)",
                ("idemseller", "idemseller@example.com", "x"))
    seller_id = cur.lastrowid
    cur.execute(
        "INSERT INTO marketplace_sellers (user_id, status, business_name, display_name) "
        "VALUES (?,?,?,?)",
        (seller_id, "approved", "Idem Store", "Idem Store"))
    cur.execute(
        """INSERT INTO marketplace_listings
           (seller_user_id, title, description, category, price_label, currency,
            quantity, status, approval_status, delivery_type)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (seller_id, "Horse eye colorful ring", "A ring.", "Education",
         "$6.25", "USD", 500, "active", "approved", "digital"))
    listing_id = cur.lastrowid
    # A second, *physical* listing. The digital one above is in
    # `STOCKLESS_KINDS`, so it neither decrements a quantity nor takes a hold —
    # which means a stock assertion against it passes no matter what the lane
    # does. §15 is about holds, so it needs a listing that actually has some.
    #
    # Written the way a real physical listing is written, which is not obvious:
    # `delivery_type='shipping'` alone produces a *digital* line. `product_type`
    # defaults to `'digital'` in the DDL, `_fulfillment_kind` reads
    # `listing_type or product_type` first, and `resolve_kind` returns early on
    # `digital` without ever consulting `delivery_type`. Even past that,
    # `delivery_lane` deliberately ignores the delivery column for any row that
    # declared a type — its docstring explains why — and reads
    # `listing_metadata.delivery_options` instead. So the lane is declared in
    # both of the two places that are actually read.
    cur.execute(
        """INSERT INTO marketplace_listings
           (seller_user_id, title, description, category, price_label, currency,
            quantity, status, approval_status, delivery_type, product_type,
            listing_type, listing_metadata_json)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (seller_id, "Shipped ring", "A ring in a box.", "Education",
         "$6.25", "USD", 500, "active", "approved", "physical", "physical",
         "physical", json.dumps({"delivery_options": "shipping"})))
    physical_listing_id = cur.lastrowid
    conn.commit()

    client = bot.webhook_app.test_client()
    with client.session_transaction() as session:
        session["account_user_id"] = buyer_id
    return client, listing_id, seller_id, buyer_id, physical_listing_id


#: A shipping address `marketplace_fulfillment.validate_details` accepts.
#:
#: More than the four fields `buyer_form("shipping")` marks required, because
#: for a US address the validator also demands `address_region` — it is
#: country-conditional, and `required_fields` does not say so. Supplied in full
#: so that a refusal in these tests is never the address form's doing; the
#: incident's own address was US, so US is the country to test under.
SHIPPING_DETAILS = {
    "contact_name": "Test Buyer",
    "address_line1": "1 Test Street",
    "address_city": "Testville",
    "address_region": "CA",
    "address_postal_code": "94016",
    "address_country": "US",
}


def _empty_cart(client):
    for entry in client.get(CART_API, headers=HTTPS).get_json()["lines"]:
        client.delete(f"{CART_API}/{entry['line_id']}", headers=HTTPS)


def _seed_one_line(client, listing_id, qty=1):
    _empty_cart(client)
    client.post(CART_API, json={"listing_id": listing_id, "qty": qty}, headers=HTTPS)


def _checkout(client, seller_id, *, client_key=PRODUCTION_CLIENT_KEY, details=None):
    return client.post(CHECKOUT_API, json={
        "seller_user_id": seller_id,
        "fulfillment_details": dict(details or {}),
        "idempotency_key": client_key,
    }, headers=HTTPS)


def _forget_claims(buyer_id):
    """Drop this buyer's claim/replay rows, so a test starts from no attempt.

    The module-scoped fixture means the table carries over between tests, and a
    stale claim would make a test about a first attempt silently be a test about
    a replay.
    """
    conn = bot.db()
    cur = conn.cursor()
    # The claim table is created lazily by the cart lane's own `_ensure_schema`,
    # which has not necessarily run when the first test calls this. Bootstrapping
    # it through the identity module rather than a local CREATE is the point of
    # that module owning its DDL: a caller that needs the table can have it
    # without having served a cart request first.
    checkout_identity.ensure_schema(cur)
    cur.execute("DELETE FROM marketplace_cart_checkout_keys WHERE user_id=?", (buyer_id,))
    conn.commit()


# --------------------------------------------------------------------------
# The production sequence
# --------------------------------------------------------------------------

def test_a_retry_after_a_post_create_failure_is_not_refused_by_the_provider(buyer):
    """The incident, reproduced and then required not to happen.

    Attempt 1 reaches Stripe, Stripe creates and binds, our process dies before
    recording it. Attempt 2 is the buyer tapping the button again with an
    unchanged cart — which is the overwhelmingly common thing to do and the one
    thing that was guaranteed to fail.

    Pre-fix this raises ``IdempotencyError`` inside the route and the buyer gets
    a 400 ``PAYMENT_CONFIGURATION_ERROR`` reading "Payments are temporarily
    unavailable", forever, for 24 hours, for this cart.
    """
    client, listing_id, seller_id, buyer_id, physical_listing_id = buyer
    _forget_claims(buyer_id)
    _seed_one_line(client, listing_id)

    stub = IdempotentStripeStub(fail_first_after_create=True)
    with card_rail(stub):
        first = _checkout(client, seller_id)
        assert first.status_code >= 400, "the fixture is meant to fail attempt 1"

        second = _checkout(client, seller_id)

    body = second.get_json() or {}
    assert body.get("code") != "PAYMENT_CONFIGURATION_ERROR", (
        "the retry was refused by Stripe's idempotency contract, not by anything "
        f"about the cart: {body.get('message')!r}")
    assert "temporarily unavailable" not in str(body.get("message") or "").lower()
    assert second.status_code == 200, second.get_data(as_text=True)
    assert body.get("checkout_url"), "the retry produced no payable session"


def test_the_provider_key_changes_whenever_the_provider_parameters_change(buyer):
    """The invariant, asserted directly rather than through a symptom.

    This is the generalisation of the bug: whatever the lane sends to Stripe, a
    changed parameter must arrive under a changed key. Asserted by reading the
    stub's ledger, so it holds for every parameter the route derives from the
    transaction row — not only the ones a test thought to name.
    """
    client, listing_id, seller_id, buyer_id, physical_listing_id = buyer
    _forget_claims(buyer_id)
    _seed_one_line(client, listing_id)

    stub = IdempotentStripeStub(fail_first_after_create=True)
    with card_rail(stub):
        _checkout(client, seller_id)
        _forget_claims(buyer_id)
        _checkout(client, seller_id)

    assert len(stub.calls) == 2, f"expected two provider calls, got {len(stub.calls)}"
    first, second = stub.calls
    assert first["params"] != second["params"], (
        "the fixture no longer varies the parameters, so this test cannot fail")
    assert first["idempotency_key"] != second["idempotency_key"], (
        "the parameters changed but the key did not — this is the defect, and "
        "Stripe answers it with a permanent 400 for that key")


def test_the_key_carries_the_transaction_the_parameters_are_built_from(buyer):
    """Not just "different" — different *because* it names the row.

    A key that varied randomly would satisfy the test above while destroying
    the double-tap guarantee. The key has to co-vary with the parameters for a
    reason: it must name the same attempt they describe.
    """
    client, listing_id, seller_id, buyer_id, physical_listing_id = buyer
    _forget_claims(buyer_id)
    _seed_one_line(client, listing_id)

    stub = IdempotentStripeStub()
    with card_rail(stub):
        answer = _checkout(client, seller_id)

    assert answer.status_code == 200, answer.get_data(as_text=True)
    primary_tx = answer.get_json()["transaction_ids"][0]
    key = stub.calls[0]["idempotency_key"]
    # Positional, not `str(primary_tx) in key`. A substring check passes
    # coincidentally whenever the transaction id happens to appear anywhere else
    # in the key — and it does: the buyer id, the line count and the subtotal are
    # all in there, so `"1" in "marketplace-cart:1:s1-n1-t625-..."` is true of the
    # *unfixed* key. The assertion has to be about where the id is, not whether
    # its digits occur.
    assert key.endswith(f":{primary_tx}"), (
        f"key {key!r} does not end by naming the transaction its success_url, "
        f"cancel_url and metadata are all derived from (tx {primary_tx})")


def test_an_identical_retried_call_still_replays_rather_than_creating_twice(buyer):
    """The key must keep doing the job it is actually for.

    Co-varying the key with the parameters must not degrade into "a fresh key
    every time", which would make the provider key decorative. When the very
    same request is sent again — the SDK's own transport retry after a dropped
    response — Stripe must still return the original session.
    """
    stub = IdempotentStripeStub()
    params = {"mode": "payment", "success_url": "https://x/s?transaction_id=1",
              "idempotency_key": "marketplace-cart:19:probe:1"}

    first = stub.create(**params)
    second = stub.create(**params)

    assert first is second, "an identical replay minted a second session"
    assert len(stub.created) == 1


# --------------------------------------------------------------------------
# §23 — a double tap is one logical checkout
# --------------------------------------------------------------------------

def test_a_second_attempt_while_the_first_is_unfinished_does_not_build_a_second_session(buyer):
    """One logical checkout, whatever the buyer's thumb does.

    The provider key used to supply this accidentally: a second tap presented
    the same key with different parameters and Stripe refused it, which looked
    like protection but was the bug wearing a useful hat — it also refused every
    legitimate retry, and it reported the refusal to the buyer as an outage.

    So the guarantee has to come from somewhere that can tell the two apart. It
    comes from a claim on ``marketplace_cart_checkout_keys``, taken before any
    transaction row or stock decrement exists, whose uniqueness is a database
    invariant rather than a belief held by a process.
    """
    client, listing_id, seller_id, buyer_id, physical_listing_id = buyer
    _forget_claims(buyer_id)
    _seed_one_line(client, listing_id)

    stub = IdempotentStripeStub()
    with card_rail(stub):
        first = _checkout(client, seller_id)
        second = _checkout(client, seller_id)

    assert first.status_code == 200, first.get_data(as_text=True)
    assert len(stub.created) == 1, (
        f"a double tap built {len(stub.created)} payable sessions")

    body = second.get_json() or {}
    # Either the stored answer replayed, or the attempt is reported as already
    # in flight. Both are "one logical checkout"; neither is a second session
    # and neither is a failure shown to the buyer.
    assert second.status_code == 200 or body.get("code") == checkout_identity.IN_PROGRESS_CODE, (
        f"a double tap was answered with {second.status_code} {body.get('code')!r}")
    if second.status_code == 200:
        assert body.get("checkout_url") == first.get_json()["checkout_url"], (
            "the second tap was handed a different session than the first")


def test_a_double_tap_leaves_one_transaction_and_one_hold(buyer):
    """§15: the stock consequence of a double tap, not just the Stripe one.

    The decrement and the reservation sit on the same path as the session, so a
    guarantee that stops at "one session" is not the guarantee the inventory
    needs.

    Deliberately the *physical* listing. `digital` is in
    ``marketplace_fulfillment.STOCKLESS_KINDS``, so the cart lane never
    decrements it and never writes a reservation for it — against that listing
    both assertions below hold no matter what the lane does, which makes the
    test green and worthless. A stock test has to be run against stock.
    """
    client, listing_id, seller_id, buyer_id, physical_listing_id = buyer
    _forget_claims(buyer_id)
    _seed_one_line(client, physical_listing_id)

    before = _listing_quantity(physical_listing_id)
    stub = IdempotentStripeStub()
    with card_rail(stub):
        first = _checkout(client, seller_id, details=SHIPPING_DETAILS)
        _checkout(client, seller_id, details=SHIPPING_DETAILS)

    assert first.status_code == 200, first.get_data(as_text=True)
    # Proves the lane really does hold stock for this listing, so the
    # equality below is a measurement and not a tautology.
    assert before > 0 and _listing_quantity(physical_listing_id) < before, (
        "the physical listing was not decremented at all — this lane is not "
        "holding stock, so the double-tap assertion would pass vacuously")
    assert _listing_quantity(physical_listing_id) == before - 1, (
        "a double tap decremented the listing more than once")
    assert _held_reservations(physical_listing_id) == 1, (
        f"a double tap left {_held_reservations(physical_listing_id)} holds "
        f"on one listing")


def _listing_quantity(listing_id: int) -> int:
    cur = bot.db().cursor()
    cur.execute("SELECT quantity FROM marketplace_listings WHERE id=?", (listing_id,))
    return int(dict(cur.fetchone() or {}).get("quantity") or 0)


def _held_reservations(listing_id: int) -> int:
    cur = bot.db().cursor()
    cur.execute("SELECT COUNT(*) AS n FROM marketplace_inventory_reservations "
                "WHERE listing_id=? AND status='held'", (listing_id,))
    return int(dict(cur.fetchone() or {}).get("n") or 0)


# --------------------------------------------------------------------------
# §9 — a failed attempt releases its claim
# --------------------------------------------------------------------------

def test_a_failed_attempt_does_not_leave_a_claim_that_blocks_the_retry(buyer):
    """The §9 state machine, stated as a database fact.

    A claim that survives a failure is the same lockout as a burned provider
    key, just one layer in. The failure path has to release it — and release it
    only when it produced no answer, so a completed attempt is never deleted by
    a late failure.
    """
    client, listing_id, seller_id, buyer_id, physical_listing_id = buyer
    _forget_claims(buyer_id)
    _seed_one_line(client, listing_id)

    stub = IdempotentStripeStub(fail_first_after_create=True)
    with card_rail(stub):
        failed = _checkout(client, seller_id)
    assert failed.status_code >= 400

    cur = bot.db().cursor()
    cur.execute(
        "SELECT idempotency_key, response_json FROM marketplace_cart_checkout_keys "
        "WHERE user_id=?", (buyer_id,))
    blocking = [dict(r) for r in cur.fetchall()
                if not (dict(r).get("response_json") or "")]
    assert blocking == [], (
        f"a failed attempt left {len(blocking)} unanswered claim(s) behind, which "
        "is the retry lockout one layer below Stripe")


def test_a_successful_attempt_keeps_its_claim_so_the_answer_can_replay(buyer):
    """The other half: success must *not* release, or replay stops working."""
    client, listing_id, seller_id, buyer_id, physical_listing_id = buyer
    _forget_claims(buyer_id)
    _seed_one_line(client, listing_id)

    stub = IdempotentStripeStub()
    with card_rail(stub):
        answer = _checkout(client, seller_id)
    assert answer.status_code == 200, answer.get_data(as_text=True)

    cur = bot.db().cursor()
    cur.execute(
        "SELECT response_json FROM marketplace_cart_checkout_keys WHERE user_id=?",
        (buyer_id,))
    stored = [dict(r).get("response_json") for r in cur.fetchall()]
    assert any(stored), "a successful checkout stored no replayable answer"


# --------------------------------------------------------------------------
# The buy-now lane — the same defect, one route over
# --------------------------------------------------------------------------
#
# Buy Now was never the lane that failed in production, and that is close to an
# accident: it carried the identical expression,
#
#     idempotency_key=f"marketplace-buy-now:{buyer}:{idempotency_key or tx_id}"
#
# against `success_url=.../success?transaction_id={tx_id}` and
# `transfer_group=f"marketplace_order:{tx_id}"`, with a *new* transaction row
# per attempt — so a retry of an unchanged Buy Now burned its key exactly as the
# cart's did. It is only untouched by the incident because this buyer went
# through the cart.
#
# It was also strictly worse off than the cart on the other half: its replay
# check was a bare SELECT that fell through on a miss, so it had no claim at all
# and the provider collision was the only thing standing between a double tap
# and two payable sessions.

BUY_NOW_API = "/api/pulse/payments/checkout"


def _buy_now(client, listing_id, *, client_key, details=None, mode="card"):
    return client.post(BUY_NOW_API, json={
        "item_type": "marketplace_product",
        "item_id": listing_id,
        "quantity": 1,
        "payment_mode": mode,
        "fulfillment_details": dict(details or {}),
        "idempotency_key": client_key,
    }, headers=HTTPS)


def test_buy_now_retry_after_a_post_create_failure_is_not_refused(buyer):
    """§9 for the second lane: a failed attempt must not burn the key.

    The same shape as the cart test at the top of this file. Attempt 1 gets a
    session out of Stripe and dies before recording it; attempt 2 is the buyer
    tapping Buy Now again on an unchanged product.
    """
    client, listing_id, seller_id, buyer_id, physical_listing_id = buyer
    _forget_claims(buyer_id)

    stub = IdempotentStripeStub(fail_first_after_create=True)
    with card_rail(stub):
        first = _buy_now(client, listing_id, client_key="buynow-retry-1")
        assert first.status_code >= 400, "the fixture is meant to fail attempt 1"

        second = _buy_now(client, listing_id, client_key="buynow-retry-1")

    body = second.get_json() or {}
    assert body.get("code") != "PAYMENT_CONFIGURATION_ERROR" and \
        body.get("error_code") != "PAYMENT_CONFIGURATION_ERROR", (
            "the Buy Now retry was refused by Stripe's idempotency contract: "
            f"{body.get('message')!r}")
    assert "temporarily unavailable" not in str(body.get("message") or "").lower()
    assert second.status_code == 200, second.get_data(as_text=True)
    assert body.get("checkout_url"), "the Buy Now retry produced no payable session"


def test_buy_now_provider_key_names_the_transaction_it_was_sent_with(buyer):
    """The invariant, on this lane, asserted positionally.

    `transaction_id` comes back in the response, and the key must end by naming
    it — because `success_url`, `cancel_url`, `metadata.seller_transaction_ids`
    and `transfer_group` are all derived from that same row.
    """
    client, listing_id, seller_id, buyer_id, physical_listing_id = buyer
    _forget_claims(buyer_id)

    stub = IdempotentStripeStub()
    with card_rail(stub):
        answer = _buy_now(client, listing_id, client_key="buynow-key-1")

    assert answer.status_code == 200, answer.get_data(as_text=True)
    tx_id = answer.get_json()["transaction_id"]
    key = stub.calls[0]["idempotency_key"]
    assert key.endswith(f":{tx_id}"), (
        f"key {key!r} does not end by naming transaction {tx_id}, which its "
        f"success_url, cancel_url and transfer_group are all built from")


def test_buy_now_double_tap_builds_one_session_and_takes_one_unit(buyer):
    """§23 on the lane that had no guard whatsoever.

    Pre-fix the two taps were kept apart only by the provider refusing the
    second one — which also means that *removing* the collision without adding
    the claim would turn a double tap into two payable sessions and two stock
    decrements. Both halves of the change are load-bearing and this is the test
    that says so.

    Against the physical listing, because `digital` is in
    ``STOCKLESS_KINDS`` and would make the stock assertions vacuous.
    """
    client, listing_id, seller_id, buyer_id, physical_listing_id = buyer
    _forget_claims(buyer_id)

    before = _listing_quantity(physical_listing_id)
    held_before = _held_reservations(physical_listing_id)
    stub = IdempotentStripeStub()
    with card_rail(stub):
        first = _buy_now(client, physical_listing_id, client_key="buynow-double-1",
                         details=SHIPPING_DETAILS)
        second = _buy_now(client, physical_listing_id, client_key="buynow-double-1",
                          details=SHIPPING_DETAILS)

    assert first.status_code == 200, first.get_data(as_text=True)
    after = _listing_quantity(physical_listing_id)
    # The lane really does hold stock for this listing, so the equality below is
    # a measurement rather than a tautology.
    assert before > 0 and after < before, (
        "Buy Now did not decrement this listing at all — the assertions below "
        "would pass vacuously")
    assert after == before - 1, "a Buy Now double tap decremented the shelf twice"
    assert _held_reservations(physical_listing_id) == held_before + 1, (
        "a Buy Now double tap left more than one hold")
    assert len(stub.created) == 1, (
        f"a Buy Now double tap built {len(stub.created)} payable sessions")

    body = second.get_json() or {}
    assert second.status_code == 200 or \
        checkout_identity.IN_PROGRESS_CODE in {body.get("code"), body.get("error_code")}, (
            f"a Buy Now double tap was answered with {second.status_code} {body!r}")


def test_buy_now_failed_attempt_leaves_no_blocking_claim(buyer):
    """§9 as a database fact on this lane too.

    Buy Now returns through ``api_error`` from seventeen places between the
    claim and the provider call, so the release is registered as a response hook
    rather than written out at each of them. If that hook ever stops firing,
    this is the test that notices — a surviving blank claim is the lockout, just
    one layer below Stripe.
    """
    client, listing_id, seller_id, buyer_id, physical_listing_id = buyer
    _forget_claims(buyer_id)

    stub = IdempotentStripeStub(fail_first_after_create=True)
    with card_rail(stub):
        failed = _buy_now(client, listing_id, client_key="buynow-release-1")
    assert failed.status_code >= 400

    cur = bot.db().cursor()
    cur.execute(
        "SELECT idempotency_key, response_json FROM marketplace_cart_checkout_keys "
        "WHERE user_id=?", (buyer_id,))
    blocking = [dict(r) for r in cur.fetchall()
                if not (dict(r).get("response_json") or "")]
    assert blocking == [], (
        f"a failed Buy Now left {len(blocking)} unanswered claim(s) behind: "
        f"{[r.get('idempotency_key') for r in blocking]}")


def test_buy_now_validation_refusal_does_not_lock_the_corrected_retry(buyer):
    """The reason the release covers *every* failing exit, not just the provider's.

    A buyer who submits Buy Now with a missing shipping address is refused
    before any Stripe call. The claim was already taken by then. If it survived,
    the buyer's *corrected* submission — same product, so the client sends the
    same content-hashed key — would come back ``CHECKOUT_IN_PROGRESS`` for the
    full TTL. That is the original lockout with a different error code on it.
    """
    client, listing_id, seller_id, buyer_id, physical_listing_id = buyer
    _forget_claims(buyer_id)

    stub = IdempotentStripeStub()
    with card_rail(stub):
        refused = _buy_now(client, physical_listing_id, client_key="buynow-fix-1",
                           details={})
        assert refused.status_code >= 400, (
            "the fixture no longer triggers a validation refusal, so this test "
            f"cannot fail: {refused.get_data(as_text=True)}")

        corrected = _buy_now(client, physical_listing_id, client_key="buynow-fix-1",
                             details=SHIPPING_DETAILS)

    body = corrected.get_json() or {}
    assert checkout_identity.IN_PROGRESS_CODE not in {body.get("code"), body.get("error_code")}, (
        "the corrected submission was refused as already-in-progress — the "
        "validation refusal did not release its claim")
    assert corrected.status_code == 200, corrected.get_data(as_text=True)


# --------------------------------------------------------------------------
# §16 / §24 — a session we cannot deliver does not stay payable
# --------------------------------------------------------------------------

@contextlib.contextmanager
def provider_read_fails_on(field: str):
    """Fail the read of one field of an already-created Stripe resource.

    This is the production failure reproduced at its true position, which
    ``fail_first_after_create`` cannot reach. That flag raises from inside
    ``create``, so the lane never learns the session id and has nothing to
    expire — faithful to "Stripe committed and we did not", but not to the part
    where we *could* have cleaned up.

    Reading a second field off the resource is the first thing that happens
    after the id is in hand, and it is a second access to the same stripe-15
    generated object that raised ``AttributeError: get`` in the incident. So a
    failure here is both realistic and exactly the window where the orphan is
    still recoverable.

    Patched in three places because the lanes bind the helper differently: the
    cart and offers lanes import it at module scope, buy-now imports it inside
    the function on every call. Patching only the defining module would silently
    exempt the two that bound it early — and the test would still pass, by
    reaching the no-orphan path instead of the orphan-expiry path it is named
    after.
    """
    from services import marketplace_cart_routes
    from services import marketplace_offers_routes
    from services import marketplace_payment_errors

    real = marketplace_payment_errors.stripe_response_value

    def patched(resource, key, *args, **kwargs):
        if key == field:
            raise RuntimeError("get")
        return real(resource, key, *args, **kwargs)

    rebound = [(marketplace_payment_errors, marketplace_payment_errors.stripe_response_value),
               (marketplace_cart_routes, marketplace_cart_routes.stripe_response_value),
               (marketplace_offers_routes, marketplace_offers_routes.stripe_response_value)]
    for module, _ in rebound:
        module.stripe_response_value = patched
    try:
        yield
    finally:
        for module, original in rebound:
            module.stripe_response_value = original


def test_cart_expires_the_session_it_created_but_could_not_deliver(buyer):
    """The $6.25 session from the incident is still open at Stripe today.

    Nothing ever returned or logged its URL, so it was unreachable rather than
    dangerous — but "unreachable" is a property of which code paths exist, not
    a guarantee, and the transaction it names is ``checkout_failed`` with its
    stock already back on the shelf. If anyone had opened that URL and paid, the
    result would be a paid session against a failed order holding no inventory.

    Cancelling is a write that can only *prevent* a charge, so the failure path
    does it without an owner gate.
    """
    client, listing_id, seller_id, buyer_id, physical_listing_id = buyer
    _forget_claims(buyer_id)
    _seed_one_line(client, listing_id)

    stub = IdempotentStripeStub()
    with card_rail(stub), provider_read_fails_on("url"):
        failed = _checkout(client, seller_id)

    assert failed.status_code >= 400, "the injected failure did not fail the attempt"
    assert len(stub.created) == 1, "the fixture did not get a session created"
    orphan = stub.created[0]["id"]
    assert stub.expired == [orphan], (
        f"the lane created session {orphan} and then left it payable; "
        f"expired={stub.expired}")


def test_buy_now_expires_the_session_it_created_but_could_not_deliver(buyer):
    """The same guarantee on the second lane."""
    client, listing_id, seller_id, buyer_id, physical_listing_id = buyer
    _forget_claims(buyer_id)

    stub = IdempotentStripeStub()
    with card_rail(stub), provider_read_fails_on("url"):
        failed = _buy_now(client, listing_id, client_key="buynow-orphan-1")

    assert failed.status_code >= 400, "the injected failure did not fail the attempt"
    assert len(stub.created) == 1, "the fixture did not get a session created"
    orphan = stub.created[0]["id"]
    assert stub.expired == [orphan], (
        f"Buy Now created session {orphan} and then left it payable; "
        f"expired={stub.expired}")


def test_a_refused_expiry_still_reports_the_original_failure_to_the_buyer(buyer):
    """Cleanup must never become the diagnosis.

    If the expire call itself fails — Stripe unreachable, session already gone —
    the buyer must still get the error for the thing that actually broke, not a
    500 about the cleanup. The orphan is logged for hand reconciliation and the
    original classification survives.
    """
    client, listing_id, seller_id, buyer_id, physical_listing_id = buyer
    _forget_claims(buyer_id)
    _seed_one_line(client, listing_id)

    stub = IdempotentStripeStub(refuse_expire=True)
    with card_rail(stub), provider_read_fails_on("url"):
        failed = _checkout(client, seller_id)

    assert stub.expired, "the lane did not even attempt the expiry"
    body = failed.get_json() or {}
    assert failed.status_code >= 400
    assert body.get("message"), "the buyer got no message at all"
    # Still the generic post-create failure, not an InvalidRequestError about a
    # checkout session the buyer has no idea exists.
    assert "no such checkout" not in str(body.get("message") or "").lower()


# --------------------------------------------------------------------------
# The accepted-offer lane — the same defect, mirror-imaged
# --------------------------------------------------------------------------
#
# This lane looked like the one that had it right. It derives its provider key
# from `checkout_identity.attempt_key(...)`, which is content-addressed over
# buyer/offer/seller/price/qty/fulfilment — so the key is *stable* across
# attempts, and a comment on the call said as much: "A retry presents the same
# key, so Stripe returns the original intent instead of minting a second payable
# one."
#
# That is the cart's bug with the two halves swapped. Stripe compares
# parameters, and this lane's parameters are addressed by row exactly like the
# other two: `success_url=.../success?transaction_id={tx_id}`, `cancel_url`,
# `transfer_group=f"marketplace_order:{tx_id}"` and
# `metadata.seller_transaction_id` all name a `tx_id` that is a fresh
# `lastrowid` on every attempt. Stable key + varying parameters is the same
# violation as varying key + ... no: it is the same violation, period. A retry
# did not get the original session. It got a permanent 400.
#
# Worse, this lane's failure path *releases* the claim and says in its own
# comment that doing so is "safe only because the provider key is derived from
# the attempt". So the release was the trigger: it let a second attempt through
# to burn its way into the 24-hour refusal, which is the lockout the release
# exists to prevent.
#
# It never fired in production because the lane has no UI callers yet. That is
# luck, not design, and it is why these cases are written against it now rather
# than after the first accepted offer is bought.

OFFERS_API = "/api/pulse/marketplace/offers"


def _accepted_offer(buyer_id, seller_id, listing_id, *, amount_minor=625, qty=1):
    """An offer in exactly the state the checkout route requires.

    Written straight to the table rather than driven through propose/accept,
    because this file is about what happens between the claim and the provider —
    routing a fixture through two more HTTP calls would add two more ways for
    these tests to go red for reasons that are not idempotency.

    `accepted_until` is set into the future on purpose: ``_effective_state``
    reads an `accepted` offer past its window as `expired`, and the checkout
    route refuses anything that is not `accepted` with a 409 before it reaches
    the claim. A fixture that forgot this would test the state guard.
    """
    from services import marketplace_offers_routes

    conn = bot.db()
    cur = conn.cursor()
    marketplace_offers_routes._ensure_schema(cur)
    now = marketplace_offers_routes._now()
    until = (marketplace_offers_routes._now_dt()
             + __import__("datetime").timedelta(hours=48)).isoformat()
    cur.execute(
        """INSERT INTO marketplace_offers
           (listing_id, buyer_user_id, seller_user_id, direction, amount_minor,
            list_price_minor, currency, qty, state, accepted_until,
            created_at, updated_at)
           VALUES (?,?,?,'buyer_to_seller',?,?, 'USD',?, 'accepted',?,?,?)""",
        (listing_id, buyer_id, seller_id, amount_minor, amount_minor, qty,
         until, now, now))
    offer_id = int(cur.lastrowid)
    conn.commit()
    return offer_id


def _offer_checkout(client, offer_id, *, details=None):
    """No `idempotency_key`: this lane derives its own, which is the point.

    The cart and buy-now lanes take a client token. This one does not — every
    component of its attempt identity is server-side truth, so a client cannot
    vary it and a double tap cannot look like two purchases by accident.
    """
    return client.post(f"{OFFERS_API}/{offer_id}/checkout", json={
        "payment_mode": "card",
        "fulfillment_details": dict(details or {}),
    }, headers=HTTPS)


def test_offer_retry_after_a_post_create_failure_is_not_refused(buyer):
    """§9 on the third lane: the release must not hand the buyer a burned key.

    Attempt 1 gets a session out of Stripe and dies before recording it, the
    failure path releases the claim — and then attempt 2 has to actually work.
    Pre-fix this is the one case the lane was structurally guaranteed to get
    wrong, because the release and the stable key were designed against each
    other.
    """
    client, listing_id, seller_id, buyer_id, physical_listing_id = buyer
    _forget_claims(buyer_id)
    offer_id = _accepted_offer(buyer_id, seller_id, listing_id)

    stub = IdempotentStripeStub(fail_first_after_create=True)
    with card_rail(stub):
        first = _offer_checkout(client, offer_id)
        assert first.status_code >= 400, (
            "the fixture is meant to fail attempt 1: "
            f"{first.get_data(as_text=True)}")

        second = _offer_checkout(client, offer_id)

    body = second.get_json() or {}
    assert body.get("code") != "PAYMENT_CONFIGURATION_ERROR" and \
        body.get("error_code") != "PAYMENT_CONFIGURATION_ERROR", (
            "the offer retry was refused by Stripe's idempotency contract, not "
            f"by anything about the offer: {body.get('message')!r}")
    assert "temporarily unavailable" not in str(body.get("message") or "").lower()
    assert second.status_code == 200, second.get_data(as_text=True)
    assert body.get("checkout_url"), "the offer retry produced no payable session"


def test_offer_provider_key_names_the_transaction_it_was_sent_with(buyer):
    """The invariant, asserted positionally on this lane.

    ``endswith`` rather than ``in``: the attempt key is a hash containing the
    offer id, the listing id and the amount, so the transaction id's digits
    occur inside it by coincidence and a substring check would pass against the
    *unfixed* key.
    """
    client, listing_id, seller_id, buyer_id, physical_listing_id = buyer
    _forget_claims(buyer_id)
    offer_id = _accepted_offer(buyer_id, seller_id, listing_id)

    stub = IdempotentStripeStub()
    with card_rail(stub):
        answer = _offer_checkout(client, offer_id)

    assert answer.status_code == 200, answer.get_data(as_text=True)
    tx_id = answer.get_json()["transaction_id"]
    key = stub.calls[0]["idempotency_key"]
    assert key.endswith(f":{tx_id}"), (
        f"key {key!r} does not end by naming transaction {tx_id}, which its "
        f"success_url, cancel_url, transfer_group and metadata are built from")


def test_offer_double_tap_builds_one_session_and_takes_one_unit(buyer):
    """§23 on the lane whose key no longer collides.

    The provider refusal used to be this lane's de-facto double-tap guard too,
    and the key change removes it. What has to carry the guarantee instead is
    the claim this lane already takes before its ``seller_transactions`` INSERT
    — so this test is the check that the pre-existing claim is load-bearing now
    that nothing downstream is backing it up.

    Against the physical listing: `digital` is in ``STOCKLESS_KINDS``, so the
    stock assertions would pass vacuously on the other one.
    """
    client, listing_id, seller_id, buyer_id, physical_listing_id = buyer
    _forget_claims(buyer_id)
    offer_id = _accepted_offer(buyer_id, seller_id, physical_listing_id)

    before = _listing_quantity(physical_listing_id)
    held_before = _held_reservations(physical_listing_id)
    stub = IdempotentStripeStub()
    with card_rail(stub):
        first = _offer_checkout(client, offer_id, details=SHIPPING_DETAILS)
        second = _offer_checkout(client, offer_id, details=SHIPPING_DETAILS)

    assert first.status_code == 200, first.get_data(as_text=True)
    after = _listing_quantity(physical_listing_id)
    assert before > 0 and after < before, (
        "the offer lane did not decrement this listing at all — the assertions "
        "below would pass vacuously")
    assert after == before - 1, "an offer double tap decremented the shelf twice"
    assert _held_reservations(physical_listing_id) == held_before + 1, (
        "an offer double tap left more than one hold")
    assert len(stub.created) == 1, (
        f"an offer double tap built {len(stub.created)} payable sessions")

    body = second.get_json() or {}
    assert second.status_code == 200 or \
        checkout_identity.IN_PROGRESS_CODE in {body.get("code"), body.get("error_code")}, (
            f"an offer double tap was answered with {second.status_code} {body!r}")
    if second.status_code == 200:
        assert body.get("checkout_url") == first.get_json()["checkout_url"], (
            "the second tap was handed a different session than the first")


def test_offer_failed_attempt_leaves_no_blocking_claim(buyer):
    """§9 as a database fact, so the release is asserted and not assumed."""
    client, listing_id, seller_id, buyer_id, physical_listing_id = buyer
    _forget_claims(buyer_id)
    offer_id = _accepted_offer(buyer_id, seller_id, listing_id)

    stub = IdempotentStripeStub(fail_first_after_create=True)
    with card_rail(stub):
        failed = _offer_checkout(client, offer_id)
    assert failed.status_code >= 400

    cur = bot.db().cursor()
    cur.execute(
        "SELECT idempotency_key, response_json FROM marketplace_cart_checkout_keys "
        "WHERE user_id=?", (buyer_id,))
    blocking = [dict(r) for r in cur.fetchall()
                if not (dict(r).get("response_json") or "")]
    assert blocking == [], (
        f"a failed offer checkout left {len(blocking)} unanswered claim(s): "
        f"{[r.get('idempotency_key') for r in blocking]}")


def test_offer_expires_the_session_it_created_but_could_not_deliver(buyer):
    """§16/§24 on the third lane.

    This lane had no expiry at all — its failure path settled the transaction,
    released the hold and released the claim, and left whatever Stripe had
    already built sitting open. With the key now co-varying, the retry mints a
    second session, so the first one has to go.
    """
    client, listing_id, seller_id, buyer_id, physical_listing_id = buyer
    _forget_claims(buyer_id)
    offer_id = _accepted_offer(buyer_id, seller_id, listing_id)

    stub = IdempotentStripeStub()
    with card_rail(stub), provider_read_fails_on("url"):
        failed = _offer_checkout(client, offer_id)

    assert failed.status_code >= 400, "the injected failure did not fail the attempt"
    assert len(stub.created) == 1, "the fixture did not get a session created"
    orphan = stub.created[0]["id"]
    assert stub.expired == [orphan], (
        f"the offer lane created session {orphan} and then left it payable; "
        f"expired={stub.expired}")


def test_the_offer_lane_bootstraps_the_hold_columns_it_writes():
    """The offer lane must not depend on a cart request having run first.

    Its reservation INSERT names ``reserved_at`` and ``expires_at``. Neither is
    in the table ``bot.init_db()`` creates — they are added by
    ``marketplace_reservation_schema``, whose only caller was
    ``marketplace_cart_routes._ensure_schema``. So an offer checkout on a
    physical listing raised ``OperationalError: no column named reserved_at``
    in any process that had not served a cart, which is a 500 where a purchase
    should be.

    Written against a bare connection rather than through the route on purpose.
    Driven over HTTP this cannot fail: both this module and the schema module
    keep *process-global* "already done" flags, and the cart tests earlier in
    this file have already set them, so by the time an offer test runs the
    columns exist however the offer lane behaves. Removing the bootstrap leaves
    the whole suite green — which is the same class of blind spot as the
    original bug, a dependency satisfied by call order rather than declared.

    So the caches are reset, a fresh database is given only the base table, and
    the lane is asked to prepare its own schema with nothing else having run.
    """
    import sqlite3

    from services import marketplace_offers_routes
    from services import marketplace_reservation_schema as reservation_schema

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    # The table exactly as bot.init_db() writes it — the state a cold database
    # is in before anything marketplace-shaped has been served.
    cur.execute("""CREATE TABLE marketplace_inventory_reservations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        seller_transaction_id INTEGER UNIQUE,
        buyer_user_id INTEGER,
        listing_id INTEGER,
        quantity INTEGER DEFAULT 1,
        status TEXT DEFAULT 'held',
        created_at TEXT,
        updated_at TEXT)""")

    def columns():
        return {row[1] for row in cur.execute(
            "PRAGMA table_info(marketplace_inventory_reservations)").fetchall()}

    # Proves the premise: without the bootstrap the lane's own INSERT cannot
    # execute, so the assertion below is a measurement and not a tautology.
    assert "reserved_at" not in columns(), (
        "the base table already has the lifecycle columns, so this test cannot "
        "detect a missing bootstrap")

    ready = marketplace_offers_routes._SCHEMA_READY
    try:
        marketplace_offers_routes._SCHEMA_READY = False
        reservation_schema.reset_schema_cache()
        marketplace_offers_routes._ensure_schema(cur)
    finally:
        marketplace_offers_routes._SCHEMA_READY = ready
        reservation_schema.reset_schema_cache()

    present = columns()
    for column in ("reserved_at", "expires_at"):
        assert column in present, (
            f"the offer lane writes {column} but does not ensure it exists; a "
            f"process that has not served a cart 500s on every physical offer "
            f"checkout. Present: {sorted(present)}")
    conn.close()


def test_every_lane_derives_its_provider_key_from_the_shared_module(buyer):
    """One namespace, three lanes, no fourth convention.

    The incident was possible because each lane invented its own key expression
    inline, so the rule "the key must name the row the parameters name" lived in
    three places and was wrong in all three. Asserted as a property of the keys
    that actually reached the stub, across all three lanes in one test, so a
    fourth lane added later with its own f-string is visible here.
    """
    client, listing_id, seller_id, buyer_id, physical_listing_id = buyer
    prefix = checkout_identity.stripe_idempotency_key("")

    keys = []
    for drive in (
        lambda: (_seed_one_line(client, listing_id),
                 _checkout(client, seller_id)),
        lambda: _buy_now(client, listing_id, client_key="all-lanes-1"),
        lambda: _offer_checkout(
            client, _accepted_offer(buyer_id, seller_id, listing_id)),
    ):
        _forget_claims(buyer_id)
        stub = IdempotentStripeStub()
        with card_rail(stub):
            drive()
        assert stub.calls, "a lane reached no provider at all"
        keys.append(stub.calls[0]["idempotency_key"])

    assert len(keys) == 3
    for key in keys:
        assert key.startswith(prefix), (
            f"key {key!r} does not come from "
            f"checkout_identity.stripe_idempotency_key (prefix {prefix!r})")
    assert len(set(keys)) == 3, (
        f"two lanes produced the same provider key: {keys} — distinct purchases "
        "sharing a key is the collision, not a fix for it")


# --------------------------------------------------------------------------
# Mutation guards — these must fail if the tests above stop testing
# --------------------------------------------------------------------------

def test_mutation_the_stub_really_does_enforce_the_binding():
    """If the stub stopped refusing, every test above would pass vacuously."""
    stub = IdempotentStripeStub()
    stub.create(idempotency_key="k", mode="payment", success_url="a")
    with pytest.raises(stripe.error.IdempotencyError):
        stub.create(idempotency_key="k", mode="payment", success_url="b")


def test_mutation_the_stub_ignores_the_key_when_comparing_parameters():
    """The key itself is not one of the parameters Stripe compares.

    If it were, two calls could never collide and the stub would be incapable
    of reproducing the incident.
    """
    assert _canonical({"idempotency_key": "a", "mode": "payment"}) == \
           _canonical({"idempotency_key": "b", "mode": "payment"})


def test_mutation_an_idempotency_error_really_classifies_as_the_incident_copy():
    """Pins the message the buyer actually saw to the exception that caused it.

    This is what makes the taxonomy work (handled separately) a change with a
    test behind it rather than a copy edit: today an ``IdempotencyError`` — our
    own key-management bug, entirely retryable — is reported as a provider
    outage.
    """
    from services.marketplace_payment_errors import classify_provider_exception

    classified = classify_provider_exception(
        stripe.error.IdempotencyError("Keys for idempotent requests ..."))

    assert classified["code"] == "PAYMENT_CONFIGURATION_ERROR"
    assert classified["message"] == (
        "Payments are temporarily unavailable. No card was charged.")
    assert classified["provider_error"]["type"] == "IdempotencyError"
