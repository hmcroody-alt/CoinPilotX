"""The web cart pays, and it offers exactly what the checkout lane will honour.

## What was wrong

`/pulse/cart` listed lines and stopped. The buyer could add, re-quantify, remove
and confirm a price, and then had nowhere to go: the only pay button in the
product was in the app. The website's funnel ended one step before the money.

Wiring the button is not the hard part. `POST /api/pulse/marketplace/cart/checkout`
has existed since before the web cart, and its hosted lane already returns a
Stripe Checkout Session URL with `success_url` and `cancel_url` pointing at *web*
routes -- it was built for a browser before there was a browser calling it.

The hard part is that it refuses far more than "is this in stock". It settles
**one seller** as **one charge**, and it refuses the whole group for: one sold,
removed or restricted line; one line whose goods policy is not ALLOWED; one
unconfirmed price change; two currencies; a booking sharing the basket with
anything else; a missing lane answer; buyer details that do not validate for the
resolved kind; a seller who cannot take a card; a group total under the currency
floor; and a supplier that can no longer fill the order.

A flat page with one cross-seller subtotal and one pay button cannot honestly
offer any of that. It says "pay for these six" where the server offers "pay for
these two, then these three, and not that one at all" -- and the buyer discovers
the difference as a 409 *after* committing. So the page is a list of seller
groups, each carrying its own verdict, its own reason and its own form, and this
file's job is to pin that the verdicts and the form are the *server's* and not a
second implementation of the server's rules living in a browser.

## Why so much of this is substitution rather than comparison

Three of the things asserted below produce, today, character-for-character what a
hand-written copy would produce:

* `app_links.website_href_template("product")` yields `/pulse/marketplace/__RESOURCE_ID__`,
  which is exactly what a literal in the JavaScript produced before this change.
* `marketplace_cart_web.details_kind_for(["shipping"])` yields `"shipping"`,
  which is what the checkout route's own inline `next(...)` yielded.
* `marketplace_fulfillment.buyer_form("shipping")` yields the same nine fields a
  port of `_FIELDS` and `_LABELS` into JavaScript would list.

So a test that compares values passes just as happily for code that never
consults the shared authority at all -- and keeps passing straight through the
change that makes the authority answer something different, which is the failure
actually worth catching. `web_equivalent` flipping on the `product` destination
turns the first into `/open/product/__RESOURCE_ID__`; a tenth shipping field
turns the third into ten. Only substituting the authority and watching the caller
follow can tell a call from a coincidence, so that is what the four
`..._through_the_...` tests below do.

## Why the group verdicts are unit tests

`group_lines` takes cart lines and returns groups. It imports no Flask and no
`bot`, like `marketplace_web` and `marketplace_storefront`, so every verdict in
it can be provoked from a dict -- including the combinations a database fixture
makes expensive: two currencies in one seller's basket, a booking beside a
widget, a goods-policy refusal on a line whose state is `available`. Those are
the cases that reach a real buyer and never reach a test that has to seed a row
for each one.

The refusal *order* is asserted too, and it is not cosmetic: telling someone to
confirm a new price on a line that is also sold out sends them to a dead end.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Importing `bot` connects and runs init_db() at module scope, so the env has to
# be bound before the import rather than in a fixture.
os.environ["DATABASE_URL"] = "sqlite:///" + tempfile.mkstemp(suffix=".db")[1]
os.environ["COINPILOTX_INIT_DB_ON_IMPORT"] = "1"
os.environ.setdefault("FLASK_SECRET_KEY", "web-cart-checkout-tests")
# The card rail is an env flag, and the seller-capability verdict below is the
# thing under test. Without this, every checkout assertion would be asserting
# the pause instead.
os.environ.setdefault("MARKETPLACE_CARD_PAYMENTS_ENABLED", "1")

import bot  # noqa: E402
from services import app_links  # noqa: E402
from services import marketplace_cart_web as cart_web  # noqa: E402
from services import marketplace_fulfillment as fulfillment  # noqa: E402

# enforce_https 301s anything that does not look like it arrived over TLS.
HTTPS = {"X-Forwarded-Proto": "https"}

CART_API = "/api/pulse/marketplace/cart"
CHECKOUT_API = CART_API + "/checkout"


# ---------------------------------------------------------------------------
# Line builders
#
# One function rather than a fixture, because most tests below need two or three
# lines that differ in one field and a fixture would have to be parametrised on
# every field that matters.
# ---------------------------------------------------------------------------

def line(line_id, seller=7, **over):
    """A cart line as ``_serialize_lines`` emits one, with the fields that decide.

    The defaults are the ordinary case -- an available, single-currency, shipped
    line -- so each test names only its own deviation and a reader can see what
    the test is about without reading past the call.
    """
    built = {
        "line_id": line_id,
        "listing_id": 1000 + line_id,
        "seller_user_id": seller,
        "seller_store_name": "Probe Store",
        "title": "Probe Widget",
        "qty": 1,
        "state": "available",
        "currency": "USD",
        "price_snapshot_minor": 1999,
        "price_now_minor": 1999,
        "fulfillment": "shipping",
        "fulfillment_kind": "shipping",
        "goods_policy": {"decision": "ALLOWED"},
        "listing_metadata": {},
    }
    built.update(over)
    return built


def only(lines):
    groups = cart_web.group_lines(lines)
    assert len(groups) == 1, f"expected one seller group, got {len(groups)}"
    return groups[0]


def empty_cart(client):
    """Leave the buyer's cart empty, and prove it.

    The `buyer` fixture is module-scoped, so the cart carries over between the
    tests that use it. Asserting a quantity without this reads as a bug in the
    quantity rather than as one test seeing another's basket -- and the state each
    test wants is "only what I put here", which is what this states.
    """
    for entry in client.get(CART_API, headers=HTTPS).get_json()["lines"]:
        client.delete(f"{CART_API}/{entry['line_id']}", headers=HTTPS)
    assert client.get(CART_API, headers=HTTPS).get_json()["lines"] == []


@contextlib.contextmanager
def _card_rail_open(session=None):
    """The two things a test process has that a payment lane does not: no Stripe.

    `marketplace_card_capability.evaluate` answers STRIPE_UNAVAILABLE without a
    secret key, and the lane returns 503 from that check *before* it reaches the
    fulfilment rules -- so without this every assertion below about a form or a
    session URL would quietly be an assertion about a missing API key.

    Deliberately narrow. The per-seller verdict is a real guard with its own tests;
    stubbing it here says "assume this seller may take a card", which is the
    precondition of the thing under test and not the thing under test. Nothing
    here touches a real card, a real key or a real Stripe account: `session` is a
    dict this file wrote, and the only network call the lane would make is the one
    being replaced.
    """
    from services import marketplace_card_capability

    created = []

    class _Sessions:
        @staticmethod
        def create(**kwargs):
            created.append(kwargs)
            return dict(session or {"id": "cs_test_stub", "url": STUB_SESSION_URL})

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
        yield created
    finally:
        marketplace_card_capability.evaluate = real_evaluate
        bot.STRIPE_SECRET_KEY = real_key
        bot.stripe.checkout = real_checkout


STUB_SESSION_URL = "https://checkout.stripe.test/c/pay/stub-session"


# ---------------------------------------------------------------------------
# 1. The groups are the units the lane transacts in
# ---------------------------------------------------------------------------

def test_lines_are_grouped_by_seller_in_the_order_the_seller_first_appears():
    """One group per seller, and the buyer's own ordering is preserved.

    First-appearance order rather than a sort on the store name: the cart is
    ordered by when each line was added, and re-sorting would move the group the
    buyer is looking at when an unrelated line arrives from somewhere else.
    """
    groups = cart_web.group_lines([
        line(1, seller=7),
        line(2, seller=9),
        line(3, seller=7),
    ])
    assert [g["seller_user_id"] for g in groups] == [7, 9]
    assert [g["line_ids"] for g in groups] == [[1, 3], [2]]


def test_a_groups_subtotal_is_the_price_the_lane_will_charge():
    """The snapshot times the quantity, per line, summed over the group.

    The snapshot and not `price_now_minor`: `create_quote` is called with
    `unit_price_minor=l["price_snapshot_minor"]`, so the stored number is the one
    that becomes the charge. A page that added up the live prices would show a
    total the receipt then contradicts.
    """
    group = only([
        line(1, qty=3, price_snapshot_minor=500, price_now_minor=900),
        line(2, qty=1, price_snapshot_minor=1250),
    ])
    assert group["subtotal_minor"] == 3 * 500 + 1250
    assert group["item_count"] == 4
    assert group["subtotal_final"] is True
    assert group["checkoutable"] is True
    assert group["reason"] == ""


def test_one_sellers_problem_does_not_block_another_sellers_group():
    """The whole reason the page is grouped rather than flat.

    A flat page has one verdict for the basket, so one sold-out line from one
    seller either blocks a purchase the server would have taken or is silently
    dropped from a subtotal. Grouped, the other seller is still payable.
    """
    groups = cart_web.group_lines([
        line(1, seller=7, state="sold"),
        line(2, seller=9),
    ])
    assert groups[0]["checkoutable"] is False
    assert groups[0]["blocking_line_ids"] == [1]
    assert groups[1]["checkoutable"] is True
    assert groups[1]["blocking_line_ids"] == []


@pytest.mark.parametrize("state", sorted(cart_web.BLOCKING_STATES))
def test_every_blocking_state_blocks_the_whole_group(state):
    """Including the sibling line that is perfectly fine.

    Parametrised over the set rather than over three literals, because the set is
    what the lane checks and the point is that the two agree. `restricted` is the
    one worth naming: it is what `_line_state` returns for a listing whose seller
    lost approval, and nothing about the line itself looks wrong.
    """
    group = only([line(1, state=state), line(2)])
    assert group["checkoutable"] is False
    assert group["blocking_line_ids"] == [1]
    assert "no longer available" in group["reason"]


def test_a_goods_policy_refusal_blocks_a_line_whose_state_is_available():
    """`state` and `goods_policy` are two independent refusals, and the lane ORs them.

    This is the case a state-only check misses entirely: the listing is live, in
    stock, and the buyer's own jurisdiction is why it cannot ship. `_line_state`
    returns `available` and the checkout lane still refuses the group.
    """
    group = only([line(1, goods_policy={"decision": "BLOCKED"})])
    assert group["checkoutable"] is False
    assert group["blocking_line_ids"] == [1]


def test_a_missing_goods_policy_is_not_read_as_a_refusal():
    """An absent verdict means nothing was asked, not that the answer was no.

    The lane defaults the same way (`l.get("goods_policy", {}).get("decision")`
    against `!= "ALLOWED"` is the lane's form, and this mirrors it via a default
    of `ALLOWED`), so the two must agree about the empty case or every ordinary
    cart would be greyed out here and payable there.
    """
    group = only([line(1, goods_policy={})])
    assert group["checkoutable"] is True
    assert group["blocking_line_ids"] == []


def test_a_price_change_withholds_the_subtotal_instead_of_stating_a_wrong_one():
    """`subtotal_final` exists so the page can decline to print a number.

    The snapshot is what the lane charges, so an unconfirmed line's contribution
    to the subtotal is the *old* price -- a number the buyer will not be charged
    and did not agree to. Rather than print it under the heading "total", the
    total is flagged unfinal and the reason names the confirm step.
    """
    group = only([line(1, state="price_changed", price_snapshot_minor=500,
                       price_now_minor=900)])
    assert group["subtotal_final"] is False
    assert group["price_changed_line_ids"] == [1]
    assert group["checkoutable"] is False
    assert "Confirm the new prices" in group["reason"]


def test_availability_is_reported_before_a_price_change():
    """The order of the reasons is the order the lane refuses in.

    `cart_checkout` checks `blocking` and returns, then checks `unconfirmed`. A
    page that reported the price change first would send the buyer to confirm a
    new price on a line that is also sold out -- a dead end, and one they only
    escape by reading the line badges the sentence distracted them from.
    """
    group = only([line(1, state="sold"), line(2, state="price_changed")])
    assert "no longer available" in group["reason"]
    assert "Confirm the new prices" not in group["reason"]
    # Both facts still travel; it is only the headline that is ordered.
    assert group["blocking_line_ids"] == [1]
    assert group["price_changed_line_ids"] == [2]


def test_two_currencies_in_one_group_are_refused_the_way_the_lane_refuses_them():
    """MIXED_CURRENCY, and the subtotal is withheld rather than added up.

    Summing minor units across currencies produces a number with no denomination
    -- 1999 USD plus 1999 EUR is not 3998 of anything. So the refusal and the
    unfinal subtotal go together.
    """
    group = only([line(1, currency="USD"), line(2, currency="EUR")])
    assert group["mixed_currency"] is True
    assert group["subtotal_final"] is False
    assert group["checkoutable"] is False
    assert "different currencies" in group["reason"]


def test_currency_is_compared_case_insensitively():
    """`usd` and `USD` are one currency, and a fixture is not the only source of case.

    Rows written by different call sites disagree about case, and reporting those
    as MIXED_CURRENCY would refuse a basket the lane -- which compares the
    serialiser's own normalised value -- happily takes.
    """
    group = only([line(1, currency="usd"), line(2, currency="USD")])
    assert group["mixed_currency"] is False
    assert group["currency"] == "USD"
    assert group["checkoutable"] is True


def test_a_booking_may_not_share_a_basket_and_says_so_when_it_does():
    """ITEM_NEEDS_OWN_CHECKOUT, mirrored.

    A group settles against one set of buyer details, which is right for an
    address and wrong for a date: two scheduled lines need two slots and a shared
    form has nowhere to put the second. The lane refuses; this reports it before
    the buyer fills the form.
    """
    group = only([line(1, fulfillment_kind="booking_remote"), line(2)])
    assert group["checkoutable"] is False
    assert "one at a time" in group["reason"]


def test_a_scheduled_line_on_its_own_is_handed_to_the_app_not_half_built_here():
    """A browser form cannot ask for a slot the seller actually published.

    The fields for a booking are a date, a time and a timezone, and the answers
    have to be checked against the seller's availability. The app has that
    picker. A bare `<input type=date>` here would take money for a booking at a
    time the seller never offered, which is worse than not offering the button.
    """
    group = only([line(1, fulfillment_kind="booking_remote")])
    assert group["checkoutable"] is False
    assert group["reason"] == cart_web.BROWSER_UNSUPPORTED
    assert "app" in group["reason"]


def test_a_group_that_would_need_two_lane_questions_is_handed_to_the_app():
    """One control cannot ask two questions, and guessing an answer costs money.

    `shipping_or_pickup` and `service_choice` are both undecided, and their
    answers are disjoint: no single word resolves both. Defaulting on the buyer's
    behalf is how someone collecting in person ends up typing an address they
    never needed -- so the group is refused rather than resolved.
    """
    group = only([
        line(1, fulfillment_kind="shipping_or_pickup"),
        line(2, fulfillment_kind="service_choice"),
    ])
    assert group["lane_question"] == {}
    assert group["forms"] == {}
    assert group["checkoutable"] is False
    assert "fulfilled in different ways" in group["reason"]
    # And specifically *not* the one-at-a-time sentence. `service_choice` starts
    # with `service_`, so the first cut of `scheduled_kinds` classified this
    # undecided question as a booking and told the buyer to check the item out on
    # its own -- advice that does not fix a group whose real problem is that it
    # asks two questions. The lane refuses it with LANE_REQUIRED, before its
    # scheduled check, and the reason order now matches.
    assert "one at a time" not in group["reason"]


def test_an_undecided_service_is_not_mistaken_for_a_scheduled_line():
    """`scheduled_kinds` is a prefix test, and one undecided kind shares the prefix.

    `service_choice` is not a service: it is the question of whether a service is
    delivered remotely or on site, and only one of those answers is scheduled. The
    checkout lane never sees the word because it calls `scheduled_kinds` with kinds
    `resolve_choice` has already settled; `group_lines` works from the cart's raw
    kinds and does.
    """
    assert cart_web.scheduled_kinds(["service_choice"]) == []
    assert cart_web.scheduled_kinds(["service_remote"]) == ["service_remote"]
    assert cart_web.scheduled_kinds(["service_in_person"]) == ["service_in_person"]
    # The narrowing is by the module's own undecided set, not by a literal, so a
    # third undecided kind added there is narrowed too.
    for kind in fulfillment.UNDECIDED_KINDS:
        assert cart_web.scheduled_kinds([kind]) == []

    # Which makes a *single* undecided service line refusable for the right
    # reason: a browser has no availability picker, not that it shares a basket.
    group = only([line(1, fulfillment_kind="service_choice")])
    assert group["checkoutable"] is False
    assert group["reason"] == cart_web.BROWSER_UNSUPPORTED


def test_a_digital_group_is_payable_and_is_asked_nothing():
    """The one kind with no questions at all, and it must not grow one.

    A digital line needs no address, no name and no slot. If `details_kind_for`
    ever returned a kind for an all-digital group, this page would demand an
    address before letting someone buy a download.
    """
    group = only([line(1, fulfillment_kind="digital")])
    assert group["checkoutable"] is True
    assert group["lane_question"] == {}
    assert group["forms"][""]["details_kind"] == ""
    assert group["forms"][""]["fields"] == []


# ---------------------------------------------------------------------------
# 2. The lane question, and the form behind each answer
# ---------------------------------------------------------------------------

def test_an_undecided_group_is_offered_one_form_per_answer_it_may_give():
    """Keyed by the answer, because the answer is what the browser posts back.

    And the two forms differ in the way that matters: picking delivery asks for a
    street address, picking collection does not. A single form covering both would
    either demand an address from someone collecting in person or let a parcel be
    ordered to nowhere.
    """
    group = only([line(1, fulfillment_kind="shipping_or_pickup")])
    assert group["lane_question"]["kind"] == "shipping_or_pickup"
    assert [o["value"] for o in group["lane_question"]["options"]] == ["shipping", "pickup"]

    assert sorted(group["forms"]) == ["pickup", "shipping"]
    assert group["forms"]["shipping"]["details_kind"] == "shipping"
    assert group["forms"]["pickup"]["details_kind"] == "pickup"

    shipping_keys = [f["key"] for f in group["forms"]["shipping"]["fields"]]
    pickup_keys = [f["key"] for f in group["forms"]["pickup"]["fields"]]
    assert "address_line1" in shipping_keys
    assert "address_line1" not in pickup_keys


def test_every_lane_option_offered_is_an_answer_resolve_choice_accepts():
    """A form offering a word the server refuses is the bug the pre-flight exists to avoid.

    Asserted over both undecided kinds rather than over the one the cart happens
    to exercise, because `lane_options` is the table a form is generated from and
    a fourth lane added to one kind and not the other would be invisible here
    otherwise.
    """
    for kind in sorted(fulfillment.UNDECIDED_KINDS):
        options = fulfillment.lane_options(kind)
        assert options, f"{kind} is undecided and offers no way to decide it"
        for option in options:
            resolved, error = fulfillment.resolve_choice(kind, option["value"])
            assert error == "", (
                f"the form offers {option['value']!r} for {kind}, and the server "
                f"refuses it with {error}")
            assert resolved not in fulfillment.UNDECIDED_KINDS, (
                f"answering {kind} with {option['value']!r} left it undecided")
            assert option["label"], f"{option['value']} is offered with no label"


def test_the_two_lanes_of_a_service_choice_are_not_the_same_lane_twice():
    """`resolve_choice` accepts four words for `service_choice` and they mean two things.

    `remote` and `service_remote` are the same lane, and a chooser built from the
    accepted-answers table alone would offer "Remotely" twice. `lane_options`
    exists precisely to collapse them, and this is the assertion that it does.
    """
    values = [o["value"] for o in fulfillment.lane_options("service_choice")]
    resolved = {fulfillment.resolve_choice("service_choice", v)[0] for v in values}
    assert len(resolved) == len(values) == 2, (
        f"service_choice offers {values} which resolve to only {sorted(resolved)}")


def test_a_ticket_tier_is_offered_only_when_the_group_is_the_one_listing_that_has_them():
    """Mirrors the lane's metadata rule exactly, including the part that looks wrong.

    Ticket tiers belong to one listing, so the lane passes
    `lines[0]["listing_metadata"] if len(lines) == 1 else {}` -- and a two-line
    group is therefore validated *without* the tier choice. That looks like a
    bug in the lane until you notice an event ticket is scheduled and so cannot
    share a basket anyway. The form must agree with it regardless: offering a
    required tier field the validator does not know about would refuse every
    submission of that form.
    """
    metadata = {"ticket_types": ["General", "VIP"]}
    solo = only([line(1, fulfillment_kind="shipping", listing_metadata=metadata)])
    keys = [f["key"] for f in solo["forms"][""]["fields"]]
    assert "ticket_type" not in keys, "a shipped item was offered a ticket tier"

    # The rule under test is the `len(lines) == 1` guard, so the interesting
    # comparison is the same metadata on a group of two.
    pair = cart_web.group_lines([
        line(1, listing_metadata=metadata),
        line(2, listing_metadata=metadata),
    ])[0]
    assert [f["key"] for f in pair["forms"][""]["fields"]] == keys


# ---------------------------------------------------------------------------
# 3. Substitution: the page's rules are the server's, not a copy of them
# ---------------------------------------------------------------------------

def test_the_form_is_generated_through_marketplace_fulfillment(monkeypatch):
    """Substitution, because a copied field table produces the right form today.

    `mobile-native/src/api/marketplaceFulfillment.ts` is a declared port of this
    module, pinned to it by a test on each side. That is the honest way to keep a
    copy, and it is what a JavaScript form here would have needed. Instead the
    server sends the form, and this proves it: swap `buyer_form` for a function
    that answers one absurd field and the group's form is that field. A test that
    compared the nine real shipping fields would pass for a hard-coded list of
    the same nine.
    """
    sentinel = ({"key": "__FROM_THE_MODULE__", "type": "text", "required": True,
                 "label": "Substituted", "autocomplete": ""},)
    calls = []

    def fake(kind, metadata=None):
        calls.append((kind, metadata))
        return sentinel

    monkeypatch.setattr(fulfillment, "buyer_form", fake)
    group = only([line(1)])

    assert [f["key"] for f in group["forms"][""]["fields"]] == ["__FROM_THE_MODULE__"], (
        "the group's form was built from something other than "
        "marketplace_fulfillment.buyer_form")
    assert calls == [("shipping", {})], (
        f"buyer_form was consulted, but not about this group's kind: {calls}")


def test_the_form_is_a_copy_the_caller_cannot_mutate_back_into_the_module():
    """`buyer_form` returns the module's own dicts; the payload must not share them.

    This is JSON that goes over the wire and gets edited on the way -- and
    `field_spec` builds `options` from a list. A group that handed out the
    module's objects would let one cart's serialisation leave a mark on the next
    request in the process.
    """
    real = [dict(f) for f in fulfillment.buyer_form("shipping")]
    group = only([line(1)])
    group["forms"][""]["fields"][0]["label"] = "vandalised"
    assert [dict(f) for f in fulfillment.buyer_form("shipping")] == real


def test_the_checkout_lane_resolves_its_details_kind_through_the_shared_helper(buyer):
    """Substitution against the *route*, which is the half that makes this matter.

    The form the buyer fills and the validation their submission faces have to
    come from one rule, or the page asks for an address the lane does not want
    (or, worse, does not ask for the one it does). The rule was inline in
    `cart_checkout`; it is now `marketplace_cart_web.details_kind_for`, and the
    route calls it.

    Proved by making the helper lie: a digital-only group is asked nothing, so
    forcing the answer to `shipping` must make the lane demand an address and
    refuse the submission that has none. If the route still had its own copy, the
    checkout would sail past this untouched.
    """
    client, listing_id, _seller_id = buyer
    empty_cart(client)
    added = client.post(CART_API, json={"listing_id": listing_id, "qty": 1},
                        headers=HTTPS)
    assert added.status_code == 200, added.get_data(as_text=True)

    cart = client.get(CART_API, headers=HTTPS).get_json()
    group = cart["groups"][0]
    assert group["forms"][""]["details_kind"] == "", (
        "the fixture listing is digital, so this test's premise is that the "
        "unpatched lane asks for nothing")

    body = {"seller_user_id": group["seller_user_id"], "fulfillment_details": {}}

    from services import marketplace_cart_routes

    with _card_rail_open():
        real = marketplace_cart_routes.marketplace_cart_web.details_kind_for
        try:
            marketplace_cart_routes.marketplace_cart_web.details_kind_for = (
                lambda kinds: "shipping")
            refused = client.post(CHECKOUT_API, json=body, headers=HTTPS)
        finally:
            marketplace_cart_routes.marketplace_cart_web.details_kind_for = real

    assert refused.status_code == 400, (
        "the checkout lane did not ask marketplace_cart_web which questions to "
        "validate; it answered HTTP %s to a substituted rule that demands an "
        "address. The buyer's form and the lane's validation are two rules."
        % refused.status_code)
    assert ((refused.get_json() or {}).get("error_code")
            == fulfillment.DETAILS_REQUIRED_CODE), refused.get_data(as_text=True)


def test_the_cart_links_products_through_the_registry(buyer):
    """The positive half of the guard in `test_marketplace_web_ctas_follow_the_registry`.

    That file forbids a product path from appearing in any `static/js` file at
    all; this one proves the cart's links are the registry's. Both halves are
    needed, and the negative one is not enough: `pulsesoc_cart.js` shipped
    `"/pulse/marketplace/" + line.listing_id` and nothing noticed, because it was
    not on a list and the general prohibitions matched only `${}` interpolation.

    Substitution rather than comparison, for the reason that whole file is about:
    the registry answers `/pulse/marketplace/__RESOURCE_ID__` today, which is
    character-for-character what the literal produced, so comparing the two
    passes for a page that never asks. It would keep passing after
    `web_equivalent` flips on `product` and the registry starts answering
    `/open/product/__RESOURCE_ID__` -- correct today, silently divergent
    tomorrow, which is the worst failure mode available.
    """
    client, _listing_id, _seller_id = buyer

    sentinel = "/open/product/__FROM_THE_REGISTRY__/__RESOURCE_ID__"
    real = app_links.website_href_template
    calls = []

    def fake(destination, source="web"):
        calls.append((destination, source))
        return sentinel

    app_links.website_href_template = fake
    try:
        body = client.get("/pulse/cart", headers=HTTPS).get_data(as_text=True)
    finally:
        app_links.website_href_template = real

    assert f'data-cart-product-href="{sentinel}"' in body, (
        "the cart page built a product href of its own instead of injecting the "
        "registry's template")
    assert ("product", "web") in calls, (
        f"the cart consulted the registry, but not about `product`: {calls}")

    # And the template really is a template, so the substitution the script
    # performs has somewhere to land. A registry that stopped emitting the
    # placeholder would leave the script replacing nothing and linking every line
    # to one URL.
    assert "__RESOURCE_ID__" in real("product", source="web")


def test_the_script_substitutes_the_template_and_assembles_nothing():
    """Read off the source, because there is no DOM here to click.

    Two claims: the script's only product href comes from the injected attribute,
    and it gets there by `String.replace` of the placeholder rather than by
    concatenating a path onto an id. The second is what makes the first survive a
    registry change -- a script that split the template and rejoined it around
    the id would be re-deriving the URL it was handed.
    """
    source = (ROOT / "static" / "js" / "pulsesoc_cart.js").read_text(encoding="utf-8")
    assert 'root.getAttribute("data-cart-product-href")' in source
    assert '__RESOURCE_ID__' in source
    assert re.search(r"PRODUCT_HREF\.replace\(\s*[\"']__RESOURCE_ID__[\"']", source), (
        "the script does not substitute the registry's placeholder")
    # The id is still escaped on the way in: a listing id is a number today and a
    # path segment regardless.
    assert "encodeURIComponent" in source


def test_the_page_names_no_buyer_field_of_its_own():
    """Every field key in the form comes down the wire; the script dispatches on type.

    This is the assertion that keeps the generated form generated. The moment the
    script mentions `address_line1`, it has an opinion about the field table and
    a tenth field added in Python stops reaching the browser -- silently, because
    the nine it knows about still render.
    """
    source = (ROOT / "static" / "js" / "pulsesoc_cart.js").read_text(encoding="utf-8")
    keys = [f["key"] for kind in ("shipping", "pickup", "booking_in_person", "event_in_person")
            for f in fulfillment.buyer_form(kind, {"ticket_types": ["General"]})]
    assert keys, "the field tables are empty, so this test proves nothing"
    leaked = sorted({key for key in keys if key in source})
    assert not leaked, (
        "pulsesoc_cart.js names buyer fields %s. The form is generated from "
        "marketplace_fulfillment.buyer_form and the script must dispatch on "
        "field.type, never on field.key." % (leaked,))


def test_no_card_field_exists_on_this_origin():
    """The hosted-session lane is the whole PCI argument, so it is pinned.

    `POST /checkout` without `payment_mode` normalises to `card`, which returns a
    Stripe-hosted Checkout Session URL; the card number is typed on Stripe's
    page. That means this origin has no Stripe.js, no Elements mount and no input
    that could accept a PAN -- and the cheapest way for that to stop being true
    is for someone to "improve" the flow into an inline form.
    """
    source = (ROOT / "static" / "js" / "pulsesoc_cart.js").read_text(encoding="utf-8")
    page = (ROOT / "templates" / "marketplace_cart.html").read_text(encoding="utf-8")
    for needle in ("js.stripe.com", "Stripe(", "elements.create", "cardNumber",
                   "card_number", "cvc", "autocomplete=\"cc-number\""):
        assert needle not in source, f"pulsesoc_cart.js has grown a card surface: {needle}"
        assert needle not in page, f"marketplace_cart.html has grown a card surface: {needle}"
    # And the redirect really is the mechanism, not a leftover comment about one.
    assert "checkout_url" in source
    assert "window.location.href" in source


def test_the_checkout_request_takes_the_hosted_lane_by_omission():
    """`payment_mode` is absent on purpose, and absence is not an oversight.

    `normalize_marketplace_payment_mode(None)` answers `card`, and `card` without
    the `payment_sheet` spelling is the hosted lane. Sending `payment_sheet` here
    would ask for a PaymentIntent and a native sheet -- there is no sheet in a
    browser, and the page would get a client secret it has nothing to do with.
    """
    source = (ROOT / "static" / "js" / "pulsesoc_cart.js").read_text(encoding="utf-8")
    assert "payment_sheet" not in source
    assert "client_secret" not in source
    assert re.search(r"payment_mode\s*:", source) is None, (
        "the cart script sends a payment_mode; omitting it is what selects the "
        "hosted Stripe Checkout Session")

    from services import marketplace_payment_pause
    assert marketplace_payment_pause.normalize_marketplace_payment_mode(None) == "card"


# ---------------------------------------------------------------------------
# 4. The page and the payload
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def buyer():
    """One approved seller with one live digital listing, and a buyer who is not them.

    Digital on purpose: it is the one kind with no questions, so a checkout
    assertion that fails is failing about the thing it names rather than about an
    address the fixture forgot to supply.
    """
    with bot.webhook_app.app_context():
        bot.init_db()
    conn = bot.db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO users (username, email, password_hash) VALUES (?,?,?)",
        ("cartbuyer", "cartbuyer@example.com", "x"))
    buyer_id = cur.lastrowid
    cur.execute(
        "INSERT INTO users (username, email, password_hash) VALUES (?,?,?)",
        ("cartseller", "cartseller@example.com", "x"))
    seller_id = cur.lastrowid
    # `approved` on both, because `_line_state` reports `restricted` for anything
    # less and every assertion below about a payable group would silently be
    # testing the refusal path instead.
    cur.execute(
        "INSERT INTO marketplace_sellers (user_id, status, business_name, display_name) "
        "VALUES (?,?,?,?)",
        (seller_id, "approved", "Probe Store", "Probe Store"))
    cur.execute(
        """INSERT INTO marketplace_listings
           (seller_user_id, title, description, category, price_label, currency,
            quantity, status, approval_status, delivery_type)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (seller_id, "Probe Widget", "A widget, for probing.", "Education",
         "$19.99", "USD", 5, "active", "approved", "digital"))
    listing_id = cur.lastrowid
    conn.commit()

    client = bot.webhook_app.test_client()
    with client.session_transaction() as session:
        session["account_user_id"] = buyer_id
    return client, listing_id, seller_id


