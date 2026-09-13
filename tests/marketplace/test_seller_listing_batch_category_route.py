"""Bulk Set Category, end to end — §21, §23, §25, §27, §33, §34.

The second payload action, and it is not the reprice with different strings. Two
things are genuinely different about re-filing a product, and each has its own
group below:

  * **A subcategory belongs to its parent.** Moving "Education / Crypto Basics"
    into "Home & Kitchen" and keeping the subcategory files the listing under
    "Home & Kitchen / Crypto Basics" — a pair no filter, breadcrumb or buyer can
    make sense of, and one that is indistinguishable from a pair somebody chose.
  * **The unchanged row is the dangerous one.** `category` is a MATERIAL_FIELD,
    so writing the value a listing already has takes a live product off sale and
    into the review queue. A store that is mostly already "Education" would be
    emptied by "select all → Set category: Education", and every row would be
    reported `succeeded`, because the write did succeed.

The rest is the reprice's ground re-walked, because it is the same route and the
same consequences: the dry run must not write, the preview must name the pair the
commit stores, a live listing must go back to review, the seller's choice must
survive the next supplier sync (§25), and a key means one request.
"""

import json
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="batch_category_route_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

import bot  # noqa: E402

SELLER = 96801
OTHER_SELLER = 96802
NOW = "2026-09-01T00:00:00"


