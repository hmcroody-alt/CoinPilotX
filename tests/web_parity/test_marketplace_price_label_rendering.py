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

Every assertion below is therefore made against bytes served by the app, and the
one surface whose output is JavaScript is *executed*, not read. Reading the
script source for the absence of a string would be exactly the class of check
that failed here: it passes for a template that never renders a price at all,
and it passes for one that renders an empty ``<span class="pill"></span>``,
which is not "no price" but "a price the seller set to nothing".

## Three price values, not one

``""`` is the value a dropship import writes. ``"   "`` is what a hand-edited or
migrated row can hold, and is the case a bare ``or`` fallback never catches --
whitespace is truthy, so the old code would have passed it through and rendered
an empty pill. A missing key covers the client-side twin being handed a row from
an older serializer. All three must render identically: no pill.
"""

from __future__ import annotations

import json
import os
import re
import shutil
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

CARD = re.compile(r"<article class='card'>.*?</article>", re.S)


def pill_paragraph(html):
    '''The first paragraph carrying any pill, or None.

    This used to key off the Safety pill, on the grounds that it was the one
    element of the row always present -- which kept "the price paragraph is
    gone" and "the price pill is gone" distinguishable. That pill has been
    removed: it printed `marketplace_listings.safety_score`, which holds the
    reviewer's *risk* number, so the worst listing the engine can score read
    "Safety 100" to a buyer.

    The category pill inherits the job. It is emitted unconditionally by both
    surfaces (`row.get('category') or 'Education'`), so a None here still means
    the paragraph itself is missing rather than the price within it.
    '''
    for para in re.findall(r"<p>.*?</p>", html, re.S):
        if re.search(r"class=['\"]pill['\"]>", para):
            return para
    return None


grid = client.get("/pulse/marketplace").get_data(as_text=True)
report["grid_status"] = 200
report["grid_invented"] = %(invented)r in grid
grid_cards = {}
for card in CARD.findall(grid):
    found = re.search(r"/pulse/marketplace/(\d+)'", card)
    if found:
        grid_cards[found.group(1)] = pill_paragraph(card)
report["grid_paragraphs"] = grid_cards

pages = {}
for lid in %(ids)r:
    response = client.get("/pulse/marketplace/%%d" %% lid)
    body = response.get_data(as_text=True)
    pages[str(lid)] = {
        "status": response.status_code,
        "paragraph": pill_paragraph(body),
        "invented": %(invented)r in body,
        "title": ("Listing %%d" %% lid) in body,
    }
report["pages"] = pages

# The client-side card, lifted out of the served page rather than out of bot.py,
# so what the test executes is what a browser would have been handed.
js = []
for pattern in (r"const marketplaceCurrentUserId=[^\n]*",
                r"const marketplaceEsc=[^\n]*",
                r"function marketplaceListingHtml\(row\)\{[^\n]*"):
    found = re.search(pattern, grid)
    js.append(found.group(0).strip() if found else "")
report["js"] = js

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


def _render_client_side(price_probe, rows):
    """Run the served ``marketplaceListingHtml`` over ``rows`` and return its HTML.

    The twin only exists as rendered markup once it has run, and the whole point
    of this file is to assert on rendered markup, so it is executed rather than
    read.
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed; the client-side card cannot be rendered")
    js = price_probe["js"]
    assert all(js), (
        "the served marketplace page no longer contains the three declarations "
        "the client-side card is built from (%r); the card was renamed, moved "
        "or reformatted onto several lines" % (js,))
    harness = "\n".join(js) + (
        "\nconsole.log(JSON.stringify(JSON.parse(process.argv[1])"
        ".map(marketplaceListingHtml)));")
    proc = subprocess.run([node, "-e", harness, json.dumps(rows)],
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, (
        "the client-side marketplace card did not run: %s" % proc.stderr[-2000:])
    return json.loads(proc.stdout)


def _paragraph(html):
    """The pill paragraph of a rendered card, mirroring the probe's extraction."""
    for para in re.findall(r"<p>.*?</p>", html, re.S):
        if re.search(r"class=['\"]pill['\"]>", para):
            return para
    return None


def _pills(paragraph):
    """The pill texts inside a pill paragraph, in order."""
    if paragraph is None:
        return None
    return re.findall(r"<span class=['\"]pill['\"]>(.*?)</span>", paragraph, re.S)


def _normalized(paragraph):
    """A paragraph with its quote style flattened.

    The server writes ``class='pill'`` and the client-side twin writes
    ``class="pill"``. That is not a difference a member can see, and it is the
    only one the two surfaces are allowed.
    """
    return None if paragraph is None else paragraph.replace('"', "'")


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


def test_a_priced_listing_still_shows_its_price_in_the_grid(price_probe):
    """The control. Without it, deleting the pill outright passes everything."""
    pills = _pills(price_probe["grid_paragraphs"].get(str(PRICED)))
    assert pills is not None, (
        "the priced listing rendered no card in the grid at all")
    assert PRICED_LABEL in pills, (
        "the grid card for a priced listing does not show its price; pills "
        "were %r" % (pills,))


@pytest.mark.parametrize("listing_id", UNPRICED)
def test_an_unpriced_grid_card_has_no_price_pill(price_probe, listing_id):
    """No price means no element, not an element holding prose or nothing.

    Asserted as "the priced card's pills, minus the price" rather than "the
    card does not contain the phrase", because an empty
    ``<span class="pill"></span>`` contains no phrase either and is still a
    price the seller did not set. Derived from the priced card rather than
    written as a literal so that adding a pill to the card does not have to be
    a change to this file.
    """
    pills = _pills(price_probe["grid_paragraphs"].get(str(listing_id)))
    assert pills is not None, (
        "listing %d rendered no card in the grid, so this test is not looking "
        "at an unpriced card -- it is looking at nothing" % listing_id)
    priced = _pills(price_probe["grid_paragraphs"][str(PRICED)])
    assert pills == [p for p in priced if p != PRICED_LABEL], (
        "the grid card for unpriced listing %d rendered %r; it must carry every "
        "pill the priced card carries (%r) except the price, and no empty or "
        "invented stand-in for it" % (listing_id, pills, priced))
    assert "" not in pills, (
        "the grid card for unpriced listing %d rendered an empty pill, which "
        "reads as a price set to nothing rather than no price" % listing_id)


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
    assert PRICED_LABEL in (_pills(page["paragraph"]) or []), (
        "the product page for a priced listing does not show its price; pills "
        "were %r" % (_pills(page["paragraph"]),))


@pytest.mark.parametrize("listing_id", UNPRICED)
def test_an_unpriced_product_page_has_no_price_pill(price_probe, listing_id):
    """The page a shared link opens must agree with the card that links to it."""
    page = price_probe["pages"][str(listing_id)]
    assert page["status"] == 200 and page["title"], (
        "listing %d's product page did not serve (%s), so nothing below is "
        "about an unpriced page" % (listing_id, page["status"]))
    pills = _pills(page["paragraph"])
    priced = _pills(price_probe["pages"][str(PRICED)]["paragraph"])
    assert pills == [p for p in priced if p != PRICED_LABEL], (
        "the product page for unpriced listing %d rendered %r; the priced "
        "page renders %r" % (listing_id, pills, priced))
    assert "" not in (pills or []), (
        "listing %d's product page rendered an empty price pill" % listing_id)
    assert not page["invented"], (
        "listing %d's product page still serves the words %r"
        % (listing_id, INVENTED))


def test_the_grid_and_the_product_page_price_a_listing_the_same_way(price_probe):
    """Browsing and following a shared link must not disagree about the price.

    The two surfaces build their HTML separately, which is how one of them came
    to be fixed without the other in the first place. Compared as markup rather
    than as pill text so a stray separator left behind by one of them fails
    here too.
    """
    for listing_id in ALL_LISTINGS:
        grid = price_probe["grid_paragraphs"].get(str(listing_id))
        page = price_probe["pages"][str(listing_id)]["paragraph"]
        assert grid is not None and page is not None, (
            "listing %d rendered no pill paragraph on one of the two surfaces: "
            "grid=%r page=%r" % (listing_id, grid, page))
        assert grid == page, (
            "listing %d renders %r in the grid and %r on its product page"
            % (listing_id, grid, page))


def test_the_client_side_card_agrees_with_the_server_rendered_one(price_probe):
    """Search replaces the grid's cards with these, over the API's payload.

    Rendered by executing the served script, because the two cards are written
    in different languages in different files and have already drifted once.
    The rows mirror what ``pulse_marketplace_listing_payload`` emits: a blank
    ``price_label`` for an unpriced listing.
    """
    rows = [
        {"id": PRICED, "title": "Listing", "category": "Education",
         "price_label": PRICED_LABEL, "safety_score": 44},
        {"id": BLANK, "title": "Listing", "category": "Education",
         "price_label": "", "safety_score": 44},
        {"id": WHITESPACE, "title": "Listing", "category": "Education",
         "price_label": "   ", "safety_score": 44},
        # A row from a serializer that does not send the key at all.
        {"id": 0, "title": "Listing", "category": "Education", "safety_score": 44},
    ]
    rendered = [_pills(_paragraph(h))
                for h in _render_client_side(price_probe, rows)]
    priced, blank, whitespace, missing = rendered
    assert priced == ["Education", PRICED_LABEL], (
        "the client-side card does not render a price it was given: %r" % (priced,))
    for name, pills in (("blank", blank), ("whitespace", whitespace),
                        ("missing", missing)):
        assert pills == ["Education"], (
            "the client-side card rendered %r for a %s price_label; search "
            "results would price a listing the grid leaves unpriced"
            % (pills, name))


def test_the_client_side_card_emits_the_same_markup_as_the_server(price_probe):
    """Byte-level agreement with the served card, not just the same pill texts.

    A member must not be able to tell whether a card came from the page load or
    from a search, so the separators between pills are part of the contract: a
    server that drops the price pill *and* its trailing space while the twin
    drops only the pill leaves a double space the eye can catch.

    Compared against the paragraph the grid actually served for the same price
    rather than against a literal, so this cannot go stale the way a pinned
    string does when a pill is added to the card.
    """
    rows = [{"id": PRICED, "title": "Listing", "category": "Education",
             "price_label": PRICED_LABEL, "safety_score": 44},
            {"id": BLANK, "title": "Listing", "category": "Education",
             "price_label": "", "safety_score": 44}]
    rendered = _render_client_side(price_probe, rows)
    for listing_id, html in zip((PRICED, BLANK), rendered):
        served = _normalized(price_probe["grid_paragraphs"].get(str(listing_id)))
        twin = _normalized(_paragraph(html))
        assert twin is not None, (
            "the client-side card rendered no pill paragraph: %r" % html[:400])
        assert twin == served, (
            "for listing %d the client-side card renders %r and the server "
            "renders %r; one of them is leaving a stray separator or an empty "
            "element behind" % (listing_id, twin, served))