def test_the_cart_payload_carries_the_groups_the_lane_transacts_in(buyer):
    """Additive on the existing endpoint, which is why there is no `/api/web/cart`.

    A client that renders one flat list ignores `groups` entirely, so the native
    app is unaffected. And the groups are computed by the module that owns the
    rules rather than by each caller -- a browser deriving them would be deriving
    them from a copy.
    """
    client, listing_id, seller_id = buyer
    empty_cart(client)
    client.post(CART_API, json={"listing_id": listing_id, "qty": 2}, headers=HTTPS)

    payload = client.get(CART_API, headers=HTTPS).get_json()
    assert payload["lines"], "the fixture cart is empty, so the rest proves nothing"
    groups = payload["groups"]
    assert len(groups) == 1
    group = groups[0]
    assert group["seller_user_id"] == seller_id
    assert group["item_count"] == 2
    assert group["line_ids"] == [l["line_id"] for l in payload["lines"]]
    assert group["checkoutable"] is True
    assert group["reason"] == ""
    # The old keys are still there. This is the compatibility claim, asserted.
    assert payload["badge_count"] == 2
    assert "checkoutable_count" in payload

    # And it survives the round trip as JSON, which is not free: a frozenset or a
    # tuple in there would raise on serialisation, and the route would 500.
    json.dumps(payload)


