"""The storefront grid can add to the cart, and it counts the cart the way the cart does.

## What was wrong

The web storefront could show you a product and the web cart could sell it to
you, and there was no step between them. A shopper on `/pulse/marketplace` had to
open a product page to add anything, and once something was in the cart the
storefront gave no sign of it — no count, no link, nothing to say the cart the
buyer had been filling still existed. The funnel was two working halves with a
gap in the middle.

## The button is withheld, never disabled, and never optimistic

`POST /api/pulse/marketplace/cart` refuses five ways — 401 with no session,
`OWN_LISTING`, `SELLER_UNAVAILABLE`/`OUT_OF_STOCK` (409), `ITEM_UNAVAILABLE`
(400, for `price_minor <= 0`) and `CART_FULL`. `marketplace_web.cart_affordance`
mirrors the first four, so a button appears only where the lane is expected to
honour it. That is a courtesy and not a permission: the route re-derives every
one of them, which is why the tests below assert that the *facts* layer withholds
rather than that anything is authorized here.

Four of those refusals are asserted twice over — once against `cart_affordance`
and once against the rendered page — because the two can disagree. A markup layer
that derived its own answer, or forgot to pass `cart` through, would satisfy the
first set and fail the second.

## Why `buyer_visible` and not the publication rules

`cart_affordance` reads `buyer_visible` and `inventory_state` off the payload
rather than calling `marketplace_listing_lifecycle` itself. Those two keys are
`pulse_marketplace_listing_payload`'s own answers, derived through the lifecycle
rules once, for the same row, on the way to the renderer.

The alternative is a second derivation, and the reason to refuse it is measured:
`PublicationRule.satisfied` is **three-valued**, and the `seller_approved` rule
fails closed when a row was never projected with `seller_status`. So two copies
handed two differently-shaped views of one listing answer differently — and the
copy in a facts module, which takes a serialized payload, is exactly the one that
would be looking at the wrong shape. `seller_status` does survive the serializer
today (it is not in `MARKETPLACE_REVIEWER_ONLY_FIELDS`, and the row is spread),
which is *why* reading the derived key is safe; it is not a reason to re-derive.

`test_a_payload_that_never_saw_the_serializer_gets_no_button` pins the fail-closed
direction, because it is the direction that costs a buyer nothing.

## Why the count is passed in and not computed

The header count and the cart page's count are one number seen one click apart, so
there is exactly one definition of it: `marketplace_cart_routes.badge_count`. It
counts `available`, `price_changed` and `low_stock` and no others — a sold-out line
is still in the cart and still listed, but a badge is a promise about what is
waiting to be paid for.

That function is new here only in the sense that the expression was previously
written out twice inline, in `cart_list` and in `cart_add`, where it agreed by
coincidence. `test_both_cart_endpoints_report_the_same_count_function` pins that
they now share it.

## `cart_count=None` is not zero

`None` means the caller did not wire the cart up, and suppresses the link and
every button. `0` means the cart is genuinely empty and renders the link with the
count hidden. The distinction is what lets `bot.marketplace_storefront_cart_count`
answer `None` for a read that *failed* rather than claiming an empty cart — the
standing rule that error and empty must never render as the same thing, applied to
a badge.
"""

from __future__ import annotations

import inspect
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services import app_links  # noqa: E402
from services import marketplace_cart_routes as cart_routes  # noqa: E402
from services import marketplace_storefront as sf  # noqa: E402
from services import marketplace_web as mw  # noqa: E402

JS = (ROOT / "static" / "js" / "pulse_marketplace.js").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "css" / "pulse_marketplace.css").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Builders
#
# `payload` returns a listing shaped the way `pulse_marketplace_listing_payload`
# shapes one, because that is what the discovery route hands the renderer. The
# two derived keys are spelled out rather than defaulted in, so a test that
# removes one is visibly removing something the serializer supplies.
# ---------------------------------------------------------------------------

def payload(**over):
    built = {
        "id": 5,
        "listing_id": 5,
        "seller_user_id": 9,
        "title": "Linen Shirt",
        "price_label": "$24.00",
        "currency": "USD",
        "quantity": 4,
        "category": "Women's Clothing > Tops",
        "seller_store_name": "Atlas Goods",
        "buyer_visible": True,
        "inventory_state": "available",
    }
    built.update(over)
    return built


BUYER = sf.Viewer(user_id=3, signed_in=True)
OWNER = sf.Viewer(user_id=9, signed_in=True)
ANON = sf.Viewer()


