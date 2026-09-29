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
been tested by three of them, in order:

* The search surface used to be an inline JavaScript twin of the card, rendered
  in the browser, and this file used to execute it under node. Search is now
  ``?q=`` rendered on the server by the same function as the grid, so the twin
  and the node harness are gone.
* The member grid moved to the storefront engine, which emits a nest of classed
  elements where the old card emitted one paragraph of pills.
* The product page followed it (``render_product``, 90dfe0a1a), which is what
  retired the pill paragraph this file used to read it through.

None of those was a change to what a member is owed, and the property each
assertion names is unchanged, because none of them named a template. They named
what a member is served. What has to be re-pointed on each swap is the
*extraction* -- and a stale extraction here does not read as "the probe cannot
see the page", it reads as "the page renders no price", which is this file's own
subject. So each reader below carries a vacuity guard.

Two shapes in particular have to stay failures rather than becoming "no price": a
surface that never renders a price at all, and one that renders its price element
*empty*, which is not "no price" but "a price the seller set to nothing". Every
claim below is paired with a control that a price *does* appear where one exists,
so neither can pass by rendering nothing.

## What an unpriced listing is allowed to lose with its price

Its add-to-cart, and only that. ``mw.cart_affordance`` answers
``CART_HIDDEN_NO_PRICE`` for a listing whose price is unknown, mirroring
``POST /api/pulse/marketplace/cart``, which refuses ``price_minor <= 0`` with 400
``ITEM_UNAVAILABLE``. So an unpriced listing is genuinely not purchasable and a
button offered on one would be a button that always fails. Both surfaces withhold
it, and the tests below name that coupling as a closed set rather than tolerating
whatever happens to differ -- an unpriced card that also dropped its seller line
or its photograph is still a regression here.

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