def test_a_web_checkout_ends_at_a_stripe_hosted_session(buyer):
    """The mission, in one assertion: the website's funnel now reaches the money.

    No `payment_mode` is sent, which normalises to `card`, which -- without the
    `payment_sheet` spelling -- is the hosted lane. The answer is a URL the page
    navigates to, so the card number is typed on Stripe's page and never on this
    origin.

    The Stripe call is replaced by a dict, so this exercises everything the lane
    does *up to* the provider and nothing beyond it: no key, no account, no card,
    no charge. What it pins is the shape of the answer and the two return URLs,
    because those point back at web routes and are the reason the hosted lane was
    already usable from a browser.
    """
    client, listing_id, seller_id = buyer
    empty_cart(client)
    client.post(CART_API, json={"listing_id": listing_id, "qty": 1}, headers=HTTPS)

    with _card_rail_open() as created:
        answer = client.post(CHECKOUT_API, json={
            "seller_user_id": seller_id,
            "fulfillment_details": {},
            "idempotency_key": "web-cart-test-1",
        }, headers=HTTPS)

    assert answer.status_code == 200, answer.get_data(as_text=True)
    payload = answer.get_json()
    assert payload["checkout_url"] == STUB_SESSION_URL
    assert payload["transaction_ids"], "a session was created against no transaction"
    # No client secret and no publishable key: that is the native sheet's payload,
    # and a browser receiving it would have a PaymentIntent it cannot present.
    assert "payment_intent_client_secret" not in payload

    assert len(created) == 1, f"expected one Stripe session, got {len(created)}"
    kwargs = created[0]
    assert kwargs["mode"] == "payment"
    primary = payload["transaction_ids"][0]
    assert kwargs["success_url"].endswith(f"/pulse/payments/success?transaction_id={primary}")
    assert kwargs["cancel_url"].endswith(f"/pulse/payments/cancel?transaction_id={primary}")
    # The amount is the server's snapshot, not a number that travelled through the
    # browser. Price manipulation has nowhere to enter: the request body carries a
    # seller id and an address, and no money at all.
    assert kwargs["line_items"][0]["price_data"]["unit_amount"] == 1999