def affordance(row=None, viewer=BUYER):
    row = payload() if row is None else row
    return mw.cart_affordance(
        row,
        price=mw.derive_price(row, []),
        signed_in=viewer.signed_in,
        viewer_user_id=viewer.user_id,
    )


def discovery(rows=None, *, viewer=BUYER, **kw):
    rows = [payload()] if rows is None else rows
    return sf.render_discovery(
        listings=rows,
        variants_by_listing={},
        filters=sf.Filters(),
        viewer=viewer,
        **kw,
    ).body_html


# ---------------------------------------------------------------------------
# The facts: when a button is offered
# ---------------------------------------------------------------------------

def test_an_ordinary_priced_listing_offers_a_button_to_a_signed_in_shopper():
    control, reason = affordance()
    assert reason == ""
    assert control is not None
    assert control.listing_id == 5


def test_the_affordance_carries_the_listing_id_the_cart_api_takes():
    """Not the row's position or a slug: `listing_id` is the key the route reads."""
    control, _ = affordance(payload(id=812, listing_id=812))
    assert control.listing_id == 812


def test_an_anonymous_reader_is_offered_nothing():
    """`_require_user()` answers 401, so the button could only ever fail."""
    control, reason = affordance(viewer=ANON)
    assert control is None
    assert reason == mw.CART_HIDDEN_ANONYMOUS


def test_a_seller_is_not_offered_their_own_listing():
    control, reason = affordance(viewer=OWNER)
    assert control is None
    assert reason == mw.CART_HIDDEN_OWN_LISTING


def test_a_listing_that_is_not_buyer_visible_offers_nothing():
    control, reason = affordance(payload(buyer_visible=False))
    assert control is None
    assert reason == mw.CART_HIDDEN_UNAVAILABLE


def test_an_out_of_stock_listing_offers_nothing():
    control, reason = affordance(payload(inventory_state="out_of_stock"))
    assert control is None
    assert reason == mw.CART_HIDDEN_UNAVAILABLE


def test_an_unpriced_listing_offers_nothing():
    """The route refuses `price_minor <= 0` with 400 ITEM_UNAVAILABLE.

    An unpriced listing is a real production shape -- a dropship draft's price is
    blank by design -- and the card already declines to print a price for it. A
    button beside a missing price is an offer to buy at an unstated amount.
    """
    control, reason = affordance(payload(price_label=""))
    assert control is None
    assert reason == mw.CART_HIDDEN_NO_PRICE


def test_a_payload_that_never_saw_the_serializer_gets_no_button():
    """Absent `buyer_visible` is refused exactly like `False`.

    This is the fail-closed direction and it is the one that matters. A raw row
    has had nothing check its publication state, and the alternative -- treating
    "nobody asked" as "it is fine" -- puts a button on a suspended seller's
    listing. Costing a buyer one tap through the app is the cheaper mistake.
    """
    raw = payload()
    del raw["buyer_visible"]
    control, reason = affordance(raw)
    assert control is None
    assert reason == mw.CART_HIDDEN_UNAVAILABLE


def test_a_row_with_no_id_offers_nothing():
    """There is nothing to post. Asserted because `int(None or 0)` is 0, not a raise."""
    control, _ = affordance(payload(id=0, listing_id=0))
    assert control is None


def test_the_owner_check_needs_a_real_viewer_id():
    """A signed-out-but-numbered viewer, or seller id 0, must not collide at zero.

    `0 == 0` is the trap: a listing whose `seller_user_id` never came through
    would otherwise look like it belonged to every viewer whose id also failed to
    parse, and the button would vanish from rows that were merely missing a join.
    """
    control, reason = affordance(payload(seller_user_id=0))
    assert reason == ""
    assert control is not None


@pytest.mark.parametrize("bad", ["", None, "not-a-number", []])
def test_an_unparseable_viewer_id_is_not_an_owner(bad):
    control, reason = mw.cart_affordance(
        payload(), price=mw.derive_price(payload(), []), signed_in=True, viewer_user_id=bad
    )
    assert reason == ""
    assert control is not None


# ---------------------------------------------------------------------------
# The markup: the page renders what the facts decided
# ---------------------------------------------------------------------------

def test_the_grid_renders_a_button_for_a_shopper_who_can_buy():
    html = discovery(cart_count=0)
    assert 'data-mkt-add="5"' in html


def test_the_button_ships_hidden_so_a_scriptless_visitor_never_sees_it():
    """Adding to the cart is a `fetch`; there is no form behind this button.

    Same rule as Save and Report on the product page, and the same reason: an
    unhidden fetch-only control is a button that visibly does nothing. The whole
    card remains a link to the product page, where the purchase is still reachable
    without JavaScript, so nothing is actually lost.
    """
    html = discovery(cart_count=0)
    assert re.search(r'data-mkt-add="5"[^>]*\shidden>', html)


