"""Approving a listing is not the same act as publishing it, and both sides say so.

## The bug this closes

``tests/marketplace/test_marketplace_moderation_reachability.py`` fixed the half
where Approve *refused* the listings ``drafts.publish`` produces. This file is
about the half that was left: Approve *accepting*, reporting success, and
leaving the listing invisible to every buyer.

``/admin/marketplace-command`` writes two columns, ``status`` and
``approval_status``. Publication needs five conditions -- those two plus an
approved seller, a public store name, and stock. So a moderator could click
Approve on a listing whose seller was suspended, get HTTP 200 and the message
"Listing updated.", have an audit entry written against their name, and leave a
listing no buyer query would ever return. Measured on four shapes, three of them
did exactly that.

And the merchant was told the opposite of the truth. ``seller_label`` read the
same two columns the moderator had just written and answered "Live". Nothing on
either surface named the missing condition, even though
``public_denial_code`` had known it all along -- nobody on the moderation path
asked.

## Why Approve still has to succeed

The obvious fix is to refuse. It is wrong. A moderator's decision is about the
*content* -- is this product allowed on the platform -- and that answer does not
change because the merchant is out of stock this morning. Refusing would also
recreate the original bug in a new place: an out-of-stock listing could never be
approved, so it could never become sellable when stock returned. The decision is
recorded; what changes is that the route stops implying the listing is now
reachable when it is not.

## Why "unknown" is not "blocked"

Half the queries that build a merchant payload never select ``seller_status``.
Reading an absent column as a failed condition would strip "Live" from every
healthy listing on those paths -- a worse and much louder bug than the one being
fixed. So the label is downgraded only by
``marketplace_listing_lifecycle.publication_blocker``, which reports a rule the
row *proves* unmet and skips one it cannot judge. The unit tests at the bottom
of this file are the ones that hold that line.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

import pytest

from services import marketplace_listing_lifecycle as lifecycle
from tests.probe_report import parse_report

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: Sellers. (user_id, marketplace_sellers.status, store display_name)
SELLERS = (
    (9401, "approved", "Healthy Store"),
    (9402, "approved", ""),
    (9403, "suspended", "Suspended Store"),
)

#: The control: nothing wrong with it. Present so a fix that simply stopped
#: saying "Live" for everyone could not pass this file.
HEALTHY = 78211

#: Seller row exists and is approved, but carries neither a display name nor a
#: business name. The merchant never finished the storefront step.
UNNAMED = 78212

#: Seller was suspended after the listing was submitted.
SUSPENDED = 78213

#: Physical goods, quantity 0.
NO_STOCK = 78214

#: Quantity 0 *and* a stockless product type. Must stay public: a digital
#: product has no stock to run out of, and a fix that reduced the stock rule to
#: "quantity > 0" would take every course, service and booking off sale.
DIGITAL = 78215

#: (id, seller_user_id, quantity, product_type)
SEED = (
    (HEALTHY, 9401, 3, "physical"),
    (UNNAMED, 9402, 3, "physical"),
    (SUSPENDED, 9403, 3, "physical"),
    (NO_STOCK, 9401, 0, "physical"),
    (DIGITAL, 9401, 0, "digital"),
)

#: The clause the route uses to introduce a blocker. Asserted as a literal
#: because it is what a human reads off the page.
STILL_BLOCKED = "still not visible to buyers"

_PROBE = r"""
import json, sys, sqlite3
sys.path.insert(0, %(repo)r)
import bot
from services import marketplace_listing_lifecycle as lifecycle

app = bot.webhook_app
app.config["SECRET_KEY"] = "marketplace-approval-visibility-test"
report = {}

with app.app_context():
    conn = bot.db(); conn.row_factory = sqlite3.Row; cur = conn.cursor()
    for uid, seller_status, store_name in %(sellers)r:
        cur.execute("INSERT INTO users (user_id, username, email, display_name, "
                    "hidden_from_discovery, account_status) VALUES (?,?,?,?,0,'active')",
                    (uid, "seller%%d" %% uid, "seller%%d@example.com" %% uid,
                     "Owner %%d" %% uid))
        cur.execute("INSERT INTO marketplace_sellers (user_id, status, display_name) "
                    "VALUES (?,?,?)", (uid, seller_status, store_name))
    cur.execute("INSERT INTO admin_users (email, password_hash, role, status, "
                "full_name, must_change_password) "
                "VALUES ('mod@example.com','x','owner','active','Moderator',0)")
    admin_id = cur.lastrowid
    # Every listing is seeded in the dropship shape: released by its merchant,
    # no moderation decision yet. That is the state Approve must accept.
    for lid, uid, quantity, product_type in %(seed)r:
        cur.execute(
            "INSERT INTO marketplace_listings (id, seller_user_id, title, description, "
            "category, price_label, currency, status, approval_status, quantity, "
            "product_type, cover_image_url) "
            "VALUES (?,?,?,?,?,?,?,'published','pending_review',?,?,?)",
            (lid, uid, "Visibility listing %%d" %% lid, "Body of %%d" %% lid,
             "Home", "$25.00", "USD", quantity, product_type,
             "https://example.com/%%d.jpg" %% lid))
    conn.commit()
    cur.execute("SELECT id, status, approval_status, quantity, product_type "
                "FROM marketplace_listings WHERE id>=78210 ORDER BY id")
    report["seeded"] = {str(r["id"]): [r["status"], r["approval_status"],
                                       r["quantity"], r["product_type"]]
                        for r in cur.fetchall()}
    conn.close()