def test_the_cart_is_not_emptied_by_starting_a_checkout(buyer):
    """An abandoned Stripe session must leave the basket alone.

    Lines are cleared by `checkout.session.completed`, not by the redirect -- so a
    buyer who backs out at Stripe comes back to the cart they left. The cancel page
    says exactly this, and this is the assertion behind that sentence.
    """
    client, listing_id, seller_id = buyer
    empty_cart(client)
    client.post(CART_API, json={"listing_id": listing_id, "qty": 1}, headers=HTTPS)
    before = client.get(CART_API, headers=HTTPS).get_json()["lines"]

    with _card_rail_open():
        started = client.post(CHECKOUT_API, json={
            "seller_user_id": seller_id, "fulfillment_details": {},
            "idempotency_key": "web-cart-test-abandon",
        }, headers=HTTPS)
    assert started.status_code == 200, started.get_data(as_text=True)

    after = client.get(CART_API, headers=HTTPS).get_json()["lines"]
    assert [l["line_id"] for l in after] == [l["line_id"] for l in before]


def test_a_buyer_cannot_check_out_a_seller_group_that_is_not_in_their_cart(buyer):
    """`seller_user_id` is the only thing the body chooses, so it is the IDOR surface.

    The lane re-reads the buyer's own lines and filters them by that id, so a
    stranger's seller id selects nothing rather than selecting their basket. A 404
    and not a 403: from the buyer's side there is genuinely nothing there.
    """
    client, listing_id, _seller_id = buyer
    empty_cart(client)
    client.post(CART_API, json={"listing_id": listing_id, "qty": 1}, headers=HTTPS)

    with _card_rail_open() as created:
        refused = client.post(CHECKOUT_API, json={
            "seller_user_id": 987654, "fulfillment_details": {},
        }, headers=HTTPS)
    assert refused.status_code == 404
    assert (refused.get_json() or {}).get("error_code") == "NOT_FOUND"
    assert created == [], "a Stripe session was created for a seller with no lines"


def test_the_checkout_lane_still_refuses_an_anonymous_caller():
    """`_require_user()` accepts the web session cookie, and only that.

    The cart routes were the app's, and the web reuses them precisely because that
    resolution already worked for a cookie. Which makes "no cookie, no checkout"
    the assertion that reuse did not widen anything.
    """
    anonymous = bot.webhook_app.test_client()
    with _card_rail_open() as created:
        response = anonymous.post(CHECKOUT_API, json={"seller_user_id": 1}, headers=HTTPS)
    assert response.status_code in (401, 403), response.get_data(as_text=True)
    assert created == []


def test_the_cart_page_injects_both_registry_hrefs(buyer):
    """The two links the script needs, and neither is written in the script.

    `app_href` must be the `/open/...` interstitial rather than the canonical
    marker link: this button is tapped from pulsesoc.com, and iOS does not consult
    associated domains for a same-domain tap, so a marker link would 302 an
    installed member to the App Store.
    """
    client, _listing_id, _seller_id = buyer
    body = client.get("/pulse/cart", headers=HTTPS).get_data(as_text=True)
    assert f'data-cart-app-href="{app_links.open_interstitial_url("cart", source="web")}"' in body
    assert f'data-cart-product-href="{app_links.website_href_template("product", source="web")}"' in body
    assert "/open/cart" in body
    assert "pulse_app=1" not in body


def test_the_empty_and_the_failed_cart_are_different_panels(buyer):
    """The standing rule: error and empty never co-render.

    The lines are drawn by JavaScript, so the first thing that breaks that rule is
    a page whose "no items" panel is also its loading state -- a buyer whose fetch
    failed would be told their cart is empty. So the shell ships four mutually
    exclusive slots and a `needsjs` panel that is visible until the script hides
    it.
    """
    client, _listing_id, _seller_id = buyer
    body = client.get("/pulse/cart", headers=HTTPS).get_data(as_text=True)
    for slot in ("data-cart-fail", "data-cart-lines", "data-cart-empty",
                 "data-cart-summary", "data-cart-needsjs"):
        assert slot in body, f"the cart shell is missing its {slot} slot"
    # `needsjs` is the only one that is *not* hidden at first paint.
    assert re.search(r"data-cart-needsjs>", body)
    assert re.search(r"data-cart-empty hidden", body)
    assert re.search(r"data-cart-fail hidden", body)


def test_the_four_panels_are_mutually_exclusive_in_the_script():
    """One writer, one call, so "empty" and "failed" cannot both be on screen.

    Asserted on the source because the invariant is structural: `show()` takes the
    name of the panel to display and hides the other three. Four independent
    `hidden = ` assignments spread through the file is how the two panels end up
    co-rendered, and it is exactly what this shape prevents.
    """
    source = (ROOT / "static" / "js" / "pulsesoc_cart.js").read_text(encoding="utf-8")
    assert re.search(r"function show\(", source)
    # Every panel's visibility is decided inside `show`, so the assignments to
    # `.hidden` for them live in one place.
    body = source[source.index("function show("):]
    body = body[:body.index("\n  function ", 1)]
    for panel in ("fail", "lines", "empty", "summary"):
        assert panel in body, f"show() does not decide the {panel} panel"


# ---------------------------------------------------------------------------
# 5. The Stripe return pages
# ---------------------------------------------------------------------------