def test_the_script_is_what_reveals_the_button():
    """The other half of the rule above: `hidden` is only honest if binding clears it."""
    assert re.search(
        r'\[data-mkt-add\]:not\(\[data-mkt-bound\]\)[\s\S]{0,600}?button\.hidden = false',
        JS,
    )


def test_each_buttons_accessible_name_names_its_product():
    """Fifteen cards, fifteen identical labels, is a list a screen reader cannot use."""
    html = discovery(cart_count=0)
    assert 'aria-label="Add to cart: Linen Shirt"' in html


def test_the_products_title_is_escaped_into_the_accessible_name():
    html = discovery([payload(title='Shirt "<script>alert(1)</script>"')], cart_count=0)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


@pytest.mark.parametrize(
    "row,viewer",
    [
        (payload(), ANON),
        (payload(), OWNER),
        (payload(buyer_visible=False), BUYER),
        (payload(inventory_state="out_of_stock"), BUYER),
        (payload(price_label=""), BUYER),
    ],
    ids=["anonymous", "own-listing", "not-visible", "out-of-stock", "unpriced"],
)
def test_the_grid_renders_no_button_where_the_facts_withhold_one(row, viewer):
    """Asserted through the renderer as well as the facts layer.

    A markup layer that forgot to pass `cart` through, or derived its own answer,
    would pass every `cart_affordance` test above and still put a button on a
    suspended seller's listing.
    """
    assert "data-mkt-add" not in discovery([row], viewer=viewer, cart_count=0)


def test_a_caller_that_does_not_ask_for_the_cart_gets_no_buttons():
    """`cart_count=None` is the default and suppresses the whole feature.

    This is what keeps every other consumer of `product_card` -- the related
    products rail among them -- rendering exactly the markup it rendered before,
    rather than sprouting controls that post to an endpoint its page never wired.
    """
    assert "data-mkt-add" not in discovery()
    assert "mkt-cart-link" not in discovery()


def test_product_card_omits_the_action_row_entirely_when_there_is_no_affordance():
    """Not an empty div. `.mkt-card-body` is a gapped flex column, so an empty
    flex item still consumes one gap and moves every card whose neighbour has a
    button -- the geometry bug `.mkt-card-meta` is already commented against."""
    html = sf.product_card(payload(), price=mw.derive_price(payload(), []))
    assert "mkt-card-actions" not in html


# ---------------------------------------------------------------------------
# The header cart link
# ---------------------------------------------------------------------------

def test_the_header_links_to_the_cart():
    assert 'class="mkt-cart-link"' in discovery(cart_count=2)


def test_the_cart_href_comes_through_the_registry_and_not_a_literal():
    """Substitution, not comparison.

    `website_href('cart', source='web')` returns `/pulse/cart` today, which is
    exactly what a literal would have produced -- so comparing the two passes for
    a renderer that never consults the registry, and keeps passing through the
    flip that makes `cart` an `/open/cart` handoff. Only moving the registry's
    answer and watching the page follow distinguishes a call from a coincidence.
    """
    original = app_links.website_href
    try:
        app_links.website_href = lambda dest, *a, **kw: (
            "/moved/cart" if dest == "cart" else original(dest, *a, **kw)
        )
        html = discovery(cart_count=1)
    finally:
        app_links.website_href = original
    assert 'href="/moved/cart"' in html
    assert 'href="/pulse/cart"' not in html


def test_cart_path_asks_the_registry_for_the_web_answer():
    """`source='web'` specifically: the registry answers a different href for the
    app-first surface, and a cart link on pulsesoc.com that returned the
    interstitial would bounce a signed-in member to the App Store."""
    seen = {}
    original = app_links.website_href

    def spy(dest, *a, **kw):
        seen.update({"dest": dest, "kw": kw})
        return original(dest, *a, **kw)

    try:
        app_links.website_href = spy
        sf.cart_path()
    finally:
        app_links.website_href = original
    assert seen["dest"] == "cart"
    assert seen["kw"].get("source") == "web"


def test_a_count_of_zero_still_links_but_shows_no_number():
    """A badge reading "0" is noise where an absent badge is the same information."""
    html = discovery(cart_count=0)
    assert "mkt-cart-link" in html
    assert re.search(r"data-mkt-cart-count\s+hidden", html)


def test_a_real_count_is_rendered_and_visible():
    html = discovery(cart_count=3)
    assert re.search(r"data-mkt-cart-count>3<", html)
    assert not re.search(r"data-mkt-cart-count\s+hidden", html)


