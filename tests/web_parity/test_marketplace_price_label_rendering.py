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

Every assertion below is therefore made against bytes served by the app. Reading
a template for the absence of a string would be exactly the class of check that
failed here: it passes for a template that never renders a price at all, and it
passes for one that renders an empty price element, which is not "no price" but
"a price the seller set to nothing".

## Three price values, not one

``""`` is the value a dropship import writes. ``"   "`` is what a hand-edited or
migrated row can hold, and is the case a bare ``or`` fallback never catches --
whitespace is truthy, so the old code would have passed it through and rendered
an empty pill. Both must render identically: no price element.

## What changed when the storefront was rebuilt

The markup these assertions read is new. The grid used to serve
``<article class='card'>`` holding a ``<p>`` of ``<span class="pill">`` elements,
one of which was the price; it now serves ``<article class="mkt-card">`` whose
price is its own ``<p class="mkt-card-price">``, and the product page's price is
``<span class="mkt-price-value">``. So the *anchors* below were re-derived, and
one pair of tests lost its subject entirely:

The grid no longer ships a second card renderer. ``marketplaceListingHtml`` --
the inline JavaScript twin that search results were built from, and half of the
drift this file was written about -- is gone, because search is now a
server-rendered GET against the same route and therefore the same renderer. The
two tests that executed that twin in node and compared it byte-for-byte with the
served card are replaced by one test asserting the twin is *absent*, so that
reintroducing a JavaScript card is a failure here rather than a silent return of
the two-renderer problem. If one ever is reintroduced, those two tests should be
restored to their original shape from git history rather than left deleted: the
claim they made (a member cannot tell which renderer produced a card) becomes
live again the moment there are two renderers.

The old anchor is also gone and did not need replacing. ``pill_paragraph`` keyed
off an unconditionally-emitted category pill so that "the paragraph is missing"
stayed distinguishable from "the price within it is missing". The new card needs
no such proxy: it is keyed by the product link that carries its own listing id,
so a missing card and a missing price are already two different findings.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile

import pytest

from tests.probe_report import parse_report

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: A listing whose seller priced it. The control: every "no price" assertion
#: below is worthless unless a price appears when there *is* one, and this is
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

#: The class the grid card puts its price in, and the class the product page puts
#: its price in. Named here rather than inline because three tests read them and
#: they are the only two places on the web where a marketplace price is printed.
CARD_PRICE_CLASS = "mkt-card-price"
PAGE_PRICE_CLASS = "mkt-price-value"

#: The one *other* thing an absent price is allowed to change about a card. The
#: stylesheet keys the reserved price height off this flag, applied to the
#: title's bottom margin, because the obvious alternative -- an empty
#: `<p class="mkt-card-price">` holding the space -- is precisely what
#: `test_an_unpriced_grid_card_has_no_price_element` forbids. Naming the pair
#: here keeps the shape assertion exact: an unpriced card differs from a priced
#: one by the missing price and by this flag, and by nothing else.
CARD_BODY_CLASS = "mkt-card-body"
CARD_BODY_UNPRICED_CLASS = "mkt-card-body is-unpriced"

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

CARD = re.compile(r'<article class="mkt-card">.*?</article>', re.S)

grid = client.get("/pulse/marketplace").get_data(as_text=True)
report["grid_status"] = 200
report["grid_invented"] = %(invented)r in grid

# Whole cards, keyed by the listing id in their own product link. The card is the
# unit of comparison: a card that did not render at all and a card that rendered
# without a price are different findings and must not collapse into one.
grid_cards = {}
for card in CARD.findall(grid):
    found = re.search(r'/pulse/marketplace/(\d+)"', card)
    if found:
        grid_cards[found.group(1)] = card
report["grid_cards"] = grid_cards

# Is a second renderer of the *marketplace* card being shipped to the browser
# again? Two scopes, both deliberate.
#
# Sources: the inline document plus the marketplace's own asset. The retired twin
# was inline, so the document has to be read; but nothing would stop the next one
# from living in `pulse_marketplace.js`, so a check that read only the document
# would call that clean. The shell's other bundles are out of scope on purpose --
# they ship `notificationCard` and `enhanceMobileReelCard`, which render different
# objects, and failing on those would make this test a tax on unrelated work.
#
# Names: a builder whose name mentions the thing at risk. `notificationCard` is
# not a second renderer of a marketplace card; `marketplaceListingHtml`,
# `renderProductCard` and `listingCardHtml` all are.
CARD_FN = re.compile(
    r"(?:function\s+|(?:const|let|var)\s+)"
    r"(\w*(?:[Ll]isting|[Pp]roduct|[Mm]arketplace)\w*)\s*(?:\(|=\s*(?:\(|function))")
