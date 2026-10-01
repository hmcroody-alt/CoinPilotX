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

A rule existing proves nothing here: `require_account()` runs before the lookup,
so an anonymous client sees `302 /login` for a real listing, a hidden one and a
nonexistent one alike. Every assertion below is therefore made against a real
response from a signed-in client, which is the only vantage point that can tell
the three outcomes apart.
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
        # The rendered buttons, not the attribute names. The page ships a click
        # handler that selects on `[data-contact-seller]`, so searching for the
        # bare attribute matches the script and passes even with no buttons at
        # all -- which is exactly how this check was vacuous when first written.
        "actions": all("<button %%s=" %% m in body for m in
                       ("data-contact-seller", "data-save-listing", "data-report-listing")),
    }
report["pages"] = pages

grid = client.get("/pulse/marketplace").get_data(as_text=True)
report["grid"] = {
    "status": 200,
    "shows": {str(l): ("Listing %%d" %% l) in grid for l in %(seeded_ids)r},
    # Every listing id the served grid links to, server-rendered.
    "links": sorted({int(m) for m in re.findall(r"/pulse/marketplace/(\d+)", grid)}),
    # The client-rendered card builds the same link from its own row.
    "js_link": "/pulse/marketplace/${listingId}" in grid,
}

# The signed-out view of all three outcomes, to show they are indistinguishable
# without a session -- which is why nothing above is asserted anonymously.
anon = app.test_client()
report["anonymous"] = {
    str(l): [anon.get("/pulse/marketplace/%%d" %% l).status_code,
             anon.get("/pulse/marketplace/%%d" %% l).headers.get("Location", "")]
    for l in (%(public)d, %(hidden)d, %(missing)d)
}

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
        "public": PUBLIC, "hidden": HIDDEN_SELLER, "missing": MISSING,
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
    """
    grid = marketplace_probe["grid"]
    assert grid["links"], "the grid links to no listings at all"
    for listing_id in grid["links"]:
        page = marketplace_probe["pages"].get("/pulse/marketplace/%d" % listing_id)
        assert page is not None and page["status"] == 200, (
            "the grid links to /pulse/marketplace/%d, which does not serve"
            % listing_id)


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


def test_the_client_rendered_card_links_to_the_same_place(marketplace_probe):
    """Search results replace the grid in the DOM, and must keep the link.

    Without this, a member who searched would lose the ability to open a
    product that browsing offered — the same page, two behaviours.
    """
    assert marketplace_probe["grid"]["js_link"], (
        "the client-side marketplace card builds no /pulse/marketplace/<id> link, "
        "so search results are not openable")


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


def test_signed_out_visitors_cannot_tell_the_outcomes_apart(marketplace_probe):
    """The login redirect happens before the lookup, and must keep doing so.

    If the row were read first, a 404 for the missing id — or a different
    destination for the hidden one — would tell an anonymous visitor which ids
    exist, turning the login wall into an enumeration oracle.

    The redirect target necessarily echoes the requested path, so the assertion
    is that it echoes *only* that: each answer must be exactly the same function
    of the URL, carrying nothing drawn from the row behind it.
    """
    answers = marketplace_probe["anonymous"]
    assert {status for status, _ in answers.values()} == {302}, (
        "a signed-out visitor gets more than one status across a real, a hidden "
        "and a nonexistent listing: %s" % answers)
    for listing_id, (_, location) in answers.items():
        expected = "/login?next=/pulse/marketplace/%s" % listing_id
        assert location == expected, (
            "the signed-out redirect for %s carries something other than the "
            "requested path (%r), so it can reveal whether the listing exists"
            % (listing_id, location))