@pytest.mark.parametrize(
    "count,expected",
    [(0, "Your cart, empty"), (1, "Your cart, 1 item"), (2, "Your cart, 2 items")],
)
def test_the_count_is_inside_the_links_accessible_name(count, expected):
    """Otherwise a screen reader announces "Cart" and then a stray number."""
    assert f'aria-label="{expected}"' in discovery(cart_count=count)


def test_the_visible_count_is_not_announced_twice():
    assert re.search(r'class="mkt-cart-count" aria-hidden="true"', discovery(cart_count=3))


def test_none_and_zero_are_different_answers():
    """The standing rule that error and empty must never render alike, as a badge.

    `bot.marketplace_storefront_cart_count` answers `None` for a read that
    *failed*. If `None` rendered as `0` the header would tell a buyer their cart
    was empty on the strength of a query that never came back.
    """
    assert "mkt-cart-link" not in discovery()
    assert "mkt-cart-link" in discovery(cart_count=0)


def test_a_failed_catalogue_read_invites_no_purchase():
    """The 503 path. A page that could not read the catalogue has no business
    offering to sell from it."""
    html = discovery(load_error=True, cart_count=4)
    assert "data-mkt-add" not in html


# ---------------------------------------------------------------------------
# One definition of the count
# ---------------------------------------------------------------------------

def test_the_badge_counts_only_lines_a_buyer_could_pay_for():
    lines = [
        {"qty": 2, "state": "available"},
        {"qty": 1, "state": "price_changed"},
        {"qty": 3, "state": "low_stock"},
        {"qty": 9, "state": "sold"},
        {"qty": 9, "state": "removed"},
        {"qty": 9, "state": "restricted"},
    ]
    assert cart_routes.badge_count(lines) == 6


def test_the_counted_states_are_exactly_the_purchasable_ones():
    """Spelled as a positive set, so a state nothing here has heard of is not
    counted by accident -- the same shape `marketplace_cart_web.BLOCKING_STATES`
    uses, and for the same reason."""
    assert cart_routes.COUNTED_STATES == frozenset(
        {"available", "price_changed", "low_stock"}
    )


@pytest.mark.parametrize("bad", [None, "", "three", [], {}])
def test_an_unparseable_quantity_does_not_break_the_count(bad):
    assert cart_routes.badge_count([{"qty": bad, "state": "available"}]) == 0


def test_a_negative_quantity_cannot_reduce_the_count():
    assert cart_routes.badge_count(
        [{"qty": -5, "state": "available"}, {"qty": 2, "state": "available"}]
    ) == 2


def test_an_empty_or_missing_cart_counts_zero():
    assert cart_routes.badge_count([]) == 0
    assert cart_routes.badge_count(None) == 0


def test_both_cart_endpoints_report_the_same_count_function():
    """The expression used to be written out twice, inline, in these two routes.

    They agreed only because nobody had edited one of them yet. Asserted against
    the source because the alternative is booting Flask and seeding a cart to
    compare two numbers that are equal in every fixture a test would build --
    which is exactly how the duplication survived in the first place.
    """
    source = inspect.getsource(cart_routes)
    assert source.count('"badge_count": badge_count(lines)') == 2
    # And no second copy of the sum survives anywhere in the module.
    assert "price_changed\", \"low_stock\"}" not in source.replace(
        "COUNTED_STATES = frozenset({\"available\", \"price_changed\", \"low_stock\"})", ""
    )


# ---------------------------------------------------------------------------
# The script: the count comes from the server
# ---------------------------------------------------------------------------

def test_the_script_posts_to_the_cart_api_the_app_already_uses():
    """No new endpoint. `/api/pulse/marketplace/cart` has answered the native app
    since before there was a web storefront."""
    assert '"/api/pulse/marketplace/cart"' in JS


def test_the_script_reads_the_badge_count_out_of_the_response():
    assert re.search(r"setCartCount\(\s*data && data\.badge_count\s*\)", JS)


def test_the_script_never_increments_the_count_itself():
    """An optimistic `+1` is right until the route refuses -- OWN_LISTING,
    OUT_OF_STOCK, CART_FULL -- and it also caps quantity against real inventory.
    A client counting its own clicks drifts on the first refusal and stays wrong
    until a reload. The response already carries the true number.
    """
    body = JS[JS.index("function setCartCount") : JS.index("function bindClamp")]
    assert "++" not in body
    assert not re.search(r"\+\s*1\b", body)
    assert not re.search(r"count\s*\+=", body)