# The two surfaces are read with two different extractions, deliberately. Both
# are the storefront engine now, but they are not one renderer: the grid emits a
# storefront card and the product page emits a purchase panel, and the panel is
# the richer of the two because it is the only one with a variant picker behind
# it. Reading each with the pattern that fits it is what lets the agreement test
# below compare the price the two surfaces *arrive at* instead of the markup they
# arrive at it in -- which they do not share and are not required to.
CARD = re.compile(r'<article class="mkt-card">.*?</article>', re.S)
CARD_LINK = re.compile(r'class="mkt-card-link" href="/pulse/marketplace/(\d+)"')


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
    '''The product page's purchase panel, or None. Where its price lives.

    This used to be `pill_paragraph`, which read the price out of a `<p>` of
    `<span class="pill">`. That paragraph belonged to the template this bug
    family shipped in, and the product page no longer runs it -- it renders
    through `marketplace_storefront.render_product`, which prints the price in a
    `.mkt-price` block inside this panel. The old reader returned None against
    every page, which the assertions correctly reported as "no price rendered"
    and which was in fact "no paragraph to find one in".

    Scoped to the panel rather than the whole document for the reason the grid
    card is scoped to one `<article>`: the page also carries a related-products
    rail of real cards with real prices on it, and a price read from the document
    at large could be a neighbouring product's. The panel is labelled, emitted
    unconditionally, and holds the price, the stock line and every buy control, so
    a None here means the panel itself is gone rather than the price within it.
    '''
    found = re.search(
        r'<section class="mkt-panel" aria-label="Purchase options">.*?</section>',
        html, re.S)
    return None if found is None else found.group(0)


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
    pages[str(lid)] = {
        "status": response.status_code,
        "panel": purchase_panel(body),
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


def _product_price(panel):
    """The raw contents of a product page's price element, or ``None``.

    The same three-way reading as ``_card_price`` below and for the same reason:
    ``None`` is "no price element", ``""`` is "a price the seller set to nothing",
    and the two must not be conflated. Returned unstripped so a stray separator
    fails the comparisons rather than being tidied away by the reader.
    """
    if panel is None:
        return None
    found = re.search(
        r'<span class="mkt-price-value"[^>]*>(.*?)</span>', panel, re.S)
    return None if found is None else found.group(1)


def _panel_hooks(panel):
    """The ``data-mkt-*`` hooks the purchase panel carries, deduplicated.

    This is the product page's port of "the priced card's pills, minus the price"
    -- the claim that dropping a price drops the price and nothing else.

    Read from the script hooks rather than from the CSS classes, which is the one
    place this port is not a transliteration of the card's. The panel's classes
    carry *visual weight* as well as identity: it shows one filled action, so
    Message seller is a `.mkt-ghost` beside an Add to cart and promotes itself to
    `.mkt-cta` when there is none. An unpriced panel therefore differs from a
    priced one by a class that has nothing to do with whether a price was
    invented, and a class fingerprint would either fail on that or have to encode
    a styling rule to excuse it. The `data-mkt-*` attributes are what the scripts
    bind to, so they name the affordances themselves and do not move when one
    changes weight.

    Deduplicated and sorted because these are a set of affordances, not an
    ordering: three separate `.mkt-ghost` buttons are three hooks with three
    distinct names, so nothing is lost by not counting repeats.
    """
    if panel is None:
        return None
    return sorted(set(re.findall(r"\bdata-mkt-([a-z]+(?:-[a-z]+)*)", panel)))


#: The panel hooks an unpriced listing is expected to lose along with its price,
#: and the only ones. ``price`` is the price itself; ``add`` is the button, absent
#: by ``CART_HIDDEN_NO_PRICE`` as the module docstring explains; ``variant`` is the
#: field that button posts and exists only alongside it.
#:
#: Note that ``variants`` -- the form's client-side variant table -- is *not* in
#: this set and is present on both, which is why these are matched as whole
#: attribute names rather than by prefix.
PRICE_COUPLED_HOOKS = ("price", "add", "variant")


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


#: The card elements an unpriced listing is expected to lose along with its price,
#: and the only ones. The price element, then the add-to-cart row and its button,
#: withheld by ``CART_HIDDEN_NO_PRICE`` -- see the module docstring.
#:
#: Named as a closed set rather than derived by diffing the two fingerprints,
#: because the property this file holds is that *nothing else* goes with the
#: price. "Ignore whatever differs" would report an unpriced card that had also
#: lost its seller line as agreement.
PRICE_COUPLED_PARTS = ("mkt-card-price", "mkt-card-actions", "mkt-add")


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

    Four claims, because three of them pass for a card that has quietly stopped
    rendering: the price element is absent outright, the card is otherwise
    structurally identical to the priced one bar the purchase controls, it offers
    no add-to-cart, and neither the phrase nor the priced listing's label appears
    anywhere in it.

    The structural half is derived from the priced card rather than written as a
    literal, so that adding a line to the card does not have to be a change to
    this file -- the same reason the old assertion derived its expected pills
    from the priced card's.

    The add-to-cart is the one thing an unpriced card is allowed to lose with its
    price, so it is subtracted from that comparison -- and then pinned back
    positively, because subtracting it without re-asserting it would leave this
    file unable to notice a quick-add appearing on a listing that cannot be
    bought. The module docstring has the reason it cannot.
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
    priced_card = price_probe["grid_cards"][str(PRICED)]
    priced = _card_parts(priced_card)
    # Without this the comparison below is two empty lists agreeing. A card whose
    # classes this reader cannot see has no fingerprint, and no fingerprint
    # matches every other card that also has none.
    assert "mkt-card-price" in priced, (
        "the priced card's fingerprint is %r, which does not include a price "
        "element -- so the comparison below would pass for any card at all. The "
        "class-attribute reader has gone blind, not the renderer." % (priced,))
    assert _card_parts(card) == [p for p in priced if p not in PRICE_COUPLED_PARTS], (
        "the grid card for unpriced listing %d is built out of %r; it must carry "
        "every element the priced card carries (%r) except the price and the "
        "purchase controls %r. Dropping the price must not drop anything else "
        "with it."
        % (listing_id, _card_parts(card), priced, list(PRICE_COUPLED_PARTS)))
    # The other half of that subtraction. Vacuity-guarded against the priced card,
    # so this cannot pass by the grid having stopped offering quick-add at all --
    # which would be a real change, owned by `test_storefront_add_to_cart.py`, and
    # should be read here as "that coupling is no longer testable from this file"
    # rather than as an unpriced card behaving itself.
    assert "data-mkt-add" in priced_card, (
        "the priced card offers no add-to-cart, so the claim below -- that an "
        "unpriced one does not either -- is true of every card on the page and "
        "proves nothing about the price")
    assert "data-mkt-add" not in card, (
        "the grid card for unpriced listing %d offers an add-to-cart. The cart "
        "route refuses a listing with no price (400 ITEM_UNAVAILABLE), so this "
        "is a button that cannot work: %r" % (listing_id, card))
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
        "is nowhere for a price to be. The panel is emitted unconditionally, so "
        "this is the extraction or the renderer -- not a missing price.")
    assert _product_price(page["panel"]) == PRICED_LABEL, (
        "the product page for a priced listing does not show its price; its "
        "price element holds %r" % (_product_price(page["panel"]),))