def _transaction(buyer_user_id, item_type="marketplace_product"):
    """A `seller_transactions` row, written the way `cart_checkout` writes one.

    `item_type` and `buyer_user_id` are the two columns `_marketplace_order_return`
    reads, and they are the point of every test below; the rest are here so the
    row is a plausible one rather than a stub the real lookup would reject.
    """
    conn = bot.db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO seller_transactions (seller_user_id, buyer_user_id, seller_type, "
        "item_type, item_id, amount_cents, currency, status) VALUES (?,?,?,?,?,?,?,?)",
        (1, buyer_user_id, "merchant", item_type, 1, 1999, "USD", "created"))
    tx_id = cur.lastrowid
    conn.commit()
    return tx_id


def test_the_success_page_tells_a_marketplace_buyer_where_the_order_went(buyer):
    """Both result URLs are shared, so the copy is specialised only where it is proved.

    The generic page said "if this was a course or product, access will update
    shortly" and pointed at the feed, which is useless to someone who just bought
    a widget. The Marketplace copy names the orders page instead -- and says
    Stripe *received* the payment rather than that the order is confirmed,
    because the order becomes an order when `checkout.session.completed` arrives
    and this page has not been told that it has.
    """
    client, _listing_id, _seller_id = buyer
    with client.session_transaction() as session:
        buyer_id = session["account_user_id"]
    tx_id = _transaction(buyer_id)

    body = client.get(f"/pulse/payments/success?transaction_id={tx_id}",
                      headers=HTTPS).get_data(as_text=True)
    assert "/pulse/orders" in body
    assert "Stripe has taken your payment" in body
    assert "confirmed" not in body.lower().replace("confirmation", ""), (
        "the success page claims the order is confirmed, which it has not been told")


def test_the_cancel_page_sends_a_buyer_back_to_the_cart_they_left(buyer):
    """The generic page dead-ended at the feed, which is the one place they were not going.

    Nothing was lost on the way out -- `cart_checkout` empties no line and the
    reservation it took is released by the sweeper -- so the page can say so
    without qualification.
    """
    client, _listing_id, _seller_id = buyer
    with client.session_transaction() as session:
        buyer_id = session["account_user_id"]
    tx_id = _transaction(buyer_id)

    body = client.get(f"/pulse/payments/cancel?transaction_id={tx_id}",
                      headers=HTTPS).get_data(as_text=True)
    assert "/pulse/cart" in body
    assert "No payment was taken" in body


@pytest.mark.parametrize("query", [
    "",
    "?transaction_id=",
    "?transaction_id=0",
    "?transaction_id=-1",
    "?transaction_id=not-a-number",
    "?transaction_id=99999999",
    "?transaction_id=1%20OR%201=1",
])
def test_an_unprovable_return_gets_the_generic_copy_and_never_an_error(buyer, query):
    """`?transaction_id=` is attacker-controlled, and this is the fall-through.

    Every one of these has to land on the wording that has been correct for every
    flow since before the cart existed. A 500 here happens *after* a real charge,
    which is the worst possible moment for a stack trace; and a page that
    specialised on the query string alone would confirm a stranger's order to
    whoever guessed the number.
    """
    client, _listing_id, _seller_id = buyer
    for path in ("/pulse/payments/success", "/pulse/payments/cancel"):
        response = client.get(path + query, headers=HTTPS)
        assert response.status_code == 200, f"{path}{query} answered {response.status_code}"
        body = response.get_data(as_text=True)
        assert "/pulse/orders" not in body, (
            f"{path}{query} was specialised for a Marketplace order it cannot prove")


def test_another_buyers_transaction_does_not_specialise_the_page(buyer):
    """The IDOR, stated. The row is the proof, and the row names its buyer.

    A signed-in member handing in someone else's transaction id must see exactly
    what a stranger sees. This is the assertion that the check is on
    `buyer_user_id` and not merely on "is there a row".
    """
    client, _listing_id, _seller_id = buyer
    conn = bot.db()
    cur = conn.cursor()
    cur.execute("INSERT INTO users (username, email, password_hash) VALUES (?,?,?)",
                ("othercartbuyer", "othercartbuyer@example.com", "x"))
    stranger_id = cur.lastrowid
    conn.commit()
    tx_id = _transaction(stranger_id)

    body = client.get(f"/pulse/payments/success?transaction_id={tx_id}",
                      headers=HTTPS).get_data(as_text=True)
    assert "/pulse/orders" not in body, (
        "the success page confirmed a transaction belonging to another account")


def test_a_non_marketplace_transaction_of_the_viewers_keeps_the_generic_copy(buyer):
    """Ownership alone is not enough: a course is the viewer's too.

    Both halves of the check are load-bearing, and this is the one a test suite
    usually forgets. The Marketplace copy talks about a seller shipping a thing;
    said to someone who just bought a course it is simply wrong.
    """
    client, _listing_id, _seller_id = buyer
    with client.session_transaction() as session:
        buyer_id = session["account_user_id"]
    tx_id = _transaction(buyer_id, item_type="course")

    body = client.get(f"/pulse/payments/success?transaction_id={tx_id}",
                      headers=HTTPS).get_data(as_text=True)
    assert "/pulse/orders" not in body
    assert "access will update shortly" in body


def test_an_anonymous_visitor_is_told_nothing_about_the_order(buyer):
    """No session, no proof, and nothing about the transaction on the way out.

    `pulse_social_shell` bounces an anonymous visitor to `/login`, which is the
    shared behaviour of every page it renders and predates the cart. So the
    assertion is about what does *not* travel: whoever guesses a transaction id
    without a cookie learns nothing from the response, not even that the id exists.

    Worth recording what this also shows, because it is a real rough edge and it is
    not the cart's: the redirect's `next` drops the query string, so a buyer whose
    session lapsed during checkout signs back in and lands on the *generic*
    success page. It costs them a sentence, not an order -- the order is settled by
    `checkout.session.completed`, and this page has never been more than
    informational -- so specialising it further is a separate change on a
    route shared by courses, ads and Premium.
    """
    _client, _listing_id, _seller_id = buyer
    anonymous = bot.webhook_app.test_client()
    tx_id = _transaction(1)
    response = anonymous.get(f"/pulse/payments/success?transaction_id={tx_id}",
                             headers=HTTPS)
    assert response.status_code == 302
    assert "/login" in response.headers.get("Location", "")
    body = response.get_data(as_text=True)
    assert "/pulse/orders" not in body
    assert str(tx_id) not in body


# ---------------------------------------------------------------------------
# 6. Mutation: each guard above can actually fail
# ---------------------------------------------------------------------------

def test_mutation_the_group_verdicts_are_not_all_the_same_answer():
    """A `group_lines` that returned `checkoutable: False` for everything would
    satisfy most of section 1, and a `True` for everything would satisfy the
    rest. So both verdicts have to be reachable from this file's own builders.
    """
    assert only([line(1)])["checkoutable"] is True
    assert only([line(1, state="sold")])["checkoutable"] is False
    assert only([line(1)])["reason"] == ""
    assert only([line(1, state="sold")])["reason"] != ""


def test_mutation_the_field_leak_scan_would_notice_a_leak():
    """`test_the_page_names_no_buyer_field_of_its_own` is a substring scan, and a
    substring scan over an empty needle list passes against anything. This pins
    that the needles exist and that the scan matches when they are present.
    """
    keys = [f["key"] for f in fulfillment.buyer_form("shipping")]
    assert "address_line1" in keys
    pretend_source = "var el = form.querySelector('[data-field=address_line1]');"
    assert [k for k in keys if k in pretend_source] == ["address_line1"]


def test_mutation_the_registry_substitution_could_have_failed(buyer):
    """The counterpart to `test_the_cart_links_products_through_the_registry`.

    That test patches the registry and looks for the sentinel. If the page did not
    render the attribute at all, the assertion would fail for the wrong reason and
    read as a caught bug. This proves the attribute is present unpatched, so a
    failure there means the page stopped *asking*, not that the page stopped
    rendering.
    """
    client, _listing_id, _seller_id = buyer
    body = client.get("/pulse/cart", headers=HTTPS).get_data(as_text=True)
    assert "data-cart-product-href=" in body
    assert "/open/product/__FROM_THE_REGISTRY__" not in body


def test_mutation_the_return_page_specialisation_is_reachable(buyer):
    """`test_an_unprovable_return_gets_the_generic_copy` asserts an absence, and an
    absence passes for a route that never specialises at all. The owning case
    above is the positive control, and this asserts the two really differ.
    """
    client, _listing_id, _seller_id = buyer
    with client.session_transaction() as session:
        buyer_id = session["account_user_id"]
    mine = _transaction(buyer_id)

    specialised = client.get(f"/pulse/payments/success?transaction_id={mine}",
                             headers=HTTPS).get_data(as_text=True)
    generic = client.get("/pulse/payments/success", headers=HTTPS).get_data(as_text=True)
    assert specialised != generic
    assert "/pulse/orders" in specialised
    assert "/pulse/orders" not in generic


# ---------------------------------------------------------------------------
# 6. The payment handoff: what the button says, against what the button does
# ---------------------------------------------------------------------------
#
# A real buyer reached `/pulse/cart`, filled in a name and a delivery address,
# and pressed a button labelled `Pay $94.98`. That button does not pay. It POSTs
# to `/api/pulse/marketplace/cart/checkout`, which creates a Stripe Checkout
# Session, and then sets `window.location.href` to Stripe's hosted page -- no
# card is entered on this origin, no charge is authorised by the click, and the
# amount in the label was a promise made by a navigation.
#
# The copy is now forward-navigation language, and these tests hold it there.
# They are written against the script's source rather than a rendered DOM for
# the same reason the four panel tests above are: there is no DOM in this suite,
# and the thing worth pinning is the sentence the script is built to emit.
#
# Every one of them is an assertion about *honesty*, not about wording. They
# permit any label that does not claim a payment and do not demand one exact
# string, except where the string is the claim.

CART_JS = ROOT / "static" / "js" / "pulsesoc_cart.js"


def _emitted(text: str) -> str:
    """The script with its `//` comments removed.

    Several assertions below are "this phrase must not appear", and a comment
    explaining *why* the phrase must not appear contains the phrase. Stripping
    the commentary is what keeps those assertions about the sentences the script
    emits rather than about the prose around them -- and the stripping is
    line-based and deliberately crude, because the only thing it has to get
    right is that a `//` line is not shipped text. A `//` inside a string
    literal would be mis-stripped; this file has none, and the assertions it
    feeds are all absence tests, so a mis-strip can only make them stricter.
    """
    return "\n".join(line for line in text.splitlines()
                      if not line.lstrip().startswith("//"))

