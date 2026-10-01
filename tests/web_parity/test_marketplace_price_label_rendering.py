"""What the web marketplace renders where a price would go, when there is none.

## The bug this closes

A listing with a blank ``price_label`` was rendered on the web as the words
"Request access". Three sites did it: the server-rendered grid card, the inline
JavaScript twin of that card used for search results, and the product page.

``pulse_marketplace_listing_payload`` and ``marketplace_normalize_price_label``
were fixed first (c0f42018, df03e8b4) on the principle that a listing with no
price carries no price -- a phrase invented on the way out is indistinguishable
downstream from one the seller typed, and "Request access" is a phrase a seller
may legitimately choose. Those fixes covered every client that reads through the
API. They did not cover these three, because this page does not read through the
API: it builds HTML from the database row itself. So the same unpriced listing
came out unpriced on the phone and priced "Request access" in a browser.

## Why these assertions are about rendered output, not helpers

This bug family survived for as long as it did because every test in it asserted
on helpers. ``marketplace_normalize_price_label`` had tests. The payload shaper
had tests. Nothing asserted the shape of what a surface actually emitted, so
three separate sites could each re-invent the phrase downstream of a correct
helper and stay green.

Every assertion below is therefore made against bytes served by the app. That
rule is what makes the checks here survive an implementation swap, and it has now
been tested by two. The search surface used to be an inline JavaScript twin of
the card, rendered in the browser, and this file used to execute it under node;
search is now ``?q=`` rendered on the server by the same function as the grid, so
the twin and the node harness are gone. Then the product page, which was
hand-built HTML in ``bot.py`` printing the price as one ``<span class="pill">``
among several, moved to ``marketplace_storefront.render_product`` and a dedicated
``data-mkt-price`` element.

Both swaps left every property this file names intact, because none of them named
an implementation -- but both broke the *readers*, which did. That is the honest
cost of asserting on served bytes, and it is worth paying: a reader that stops
matching fails loudly and is fixed here, whereas a helper-level test would have
kept passing while the surface it claimed to cover was replaced underneath it.

Two shapes in particular have to stay failures rather than becoming "no price": a
surface that never renders a price at all, and one that renders the price element
present and empty, which is not "no price" but "a price the seller set to
nothing". Every claim below is paired with a control that a price *does* appear
where one exists, so neither can pass by rendering nothing.

## What may leave with the price

Not every element beside a price is independent of it. An add-to-cart control on
an unpriced listing is an offer to buy at an unstated amount, and the cart route
refuses exactly that, so the renderers withhold it -- see ``PRICE_DEPENDENT``.
The structural assertions below therefore permit the price's dependent controls
to go with it and nothing else, rather than requiring an unpriced surface to be
identical to a priced one but for the number.

## Three price values, not one

``""`` is the value a dropship import writes. ``"   "`` is what a hand-edited or
migrated row can hold, and is the case a bare ``or`` fallback never catches --
whitespace is truthy, so the old code would have passed it through and rendered
an empty pill. A missing key covers the client-side twin being handed a row from
an older serializer. All three must render identically: no pill.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile

import pytest

from tests.probe_report import parse_report

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: A listing whose seller priced it. The control: every "no pill" assertion
#: below is worthless unless a pill appears when there *is* a price, and this is
#: what distinguishes the fix from deleting the price from the card entirely.
PRICED = 78001
PRICED_LABEL = "$19.99"

#: Unpriced, two ways a row reaches that state.
BLANK = 78002       #: ``price_label=''`` -- what a dropship import writes.
WHITESPACE = 78003  #: ``price_label='   '`` -- truthy, so a bare ``or`` misses it.

UNPRICED = (BLANK, WHITESPACE)
ALL_LISTINGS = (PRICED, BLANK, WHITESPACE)

#: The substitution that used to be rendered here.
INVENTED = "Request access"

_PROBE = r"""
import json, re, sys, sqlite3
sys.path.insert(0, %(repo)r)
import bot

app = bot.webhook_app
app.config["SECRET_KEY"] = "marketplace-price-label-test"
report = {}

