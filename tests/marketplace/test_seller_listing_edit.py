"""Seller product editing: PATCH semantics, price authority, ownership.

The failure this suite exists to catch is silent data loss on save. The update
route used to read every field as ``payload.get(name) or <default>``, so a
client that sent five keys did not "leave the rest alone" — it reset them. The
worst case was money: an omitted ``price_label`` became the literal string
"Request access", which ``parse_price_label_to_cents`` maps to 0 cents, so a
seller editing their inventory count could silently make the product free.

Every price assertion below goes through ``parse_price_label_to_cents`` rather
than comparing the label text, because that function is what checkout actually
calls. A label that looks right but parses wrong is the bug, not the fix.

Runs against a temp sqlite file so nothing touches coinpilotx.db.
"""

import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="marketplace_listing_edit_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

import bot  # noqa: E402


def _use_module_database():
    os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
    bot.INIT_DB_COMPLETED = False
    bot.PULSE_MESSENGER_SCHEMA_READY = False
    bot.init_db()


class PriceLabelParsingTest(unittest.TestCase):
    """The parser is the bridge between the seller's label and the charge."""

    def test_thousands_separator_is_not_truncated(self):
        # "$2,500.00" used to parse as 200 cents: the digit run stopped at the
        # comma. Buyers saw the full label and would have been charged $2.00.
        self.assertEqual(bot.parse_price_label_to_cents("$2,500.00"), (250000, "USD"))
        self.assertEqual(bot.parse_price_label_to_cents("$1,234.56"), (123456, "USD"))

    def test_plain_and_currency_prefixed_labels(self):
        self.assertEqual(bot.parse_price_label_to_cents("$19.99"), (1999, "USD"))
        self.assertEqual(bot.parse_price_label_to_cents("USD 40.00"), (4000, "USD"))

    def test_unpriced_labels_are_zero(self):
        for label in ["", "Free", "Request access", "paid later"]:
            self.assertEqual(bot.parse_price_label_to_cents(label)[0], 0, label)

    def test_negative_label_never_becomes_a_positive_charge(self):
        self.assertEqual(bot.parse_price_label_to_cents("-5")[0], 0)

    def test_normalized_label_round_trips_through_the_checkout_parser(self):
        for raw in ["19.9", "2500", "$2,500.00", "1234.5"]:
            label, cents, currency, error = bot.marketplace_normalize_price_label(raw)
            self.assertEqual(error, "", raw)
            self.assertEqual(
                bot.parse_price_label_to_cents(label, currency)[0], cents,
                f"{raw!r} normalized to {label!r} which does not re-parse to {cents}",
            )

    def test_normalizer_rejects_junk_and_negatives(self):
        self.assertTrue(bot.marketplace_normalize_price_label("-5")[3])
        self.assertTrue(bot.marketplace_normalize_price_label("abc")[3])
        self.assertTrue(bot.marketplace_normalize_price_label("0")[3])

    def test_a_blank_price_stays_blank_instead_of_becoming_a_phrase(self):
        """No price is a state. It must not be answered with words.

        This returned "Request access" for an empty label. That is a phrase a
        seller may well pick, which is exactly the problem: picking it for them
        and then storing it is indistinguishable, afterwards, from their having
        picked it. A dropship import writes a blank price deliberately -- the
        supplier cost is the seller's own margin and does not belong on their
        storefront -- so the substitution turned "not priced yet" into a public
        pricing decision the seller never made.

        Unpriced words the seller *did* type are preserved as typed, because
        those are a decision.
        """
        for blank in ["", "   ", None]:
            label, cents, currency, error = bot.marketplace_normalize_price_label(blank)
            self.assertEqual(label, "", repr(blank))
            self.assertEqual(error, "", repr(blank))
            self.assertEqual(cents, 0, repr(blank))

        for chosen in ["Free", "Request access", "paid later"]:
            self.assertEqual(bot.marketplace_normalize_price_label(chosen)[0], chosen)

    def test_a_blank_price_promises_no_money_exactly_as_before(self):
        """The safety property the old substitution was riding on is unchanged."""
        for label in ["", "Request access"]:
            self.assertEqual(bot.parse_price_label_to_cents(label)[0], 0, label)

    def test_the_read_serializer_does_not_price_an_unpriced_listing(self):
        """Every marketplace read goes through here, so this is where it counts.

        Fixing the writers and the clients was not enough and the gap was
        visible on a real device: the app, the product screen and the Page block
        each answer a blank price with their own copy, but this function handed
        them "Request access" first, so their fallbacks were unreachable and a
        dropship draft -- blank by design -- still arrived in the seller's own
        store carrying a price they had never set. Four endpoints share this
        serializer, buyer and seller, list and detail, which is why a phrase
        invented here cannot be told apart downstream from one the seller typed.

        The whole earlier fix passed its tests while the bug stayed on screen,
        because nothing asserted the shape of the payload itself. That is the
        hole this closes.
        """
        served = bot.pulse_marketplace_listing_payload(
            {"id": 7, "seller_user_id": 1, "title": "Beaded bracelet", "price_label": ""})
        self.assertEqual(served["price_label"], "")
        # The point of keeping it empty: a client fallback can now run at all.
        self.assertEqual(served["price_label"] or "Price at checkout", "Price at checkout")

        missing = bot.pulse_marketplace_listing_payload(
            {"id": 7, "seller_user_id": 1, "title": "Beaded bracelet"})
        self.assertEqual(missing["price_label"], "")

    def test_the_read_serializer_still_reports_a_price_the_seller_chose(self):
        """Including an unpriced phrase, when it is the seller's own words."""
        for chosen in ["$18.50", "Free", "Request access"]:
            served = bot.pulse_marketplace_listing_payload(
                {"id": 7, "seller_user_id": 1, "title": "x", "price_label": chosen})
            self.assertEqual(served["price_label"], chosen)


class SellerListingEditTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _use_module_database()
        bot.webhook_app.config["TESTING"] = True
        cls.client = bot.webhook_app.test_client()

    def setUp(self):
        _use_module_database()
        self.now = datetime.utcnow().isoformat(timespec="seconds")
        self._real_api_account_user = bot.api_account_user
        self._real_require_account = bot.require_account
        self._real_emit = bot.pulse_emit_event
        bot.pulse_emit_event = lambda *a, **k: None
        self.owner = self._make_seller("owner")
        self.other = self._make_seller("other")
        self.listing_id = self._make_listing(self.owner)

    def tearDown(self):
        bot.api_account_user = self._real_api_account_user
        bot.require_account = self._real_require_account
        bot.pulse_emit_event = self._real_emit

    # ------------------------------------------------------------------
    # fixtures
    # ------------------------------------------------------------------
    def _make_seller(self, role):
        conn = bot.db()
        cur = conn.cursor()
        username = f"mkedit_{role}"
        cur.execute(
            "INSERT INTO users (username, display_name, email, account_status, created_at) VALUES (?,?,?,?,?)",
            (username, f"Edit {role}", f"{username}@example.com", "active", self.now),
        )
        user_id = int(cur.lastrowid)
        cur.execute(
            "INSERT INTO marketplace_sellers (user_id, business_name, display_name, status, created_at, updated_at) VALUES (?,?,?,?,?,?)",
            (user_id, f"{role} store", f"{role} store", "approved", self.now, self.now),
        )
        conn.commit()
        conn.close()
        return {"user_id": user_id, "username": username}

    def _make_listing(self, seller, **overrides):
        row = {
            "title": "Handmade lamp",
            "short_description": "Warm brass lamp",
            "description": "A brass desk lamp finished by hand.",
            "category": "Home",
            "subcategory": "Lighting",
            "price_label": "$40.00",
            "currency": "USD",
            "quantity": 5,
            "product_type": "physical",
            "listing_type": "physical",
            "status": "published",
            "approval_status": "approved",
            "refund_policy": "Returns within 30 days.",
            "estimated_delivery": "3-5 days",
            "seller_notes": "Ships flat-packed.",
            "tags_json": '["brass", "lamp"]',
            "cover_image_url": "https://cdn.example.com/lamp.jpg",
        }
        row.update(overrides)
        columns = ", ".join(row)
        placeholders = ", ".join(["?"] * len(row))
        conn = bot.db()
        cur = conn.cursor()
        cur.execute(
            f"INSERT INTO marketplace_listings (seller_user_id, {columns}, created_at, updated_at) "
            f"VALUES (?, {placeholders}, ?, ?)",
            (int(seller["user_id"]), *row.values(), self.now, self.now),
        )
        listing_id = int(cur.lastrowid)
        conn.commit()
        conn.close()
        return listing_id

    @contextmanager
    def acting_as(self, user):
        previous_api, previous_require = bot.api_account_user, bot.require_account
        bot.api_account_user = lambda: dict(user)
        bot.require_account = lambda: dict(user)
        try:
            yield
        finally:
            bot.api_account_user, bot.require_account = previous_api, previous_require

    def patch_listing(self, user, payload, listing_id=None):
        with self.acting_as(user):
            return self.client.patch(
                f"/api/pulse/marketplace/seller/listings/{listing_id or self.listing_id}",
                json=payload,
            )

    def test_creating_a_listing_without_a_price_does_not_invent_one(self):
        """Submitting no price is not the same as choosing words for one.

        The create route filled a missing `price_label` with "Request access"
        before storing it, so a seller who left the field alone -- or any client
        that omits it, which is how a dropship draft is built -- had a pricing
        decision written into their listing under their name. Every later read
        then presented it as theirs, because by then it genuinely was in their
        row.

        This is the third site in the same family, after the update route and
        the read serializer, and it was the one still standing once the other
        two were closed.
        """
        with self.acting_as(self.owner):
            response = self.client.post(
                "/api/pulse/marketplace/listings/create",
                json={"title": "Unpriced lamp", "description": "No price yet.",
                      "category": "Home", "product_type": "physical",
                      "submission_action": "draft"},
            )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        created = int((response.get_json() or {}).get("listing_id") or 0)
        self.assertTrue(created, "no listing id came back")
        self.assertEqual(self.stored(created).get("price_label"), "")

    def test_creating_a_listing_with_a_price_keeps_exactly_that_price(self):
        """The other half: a price that was submitted is stored as submitted."""
        with self.acting_as(self.owner):
            response = self.client.post(
                "/api/pulse/marketplace/listings/create",
                json={"title": "Priced lamp", "description": "Has a price.",
                      "category": "Home", "product_type": "physical",
                      "price_label": "$40.00", "submission_action": "draft"},
            )
        created = int((response.get_json() or {}).get("listing_id") or 0)
        self.assertEqual(self.stored(created).get("price_label"), "$40.00")

    def stored(self, listing_id=None):
        conn = bot.db()
        conn.row_factory = bot.sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM marketplace_listings WHERE id=?", (listing_id or self.listing_id,))
        row = dict(cur.fetchone() or {})
        conn.close()
        return row

    def price_cents(self, listing_id=None):
        row = self.stored(listing_id)
        return bot.parse_price_label_to_cents(row.get("price_label") or "", row.get("currency") or "USD")[0]

    # ------------------------------------------------------------------
    # editing the fields a seller actually changes
    # ------------------------------------------------------------------
    def test_owner_can_edit_title_description_and_price(self):
        resp = self.patch_listing(self.owner, {
            "title": "Handmade brass lamp",
            "description": "Hand-finished brass desk lamp with a dimmer.",
            "price_label": "52.50",
        })
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True)[:400])
        row = self.stored()
        self.assertEqual(row["title"], "Handmade brass lamp")
        self.assertEqual(row["description"], "Hand-finished brass desk lamp with a dimmer.")
        self.assertEqual(self.price_cents(), 5250)

    def test_price_change_persists_and_is_what_checkout_would_charge(self):
        self.patch_listing(self.owner, {"price_label": "$2,500.00"})
        # The value checkout resolves, not the label text, is the assertion.
        self.assertEqual(self.price_cents(), 250000)

    def test_price_can_be_changed_twice_without_recreating_the_listing(self):
        self.patch_listing(self.owner, {"price_label": "60"})
        self.assertEqual(self.price_cents(), 6000)
        self.patch_listing(self.owner, {"price_label": "12.34"})
        self.assertEqual(self.price_cents(), 1234)

    def test_invalid_prices_are_rejected_and_leave_the_old_price_intact(self):
        for bad in ["-5", "abc", "0"]:
            resp = self.patch_listing(self.owner, {"price_label": bad})
            self.assertEqual(resp.status_code, 400, bad)
            self.assertEqual(self.price_cents(), 4000, f"{bad} must not disturb the stored price")

    def test_inventory_updates_and_rejects_negative_values(self):
        self.assertEqual(self.patch_listing(self.owner, {"quantity": 9}).status_code, 200)
        self.assertEqual(self.stored()["quantity"], 9)
        self.assertEqual(self.patch_listing(self.owner, {"quantity": 0}).status_code, 200)
        self.assertEqual(self.stored()["quantity"], 0)

        resp = self.patch_listing(self.owner, {"quantity": -3})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self.stored()["quantity"], 0, "a rejected edit must not write anything")

    def test_inventory_cannot_drop_below_units_held_in_checkout(self):
        conn = bot.db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO marketplace_inventory_reservations (seller_transaction_id, buyer_user_id, listing_id, quantity, status, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (901, int(self.other["user_id"]), self.listing_id, 2, "held", self.now, self.now),
        )
        conn.commit()
        conn.close()

        resp = self.patch_listing(self.owner, {"quantity": 1})
        self.assertEqual(resp.status_code, 409, resp.get_data(as_text=True)[:300])
        self.assertEqual(self.stored()["quantity"], 5)
        self.assertEqual(self.patch_listing(self.owner, {"quantity": 2}).status_code, 200)

    def test_text_fields_are_individually_editable(self):
        self.patch_listing(self.owner, {
            "category": "Lighting", "subcategory": "Desk lamps",
            "tags_json": "ignored", "tags": "brass, desk, warm",
            "refund_policy": "Returns within 14 days.",
            "estimated_delivery": "2 days", "seller_notes": "Made to order.",
        })
        row = self.stored()
        self.assertEqual(row["category"], "Lighting")
        self.assertEqual(row["subcategory"], "Desk lamps")
        self.assertEqual(row["refund_policy"], "Returns within 14 days.")
        self.assertEqual(row["estimated_delivery"], "2 days")
        self.assertEqual(row["seller_notes"], "Made to order.")
        self.assertIn("brass", row["tags_json"])
        self.assertIn("desk", row["tags_json"])

    # ------------------------------------------------------------------
    # PATCH semantics — the data-loss regression
    # ------------------------------------------------------------------
    def test_omitted_fields_are_preserved(self):
        before = self.stored()
        resp = self.patch_listing(self.owner, {"quantity": 4})
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True)[:400])
        after = self.stored()
        for field in [
            "title", "short_description", "description", "category", "subcategory",
            "price_label", "currency", "refund_policy", "estimated_delivery",
            "seller_notes", "tags_json", "cover_image_url",
        ]:
            self.assertEqual(after[field], before[field], f"{field} was clobbered by a partial save")
        self.assertEqual(after["quantity"], 4)

    def test_editing_inventory_alone_cannot_make_the_product_free(self):
        # The exact production failure: no price_label in the payload used to
        # mean price_label = "Request access" = 0 cents = unpurchasable.
        self.patch_listing(self.owner, {"quantity": 2})
        self.assertEqual(self.price_cents(), 4000)

    def test_quick_edit_payload_does_not_wipe_the_short_description(self):
        self.patch_listing(self.owner, {
            "title": "Handmade lamp", "description": "A brass desk lamp finished by hand.",
            "category": "Home", "price_label": "$40.00", "quantity": 5,
        })
        self.assertEqual(self.stored()["short_description"], "Warm brass lamp")

    def test_an_explicitly_empty_field_is_still_cleared(self):
        self.patch_listing(self.owner, {"seller_notes": ""})
        self.assertEqual(self.stored()["seller_notes"], "")

    # ------------------------------------------------------------------
    # the imported draft, which arrives with no price on purpose
    # ------------------------------------------------------------------
    def _dropship_draft(self):
        """What `suppliers/importer.py::_create_draft_listing` actually writes.

        Blank `price_label` is that function's documented choice: the seller has
        not set a price, and seeding it with the supplier's cost would print
        their own margin on their storefront.
        """
        return self._make_listing(self.owner, price_label="", status="draft",
                                  approval_status="pending_review", quantity=0,
                                  title="Colored Glaze Neutral Red Beaded Bracelet")

    def test_editing_an_imported_draft_does_not_price_it_for_the_seller(self):
        """The importer's blank price survived until the first edit of any kind.

        Not a price edit -- any edit. The route filled an absent `price_label`
        with "Request access", so fixing a typo in the title published a pricing
        decision the seller never made, on a product they had not priced yet.
        """
        draft = self._dropship_draft()
        resp = self.patch_listing(self.owner, {"title": "Red beaded bracelet"}, listing_id=draft)
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True)[:400])
        self.assertEqual(self.stored(draft)["price_label"], "")

    def test_sending_a_blank_price_explicitly_also_leaves_it_blank(self):
        """The client sends "" for an untouched price field. It must land as "".

        Otherwise the fix only moves the substitution from the app to the route.
        """
        draft = self._dropship_draft()
        resp = self.patch_listing(self.owner, {"title": "Red bracelet", "price_label": ""},
                                  listing_id=draft)
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True)[:400])
        self.assertEqual(self.stored(draft)["price_label"], "")

    def test_an_unpriced_draft_can_still_be_given_a_real_price(self):
        """Blank is a starting state, not a trap."""
        draft = self._dropship_draft()
        self.patch_listing(self.owner, {"price_label": "$18.50"}, listing_id=draft)
        self.assertEqual(self.price_cents(draft), 1850)

    def test_a_title_edit_on_an_unpriced_draft_is_not_recorded_as_a_price_change(self):
        """The substitution made the route disagree with itself about the diff.

        `changed_fields` compares stored against next, so "" becoming "Request
        access" registered `price_label` as edited on a save that never
        mentioned it -- and a price change is a material change, which sends the
        listing back through review. The seller was penalised for the route's
        own rewrite.
        """
        draft = self._dropship_draft()
        before = self.stored(draft)
        self.patch_listing(self.owner, {"title": "Red beaded bracelet"}, listing_id=draft)
        after = self.stored(draft)
        self.assertEqual(after["price_label"], before["price_label"])
        self.assertNotEqual(after["title"], before["title"])

    # ------------------------------------------------------------------
    # ownership
    # ------------------------------------------------------------------
    def test_a_seller_cannot_edit_another_sellers_listing(self):
        resp = self.patch_listing(self.other, {"title": "Stolen", "price_label": "1.00"})
        self.assertIn(resp.status_code, (403, 404), resp.get_data(as_text=True)[:300])
        row = self.stored()
        self.assertEqual(row["title"], "Handmade lamp")
        self.assertEqual(self.price_cents(), 4000)

    def test_seller_id_in_the_body_cannot_redirect_the_write(self):
        resp = self.patch_listing(self.other, {"seller_user_id": self.owner["user_id"], "price_label": "1.00"})
        self.assertIn(resp.status_code, (403, 404))
        self.assertEqual(self.price_cents(), 4000)

    def test_anonymous_requests_are_rejected(self):
        bot.api_account_user = lambda: None
        resp = self.client.patch(f"/api/pulse/marketplace/seller/listings/{self.listing_id}", json={"price_label": "1.00"})
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(self.price_cents(), 4000)

    # ------------------------------------------------------------------
    # historical orders
    # ------------------------------------------------------------------
    def test_editing_the_price_does_not_rewrite_a_placed_order(self):
        conn = bot.db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO seller_transactions (buyer_user_id, seller_user_id, seller_type, item_type, item_id, "
            "amount_cents, currency, platform_fee_cents, seller_net_cents, status, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (int(self.other["user_id"]), int(self.owner["user_id"]), "merchant", "marketplace_product",
             self.listing_id, 4000, "USD", 400, 3600, "paid", self.now, self.now),
        )
        tx_id = int(cur.lastrowid)
        conn.commit()
        conn.close()

        self.patch_listing(self.owner, {"price_label": "$99.00"})
        self.assertEqual(self.price_cents(), 9900)

        conn = bot.db()
        conn.row_factory = bot.sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT amount_cents, platform_fee_cents, seller_net_cents FROM seller_transactions WHERE id=?", (tx_id,))
        order = dict(cur.fetchone())
        conn.close()
        self.assertEqual(order["amount_cents"], 4000, "a settled order must keep its own price snapshot")
        self.assertEqual(order["platform_fee_cents"], 400)
        self.assertEqual(order["seller_net_cents"], 3600)

    # ------------------------------------------------------------------
    # moderation
    # ------------------------------------------------------------------
    def test_a_material_edit_sends_a_live_listing_back_to_review(self):
        resp = self.patch_listing(self.owner, {"price_label": "$77.00"})
        self.assertEqual(resp.status_code, 200)
        row = self.stored()
        self.assertEqual(row["status"], "pending_review")
        self.assertEqual(row["approval_status"], "pending_review")

    def test_an_inventory_only_edit_does_not_trigger_re_review(self):
        self.patch_listing(self.owner, {"quantity": 7})
        row = self.stored()
        self.assertEqual(row["status"], "published")
        self.assertEqual(row["approval_status"], "approved")

    def test_a_seller_cannot_set_status_directly_through_the_edit_route(self):
        held = self._make_listing(self.owner, status="suspended", approval_status="suspended", title="Held item")
        resp = self.patch_listing(self.owner, {"status": "published", "approval_status": "approved"}, listing_id=held)
        self.assertEqual(resp.status_code, 200)
        row = self.stored(held)
        self.assertEqual(row["status"], "suspended", "edit must not be a moderation bypass")
        self.assertEqual(row["approval_status"], "suspended")

    def test_a_deleted_listing_cannot_be_edited(self):
        removed = self._make_listing(self.owner, status="seller_deleted", title="Gone")
        resp = self.patch_listing(self.owner, {"price_label": "5.00"}, listing_id=removed)
        self.assertEqual(resp.status_code, 400)

    # ------------------------------------------------------------------
    # response contract and concurrency
    # ------------------------------------------------------------------
    def test_the_response_carries_the_authoritative_listing(self):
        resp = self.patch_listing(self.owner, {"price_label": "$61.00", "quantity": 3})
        body = resp.get_json()
        self.assertTrue(body.get("ok"))
        listing = body.get("listing") or {}
        self.assertEqual(listing.get("price_label"), "$61.00")
        self.assertEqual(listing.get("quantity"), 3)
        self.assertEqual(int(listing.get("id") or 0), self.listing_id)

    def test_repeated_saves_converge_rather_than_conflict(self):
        first = self.patch_listing(self.owner, {"price_label": "$25.00", "quantity": 2})
        second = self.patch_listing(self.owner, {"price_label": "$25.00", "quantity": 2})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        row = self.stored()
        self.assertEqual(self.price_cents(), 2500)
        self.assertEqual(row["quantity"], 2)


