"""`/pulse/marketplace/:listingId` — the shared product link, and who may see it.

## What was broken

`linking.ts` publishes `https://pulsesoc.com/pulse/marketplace/:listingId` as a
universal link, so it is a URL the app puts on clipboards and into
notifications. The web had no rule for it at all: every shared product link
404'd, for the recipient and for the sender's own browser.

## Why this route reads the database instead of calling an API

Because there is nothing to call. `MarketplaceProductScreen` says so outright:
"There is no read-one endpoint -- /api/pulse/marketplace/search is the only
buyer-side read." Native therefore carries the listing in its route params, and
a deep link has no params, only an id — so on the phone a shared link can open
the product page *only* when that listing happens to be inside the 40 rows
search last returned. Anything older strands the member on the grid. That is a
shared product gap, and it is worth being explicit that these tests do not
claim it is fixed on native.

The web does not have to inherit it, and closing it needs no new backend
authority: this *is* the server. The page reads one row under the two
predicates that already decide what is public — `public_sql` for the listing
lifecycle and `discovery_visible_sql` for the seller — and renders it with
`pulse_marketplace_listing_payload`, the same shaper the search API returns.
Nothing here decides visibility; it asks the modules that already do. The tests
below pin exactly that: each predicate is proven by a seeded row that only it
excludes, so deleting either one from the query fails a test rather than
quietly widening who can read a listing.

## The grid was the looser surface, and now is not

Tightening the *grid* is part of this change rather than a drive-by. The grid
applied only `public_sql`, while the search endpoint the app actually reads
applies both. So the same catalogue answered differently depending on whether
you scrolled it or searched it, and the looser answer — the one that showed QA
and deactivated sellers' listings — was the web's. It also made the new links
self-defeating: a card linking to a listing the detail page refuses is a 404
the page rendered itself. `test_the_grid_only_links_to_listings_it_can_serve`
is the standing guard on that, and it is deliberately written as a property
over whatever the grid emitted, not as a fixed id list.

## Why responses, not routes

A rule existing proves nothing here, so every assertion below is made against a
real response.

Most of them use a signed-in client. That used to be the only vantage point that
could tell the outcomes apart, because `require_account()` ran before the lookup
and an anonymous request got `302 /login` for a real listing, a hidden one and a
nonexistent one alike. It no longer does: the page is public, because it is a
canonical URL submitted in `/sitemap-products.xml` and a crawler never has a
session. The signed-in client is kept anyway — it is the vantage point the
predicates are easiest to read from, since a refusal and a login wall are both
"not the listing" when seen anonymously.

The two anonymous assertions are at the bottom of the file and divide what that
change gave up from what it kept: a public listing is readable without a session
(`test_a_public_listing_is_readable_without_a_session`), and every *refusal*
still looks identical to one another (`test_every_refusal_looks_the_same_to_a_
signed_out_visitor`), so the page cannot be used to enumerate withheld listings.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile

import pytest

from tests.probe_report import parse_report

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: The seeded listings, and what each one exists to prove.
#:
#: Every row is public except for one attribute, so a row's outcome names the
#: rule that produced it. Remove a predicate from the query and exactly one of
#: these flips — which is what makes these ids a test rather than a fixture.
PUBLIC = 77001          #: fully public: approved, active, discoverable seller.
PAUSED = 77002          #: `status='paused'` — excluded by `public_sql`.
UNAPPROVED = 77003      #: `approval_status='pending'` — excluded by `public_sql`.
HIDDEN_SELLER = 77004   #: seller `hidden_from_discovery=1` — `discovery_visible_sql`.
MISSING = 99999         #: never existed.

#: Refused ids, and the human name for why. Kept as a mapping so a failure says
#: which rule stopped working rather than only which number changed.
REFUSED = {
    PAUSED: "a paused listing (public_sql: status)",
    UNAPPROVED: "an unapproved listing (public_sql: approval_status)",
    HIDDEN_SELLER: "a hidden seller's listing (discovery_visible_sql)",
    MISSING: "a listing id that does not exist",
}

#: `/pulse/marketplace/create` is a sibling of the new `<int:listing_id>` rule
#: and must keep serving its own page.
#:
#: Worth being precise about what this catches, because the obvious worry is not
#: it: loosening the rule to `<listing_id>` does *not* break this, since Werkzeug
#: weights static segments above converters and the static rule still wins. That
#: loosening is caught by `test_the_route_is_registered_for_an_integer_id`
#: instead. What this catches is the create page being removed, renamed, or
#: reordered under the id route.
STATIC_SIBLING = "/pulse/marketplace/create"

_PROBE = r"""
import json, re, sys, sqlite3
sys.path.insert(0, %(repo)r)
import bot