scripts = {"(inline)": grid}
for src in re.findall(r'<script[^>]+src="([^"]+)"', grid):
    if src.startswith("/") and "marketplace" in src:
        asset = client.get(src)
        if asset.status_code == 200:
            scripts[src] = asset.get_data(as_text=True)
report["grid_scripts"] = sorted(scripts)
report["grid_js_card"] = sorted({
    "%%s:%%s" %% (where, name)
    for where, source in scripts.items() for name in CARD_FN.findall(source)
})

pages = {}
for lid in %(ids)r:
    response = client.get("/pulse/marketplace/%%d" %% lid)
    body = response.get_data(as_text=True)
    pages[str(lid)] = {
        "status": response.status_code,
        # Every element bearing the product page's price class, with its text.
        "price_elements": re.findall(
            r'<span class="%(page_price)s"[^>]*>(.*?)</span>', body, re.S),
        "invented": %(invented)r in body,
        "title": ("Listing %%d" %% lid) in body,
    }
report["pages"] = pages

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
        "page_price": PAGE_PRICE_CLASS,
    }
    proc = subprocess.run([sys.executable, "-c", code], cwd=REPO, env=env,
                          capture_output=True, text=True, timeout=900)
    return parse_report(proc.stdout, proc.stderr)


def _card(price_probe, listing_id):
    """A listing's grid card, or None when it rendered none."""
    return price_probe["grid_cards"].get(str(listing_id))


def _elements(card):
    """The ``(tag, class)`` of every element in a card, in document order.

    The shape of a card rather than its text, so "unpriced renders everything a
    priced card renders except the price" can be asserted without pinning the
    card's full markup -- the derived-not-literal discipline the pill version of
    this file used, carried over. Adding an element to the card therefore does
    not require editing this file; adding one *only* to unpriced cards does.
    """
    if card is None:
        return None
    return re.findall(r'<([a-z0-9]+)\b[^>]*?\bclass="([^"]*)"', card)


def _card_price_texts(card):
    """The text of every price element in a card, in order. ``[]`` means none."""
    if card is None:
        return None
    return re.findall(
        r'<p class="%s[^"]*"[^>]*>(.*?)</p>' % CARD_PRICE_CLASS, card, re.S)


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


def test_every_seeded_listing_renders_a_card_at_all(price_probe):
    """Separated out so no assertion below can pass by looking at nothing.

    Each of the tests that follow needs a card to examine, and each used to say
    so itself. Saying it once here means a visibility or pagination change that
    dropped these listings from the grid reports as one clear failure rather than
    as five tests each claiming something about the price.
    """
    missing = [l for l in ALL_LISTINGS if _card(price_probe, l) is None]
    assert missing == [], (
        "listings %r rendered no grid card, so the price assertions in this file "
        "would be examining nothing. The seeded seller is approved and visible, "
        "so this is a grid, visibility or pagination change rather than a price "
        "one." % missing)


def test_a_priced_listing_still_shows_its_price_in_the_grid(price_probe):
    """The control. Without it, deleting the price outright passes everything."""
    prices = _card_price_texts(_card(price_probe, PRICED))
    assert prices == [PRICED_LABEL], (
        "the grid card for a priced listing should print exactly its price once; "
        "it printed %r" % (prices,))


@pytest.mark.parametrize("listing_id", UNPRICED)
def test_an_unpriced_grid_card_has_no_price_element(price_probe, listing_id):
    """No price means no element, not an element holding prose or nothing.

    Asserted as "the priced card's elements, minus the price element" rather than
    "the card does not contain the phrase", because an empty
    ``<p class="mkt-card-price"></p>`` contains no phrase either and is still a
    price the seller did not set. Derived from the priced card rather than
    written as a literal so that adding an element to the card does not have to
    be a change to this file.

    Two differences are expected, not one: the price element goes, and the body
    picks up ``is-unpriced``. Removing an element also removes the flex gap above
    it, so a card that simply dropped its price pulled its seller row and stock
    line up by 42px and showed them where its neighbours show a price -- the
    grid's "uniform by construction" claim, quietly false. The flag is how the
    stylesheet gives that height back without emitting the empty paragraph this
    test exists to forbid. Both halves are stated so the assertion stays exact:
    anything *else* that differs is still a failure.
    """
    card = _card(price_probe, listing_id)
    assert _card_price_texts(card) == [], (
        "the grid card for unpriced listing %d rendered a price element holding "
        "%r; no price must mean no element"
        % (listing_id, _card_price_texts(card)))
    priced = _elements(_card(price_probe, PRICED))
    # The expectation below rewrites the body class, so it would also accept a
    # priced card that carried the flag -- and a priced card carrying the flag
    # gets the reserved price height *on top of* its real price, which is the
    # 42px misalignment again, this time on every card in the grid. Pin the
    # control before deriving from it.
    assert CARD_BODY_UNPRICED_CLASS not in [cls for _tag, cls in priced], (
        "the priced control card is flagged %r, so every card reserves height "
        "for a price it already shows, and the derivation below cannot see it"
        % CARD_BODY_UNPRICED_CLASS)
    assert _elements(card) == [
        (tag, CARD_BODY_UNPRICED_CLASS if cls == CARD_BODY_CLASS else cls)
        for tag, cls in priced
        if not cls.startswith(CARD_PRICE_CLASS)
    ], (
        "the grid card for unpriced listing %d has a different shape than the "
        "priced card with its price removed.\n  unpriced: %r\n  priced:   %r"
        % (listing_id, _elements(card), priced))
    assert INVENTED not in card, (
        "the grid card for unpriced listing %d serves the words %r"
        % (listing_id, INVENTED))


