"""The bulk reprice, end to end — §11, §21, §23, §25, §27, §34.

`tests/business_os/test_listing_batch.py` proves what a pricing rule decides, and
`test_seller_listing_batch_route.py` proves that publish and hide actually move
rows. Neither can catch what this file is for: the `price` action is the only one
with a *payload*, the only one with a dry run, and the only one that can hand a
column back and forth with a supplier sync. Every one of those is a route
concern.

Six failures it exists to catch, all of which leave both pure modules green:

  * **The dry run writes.** `dry_run: true` is answered above the claim, so a
    misplaced `continue` or a flag read further down would turn §34's preview into
    the thing it exists to prevent. The test is not "does it say preview" — it is
    "is the row unchanged afterwards, and is the key still spendable".
  * **The preview and the commit disagree.** The arrow the seller approves —
    "$49.00 → $58.80" — is a promise. Two formatters, or two cost lookups, and the
    number they approved is not the number stored. The only way to prove one
    decision is to ask twice and compare.
  * **UNKNOWN COST becomes $0.00.** A listing with no supplier source has no
    cost. Treating the absence as zero prices a product at the markup alone, which
    for "cost + 20%" is a free product, and the batch reports it a success.
  * **A live storefront is repriced without review.** `price_label` is a material
    field, so a bulk +20% over approved listings must put them back in the queue.
    If it did not, this endpoint would be the cheapest possible moderation bypass
    in the application.
  * **The next supplier sync reverts it.** §25. Without `mark_overridden` the
    reprice appears to work and then silently reverts on a schedule nobody
    watches.
  * **Supplier cost reaches the seller's own client.** §27's narrower half: the
    result entries name a price, and they must not name the margin it was derived
    from.
"""

import json
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="batch_price_route_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

import bot  # noqa: E402

SELLER = 96701
OTHER_SELLER = 96702
NOW = "2026-09-01T00:00:00"


class SellerListingBatchPriceRouteTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db_path = _DB_PATH
        bot.init_db()
        from services.business_os.marketplace import listing_batch
        listing_batch.ensure_schema()
        cls._real_account_user = bot.api_account_user
        bot.webhook_app.config["TESTING"] = True
        cls.client = bot.webhook_app.test_client()

    @classmethod
    def tearDownClass(cls):
        bot.api_account_user = cls._real_account_user

    def setUp(self):
        self.login(SELLER)
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("DELETE FROM marketplace_listings WHERE seller_user_id IN (?,?)",
                    (SELLER, OTHER_SELLER))
        cur.execute("DELETE FROM marketplace_product_sources WHERE seller_user_id IN (?,?)",
                    (SELLER, OTHER_SELLER))
        cur.execute("DELETE FROM marketplace_sellers WHERE user_id IN (?,?)",
                    (SELLER, OTHER_SELLER))
        cur.execute("DELETE FROM marketplace_listing_batches WHERE seller_user_id IN (?,?)",
                    (str(SELLER), str(OTHER_SELLER)))
        for user_id, username in ((SELLER, "price_seller"), (OTHER_SELLER, "price_rival")):
            cur.execute("INSERT OR IGNORE INTO users (user_id, username, display_name) VALUES (?,?,?)",
                        (user_id, username, username))
            cur.execute(
                "INSERT INTO marketplace_sellers (user_id, display_name, status, created_at, updated_at)"
                " VALUES (?,?,?,?,?)", (user_id, f"Store {user_id}", "approved", NOW, NOW))
        conn.commit()
        conn.close()

    # -- helpers --------------------------------------------------------------

    def login(self, user_id):
        bot.api_account_user = lambda *a, **k: {
            "user_id": user_id, "username": f"user{user_id}",
            "email": f"user{user_id}@example.com"}

    def insert_listing(self, seller_user_id=SELLER, cost_cents=4000, **overrides):
        """A live, approved, priced listing — with a supplier cost unless told not to.

        `cost_cents=None` is the merchant-authored case: a real listing with no
        source row at all, which is most of a hand-built store and the one shape
        where "cost + 20%" has nothing to work from.
        """
        row = {
            "seller_user_id": seller_user_id,
            "title": "Brass desk lamp",
            "description": "A weighted brass lamp with a linen shade.",
            "category": "Home",
            "price_label": "$49.00",
            "currency": "USD",
            "cover_image_url": "https://cdn.example/lamp.jpg",
            "status": "active",
            "approval_status": "approved",
            "listing_type": "physical",
            "product_type": "physical",
            "quantity": 40,
            "created_at": NOW,
            "updated_at": NOW,
        }
        row.update(overrides)
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute(f"INSERT INTO marketplace_listings ({cols}) VALUES ({marks})", tuple(row.values()))
        listing_id = int(cur.lastrowid)
        if cost_cents is not None:
            cur.execute(
                "INSERT INTO marketplace_product_sources (listing_id, seller_user_id, provider,"
                " provider_product_id, supplier_cost_cents, supplier_cost_currency, created_at,"
                " updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (listing_id, seller_user_id, "CJ", f"cj-{listing_id}", cost_cents, "USD", NOW, NOW))
        conn.commit()
        conn.close()
        return listing_id

    def stored(self, listing_id):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM marketplace_listings WHERE id=?", (listing_id,)).fetchone()
        conn.close()
        return dict(row) if row else {}

    def overridden(self, listing_id):
        conn = sqlite3.connect(self.db_path)
        row = conn.execute(
            "SELECT overridden_fields_json FROM marketplace_product_sources WHERE listing_id=?",
            (listing_id,)).fetchone()
        conn.close()
        return json.loads(row[0]) if row and row[0] else []

    def reprice(self, listing_ids, rule=None, key="price-key-1", dry_run=False):
        body = {
            "action": "price",
            "listing_ids": listing_ids,
            "idempotency_key": key,
            "pricing_rule": rule or {"type": "COST_PLUS_PERCENT", "value": 20},
        }
        if dry_run:
            body["dry_run"] = True
        return self.client.post(
            "/api/pulse/marketplace/seller/listings/batch",
            data=json.dumps(body), content_type="application/json")

    def outcomes(self, body):
        return {int(r["listing_id"]): r for r in body["results"]}

    # -- §34: the dry run is dry ---------------------------------------------

    def test_a_preview_changes_no_row(self):
        """The one thing a preview must not do.

        Asserted on the stored row rather than on the response, because a response
        that says `preview` while the UPDATE ran is exactly the bug.
        """
        listing_id = self.insert_listing()
        body = self.reprice([listing_id], dry_run=True).get_json()

        self.assertTrue(body["preview"])
        self.assertEqual(self.stored(listing_id)["price_label"], "$49.00")
        self.assertEqual(self.stored(listing_id)["status"], "active")

    def test_a_preview_does_not_spend_the_key(self):
        """A preview that claimed its key would make the commit a replay — the
        seller would read a confirmation for a batch that never ran."""
        listing_id = self.insert_listing()
        self.reprice([listing_id], key="shared-key", dry_run=True)

        body = self.reprice([listing_id], key="shared-key").get_json()

        self.assertNotIn("replayed", body)
        self.assertEqual(self.outcomes(body)[listing_id]["outcome"], "succeeded")

    def test_a_preview_says_would_apply_not_succeeded(self):
        """Separate vocabularies, so a preview cannot be rendered as a result.
        `succeeded` is the word the store prints as "14 products repriced"."""
        listing_id = self.insert_listing()
        body = self.reprice([listing_id], dry_run=True).get_json()

        self.assertEqual(self.outcomes(body)[listing_id]["outcome"], "would_apply")
        self.assertNotIn("successful_count", body)
        self.assertNotIn("batch_id", body)

    def test_a_preview_is_validated_exactly_like_a_real_request(self):
        """Otherwise the preview is a way around validation, and the seller is
        shown the outcome of a batch that would be refused."""
        listing_id = self.insert_listing()
        response = self.reprice(
            [listing_id], rule={"type": "MANUAL_PRICE", "value": 1}, dry_run=True)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "INVALID_PRICING_RULE")

    # -- §21: one decision, asked twice --------------------------------------

    def test_the_previewed_price_is_the_price_that_lands(self):
        """§34's promise, stated as an equality.

        Not "both look right" — the same string. A second formatter on either
        side disagrees the first time a rule lands on a fraction of a cent, and
        it does so across a whole storefront at once.
        """
        listing_id = self.insert_listing(cost_cents=4999)
        preview = self.outcomes(self.reprice([listing_id], dry_run=True).get_json())[listing_id]

        committed = self.outcomes(self.reprice([listing_id]).get_json())[listing_id]

        self.assertEqual(preview["price_label"], committed["price_label"])
        self.assertEqual(self.stored(listing_id)["price_label"], preview["price_label"])

    def test_the_preview_names_the_price_the_row_has_now(self):
        """The left-hand side of the arrow. Read off the row, not reconstructed,
        so a listing whose label the seller typed by hand still shows what they
        typed."""
        listing_id = self.insert_listing(price_label="$49.00")
        entry = self.outcomes(self.reprice([listing_id], dry_run=True).get_json())[listing_id]

        self.assertEqual(entry["current_price_label"], "$49.00")
        self.assertNotEqual(entry["price_label"], "$49.00")

    def test_a_fixed_amount_is_read_as_minor_units(self):
        """The client sends 500 for "$5 on everything", and this is the contract
        that makes that the right thing to send. A route reading 500 as dollars
        would add $500 to every product and report it a success."""
        listing_id = self.insert_listing(cost_cents=4000)
        entry = self.outcomes(
            self.reprice([listing_id], rule={"type": "COST_PLUS_FIXED", "value": 500},
                         dry_run=True).get_json())[listing_id]

        self.assertEqual(entry["price_label"], "$45.00")

    # -- §11: unknown cost is not zero ---------------------------------------

    def test_a_listing_with_no_supplier_cost_is_blocked_not_zeroed(self):
        """The §11 rule where it bites hardest.

        No source row means no cost. Treating the absence as zero turns
        "cost + 20%" into a free product, and the batch would report it a success
        — the seller finds out from an order.

        The block *code* is asserted, not merely the fact of a block, because
        under a percent rule a cost defaulted to zero blocks anyway: 0 × 1.2 is 0
        and zero is refused as a price. That row is blocked for the wrong reason
        by accident, and a test satisfied with "blocked" reads the accident as
        the rule working. The seller sees the difference too — "add a supplier
        cost" and "that rule doesn't give a usable price" are different jobs.
        """
        listing_id = self.insert_listing(cost_cents=None)
        body = self.reprice([listing_id]).get_json()
        entry = self.outcomes(body)[listing_id]

        self.assertEqual(entry["outcome"], "blocked")
        self.assertEqual(entry["error_code"], "UNKNOWN_COST")
        self.assertEqual(self.stored(listing_id)["price_label"], "$49.00")
        self.assertNotIn("$0.00", json.dumps(body))

    def test_a_flat_markup_on_an_unknown_cost_is_not_the_markup_alone(self):
        """The one rule where "unknown cost is zero" produces a writable price.

        Every other rule multiplies, so a defaulted zero collapses to zero and
        gets refused by the guard against a zero price — the §11 rule appears to
        hold whichever way it is written. ``COST_PLUS_FIXED`` adds instead, so a
        cost read as zero makes "$5 over cost" mean "$5", full stop: a real
        price, on a live product, reported as a success, on exactly the listings
        whose supplier row is missing. Nothing downstream can tell that apart
        from a seller who meant to charge $5.
        """
        listing_id = self.insert_listing(cost_cents=None)
        body = self.reprice(
            [listing_id], rule={"type": "COST_PLUS_FIXED", "value": 500}).get_json()
        entry = self.outcomes(body)[listing_id]

        self.assertEqual(entry["outcome"], "blocked")
        self.assertEqual(entry["error_code"], "UNKNOWN_COST")
        self.assertEqual(body["successful_count"], 0)
        self.assertEqual(self.stored(listing_id)["price_label"], "$49.00")
        self.assertNotIn("$5.00", json.dumps(body))

    def test_the_preview_will_not_promise_a_price_it_has_no_cost_for(self):
        """The same absence, read before the tap rather than after.

        A preview that computed the flat markup from a defaulted zero would show
        the seller "$49.00 → $5.00" and call it a change they were choosing.
        """
        listing_id = self.insert_listing(cost_cents=None)
        entry = self.outcomes(self.reprice(
            [listing_id], rule={"type": "COST_PLUS_FIXED", "value": 500},
            dry_run=True).get_json())[listing_id]

        self.assertEqual(entry["outcome"], "blocked")
        self.assertEqual(entry["error_code"], "UNKNOWN_COST")
        self.assertNotIn("price_label", entry)

    def test_a_blocked_row_does_not_stop_the_ones_that_can_be_priced(self):
        """§19. A store that is half dropshipped and half hand-written is the
        normal case, not an error, and refusing the whole batch over it is the
        failure a per-row verdict exists to avoid."""
        priced = self.insert_listing(cost_cents=4000)
        costless = self.insert_listing(cost_cents=None)

        body = self.reprice([priced, costless]).get_json()

        self.assertEqual(body["successful_count"], 1)
        self.assertEqual(body["blocked_count"], 1)
        self.assertEqual(self.outcomes(body)[priced]["outcome"], "succeeded")
        self.assertEqual(self.stored(priced)["price_label"], "$48.00")
        self.assertEqual(self.stored(costless)["price_label"], "$49.00")

    def test_the_preview_blocks_the_same_row_the_commit_does(self):
        """Otherwise the seller approves fourteen and gets thirteen."""
        priced = self.insert_listing(cost_cents=4000)
        costless = self.insert_listing(cost_cents=None)

        preview = self.outcomes(self.reprice([priced, costless], dry_run=True).get_json())
        committed = self.outcomes(self.reprice([priced, costless]).get_json())

        self.assertEqual(preview[costless]["outcome"], "blocked")
        self.assertEqual(committed[costless]["outcome"], "blocked")
        self.assertEqual(preview[costless]["reason"], committed[costless]["reason"])

    # -- the material-field rule: no moderation bypass -----------------------

    def test_repricing_a_live_listing_sends_it_back_to_review(self):
        """The cheapest moderation bypass in the application, closed.

        `price_label` is a material field, so the single edit re-reviews. If bulk
        did not, "select all → +20%" would reprice an approved storefront with no
        moderator ever seeing it.
        """
        listing_id = self.insert_listing(status="active", approval_status="approved")
        body = self.reprice([listing_id]).get_json()
        row = self.stored(listing_id)

        self.assertEqual(row["status"], "pending_review")
        self.assertEqual(row["approval_status"], "pending_review")
        self.assertTrue(self.outcomes(body)[listing_id]["returns_to_review"])

    def test_the_preview_warns_before_the_tap(self):
        """A seller repricing their storefront is entitled to know which products
        leave it, and to know it before committing rather than from a buyer who
        cannot find them."""
        listing_id = self.insert_listing(status="active", approval_status="approved")
        entry = self.outcomes(self.reprice([listing_id], dry_run=True).get_json())[listing_id]

        self.assertTrue(entry["returns_to_review"])

    def test_a_draft_is_not_said_to_go_back_to_review(self):
        """The same field, and the reason it is the server's to write.

        A draft was never on the storefront, so nothing goes *back*. A client
        deriving this from `status == "pending_review"` would invent a consequence
        for every row already in the queue.
        """
        draft = self.insert_listing(status="draft", approval_status="draft")
        body = self.reprice([draft]).get_json()
        entry = self.outcomes(body)[draft]

        self.assertEqual(entry["outcome"], "succeeded")
        self.assertFalse(entry.get("returns_to_review"))
        self.assertEqual(self.stored(draft)["status"], "draft")
        self.assertNotIn("status", entry.get("changes_applied") or [])

    def test_a_listing_already_in_the_queue_is_repriced_without_a_warning(self):
        """Pending review and repriced: the price moves, the status does not, and
        `returns_to_review` is false because it is already there."""
        pending = self.insert_listing(status="pending_review", approval_status="pending_review")
        entry = self.outcomes(self.reprice([pending]).get_json())[pending]

        self.assertEqual(entry["outcome"], "succeeded")
        self.assertFalse(entry.get("returns_to_review"))
        self.assertEqual(self.stored(pending)["price_label"], "$48.00")

    # -- §25: the supplier must not take the column back ---------------------

    def test_a_repriced_listing_is_marked_overridden(self):
        """§25, and the failure mode that makes it worth a test: without this the
        reprice works and then reverts on the next sync, on a schedule nobody is
        watching."""
        listing_id = self.insert_listing(cost_cents=4000)
        self.reprice([listing_id])

        self.assertIn("price_label", self.overridden(listing_id))

    def test_a_merchant_authored_listing_needs_no_override_row(self):
        """No supplier means no one who could overwrite the column, so the absence
        of a source row is not a failed reprice. It is blocked for want of a cost,
        which is a different answer and the honest one."""
        listing_id = self.insert_listing(cost_cents=None)
        body = self.reprice([listing_id]).get_json()

        self.assertEqual(body["failed_count"], 0)
        self.assertEqual(body["blocked_count"], 1)

    # -- §23: one attempt, one key -------------------------------------------

    def test_a_double_tap_reprices_once(self):
        listing_id = self.insert_listing(cost_cents=4000)
        first = self.reprice([listing_id], key="same-key").get_json()

        second = self.reprice([listing_id], key="same-key").get_json()

        self.assertTrue(second["replayed"])
        self.assertEqual(first["results"], second["results"])
        # The compounding failure this prevents: +20% twice is +44%.
        self.assertEqual(self.stored(listing_id)["price_label"], "$48.00")

    def test_the_same_key_with_a_different_rule_is_refused(self):
        """A spent key is answered by replay, without looking at the payload. If
        the route accepted a second rule under the first rule's key, the seller
        would read a confirmation for prices that were never applied — so the
        refusal has to happen here, not in the replay."""
        listing_id = self.insert_listing(cost_cents=4000)
        self.reprice([listing_id], key="rule-key")

        response = self.reprice(
            [listing_id], rule={"type": "COST_PLUS_PERCENT", "value": 25}, key="rule-key")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.stored(listing_id)["price_label"], "$48.00")

    # -- ownership and §27 ----------------------------------------------------

    def test_another_sellers_listing_is_never_repriced(self):
        mine = self.insert_listing(cost_cents=4000)
        theirs = self.insert_listing(seller_user_id=OTHER_SELLER, cost_cents=4000)

        body = self.reprice([mine, theirs]).get_json()

        self.assertEqual(self.outcomes(body)[theirs]["outcome"], "failed")
        self.assertEqual(self.stored(theirs)["price_label"], "$49.00")

    def test_a_foreign_listing_is_indistinguishable_from_one_that_never_existed(self):
        """Otherwise the endpoint is a way to enumerate which listing ids exist."""
        theirs = self.insert_listing(seller_user_id=OTHER_SELLER, cost_cents=4000)

        foreign = self.outcomes(self.reprice([theirs], key="k-a").get_json())[theirs]
        missing = self.outcomes(self.reprice([999999], key="k-b").get_json())[999999]

        self.assertEqual(foreign["error_code"], missing["error_code"])
        self.assertEqual(foreign["reason"], missing["reason"])

    def test_no_supplier_cost_or_margin_reaches_the_client(self):
        """§27's narrower half. The result names a price; it must not name the
        cost it was derived from, because the same shapes are consumed by screens
        a buyer can reach."""
        listing_id = self.insert_listing(cost_cents=4000)
        for dry in (True, False):
            body = self.reprice([listing_id], key=f"leak-{dry}", dry_run=dry).get_json()
            blob = json.dumps(body)
            for forbidden in ("supplier_cost", "cost_cents", "margin", "4000"):
                self.assertNotIn(forbidden, blob, f"dry_run={dry} leaked {forbidden}")

    # -- the route's own refusals --------------------------------------------

    def test_a_reprice_with_no_rule_is_refused(self):
        """`price` is the only action with a payload, so it is the only one that
        can be sent without the thing it needs."""
        listing_id = self.insert_listing()
        response = self.client.post(
            "/api/pulse/marketplace/seller/listings/batch",
            data=json.dumps({"action": "price", "listing_ids": [listing_id],
                             "idempotency_key": "no-rule"}),
            content_type="application/json")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.stored(listing_id)["price_label"], "$49.00")

    def test_an_unapproved_seller_cannot_reprice_in_bulk(self):
        """The single-listing edit refuses an unapproved seller. If bulk did not,
        this endpoint would be the one way to edit a listing without merchant
        approval."""
        listing_id = self.insert_listing(cost_cents=4000)
        conn = sqlite3.connect(self.db_path)
        conn.execute("UPDATE marketplace_sellers SET status='pending' WHERE user_id=?", (SELLER,))
        conn.commit()
        conn.close()

        response = self.reprice([listing_id])

        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.stored(listing_id)["price_label"], "$49.00")

    def test_an_anonymous_request_is_refused(self):
        listing_id = self.insert_listing()
        bot.api_account_user = lambda *a, **k: None

        response = self.reprice([listing_id])

        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.stored(listing_id)["price_label"], "$49.00")


if __name__ == "__main__":
    unittest.main()
