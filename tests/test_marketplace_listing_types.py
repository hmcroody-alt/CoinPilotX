"""Route-level tests for the 5-type marketplace listing flow.

The legacy marketplace surface (``/api/pulse/marketplace/*`` over
``marketplace_listings``) now supports listing_type in
{physical, digital, service, event, booking} with a per-type metadata
document. The mobile app builds against this contract, so the tests pin the
observable behavior: creation stores + echoes the type and cleaned metadata,
invalid enums and malformed structures are 400s, seller reads echo the parsed
metadata, PATCH revalidates metadata against the row's type, listing_type is
immutable after creation, and the digital deliverable upload/download flow is
gated on seller approval and a paid order.

Runs against a temp sqlite file rather than the local dev database, so the
schema comes from init_db and nothing here can leave rows behind in
coinpilotx.db.

Run: python3 -m pytest tests/test_marketplace_listing_types.py
"""

import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="mkt_listing_types_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
# Digital deliverable files land under the private upload root; keep the test
# run inside its own temp directory. Must be set before bot/media_storage import.
_PRIVATE_DIR = tempfile.mkdtemp(prefix="mkt_digital_files_")
os.environ["PRIVATE_MEDIA_UPLOAD_DIR"] = _PRIVATE_DIR

import bot  # noqa: E402


SELLER = 93401
BUYER = 93402
OTHER_SELLER = 93403

NOW = "2026-08-01T00:00:00"


def _account(user_id, username):
    return {"user_id": user_id, "username": username, "email": f"{username}@example.com"}


VALID_METADATA = {
    "physical": {
        "condition": "New",
        "variants": [{"name": "Size", "value": "M"}, {"name": "Color", "value": "Black"}],
        "delivery_options": "both",
        "location": "Lagos, Nigeria",
        "return_policy": "Returns accepted within 7 days.",
    },
    "service": {
        "pricing_mode": "starting_at",
        "delivery_time_days": 14,
        "service_location": "both",
        "location": "Accra",
        "included": ["Initial consultation", "Two revisions"],
        "addons": [{"title": "Rush delivery", "price_label": "$50"}],
    },
    "event": {
        "event_date": "2026-09-12",
        "start_time": "18:00",
        "end_time": "21:00",
        "venue_mode": "in_person",
        "location": "Eko Convention Centre, Lagos",
        "online_url": "",
        "tickets": [{"name": "General", "price_label": "$25", "capacity": 300}],
    },
    "booking": {
        "duration_minutes": 60,
        "meeting_mode": "video",
        "availability": {
            "mon": [{"start": "09:00", "end": "12:00"}],
            "wed": [{"start": "14:00", "end": "17:00"}],
        },
        "buffer_minutes": 15,
        "cancellation_policy": "Cancel up to 24 hours before the session.",
    },
}


def digital_metadata(file_id, size_bytes=1024):
    return {
        "files": [{"file_id": file_id, "name": "workbook.pdf", "size_bytes": size_bytes}],
        "delivery": "automatic",
        "license": "personal",
        "download_limit": None,
    }


class ListingTypesTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db_path = _DB_PATH
        bot.init_db()
        conn = sqlite3.connect(cls.db_path)
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='marketplace_listings'")
        if not cur.fetchone():
            conn.close()
            raise unittest.SkipTest("init_db did not create marketplace_listings in the temp database")
        conn.close()
        cls._real_account_user = bot.api_account_user
        bot.webhook_app.config["TESTING"] = True
        cls.client = bot.webhook_app.test_client()

    @classmethod
    def tearDownClass(cls):
        bot.api_account_user = cls._real_account_user

    def login(self, user_id, username):
        bot.api_account_user = lambda *args, **kwargs: _account(user_id, username)

    def setUp(self):
        self.login(SELLER, "listing_seller")
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        for table, column in (
            ("marketplace_listings", "seller_user_id"),
            ("marketplace_product_media", "merchant_id"),
            ("marketplace_sellers", "user_id"),
            ("marketplace_digital_files", "seller_user_id"),
            ("seller_transactions", "buyer_user_id"),
        ):
            cur.execute(f"DELETE FROM {table} WHERE {column} IN (?,?,?)", (SELLER, BUYER, OTHER_SELLER))
        for user_id, username in ((SELLER, "listing_seller"), (BUYER, "listing_buyer"), (OTHER_SELLER, "other_seller")):
            cur.execute("INSERT OR IGNORE INTO users (user_id, username, display_name) VALUES (?,?,?)", (user_id, username, username))
        for user_id in (SELLER, OTHER_SELLER):
            cur.execute(
                "INSERT INTO marketplace_sellers (user_id, display_name, status, created_at, updated_at) VALUES (?,?,?,?,?)",
                (user_id, f"Seller {user_id}", "approved", NOW, NOW),
            )
        conn.commit()
        conn.close()

    # -- helpers --------------------------------------------------------------

    def db(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def cover_media_id(self, merchant_id=SELLER):
        conn = self.db()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO marketplace_product_media
            (product_id, merchant_id, media_type, media_url, thumbnail_url, position, is_cover, mime_type, file_size, moderation_status, created_at)
            VALUES (0, ?, 'image', '/static/uploads/pulse_media/test-cover.jpg', '/static/uploads/pulse_media/test-cover.jpg', 0, 1, 'image/jpeg', 2048, 'pending_review', ?)
            """,
            (merchant_id, NOW),
        )
        media_id = int(cur.lastrowid)
        conn.commit()
        conn.close()
        return media_id

    def digital_file_id(self, seller_user_id=SELLER, file_name="workbook.pdf"):
        conn = self.db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO marketplace_digital_files (seller_user_id, file_name, file_size, storage_url, created_at) VALUES (?,?,?,?,?)",
            (seller_user_id, file_name, 1024, "marketplace-digital/2026/08/01/workbook-test.pdf", NOW),
        )
        file_id = int(cur.lastrowid)
        conn.commit()
        conn.close()
        return file_id

    def create_listing(self, listing_type=None, listing_metadata=None, **overrides):
        payload = {
            "title": f"{(listing_type or 'legacy').title()} listing",
            "description": "A well described listing for coverage purposes.",
            "category": "Education",
            "price_label": "$20",
            "media_ids": [self.cover_media_id()],
        }
        if listing_type is not None:
            payload["listing_type"] = listing_type
        if listing_metadata is not None:
            payload["listing_metadata"] = listing_metadata
        payload.update(overrides)
        return self.client.post("/api/pulse/marketplace/listings/create", json=payload)

    def listing_row(self, listing_id):
        conn = self.db()
        row = conn.execute("SELECT * FROM marketplace_listings WHERE id=?", (listing_id,)).fetchone()
        conn.close()
        return dict(row or {})

    def mark_paid(self, listing_id, buyer_user_id=BUYER, seller_user_id=SELLER, status="paid"):
        conn = self.db()
        conn.execute(
            """
            INSERT INTO seller_transactions
            (buyer_user_id, seller_user_id, seller_type, item_type, item_id, amount_cents, currency, status, created_at, updated_at)
            VALUES (?, ?, 'merchant', 'marketplace_product', ?, 2000, 'USD', ?, ?, ?)
            """,
            (buyer_user_id, seller_user_id, listing_id, status, NOW, NOW),
        )
        conn.commit()
        conn.close()

    # -- creation: one listing of each type ----------------------------------

    def test_draft_submit_and_publication_gate(self):
        response = self.create_listing(
            "physical",
            VALID_METADATA["physical"],
            submission_action="draft",
            quantity=3,
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        listing_id = response.get_json()["listing_id"]
        self.assertEqual(self.listing_row(listing_id)["status"], "draft")

        hidden = self.client.get("/api/pulse/marketplace/search?q=Physical")
        self.assertFalse(any(item["id"] == listing_id for item in hidden.get_json()["items"]))

        submitted = self.client.post(f"/api/pulse/marketplace/seller/listings/{listing_id}/submit")
        self.assertEqual(submitted.status_code, 200, submitted.get_json())
        self.assertEqual(self.listing_row(listing_id)["status"], "pending_review")

        conn = self.db()
        conn.execute(
            "UPDATE marketplace_listings SET status='published', approval_status='approved' WHERE id=?",
            (listing_id,),
        )
        conn.commit()
        conn.close()
        public = self.client.get("/api/pulse/marketplace/search?q=Physical")
        self.assertTrue(any(item["id"] == listing_id for item in public.get_json()["items"]))

        conn = self.db()
        conn.execute("UPDATE marketplace_listings SET status='suspended' WHERE id=?", (listing_id,))
        conn.commit()
        conn.close()
        suspended = self.client.get("/api/pulse/marketplace/search?q=Physical")
        self.assertFalse(any(item["id"] == listing_id for item in suspended.get_json()["items"]))

    def test_foreign_seller_cannot_submit_listing(self):
        response = self.create_listing(
            "physical", VALID_METADATA["physical"], submission_action="draft"
        )
        listing_id = response.get_json()["listing_id"]
        self.login(OTHER_SELLER, "other_seller")
        denied = self.client.post(f"/api/pulse/marketplace/seller/listings/{listing_id}/submit")
        self.assertEqual(denied.status_code, 404)

    def test_create_one_listing_of_each_of_the_five_types(self):
        digital_file = self.digital_file_id()
        metadata_by_type = dict(VALID_METADATA)
        metadata_by_type["digital"] = digital_metadata(digital_file)
        for listing_type, metadata in metadata_by_type.items():
            with self.subTest(listing_type=listing_type):
                response = self.create_listing(listing_type, metadata)
                self.assertEqual(response.status_code, 200, response.get_json())
                body = response.get_json()
                self.assertTrue(body["ok"])
                self.assertEqual(body["listing_type"], listing_type)
                row = self.listing_row(body["listing_id"])
                self.assertEqual(row["listing_type"], listing_type)
                self.assertEqual(row["product_type"], listing_type)
                stored = json.loads(row["listing_metadata_json"])
                self.assertIsInstance(stored, dict)
                if listing_type == "physical":
                    self.assertEqual(stored["condition"], "New")
                    self.assertEqual(stored["delivery_options"], "both")
                    self.assertEqual(len(stored["variants"]), 2)
                elif listing_type == "digital":
                    self.assertEqual(stored["files"][0]["file_id"], digital_file)
                    self.assertEqual(stored["delivery"], "automatic")
                    self.assertIsNone(stored["download_limit"])
                elif listing_type == "service":
                    self.assertEqual(stored["pricing_mode"], "starting_at")
                    self.assertEqual(stored["delivery_time_days"], 14)
                elif listing_type == "event":
                    self.assertEqual(stored["event_date"], "2026-09-12")
                    self.assertEqual(stored["tickets"][0]["capacity"], 300)
                elif listing_type == "booking":
                    self.assertEqual(stored["duration_minutes"], 60)
                    self.assertEqual(stored["availability"]["mon"][0]["start"], "09:00")

    def test_legacy_create_without_new_fields_still_works(self):
        response = self.create_listing()
        self.assertEqual(response.status_code, 200, response.get_json())
        row = self.listing_row(response.get_json()["listing_id"])
        # Old clients that sent nothing keep the legacy default product_type.
        self.assertEqual(row["product_type"], "digital")
        self.assertEqual(row["listing_type"], "digital")
        self.assertEqual(row["listing_metadata_json"] or "", "")

    def test_create_with_unknown_listing_type_is_rejected(self):
        response = self.create_listing("subscription", {})
        self.assertEqual(response.status_code, 400)
        self.assertIn("listing_type", response.get_json()["message"])

    def test_invalid_enums_are_rejected_with_400(self):
        cases = [
            ("physical", {"delivery_options": "teleport"}),
            ("service", {"pricing_mode": "auction"}),
            ("event", {"venue_mode": "metaverse"}),
            ("booking", {"meeting_mode": "hologram"}),
            ("digital", {"delivery": "manual"}),
        ]
        for listing_type, metadata in cases:
            with self.subTest(listing_type=listing_type):
                response = self.create_listing(listing_type, metadata)
                self.assertEqual(response.status_code, 400, response.get_json())
                self.assertTrue(response.get_json()["message"])

    def test_malformed_structures_are_rejected_with_400(self):
        cases = [
            ("digital", {"files": {"file_id": 1}}),
            ("physical", {"variants": ["Size M"]}),
            ("booking", {"availability": {"mon": [{"start": "25:99", "end": "26:00"}]}}),
            ("booking", {"duration_minutes": 3}),
            ("event", {"event_date": "next friday"}),
            ("service", {"delivery_time_days": 900}),
        ]
        for listing_type, metadata in cases:
            with self.subTest(listing_type=listing_type, metadata=metadata):
                response = self.create_listing(listing_type, metadata)
                self.assertEqual(response.status_code, 400, response.get_json())

    def test_metadata_strings_are_sanitized_and_lists_capped(self):
        metadata = {
            "condition": "<b>Brand   new</b>",
            "variants": [{"name": f"Option {i}", "value": "x"} for i in range(20)],
            "delivery_options": "pickup",
        }
        response = self.create_listing("physical", metadata)
        self.assertEqual(response.status_code, 200, response.get_json())
        stored = json.loads(self.listing_row(response.get_json()["listing_id"])["listing_metadata_json"])
        self.assertEqual(stored["condition"], "Brand new")
        self.assertEqual(len(stored["variants"]), 12)

    def test_digital_listing_cannot_reference_another_sellers_file(self):
        foreign_file = self.digital_file_id(seller_user_id=OTHER_SELLER)
        response = self.create_listing("digital", digital_metadata(foreign_file))
        self.assertEqual(response.status_code, 400)
        self.assertIn("not found in your uploads", response.get_json()["message"])

    # -- reads ----------------------------------------------------------------

    def test_seller_listings_echo_listing_type_and_parsed_metadata(self):
        response = self.create_listing("service", VALID_METADATA["service"])
        listing_id = response.get_json()["listing_id"]
        listing = self.get_seller_listing(listing_id)
        self.assertEqual(listing["listing_type"], "service")
        self.assertEqual(listing["listing_metadata"]["pricing_mode"], "starting_at")
        self.assertEqual(listing["listing_metadata"]["included"], ["Initial consultation", "Two revisions"])

    def get_seller_listing(self, listing_id):
        response = self.client.get("/api/pulse/marketplace/seller/listings")
        self.assertEqual(response.status_code, 200)
        items = response.get_json()["items"]
        listing = next((item for item in items if int(item.get("id") or 0) == int(listing_id)), None)
        self.assertIsNotNone(listing, f"listing {listing_id} missing from seller listings")
        return listing

    def test_listing_without_metadata_reads_as_empty_dict(self):
        response = self.create_listing("physical")
        listing = self.get_seller_listing(response.get_json()["listing_id"])
        self.assertEqual(listing["listing_type"], "physical")
        self.assertEqual(listing["listing_metadata"], {})

    # -- PATCH ----------------------------------------------------------------

    def patch_listing(self, listing_id, **payload):
        body = {
            "title": "Updated listing title",
            "description": "Updated description that is long enough.",
            "category": "Education",
            "price_label": "$25",
        }
        body.update(payload)
        return self.client.patch(f"/api/pulse/marketplace/seller/listings/{listing_id}", json=body)

    def test_patch_revalidates_metadata_against_the_rows_listing_type(self):
        listing_id = self.create_listing("booking", VALID_METADATA["booking"]).get_json()["listing_id"]
        good = self.patch_listing(listing_id, listing_metadata={"duration_minutes": 30, "meeting_mode": "audio"})
        self.assertEqual(good.status_code, 200, good.get_json())
        stored = json.loads(self.listing_row(listing_id)["listing_metadata_json"])
        self.assertEqual(stored, {"duration_minutes": 30, "meeting_mode": "audio"})
        self.assertEqual(good.get_json()["listing"]["listing_metadata"]["duration_minutes"], 30)

        bad = self.patch_listing(listing_id, listing_metadata={"meeting_mode": "hologram"})
        self.assertEqual(bad.status_code, 400)
        unchanged = json.loads(self.listing_row(listing_id)["listing_metadata_json"])
        self.assertEqual(unchanged, {"duration_minutes": 30, "meeting_mode": "audio"})

    def test_patch_without_metadata_leaves_stored_metadata_alone(self):
        listing_id = self.create_listing("event", VALID_METADATA["event"]).get_json()["listing_id"]
        response = self.patch_listing(listing_id)
        self.assertEqual(response.status_code, 200, response.get_json())
        stored = json.loads(self.listing_row(listing_id)["listing_metadata_json"])
        self.assertEqual(stored["event_date"], "2026-09-12")

    def test_listing_type_is_immutable_after_creation(self):
        listing_id = self.create_listing("event", VALID_METADATA["event"]).get_json()["listing_id"]
        response = self.patch_listing(listing_id, listing_type="physical")
        self.assertEqual(response.status_code, 400)
        self.assertIn("cannot be changed", response.get_json()["message"])
        self.assertEqual(self.listing_row(listing_id)["listing_type"], "event")
        # Re-sending the same type is a harmless no-op, not an error.
        same = self.patch_listing(listing_id, listing_type="event")
        self.assertEqual(same.status_code, 200, same.get_json())

    # -- digital deliverables -------------------------------------------------

    def upload_digital_file(self, filename="guide.pdf", content=b"%PDF-1.4 test digital deliverable"):
        return self.client.post(
            "/api/pulse/marketplace/digital-files/upload",
            data={"file": (io.BytesIO(content), filename)},
            content_type="multipart/form-data",
        )

    def test_digital_file_upload_requires_an_approved_seller(self):
        self.login(BUYER, "listing_buyer")
        response = self.upload_digital_file()
        self.assertEqual(response.status_code, 403)

    def test_digital_file_upload_rejects_unsupported_types(self):
        response = self.upload_digital_file(filename="malware.exe", content=b"MZ....")
        self.assertEqual(response.status_code, 400)

    def test_digital_upload_purchase_and_download_flow(self):
        content = b"%PDF-1.4 the actual deliverable body"
        upload = self.upload_digital_file(content=content)
        self.assertEqual(upload.status_code, 200, upload.get_json())
        file_payload = upload.get_json()["file"]
        self.assertEqual(set(file_payload), {"file_id", "name", "size_bytes"})
        self.assertEqual(file_payload["size_bytes"], len(content))
        file_id = file_payload["file_id"]

        listing_id = self.create_listing(
            "digital", digital_metadata(file_id, size_bytes=len(content))
        ).get_json()["listing_id"]

        # The seller who owns the file can always download it.
        owner_download = self.client.get(f"/api/pulse/marketplace/digital-files/{file_id}/download")
        self.assertEqual(owner_download.status_code, 200)
        self.assertEqual(owner_download.data, content)

        # A buyer without a paid order is refused.
        self.login(BUYER, "listing_buyer")
        blocked = self.client.get(f"/api/pulse/marketplace/digital-files/{file_id}/download")
        self.assertEqual(blocked.status_code, 403)

        # After a paid order the download succeeds ...
        self.mark_paid(listing_id)
        allowed = self.client.get(f"/api/pulse/marketplace/digital-files/{file_id}/download")
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(allowed.data, content)

        # ... and the buyer orders response lists the deliverable.
        orders = self.client.get("/api/pulse/orders")
        self.assertEqual(orders.status_code, 200)
        order = next(
            (o for o in orders.get_json()["orders"] if int(o.get("item_id") or 0) == int(listing_id)),
            None,
        )
        self.assertIsNotNone(order, "paid marketplace order missing from buyer orders")
        self.assertEqual(
            order["digital_files"],
            [{"name": "workbook.pdf", "download_url": f"/api/pulse/marketplace/digital-files/{file_id}/download"}],
        )

    def test_unpaid_order_carries_no_digital_files(self):
        file_id = self.upload_digital_file().get_json()["file"]["file_id"]
        listing_id = self.create_listing("digital", digital_metadata(file_id)).get_json()["listing_id"]
        self.mark_paid(listing_id, status="checkout_created")
        self.login(BUYER, "listing_buyer")
        orders = self.client.get("/api/pulse/orders")
        order = next(
            (o for o in orders.get_json()["orders"] if int(o.get("item_id") or 0) == int(listing_id)),
            None,
        )
        self.assertIsNotNone(order)
        self.assertEqual(order["digital_files"], [])
        blocked = self.client.get(f"/api/pulse/marketplace/digital-files/{file_id}/download")
        self.assertEqual(blocked.status_code, 403)


if __name__ == "__main__":
    unittest.main()