with app.app_context():
    conn = bot.db(); conn.row_factory = sqlite3.Row; cur = conn.cursor()
    cur.execute("INSERT INTO users (username, email, display_name) "
                "VALUES ('pricebuyer','pricebuyer@example.com','Buyer')")
    viewer_id = cur.lastrowid
    # A seller the discovery predicate admits, so a missing card is always about
    # the price and never about visibility.
    cur.execute("INSERT INTO users (user_id, username, email, display_name, "
                "hidden_from_discovery, account_status) "
                "VALUES (9101,'seller9101','seller9101@example.com','Seller9101',0,'active')")
    cur.execute("INSERT INTO marketplace_sellers (user_id, status, display_name) "
                "VALUES (9101,'approved','Seller9101 Store')")
    for lid, label in %(rows)r:
        cur.execute(
            # `safety_score` is still named, and deliberately: it is seeded
            # non-default so that a card which started printing it again would
            # show up here rather than blend into a column of zeroes.
            "INSERT INTO marketplace_listings (id, seller_user_id, title, description, "
            "category, price_label, currency, approval_status, status, quantity, "
            "safety_score) VALUES (?,?,?,?,?,?,?,'approved','active',5,44)",
            (lid, 9101, "Listing %%d" %% lid, "Body of listing %%d" %% lid,
             "Education", label, "USD"))
    conn.commit()
    # Proof the seed holds the exact strings the assertions name. A column
    # default or a trigger rewriting a blank to prose would otherwise read as
    # the renderer still substituting.
    cur.execute("SELECT id, price_label FROM marketplace_listings "
                "WHERE id>=78000 ORDER BY id")
    report["seeded"] = {str(r["id"]): r["price_label"] for r in cur.fetchall()}
    conn.close()

client = app.test_client()
with client.session_transaction() as session:
    session["account_user_id"] = viewer_id

# The two surfaces are read with two different extractions, deliberately: the
# member grid emits a storefront card and the product page emits a purchase
# panel, so neither pattern fits the other. Reading each with the pattern that
# fits it is what lets the agreement test below compare the price the two
# surfaces *arrive at* instead of the markup they arrive at it in -- which they
# do not share.
CARD = re.compile(r'<article class="mkt-card">.*?</article>', re.S)
CARD_LINK = re.compile(r'class="mkt-card-link" href="/pulse/marketplace/(\d+)"')

#: The product page's purchase panel, and the price element inside it.
#:
#: The panel is the anchor: it is emitted unconditionally, so its presence means
#: "this page rendered somewhere for a price to go" and lets a missing price stay
#: distinguishable from a missing page region. That job used to belong to the
#: category pill, back when this page was hand-built HTML in ``bot.py`` and the
#: price was one anonymous ``<span class="pill">`` among several.
#:
#: ``data-mkt-price`` is the hook ``pulse_marketplace.js`` rewrites when a buyer
#: picks a variant, so this reads the element the *page* considers to be the
#: price rather than one this file went looking for.
PANEL = re.compile(r'<section class="mkt-panel".*?</section>', re.S)
PRICE_VALUE = re.compile(r'<span class="mkt-price-value"[^>]*>(.*?)</span>', re.S)


def cards_by_listing(html):
    '''Every storefront card on the page, keyed by the listing it links to.

    Attributed by the card's own product link, because the card has no listing-id
    attribute to read instead: the whole card is one stretched anchor, so the
    link *is* the card's identity. The previous anchor for this step was a
    `data-save-listing` attribute, and it went with the per-card action buttons
    when those moved to the product page -- at which point every card came back
    unattributed and this file reported a rendered, correctly priced grid as "the
    listing rendered no card at all". `cards_seen` below exists so that failure
    mode stays separable from a renderer that really emitted nothing.
    '''
    found = {}
    for card in CARD.findall(html):
        match = CARD_LINK.search(card)
        if match:
            found[match.group(1)] = card
    return found


def purchase_panel(html):
    '''The product page's purchase panel, or None if it rendered none.

    Read as a region rather than searching the whole document on purpose: the
    price element has to be located *inside the panel a buyer reads*, so that a
    number printed anywhere else on the page -- a related-products rail, a
    structured-data block, an inline script -- cannot stand in for a price the
    panel itself never showed.
    '''
    found = PANEL.search(html)
    return None if found is None else found.group(0)


