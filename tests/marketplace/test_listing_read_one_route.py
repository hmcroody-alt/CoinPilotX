"""A deep link carries an id, so one listing has to be readable by id alone.

## What was missing

``/api/pulse/marketplace/search`` was the only buyer-side read of a listing.
``MarketplaceProductScreen`` said so in its own header comment -- "There is no
read-one endpoint" -- and took the listing in its route params instead. A
universal link from the web PDP carries no params, only an id, so the native
product page could open a listing *only* when that id happened to fall inside
the page of at most 40 rows search had just returned. ``bot.py`` names this in
as many words and calls it "a shared product gap, not a web one".

``/api/pulse/marketplace/listings/<id>`` closes it.

## Why this file is mostly about *sameness*

The wrong fix is a second commerce read. Two queries answering "what is listing
X" drift: one grows a field, the other does not, and a member who arrived from
a web link sees a subtly different product from a member who tapped the same
listing in the grid. So the load-bearing assertion here is not that the route
returns 200 -- it is that its payload equals **search's** payload for the same
listing, field for field. If someone re-implements this route against its own
SELECT list, this file fails.

The rest holds the visibility line. The route is anonymous, which is the whole
point (a cold deep link from Safari has no session), so every gate that keeps a
listing off a buyer surface has to be re-proved here rather than inherited from
the login check search had.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

import pytest

from tests.probe_report import parse_report

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: (user_id, account_status, hidden_from_discovery, sellers.status, store name)
SELLERS = (
    (9601, "active", 0, "approved", "Read One Store"),
    (9602, "active", 0, "suspended", "Suspended Store"),
    (9603, "active", 1, "approved", "Hidden Store"),
    (9604, "suspended", 0, "approved", "Closed Account Store"),
)

#: The control. Public by every rule, and the only id that may return 200.
HEALTHY = 79101

#: Seller suspended in `marketplace_sellers` -- `public_sql` excludes it.
SUSPENDED_SELLER = 79102

#: Seller opted out of discovery -- `discovery_visible_sql` excludes it.
HIDDEN_SELLER = 79103

#: Seller's whole account is suspended -- `discovery_visible_sql` excludes it.
CLOSED_ACCOUNT = 79104

#: Healthy seller, but the listing itself was never approved.
PENDING = 79105

#: Healthy seller, approved listing, physical goods, empty shelf.
NO_STOCK = 79106

#: Never existed. Must be indistinguishable from every case above.
ABSENT = 79199

#: (id, seller_user_id, status, approval_status, quantity)
SEED = (
    (HEALTHY, 9601, "published", "approved", 5),
    (SUSPENDED_SELLER, 9602, "published", "approved", 5),
    (HIDDEN_SELLER, 9603, "published", "approved", 5),
    (CLOSED_ACCOUNT, 9604, "published", "approved", 5),
    (PENDING, 9601, "published", "pending_review", 5),
    (NO_STOCK, 9601, "published", "approved", 0),
)

#: Every id the probe asks the route about.
PROBED = tuple(lid for lid, *_ in SEED) + (ABSENT,)

#: The ones that must not be readable, and why each is here.
WITHHELD = (
    (SUSPENDED_SELLER, "its seller is suspended"),
    (HIDDEN_SELLER, "its seller opted out of discovery"),
    (CLOSED_ACCOUNT, "its seller's account is suspended"),
    (PENDING, "the listing was never approved"),
    (NO_STOCK, "the listing has no stock"),
    (ABSENT, "no such listing exists"),
)

_PROBE = r"""
import json, sys, sqlite3
sys.path.insert(0, %(repo)r)
import bot

app = bot.webhook_app
app.config["SECRET_KEY"] = "marketplace-read-one-test"
report = {}