#: The submit button as `checkoutFormHtml` assembles it: the opening tag, the
#: `pending` ternary, and then the label on the following line.
SUBMIT_BUTTON = re.compile(
    r"<button type='submit'.*?\+\s*\n\s*\"(?P<label>.*?)</button>", re.S)


def test_the_checkout_button_does_not_claim_to_take_a_payment():
    """The label is a navigation, because the click is a navigation.

    "Pay $94.98" is a statement about money leaving an account. This click
    creates a Stripe session and redirects; the money leaves on Stripe's page,
    after a second deliberate action the buyer takes there. A label that cannot
    be distinguished from the real pay button two pages later is how a buyer
    ends up believing they have paid when they have not, and how one did.
    """
    source = CART_JS.read_text(encoding="utf-8")
    match = SUBMIT_BUTTON.search(source)
    assert match, (
        "the checkout form no longer renders a single `type='submit'` button in "
        "the shape this test reads; the label contract is unpinned")
    label = match.group("label")
    assert label.strip(), "the submit button has an empty label"

    assert "Continue to secure payment" in label, (
        f"the checkout button is labelled {label!r}, which does not tell the "
        "buyer that the next thing they see is a payment page they have not "
        "reached yet")
    assert not re.match(r"(?i)^\s*(&\w+;)?\s*pay\b", label), (
        f"the checkout button opens with a payment claim: {label!r}")
    assert "money(" not in label, (
        "the checkout button interpolates the amount into its own label. The "
        "amount belongs on the subtotal row and on Stripe's page, which are the "
        "two places it is authoritative; inside this button it reads as a charge")


def test_the_handoff_sentence_is_above_the_button_not_below_it():
    """Order on the page is order of reading, and the correction has to come first.

    The explanation already existed -- "you will finish on Stripe's secure
    payment page" -- and sat underneath a button that said "Pay". A buyer
    reading downwards met the promise, acted on it, and met the correction
    afterwards, if at all. Being present is not the same as being read.
    """
    source = CART_JS.read_text(encoding="utf-8")
    form = source[source.index("function checkoutFormHtml("):]
    form = form[:form.index("\n  // The app handoff")]

    next_line = form.index("class='next'")
    button = form.index("<button type='submit'")
    assert next_line < button, (
        "the 'next step' line is emitted after the submit button, so a buyer "
        "reading top to bottom reaches the button before the explanation of "
        "what it does")
    assert "Next: secure payment" in form


def test_the_loading_state_describes_opening_a_page_not_taking_money():
    """What the buyer reads during the round trip, which is the most ambiguous moment.

    The POST can take seconds -- it validates the destination, the variant and
    the supplier before it reaches Stripe -- and whatever the button says during
    that window is the buyer's only account of what is happening to their money.
    """
    source = _emitted(CART_JS.read_text(encoding="utf-8"))
    assert "Opening secure payment" in source, (
        "the in-flight button text does not say what is being opened")
    for vague in ("Starting checkout", "Paying", "Processing payment", "Charging"):
        assert vague not in source, (
            f"the in-flight button text says {vague!r}, which either says nothing "
            "or says money is moving; neither is true")


def test_a_failed_session_restores_the_form_and_never_reports_a_failed_payment():
    """A session that could not be created is not a payment that failed.

    They have different remedies and different consequences, and the buyer is
    owed the difference: nothing was attempted, nothing was charged, nothing was
    removed from the cart, and everything they typed is still on screen.
    """
    source = CART_JS.read_text(encoding="utf-8")
    # The window starts at `handoffFailureMessage`, not at `checkout`, because
    # that is where the rejected-request sentence is now composed. Slicing from
    # `checkout` alone would read a handler whose only failure copy is a call to
    # a helper, and would then conclude the copy had been deleted -- which is
    # what it did conclude. The two refusal paths are the resolved-but-unusable
    # one (inline, below) and the rejected one (the helper), and this window has
    # to span both or it is testing half the behaviour.
    handler = source[source.index("  function handoffFailureMessage(err)"):]
    handler = handler[:handler.index("\n  // ---")]
    said = _emitted(handler)

    # The CTA comes back, with its original label, so the form is usable again.
    assert "function restore()" in handler
    assert "button.innerHTML = label" in handler, (
        "the button is re-enabled without its label being restored, so a failed "
        "attempt leaves 'Opening secure payment…' on a button that is not")
    assert handler.count("restore();") >= 2, (
        "only one of the two failure paths restores the form")
    # Without this the helper could sit in the window unreferenced while the
    # catch path emitted a bare `err.message`, and every assertion below would
    # pass by reading copy that never reaches a buyer.
    assert "groupError(sellerId, handoffFailureMessage(err), err && err.cta)" in handler, (
        "the rejected-request path no longer routes through "
        "handoffFailureMessage *and* the server's cta verdict, so either the "
        "sentences asserted below are dead copy or the CTA state machine is "
        "being driven by something other than the server's answer")

    # And it never says the payment failed, because no payment was attempted.
    for claim in ("payment failed", "payment was declined", "your card was",
                  "charge failed"):
        assert claim not in said.lower(), (
            f"the failure copy claims {claim!r}; the session was never created, "
            "so there was no payment to fail")
    # Both refusal paths state the absence rather than leaving it to be
    # inferred. A rejected request reaches the buyer as the server's own
    # sentence plus an appended clause, so "charged" has to survive in the
    # helper as well as in the inline message.
    assert said.lower().count("charged") >= 2
    assert "charged" in _emitted(
        handler[:handler.index("  function findGroup(sellerId)")]).lower(), (
        "handoffFailureMessage no longer answers the only question a refused "
        "buyer has, which is whether their money moved")


def test_the_cart_draws_no_step_it_has_no_authority_over():
    """The indicator is a progress indicator, not a wizard this page drives.

    Steps three and four are Stripe's page and the webhook's confirmation. This
    origin learns about neither: the session URL is a redirect, and
    `checkout.session.completed` arrives on the server. A page that drew itself
    as having reached "Payment" would be asserting something it cannot observe,
    which is the same class of claim as the button label above.
    """
    source = CART_JS.read_text(encoding="utf-8")
    assert "function stepsHtml(" in source
    calls = re.findall(r"stepsHtml\((\d+)", source)
    assert calls, (
        "stepsHtml is defined and never called, so the progress indicator is "
        "dead code and this test proves nothing")
    assert set(calls) <= {"1", "2"}, (
        f"the cart draws checkout step(s) {sorted(set(calls) - {'1', '2'})} as "
        "reached. Steps 3 and 4 belong to Stripe and to the webhook")


def test_every_details_kind_the_server_can_emit_has_its_own_step_name():
    """"Delivery" over a pickup form is a false statement about where the order goes.

    `details_kind_for` is the authority, and it can answer any shipping-address
    kind, any scheduled kind, `pickup`, or `""` for a group it asks nothing of.
    The table is enumerated from that function's own inputs rather than from a
    list written here, so a tenth fulfilment kind added in Python fails this
    test instead of silently rendering as the generic noun.
    """
    source = CART_JS.read_text(encoding="utf-8")
    table = source[source.index("var DETAIL_COPY"):source.index("var DETAIL_FALLBACK")]

    emittable = {cart_web.details_kind_for([kind])
                 for kind in fulfillment.KINDS
                 if kind not in fulfillment.UNDECIDED_KINDS}
    assert emittable, "no fulfilment kinds, so this test proves nothing"
    assert "" in emittable, (
        "details_kind_for no longer answers '' for a group it asks nothing of; "
        "the empty-key entry in DETAIL_COPY may now be unreachable")

    missing = sorted(kind for kind in emittable
                     if f'"{kind}":' not in table and f"{kind}: {{" not in table)
    assert not missing, (
        f"details_kind {missing} reaches the cart with no step name of its own, "
        "so the form is headed by the generic noun and the progress indicator "
        "says 'Details' where it should say what is actually being collected")


def test_the_progress_strip_on_the_return_pages_never_marks_confirmation_reached():
    """The success page is reached by Stripe's `success_url`, which proves nothing.

    `success_url` fires on redirect. It is not a payment result, it is not
    signed, and it is reachable by typing the URL. The order becomes an order
    when `checkout.session.completed` arrives at the webhook -- so the fourth
    dot is drawn as waiting and never as done, which is the visual half of the
    sentence the copy already makes.
    """
    strip = bot._checkout_progress_html(3, pending=4)
    cells = [cell for cell in strip.split("<li") if "Confirmation" in cell]
    assert len(cells) == 1, strip
    assert "&#10003;" not in cells[0], (
        "the success page ticks Confirmation, which only the webhook can do")
    # Named by token, because that is what the helper emits. It used to emit
    # `#2ecc71`; bot.py's hardcoded-colour ratchet refuses new hex literals and
    # counts `var(--x, #abc123)` fallbacks too, so the literal went and this
    # assertion would have passed vacuously against a strip that still drew
    # Confirmation as done.
    done_colour = "var(--status-success)"
    assert done_colour in strip, (
        f"the strip no longer draws any step in {done_colour}, so asserting its "
        "absence on Confirmation proves nothing about Confirmation")
    assert done_colour not in cells[0], (
        "the success page draws Confirmation in the done colour")

    # The cancel page: the buyer reached Stripe and came back without paying, so
    # Payment is neither done nor in flight.
    cancelled = bot._checkout_progress_html(2)
    payment = [cell for cell in cancelled.split("<li") if "Payment" in cell]
    assert len(payment) == 1
    assert "&#10003;" not in payment[0], (
        "the cancel page ticks Payment after a checkout that took no money")
    assert "&hellip;" not in payment[0], (
        "the cancel page draws Payment as in flight; nothing is in flight")


def test_the_return_pages_actually_render_the_strip(buyer):
    """The positive control for the test above, which asserts absences.

    An absence passes for a page that renders no indicator at all.
    """
    client, _listing_id, _seller_id = buyer
    with client.session_transaction() as session:
        buyer_id = session["account_user_id"]
    tx_id = _transaction(buyer_id)

    for path in ("success", "cancel"):
        body = client.get(f"/pulse/payments/{path}?transaction_id={tx_id}",
                          headers=HTTPS).get_data(as_text=True)
        assert "Checkout progress" in body, f"the {path} page renders no progress strip"
        assert "Confirmation" in body