def panel_price(panel):
    '''The raw contents of the panel's price element, or None if it has none.

    The same two-way distinction `_card_price` makes on the grid, and for the
    same reason: None is "no price element at all", which is what an unpriced
    listing must render, while "" is an element present and holding nothing,
    which is a price the seller set to nothing. Returned unstripped so a stray
    separator left inside the element fails the comparisons rather than being
    tidied away by the reader.
    '''
    if panel is None:
        return None
    found = PRICE_VALUE.search(panel)
    return None if found is None else found.group(1)


grid_response = client.get("/pulse/marketplace")
grid = grid_response.get_data(as_text=True)
report["grid_status"] = grid_response.status_code
report["grid_invented"] = %(invented)r in grid
report["grid_cards"] = cards_by_listing(grid)
report["grid_cards_seen"] = len(CARD.findall(grid))
# Whether each seeded listing reached the page at all, independently of whether
# the card extraction above could attribute it. That is the difference between a
# renderer that emitted nothing and a probe that could not read what it emitted.
report["grid_ids_present"] = [str(lid) for lid in %(ids)r
                              if ("Listing %%d" %% lid) in grid]

pages = {}
for lid in %(ids)r:
    response = client.get("/pulse/marketplace/%%d" %% lid)
    body = response.get_data(as_text=True)
    panel = purchase_panel(body)
    pages[str(lid)] = {
        "status": response.status_code,
        "panel": panel,
        "price": panel_price(panel),
        "invented": %(invented)r in body,
        "title": ("Listing %%d" %% lid) in body,
    }
report["pages"] = pages

# Search results. This is the surface that used to be the inline JavaScript twin
# of the card, rendered in the browser from `/api/pulse/marketplace/search`; it
# is now `?q=` rendered on the server. The capability is the same and so is the
# risk the twin carried -- a second renderer pricing a listing the grid leaves
# unpriced -- so it is still read here, from the document a member is served
# rather than by executing a script.
search = {}
for lid in %(ids)r:
    response = client.get("/pulse/marketplace?q=Listing+%%d" %% lid)
    body = response.get_data(as_text=True)
    search[str(lid)] = {
        "status": response.status_code,
        "shows": ("Listing %%d" %% lid) in body,
        "card": cards_by_listing(body).get(str(lid)),
        "invented": %(invented)r in body,
    }
report["search"] = search