def test_a_response_without_a_count_leaves_the_badge_alone():
    """`undefined` is "this response carried no count", not "your cart is empty"."""
    assert re.search(
        r"function setCartCount\(value\) \{\s*if \(value === null \|\| value === undefined\) return;",
        JS,
    )


def test_the_script_rewrites_the_accessible_name_with_the_count():
    """Leaving the old one is the failure where a screen reader says "Your cart,
    empty" over a cart holding three things."""
    body = JS[JS.index("function setCartCount") : JS.index("function bindAddToCart")]
    assert "aria-label" in body
    assert "data-mkt-cart-link" in body


def test_the_click_is_stopped_from_reaching_the_card_wide_link():
    """The card is one big anchor and this button sits inside it. Without
    `stopPropagation` the click adds to the cart *and* navigates away mid-request.
    Both halves are needed -- the CSS raise makes the button the target, this
    keeps the event from bubbling to the card."""
    body = JS[JS.index("function bindAddToCart") : JS.index("Long-description")]
    assert "event.preventDefault()" in body
    assert "event.stopPropagation()" in body


def test_the_button_is_disabled_while_the_request_is_open():
    body = JS[JS.index("function bindAddToCart") : JS.index("Long-description")]
    assert "button.disabled = true" in body


def test_a_refused_add_gives_the_button_back():
    """Every refusal is something the buyer might fix. A spent control gives them
    nothing to retry."""
    body = JS[JS.index("function bindAddToCart") : JS.index("Long-description")]
    assert re.search(r"\.catch\(function \(err\) \{[\s\S]{0,400}?button\.disabled = false", body)


def test_the_add_to_cart_binding_runs_on_hydrate():
    """A bound function nothing calls is a button that stays hidden forever."""
    assert re.search(r"bindActions\(scope\);\s*bindAddToCart\(scope\);", JS)


# ---------------------------------------------------------------------------
# The stylesheet: the button has to be clickable
# ---------------------------------------------------------------------------

def test_the_button_is_raised_above_the_card_wide_link():
    """`.mkt-card-link::after` stretches over the whole card at `z-index: 1`.
    Anything that must stay separately clickable is raised above it -- the rule
    `.mkt-card-seller` already follows. Without this the button is unreachable:
    every click lands on the overlay and navigates to the product page instead.
    """
    block = CSS[CSS.index(".mkt-add {") : CSS.index(".mkt-add:hover")]
    assert "position: relative" in block
    assert "z-index: 2" in block


def test_the_overlay_it_is_raised_above_is_still_at_z_index_one():
    """Pins the other side of the comparison, so a later bump of the overlay's
    z-index fails here instead of silently swallowing every click."""
    block = CSS[CSS.index(".mkt-card-link::after {") : CSS.index(".mkt-card-link:focus-visible")]
    assert "z-index: 1" in block


def test_the_button_has_a_visible_focus_ring():
    """It is reached by keyboard, one Tab after the card's own link."""
    assert ".mkt-add:focus-visible" in CSS


def test_the_action_row_is_pinned_to_the_bottom_of_the_card():
    """Cards in a row have titles of different heights. `margin-top: auto` in the
    body's flex column puts every button on one horizontal line regardless, which
    is the "uniform by construction" rule the rest of the card follows."""
    block = CSS[CSS.index(".mkt-card-actions {") : CSS.index(".mkt-add {")]
    assert "margin-top: auto" in block


def test_the_hidden_count_pill_is_actually_hidden():
    """`[hidden]` is a weaker selector than a class that sets a display mode, so
    the stylesheet has to restate it or an empty cart shows a bare pill."""
    assert ".mkt-cart-count[hidden]" in CSS
    assert "display: none" in CSS[CSS.index(".mkt-cart-count[hidden]") :][:120]


# ---------------------------------------------------------------------------
# A listing with options gets the picker, not a guess
#
# `POST /api/pulse/marketplace/cart` takes a `listing_id` and a `qty`. It has no
# variant parameter -- there is no "variant" anywhere in
# `services/marketplace_cart_routes.py`, which the first test here pins, because
# every other test in this section is only worth having while that stays true.
#
# So a one-tap add on a listing sold in four sizes does not fail. It succeeds,
# and writes a cart line naming no size, priced from the listing rather than from
# the variant nobody picked. The quick-add is therefore withheld from any listing
# where the buyer would have to choose, and the card offers a link to the product
# page instead -- where the real picker already lives.
#
# This is the one withheld reason that renders something. The other four mean
# "this cannot be bought"; this one means "not in one tap".
# ---------------------------------------------------------------------------