class SellerListingBatchCategoryRouteTestCase(unittest.TestCase):
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
        for user_id, username in ((SELLER, "cat_seller"), (OTHER_SELLER, "cat_rival")):
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

    def insert_listing(self, seller_user_id=SELLER, sourced=True, **overrides):
        """A live, approved listing filed under "Education / Crypto Basics".

        `sourced=False` is the merchant-authored case — no supplier row, so
        nothing that could later overwrite the seller's filing.
        """
        row = {
            "seller_user_id": seller_user_id,
            "title": "Brass desk lamp",
            "description": "A weighted brass lamp with a linen shade.",
            "category": "Education",
            "subcategory": "Crypto Basics",
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
        if sourced:
            cur.execute(
                "INSERT INTO marketplace_product_sources (listing_id, seller_user_id, provider,"
                " provider_product_id, supplier_cost_cents, supplier_cost_currency, created_at,"
                " updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (listing_id, seller_user_id, "CJ", f"cj-{listing_id}", 4000, "USD", NOW, NOW))
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

    def refile(self, listing_ids, settings=None, key="cat-key-1", dry_run=False):
        body = {
            "action": "category",
            "listing_ids": listing_ids,
            "idempotency_key": key,
            "category": {"category": "Home & Kitchen"} if settings is None else settings,
        }
        if dry_run:
            body["dry_run"] = True
        return self.client.post(
            "/api/pulse/marketplace/seller/listings/batch",
            data=json.dumps(body), content_type="application/json")

    def outcomes(self, body):
        return {int(r["listing_id"]): r for r in body["results"]}

    # -- the pair: a subcategory belongs to its parent ------------------------

    def test_a_new_category_clears_a_subcategory_that_no_longer_fits(self):
        """The listing is "Education / Crypto Basics". Re-filed under Home &
        Kitchen with no subcategory given, it must not become
        "Home & Kitchen / Crypto Basics" — that pair is wrong in a way nothing
        downstream can detect, because it looks exactly like a pair somebody
        chose."""
        listing_id = self.insert_listing()

        self.refile([listing_id])

        row = self.stored(listing_id)
        self.assertEqual(row["category"], "Home & Kitchen")
        self.assertEqual(row["subcategory"], "")

    def test_a_supplied_subcategory_is_kept(self):
        """Clearing is the answer to "no subcategory was given", not a rule that
        the batch cannot set one."""
        listing_id = self.insert_listing()

        self.refile([listing_id],
                    settings={"category": "Home & Kitchen", "subcategory": "Lighting"})

        row = self.stored(listing_id)
        self.assertEqual(row["category"], "Home & Kitchen")
        self.assertEqual(row["subcategory"], "Lighting")

    def test_the_same_category_with_a_new_subcategory_is_a_real_change(self):
        """The pair is what the listing claims, so comparing only the parent
        would call this a no-op and block it."""
        listing_id = self.insert_listing()

        body = self.refile(
            [listing_id], settings={"category": "Education", "subcategory": "Trading"}).get_json()

        self.assertEqual(self.outcomes(body)[listing_id]["outcome"], "succeeded")
        self.assertEqual(self.stored(listing_id)["subcategory"], "Trading")

    # -- the unchanged row is the dangerous one -------------------------------

    def test_a_listing_already_in_that_category_is_blocked_not_rewritten(self):
        """The failure this block exists for: `category` is a material field, so
        re-writing the value a row already has sends a live product back to the
        review queue and off sale. "Select all → Education" over a store that is
        mostly Education would empty the storefront and report every row
        `succeeded`, because the write does succeed."""
        listing_id = self.insert_listing(category="Education", subcategory="Crypto Basics")

        body = self.refile(
            [listing_id],
            settings={"category": "Education", "subcategory": "Crypto Basics"}).get_json()
        entry = self.outcomes(body)[listing_id]

        self.assertEqual(entry["outcome"], "blocked")
        self.assertEqual(entry["error_code"], "CATEGORY_UNCHANGED")
        self.assertEqual(body["successful_count"], 0)
        row = self.stored(listing_id)
        self.assertEqual(row["status"], "active")
        self.assertEqual(row["approval_status"], "approved")

    def test_whitespace_in_the_request_does_not_make_a_category_different(self):
        """Otherwise "  Education  " is a change, and the store goes into review
        because a text field had a trailing space."""
        listing_id = self.insert_listing(category="Education", subcategory="Crypto Basics")

        entry = self.outcomes(self.refile(
            [listing_id],
            settings={"category": "  Education  ", "subcategory": "Crypto Basics"}).get_json()
        )[listing_id]

        self.assertEqual(entry["outcome"], "blocked")
        self.assertEqual(entry["error_code"], "CATEGORY_UNCHANGED")
        self.assertEqual(self.stored(listing_id)["status"], "active")

    def test_whitespace_already_in_the_column_does_not_make_it_different_either(self):
        """The other direction, and the one that actually happens.

        Normalizing only the incoming text is half a comparison: it makes the
        request clean and still compares it against whatever is in the column.
        Stored categories are not guaranteed clean -- a CJ feed supplies them,
        and the single-listing editor predates the trimming here -- so
        ``"Education "`` sitting in the row and ``"Education"`` arriving in the
        payload read as a change. Nothing changes except ``updated_at``, and a
        live product spends a moderation cycle off sale to record that.

        Asserted from the stored side because a test that only trims the payload
        passes whether or not the column is trimmed too.
        """
        listing_id = self.insert_listing(category=" Education ", subcategory="Crypto Basics ")

        entry = self.outcomes(self.refile(
            [listing_id],
            settings={"category": "Education", "subcategory": "Crypto Basics"}).get_json()
        )[listing_id]

        self.assertEqual(entry["outcome"], "blocked")
        self.assertEqual(entry["error_code"], "CATEGORY_UNCHANGED")
        row = self.stored(listing_id)
        self.assertEqual(row["status"], "active")
        self.assertEqual(row["approval_status"], "approved")

    def test_the_unchanged_row_does_not_stop_the_ones_that_move(self):
        """§19. Re-filing part of a store is the normal case: some rows are
        already where the seller is sending them."""
        moving = self.insert_listing(category="Education", subcategory="")
        already = self.insert_listing(category="Home & Kitchen", subcategory="")

        body = self.refile([moving, already]).get_json()

        self.assertEqual(body["successful_count"], 1)
        self.assertEqual(body["blocked_count"], 1)
        self.assertEqual(self.stored(moving)["category"], "Home & Kitchen")
        self.assertEqual(self.stored(already)["status"], "active")

    # -- §34: the dry run is dry ---------------------------------------------

    def test_a_preview_changes_no_row(self):
        listing_id = self.insert_listing()

        body = self.refile([listing_id], dry_run=True).get_json()

        self.assertTrue(body["preview"])
        row = self.stored(listing_id)
        self.assertEqual(row["category"], "Education")
        self.assertEqual(row["subcategory"], "Crypto Basics")
        self.assertEqual(row["status"], "active")

    def test_a_preview_does_not_spend_the_key(self):
        """A seller who previews three categories before choosing one has spent
        nothing, and may commit under the key they previewed with."""
        listing_id = self.insert_listing()

        self.refile([listing_id], dry_run=True)
        body = self.refile([listing_id]).get_json()

        self.assertNotIn("replayed", body)
        self.assertEqual(self.outcomes(body)[listing_id]["outcome"], "succeeded")

    def test_a_preview_says_would_apply_not_succeeded(self):
        listing_id = self.insert_listing()

        body = self.refile([listing_id], dry_run=True).get_json()

        self.assertEqual(self.outcomes(body)[listing_id]["outcome"], "would_apply")
        self.assertNotIn("successful_count", body)
        self.assertNotIn("batch_id", body)

    def test_a_preview_is_validated_exactly_like_a_real_request(self):
        """A preview cannot be a way around validation, or the seller is shown a
        preview of a batch that would be refused."""
        listing_id = self.insert_listing()

        response = self.refile([listing_id], settings={"category": "   "}, dry_run=True)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "INVALID_CATEGORY")

    # -- §21: one decision, asked twice --------------------------------------

    def test_the_previewed_pair_is_the_pair_that_lands(self):
        listing_id = self.insert_listing()

        preview = self.outcomes(self.refile([listing_id], dry_run=True).get_json())[listing_id]
        committed = self.outcomes(self.refile([listing_id]).get_json())[listing_id]

        self.assertEqual(preview["category"], committed["category"])
        self.assertEqual(preview["subcategory"], committed["subcategory"])
        self.assertEqual(self.stored(listing_id)["category"], preview["category"])
        self.assertEqual(self.stored(listing_id)["subcategory"], preview["subcategory"])

    def test_the_preview_names_the_filing_the_row_has_now(self):
        """The sheet draws "Education → Home & Kitchen" from these two fields, so
        the left-hand side has to come from the server too."""
        listing_id = self.insert_listing()

        entry = self.outcomes(self.refile([listing_id], dry_run=True).get_json())[listing_id]

        self.assertEqual(entry["current_category"], "Education")
        self.assertEqual(entry["current_subcategory"], "Crypto Basics")
        self.assertEqual(entry["category"], "Home & Kitchen")

    def test_the_preview_blocks_the_same_row_the_commit_does(self):
        moving = self.insert_listing(category="Education", subcategory="")
        already = self.insert_listing(category="Home & Kitchen", subcategory="")

        preview = self.outcomes(self.refile([moving, already], dry_run=True).get_json())
        committed = self.outcomes(self.refile([moving, already]).get_json())

        self.assertEqual(preview[already]["outcome"], "blocked")
        self.assertEqual(committed[already]["outcome"], "blocked")
        self.assertEqual(preview[already]["reason"], committed[already]["reason"])

    # -- the material-field rule: no moderation bypass -----------------------

    def test_re_filing_a_live_listing_sends_it_back_to_review(self):
        """Arguably the change a moderator most needs to see: "select all →
        Education" is how a prohibited product moves into a benign aisle, and it
        is one tap."""
        listing_id = self.insert_listing(status="active", approval_status="approved")

        body = self.refile([listing_id]).get_json()

        row = self.stored(listing_id)
        self.assertEqual(row["status"], "pending_review")
        self.assertEqual(row["approval_status"], "pending_review")
        self.assertTrue(self.outcomes(body)[listing_id]["returns_to_review"])

    def test_the_preview_warns_before_the_tap(self):
        listing_id = self.insert_listing(status="active", approval_status="approved")

        entry = self.outcomes(self.refile([listing_id], dry_run=True).get_json())[listing_id]

        self.assertTrue(entry["returns_to_review"])

    def test_a_draft_is_not_said_to_go_back_to_review(self):
        """Nothing went back, and saying so would invent a consequence."""
        draft = self.insert_listing(status="draft", approval_status="draft")

        entry = self.outcomes(self.refile([draft]).get_json())[draft]

        self.assertEqual(entry["outcome"], "succeeded")
        self.assertFalse(entry.get("returns_to_review"))
        self.assertEqual(self.stored(draft)["status"], "draft")
        self.assertNotIn("status", entry.get("changes_applied") or [])

    def test_setting_a_missing_category_is_how_a_draft_gets_readier(self):
        """MISSING_CATEGORY is a publish blocker, so this is one of the few bulk
        actions that leaves rows readier than it found them. Readiness must not
        be consulted as a gate, or the rows most in need of this would be the
        only ones it refuses."""
        draft = self.insert_listing(status="draft", approval_status="draft",
                                    category="", subcategory="")

        entry = self.outcomes(self.refile([draft]).get_json())[draft]

        self.assertEqual(entry["outcome"], "succeeded")
        self.assertEqual(self.stored(draft)["category"], "Home & Kitchen")

    # -- §25: the supplier must not take the column back ---------------------

    def test_a_re_filed_listing_is_marked_overridden(self):
        """Without this the next CJ sync owns `category` again and files the
        product back where the provider thinks it belongs — the seller's
        re-organised store un-organises itself on a schedule nobody watches."""
        listing_id = self.insert_listing(sourced=True)

        self.refile([listing_id])

        self.assertIn("category", self.overridden(listing_id))

    def test_the_subcategory_is_handed_over_too(self):
        """Handing over only the parent lets a sync restore the provider's
        subcategory under the seller's category — the incoherent pair, put back
        by the mechanism that was supposed to protect the change."""
        listing_id = self.insert_listing(sourced=True)

        self.refile([listing_id])

        self.assertIn("subcategory", self.overridden(listing_id))

    def test_a_merchant_authored_listing_needs_no_override_row(self):
        """No supplier source means nobody else owns the column. Not a failure."""
        listing_id = self.insert_listing(sourced=False)

        body = self.refile([listing_id]).get_json()

        self.assertEqual(body["failed_count"], 0)
        self.assertEqual(body["successful_count"], 1)
        self.assertEqual(self.stored(listing_id)["category"], "Home & Kitchen")

    # -- §23: a key means one request ----------------------------------------

    def test_a_double_tap_re_files_once(self):
        listing_id = self.insert_listing()

        first = self.refile([listing_id]).get_json()
        second = self.refile([listing_id]).get_json()

        self.assertTrue(second["replayed"])
        self.assertEqual(first["results"], second["results"])

    def test_the_same_key_with_a_different_category_is_refused(self):
        """Same key, same rows, "Home & Kitchen" then "Garden" is not a replay.
        Treating it as one hands the seller the first batch's summary while their
        store keeps the first category."""
        listing_id = self.insert_listing()
        self.refile([listing_id], settings={"category": "Home & Kitchen"})

        response = self.refile([listing_id], settings={"category": "Garden"})

        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.stored(listing_id)["category"], "Home & Kitchen")

    # -- ownership and refusals ----------------------------------------------

    def test_another_sellers_listing_is_never_re_filed(self):
        theirs = self.insert_listing(seller_user_id=OTHER_SELLER)

        body = self.refile([theirs]).get_json()

        self.assertEqual(self.outcomes(body)[theirs]["outcome"], "failed")
        self.assertEqual(self.stored(theirs)["category"], "Education")

    def test_a_foreign_listing_is_indistinguishable_from_one_that_never_existed(self):
        """Otherwise the endpoint enumerates which listing ids exist."""
        theirs = self.insert_listing(seller_user_id=OTHER_SELLER)

        body = self.refile([theirs, 7778889]).get_json()
        foreign = self.outcomes(body)[theirs]
        missing = self.outcomes(body)[7778889]

        self.assertEqual(foreign["error_code"], missing["error_code"])
        self.assertEqual(foreign["reason"], missing["reason"])

    def test_no_supplier_cost_reaches_the_client(self):
        """§27. This action names no price, so it has no business naming a cost
        either — and it reads the same source rows the reprice does."""
        listing_id = self.insert_listing(sourced=True)

        for dry in (True, False):
            blob = json.dumps(self.refile(
                [listing_id], key=f"cat-leak-{dry}", dry_run=dry).get_json())
            for forbidden in ("supplier_cost", "cost_cents", "margin", "4000"):
                self.assertNotIn(forbidden, blob, f"dry_run={dry} leaked {forbidden}")

    def test_a_re_filing_with_no_category_is_refused(self):
        listing_id = self.insert_listing()

        response = self.client.post(
            "/api/pulse/marketplace/seller/listings/batch",
            data=json.dumps({"action": "category", "listing_ids": [listing_id],
                             "idempotency_key": "cat-none"}),
            content_type="application/json")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "INVALID_CATEGORY")
        self.assertEqual(self.stored(listing_id)["category"], "Education")

    def test_a_category_that_is_not_text_is_refused(self):
        """`str(True)` is a category named "True", and coercing here would file
        products under it."""
        listing_id = self.insert_listing()

        response = self.refile([listing_id], settings={"category": True})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.stored(listing_id)["category"], "Education")

    def test_an_unrecognised_setting_is_refused(self):
        """A client sending `{"category": ..., "visibility": "public"}` believes
        it is asking for two things. Silence would have the seller told the batch
        did both."""
        listing_id = self.insert_listing()

        response = self.refile(
            [listing_id], settings={"category": "Garden", "visibility": "public"})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "INVALID_CATEGORY")
        self.assertEqual(self.stored(listing_id)["category"], "Education")

    # -- settings that belong to another action -------------------------------
    #
    # Found by driving the real HTTP flow, not by these tests, and the gap is
    # worth naming because it is the shape that keeps recurring: the validator
    # refused a payload on `hide` and had a unit test proving it, the route read
    # a payload key and had tests proving that, and nothing tested the seam. The
    # route picked the key *by action* —
    #
    #     settings = body.get("category") if action == "category" else body.get("pricing_rule")
    #
    # — so `{"action": "hide", "category": {...}}` did not reach the refusal at
    # all. It looked for a `pricing_rule`, found none, and ran as a plain hide:
    # every selected product pulled from sale, `succeeded` for each one. The
    # equivalent with `pricing_rule` *was* caught, which is why the hole survived
    # a mutation run and a green suite — the one key the else-branch happened to
    # read was the one key under test.
    #
    # A client cannot send these by accident, but it can send them by regression,
    # and the failure is silent and destructive in the same breath: the seller
    # meant to re-file forty listings and is told forty things worked.

    def test_a_category_sent_with_hide_is_refused_not_quietly_hidden(self):
        listing_id = self.insert_listing()

        response = self.client.post(
            "/api/pulse/marketplace/seller/listings/batch",
            data=json.dumps({"action": "hide", "listing_ids": [listing_id],
                             "idempotency_key": "hide-cat", "category": {"category": "Garden"}}),
            content_type="application/json")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "UNSUPPORTED_ACTION")
        row = self.stored(listing_id)
        self.assertEqual(row["status"], "active", "the listing was hidden by a refused request")
        self.assertEqual(row["category"], "Education")

    def test_a_category_sent_with_publish_is_refused(self):
        listing_id = self.insert_listing(status="draft", approval_status="draft")

        response = self.client.post(
            "/api/pulse/marketplace/seller/listings/batch",
            data=json.dumps({"action": "publish", "listing_ids": [listing_id],
                             "idempotency_key": "pub-cat", "category": {"category": "Garden"}}),
            content_type="application/json")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "UNSUPPORTED_ACTION")
        row = self.stored(listing_id)
        self.assertEqual(row["status"], "draft")
        self.assertEqual(row["category"], "Education")

    def test_a_pricing_rule_sent_alongside_a_category_is_refused(self):
        """Two settings for one action is a client asking for two things. The
        batch does one, and answering `succeeded` would confirm both."""
        listing_id = self.insert_listing()

        response = self.client.post(
            "/api/pulse/marketplace/seller/listings/batch",
            data=json.dumps({"action": "category", "listing_ids": [listing_id],
                             "idempotency_key": "cat-and-rule",
                             "category": {"category": "Garden"},
                             "pricing_rule": {"type": "COST_PLUS_PERCENT", "value": 20}}),
            content_type="application/json")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "UNSUPPORTED_ACTION")
        row = self.stored(listing_id)
        self.assertEqual(row["category"], "Education")
        self.assertEqual(row["price_label"], "$49.00")

    def test_a_category_sent_alongside_a_pricing_rule_is_refused(self):
        listing_id = self.insert_listing()

        response = self.client.post(
            "/api/pulse/marketplace/seller/listings/batch",
            data=json.dumps({"action": "price", "listing_ids": [listing_id],
                             "idempotency_key": "rule-and-cat",
                             "pricing_rule": {"type": "COST_PLUS_PERCENT", "value": 20},
                             "category": {"category": "Garden"}}),
            content_type="application/json")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "UNSUPPORTED_ACTION")
        row = self.stored(listing_id)
        self.assertEqual(row["price_label"], "$49.00")
        self.assertEqual(row["category"], "Education")

    def test_a_null_setting_is_absent_rather_than_a_second_request(self):
        """A JSON `null` cannot be told apart from an omitted key by anyone
        reading the body, so it must not be a different outcome. The re-file
        proceeds; it is the `pricing_rule` that is absent, not empty."""
        listing_id = self.insert_listing()

        response = self.client.post(
            "/api/pulse/marketplace/seller/listings/batch",
            data=json.dumps({"action": "category", "listing_ids": [listing_id],
                             "idempotency_key": "cat-null-rule",
                             "category": {"category": "Garden"}, "pricing_rule": None}),
            content_type="application/json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.stored(listing_id)["category"], "Garden")

    def test_an_unapproved_seller_cannot_re_file_in_bulk(self):
        """Otherwise this endpoint is the one way to edit a listing without
        merchant approval."""
        listing_id = self.insert_listing()
        conn = sqlite3.connect(self.db_path)
        conn.execute("UPDATE marketplace_sellers SET status='pending' WHERE user_id=?", (SELLER,))
        conn.commit()
        conn.close()

        response = self.refile([listing_id])

        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.stored(listing_id)["category"], "Education")

    def test_an_anonymous_request_is_refused(self):
        listing_id = self.insert_listing()
        bot.api_account_user = lambda *a, **k: None

        response = self.refile([listing_id])

        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.stored(listing_id)["category"], "Education")


if __name__ == "__main__":
    unittest.main()
