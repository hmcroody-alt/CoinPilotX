"""The bulk listing route end to end: does the batch actually move the rows?

`tests/business_os/test_listing_batch.py` pins what a batch decides. Every
decision there is a pure function, which is what makes it provable — and also
what makes it unable to catch any of the things only the route can get wrong:

  * the batch WRITES. A perfect verdict attached to nothing is what this
    endpoint replaced: the module existed, was fully tested, and no route in
    the application called it, so no seller could publish more than one listing
    at a time;
  * PARTIAL SUCCESS is real. Fourteen ready and four blocked must leave
    fourteen rows moved and four untouched. The two failures worth naming are
    the batch that refuses all eighteen because four were bad, and the batch
    that reports eighteen successes because it never looked;
  * OWNERSHIP is per listing, not per request. A seller authenticated for their
    own store who slips someone else's listing id into the array must not move
    it, and must not learn whether it exists;
  * a RETRY does not act twice. Publishing submits for review and bumps
    `review_version`; a double tap that ran twice would put two submissions in
    the moderation queue for one listing.
"""

import json
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="batch_route_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

import bot  # noqa: E402

SELLER = 96601
OTHER_SELLER = 96602
NOW = "2026-09-01T00:00:00"


class SellerListingBatchRouteTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db_path = _DB_PATH
        bot.init_db()
        # The batch ledger is not in `init_db`; the route creates it on first
        # use, on its own connection. See `listing_batch.ensure_schema`.
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
        cur.execute("DELETE FROM marketplace_sellers WHERE user_id IN (?,?)",
                    (SELLER, OTHER_SELLER))
        cur.execute("DELETE FROM marketplace_listing_batches WHERE seller_user_id IN (?,?)",
                    (str(SELLER), str(OTHER_SELLER)))
        for user_id, username in ((SELLER, "batch_seller"), (OTHER_SELLER, "batch_rival")):
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

    def insert_listing(self, seller_user_id=SELLER, **overrides):
        """A draft that is ready to publish. Each test breaks exactly one fact."""
        row = {
            "seller_user_id": seller_user_id,
            "title": "Brass desk lamp",
            "description": "A weighted brass lamp with a linen shade.",
            "category": "Home",
            "price_label": "$24.00",
            "cover_image_url": "https://cdn.example/lamp.jpg",
            "status": "draft",
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
        conn.commit()
        conn.close()
        return listing_id

    def stored(self, listing_id):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM marketplace_listings WHERE id=?", (listing_id,)).fetchone()
        conn.close()
        return dict(row) if row else {}

    def batch(self, action, listing_ids, key="key-1"):
        return self.client.post(
            "/api/pulse/marketplace/seller/listings/batch",
            data=json.dumps({"action": action, "listing_ids": listing_ids,
                             "idempotency_key": key}),
            content_type="application/json")

    def outcomes(self, body):
        return {int(r["listing_id"]): r for r in body["results"]}

    # -- the batch writes -----------------------------------------------------

    def test_a_ready_draft_is_actually_submitted(self):
        """The whole point. Before this route the decision existed and the row
        never moved."""
        listing_id = self.insert_listing()
        response = self.batch("publish", [listing_id])
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        body = response.get_json()

        self.assertEqual(body["successful_count"], 1)
        self.assertEqual(body["blocked_count"], 0)
        self.assertEqual(body["failed_count"], 0)
        self.assertEqual(self.outcomes(body)[listing_id]["outcome"], "succeeded")

        # Read back, because a summary is a claim and the row is the fact.
        self.assertEqual(self.stored(listing_id)["status"], "pending_review")

    def test_the_batch_lands_a_row_in_the_same_state_the_single_route_does(self):
        """§21. One publication engine, asserted by comparing its two callers.

        A seller who publishes one listing and a seller who publishes eighteen
        must end up with rows in the same state, or the store's own filters
        start disagreeing with themselves.
        """
        single = self.insert_listing()
        bulk = self.insert_listing()

        self.assertEqual(
            self.client.post(f"/api/pulse/marketplace/seller/listings/{single}/submit").status_code,
            200)
        self.assertEqual(self.batch("publish", [bulk]).status_code, 200)

        ignored = {"id", "created_at", "updated_at", "submitted_at"}
        one, many = self.stored(single), self.stored(bulk)
        self.assertEqual({k: v for k, v in one.items() if k not in ignored},
                         {k: v for k, v in many.items() if k not in ignored})

    def test_hide_pauses_without_consulting_readiness(self):
        """A seller must be able to take a broken listing off sale.

        Hiding that depended on the readiness engine would trap the listings
        most in need of hiding.
        """
        broken = self.insert_listing(status="active", price_label="", title="")
        response = self.batch("hide", [broken])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["successful_count"], 1)
        self.assertEqual(self.stored(broken)["status"], "paused")

    # -- partial success ------------------------------------------------------

    def test_fourteen_publish_and_four_stay_drafts(self):
        """§19. The mixed batch, at the size the mission names.

        Both halves are asserted against the table. A batch that refused all
        eighteen and a batch that claimed all eighteen are the two failures this
        is here to tell apart, and both of them produce a plausible summary.
        """
        ready = [self.insert_listing() for _ in range(14)]
        blocked = [self.insert_listing(price_label="") for _ in range(4)]

        body = self.batch("publish", ready + blocked).get_json()

        self.assertEqual(body["requested_count"], 18)
        self.assertEqual(body["successful_count"], 14)
        self.assertEqual(body["blocked_count"], 4)
        self.assertEqual(body["failed_count"], 0)

        for listing_id in ready:
            self.assertEqual(self.stored(listing_id)["status"], "pending_review")
        for listing_id in blocked:
            self.assertEqual(self.stored(listing_id)["status"], "draft",
                             "a blocked listing was published anyway")

    def test_a_blocked_row_says_exactly_what_is_missing(self):
        """§18/§20. "Needs attention: 4" with no reason is a dead end."""
        listing_id = self.insert_listing(price_label="", category="")
        result = self.outcomes(self.batch("publish", [listing_id]).get_json())[listing_id]

        self.assertEqual(result["outcome"], "blocked")
        self.assertEqual(result["reason"], "2 things left")
        self.assertEqual(set(result["blockers"]), {"MISSING_PRICE", "MISSING_CATEGORY"})
        # And in words the seller can act on, from the one engine that owns them.
        self.assertEqual({f["label"] for f in result["fixes"]},
                         {"Add price", "Choose category"})
        self.assertEqual({f["section"] for f in result["fixes"]}, {"pricing", "details"})

    def test_the_counts_always_add_up_to_the_request(self):
        """A summary that disagrees with its own detail is the one thing a
        seller cannot check."""
        body = self.batch("publish", [
            self.insert_listing(),
            self.insert_listing(price_label=""),
            self.insert_listing(seller_user_id=OTHER_SELLER),
        ]).get_json()

        self.assertEqual(body["requested_count"], len(body["results"]))
        self.assertEqual(
            body["successful_count"] + body["blocked_count"] + body["failed_count"],
            body["requested_count"])
        self.assertEqual((body["successful_count"], body["blocked_count"],
                          body["failed_count"]), (1, 1, 1))

    # -- ownership ------------------------------------------------------------

    def test_another_sellers_listing_is_never_moved(self):
        """§27. Authentication is for the request; ownership is per listing.

        The rival row is inserted `draft` and ready, so nothing except the
        ownership check stands between this request and publishing it.
        """
        mine = self.insert_listing()
        theirs = self.insert_listing(seller_user_id=OTHER_SELLER)

        body = self.batch("publish", [mine, theirs]).get_json()

        self.assertEqual(self.outcomes(body)[theirs]["outcome"], "failed")
        self.assertEqual(self.stored(theirs)["status"], "draft")
        self.assertEqual(self.stored(mine)["status"], "pending_review")

    def test_a_foreign_listing_is_indistinguishable_from_one_that_never_existed(self):
        """Otherwise the endpoint enumerates the listing table for anyone with
        an account and a for-loop."""
        theirs = self.insert_listing(seller_user_id=OTHER_SELLER)
        missing = 99_000_123

        body = self.batch("publish", [theirs, missing]).get_json()
        results = self.outcomes(body)

        def shape(entry):
            return {k: v for k, v in entry.items() if k != "listing_id"}

        self.assertEqual(shape(results[theirs]), shape(results[missing]))
        self.assertEqual(results[theirs]["error_code"], "NOT_FOUND")

    def test_hiding_another_sellers_listing_is_refused_too(self):
        """Hide skips readiness, so it must not also skip ownership."""
        theirs = self.insert_listing(seller_user_id=OTHER_SELLER, status="active")
        body = self.batch("hide", [theirs]).get_json()

        self.assertEqual(body["successful_count"], 0)
        self.assertEqual(self.stored(theirs)["status"], "active")

    # -- idempotency ----------------------------------------------------------

    def test_a_double_tap_does_not_submit_twice(self):
        """§23. `review_version` counts submissions, so a second write is
        visible as a second entry in the moderation queue."""
        listing_id = self.insert_listing()

        first = self.batch("publish", [listing_id], key="tap-once").get_json()
        after_first = self.stored(listing_id)["review_version"]
        second = self.batch("publish", [listing_id], key="tap-once").get_json()

        self.assertEqual(second["batch_id"], first["batch_id"])
        self.assertEqual(second["results"], first["results"])
        self.assertEqual(self.stored(listing_id)["review_version"], after_first)

    def test_reusing_a_key_for_a_different_selection_is_refused(self):
        """Without this the client gets the first batch's answer naming
        listings nobody asked about, while the rows they selected never move."""
        first = self.insert_listing()
        second = self.insert_listing()

        self.assertEqual(self.batch("publish", [first], key="shared").status_code, 200)
        conflict = self.batch("publish", [second], key="shared")

        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.get_json()["error"], "IDEMPOTENCY_KEY_CONFLICT")
        self.assertEqual(self.stored(second)["status"], "draft")

    def test_one_sellers_key_is_not_another_sellers_key(self):
        mine = self.insert_listing()
        self.assertEqual(self.batch("publish", [mine], key="collide").status_code, 200)

        self.login(OTHER_SELLER)
        theirs = self.insert_listing(seller_user_id=OTHER_SELLER)
        response = self.batch("publish", [theirs], key="collide")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.stored(theirs)["status"], "pending_review")

    # -- the mutations §35 names ----------------------------------------------

    def test_a_listing_with_no_price_never_publishes(self):
        listing_id = self.insert_listing(price_label="")
        body = self.batch("publish", [listing_id]).get_json()

        self.assertEqual(body["successful_count"], 0)
        self.assertEqual(self.stored(listing_id)["status"], "draft")
        # And the price is never invented on the way out.
        flat = json.dumps(body).lower()
        self.assertNotIn("$0", flat)
        self.assertNotIn("free", flat)

    def test_a_restricted_category_never_publishes_in_bulk(self):
        """The gate that used to live only in the single submit route.

        Bulk publish reads a verdict; anything the verdict cannot see is a gate
        that does not exist. Before readiness absorbed the goods policy, this
        row read "Ready to publish" on the seller's own store screen.
        """
        listing_id = self.insert_listing(category="Weapons")
        body = self.batch("publish", [listing_id]).get_json()

        self.assertEqual(body["blocked_count"], 1)
        self.assertIn("RESTRICTED_PRODUCT", self.outcomes(body)[listing_id]["blockers"])
        self.assertEqual(self.stored(listing_id)["status"], "draft")

    def test_a_live_listing_is_not_knocked_back_into_review(self):
        """"Select all" then Publish must not cost a seller their storefront."""
        live = self.insert_listing(status="active")
        body = self.batch("publish", [live]).get_json()

        self.assertEqual(body["blocked_count"], 1)
        self.assertEqual(self.outcomes(body)[live]["error_code"], "ALREADY_PUBLISHED")
        self.assertEqual(self.stored(live)["status"], "active")

    def test_a_video_only_listing_never_publishes(self):
        """Media is not a cover. A video-only row publishes as a black tile."""
        listing_id = self.insert_listing(cover_image_url="", media_url="")
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT INTO marketplace_product_media (product_id, media_url, media_type, is_cover,"
            " position, moderation_status, created_at) VALUES (?,?,?,?,?,?,?)",
            (listing_id, "https://cdn.example/clip.mp4", "video", 1, 0, "approved", NOW))
        conn.commit()
        conn.close()

        body = self.batch("publish", [listing_id]).get_json()
        self.assertIn("NO_VALID_MEDIA", self.outcomes(body)[listing_id]["blockers"])
        self.assertEqual(self.stored(listing_id)["status"], "draft")

    def test_no_supplier_cost_or_margin_reaches_the_client(self):
        """§27. The batch reply is rendered on the seller's phone, but the same
        shape is the one a leak would travel in."""
        body = self.batch("publish", [
            self.insert_listing(), self.insert_listing(price_label="")]).get_json()

        flat = json.dumps(body).lower()
        for word in ("supplier_cost", "margin", "access_token", "refresh_token",
                     "openid", "api_key", "cost_cents"):
            self.assertNotIn(word, flat, f"{word!r} has no business in a batch reply")

    # -- whole-request refusals -----------------------------------------------

    def test_an_unsupported_action_moves_nothing(self):
        """An unknown action falling through to a no-op would report
        `successful_count` for work nobody did."""
        listing_id = self.insert_listing()
        response = self.batch("delete_forever", [listing_id])

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "UNSUPPORTED_ACTION")
        self.assertEqual(self.stored(listing_id)["status"], "draft")

    def test_a_request_with_no_idempotency_key_is_refused(self):
        listing_id = self.insert_listing()
        response = self.client.post(
            "/api/pulse/marketplace/seller/listings/batch",
            data=json.dumps({"action": "publish", "listing_ids": [listing_id]}),
            content_type="application/json")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "MISSING_IDEMPOTENCY_KEY")
        self.assertEqual(self.stored(listing_id)["status"], "draft")

    def test_an_anonymous_request_is_refused(self):
        listing_id = self.insert_listing()
        bot.api_account_user = lambda *a, **k: None
        try:
            self.assertEqual(self.batch("publish", [listing_id]).status_code, 401)
        finally:
            self.login(SELLER)
        self.assertEqual(self.stored(listing_id)["status"], "draft")


if __name__ == "__main__":
    unittest.main()