app = bot.webhook_app
app.config["SECRET_KEY"] = "marketplace-listing-links-test"
report = {}

# One viewer, plus two sellers who differ only in discoverability, so the
# seller-side predicate is exercised by a row rather than by reading the SQL.
with app.app_context():
    conn = bot.db(); conn.row_factory = sqlite3.Row; cur = conn.cursor()
    cur.execute("INSERT INTO users (username, email, display_name) "
                "VALUES ('mkviewer','mkviewer@example.com','Viewer')")
    viewer_id = cur.lastrowid
    for uid, uname, hidden in ((9001, 'seller9001', 0), (9002, 'seller9002', 1)):
        cur.execute(
            "INSERT INTO users (user_id, username, email, display_name, "
            "hidden_from_discovery, account_status) VALUES (?,?,?,?,?,'active')",
            (uid, uname, uname + "@example.com", uname.title(), hidden))
        cur.execute("INSERT INTO marketplace_sellers (user_id, status, display_name) "
                    "VALUES (?,'approved',?)", (uid, uname.title() + " Store"))
    for lid, seller, appr, st in %(rows)r:
        cur.execute(
            "INSERT INTO marketplace_listings (id, seller_user_id, title, description, "
            "category, price_label, currency, approval_status, status, quantity) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (lid, seller, "Listing %%d" %% lid, "Body of listing %%d" %% lid,
             "Education", "$10.00", "USD", appr, st, 5))
    conn.commit()
    # Proof the seed is what the test believes it is. A silently failed insert
    # would otherwise read as "the predicate refused it".
    cur.execute("SELECT id, seller_user_id, status, approval_status "
                "FROM marketplace_listings WHERE id>=77000 ORDER BY id")
    report["seeded"] = [dict(r) for r in cur.fetchall()]
    conn.close()

client = app.test_client()
with client.session_transaction() as session:
    session["account_user_id"] = viewer_id

pages = {}
for path in %(paths)r:
    response = client.get(path)
    body = response.get_data(as_text=True)
    pages[path] = {
        "status": response.status_code,
        "location": response.headers.get("Location", ""),
        "title": ("Listing %(public)d" in body),
        "body": ("Body of listing %(public)d" in body),
        "seller": ("Seller9001 Store" in body),
        # The rendered controls, not the attribute names. A click handler
        # selects on `[data-mkt-save]`, so searching for the bare attribute
        # matches the script and passes even with no controls at all -- which is
        # exactly how this check was vacuous when first written. The pattern
        # therefore demands the attribute sit inside an element's opening tag.
        #
        # Two of the three are buttons and the first is an anchor, deliberately.
        # `/pulse/messages/new?q=` is a real page, so Message seller still works
        # with JavaScript off; Save and Report are fetch-only and ship `hidden`
        # until the script binds them. Do not "fix" contact back to a button.
        "actions": all(
            re.search(r"<(?:a|button)\b[^>]*\bdata-mkt-%%s=" %% m, body)
            for m in ("contact", "save", "report")),
    }
report["pages"] = pages

grid = client.get("/pulse/marketplace").get_data(as_text=True)
report["grid"] = {
    "status": 200,
    "shows": {str(l): ("Listing %%d" %% l) in grid for l in %(seeded_ids)r},
    # Every listing id the served grid links to, server-rendered. The href shape
    # is whatever `app_links` decides for the `product` destination -- see
    # tests/test_marketplace_web_ctas_follow_the_registry.py -- so it is asked for
    # here rather than pattern-matched. It used to be the app-first interstitial
    # and is now the public product page; either way it names a listing id, and
    # that id is what this file is about.
    "links": sorted(l for l in %(seeded_ids)r
                    if ("href='%%s'" %% bot.app_first_href("product", l)) in grid
                    or ('href="%%s"' %% bot.app_first_href("product", l)) in grid),
}

