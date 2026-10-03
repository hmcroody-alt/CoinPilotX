"""Phase 11 money invariants, pinned at the checkout *wiring*.

`test_quote_authority.py` already proves the quote module charges the right
rate. That is not the same claim as "the cart checkout charges the right rate",
and this mission has twice found the gap between those two: a reconciliation
sweep that was fully tested and never called, and three financial events whose
module was proven while deleting their call sites left the suite green. A tested
module whose caller is untested is an unproven feature.

So these tests look at the chain the buyer's money actually travels:

    bot.seller_fee_bps(cur, "merchant")
        -> business_os.marketplace.policy.platform_fee_bps()
    -> marketplace_payment_pause.platform_fee_bps_for_marketplace_payment(.., mode)
    -> marketplace_quote_service.create_quote(live_fee_bps=..)

The invariants, from the brief: the platform fee is 0%, 5% is not activated,
10% is not resurrected, shipping is free, money is server-authoritative, and the
buyer is charged for the variant they picked.

Why some of these are AST assertions
------------------------------------
Three of the invariants are properties of a *call that is written*, not of a
value that can be returned: that the checkout passes the literal ``"merchant"``,
that it passes no ``shipping_minor``/``tax_minor``, and that the unit price it
quotes is the server's stored snapshot rather than anything off the request.
Parsed rather than grepped, deliberately -- a grep counts a mention inside a
comment or a docstring as a call, and this repo has a protection suite that was
blind for exactly that reason. The honest limit is the same one as in
`test_checkout_financial_events.py`: these prove the call is written that way,
not that it ran.
"""

import ast
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import marketplace_cart_routes as cart_routes
from services import marketplace_payment_pause as pause
from services import marketplace_quote_service as quotes
from services.business_os.marketplace import policy

FEE_GATES = (
    "MARKETPLACE_STANDARD_V1_OWNER_APPROVED",
    "MARKETPLACE_STANDARD_V1_SELLER_DISCLOSURE_READY",
    "MARKETPLACE_STANDARD_V1_EFFECTIVE_AT",
)


@pytest.fixture
def gates_shut(monkeypatch):
    """Production's real state: no gate variable is set on any service."""
    for key in FEE_GATES:
        monkeypatch.delenv(key, raising=False)
    assert policy.platform_fee_bps() == 0
    return policy


@pytest.fixture
def gates_open(monkeypatch):
    monkeypatch.setenv("MARKETPLACE_STANDARD_V1_OWNER_APPROVED", "1")
    monkeypatch.setenv("MARKETPLACE_STANDARD_V1_SELLER_DISCLOSURE_READY", "1")
    monkeypatch.setenv("MARKETPLACE_STANDARD_V1_EFFECTIVE_AT", "2000-01-01T00:00:00Z")
    assert policy.platform_fee_bps() == policy.PROPOSED_PLATFORM_FEE_BPS
    return policy


def _checkout_quote_call() -> ast.Call:
    """The one `create_quote(...)` the cart checkout prices its lines with."""
    tree = ast.parse(open(cart_routes.__file__, encoding="utf-8").read())
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "create_quote"
    ]
    assert len(calls) == 1, (
        f"expected exactly one create_quote call site in {cart_routes.__file__}, "
        f"found {len(calls)}. A second pricing path is a second place for a fee, "
        "a shipping charge or a client-supplied price to enter; point this test "
        "at both or collapse them."
    )
    return calls[0]


def _kwargs(call: ast.Call) -> dict:
    return {kw.arg: kw.value for kw in call.keywords if kw.arg}


# --------------------------------------------------------------------------
# the rate itself
# --------------------------------------------------------------------------

def test_the_platform_fee_is_zero_in_the_configuration_production_runs(gates_shut):
    # Not "can be zero" -- is zero, for the environment prod actually has. No
    # MARKETPLACE_STANDARD_V1 variable is set on any Railway service.
    assert policy.fee_policy_active() is False
    assert policy.platform_fee_bps() == 0


def test_five_percent_exists_only_as_a_proposal(gates_shut):
    # The brief: do not activate 5%. It stays a named constant that the gates
    # withhold, so this test fails the moment someone makes it the live rate.
    assert policy.PROPOSED_PLATFORM_FEE_BPS == 500
    assert policy.platform_fee_bps() == 0


def test_ten_percent_is_not_a_value_the_policy_can_ever_return(gates_open):
    # The brief: do not resurrect 10%. There are exactly two reachable rates,
    # and 1000 is neither -- on either side of the gates.
    assert policy.platform_fee_bps() == 500
    assert policy.platform_fee_bps() != 1000


