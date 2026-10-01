"""The moderation number a reviewer works with does not reach a buyer.

## The bug this closes

``marketplace_listings.safety_score`` is named for safety and holds risk. All
three writers store ``revenue_safety_engine.score_text(...)["risk_score"]``
unchanged -- 0 for a clean listing, 100 for "guaranteed profit, risk free,
100x" -- the column defaults to 0, and the admin queue counts
``safety_score >= 30`` as risky. The storage side is consistent.

Three buyer surfaces printed it as ``<span class='pill'>Safety N</span>``: the
server-rendered grid card, its client-side twin used for search results, and the
product page. A fourth path shipped the raw number to the app, because
``pulse_marketplace_listing_payload`` -- the serializer every marketplace read
goes through -- emitted ``safety_score``.

So the pill was exactly inverted. Measured over the real engine before the fix:

===========================================  ====  ==============
listing                                      risk  the pill said
===========================================  ====  ==============
Handmade oak dining table                       0  Safety 0
Guaranteed profit trading bot (blocked)       100  Safety 100
===========================================  ====  ==============

The worst listing the engine can score advertised the best number, and every
honest one read "Safety 0".

## Why the fix is removal and not inversion

A raw moderation integer is not a buyer concept in either direction. A listing
is only on these surfaces because moderation approved it; that approval is the
signal. Inverting the pill would have left a number whose meaning a buyer
cannot check, still derived from a column named for its opposite. So the buyer
surfaces print nothing and the reviewer's number stays with the reviewer.

## Why these assertions are about served bytes

The app already believed it was safe from this. ``src/api/marketplace.ts`` and
``MarketplaceProductScreen.tsx`` both carry comments saying ``safety_score``
must never reach a buyer surface, and a native test asserted the field was
absent -- from a hand-built fixture. The field was on the wire the whole time.
That is the failure this repository keeps repeating: a claim asserted rather
than measured. Every assertion below is therefore made against bytes the app
served.
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

#: A listing the safety engine would flag hard. Seeded with the risk number the
#: engine gives that copy, so what these surfaces are asked to keep quiet about
#: is a real score and not a placeholder.
RISKY = 79001
RISKY_SCORE = 88

#: The control: same shape, clean copy, risk 0. Without it "the number is
#: absent" is satisfied by a page that failed to render any listing at all.
CLEAN = 79002
CLEAN_SCORE = 0

ALL_LISTINGS = (RISKY, CLEAN)

#: The pill markup that used to carry it. Matched as a pill rather than as the
#: bare word, because both surfaces legitimately serve the sentence "Safety
#: notice: educational products only" and a substring check for "Safety" would
#: fail on copy that is fine.
PILL = re.compile(r"class=['\"]pill['\"]>\s*Safety", re.I)

_PROBE = r"""
import json, re, sys, sqlite3
sys.path.insert(0, %(repo)r)
import bot

app = bot.webhook_app
app.config["SECRET_KEY"] = "marketplace-reviewer-signal-test"
report = {}

with app.app_context():
    conn = bot.db(); conn.row_factory = sqlite3.Row; cur = conn.cursor()
    cur.execute("INSERT INTO users (username, email, display_name) "
                "VALUES ('signalbuyer','signalbuyer@example.com','Buyer')")
    viewer_id = cur.lastrowid
    cur.execute("INSERT INTO users (user_id, username, email, display_name, "
                "hidden_from_discovery, account_status) "
                "VALUES (9201,'seller9201','seller9201@example.com','Seller9201',0,'active')")
    cur.execute("INSERT INTO marketplace_sellers (user_id, status, display_name) "
                "VALUES (9201,'approved','Seller9201 Store')")
    for lid, score in %(rows)r:
        cur.execute(
            "INSERT INTO marketplace_listings (id, seller_user_id, title, description, "
            "category, price_label, currency, approval_status, status, quantity, "
            "safety_score) VALUES (?,?,?,?,?,?,?,'approved','active',5,?)",
            (lid, 9201, "Signal listing %%d" %% lid, "Body of listing %%d" %% lid,
             "Education", "$19.99", "USD", score))
    conn.commit()
    # Proof the seed holds the numbers the assertions name. If a default or a
    # trigger rewrote them to 0, "the number is absent" would be trivially true.
    cur.execute("SELECT id, safety_score FROM marketplace_listings "
                "WHERE id>=79000 ORDER BY id")
    report["seeded"] = {str(r["id"]): r["safety_score"] for r in cur.fetchall()}
    conn.close()

client = app.test_client()
with client.session_transaction() as session:
    session["account_user_id"] = viewer_id