@pytest.mark.parametrize("listing_id", UNPRICED)
def test_an_unpriced_product_page_has_no_price_pill(price_probe, listing_id):
    """The page a shared link opens must agree with the card that links to it.

    The grid card's three claims, on the page's own shape: the price element is
    absent rather than empty, the panel is otherwise the priced one minus the
    purchase controls, and the phrase is nowhere in the document.
    """
    page = price_probe["pages"][str(listing_id)]
    assert page["status"] == 200 and page["title"], (
        "listing %d's product page did not serve (%s), so nothing below is "
        "about an unpriced page" % (listing_id, page["status"]))
    panel = page["panel"]
    assert panel is not None, (
        "listing %d's product page rendered no purchase panel at all, so this "
        "test is not looking at an unpriced page -- it is looking at nothing"
        % listing_id)
    price = _product_price(panel)
    assert price is None, (
        "the product page for unpriced listing %d rendered a price element "
        "holding %r. An absent price is an absent element: an element present "
        "and empty is a price the seller set to nothing, and one holding prose "
        "is a price invented on the way out" % (listing_id, price))
    priced = _panel_hooks(price_probe["pages"][str(PRICED)]["panel"])
    # The same guard the grid's fingerprint carries, for the same reason: a panel
    # whose hooks this reader cannot see has no hooks, and no hooks match every
    # other panel that also has none.
    assert "price" in priced, (
        "the priced panel's hooks are %r, which do not include the price -- so "
        "the comparison below would pass for any panel at all. The hook reader "
        "has gone blind, not the renderer." % (priced,))
    assert _panel_hooks(panel) == [h for h in priced if h not in PRICE_COUPLED_HOOKS], (
        "the product page for unpriced listing %d offers %r; it must offer "
        "everything the priced page offers (%r) except the price and the "
        "purchase controls %r. Dropping the price must not drop the seller "
        "contact, Save or Report with it."
        % (listing_id, _panel_hooks(panel), priced, list(PRICE_COUPLED_HOOKS)))
    assert not page["invented"], (
        "listing %d's product page still serves the words %r"
        % (listing_id, INVENTED))


def test_the_grid_and_the_product_page_price_a_listing_the_same_way(price_probe):
    """Browsing and following a shared link must not disagree about the price.

    The two surfaces build their HTML separately, which is how one of them came
    to be fixed without the other in the first place. They are both the storefront
    engine now, but they are still two renderers -- a card and a purchase panel --
    so this cannot be a string comparison of their markup.

    Comparing markup was never the point of it, though -- it was a proxy for "a
    member sees the same price in both places", with byte-equality standing in for
    "and no stray separator either". Both survive, and more directly than before:
    each surface's price element is read on its own terms and the *text* is
    compared exactly, unstripped, so a separator left inside either one fails here.

    Neither side is read by looking for the label, so this cannot pass by finding
    what it went looking for; and the three-way reading of each reader keeps
    "absent" distinct from "empty", so a surface that renders an empty price
    element does not agree with one that renders none.
    """
    # Both surfaces answering None for all three listings is agreement, and it is
    # also what two broken readers look like. This is what excludes that: the
    # priced listing must produce a real price on both, so at least one comparison
    # below is between two present prices.
    assert _card_price(price_probe["grid_cards"].get(str(PRICED))) == PRICED_LABEL, (
        "the priced listing shows no price in the grid, so every comparison "
        "below is None against None and agreement is vacuous")
    assert _product_price(price_probe["pages"][str(PRICED)]["panel"]) == PRICED_LABEL, (
        "the priced listing shows no price on its product page, so every "
        "comparison below is None against None and agreement is vacuous")

    for listing_id in ALL_LISTINGS:
        card = price_probe["grid_cards"].get(str(listing_id))
        panel = price_probe["pages"][str(listing_id)]["panel"]
        assert card is not None and panel is not None, (
            "listing %d did not render on one of the two surfaces: grid card=%r "
            "product panel=%r" % (listing_id, card, panel))

        assert _card_price(card) == _product_price(panel), (
            "listing %d shows %r in the grid and %r on its product page. A "
            "member browsing and a member following a shared link must not be "
            "told different things about the price."
            % (listing_id, _card_price(card), _product_price(panel)))


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