client = app.test_client()
with client.session_transaction() as session:
    session["admin_user_id"] = admin_id
    session["admin_session_issued_at"] = bot.datetime.now().isoformat()
    session["admin_session_last_seen"] = bot.datetime.now().isoformat()
    session["csrf_token"] = "visibility-probe-token"

decisions = {}
for lid, uid, quantity, product_type in %(seed)r:
    response = client.post("/admin/marketplace-command", data={
        "listing_id": str(lid),
        "action": "approve",
        "csrf_token": "visibility-probe-token",
    })
    body = response.get_data(as_text=True)
    conn = bot.db(); conn.row_factory = sqlite3.Row; cur = conn.cursor()
    cur.execute("SELECT l.*, COALESCE(ms.status,'missing') AS seller_status, "
                + bot.marketplace_seller_identity.store_name_select("ms")
                + " FROM marketplace_listings l "
                  "LEFT JOIN marketplace_sellers ms ON ms.user_id=l.seller_user_id "
                  "WHERE l.id=? LIMIT 1", (lid,))
    row = dict(cur.fetchone() or {})
    cur.execute("SELECT COUNT(*) AS n FROM marketplace_listings l "
                "LEFT JOIN marketplace_sellers ms ON ms.user_id=l.seller_user_id "
                "WHERE l.id=? AND " + lifecycle.public_sql("l", "ms"), (lid,))
    discoverable = int(dict(cur.fetchone() or {}).get("n") or 0)
    conn.close()
    decisions[str(lid)] = {
        "http": response.status_code,
        "status": row.get("status"),
        "approval_status": row.get("approval_status"),
        "reviewed_by": row.get("reviewed_by"),
        "is_public": lifecycle.is_public(row),
        "discoverable": discoverable,
        "blocker": lifecycle.publication_blocker(row),
        "seller_label": lifecycle.seller_label(row),
        # Only the part of the page the moderator reads as the result of their
        # click. Taking the whole body would match the word "stock" anywhere in
        # the queue table below it.
        "message": (body.split("<p>", 1)[1].split("</p>", 1)[0]
                    if "<p>" in body else ""),
    }
report["decisions"] = decisions