def test_a_second_tap_cannot_reach_the_checkout_post():
    """The double-submit guard, asserted as an ordering fact rather than a wish.

    Measured in a browser at a true 390px viewport, three clicks on the CTA in
    one task produce exactly one `POST /cart/checkout`. But that measurement
    does not say *which* guard did it, and the difference matters: with the
    `ui.busy` early-return removed and `button.disabled = true` left in place,
    three clicks still produced one POST -- the synchronous `disabled` absorbed
    them. With `disabled` removed and `ui.busy` left, three clicks also produced
    one POST. Only with both removed did three clicks produce three POSTs.

    So `disabled` alone is enough for a tap that lands on the same rendered
    button, and that is exactly the case it does not have to survive: any
    re-render between the two taps replaces the element and the attribute with
    it, while `ui.busy` is module-scoped and does not care. A refactor that
    deletes the early-return would keep every browser-level double-tap test
    green and leave the real race open.

    Hence an ordering assertion on the source: the flag must be read before the
    function can proceed and written before anything awaits.
    """
    source = CART_JS.read_text(encoding="utf-8")
    handler = source[source.index("  function checkout(sellerId)"):]
    handler = handler[:handler.index("\n  // ---")]

    # Named before they are located, so a handler that lost one of them fails
    # saying which. `str.index` raises ValueError, which pytest reports as an
    # error rather than a failure: still red, but red for the wrong reason, and
    # the kind of red a reader resolves by deleting the test.
    for fragment, what in (
            ("ui.busy) return;", "the early return that makes a second call a no-op"),
            ("ui.busy = true;", "the claim that arms that early return"),
            ('pulseApi("/api/pulse/marketplace/cart/checkout"', "the checkout POST")):
        assert fragment in handler, (
            f"checkout() no longer contains {what} ({fragment!r}), so two taps "
            "can start two Stripe sessions for the same cart")

    guard = handler.index("ui.busy) return;")
    claim = handler.index("ui.busy = true;")
    post = handler.index('pulseApi("/api/pulse/marketplace/cart/checkout"')

    assert guard < claim < post, (
        "checkout() does not read ui.busy, claim it, and only then POST, in "
        f"that order (read at {guard}, claimed at {claim}, POST at {post}) -- "
        "a second tap landing between the claim and the POST would start a "
        "second Stripe session for the same cart")

    # Nothing may await between the read and the claim: an await there reopens
    # the window the flag exists to close.
    between = handler[guard:claim]
    for yielding in ("await ", "pulseApi(", "setTimeout(", ".then("):
        assert yielding not in between, (
            f"checkout() yields on {yielding!r} between reading ui.busy and "
            "setting it, so two taps can both pass the guard")

    # And the visible half must not be mistaken for the enforcement: the
    # disabled attribute is set after the flag, not instead of it.
    assert handler.index("button.disabled = true") > claim, (
        "the button is disabled before ui.busy is claimed, which puts the "
        "enforcement on an element a re-render can replace")


def test_mutation_the_double_submit_ordering_is_actually_checked():
    """The positive control: the assertion above fails on a handler that lost the flag.

    An ordering test on substrings passes vacuously if the substrings it looks
    for stop existing, because `str.index` raises and pytest reports an error
    rather than a failure -- which is still red, but red for the wrong reason
    and easy to "fix" by deleting the test.
    """
    source = CART_JS.read_text(encoding="utf-8")
    handler = source[source.index("  function checkout(sellerId)"):]
    handler = handler[:handler.index("\n  // ---")]

    broken = handler.replace("if (!group || ui.busy) return;", "if (!group) return;")
    assert "ui.busy) return;" not in broken, (
        "the mutation did not remove the guard, so this control proves nothing")
    with pytest.raises(ValueError):
        broken.index("ui.busy) return;")

    moved = handler.replace("ui.busy = true;", "", 1)
    assert "ui.busy = true;" not in moved


def test_a_refusal_message_survives_the_redraw_that_immediately_follows_it():
    """Both refusal paths call `load()` on the line after they write the message.

    `load()` refetches and redraws the group, and the redraw re-emits
    `[data-group-error]` from `checkoutFormHtml`. A message written only into the
    DOM element is therefore erased in the same tick it was written -- which is
    what a browser measured on the rejected-session path: the form came back with
    all nine fields still filled, the button restored, the cart intact, and no
    sentence anywhere saying what had happened. Silence is what made the original
    customer tap the button six times.

    So the message has to live in `ui`, next to `ui.typed`, which is module-scoped
    for the same reason, and the renderer has to read it back.
    """
    source = CART_JS.read_text(encoding="utf-8")

    # The store exists and is keyed per seller: two groups can refuse
    # independently and one must not overwrite the other's explanation.
    # Not `[^}]*`: the literal's earlier members are themselves `{}`, so a
    # no-closing-brace class stops inside `lane: {}` and reports the store
    # missing when it is three members further along.
    assert re.search(r"var ui = \{.*?\berror: \{\}", source), (
        "`ui` has no per-seller error store, so a refusal has nowhere to live "
        "across the redraw that follows it")

    writer = source[source.index("  function groupError(sellerId, message, cta)"):]
    writer = writer[:writer.index("\n  function ")]
    assert "ui.error[sellerId] = message" in writer, (
        "groupError paints the element without recording the message, so the "
        "load() on the next line erases it")
    # Recorded BEFORE it is painted: the painting is the half that does not
    # survive, so an ordering where the store is a trailing afterthought is one
    # early return away from being skipped.
    assert writer.index("ui.error[sellerId] = message") < writer.index("querySelector"), (
        "the message is stored after the element is looked up, so the "
        "no-element branch returns without recording it")

    # And the renderer reads it back, un-hidden, rather than always emitting an
    # empty hidden slot. Read through `heldError`, the single reader of the
    # store now that it holds a {message, cta} pair rather than a bare string.
    renderer = source[source.index("  function checkoutFormHtml("):]
    renderer = renderer[:renderer.index("\n  function ")]
    assert "heldError(sellerId)" in renderer, (
        "checkoutFormHtml ignores the held refusal, so storing it changes "
        "nothing that reaches the buyer")
    slot = renderer[renderer.index("data-group-error"):]
    slot = slot[:slot.index("</div>") + 6]
    assert "hidden" in slot and "held" in slot, (
        "the redrawn error slot is unconditionally empty and hidden, which is "
        "the defect this test exists to catch")

    # A new attempt clears the last verdict, so a stale refusal is not left on
    # screen next to a button that says it is working.
    handler = source[source.index("  function checkout(sellerId)"):]
    handler = handler[:handler.index("\n  // ---")]
    assert "ui.error[sellerId] = null;" in handler, (
        "a retry leaves the previous refusal on screen while the new attempt "
        "is in flight")
    assert handler.index("ui.error[sellerId] = null;") < handler.index("ui.busy = true;"), (
        "the clear runs after the busy claim rather than before it")


def test_mutation_the_held_refusal_message_is_actually_checked():
    """The assertions above fail when the behaviour they describe is removed.

    Written against the two edits that reintroduce the measured defect: dropping
    the store from `groupError`, and emitting the slot unconditionally empty.
    """
    source = CART_JS.read_text(encoding="utf-8")

    store_write = ("    ui.error[sellerId] = message\n"
                   "      ? { message: message, cta: cta === \"blocked\" ? \"blocked\" : \"retry\" }\n"
                   "      : null;")
    assert store_write in source, (
        "`groupError` no longer writes the store in the shape this mutation "
        "removes, so the mutation below deletes nothing and proves nothing")
    no_store = source.replace(store_write, "", 1)
    writer = no_store[no_store.index("  function groupError(sellerId, message, cta)"):]
    writer = writer[:writer.index("\n  function ")]
    assert "ui.error[sellerId] = message" not in writer

    renderer = source[source.index("  function checkoutFormHtml("):]
    renderer = renderer[:renderer.index("\n  function ")]
    slot = renderer[renderer.index("data-group-error"):]
    slot = slot[:slot.index("</div>") + 6]
    blanked = slot.replace("(held ? \"\" : \" hidden\")", "\" hidden\"")
    blanked = blanked.replace("(held ? groupErrorHtml(held.message) : \"\")", "\"\"")
    assert "held" not in blanked, (
        "the mutation did not actually remove the held-message read, so the "
        "assertion it is meant to break was never exercised")


# ==========================================================================
# §17-§21 -- the CTA state machine, executed rather than grepped
# ==========================================================================
#
# What a real buyer saw on 2 October 2026, on one screen, at the same time:
#
#     "Payments are temporarily unavailable. No card was charged.
#      You can try again."
#     NEXT: SECURE PAYMENT
#     [ Continue to secure payment -> ]        <- enabled
#     "You will continue to Stripe's secure payment page to pay $6.25."
#
# Four statements, of which the first contradicted the other three. The refusal
# was real and, for its cause, permanent for 24 hours; the invitation beside it
# was live and could not work. They tapped it nine times across five hours.
#
# The cause in this file was narrow and is worth naming exactly: the error slot
# was gated on the held refusal, and the three payment statements were gated on
# `pending` -- an unanswered fulfilment question, a different condition
# entirely. Two guards, one screen, no relationship between them. So a refusal
# could not switch off the thing it contradicted, because nothing connected
# them.
#
# The tests below execute `pulsesoc_cart.js` under a DOM stub and read the HTML
# it actually produces. Every other JS assertion in this file is a source grep,
# for the reason the sibling suite `tests/pulse_ads/test_web_portal_absent_states`
# states plainly: there is no JS test harness in this repository and no jsdom
# reachable from a Python test. A grep was not good enough here. The defect was
# *two guards disagreeing*, and the way a grep catches that is by naming both
# guards -- which pins the variable names and goes green the moment someone
# renames one, exactly the trap `test_23` fell into earlier in this incident.
# Running the file is the only thing that reads the relationship instead of the
# spelling.
#
# `node` only: no jsdom, no bundler, no network. The stub is ~40 lines and the
# precedent is `tests/pulse_commerce/test_commerce_card_parity.py`, which runs
# the shipped commerce renderer the same way.