# Searching used to replace the grid in the DOM with cards a client-side twin
# built, so the twin had to be handed the server's link shape and this file
# checked that shape was present. Search is now rendered by the server from
# `?q=`, through the very same card function the grid uses, so the question "do
# search results keep the link" is asked of the search results themselves
# instead of a template string left in the page for a script to fill in.
search = client.get(
    "/pulse/marketplace?q=Listing+%%d" %% %(public)d).get_data(as_text=True)
report["search"] = {
    "shows": ("Listing %%d" %% %(public)d) in search,
    "links": sorted(l for l in %(seeded_ids)r
                    if ("href='%%s'" %% bot.app_first_href("product", l)) in search
                    or ('href="%%s"' %% bot.app_first_href("product", l)) in search),
}

# The signed-out view of every outcome. A public listing is now readable without
# a session -- that is the point of the public product page -- so what is recorded
# is enough to compare the *refusals* to each other, byte for byte, rather than
# only their status codes. Length and title stand in for the body: a refusal that
# leaked the title, or that rendered a different-sized page for a row that exists,
# would still be a 404.
anon = app.test_client()
_anon = {}
for l in %(all_ids)r:
    r = anon.get("/pulse/marketplace/%%d" %% l)
    b = r.get_data(as_text=True)
    _anon[str(l)] = [r.status_code, r.headers.get("Location", ""), len(b),
                     ("Listing %%d" %% l) in b]
report["anonymous"] = _anon

report["rules"] = sorted({r.rule for r in app.url_map.iter_rules()
                          if r.rule.startswith("/pulse/marketplace")})