sys.stdout.write("<<<REPORT>>>" + json.dumps(report))
"""


@pytest.fixture(scope="module")
def visibility_probe():
    """Boot the app once in a child process; importing ``bot`` binds DATABASE_URL."""
    workdir = tempfile.mkdtemp(prefix="marketplace-approval-visibility-")
    env = dict(os.environ)
    env["DATABASE_URL"] = "sqlite:///" + os.path.join(workdir, "marketplace.db")
    env["COINPILOTX_DB_INIT_STARTUP_MODE"] = "sync"
    env["PYTHONPATH"] = REPO
    code = _PROBE % {"repo": REPO, "sellers": SELLERS, "seed": SEED}
    proc = subprocess.run([sys.executable, "-c", code], cwd=REPO, env=env,
                          capture_output=True, text=True, timeout=900)
    return parse_report(proc.stdout, proc.stderr)


def test_the_seed_is_what_these_tests_assume(visibility_probe):
    """Guard the fixture. Every assertion below is read against these shapes."""
    expected = {str(lid): ["published", "pending_review", quantity, product_type]
                for lid, _uid, quantity, product_type in SEED}
    assert visibility_probe["seeded"] == expected, (
        "the probe did not create the five states these assertions name: %r"
        % visibility_probe["seeded"])


@pytest.mark.parametrize("listing_id", [HEALTHY, UNNAMED, SUSPENDED, NO_STOCK, DIGITAL])
def test_every_shape_is_still_approvable(visibility_probe, listing_id):
    """A moderator judges content, so none of these may be refused.

    Refusing an out-of-stock or nameless-seller listing would put the decision
    out of reach exactly the way the 409 did: the merchant cannot submit their
    way back to a reviewable state, so the listing would be stuck forever, and
    restocking would not help because the approval it needs could never be
    recorded.
    """
    decision = visibility_probe["decisions"][str(listing_id)]
    assert decision["http"] != 409, (
        "listing %d was refused (HTTP %r). Approve is a decision about content; "
        "a seller problem or an empty shelf is not grounds to make the decision "
        "unreachable." % (listing_id, decision["http"]))
    assert (decision["status"], decision["approval_status"]) == ("published", "approved")
    assert decision["reviewed_by"], "no reviewer recorded against listing %d" % listing_id


def test_the_healthy_listing_actually_goes_public(visibility_probe):
    """The control. Without it every assertion below is satisfied by "block all"."""
    decision = visibility_probe["decisions"][str(HEALTHY)]
    assert decision["blocker"] == "", (
        "a listing with an approved, named seller and stock reports blocker %r"
        % decision["blocker"])
    assert decision["is_public"] is True
    assert decision["discoverable"] == 1, (
        "the healthy listing is not returned by `public_sql`, so buyers cannot "
        "find it however good the moderation message is")
    assert decision["seller_label"] == "Live"
    assert STILL_BLOCKED not in decision["message"], (
        "the moderator was warned about a listing that is fine: %r"
        % decision["message"])
    assert "visible to buyers" in decision["message"], (
        "a successful publication said %r, which does not tell the moderator "
        "the listing is now reachable" % decision["message"])


@pytest.mark.parametrize("listing_id,blocker,label", [
    (UNNAMED, "seller_named", "Store name needed"),
    (SUSPENDED, "seller_approved", "Store offline"),
    (NO_STOCK, "in_stock", "Out of stock"),
])
def test_an_approved_but_invisible_listing_is_reported_as_such(
        visibility_probe, listing_id, blocker, label):
    """The fix. Each of these returned 200 and "Listing updated." before.

    Three surfaces have to agree: the moderator is told the decision did not
    make the listing reachable and why, the merchant's own chip stops claiming
    "Live", and buyer discovery still excludes it. The third is not a
    consequence of the first two -- it is the fact the first two are describing,
    so it is asserted separately.
    """
    decision = visibility_probe["decisions"][str(listing_id)]

    assert decision["discoverable"] == 0, (
        "listing %d is discoverable, so this case no longer tests anything"
        % listing_id)
    assert decision["is_public"] is False

    assert decision["blocker"] == blocker, (
        "listing %d reports blocker %r, expected %r" % (listing_id, decision["blocker"], blocker))

    assert decision["seller_label"] == label, (
        "the merchant's dashboard reads %r for a listing no buyer can reach. "
        "Reading 'Live' here is what left a merchant with no explanation for "
        "zero orders." % decision["seller_label"])

    assert STILL_BLOCKED in decision["message"], (
        "the moderation page said %r. The decision was written and the listing "
        "is invisible; saying only that the listing was updated is how three of "
        "four approvals silently produced nothing." % decision["message"])

    note = lifecycle.RULES_BY_KEY[blocker].moderator_note
    assert note in decision["message"], (
        "the page warned that something is wrong but not what: %r. The reason "
        "is already derived -- see PUBLICATION_RULES -- so a generic warning is "
        "a choice, not a limitation." % decision["message"])


def test_a_stockless_product_is_not_held_back_by_quantity(visibility_probe):
    """quantity 0 on a digital product is not a blocker, and must not become one.

    The narrowest possible reading of "out of stock" is ``quantity > 0``, and it
    would take every course, service, event and booking on the platform off sale
    in one commit. ``STOCKLESS_TYPES`` is why, and this is the test that notices.
    """
    decision = visibility_probe["decisions"][str(DIGITAL)]
    assert decision["blocker"] == "", (
        "a digital product with quantity 0 reports blocker %r" % decision["blocker"])
    assert decision["is_public"] is True
    assert decision["discoverable"] == 1
    assert decision["seller_label"] == "Live"
    assert STILL_BLOCKED not in decision["message"]


# --------------------------------------------------------------------------
# The silence discipline. No app boot needed: `marketplace_listing_lifecycle`
# imports nothing from `bot`.
# --------------------------------------------------------------------------

#: A merchant payload as the seller-listings query builds one: no seller columns
#: at all, because that query never joined `marketplace_sellers`.
UNJOINED = {"status": "published", "approval_status": "approved", "quantity": 4,
            "product_type": "physical"}


def test_a_row_without_seller_columns_still_reads_live():
    """The regression that would be worse than the bug.

    Several payload builders fetch from ``marketplace_listings`` alone. If an
    absent ``seller_status`` counted as a failed condition, every listing served
    by those paths would stop saying "Live" -- including listings that are
    perfectly public.
    """
    assert lifecycle.publication_blocker(UNJOINED) == ""
    assert lifecycle.seller_label(UNJOINED) == "Live"


def test_a_row_without_a_quantity_column_is_not_called_out_of_stock():
    row = {k: v for k, v in UNJOINED.items() if k != "quantity"}
    assert lifecycle.publication_blocker(row) == "", (
        "an unprojected quantity column was read as empty shelves")
    assert lifecycle.seller_label(row) == "Live"


def test_a_stockless_type_is_never_blocked_by_stock_even_unprojected():
    """A digital product has no shelf, so an unprojected quantity is not doubt.

    The stock rule abstains when ``quantity`` was not selected, and a gate reads
    abstention as "no". For a stockless type that would be wrong rather than
    cautious: there is no quantity that could have made it available, so the
    rule has a definite answer without the column. This is the one case where
    the stock rule is certain about a row it cannot count, and dropping it would
    make every digital listing served by a query that omits ``quantity``
    silently unpurchasable.
    """
    row = {"status": "published", "approval_status": "approved",
           "seller_status": "approved", "seller_store_name": "Store",
           "product_type": "digital"}
    assert "quantity" not in row
    assert lifecycle.publication_blocker(row) == ""
    assert lifecycle.is_public(row) is True, (
        "a digital listing fetched without a quantity column was gated as out "
        "of stock")
    assert lifecycle.seller_label(row) == "Live"


def test_the_gate_still_refuses_the_same_row():
    """Silence is not a blocker for a *description* and still is for a *gate*.

    The two readings of one table are the point of the design, so they are
    asserted against the same input. ``is_public`` must not have been softened
    into agreeing with the label.
    """
    assert lifecycle.is_public(UNJOINED) is False, (
        "a row that cannot prove its seller is approved was treated as public. "
        "The safe default for a gate is no; only the label may abstain.")
    assert lifecycle.public_denial_code(UNJOINED) == "SELLER_UNAVAILABLE"


def test_an_explicitly_missing_seller_row_is_evidence():
    """``COALESCE(ms.status,'missing')`` is an answer, not an absence.

    A LEFT JOIN that found no seller row projects the literal ``'missing'``.
    That is proof the seller is not approved, and it must downgrade the label --
    otherwise the sentinel the queries deliberately select would be worth
    nothing.
    """
    row = dict(UNJOINED, seller_status="missing")
    assert lifecycle.publication_blocker(row) == "seller_approved"
    assert lifecycle.seller_label(row) == "Store offline"


def test_a_blank_store_name_is_evidence_but_an_absent_column_is_not():
    named = dict(UNJOINED, seller_status="approved", seller_store_name="Real Store")
    blank = dict(UNJOINED, seller_status="approved", seller_store_name="")
    silent = dict(UNJOINED, seller_status="approved")
    assert lifecycle.seller_label(named) == "Live"
    assert lifecycle.seller_label(blank) == "Store name needed", (
        "a projected-but-empty store name is the data defect this rule exists "
        "for; it is not the same as a column that was never selected")
    assert lifecycle.seller_label(silent) == "Live"


def test_every_rule_carries_the_three_strings_its_consumers_read():
    """One table, three projections. A rule added without all three breaks one.

    ``seller_label`` indexes ``RULES_BY_KEY`` directly and would raise; the
    moderator message would silently degrade to a sentence with a hole in it.
    """
    assert lifecycle.PUBLICATION_RULES, "the rule table is empty"
    for rule in lifecycle.PUBLICATION_RULES:
        assert rule.denial_code, "rule %r has no buyer-facing code" % rule.key
        assert rule.seller_label, "rule %r has no merchant label" % rule.key
        assert rule.moderator_note, "rule %r has no moderator note" % rule.key
        assert lifecycle.blocker_note(rule.key) == rule.moderator_note
    keys = [rule.key for rule in lifecycle.PUBLICATION_RULES]
    assert len(set(keys)) == len(keys), "duplicate rule keys: %r" % keys


def test_the_buyer_facing_codes_are_the_ones_already_on_the_wire():
    """Native clients branch on these three strings. Adding a rule must not add a fourth."""
    assert {rule.denial_code for rule in lifecycle.PUBLICATION_RULES} == {
        "SELLER_UNAVAILABLE", "ITEM_UNAVAILABLE", "OUT_OF_STOCK"}


def test_a_listing_the_merchant_has_not_published_is_labelled_by_its_status():
    """The downgrade applies only where "Live" was on offer.

    A draft or a paused listing already has an accurate label, and running it
    through the publication rules would replace "Draft" with "Out of stock" --
    true, useless, and hiding the thing the merchant needs to act on.
    """
    assert lifecycle.seller_label({"status": "draft", "approval_status": "pending_review",
                                   "quantity": 0}) == "Draft"
    assert lifecycle.seller_label({"status": "paused", "approval_status": "approved",
                                   "quantity": 0}) == "Paused"
    assert lifecycle.seller_label({"status": "pending_review",
                                   "approval_status": "pending_review"}) == "In review"