def test_the_grid_never_invents_a_price(price_probe):
    """The phrase is gone from the whole served document, script included.

    A weaker claim than the per-card assertions above and deliberately kept
    beside them: it covers every byte of the page rather than the inside of a
    card, and it is the exact regression that shipped.
    """
    assert not price_probe["grid_invented"], (
        "the marketplace grid still serves the words %r" % INVENTED)


def test_a_priced_product_page_still_shows_its_price(price_probe):
    """The detail page's control, matching the grid's."""
    page = price_probe["pages"][str(PRICED)]
    assert page["status"] == 200 and page["title"], (
        "the priced listing's product page did not serve (%s)" % page["status"])
    assert page["price_elements"] == [PRICED_LABEL], (
        "the product page for a priced listing should print exactly its price "
        "once; it printed %r" % (page["price_elements"],))


@pytest.mark.parametrize("listing_id", UNPRICED)
def test_an_unpriced_product_page_has_no_price_element(price_probe, listing_id):
    """The page a shared link opens must agree with the card that links to it."""
    page = price_probe["pages"][str(listing_id)]
    assert page["status"] == 200 and page["title"], (
        "listing %d's product page did not serve (%s), so nothing below is "
        "about an unpriced page" % (listing_id, page["status"]))
    assert page["price_elements"] == [], (
        "listing %d's product page rendered a price element holding %r"
        % (listing_id, page["price_elements"]))
    assert not page["invented"], (
        "listing %d's product page still serves the words %r"
        % (listing_id, INVENTED))


def test_the_grid_and_the_product_page_price_a_listing_the_same_way(price_probe):
    """Browsing and following a shared link must not disagree about the price.

    The two surfaces are now built by one renderer
    (``services/marketplace_storefront.py``), which is the structural reason they
    can no longer drift -- but they call it through two different Flask routes
    reading two different SQL statements, so *which row* each one prices is still
    answered twice. That is the half of the original divergence that survives the
    rebuild, and it is what this compares: same price text, and the same answer
    to "is there a price at all".
    """
    for listing_id in ALL_LISTINGS:
        card = [text.strip() for text in _card_price_texts(_card(price_probe, listing_id))]
        page = [text.strip() for text in price_probe["pages"][str(listing_id)]["price_elements"]]
        assert card == page, (
            "listing %d prices as %r in the grid and %r on its product page"
            % (listing_id, card, page))


def test_the_grid_ships_no_second_card_renderer(price_probe):
    """The structural half of this bug family, asserted as absent.

    "Request access" reached three sites, and the third was a JavaScript twin of
    the card -- ``marketplaceListingHtml`` -- that search results were built
    from. Two renderers of one card is how the fix landed in one of them and not
    the other, and no amount of testing the server's card can see the twin.

    Search is now a server-rendered GET against this same route, so there is one
    renderer and nothing to keep in sync. This test holds that: a function
    shipped to the browser whose name says it builds a listing or a card is the
    two-renderer problem returning. If that is ever a deliberate choice, restore
    the two tests this replaced (``test_the_client_side_card_agrees_with_the_
    server_rendered_one`` and ``..._emits_the_same_markup_as_the_server``, in git
    history before the storefront rebuild) rather than relaxing this one -- they
    executed the twin in node and compared bytes, which is the only check that
    can actually see the drift.
    """
    # Anti-vacuity first: if the probe fetched no script at all then the scan
    # below found nothing because it read nothing.
    assert price_probe["grid_scripts"], "the probe collected no scripts to scan"
    assert any(name != "(inline)" for name in price_probe["grid_scripts"]), (
        "the probe scanned only the inline document; the marketplace page is "
        "supposed to load %r, so either the asset stopped being referenced or the "
        "test client could not fetch it -- and a twin living in that file would "
        "now go unnoticed" % "/static/js/pulse_marketplace.js")
    assert price_probe["grid_js_card"] == [], (
        "the marketplace grid ships client-side card renderer(s) %r. A second "
        "renderer of the same card is what let one of them be fixed and the "
        "other not." % (price_probe["grid_js_card"],))