grid = client.get("/pulse/marketplace")
report["grid_status"] = grid.status_code
grid_body = grid.get_data(as_text=True)
report["grid_body"] = grid_body

pages = {}
for lid in %(ids)r:
    response = client.get("/pulse/marketplace/%%d" %% lid)
    body = response.get_data(as_text=True)
    pages[str(lid)] = {
        "status": response.status_code,
        "body": body,
        "title": ("Signal listing %%d" %% lid) in body,
    }
report["pages"] = pages

# The buyer search API, which is what the client-side card is fed and what the
# phone reads. Captured as the parsed payload so the assertion is about keys
# rather than about whether a number happens to appear in some other field.
search = client.get("/api/pulse/marketplace/search?q=Signal")
report["search_status"] = search.status_code
report["search_body"] = search.get_data(as_text=True)
try:
    items = json.loads(search.get_data(as_text=True)).get("items") or []
except Exception:
    items = []
report["search_items"] = {
    str(i.get("id")): sorted(i.keys()) for i in items
    if str(i.get("id")) in {str(x) for x in %(ids)r}
}

sys.stdout.write("<<<REPORT>>>" + json.dumps(report))
"""

_ROWS = ((RISKY, RISKY_SCORE), (CLEAN, CLEAN_SCORE))


@pytest.fixture(scope="module")
def signal_probe():
    """Boot the app once, in a child process, and report what it serves.

    In a subprocess for the reason the rest of this directory is: importing
    ``bot`` binds ``DATABASE_URL`` process-wide and cannot be undone.
    """
    workdir = tempfile.mkdtemp(prefix="marketplace-signal-")
    env = dict(os.environ)
    env["DATABASE_URL"] = "sqlite:///" + os.path.join(workdir, "marketplace.db")
    env["COINPILOTX_DB_INIT_STARTUP_MODE"] = "sync"
    env["PYTHONPATH"] = REPO
    code = _PROBE % {"repo": REPO, "rows": _ROWS, "ids": list(ALL_LISTINGS)}
    proc = subprocess.run([sys.executable, "-c", code], cwd=REPO, env=env,
                          capture_output=True, text=True, timeout=900)
    return parse_report(proc.stdout, proc.stderr)


def test_the_seed_is_what_these_tests_assume(signal_probe):
    """Guard the fixture: the risky row must really carry a risky number."""
    seeded = signal_probe["seeded"]
    assert set(seeded) == {str(l) for l in ALL_LISTINGS}, (
        "the probe did not create the listings these assertions name: %r" % seeded)
    assert seeded[str(RISKY)] == RISKY_SCORE, (
        "the risky listing reached the database holding %r rather than %d, so "
        "every 'the number is absent' assertion below is vacuous"
        % (seeded[str(RISKY)], RISKY_SCORE))
    assert seeded[str(CLEAN)] == CLEAN_SCORE


def test_the_engine_still_scores_risk_upward(signal_probe):
    """The fact the whole decision rests on, asserted against the engine itself.

    If ``score_text`` is ever changed to return higher-is-better, the column
    stops holding what this file says it holds and the removal above needs
    revisiting rather than inheriting. Pinned here so that change cannot be
    made quietly.
    """
    sys.path.insert(0, REPO)
    from services import revenue_safety_engine

    clean = revenue_safety_engine.marketplace_listing_review(
        {"title": "Handmade oak dining table",
         "description": "Solid oak, seats six.", "category": "Home"})
    scam = revenue_safety_engine.marketplace_listing_review(
        {"title": "Guaranteed profit trading bot",
         "description": "Risk free, 100x, sure win. Get rich.",
         "category": "trading signals"})
    assert clean["risk_score"] == 0, (
        "an unremarkable listing now scores %r; the column's 0 no longer means "
        "clean" % clean["risk_score"])
    assert scam["risk_score"] > clean["risk_score"], (
        "`score_text` no longer scores risk upward (scam=%r clean=%r). The "
        "buyer pills were removed because this column holds risk under a name "
        "that says safety -- re-check that reasoning before trusting it."
        % (scam["risk_score"], clean["risk_score"]))
    assert scam["status"] == "blocked_review"


def test_the_grid_serves_the_listings_these_assertions_are_about(signal_probe):
    """The control. Absence proves nothing against a page that rendered nothing."""
    assert signal_probe["grid_status"] == 200
    body = signal_probe["grid_body"]
    for lid in ALL_LISTINGS:
        assert "Signal listing %d" % lid in body, (
            "listing %d did not render in the grid, so this suite is not "
            "looking at a buyer surface -- it is looking at an empty page" % lid)


def test_the_grid_card_prints_no_safety_pill(signal_probe):
    """The regression that shipped, on the server-rendered card."""
    body = signal_probe["grid_body"]
    assert not PILL.search(body), (
        "the marketplace grid still serves a Safety pill: %r"
        % body[max(0, PILL.search(body).start() - 120):PILL.search(body).end() + 60])


def test_the_grid_script_prints_no_safety_pill(signal_probe):
    """The client-side twin, which is a separate implementation of the same card.

    These two have drifted before -- the price-label fix had to be applied to
    both -- so the one that is only reachable through search gets its own
    assertion rather than riding on the server's.
    """
    found = re.search(r"function marketplaceListingHtml\(row\)\{[^\n]*",
                      signal_probe["grid_body"])
    assert found, (
        "the client-side marketplace card is no longer on the served page; it "
        "was renamed, moved, or reformatted onto several lines")
    assert not PILL.search(found.group(0)), (
        "the client-side marketplace card still builds a Safety pill, so search "
        "results would print a reviewer's risk number that the grid does not")


@pytest.mark.parametrize("listing_id", ALL_LISTINGS)
def test_the_product_page_prints_no_safety_pill(signal_probe, listing_id):
    """The page a shared link opens must agree with the card that links to it."""
    page = signal_probe["pages"][str(listing_id)]
    assert page["status"] == 200 and page["title"], (
        "listing %d's product page did not serve (%s), so nothing below is "
        "about a product page" % (listing_id, page["status"]))
    assert not PILL.search(page["body"]), (
        "listing %d's product page still serves a Safety pill" % listing_id)


def _without_stylesheets(html):
    """The served document minus its ``<style>`` blocks.

    The page's CSS is full of two-digit numbers -- ``rgba(5,11,20,.88)``,
    ``bottom:calc(... + 88px)`` -- and a risk score is a 0-100 integer, so a
    bare substring search over the whole document collides with chrome that has
    nothing to do with the listing. Stripping the stylesheet is the narrowest
    thing that removes the collisions without narrowing the claim: everything
    that could carry listing data, markup and inline script alike, is still
    searched.
    """
    return re.sub(r"<style\b.*?</style>", "", html, flags=re.S | re.I)


def test_the_risky_number_appears_on_no_buyer_surface(signal_probe):
    """Not just the pill: the number itself, anywhere in the served bytes.

    Broader than the pill assertions on purpose. Removing a pill and leaving the
    figure in a data attribute, a JSON blob or a tooltip would satisfy every
    check above and still hand a buyer the reviewer's working number.

    Only the risky listing is checked this way. ``CLEAN`` scores 0, a digit far
    too common to assert the absence of.
    """
    needle = str(RISKY_SCORE)
    haystacks = {
        "grid": _without_stylesheets(signal_probe["grid_body"]),
        "product page": _without_stylesheets(
            signal_probe["pages"][str(RISKY)]["body"]),
        "search API": signal_probe["search_body"],
    }
    leaked = {where: body.count(needle)
              for where, body in haystacks.items() if needle in body}
    assert leaked == {}, (
        "the risk score %s still appears in what the buyer is served: %r"
        % (needle, leaked))


def test_the_search_payload_carries_no_reviewer_fields(signal_probe):
    """The wire the app reads, asserted on the real response rather than a stub.

    ``pulse_marketplace_listing_payload`` is the serializer every marketplace
    read goes through, so a field added there reaches four endpoints at once --
    list and detail, buyer and seller. The native suite's version of this claim
    was made against a hand-built fixture and stayed green while the field was
    on the wire.
    """
    assert signal_probe["search_status"] == 200, (
        "the buyer search API answered %s" % signal_probe["search_status"])
    items = signal_probe["search_items"]
    assert set(items) == {str(l) for l in ALL_LISTINGS}, (
        "the search API did not return the seeded listings (%r), so this "
        "assertion is not about a buyer payload" % sorted(items))
    forbidden = {"safety_score", "safety_flags_json", "safety_flags",
                 "moderation_reason", "moderation_category", "review_version"}
    for listing_id, keys in sorted(items.items()):
        leaked = forbidden.intersection(keys)
        assert leaked == set(), (
            "the buyer search payload for listing %s carries reviewer-only "
            "fields %r" % (listing_id, sorted(leaked)))


def test_the_search_payload_still_carries_what_a_buyer_needs(signal_probe):
    """Without this, deleting the payload wholesale passes the test above."""
    items = signal_probe["search_items"]
    for listing_id, keys in sorted(items.items()):
        for required in ("id", "title", "price_label", "category"):
            assert required in keys, (
                "the buyer search payload for listing %s no longer carries %r; "
                "the reviewer-signal fix has taken something with it"
                % (listing_id, required))