def opts(*pairs):
    """`options_json` exactly as `services/marketplace_variants.py` writes it."""
    return json.dumps([{"name": name, "value": value} for name, value in pairs])


def variant(vid, options_json, *, status="active", price_cents=2400):
    return {
        "id": vid, "status": status, "price_cents": price_cents,
        "variant_key": f"k{vid}", "options_json": options_json,
        "stock_state": "IN_STOCK",
    }


def test_the_cart_api_really_has_no_variant_parameter():
    """The premise of this whole section, asserted rather than assumed.

    Every test below exists *because* a cart line cannot name a variant. On the
    day the cart API grows a variant column, this test fails first and points at
    the rest: the correct response then is to let the quick-add send one, not to
    delete this assertion.
    """
    source = inspect.getsource(cart_routes)
    assert "variant" not in source.lower()


def choice(variants, row=None):
    row = payload() if row is None else row
    return mw.cart_affordance(
        row,
        price=mw.derive_price(row, variants),
        signed_in=True,
        viewer_user_id=3,
        variants=variants,
    )


def test_needing_a_choice_is_a_distinct_reason_from_being_unavailable():
    """Asserted as distinctness, not by comparing a constant to itself.

    Every other test in this section reads `reason == mw.CART_HIDDEN_NEEDS_CHOICE`,
    which passes just as happily if that name is an alias of
    `CART_HIDDEN_UNAVAILABLE`. It must not be: the renderer branches on exactly
    this difference, so collapsing the two makes every out-of-stock and
    suspended-seller card sprout a "Choose options" link. That is the one
    mutation the equality assertions cannot see, so it is pinned here.
    """
    reasons = [
        mw.CART_HIDDEN_NEEDS_CHOICE, mw.CART_HIDDEN_UNAVAILABLE,
        mw.CART_HIDDEN_ANONYMOUS, mw.CART_HIDDEN_OWN_LISTING, mw.CART_HIDDEN_NO_PRICE,
    ]
    assert len(set(reasons)) == len(reasons), reasons


def test_a_listing_sold_in_three_sizes_gets_no_quick_add():
    control, reason = choice([
        variant(1, opts(("Size", "S"))),
        variant(2, opts(("Size", "M"))),
        variant(3, opts(("Size", "L"))),
    ])
    assert control is None
    assert reason == mw.CART_HIDDEN_NEEDS_CHOICE


def test_a_single_colour_is_not_a_choice_and_keeps_its_button():
    """A group with one value asks the buyer nothing. Withholding the quick-add
    there would cost a tap to confirm the only option there is."""
    control, reason = choice([variant(1, opts(("Color", "Black")))])
    assert reason == ""
    assert control is not None


def test_a_listing_with_no_variants_keeps_its_button():
    """The common case in this catalogue: one price, one thing."""
    control, reason = choice([])
    assert reason == ""
    assert control is not None


def test_two_variants_with_unreadable_options_still_need_a_choice():
    """`options_json` empty or malformed yields no option groups at all, so a
    check that only counted groups would wave these through. The line still has
    to name one of two rows and nothing on this page knows which."""
    for junk in ("", "null", "[]", "not json", '{"Size": "M"}'):
        control, reason = choice([variant(1, junk), variant(2, junk)])
        assert control is None, junk
        assert reason == mw.CART_HIDDEN_NEEDS_CHOICE, junk


def test_archived_variants_do_not_manufacture_a_choice():
    """A seller who stocked S/M/L and retired all but M sells one thing now.
    Counting retired rows would withhold the button from every listing whose
    seller ever cleaned up their inventory."""
    control, reason = choice([
        variant(1, opts(("Size", "M"))),
        variant(2, opts(("Size", "L")), status="archived"),
        variant(3, opts(("Size", "S")), status="archived"),
    ])
    assert reason == ""
    assert control is not None


def test_a_price_range_alone_withholds_the_quick_add():
    """The consequence that reaches the buyer's card statement. If the card says
    "$24.00 – $40.00" then a cart line holding one number holds a number the
    buyer never agreed to. Asserted through `requires_variant_choice` directly so
    it is pinned independently of the group and variant-count checks that
    normally imply it."""
    ranged = mw.PriceView(currency="USD", min_cents=2400, max_cents=4000, source="variants")
    assert ranged.is_range
    assert mw.requires_variant_choice([], price=ranged) is True


def test_an_unavailable_reason_still_beats_needing_a_choice():
    """Order matters: a suspended seller's four-size jacket must render nothing,
    not a link inviting the buyer to configure something they cannot buy."""
    control, reason = choice(
        [variant(1, opts(("Size", "S"))), variant(2, opts(("Size", "M")))],
        row=payload(buyer_visible=False),
    )
    assert control is None
    assert reason == mw.CART_HIDDEN_UNAVAILABLE