sys.stdout.write("<<<REPORT>>>" + json.dumps(report))
"""

_ROWS = (
    (PUBLIC, 9001, "approved", "active"),
    (PAUSED, 9001, "approved", "paused"),
    (UNAPPROVED, 9001, "pending", "active"),
    (HIDDEN_SELLER, 9002, "approved", "active"),
)


@pytest.fixture(scope="module")
def marketplace_probe():
    """Boot the app once, in a child process, and report what it serves.

    Booting in a subprocess for the reason the rest of this directory does:
    importing ``bot`` binds ``DATABASE_URL`` for the whole process and cannot be
    undone, so an in-process import would steal the database from every other
    suite sharing the run.
    """
    workdir = tempfile.mkdtemp(prefix="marketplace-links-")
    env = dict(os.environ)
    env["DATABASE_URL"] = "sqlite:///" + os.path.join(workdir, "marketplace.db")
    # Sync, so a schema failure surfaces as a failed probe rather than on a
    # daemon thread whose traceback only reaches the log.
    env["COINPILOTX_DB_INIT_STARTUP_MODE"] = "sync"
    env["PYTHONPATH"] = REPO
    paths = ["/pulse/marketplace/%d" % lid
             for lid in (PUBLIC, PAUSED, UNAPPROVED, HIDDEN_SELLER, MISSING)]
    paths.append(STATIC_SIBLING)
    code = _PROBE % {
        "repo": REPO, "rows": _ROWS, "paths": paths,
        "seeded_ids": [PUBLIC, PAUSED, UNAPPROVED, HIDDEN_SELLER],
        "all_ids": [PUBLIC, PAUSED, UNAPPROVED, HIDDEN_SELLER, MISSING],
        "public": PUBLIC,
    }
    proc = subprocess.run([sys.executable, "-c", code], cwd=REPO, env=env,
                          capture_output=True, text=True, timeout=900)
    return parse_report(proc.stdout, proc.stderr)


def _page(probe, listing_id):
    return probe["pages"]["/pulse/marketplace/%d" % listing_id]


def test_the_seed_is_what_these_tests_assume(marketplace_probe):
    """Guard the fixture itself, so a refusal is never really a failed insert."""
    seeded = {row["id"]: row for row in marketplace_probe["seeded"]}
    assert set(seeded) == {PUBLIC, PAUSED, UNAPPROVED, HIDDEN_SELLER}, (
        "the probe did not create the listings the assertions below name; every "
        "404 they check for would pass for the wrong reason")
    assert seeded[PAUSED]["status"] == "paused"
    assert seeded[UNAPPROVED]["approval_status"] == "pending"
    assert seeded[HIDDEN_SELLER]["seller_user_id"] == 9002, (
        "the hidden-seller case must belong to the hidden seller, or "
        "discovery_visible_sql is not being exercised at all")


def test_a_shared_listing_link_opens_the_product(marketplace_probe):
    """The whole point: the URL the app shares renders that listing."""
    page = _page(marketplace_probe, PUBLIC)
    assert page["status"] == 200, (
        "the universal link /pulse/marketplace/%d did not resolve (%s)"
        % (PUBLIC, page["status"]))
    assert page["title"], "the page rendered without the listing's title"
    assert page["body"], "the page rendered without the listing's description"
    assert page["seller"], "the page rendered without the seller's store name"


def test_the_shared_page_offers_the_same_actions_as_a_grid_card(marketplace_probe):
    """Arriving by link must not be a lesser page than arriving by browsing.

    A detail page that rendered the product but dropped Contact/Save/Report
    would still pass every routing check while being a dead end.
    """
    assert _page(marketplace_probe, PUBLIC)["actions"], (
        "the listing page is missing one of contact seller, save, or report")


@pytest.mark.parametrize("listing_id", sorted(REFUSED))
def test_a_listing_the_catalogue_hides_is_not_readable_by_id(marketplace_probe,
                                                             listing_id):
    """Each refusal is produced by exactly one rule, named in ``REFUSED``.

    All four answer 404 rather than 403 or a "removed" notice: distinguishing
    them would confirm the existence of a row to someone guessing ids.
    """
    page = _page(marketplace_probe, listing_id)
    assert page["status"] == 404, (
        "%s was served by id (%s); a guessed URL reached content the catalogue "
        "does not show" % (REFUSED[listing_id], page["status"]))
    assert not page["title"], "the refused page still leaked a listing title"


def test_the_grid_only_links_to_listings_it_can_serve(marketplace_probe):
    """A card linking to its own 404 is a broken link the page produced.

    Written over whatever the grid emitted rather than a fixed list, so a
    future card that links somewhere new is covered without editing this test.

    The card now points at `/open/product/<id>`, the app-first interstitial, so
    the id is read from there. The listing it names still has to be one this
    server will serve: that is what the interstitial's App Store fallback and
    its native destination both resolve to, and it is what a desktop visitor
    reaches. Moving the button did not make a dead id acceptable.
    """
    grid = marketplace_probe["grid"]
    assert grid["links"], "the grid links to no listings at all"
    for listing_id in grid["links"]:
        page = marketplace_probe["pages"].get("/pulse/marketplace/%d" % listing_id)
        assert page is not None and page["status"] == 200, (
            "the grid links to listing %d, which does not serve" % listing_id)


def test_the_grid_and_the_listing_page_agree_on_who_is_public(marketplace_probe):
    """The two surfaces must apply the same predicates, in both directions.

    The grid used to apply only ``public_sql``, so a hidden seller's listing
    appeared in it. Asserting equality rather than one-way containment means
    loosening *either* surface fails here.
    """
    grid_shows = {int(k) for k, v in marketplace_probe["grid"]["shows"].items() if v}
    page_serves = {lid for lid in (PUBLIC, PAUSED, UNAPPROVED, HIDDEN_SELLER)
                   if _page(marketplace_probe, lid)["status"] == 200}
    assert grid_shows == page_serves, (
        "the grid and the listing page disagree about which listings are "
        "public: grid=%s page=%s" % (sorted(grid_shows), sorted(page_serves)))
    assert grid_shows == {PUBLIC}, (
        "expected only the fully public listing to be visible; got %s"
        % sorted(grid_shows))


def test_a_search_result_links_to_the_same_place_as_the_grid(marketplace_probe):
    """Search results must be openable, exactly as browsing results are.

    Without this, a member who searched would lose the ability to open a product
    that browsing offered — the same page, two behaviours.

    This used to assert that the page carried the link *template* an inline
    JavaScript twin of the card substituted an id into, because searching
    replaced the grid in the DOM with cards that twin built. Search is now
    rendered by the server from ``?q=``, through the same card function as the
    grid, so the twin is gone and the template with it.

    The property did not go away and is now checked one step closer to the user:
    against the links in a real search response, rather than against a string a
    script was trusted to use correctly. That is stricter than before -- a
    template being present never proved the twin rendered it -- and the third
    assertion is the one the twin existed to make true, now free.
    """
    search = marketplace_probe["search"]
    # Guarded first, because an empty result set would make everything below
    # vacuous: a search that silently stopped matching fails here rather than
    # passing on zero cards.
    assert search["shows"], (
        "searching for the public listing's own title returned a page that does "
        "not contain it, so this test is not looking at a search result")
    assert search["links"] == [PUBLIC], (
        "a search result does not link to the listing it shows: expected [%d], "
        "got %s" % (PUBLIC, search["links"]))
    assert search["links"] == marketplace_probe["grid"]["links"], (
        "browsing and searching disagree about where a product opens: grid=%s "
        "search=%s" % (marketplace_probe["grid"]["links"], search["links"]))


def test_the_create_page_still_wins_over_the_id_route(marketplace_probe):
    """`/pulse/marketplace/create` must not be swallowed by the new converter."""
    page = marketplace_probe["pages"][STATIC_SIBLING]
    assert page["status"] == 200, (
        "%s stopped serving after the listing route was added" % STATIC_SIBLING)
    assert not page["title"], (
        "%s rendered a listing, so the id route is capturing it" % STATIC_SIBLING)


def test_the_route_is_registered_for_an_integer_id(marketplace_probe):
    """A `<path:>` or bare `<listing_id>` here would shadow every sibling."""
    assert "/pulse/marketplace/<int:listing_id>" in marketplace_probe["rules"], (
        "the listing rule is missing or no longer int-converted; rules seen: %s"
        % marketplace_probe["rules"])


def test_a_public_listing_is_readable_without_a_session(marketplace_probe):
    """The deliberate withdrawal, asserted so it reads as a decision.

    This file used to assert that an anonymous request for *any* listing id got
    the same `302 /login`, because `require_account()` ran before the lookup. That
    is no longer true and must not be: `/pulse/marketplace/<id>` is submitted in
    `/sitemap-products.xml` and carries a canonical URL, so a crawler — which
    never has a session — has to get the page. A login wall there would be a
    sitemap full of URLs that redirect.

    What was given up is narrow, and it is the part that was never worth keeping:
    a public listing answering 200 tells an anonymous visitor that a public
    listing exists. It is in the sitemap; that is the same statement.
    """
    status, location, _, title = marketplace_probe["anonymous"][str(PUBLIC)]
    assert status == 200, (
        "a signed-out visitor cannot read the public listing (%s -> %r), so every "
        "URL in /sitemap-products.xml is a redirect to Google"
        % (status, location))
    assert title, (
        "the anonymous response was a 200 that does not contain the listing "
        "title, so it is some other page wearing a success code")


def test_every_refusal_looks_the_same_to_a_signed_out_visitor(marketplace_probe):
    """The privacy property that survives, and now the only one here.

    Opening the public page gave up "you cannot tell a real id from a fake one".
    It did *not* give up the one that matters: you must not be able to tell a
    listing that exists but is withheld from one that never existed. Otherwise
    the page is an enumeration oracle over unapproved and paused inventory and
    over hidden sellers' catalogues — the three things the two predicates exist
    to withhold.

    Compared byte-count and title rather than status alone, deliberately. Three
    404s whose bodies differ in length leak exactly what the status code hides,
    and a 404 that still renders the title leaks more than the length would.
    """
    answers = marketplace_probe["anonymous"]
    refused = {lid: answers[str(lid)] for lid in REFUSED}
    shapes = {tuple(answer) for answer in refused.values()}
    assert len(shapes) == 1, (
        "a signed-out visitor can tell the refusals apart, so the page reveals "
        "which withheld listings exist: %s"
        % {REFUSED[lid]: answer for lid, answer in refused.items()})
    status, location, _, title = shapes.pop()
    assert status == 404, "refusals answer %s, not 404" % status
    assert not location, "a refusal carries a redirect target: %r" % location
    assert not title, "a refusal renders the listing title"