def test_the_merchant_lane_cannot_reach_the_mutable_fee_table():
    """The undisclosed 10% row is unreachable, not merely unused.

    ``platform_fee_rules`` still holds a 1000 bps merchant row, and it is an
    admin-editable table -- exactly the thing a commission must never be priced
    off. ``seller_fee_bps`` short-circuits the ``merchant`` lane to the versioned
    policy before the SELECT.

    Passing ``None`` as the cursor is the assertion: if the merchant lane ever
    reached the table again this would raise ``AttributeError`` instead of
    answering. A test that passed a working cursor would still pass with the
    short-circuit deleted, because the table lookup would simply return 1000.
    """
    import bot

    assert bot.seller_fee_bps(None, "merchant") == policy.platform_fee_bps()


def test_the_checkout_asks_for_the_merchant_lane_by_name():
    tree = ast.parse(open(cart_routes.__file__, encoding="utf-8").read())
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "seller_fee_bps"
    ]
    assert len(calls) == 1, f"expected one seller_fee_bps call, found {len(calls)}"
    literals = [a.value for a in calls[0].args if isinstance(a, ast.Constant)]
    # A computed seller_type here would let the table lane back in: any value
    # that is not "merchant" falls through to `platform_fee_rules` and its 1000.
    assert "merchant" in literals, (
        "the cart checkout must pass the literal \"merchant\" so the lane that "
        "reads platform_fee_rules stays unreachable"
    )


# --------------------------------------------------------------------------
# what the buyer is charged
# --------------------------------------------------------------------------

def test_the_buyer_pays_exactly_the_merchandise_price(gates_shut):
    # The cart's own argument shape: no shipping, no tax, fee transported as 0.
    fee_bps = pause.platform_fee_bps_for_marketplace_payment(
        policy.platform_fee_bps(), "card")
    q = quotes.create_quote(listing_id=1, seller_id=2, quantity=1,
                            unit_price_minor=625, currency="USD",
                            live_fee_bps=fee_bps)
    assert q["buyer_total_minor"] == 625
    assert q["merchandise_net_minor"] == 625
    assert q["platform_fee_minor"] == 0
    assert q["seller_earnings_minor"] == 625


def test_shipping_and_tax_are_zero_and_no_buyer_service_fee_exists(gates_shut):
    assert policy.BUYER_SERVICE_FEE_CENTS == 0
    q = quotes.create_quote(listing_id=1, seller_id=2, quantity=2,
                            unit_price_minor=1_000, currency="USD",
                            live_fee_bps=0)
    assert q["shipping_minor"] == 0
    assert q["tax_minor"] == 0
    assert q["buyer_service_fee_minor"] == 0
    assert q["buyer_total_minor"] == 2_000


def test_the_checkout_passes_no_shipping_or_tax_amount():
    """Free shipping is a property of the call, not of a default.

    ``create_quote`` would happily price a shipping charge; the cart simply
    never hands it one. The ``shipping=`` it does pass is the fulfilment
    *descriptor* (``{"fulfillment": lane}``), not an amount, so this test has to
    distinguish the two -- which is the whole reason it names the minor-unit
    parameters rather than looking for the word "shipping".
    """
    kwargs = _kwargs(_checkout_quote_call())
    assert "shipping_minor" not in kwargs, "the cart checkout must add no shipping charge"
    assert "tax_minor" not in kwargs, "the cart checkout must add no tax"
    assert "seller_discount_minor" not in kwargs


def test_the_quoted_price_is_the_servers_stored_snapshot():
    """Money is server-authoritative: the price is read, never accepted.

    ``price_snapshot_minor`` is the per-(user, listing, variant) price the
    server captured when the line was added. A request-derived value here --
    anything off ``request.json`` or a posted ``price`` -- would let the buyer
    name their own total.
    """
    kwargs = _kwargs(_checkout_quote_call())
    price = kwargs.get("unit_price_minor")
    assert isinstance(price, ast.Subscript), (
        "unit_price_minor must be a subscript of the stored cart line")
    assert isinstance(price.slice, ast.Constant)
    assert price.slice.value == "price_snapshot_minor", (
        f"unit_price_minor reads {ast.dump(price.slice)}, not the server's "
        "price_snapshot_minor")
    assert isinstance(price.value, ast.Name) and price.value.id == "l"


def test_the_quantity_is_also_taken_from_the_stored_line():
    kwargs = _kwargs(_checkout_quote_call())
    qty = kwargs.get("quantity")
    assert isinstance(qty, ast.Subscript) and isinstance(qty.slice, ast.Constant)
    assert qty.slice.value == "qty"