class MarketplaceWebPriceFallbackTest(unittest.TestCase):
    """The web pages a buyer actually loads, not the payload behind them.

    Web answered a blank price first with "Request access" and then with "Price
    at checkout". Both are the same mistake in different words: the seller set
    no price, and the page states one anyway. "Request access" additionally
    describes a gated product with an application flow that does not exist.

    The rule these pin is the serializer's, established once and now applied
    everywhere a price is drawn: an absent price renders as *nothing*. Not a
    phrase, and not an empty pill either -- an empty pill reads as a price the
    seller deliberately set to nothing. The category and safety pills still
    render, so the card never collapses.

    These render the real routes rather than inspecting source text. That is
    deliberate: the JS card builds its own HTML inside a ``%``-formatted script
    block, so anything threaded into it travels by string interpolation, and a
    mistake there is a 500 on the whole marketplace page rather than a wrong
    word. Only rendering catches that -- and removing the fallback changed that
    block's argument count, which is exactly such a mistake.
    """

    # Asserted as literals rather than through a constant. The constant these
    # used to import no longer exists, and re-introducing one so the tests can
    # name it would let a future edit rename the phrase and keep the suite green
    # while the page still says it.
    INVENTED = ("Price at checkout", "Request access", "Price shown at checkout")

    def assertInventsNoPrice(self, text, where):
        for phrase in self.INVENTED:
            self.assertNotIn(phrase, text, f"{where} invented a price: {phrase!r}")

    # ------------------------------------------------------------------
    # fixtures
    #
    # Deliberately its own, rather than subclassing the edit suite. Inheriting
    # from SellerListingEditTest re-runs all 27 of its tests under a second
    # name, which turns "5 web tests" into "32 passed" and hides whether these
    # five actually ran. The parent's helpers are interleaved with its tests, so
    # lifting them into a shared mixin would mean restructuring a passing file
    # for no gain -- these need a seller, a listing, and a logged-in client, and
    # that is short enough to state here.
    # ------------------------------------------------------------------
    @classmethod
    def setUpClass(cls):
        _use_module_database()
        bot.webhook_app.config["TESTING"] = True
        cls.client = bot.webhook_app.test_client()

    def setUp(self):
        _use_module_database()
        self.now = datetime.utcnow().isoformat(timespec="seconds")
        self._real_api_account_user = bot.api_account_user
        self._real_require_account = bot.require_account
        self._real_emit = bot.pulse_emit_event
        bot.pulse_emit_event = lambda *a, **k: None
        self.owner = self._make_seller()

    def tearDown(self):
        bot.api_account_user = self._real_api_account_user
        bot.require_account = self._real_require_account
        bot.pulse_emit_event = self._real_emit

    def _make_seller(self):
        conn = bot.db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO users (username, display_name, email, account_status, created_at) "
            "VALUES (?,?,?,?,?)",
            ("mkweb_owner", "Web owner", "mkweb_owner@example.com", "active", self.now),
        )
        user_id = int(cur.lastrowid)
        cur.execute(
            "INSERT INTO marketplace_sellers (user_id, business_name, display_name, status, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?)",
            (user_id, "Web store", "Web store", "approved", self.now, self.now),
        )
        conn.commit()
        conn.close()
        return {"user_id": user_id, "username": "mkweb_owner"}

    def _make_listing(self, seller, **overrides):
        row = {
            "title": "Handmade lamp",
            "short_description": "Warm brass lamp",
            "description": "A brass desk lamp finished by hand.",
            "category": "Home",
            "price_label": "$40.00",
            "currency": "USD",
            "quantity": 5,
            "product_type": "physical",
            "listing_type": "physical",
            "status": "published",
            "approval_status": "approved",
            "cover_image_url": "https://cdn.example.com/lamp.jpg",
        }
        row.update(overrides)
        columns = ", ".join(row)
        placeholders = ", ".join(["?"] * len(row))
        conn = bot.db()
        cur = conn.cursor()
        cur.execute(
            f"INSERT INTO marketplace_listings (seller_user_id, {columns}, created_at, updated_at) "
            f"VALUES (?, {placeholders}, ?, ?)",
            (int(seller["user_id"]), *row.values(), self.now, self.now),
        )
        listing_id = int(cur.lastrowid)
        conn.commit()
        conn.close()
        return listing_id

    @contextmanager
    def acting_as(self, user):
        previous_api, previous_require = bot.api_account_user, bot.require_account
        bot.api_account_user = lambda: dict(user)
        bot.require_account = lambda: dict(user)
        try:
            yield
        finally:
            bot.api_account_user, bot.require_account = previous_api, previous_require

    def stored(self, listing_id):
        conn = bot.db()
        conn.row_factory = bot.sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM marketplace_listings WHERE id=?", (listing_id,))
        row = dict(cur.fetchone() or {})
        conn.close()
        return row

    def unpriced_listing(self):
        return self._make_listing(self.owner, title="Unpriced web lamp", price_label="")

    def test_the_marketplace_grid_renders_and_never_says_request_access(self):
        listing_id = self.unpriced_listing()
        with self.acting_as(self.owner):
            response = self.client.get("/pulse/marketplace")
        # A 500 here means the %-format broke when the fallback was threaded
        # into the inline script -- the failure mode this test exists for.
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:400])
        html = response.get_data(as_text=True)
        self.assertIn("Unpriced web lamp", html, "the unpriced listing never rendered")
        self.assertInventsNoPrice(html, "the marketplace grid")
        # The card must still be a card. Dropping the price pill must not take
        # the row's other pills with it, or "no invented price" would be
        # satisfied by rendering nothing at all.
        self.assertIn("Safety", html)
        del listing_id

    def test_the_inline_card_script_invents_no_price_either(self):
        """Same page, second renderer. Search results are drawn in JS.

        The grid is server-rendered on load and re-rendered client-side after a
        search, so the identical card exists twice in two languages. Fixing only
        the Python half would leave a buyer who typed in the search box looking
        at the old phrase.
        """
        with self.acting_as(self.owner):
            html = self.client.get("/pulse/marketplace").get_data(as_text=True)
        self.assertIn("function marketplaceListingHtml", html)
        script = html[html.index("function marketplaceListingHtml"):]
        script = script[:script.index("</script>")] if "</script>" in script else script
        self.assertInventsNoPrice(script, "the inline JS card")

    def test_the_product_page_renders_and_never_says_request_access(self):
        listing_id = self.unpriced_listing()
        with self.acting_as(self.owner):
            response = self.client.get(f"/pulse/marketplace/{listing_id}")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:400])
        html = response.get_data(as_text=True)
        self.assertInventsNoPrice(html, "the product page")

    def test_a_price_the_seller_set_is_still_shown(self):
        """Removing the invented phrase must not remove real prices with it.

        The suppression is keyed on absence. A test that only proves "no
        invented phrase" is satisfied by a page that never prints a price at
        all, which would be a worse bug than the one being fixed.
        """
        priced = self._make_listing(self.owner, title="Priced web lamp",
                                    price_label="$40.00")
        with self.acting_as(self.owner):
            html = self.client.get(f"/pulse/marketplace/{priced}").get_data(as_text=True)
        self.assertIn("$40.00", html)
        self.assertInventsNoPrice(html, "a priced product page")

    def test_the_fallback_is_presentation_only_and_never_reaches_the_row(self):
        """Rendering a page must not write words into the seller's price.

        This is the distinction the whole fix rests on: the serializer keeps an
        unpriced listing empty so nothing downstream can mistake an invented
        phrase for the seller's own, and the card supplies copy at the very last
        moment. If loading a page could persist that copy, the web would simply
        be reintroducing the original bug one GET at a time.
        """
        listing_id = self.unpriced_listing()
        with self.acting_as(self.owner):
            self.client.get("/pulse/marketplace")
            self.client.get(f"/pulse/marketplace/{listing_id}")
        self.assertEqual(self.stored(listing_id).get("price_label"), "")


if __name__ == "__main__":
    unittest.main()