def test_an_anonymous_visitor_is_told_to_sign_in_before_being_told_to_choose():
    control, reason = mw.cart_affordance(
        payload(),
        price=mw.derive_price(payload(), []),
        signed_in=False,
        variants=[variant(1, opts(("Size", "S"))), variant(2, opts(("Size", "M")))],
    )
    assert control is None
    assert reason == mw.CART_HIDDEN_ANONYMOUS


# --- and the same again through the renderer, which can disagree -------------

def sized_page(variants, **kw):
    row = payload(id=5, listing_id=5)
    return sf.render_discovery(
        listings=[row],
        variants_by_listing={5: variants},
        filters=sf.Filters(),
        viewer=BUYER,
        cart_count=0,
        **kw,
    ).body_html


def test_the_rendered_card_for_a_sized_listing_swaps_button_for_picker_link():
    html = sized_page([
        variant(1, opts(("Size", "S"))),
        variant(2, opts(("Size", "M"))),
    ])
    assert "data-mkt-add=" not in html
    assert 'data-mkt-choose="5"' in html
    assert "Choose options" in html


def test_the_picker_link_points_at_the_product_page():
    """The same destination the card-wide link already has, taken from
    `product_path` rather than written out, so a change to the URL shape moves
    both."""
    html = sized_page([variant(1, opts(("Size", "S"))), variant(2, opts(("Size", "M")))])
    href = re.search(r'class="mkt-add is-choose" href="([^"]+)"', html)
    assert href, html[:400]
    assert href.group(1) == sf.product_path(5)


def test_the_picker_link_is_not_hidden_because_it_needs_no_script():
    """The opposite of the quick-add button, and for the stated reason: this one
    is an anchor that works with JavaScript off, so shipping it `hidden` would
    hide a control that functions."""
    html = sized_page([variant(1, opts(("Size", "S"))), variant(2, opts(("Size", "M")))])
    tag = re.search(r'<a class="mkt-add is-choose".*?>', html).group(0)
    assert "hidden" not in tag


def test_the_picker_link_names_the_product_for_a_screen_reader():
    """"Choose options" fifteen times over is not a page a screen-reader user can
    navigate, same argument as the button's own label."""
    html = sized_page([variant(1, opts(("Size", "S"))), variant(2, opts(("Size", "M")))])
    assert 'aria-label="Choose options: Linen Shirt"' in html


def test_a_single_variant_listing_still_renders_the_real_button():
    """The negative control for the four tests above: the swap must be conditional
    on the listing, not something every card now gets."""
    html = sized_page([variant(1, opts(("Color", "Black")))])
    assert 'data-mkt-add="5"' in html
    assert "data-mkt-choose" not in html
    assert "Choose options" not in html


def test_a_card_never_carries_both_actions():
    """They share one row and one slot. Two actions would be two answers to the
    same question."""
    for variants in ([], [variant(1, opts(("Color", "Black")))],
                     [variant(1, opts(("Size", "S"))), variant(2, opts(("Size", "M")))]):
        html = sized_page(variants)
        assert not ("data-mkt-add=" in html and "data-mkt-choose=" in html), variants


def test_the_picker_link_is_absent_where_the_caller_never_asked_for_a_cart():
    """`cart_count=None` suppresses the whole action row, this link included.
    Otherwise a call site that never wired up the cart API would start sprouting
    action rows on configurable products only."""
    html = sf.render_discovery(
        listings=[payload(id=5, listing_id=5)],
        variants_by_listing={5: [variant(1, opts(("Size", "S"))), variant(2, opts(("Size", "M")))]},
        filters=sf.Filters(),
        viewer=BUYER,
    ).body_html
    assert "mkt-card-actions" not in html
    assert "data-mkt-choose" not in html


#: Two sizes, and no price anywhere -- neither on the listing nor on the variant
#: rows. Spelled out because the ordinary `variant()` helper carries a
#: `price_cents`, and a priced variant makes the listing priced no matter what its
#: own `price_label` says: `derive_price` prefers the variants. A first draft of
#: the unpriced case below used priced variants and was therefore testing the
#: needs-choice path under an "unpriced" name.
UNPRICED_SIZES = [
    variant(1, opts(("Size", "S")), price_cents=None),
    variant(2, opts(("Size", "M")), price_cents=None),
]

SIZED = [variant(1, opts(("Size", "S"))), variant(2, opts(("Size", "M")))]