#: Drives the real `pulsesoc_cart.js` through: initial load, open the checkout
#: form, submit it, have the server refuse. Prints the cart's rendered HTML for
#: three scenarios. Scenario names are the keys of the JSON on stdout.
_CTA_DRIVER = r"""
const fs = require("fs");

const SELLER = 7;
const CART = {
  lines: [{ id: 1, seller_user_id: SELLER, title: "Thing", state: "available",
            quantity: 1, unit_price_minor: 625, currency: "USD" }],
  // One digital line, so `forms[""]` resolves with no fields to fill and
  // `checkout()` reaches the POST instead of stopping on a missing address.
  groups: [{ seller_user_id: SELLER, seller_name: "A Seller", checkoutable: true,
             item_count: 1, subtotal_minor: 625, currency: "USD",
             lane_question: null, forms: { "": { kind: "digital", fields: [] } } }],
};

function makeEl(tag, attrs, parent) {
  return {
    tagName: tag, _attrs: attrs || {}, dataset: {}, parentNode: parent || null,
    innerHTML: "", textContent: "", hidden: false, disabled: false,
    getAttribute(n) { return n in this._attrs ? this._attrs[n] : null; },
    setAttribute(n, v) { this._attrs[n] = v; },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    addEventListener(type, fn) { (this._handlers = this._handlers || {})[type] = fn; },
    focus() {},
    // Enough of `closest` for the handlers: walk up looking for the attribute
    // the selector names.
    closest(sel) {
      let at = this;
      while (at) {
        if (sel.replace(/^\[|\]$/g, "").split("=")[0] in at._attrs) return at;
        at = at.parentNode;
      }
      return null;
    },
  };
}

const root = makeEl("div", { "data-cart-root": "1" });
const panels = {};
root.querySelector = (sel) => (panels[sel] = panels[sel] || makeEl("div", { sel }, root));

global.document = {
  querySelector: (sel) => (sel === "[data-cart-root]" ? root : null),
  addEventListener() {}, getElementById: () => null,
  createElement: (t) => makeEl(t, {}), body: makeEl("body", {}),
};
global.window = { location: { href: "" }, addEventListener() {} };
global.toast = () => {};

let checkoutAnswer = null;
global.pulseApi = function (url) {
  if (url.indexOf("checkout-options") >= 0) return Promise.resolve({ ok: true });
  if (url.indexOf("/cart/checkout") >= 0) return checkoutAnswer();
  if (url.indexOf("/api/pulse/marketplace/cart") >= 0) return Promise.resolve(CART);
  return Promise.resolve({});
};
global.window.pulseApi = global.pulseApi;

new Function(fs.readFileSync(process.argv[2], "utf8"))();

function fire(type, node) { root._handlers[type]({ target: node, preventDefault() {} }); }
const lines = () => panels["[data-cart-lines]"].innerHTML;

function openForm() {
  const opener = makeEl("button", { "data-open-checkout": String(SELLER) }, root);
  opener.dataset.openCheckout = String(SELLER);
  fire("click", opener);
}
function submit() {
  const form = makeEl("form", { "data-checkout": String(SELLER) }, root);
  form.dataset.checkout = String(SELLER);
  fire("submit", form);
}
// `checkout()` rejects, calls groupError, then load() -- two more promise hops.
const settle = () => new Promise((r) => setImmediate(() => setImmediate(() => setImmediate(r))));

// How `pulseApi` rejects: an Error with the response body assigned onto it,
// which is literally what static/js/pulse_runtime.js does.
function refusal(message, extra) {
  return () => Promise.reject(Object.assign(new Error(message), extra));
}

(async () => {
  const out = {};
  await settle();
  openForm();
  out.clean = lines();

  checkoutAnswer = refusal(
    "We couldn't open secure payment for this order, and trying again will not help. No card was charged.",
    { error_code: "PAYMENT_CONFIGURATION_ERROR", retryable: false, cta: "blocked" });
  submit();
  await settle();
  out.blocked = lines();

  checkoutAnswer = refusal(
    "We couldn't open secure payment. No card was charged. Please try again.",
    { error_code: "PAYMENT_CONFIGURATION_ERROR", retryable: true, cta: "retry" });
  submit();
  await settle();
  out.retryable = lines();

  // An older server, or any failure from outside the checkout lanes: no verdict
  // on the wire at all.
  checkoutAnswer = refusal("Something went wrong.", { error_code: "PAYMENT_UNAVAILABLE" });
  submit();
  await settle();
  out.no_verdict = lines();

  process.stdout.write(JSON.stringify(out));
})();
"""

#: The three statements that together constitute "payment is being offered".
#: Each was rendered unconditionally before this change.
_ACTIVE_CTA_MARKERS = (
    "Next: secure payment",
    "continue to Stripe's secure payment page to pay",
)
_DISABLED_SUBMIT = "<button type='submit' class='button primary' disabled>"
_ENABLED_SUBMIT = "<button type='submit' class='button primary'>"


def _render_cta_states(source: str | None = None) -> dict[str, str]:
    """Run the cart script under node and return {scenario: rendered html}."""
    script = source if source is not None else CART_JS.read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory() as workdir:
        driver = Path(workdir) / "driver.js"
        target = Path(workdir) / "cart.js"
        driver.write_text(_CTA_DRIVER, encoding="utf-8")
        target.write_text(script, encoding="utf-8")
        result = subprocess.run(
            ["node", str(driver), str(target)],
            capture_output=True, text=True, timeout=60,
        )
    assert result.returncode == 0, f"driver failed: {result.stderr}"
    rendered = json.loads(result.stdout)
    # Anti-vacuity: a stub that silently rendered nothing would pass every
    # absence assertion below.
    for name, html in rendered.items():
        assert "Continue to secure payment" in html, (
            f"the {name!r} scenario rendered no checkout form at all, so every "
            f"assertion about it is vacuous: {html[:200]!r}")
    return rendered


needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


@needs_node
def test_an_unrefused_checkout_offers_payment_in_full():
    """The baseline, so the two tests after it are not passing by rendering nothing."""
    clean = _render_cta_states()["clean"]
    assert _ENABLED_SUBMIT in clean, "the pay button is disabled with nothing wrong"
    for marker in _ACTIVE_CTA_MARKERS:
        assert marker in clean, f"a clean checkout form is missing {marker!r}"
    assert "class='fail'" not in clean, "a clean checkout form is showing a refusal"


@needs_node
def test_a_blocked_refusal_cannot_co_render_with_an_active_payment_cta():
    """§17/§20. The screenshot, asserted as a thing that must not happen again.

    All four of the statements the buyer saw are checked, not just the button,
    because the button was never the whole lie. "NEXT: SECURE PAYMENT" and "You
    will continue to Stripe's secure payment page to pay $6.25" are both claims
    about what happens next, and next to a refusal that cannot be retried they
    are simply false -- the second one especially, since it names an amount and
    a destination for a payment that will not occur.
    """
    blocked = _render_cta_states()["blocked"]

    assert "class='fail'" in blocked, "the refusal is not on screen at all"
    assert _DISABLED_SUBMIT in blocked, (
        "a refusal the server marked unretryable is rendered beside an enabled "
        "'Continue to secure payment' button -- this is the incident")
    for marker in _ACTIVE_CTA_MARKERS:
        assert marker not in blocked, (
            f"a blocked refusal still promises {marker!r}, which is a statement "
            "about a payment that cannot happen")

    # §22. The label survives: the button is disabled, not relabelled and not
    # removed. A control that vanishes leaves the buyer unable to tell a broken
    # order from a finished one.
    assert "Continue to secure payment" in blocked


@needs_node
def test_a_retryable_refusal_keeps_the_payment_cta_live():
    """§19, and the half that is easy to get wrong in the other direction.

    This is the incident's own failure -- our idempotency key burned against
    changed parameters. It is entirely retryable, and the buyer's very next tap
    could have succeeded. Hiding the CTA here would answer a five-hour lockout
    with a permanent one.
    """
    retryable = _render_cta_states()["retryable"]

    assert "class='fail'" in retryable, "the refusal is not on screen at all"
    assert _ENABLED_SUBMIT in retryable, (
        "a retryable refusal disabled the pay button, which strands a buyer "
        "whose next tap would have worked")
    for marker in _ACTIVE_CTA_MARKERS:
        assert marker in retryable, (
            f"a retryable refusal dropped {marker!r}, so the buyer is offered a "
            "button with no account of what it does")


@needs_node
def test_a_refusal_with_no_verdict_still_offers_a_retry():
    """The compatibility direction, which decides which way an absence fails.

    A server that sends no `cta` -- an older deployment, or a failure raised
    outside the three checkout lanes -- must not silently disable checkout.
    Defaulting to "offer payment" means this change can only ever remove a CTA
    the server explicitly refused, never withhold a legitimate one, and that is
    the only default under which a partial rollout is safe.
    """
    no_verdict = _render_cta_states()["no_verdict"]
    assert _ENABLED_SUBMIT in no_verdict, (
        "a refusal carrying no verdict disabled checkout, so any endpoint not "
        "yet threading `cta` silently breaks the buyer's ability to pay")


@needs_node
def test_mutation_the_cta_state_machine_assertions_can_fail():
    """§29, mutations 4 and 5. Both directions, against the executed renderer.

    Mutation 4 -- CTA REMAINS ACTIVE FOR NONRETRYABLE FAILURE -- is the literal
    pre-fix code: gate the payment statements on `pending` alone, as they were
    for the buyer in the incident. Mutation 5 -- CTA DISAPPEARS FOR RETRYABLE
    FAILURE -- is the overcorrection, blocking on any held refusal at all.

    Applied to the source text and re-executed, so this proves the *tests*
    discriminate, not merely that two strings differ.
    """
    source = CART_JS.read_text(encoding="utf-8")
    guard = "    var offerPayment = !pending && !blocked;"
    assert guard in source, (
        "the single guard this mutation rewrites is gone, so neither mutation "
        "below reintroduces the defect and this test proves nothing")

    # Mutation 4: the guard forgets the refusal. Exactly the shipped defect.
    regressed = _render_cta_states(
        source.replace(guard, "    var offerPayment = !pending;", 1))
    assert _ENABLED_SUBMIT in regressed["blocked"], "mutation 4 did not take effect"
    assert "Next: secure payment" in regressed["blocked"], "mutation 4 did not take effect"

    # Mutation 5: the guard forgets to ask *which* refusal.
    overcorrected = _render_cta_states(
        source.replace(guard, "    var offerPayment = !pending && !held;", 1))
    assert _DISABLED_SUBMIT in overcorrected["retryable"], "mutation 5 did not take effect"
    assert "Next: secure payment" not in overcorrected["retryable"], (
        "mutation 5 did not take effect")