with app.app_context():
    conn = bot.db(); conn.row_factory = sqlite3.Row; cur = conn.cursor()
    for uid, account_status, hidden, seller_status, store_name in %(sellers)r:
        cur.execute("INSERT INTO users (user_id, username, email, display_name, "
                    "hidden_from_discovery, account_status) VALUES (?,?,?,?,?,?)",
                    (uid, "readone%%d" %% uid, "readone%%d@example.com" %% uid,
                     "Owner %%d" %% uid, hidden, account_status))
        cur.execute("INSERT INTO marketplace_sellers (user_id, status, display_name) "
                    "VALUES (?,?,?)", (uid, seller_status, store_name))
    # The buyer. Search is login-gated; the route under test is not, and the
    # comparison below is the reason this account has to exist at all.
    cur.execute("INSERT INTO users (user_id, username, email, display_name, "
                "hidden_from_discovery, account_status) "
                "VALUES (9650,'readonebuyer','buyer@example.com','Buyer',0,'active')")
    for lid, uid, status, approval_status, quantity in %(seed)r:
        cur.execute(
            "INSERT INTO marketplace_listings (id, seller_user_id, title, description, "
            "short_description, category, price_label, currency, status, approval_status, "
            "quantity, product_type, cover_image_url) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,'physical',?)",
            (lid, uid, "Read-one listing %%d" %% lid, "Body of %%d" %% lid,
             "Short %%d" %% lid, "Home", "$41.00", "USD", status, approval_status,
             quantity, "https://example.com/%%d.jpg" %% lid))
    conn.commit()
    cur.execute("SELECT id, status, approval_status, quantity FROM marketplace_listings "
                "WHERE id>=79100 ORDER BY id")
    report["seeded"] = {str(r["id"]): [r["status"], r["approval_status"], r["quantity"]]
                        for r in cur.fetchall()}
    conn.close()

# Anonymous on purpose. A cold universal link from Safari arrives with no
# session, and if this client were logged in the whole point of the route would
# go untested.
anon = app.test_client()
reads = {}
for lid in %(probed)r:
    response = anon.get("/api/pulse/marketplace/listings/%%d" %% lid)
    try:
        body = response.get_json() or {}
    except Exception:
        body = {}
    reads[str(lid)] = {
        "http": response.status_code,
        "ok": body.get("ok"),
        "error_code": body.get("error_code"),
        "error": body.get("error") or body.get("message"),
        "listing": body.get("listing"),
    }
report["reads"] = reads

# The same listing as the grid gets it. Search needs a member session; that is
# the only reason a second client exists.
member = app.test_client()
with member.session_transaction() as session:
    session["account_user_id"] = 9650
search = member.get("/api/pulse/marketplace/search?limit=40")
report["search_http"] = search.status_code
search_body = search.get_json() or {}
report["search_keys"] = sorted(search_body.keys())
rows = search_body.get("listings") or search_body.get("items") or []
report["search_ids"] = [row.get("id") for row in rows if isinstance(row, dict)]
report["search_listing"] = next(
    (row for row in rows if isinstance(row, dict) and row.get("id") == %(healthy)d), None)

