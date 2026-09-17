"""The readiness verdict on the wire: does the seller get it, and only the seller?

`tests/business_os/test_listing_readiness.py` pins what the engine decides. It
cannot pin any of the three things that only the route can get wrong, which is
what this file is for:

  * the verdict is actually ATTACHED. A perfect engine nobody calls leaves the
    client deriving its own answer, which is the defect being repaired;
  * it is computed from the DATABASE ROW, not from the serialized payload. The
    serializer coerces `quantity` and defaults a blank price, and readiness has
    to see the NULL that separates "no stock tracked" from "none left". Passing
    it the payload instead would compile, pass every engine test, and silently
    reintroduce the exact bug -- so the NULL is asserted end to end, through
    Flask, against a row written as NULL;
  * it does NOT reach a buyer. `pulse_marketplace_listing_payload` also feeds
    the public listing page and `/api/pulse/marketplace/search`, so attaching
    readiness inside the serializer would have published every merchant's
    unfinished listings to strangers. It is attached in the seller route
    instead, and that separation is asserted rather than trusted.
"""

import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="readiness_route_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

import bot  # noqa: E402

from services.business_os.marketplace import listing_readiness as readiness  # noqa: E402

SELLER = 94501
BUYER = 94502
NOW = "2026-09-01T00:00:00"


