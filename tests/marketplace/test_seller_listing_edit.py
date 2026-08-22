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


if __name__ == "__main__":
    unittest.main()