sys.stdout.write("<<<REPORT>>>" + json.dumps(report))
"""

_ROWS = ((PRICED, PRICED_LABEL), (BLANK, ""), (WHITESPACE, "   "))


@pytest.fixture(scope="module")
def price_probe():
    """Boot the app once, in a child process, and report what it serves.

    In a subprocess for the reason the rest of this directory is: importing
    ``bot`` binds ``DATABASE_URL`` process-wide and cannot be undone, so an
    in-process import would steal the database from every other suite sharing
    the run.
    """
    workdir = tempfile.mkdtemp(prefix="marketplace-price-")
    env = dict(os.environ)
    env["DATABASE_URL"] = "sqlite:///" + os.path.join(workdir, "marketplace.db")
    env["COINPILOTX_DB_INIT_STARTUP_MODE"] = "sync"
    env["PYTHONPATH"] = REPO
    code = _PROBE % {
        "repo": REPO, "rows": _ROWS, "ids": list(ALL_LISTINGS), "invented": INVENTED,
    }
    proc = subprocess.run([sys.executable, "-c", code], cwd=REPO, env=env,
                          capture_output=True, text=True, timeout=900)
    return parse_report(proc.stdout, proc.stderr)


def _card_price(card):
    """The raw contents of a storefront card's price element, or ``None``.

    ``None`` means the card carries no price element at all, which is the shape
    this file exists to pin. It is deliberately *not* conflated with ``""`` -- an
    element present and empty is a price the seller set to nothing, and that is
    the regression, not the fix. Returned unstripped so a stray separator left
    inside the element fails the comparisons below rather than being tidied away
    by the reader.
    """
    if card is None:
        return None
    found = re.search(r'<p class="mkt-card-price[^"]*">(.*?)</p>', card, re.S)
    return None if found is None else found.group(1)


def _card_parts(card):
    """The card's structural fingerprint: its element classes, in document order.

    This is the port of "the priced card's pills, minus the price". The old card
    put its whole row in one paragraph of pills, so a list of pill texts was both
    the content and the structure; this one is a nest of classed elements, so the
    classes are what carry the structure. Comparing fingerprints keeps the same
    property the pill comparison had -- that removing a price must remove the
    price and nothing else -- without pinning any particular set of elements, so
    adding a line to the card is not a change to this file.
    """
    if card is None:
        return None
    return re.findall(r'<[a-z0-9]+ class="(mkt-[a-z-]+)', card)


#: The card elements that exist *because* the listing has a price, and which an
#: unpriced card is therefore right to drop along with it.
#:
#: The price element itself, and the add-to-cart row. ``mw.cart_affordance``
#: returns ``CART_HIDDEN_NO_PRICE`` for a listing whose price is not known, and
#: that is not a styling choice: it mirrors the refusal
#: ``POST /api/pulse/marketplace/cart`` answers for ``price_minor <= 0`` -- 400
#: ``ITEM_UNAVAILABLE``, at ``marketplace_cart_routes.py``. So a button here
#: would be an offer to buy at an amount nobody has stated, which is the same
#: invention this file exists to prevent carried one step further on: not a price
#: made up in the markup, but a purchase offered without one.
#:
#: This list is the *only* licence the comparison below grants. Everything the
#: priced card carries and this does not name must still appear on the unpriced
#: card, which is what keeps the original bug -- a conditional wrapped around too
#: much markup -- a failure here.
PRICE_DEPENDENT = ("mkt-card-price", "mkt-card-actions", "mkt-add")

#: ``loading`` and ``fetchpriority`` are a position-dependent loading hint: the
#: first card in any grid is eager so it can be the largest contentful paint.
#: They are therefore the one difference between the same listing's card in the
#: full grid and in a search result that says nothing about content, and the only
#: one normalised away before the two are compared byte-for-byte.
_LOADING_HINT = re.compile(r' (?:loading="(?:eager|lazy)"|fetchpriority="high")')


def _content(card):
    return None if card is None else _LOADING_HINT.sub("", card)


def test_the_seed_is_what_these_tests_assume(price_probe):
    """Guard the fixture: the unpriced rows must really be unpriced in the row.

    ``marketplace_listings.price_label`` still carries a column default of
    "Request access". Both insert sites name the column explicitly so it is
    unreachable, but if that ever stopped being true these tests would pass
    against rows that were never blank, proving nothing about the renderer.
    """
    seeded = price_probe["seeded"]
    assert set(seeded) == {str(l) for l in ALL_LISTINGS}, (
        "the probe did not create the listings these assertions name: %r" % seeded)
    assert seeded[str(PRICED)] == PRICED_LABEL
    assert seeded[str(BLANK)] == "", (
        "the blank listing reached the database holding %r, so the column "
        "default or a write path is filling it in" % seeded[str(BLANK)])
    assert seeded[str(WHITESPACE)].strip() == "" and seeded[str(WHITESPACE)] != "", (
        "the whitespace listing is no longer whitespace (%r), so the case a "
        "bare `or` fallback misses is not being exercised"
        % seeded[str(WHITESPACE)])


def test_every_card_the_grid_rendered_was_attributed_to_a_listing(price_probe):
    """Guard the probe's card-to-listing step, which is what silently broke.

    Every assertion about the grid below reads ``grid_cards`` and reports a
    missing entry as "the listing rendered no card". That sentence is only true
    if the extraction can be trusted, and twice now it could not be: three cards
    were served, all three correctly priced, and the dictionary was empty. So the
    suite claimed the renderer had stopped rendering.

    Separating the two makes that distinguishable: a listing whose title is not
    in the served page failed to render, and a listing whose title is there but
    whose card the probe could not attribute failed to be read.

    Deliberately not asserted as "every card carries a listing id". The page also
    serves an unrelated card of its own -- a "PulseSoc Intelligence" blurb with no
    listing behind it -- so that invariant is false for reasons that have nothing
    to do with this file.
    """
    assert price_probe["grid_status"] == 200, (
        "the marketplace grid did not serve (%s), so no assertion below is "
        "about a rendered card" % price_probe["grid_status"])
    assert price_probe["grid_cards_seen"], (
        "the grid served no cards at all; the card pattern no longer matches "
        "the markup, or the grid rendered nothing")
    present = set(price_probe["grid_ids_present"])
    attributed = set(price_probe["grid_cards"])
    missing = sorted({str(l) for l in ALL_LISTINGS} - present)
    assert not missing, (
        "the grid did not render listings %r at all, so the price assertions "
        "below have nothing to look at. This is the renderer or the discovery "
        "predicate, not the probe." % (missing,))
    unread = sorted(present - attributed)
    assert not unread, (
        "listings %r are on the served page but the probe could not attribute "
        "their cards, so they are invisible to every assertion in this file. "
        "The card's listing-id attribute moved or was renamed." % (unread,))


def test_a_priced_listing_still_shows_its_price_in_the_grid(price_probe):
    """The control. Without it, deleting the price outright passes everything."""
    card = price_probe["grid_cards"].get(str(PRICED))
    assert card is not None, (
        "the priced listing rendered no card in the grid at all")
    assert _card_price(card) == PRICED_LABEL, (
        "the grid card for a priced listing does not show its price; its price "
        "element holds %r" % (_card_price(card),))


@pytest.mark.parametrize("listing_id", UNPRICED)
def test_an_unpriced_grid_card_has_no_price_pill(price_probe, listing_id):
    """No price means no element, not an element holding prose or nothing.

    Three claims, because two of them pass for a card that has quietly stopped
    rendering: the price element is absent outright, the card is otherwise
    structurally identical to the priced one, and neither the phrase nor the
    priced listing's label appears anywhere in it.

    The structural half is derived from the priced card rather than written as a
    literal, so that adding a line to the card does not have to be a change to
    this file -- the same reason the old assertion derived its expected pills
    from the priced card's.

    The one thing it cannot derive is which elements are *allowed* to leave with
    the price. That is a claim about the domain rather than about the markup, so
    it is stated once in ``PRICE_DEPENDENT`` with the server refusal it mirrors,
    and it is held to a control below: every element it names must be present on
    the priced card, so the licence can never quietly grow to cover an element
    that had stopped rendering anyway.
    """
    card = price_probe["grid_cards"].get(str(listing_id))
    assert card is not None, (
        "listing %d rendered no card in the grid, so this test is not looking "
        "at an unpriced card -- it is looking at nothing" % listing_id)
    price = _card_price(card)
    assert price is None, (
        "the grid card for unpriced listing %d rendered a price element holding "
        "%r. An absent price is an absent element: an element present and empty "
        "is a price the seller set to nothing, and one holding prose is a price "
        "invented on the way out" % (listing_id, price))
    priced = _card_parts(price_probe["grid_cards"][str(PRICED)])
    # Without this the comparison below is two empty lists agreeing. A card whose
    # classes this reader cannot see has no fingerprint, and no fingerprint
    # matches every other card that also has none.
    #
    # It is asserted over every name in `PRICE_DEPENDENT` rather than over the
    # price alone, because each name is an exemption: one that is absent from the
    # priced card too is excusing a difference that is not there, and would go on
    # excusing it if the element ever came back unpriced.
    missing = [p for p in PRICE_DEPENDENT if p not in priced]
    assert not missing, (
        "the priced card's fingerprint is %r, which is missing %r. Those names "
        "are exempted from the comparison below, so an exemption for an element "
        "the priced card does not carry silently licenses a difference instead "
        "of describing one. Either the class-attribute reader has gone blind, or "
        "the card stopped rendering an element this file still believes depends "
        "on the price." % (priced, missing))
    assert _card_parts(card) == [p for p in priced if p not in PRICE_DEPENDENT], (
        "the grid card for unpriced listing %d is built out of %r; it must carry "
        "every element the priced card carries (%r) except the price and the "
        "controls that depend on it (%r). Dropping the price must not drop "
        "anything else with it."
        % (listing_id, _card_parts(card), priced, list(PRICE_DEPENDENT)))
    assert INVENTED not in card and PRICED_LABEL not in card, (
        "the grid card for unpriced listing %d names a price somewhere outside "
        "its price element: %r" % (listing_id, card))


def test_the_grid_never_invents_a_price(price_probe):
    """The phrase is gone from the whole served document, script included.

    A weaker claim than the pill assertions above and deliberately kept beside
    them: it is the one that also covers the inline script's source, and it is
    the exact regression that shipped.
    """
    assert not price_probe["grid_invented"], (
        "the marketplace grid still serves the words %r" % INVENTED)


def test_a_priced_product_page_still_shows_its_price(price_probe):
    """The detail page's control, matching the grid's."""
    page = price_probe["pages"][str(PRICED)]
    assert page["status"] == 200 and page["title"], (
        "the priced listing's product page did not serve (%s)" % page["status"])
    assert page["panel"] is not None, (
        "the priced listing's product page rendered no purchase panel, so there "
        "is nowhere for a price to be -- which makes every 'no price element' "
        "claim below true for the wrong reason")
    assert page["price"] == PRICED_LABEL, (
        "the product page for a priced listing does not show its price; its "
        "price element holds %r" % (page["price"],))


@pytest.mark.parametrize("listing_id", UNPRICED)
def test_an_unpriced_product_page_has_no_price_element(price_probe, listing_id):
    """The page a shared link opens must agree with the card that links to it.

    The structural half of this claim -- that dropping the price drops nothing
    else -- is deliberately *not* made here, unlike on the grid card. The panel's
    remaining controls legitimately reshuffle when there is nothing to buy: with
    no add-to-cart taking the primary slot, "Message seller" is promoted into it
    and changes class. Pinning that here would pin a styling consequence, and it
    is already held where it belongs, against the renderer, in
    ``tests/test_storefront_add_to_cart.py``. What is unique to this file is the
    price, and the two surfaces agreeing about it.
    """
    page = price_probe["pages"][str(listing_id)]
    assert page["status"] == 200 and page["title"], (
        "listing %d's product page did not serve (%s), so nothing below is "
        "about an unpriced page" % (listing_id, page["status"]))
    # Without this the claim below is satisfied by a page that rendered no
    # purchase panel at all. "No price element" and "no region to hold one" are
    # the two failures this file has always kept separable.
    assert page["panel"] is not None, (
        "listing %d's product page rendered no purchase panel, so its missing "
        "price element proves nothing about the price" % listing_id)
    assert page["price"] is None, (
        "the product page for unpriced listing %d rendered a price element "
        "holding %r. An absent price is an absent element: an element present "
        "and empty is a price the seller set to nothing, and one holding prose "
        "is a price invented on the way out" % (listing_id, page["price"]))
    assert not page["invented"], (
        "listing %d's product page still serves the words %r"
        % (listing_id, INVENTED))


def test_the_grid_and_the_product_page_price_a_listing_the_same_way(price_probe):
    """Browsing and following a shared link must not disagree about the price.

    The two surfaces build their HTML separately, which is how one of them came
    to be fixed without the other in the first place. They still do, and they no
    longer emit comparable markup -- a storefront card against a purchase panel --
    so this cannot be a string comparison of the two regions.

    Comparing markup was never the point of it, though -- it was a proxy for "a
    member sees the same price in both places", with byte-equality standing in for
    "and no stray separator either". Both survive: the price *text* is compared
    exactly and unstripped, so a separator left inside either element fails here.

    This used to isolate the product page's price by difference -- "the pills a
    priced page has that an unpriced one does not" -- because the price was one
    anonymous pill among several and naming it would have meant going looking for
    the label. It is now a dedicated element carrying the ``data-mkt-price`` hook
    the variant picker rewrites, so it can be read directly, and the comparison
    gets stronger for it: one equality covers both "priced on one surface and not
    the other" and "priced differently", with no baseline to be wrong about.

    What the old isolation bought was that the test could not pass by finding what
    it went looking for. That is kept, as an explicit control: the priced listing
    must be priced on both surfaces, so a pair of surfaces that both rendered no
    price cannot satisfy the loop.
    """
    for listing_id in ALL_LISTINGS:
        card = price_probe["grid_cards"].get(str(listing_id))
        page = price_probe["pages"][str(listing_id)]
        assert card is not None and page["panel"] is not None, (
            "listing %d did not render on one of the two surfaces: grid card=%r "
            "product panel=%r" % (listing_id, card, page["panel"]))

        grid_price = _card_price(card)
        page_price = page["price"]
        # One equality, covering both halves: `None == None` is the agreed
        # unpriced shape, `"$19.99" == "$19.99"` the agreed priced one, and every
        # mixed pair -- including `None` against `""` -- is a disagreement.
        assert grid_price == page_price, (
            "listing %d shows %r in the grid and %r on its product page. A "
            "member who browses to it and a member who follows a shared link "
            "must be quoted the same price, and None is not the same as an "
            "element holding nothing."
            % (listing_id, grid_price, page_price))

    # The control. Everything above is equalities, and equalities are satisfied by
    # two surfaces that have both stopped rendering a price -- which is exactly
    # what this file's fix could be mistaken for if it went too far.
    assert _card_price(price_probe["grid_cards"][str(PRICED)]) == PRICED_LABEL, (
        "the priced listing has no price in the grid, so the agreement above is "
        "agreement about nothing")
    assert price_probe["pages"][str(PRICED)]["price"] == PRICED_LABEL, (
        "the priced listing has no price on its product page, so the agreement "
        "above is agreement about nothing")


@pytest.mark.parametrize("listing_id", ALL_LISTINGS)
def test_a_search_result_prices_a_listing_exactly_as_the_grid_does(
        price_probe, listing_id):
    """Searching and browsing must not disagree about the price either.

    This is the surface that used to be a hand-written JavaScript twin of the
    card, and the twin is why this file had two tests here: two renderers in two
    languages, one of which had already been fixed without the other. Search is
    now ``?q=`` rendered by the same ``product_card`` on the server, so the two
    tests collapse into one -- and into a stronger claim than either made, since
    the twin was only ever held to matching pill texts and separators, whereas the
    single renderer can be held to serving the identical card.

    Byte-identity is one of the two claims and the weaker one, so it is worth
    naming what it is worth: with a single renderer it is close to a comparison of
    an implementation against itself, and what it really pins is that there is
    still only one -- the day a second appears for search results, the cards stop
    being identical and this fails. That is a real regression guard but it is not a
    statement about prices, so the price claim is made directly as well, against
    the search document alone. If the grid and the search results were ever both
    wrong in the same way, the first assertion would pass and the second would not.
    """
    result = price_probe["search"][str(listing_id)]
    assert result["status"] == 200 and result["shows"], (
        "searching for listing %d served %s and did not show it, so this test is "
        "not comparing two cards -- it is comparing nothing"
        % (listing_id, result["status"]))
    assert not result["invented"], (
        "the search results for listing %d serve the words %r"
        % (listing_id, INVENTED))

    # The price the search result itself shows, judged on its own terms rather
    # than against the grid: the priced listing must carry its label and the
    # unpriced ones must carry no price element at all.
    price = _card_price(result["card"])
    expected = PRICED_LABEL if listing_id == PRICED else None
    assert price == expected, (
        "the search result for listing %d shows %r where it should show %r; "
        "search would price a listing the grid leaves unpriced"
        % (listing_id, price, expected))

    found = _content(result["card"])
    served = _content(price_probe["grid_cards"].get(str(listing_id)))
    assert found is not None, (
        "listing %d is named in its own search results but rendered no card "
        "there" % listing_id)
    assert served is not None, (
        "listing %d rendered no card in the full grid, so there is nothing to "
        "compare the search result against" % listing_id)
    assert found == served, (
        "listing %d renders a different card in search results than in the "
        "grid.\nsearch: %r\ngrid:   %r" % (listing_id, found, served))