class _ReadinessRouteBase(unittest.TestCase):
    """Fixtures only. Carries no test of its own, so it collects as nothing and
    the supplier class below inherits the store without re-running the store's
    assertions."""

    @classmethod
    def setUpClass(cls):
        cls.db_path = _DB_PATH
        bot.init_db()
        cls._real_account_user = bot.api_account_user
        bot.webhook_app.config["TESTING"] = True
        cls.client = bot.webhook_app.test_client()

    @classmethod
    def tearDownClass(cls):
        bot.api_account_user = cls._real_account_user

    def setUp(self):
        bot.api_account_user = lambda *a, **k: {
            "user_id": SELLER, "username": "readiness_seller",
            "email": "readiness_seller@example.com"}
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("DELETE FROM marketplace_listings WHERE seller_user_id IN (?,?)", (SELLER, BUYER))
        cur.execute("DELETE FROM marketplace_sellers WHERE user_id IN (?,?)", (SELLER, BUYER))
        for user_id, username in ((SELLER, "readiness_seller"), (BUYER, "readiness_buyer")):
            cur.execute("INSERT OR IGNORE INTO users (user_id, username, display_name) VALUES (?,?,?)",
                        (user_id, username, username))
        cur.execute(
            "INSERT INTO marketplace_sellers (user_id, display_name, status, created_at, updated_at)"
            " VALUES (?,?,?,?,?)", (SELLER, "Readiness Seller", "approved", NOW, NOW))
        conn.commit()
        conn.close()

    # -- helpers --------------------------------------------------------------

    def insert_listing(self, **overrides):
        """A listing written straight to the table, so a test can write a NULL.

        Deliberately not via the create route: the route validates, and the
        states this file cares about (a NULL quantity, a blank price) are states
        the route would refuse to create but the table already holds -- rows
        predating the validation, and rows written by the supplier importer.
        """
        row = {
            "seller_user_id": SELLER,
            "title": "Brass desk lamp",
            "description": "A well described listing.",
            "category": "Home",
            "price_label": "$24.00",
            "cover_image_url": "https://cdn.example/lamp.jpg",
            "status": "active",
            "approval_status": "approved",
            "listing_type": "physical",
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

    def seller_item(self, listing_id):
        response = self.client.get("/api/pulse/marketplace/seller/listings")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        items = response.get_json()["items"]
        item = next((i for i in items if int(i.get("id") or 0) == listing_id), None)
        self.assertIsNotNone(item, f"listing {listing_id} missing from the seller's own store")
        return item


class SellerListingReadinessRouteTestCase(_ReadinessRouteBase):
    # -- the verdict is on the wire -------------------------------------------

    def test_a_ready_listing_carries_the_verdict(self):
        # `resubmittable` answers a rejection, so a healthy listing reports False.
        # It is asserted here rather than ignored because this is an exact-dict
        # comparison on purpose: the route must hand the client the whole verdict
        # the evaluator produced, not a subset it chose.
        item = self.seller_item(self.insert_listing())
        self.assertEqual(item["readiness"], {
            "publishable": True, "resubmittable": False, "checkout_ready": True,
            "blockers": [], "warnings": [],
            "summary": "Ready to publish", "fixes": [], "notes": []})

    def test_the_verdict_names_the_gap_the_row_renders_as_silence(self):
        """§12: a listing with no price must say so.

        The row renders a blank `price_label` as nothing at all, which is safe
        from "Free" but tells the merchant nothing. The verdict is where the gap
        gets a name.
        """
        item = self.seller_item(self.insert_listing(price_label=""))
        self.assertEqual(item["readiness"]["blockers"], [readiness.MISSING_PRICE])
        self.assertFalse(item["readiness"]["publishable"])
        # And the listing still says nothing misleading about money.
        self.assertNotIn("$0.00", repr(item.get("price_label")))
        self.assertNotIn("Free", repr(item.get("price_label")))

    # -- the NULL survives the round trip -------------------------------------

    def test_an_untracked_quantity_reaches_the_verdict_as_unknown(self):
        """The regression, asserted through Flask against a real NULL.

        This is the test that fails if the route ever computes readiness from
        the serialized payload instead of the row, or if any layer in between
        reintroduces `x or 0`. The engine's own tests cannot see that mistake:
        they hand `evaluate` a dict directly.
        """
        item = self.seller_item(self.insert_listing(quantity=None))
        self.assertEqual(item["readiness"]["warnings"], [readiness.UNKNOWN_INVENTORY])
        # Unknown does not stop the merchant listing, but it does stop a promise
        # to a buyer.
        self.assertTrue(item["readiness"]["publishable"])
        self.assertFalse(item["readiness"]["checkout_ready"])

    def test_unknown_and_sold_out_stay_different_answers_on_the_wire(self):
        unknown = self.seller_item(self.insert_listing(quantity=None))
        sold_out = self.seller_item(self.insert_listing(quantity=0))
        self.assertEqual(unknown["readiness"]["warnings"], [readiness.UNKNOWN_INVENTORY])
        self.assertEqual(sold_out["readiness"]["warnings"], [readiness.OUT_OF_STOCK])
        self.assertNotEqual(unknown["readiness"]["warnings"], sold_out["readiness"]["warnings"])

    def test_the_row_really_did_hold_a_null(self):
        """Guards the test above from passing for the wrong reason.

        If the column ever gained a NOT NULL default, `quantity=None` would be
        stored as 0 and the unknown test would be asserting against a zero that
        the engine happens to call unknown for some other reason.
        """
        listing_id = self.insert_listing(quantity=None)
        conn = sqlite3.connect(self.db_path)
        stored = conn.execute("SELECT quantity FROM marketplace_listings WHERE id=?",
                              (listing_id,)).fetchone()[0]
        conn.close()
        self.assertIsNone(stored, "the column no longer stores NULL; the unknown/empty "
                                  "distinction has nowhere to live")

    # -- and it stays the merchant's business ---------------------------------

    def test_a_buyer_facing_listing_carries_no_verdict(self):
        """§64 in spirit: readiness is merchant-internal.

        The public search endpoint shares the serializer with the seller route.
        A verdict there would tell every shopper which sellers have unpriced
        drafts and empty shelves.
        """
        self.insert_listing(title="Brass desk lamp public", quantity=0, price_label="")
        response = self.client.get("/api/pulse/marketplace/search")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        items = response.get_json().get("items") or []
        for item in items:
            self.assertNotIn("readiness", item,
                             "the public search endpoint is publishing merchant readiness")
            # And the same for the field added beside it. `bulk_eligibility`
            # carries strings like "2 things left" and "Already in review",
            # which describe the merchant's backlog rather than the product.
            self.assertNotIn("bulk_eligibility", item,
                             "the public search endpoint is publishing bulk eligibility")

    def test_the_verdict_carries_no_money_or_supplier_facts(self):
        item = self.seller_item(self.insert_listing(quantity=0, price_label=""))
        verdict = item["readiness"]
        # Deliberately an exact set, not a subset: this is the guard that a money
        # or supplier fact cannot appear on a seller-facing payload, and a subset
        # check would let a new key through silently. Adding a key here is meant
        # to be a decision, which is why `resubmittable` had to be added by hand.
        self.assertEqual(set(verdict), {"publishable", "resubmittable", "checkout_ready",
                                        "blockers", "warnings", "summary", "fixes", "notes"})
        flat = repr(verdict).lower()
        for word in ("cost", "margin", "supplier", "token", "openid", "connection", "cents"):
            self.assertNotIn(word, flat, f"{word!r} has no business in a readiness verdict")

    # -- every row gets one ---------------------------------------------------

    def test_every_listing_in_the_store_carries_a_verdict(self):
        """A client that must ask "did this one get a verdict?" will grow a
        fallback, and the fallback is the client-side derivation being retired."""
        for overrides in ({}, {"quantity": None}, {"quantity": 0}, {"price_label": ""},
                          {"title": ""}, {"listing_type": "digital", "quantity": None}):
            self.insert_listing(**overrides)
        response = self.client.get("/api/pulse/marketplace/seller/listings")
        items = response.get_json()["items"]
        self.assertGreaterEqual(len(items), 6)
        for item in items:
            self.assertIn("readiness", item, f"listing {item.get('id')} has no verdict")
            self.assertEqual(set(item["readiness"]),
                             {"publishable", "resubmittable", "checkout_ready",
                              "blockers", "warnings", "summary", "fixes", "notes"})

    # -- what a bulk action would do, decided here rather than on the phone ----

    def test_every_listing_says_what_a_bulk_action_would_do_to_it(self):
        """§34's preview is drawn from this field, so a row without one leaves
        the client to re-derive it -- which is the whole defect."""
        for overrides in ({}, {"price_label": ""}, {"status": "draft",
                                                    "approval_status": "draft"}):
            self.insert_listing(**overrides)
        items = self.client.get("/api/pulse/marketplace/seller/listings").get_json()["items"]
        for item in items:
            self.assertIn("bulk_eligibility", item)
            self.assertEqual(set(item["bulk_eligibility"]), {"publish", "hide"})

    def test_a_finished_live_listing_is_publishable_and_still_blocked(self):
        """The case a client-side derivation cannot see, and the reason this
        field exists at all.

        An approved, active listing passes every readiness check -- it is a
        finished product. Republishing it would push it back into the review
        queue and take the storefront dark until a moderator cleared it. Only
        `listing_batch.block_reason` knows that, because only it owns the state
        gate; readiness is about the listing's contents and says nothing about
        where it already is.
        """
        item = self.seller_item(self.insert_listing())
        self.assertTrue(item["readiness"]["publishable"])
        block = item["bulk_eligibility"]["publish"]
        self.assertIsNotNone(block, "select-all + Publish would unpublish this seller's store")
        self.assertEqual(block["code"], "ALREADY_PUBLISHED")
        # Hiding it, on the other hand, is exactly what a seller might want.
        self.assertIsNone(item["bulk_eligibility"]["hide"])

    def test_the_preview_and_the_batch_give_the_same_answer(self):
        """One authority, asked twice.

        Not "two implementations that agree" -- the assertion is that the field
        on the row is literally what the batch endpoint computes, so there is no
        second answer that could drift.
        """
        listing_id = self.insert_listing(price_label="", status="draft",
                                         approval_status="draft")
        preview = self.seller_item(listing_id)["bulk_eligibility"]["publish"]
        self.assertIsNotNone(preview)
        self.assertEqual(preview["reason"], "1 thing left")

        response = self.client.post(
            "/api/pulse/marketplace/seller/listings/batch",
            json={"action": "publish", "listing_ids": [listing_id],
                  "idempotency_key": "preview-vs-outcome"})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        outcome = response.get_json()["results"][0]
        self.assertEqual(outcome["outcome"], "blocked")
        self.assertEqual(outcome["reason"], preview["reason"])
        self.assertEqual(outcome["blockers"], preview["blockers"])

    # -- the SINGLE product path answers with the same verdict ----------------
    #
    # §32's journey is Store -> Edit -> Ready to Sell -> fix -> Publish, and it
    # runs entirely on responses from the routes below. They used to hand back a
    # listing with no verdict on it at all, while the list route beside them
    # attached one, so the same product gave two different answers depending on
    # which route the phone had asked last. The editor merges an update response
    # over the row it is holding, which means the missing key did not clear the
    # stale verdict -- it preserved it. That is the shape of the bug these pin.

    def edit_response(self, listing_id, **fields):
        response = self.client.patch(
            f"/api/pulse/marketplace/seller/listings/{listing_id}", json=fields)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.get_json()["listing"]

    def test_saving_an_edit_answers_with_the_verdict(self):
        listing_id = self.insert_listing(price_label="", status="draft",
                                         approval_status="draft")
        item = self.edit_response(listing_id, title="Brass desk lamp, small")
        self.assertIn("readiness", item, "the editor has nothing to draw Ready to Sell from")
        self.assertEqual(item["readiness"]["blockers"], [readiness.MISSING_PRICE])

    def test_fixing_the_price_clears_the_blocker_in_the_same_response(self):
        """The §31 read-back, on the one surface where it is easy to fake.

        Adding a price and being told to add a price is what the seller saw
        before: the update response carried no verdict, so the phone kept the one
        it fetched with the list. The fix has to be visible in the answer to the
        request that made it, not on the next full reload.
        """
        listing_id = self.insert_listing(price_label="", status="draft",
                                         approval_status="draft")
        before = self.edit_response(listing_id, title="Brass desk lamp")
        self.assertEqual(before["readiness"]["blockers"], [readiness.MISSING_PRICE])

        after = self.edit_response(listing_id, price_label="$24.00")
        self.assertEqual(after["readiness"]["blockers"], [])
        self.assertTrue(after["readiness"]["publishable"])
        self.assertEqual(after["readiness"]["summary"], "Ready to publish")

    def test_the_editor_and_the_list_cannot_disagree(self):
        """One authority, asked twice -- the single-product half of it.

        The list route and the update route are different functions with
        different SQL, and both now go through one serializer. Asserting the
        whole verdict is equal, rather than each field, is what would catch a
        second attachment point growing beside the first.
        """
        listing_id = self.insert_listing(quantity=None, price_label="",
                                         status="draft", approval_status="draft")
        from_list = self.seller_item(listing_id)
        from_edit = self.edit_response(listing_id, title=from_list["title"])
        self.assertEqual(from_edit["readiness"], from_list["readiness"])
        self.assertEqual(from_edit["bulk_eligibility"], from_list["bulk_eligibility"])

    def test_hiding_a_listing_answers_with_the_verdict_too(self):
        """Pause and resume return a listing, so they return a verdict.

        Not for completeness: the row the seller lands back on is rendered from
        this response, and a row whose verdict went missing renders as one with
        nothing left to do.
        """
        listing_id = self.insert_listing(price_label="")
        for action in ("pause", "resume"):
            response = self.client.post(
                f"/api/pulse/marketplace/seller/listings/{listing_id}/{action}")
            self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
            item = response.get_json()["listing"]
            self.assertIn("readiness", item, f"{action} dropped the verdict")
            self.assertEqual(item["readiness"]["blockers"], [readiness.MISSING_PRICE])
            self.assertIn("bulk_eligibility", item, f"{action} dropped bulk eligibility")

    def test_the_single_path_verdict_still_reaches_nobody_else(self):
        """The attachment moved into a shared serializer, so §27 gets re-asserted.

        `pulse_marketplace_seller_listing_payload` wraps the same function the
        public listing page and search use. If the wrapping had gone the other
        way round -- verdicts inside the public serializer, stripped on the way
        out -- this would be how it showed up.
        """
        listing_id = self.insert_listing(price_label="", quantity=0)
        self.edit_response(listing_id, title="Brass desk lamp public two")
        response = self.client.get("/api/pulse/marketplace/search")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        for item in response.get_json().get("items") or []:
            self.assertNotIn("readiness", item)
            self.assertNotIn("bulk_eligibility", item)


if __name__ == "__main__":
    unittest.main()


class SupplierListingReadinessRouteTestCase(_ReadinessRouteBase):
    """The supplier half, on the wire.

    The engine test proves `evaluate(..., supplier=facts)` reads variant prices.
    It cannot prove the route ever LOADS those facts, and a route that passes
    `supplier=None` compiles, passes every engine test, and leaves all 31
    production drafts saying "Price required" -- which is the state this whole
    branch exists to leave. So the assertion here is made against a row written
    exactly as the CJ importer writes it: no `price_label`, no `quantity`, and
    the money in `marketplace_listing_variants`.
    """

    def insert_supplier_listing(self, *, variants, provider_variant_id=None,
                                sync_state="SYNCED", fulfillment_mode="DROPSHIP",
                                **overrides):
        overrides.setdefault("price_label", "")
        overrides.setdefault("quantity", None)
        overrides.setdefault(
            "listing_metadata_json",
            '{"media": ["https://cdn.example/lamp.jpg"]}')
        listing_id = self.insert_listing(**overrides)
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO marketplace_product_sources"
            " (listing_id, seller_user_id, provider, provider_product_id,"
            "  provider_variant_id, fulfillment_mode, sync_state, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (listing_id, SELLER, "cj", "pid-1", provider_variant_id,
             fulfillment_mode, sync_state, NOW, NOW))
        for position, variant in enumerate(variants):
            cur.execute(
                "INSERT INTO marketplace_listing_variants"
                " (listing_id, seller_user_id, variant_key, provider_variant_id,"
                "  price_cents, cost_cents, currency, stock_state, stock_quantity,"
                "  position, status, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (listing_id, SELLER, variant["provider_variant_id"],
                 variant["provider_variant_id"], variant.get("price_cents"),
                 variant.get("cost_cents"), "USD", variant.get("stock_state"),
                 variant.get("stock_quantity"), position, "active", NOW, NOW))
        conn.commit()
        conn.close()
        return listing_id

    def test_a_priced_cj_draft_does_not_ask_the_merchant_for_a_price(self):
        listing_id = self.insert_supplier_listing(
            provider_variant_id="vid-1",
            variants=[{"provider_variant_id": "vid-1", "price_cents": 2400,
                       "cost_cents": 900, "stock_state": "IN_STOCK",
                       "stock_quantity": 132}])
        verdict = self.seller_item(listing_id)["readiness"]
        self.assertNotIn(readiness.MISSING_PRICE, verdict["blockers"])
        self.assertEqual(verdict["summary"], "Ready to publish")

    def test_the_same_row_without_the_supplier_facts_still_asks_for_a_price(self):
        """The negative control that makes the test above mean something.

        A listing with an empty `price_label` and no source row is exactly the
        state MISSING_PRICE exists for. If this passed too, the route would be
        ignoring `price_label` for everyone.
        """
        listing_id = self.insert_listing(price_label="", quantity=None)
        verdict = self.seller_item(listing_id)["readiness"]
        self.assertIn(readiness.MISSING_PRICE, verdict["blockers"])

    def test_the_real_faults_reach_the_merchant_in_words(self):
        """Production's dominant shape: unknown stock, unbound, multi-variant.

        Before this the row read "1 thing left - Add price". The price was not
        missing and neither named fault was reachable.
        """
        listing_id = self.insert_supplier_listing(
            provider_variant_id=None,
            variants=[{"provider_variant_id": "vid-1", "price_cents": 2400,
                       "cost_cents": 900, "stock_state": "UNKNOWN",
                       "stock_quantity": None},
                      {"provider_variant_id": "vid-2", "price_cents": 2400,
                       "cost_cents": 900, "stock_state": "UNKNOWN",
                       "stock_quantity": None}])
        verdict = self.seller_item(listing_id)["readiness"]
        self.assertNotIn(readiness.MISSING_PRICE, verdict["blockers"])
        self.assertIn(readiness.UNKNOWN_INVENTORY, verdict["blockers"])
        self.assertIn(readiness.SUPPLIER_VARIANT_UNBOUND, verdict["blockers"])
        labels = {entry["label"] for entry in verdict["fixes"]}
        self.assertNotIn("Review this listing", labels)
        self.assertNotIn("Add price", labels)

    def test_the_submit_gate_and_the_store_row_agree_about_a_supplier_draft(self):
        """The divergence, closed at both ends.

        The submit route refuses on `listing_readiness`; `drafts._validate`
        refuses the publish. Production's state was the Store row reporting a
        price fault while those two refused a stock fault and a binding fault --
        so the merchant could not clear their own store by following it. A draft
        the row calls ready must be a draft submit accepts.
        """
        ready = self.insert_supplier_listing(
            provider_variant_id="vid-1", status="draft",
            variants=[{"provider_variant_id": "vid-1", "price_cents": 2400,
                       "cost_cents": 900, "stock_state": "IN_STOCK",
                       "stock_quantity": 132}])
        self.assertEqual(self.seller_item(ready)["readiness"]["summary"],
                         "Ready to publish")
        accepted = self.client.post(
            f"/api/pulse/marketplace/seller/listings/{ready}/submit")
        self.assertEqual(accepted.status_code, 200,
                         accepted.get_data(as_text=True))

        # The negative control: the row that is genuinely not ready is refused,
        # and refused by name rather than by "Add price".
        blocked = self.insert_supplier_listing(
            provider_variant_id=None, status="draft",
            variants=[{"provider_variant_id": "vid-1", "price_cents": 2400,
                       "cost_cents": 900, "stock_state": "UNKNOWN",
                       "stock_quantity": None},
                      {"provider_variant_id": "vid-2", "price_cents": 2400,
                       "cost_cents": 900, "stock_state": "UNKNOWN",
                       "stock_quantity": None}])
        refused = self.client.post(
            f"/api/pulse/marketplace/seller/listings/{blocked}/submit")
        self.assertEqual(refused.status_code, 409, refused.get_data(as_text=True))
        body = refused.get_json()
        codes = {entry["code"] for entry in (body.get("readiness") or {}).get("fixes", [])}
        self.assertIn(readiness.SUPPLIER_VARIANT_UNBOUND, codes)
        self.assertNotIn(readiness.MISSING_PRICE, codes)

    def test_the_bulk_preview_and_the_row_agree_about_a_supplier_draft(self):
        """`evaluate_rows` decides the batch. Loading supplier facts in the list
        route and not in the batch route would have "Publish 1" report the row
        blocked while the screen offered it."""
        listing_id = self.insert_supplier_listing(
            provider_variant_id="vid-1", status="draft",
            variants=[{"provider_variant_id": "vid-1", "price_cents": 2400,
                       "cost_cents": 900, "stock_state": "IN_STOCK",
                       "stock_quantity": 132}])
        response = self.client.post(
            "/api/pulse/marketplace/seller/listings/batch",
            json={"action": "publish", "listing_ids": [listing_id],
                  "mode": "preview", "idempotency_key": "supplier-preview-1"})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        body = response.get_json()
        blocked = [item for item in (body.get("items") or [])
                   if (item.get("block") or {}).get("code")]
        self.assertEqual(
            blocked, [],
            f"the batch route blocked a draft the store row calls ready: {blocked}")