# --------------------------------------------------------------------------
# the lanes
# --------------------------------------------------------------------------

def test_cash_carries_no_commission_even_once_the_gates_open(gates_open):
    # Cash settles in person, so the platform never touches the money and must
    # not book a fee against it. True on both sides of the gates.
    assert pause.platform_fee_bps_for_marketplace_payment(500, "cash") == 0
    assert pause.platform_fee_bps_for_marketplace_payment(500, "card") == 500


def test_the_card_lane_transports_the_policy_rate_unchanged(gates_shut):
    # The pause helper may zero a rate; it may never invent one.
    assert pause.platform_fee_bps_for_marketplace_payment(0, "card") == 0
    assert pause.platform_fee_bps_for_marketplace_payment(500, "card") == 500
    assert pause.platform_fee_bps_for_marketplace_payment(1000, "card") == 1000


@pytest.mark.parametrize("rate", [1, 250, 1000, 1500, 10_000])
def test_a_rate_the_policy_did_not_set_is_refused_not_corrected(gates_shut, rate):
    # Loudly, so a miswired caller surfaces instead of being silently repriced
    # to the policy rate. Refusing is also what makes the whole chain above
    # fail closed: a resurrected 10% stops a checkout rather than overcharging.
    with pytest.raises(ValueError, match=str(rate)):
        quotes.create_quote(listing_id=1, seller_id=2, quantity=1,
                            unit_price_minor=10_000, currency="USD",
                            live_fee_bps=rate)


def test_the_snapshot_records_the_rate_and_which_side_of_the_gates_it_was_priced_on(gates_shut):
    q = quotes.create_quote(listing_id=1, seller_id=2, quantity=1,
                            unit_price_minor=10_000, currency="USD",
                            live_fee_bps=0)
    # A settlement audited years later has to be able to say what the seller was
    # shown, so the rate and the policy state are both in the snapshot.
    assert q["platform_fee_bps"] == 0
    assert q["fee_policy_active"] is False
    assert q["fee_policy_version"] == policy.POLICY_VERSION


def test_the_snapshot_records_a_nonzero_rate_as_the_rate_it_charged(gates_open):
    """The snapshot must carry the real rate, not a plausible zero.

    Asserting this only with the gates shut proves nothing: the rate *is* zero
    there, so a snapshot hardcoded to zero passes. Found by mutation -- replacing
    `"platform_fee_bps": rate` with a literal 0 survived the whole suite until
    this test existed.
    """
    q = quotes.create_quote(listing_id=1, seller_id=2, quantity=1,
                            unit_price_minor=10_000, currency="USD",
                            live_fee_bps=500)
    assert q["platform_fee_bps"] == 500
    assert q["platform_fee_minor"] == 500
    assert q["fee_policy_active"] is True


# Each partial state, plus each pair. Activation is `all(...)`, and the failure
# this guards is `any(...)`: one stray variable on one service would switch on a
# live commission nobody disclosed. Found by mutation -- the all/any flip
# survived while the fixtures only ever set three gates or none.
@pytest.mark.parametrize("present", [
    ("MARKETPLACE_STANDARD_V1_OWNER_APPROVED",),
    ("MARKETPLACE_STANDARD_V1_SELLER_DISCLOSURE_READY",),
    ("MARKETPLACE_STANDARD_V1_EFFECTIVE_AT",),
    ("MARKETPLACE_STANDARD_V1_OWNER_APPROVED",
     "MARKETPLACE_STANDARD_V1_SELLER_DISCLOSURE_READY"),
    ("MARKETPLACE_STANDARD_V1_OWNER_APPROVED",
     "MARKETPLACE_STANDARD_V1_EFFECTIVE_AT"),
    ("MARKETPLACE_STANDARD_V1_SELLER_DISCLOSURE_READY",
     "MARKETPLACE_STANDARD_V1_EFFECTIVE_AT"),
])
def test_a_partially_opened_gate_takes_no_commission(monkeypatch, present):
    for key in FEE_GATES:
        monkeypatch.delenv(key, raising=False)
    for key in present:
        monkeypatch.setenv(
            key, "2000-01-01T00:00:00Z" if key.endswith("_EFFECTIVE_AT") else "1")

    missing = [k for k in FEE_GATES if k not in present]
    assert missing, "this test is about incomplete activation"
    assert policy.fee_policy_active() is False, (
        f"the fee activated with {sorted(missing)} still unset")
    assert policy.platform_fee_bps() == 0

    # And the rate the checkout would transport is zero too, not merely the
    # policy's answer in isolation.
    assert pause.platform_fee_bps_for_marketplace_payment(
        policy.platform_fee_bps(), "card") == 0