sys.stdout.write("<<<REPORT>>>" + json.dumps(report))
"""


@pytest.fixture(scope="module")
def read_one_probe():
    """Boot the app once in a child process; importing ``bot`` binds DATABASE_URL."""
    workdir = tempfile.mkdtemp(prefix="marketplace-read-one-")
    env = dict(os.environ)
    env["DATABASE_URL"] = "sqlite:///" + os.path.join(workdir, "marketplace.db")
    env["COINPILOTX_DB_INIT_STARTUP_MODE"] = "sync"
    env["PYTHONPATH"] = REPO
    code = _PROBE % {
        "repo": REPO,
        "sellers": SELLERS,
        "seed": SEED,
        "probed": PROBED,
        "healthy": HEALTHY,
    }
    proc = subprocess.run([sys.executable, "-c", code], cwd=REPO, env=env,
                          capture_output=True, text=True, timeout=900)
    return parse_report(proc.stdout, proc.stderr)


def test_the_seed_is_what_these_tests_assume(read_one_probe):
    """Guard the fixture. Every assertion below is read against these rows."""
    expected = {str(lid): [status, approval_status, quantity]
                for lid, _uid, status, approval_status, quantity in SEED}
    assert read_one_probe["seeded"] == expected, (
        "the probe did not create the shapes these assertions name: %r"
        % read_one_probe["seeded"])


def test_a_public_listing_opens_from_its_id_with_no_session(read_one_probe):
    """The whole mission in one assertion.

    A universal link tapped in Safari hands the app an id and nothing else, and
    a cold start has no session yet. If this needed a login the deep link would
    resolve to an auth wall for every logged-out arrival -- which is the "lands
    somewhere that is not the product" failure wearing a different hat.
    """
    read = read_one_probe["reads"][str(HEALTHY)]
    assert read["http"] == 200, (
        "an anonymous read of a public listing returned HTTP %r (%r). The web "
        "PDP already serves these same fields to anyone, crawlers included."
        % (read["http"], read["error"]))
    assert read["ok"] is True
    assert isinstance(read["listing"], dict), (
        "HTTP 200 with no `listing` key: %r" % read["listing"])
    assert read["listing"].get("id") == HEALTHY, (
        "asked for listing %d and got %r -- the one thing a deep link may never "
        "do is open a different product" % (HEALTHY, read["listing"].get("id")))


def test_the_read_one_payload_is_the_same_product_search_returns(read_one_probe):
    """The load-bearing test. Identity, not a parallel commerce read.

    Two endpoints answering "what is listing X" is how a member who arrived
    from a web link ends up looking at a subtly different product from the
    member who tapped the same tile in the grid -- a different price format, a
    missing badge, no media. The route is built on search's own SELECT list and
    the shared serializer precisely so that cannot happen, and this is the
    assertion that makes re-implementing it fail.
    """
    assert read_one_probe["search_http"] == 200, (
        "search itself did not answer (HTTP %r), so this comparison proves "
        "nothing" % read_one_probe["search_http"])
    from_search = read_one_probe["search_listing"]
    assert isinstance(from_search, dict), (
        "listing %d was not in search's own results (%r), so the two payloads "
        "cannot be compared -- fix the seed before reading anything else into "
        "this failure" % (HEALTHY, read_one_probe["search_ids"]))
    from_read_one = read_one_probe["reads"][str(HEALTHY)]["listing"]

    assert sorted(from_read_one.keys()) == sorted(from_search.keys()), (
        "the two reads of one listing do not even carry the same fields.\n"
        "  only in read-one: %r\n  only in search:   %r"
        % (sorted(set(from_read_one) - set(from_search)),
           sorted(set(from_search) - set(from_read_one))))

    differing = {key: (from_read_one.get(key), from_search.get(key))
                 for key in from_search
                 if from_read_one.get(key) != from_search.get(key)}
    assert differing == {}, (
        "same listing, two answers: %r. A deep link must carry identity, not a "
        "duplicated copy of commerce truth." % differing)


@pytest.mark.parametrize("listing_id,reason", WITHHELD, ids=[r for _, r in WITHHELD])
def test_a_listing_no_buyer_may_see_is_not_readable_by_id(read_one_probe, listing_id, reason):
    """Anonymous does not mean ungated.

    Search inherited a login check in front of its predicates. This route has
    none, so every rule that keeps a listing off a buyer surface has to hold on
    its own here -- and guessing an id must not be a way around any of them.
    """
    read = read_one_probe["reads"][str(listing_id)]
    assert read["http"] == 404, (
        "listing %d is readable (HTTP %r) although %s" % (listing_id, read["http"], reason))
    assert read["listing"] is None, (
        "a 404 still shipped listing data for %d: %r" % (listing_id, read["listing"]))
    # Upper-case because that spelling is the live contract, not a style
    # choice: `getListing` in `mobile-native/src/api/marketplace.ts` compares
    # against `LISTING_UNAVAILABLE`, and `tests/test_marketplace_listing_detail`
    # pins it on the same route.
    assert read["error_code"] == "LISTING_UNAVAILABLE", (
        "the client branches on `error_code` to decide between a permanent "
        "'Product unavailable' and a retry; listing %d answered %r"
        % (listing_id, read["error_code"]))


def test_a_withheld_listing_is_indistinguishable_from_one_that_never_existed(read_one_probe):
    """Otherwise the route is an existence oracle for unapproved listings.

    Saying "this was removed" about a guessed id confirms the row is there, and
    the ids are sequential. Every withheld case has to answer exactly what
    ``ABSENT`` answers.
    """
    absent = read_one_probe["reads"][str(ABSENT)]
    for listing_id, reason in WITHHELD:
        if listing_id == ABSENT:
            continue
        read = read_one_probe["reads"][str(listing_id)]
        assert (read["http"], read["error_code"], read["error"]) == (
            absent["http"], absent["error_code"], absent["error"]), (
            "listing %d (%s) answers %r where a nonexistent id answers %r, so "
            "the difference between the two tells a stranger the row exists"
            % (listing_id, reason, (read["http"], read["error_code"], read["error"]),
               (absent["http"], absent["error_code"], absent["error"])))


def test_the_unavailable_message_is_one_a_member_can_read(read_one_probe):
    """It is rendered verbatim on the product page, so it is not a code."""
    read = read_one_probe["reads"][str(ABSENT)]
    assert read["error"], "a 404 with no message leaves the app nothing to show"
    assert "listing" in read["error"].lower(), (
        "the unavailable message %r does not mention the thing that is "
        "unavailable" % read["error"])