@pytest.mark.parametrize("label,row,viewer,variants", [
    ("suspended or unlisted", payload(buyer_visible=False), BUYER, SIZED),
    ("out of stock", payload(inventory_state="out_of_stock"), BUYER, SIZED),
    ("unpriced", payload(price_label="", price_cents=None), BUYER, UNPRICED_SIZES),
    ("the seller's own listing", payload(), OWNER, SIZED),
    ("nobody signed in", payload(), ANON, SIZED),
])
def test_an_unbuyable_listing_with_sizes_renders_no_action_at_all(label, row, viewer, variants):
    """The other four reasons render *nothing* -- not a picker link.

    Each of these rows is sold in two sizes, so `requires_variant_choice` is true
    for all of them; what has to decide the markup is the reason that won. A
    renderer that offered the link for any withheld reason, or one that could not
    tell "needs configuring" from "cannot be bought", would invite the buyer to
    pick a size for a product with no price, a suspended seller, or no session --
    and then refuse them on the next page. Both of those are mutations the
    positive `Choose options` tests above pass happily.
    """
    row = dict(row, id=5, listing_id=5)
    html = sf.render_discovery(
        listings=[row],
        variants_by_listing={5: variants},
        filters=sf.Filters(),
        viewer=viewer,
        cart_count=0,
    ).body_html
    assert "data-mkt-choose" not in html, label
    assert "Choose options" not in html, label
    assert "data-mkt-add=" not in html, label
    assert "mkt-card-actions" not in html, label


def test_product_card_shows_one_action_when_handed_both():
    """`product_card` is public and its two action arguments are exclusive. The
    call site never passes both, so the exclusivity is invisible from the
    renderer's own tests -- asserted here directly so the `elif` that enforces it
    is load-bearing rather than decorative. The quick-add wins: a caller that
    derived a real `CartAffordance` has already established no choice is needed.
    """
    html = sf.product_card(
        payload(id=5, listing_id=5),
        price=mw.derive_price(payload(), []),
        cart=mw.CartAffordance(listing_id=5),
        choose_options=True,
    )
    assert 'data-mkt-add="5"' in html
    assert "data-mkt-choose" not in html
    assert html.count("mkt-card-actions") == 1


def test_the_script_does_not_bind_the_picker_link():
    """It is a navigation, not a fetch. If the add-to-cart binder matched it, a
    click would post an unconfigured line -- the exact thing this section
    prevents."""
    assert "data-mkt-choose" not in JS


def test_the_picker_link_is_raised_above_the_card_wide_link_like_the_button():
    """The card-wide link's `::after` covers this whole area. Without the raise,
    a click here would be caught by that overlay instead.

    The raise lives on `.mkt-add`, so what has to hold is that the rendered link
    actually wears that class -- a markup change to a private `.mkt-choose` would
    still look fine in the stylesheet and silently lose the z-index. Asserted
    against the markup, then against the rule it depends on, and `is-choose` must
    not cancel it.
    """
    html = sized_page([variant(1, opts(("Size", "S"))), variant(2, opts(("Size", "M")))])
    classes = re.search(r'<a class="([^"]+)" href[^>]*data-mkt-choose', html)
    assert classes, html[:400]
    assert "mkt-add" in classes.group(1).split()

    base = rule(".mkt-add")
    assert "position: relative" in base and "z-index: 2" in base
    override = rule(".mkt-add.is-choose")
    assert "position:" not in override
    assert "z-index" not in override


def rule(selector):
    """The declarations of one rule, stopping at its closing brace.

    A fixed-width slice off the selector reaches into the *next* rule, and the
    `:hover` that follows `.mkt-add.is-choose` also sets `text-decoration: none`
    -- so a slice-based test went on passing after the declaration it was
    checking had been deleted from the rule it names.
    """
    start = CSS.index(selector + " {") + len(selector) + 2
    return CSS[start : CSS.index("}", start)]


def test_the_picker_link_does_not_look_like_a_quick_add():
    """It navigates. A buyer who read it as a cart button would be surprised
    twice -- once by the page change and once by the empty cart."""
    block = rule(".mkt-add.is-choose")
    assert "background: transparent" in block
    assert "text-decoration: none" in block


def test_no_rating_or_sold_count_styling_arrived_with_the_button():
    """The catalogue holds no review, rating or sales data, so there must be
    nothing here that could render one. Guarded because an "add to cart" row is
    exactly where a storefront template would normally put them."""
    for banned in ("mkt-card-rating", "mkt-card-reviews", "mkt-card-sold", "mkt-card-stars"):
        assert banned not in CSS
        assert banned not in JS
