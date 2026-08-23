"""Product media attachment: ownership, idempotency, and Mux delivery.

Product media used to be POSTed as a multipart body to Flask, which read the
whole file into the web process before forwarding it to R2. The upload now goes
straight to R2 over a signed URL and only the resulting upload id is sent back
to be attached, so the attach step is the entire trust boundary: it is the only
place that decides whether a stored object may appear on a public listing.

The properties pinned here are the ones that boundary exists to guarantee — a
seller can only attach their own upload, only an upload that was authorized for
product media, and attaching twice cannot duplicate a gallery entry.

Runs against a temp sqlite file so nothing touches coinpilotx.db.
"""

import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="marketplace_media_attach_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

import bot  # noqa: E402


def _use_module_database():
    os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
    bot.INIT_DB_COMPLETED = False
    bot.PULSE_MESSENGER_SCHEMA_READY = False
    bot.init_db()


class ProductMediaAttachTest(unittest.TestCase):
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
        self.buyer = self._make_user("buyer")

    def tearDown(self):
        bot.api_account_user = self._real_api_account_user
        bot.require_account = self._real_require_account
        bot.pulse_emit_event = self._real_emit

    # ------------------------------------------------------------------
    # fixtures
    # ------------------------------------------------------------------
    def _make_user(self, role):
        conn = bot.db()
        cur = conn.cursor()
        username = f"mkmedia_{role}"
        cur.execute(
            "INSERT INTO users (username, display_name, email, account_status, created_at) VALUES (?,?,?,?,?)",
            (username, f"Media {role}", f"{username}@example.com", "active", self.now),
        )
        user_id = int(cur.lastrowid)
        conn.commit()
        conn.close()
        return {"user_id": user_id, "username": username}

    def _make_seller(self, role):
        user = self._make_user(role)
        conn = bot.db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO marketplace_sellers (user_id, business_name, display_name, status, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?)",
            (user["user_id"], f"{role} store", f"{role} store", "approved", self.now, self.now),
        )
        conn.commit()
        conn.close()
        return user

    def _make_upload(self, user, **overrides):
        """A finalized direct-to-R2 upload, as `finalize_upload` would leave it."""
        row = {
            "uploader_user_id": int(user["user_id"]),
            "context_type": "marketplace_product",
            "context_id": "draft",
            "original_filename": "product.jpg",
            "media_url": "https://cdn.example.com/pulse_media/1/2026/08/22/abc/product.jpg",
            "thumbnail_url": "https://cdn.example.com/pulse_media/1/2026/08/22/abc/product.jpg",
            "media_type": "image",
            "mime_type": "image/jpeg",
            "file_size_bytes": 240000,
            "storage_provider": "r2",
            "object_key": "pulse_media/1/2026/08/22/abc/product.jpg",
            "is_available": 1,
            "processing_status": "ready",
            "created_at": self.now,
            "updated_at": self.now,
        }
        row.update(overrides)
        conn = bot.db()
        cur = conn.cursor()
        columns = ", ".join(row)
        placeholders = ", ".join(["?"] * len(row))
        cur.execute(
            f"INSERT INTO chat_media_uploads ({columns}) VALUES ({placeholders})",
            tuple(row.values()),
        )
        media_id = int(cur.lastrowid)
        conn.commit()
        conn.close()
        return media_id

    @contextmanager
    def acting_as(self, user):
        previous_api, previous_require = bot.api_account_user, bot.require_account
        bot.api_account_user = lambda: dict(user)
        bot.require_account = lambda: dict(user)
        try:
            yield
        finally:
            bot.api_account_user, bot.require_account = previous_api, previous_require

    def attach(self, user, media_id, kind="gallery"):
        with self.acting_as(user):
            return self.client.post(
                "/api/pulse/marketplace/media/attach",
                json={"media_id": media_id, "kind": kind},
            )

    def product_media(self, product_media_id):
        conn = bot.db()
        conn.row_factory = bot.sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM marketplace_product_media WHERE id=?", (product_media_id,))
        row = dict(cur.fetchone() or {})
        conn.close()
        return row

    def media_row_count(self, user):
        conn = bot.db()
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM marketplace_product_media WHERE merchant_id=?",
            (int(user["user_id"]),),
        )
        total = int((cur.fetchone() or [0])[0])
        conn.close()
        return total

    # ------------------------------------------------------------------
    # the happy path
    # ------------------------------------------------------------------
    def test_owner_attaches_a_finished_upload_to_their_media_library(self):
        media_id = self._make_upload(self.owner)
        resp = self.attach(self.owner, media_id, "cover")
        self.assertEqual(resp.status_code, 200, resp.get_json())

        product_media_id = resp.get_json()["media"]["id"]
        row = self.product_media(product_media_id)
        self.assertEqual(int(row["merchant_id"]), int(self.owner["user_id"]))
        self.assertEqual(int(row["source_media_id"]), media_id)
        self.assertEqual(int(row["is_cover"]), 1)
        self.assertEqual(int(row["product_id"]), 0, "media starts unattached to any listing")
        # The stored URL is the R2 object the client uploaded to directly. If
        # this were ever a pulsesoc.com path the bytes went through Flask.
        self.assertIn("cdn.example.com", row["media_url"])

    def test_attached_media_is_held_for_review_not_auto_published(self):
        media_id = self._make_upload(self.owner)
        product_media_id = self.attach(self.owner, media_id).get_json()["media"]["id"]
        self.assertEqual(self.product_media(product_media_id)["moderation_status"], "pending_review")

    # ------------------------------------------------------------------
    # idempotency
    # ------------------------------------------------------------------
    def test_attaching_the_same_upload_twice_does_not_duplicate_the_gallery(self):
        media_id = self._make_upload(self.owner)
        first = self.attach(self.owner, media_id).get_json()["media"]["id"]
        before = self.media_row_count(self.owner)

        # A retry after a dropped response looks identical to a first call.
        second = self.attach(self.owner, media_id).get_json()["media"]["id"]
        self.assertEqual(first, second)
        self.assertEqual(self.media_row_count(self.owner), before)

    # ------------------------------------------------------------------
    # the trust boundary
    # ------------------------------------------------------------------
    def test_a_seller_cannot_attach_another_sellers_upload(self):
        media_id = self._make_upload(self.other)
        resp = self.attach(self.owner, media_id)
        self.assertEqual(resp.status_code, 403, resp.get_json())
        self.assertEqual(self.media_row_count(self.owner), 0)

    def test_an_upload_authorized_for_something_else_cannot_become_product_media(self):
        # The seller owns this upload, but they authorized it as a private
        # message attachment. Binding it to a listing would publish it.
        media_id = self._make_upload(self.owner, context_type="pulse_message")
        resp = self.attach(self.owner, media_id)
        self.assertEqual(resp.status_code, 400, resp.get_json())
        self.assertEqual(self.media_row_count(self.owner), 0)

    def test_a_non_seller_cannot_attach_product_media(self):
        media_id = self._make_upload(self.buyer)
        resp = self.attach(self.buyer, media_id)
        self.assertEqual(resp.status_code, 403, resp.get_json())

    def test_an_unknown_upload_is_rejected(self):
        self.assertEqual(self.attach(self.owner, 999999).status_code, 404)
        self.assertEqual(self.attach(self.owner, 0).status_code, 400)

    def test_an_upload_that_never_finished_storing_is_rejected(self):
        # No media_url means finalize never ran: there is no object in R2 to
        # show a buyer, so claiming success here would publish a broken image.
        media_id = self._make_upload(self.owner, media_url="", processing_status="uploading")
        resp = self.attach(self.owner, media_id)
        self.assertEqual(resp.status_code, 409, resp.get_json())

    def test_unsupported_media_kinds_are_rejected(self):
        media_id = self._make_upload(
            self.owner, media_type="audio", mime_type="audio/mpeg", original_filename="jingle.mp3"
        )
        resp = self.attach(self.owner, media_id)
        self.assertEqual(resp.status_code, 400, resp.get_json())
        self.assertEqual(self.media_row_count(self.owner), 0)

    # ------------------------------------------------------------------
    # video delivery
    # ------------------------------------------------------------------
    def test_video_attaches_while_mux_is_still_processing(self):
        # Mux has not produced a playback id yet. The seller must not be
        # blocked, and the row must not claim to be ready.
        media_id = self._make_upload(
            self.owner,
            media_type="video",
            mime_type="video/mp4",
            original_filename="demo.mp4",
            processing_status="mux_processing",
        )
        resp = self.attach(self.owner, media_id, "video")
        self.assertEqual(resp.status_code, 200, resp.get_json())
        self.assertEqual(resp.get_json()["media"]["processing_status"], "mux_processing")
        self.assertEqual(self.product_media(resp.get_json()["media"]["id"])["media_type"], "video")

    def test_a_saved_listing_serves_the_mux_stream_not_the_r2_original(self):
        media_id = self._make_upload(
            self.owner,
            media_type="video",
            mime_type="video/mp4",
            original_filename="demo.mp4",
            media_url="https://cdn.example.com/pulse_media/1/demo.mp4",
            processing_status="ready",
        )
        product_media_id = self.attach(self.owner, media_id, "video").get_json()["media"]["id"]

        # Mux finishes after the seller attached the file, which is the normal
        # ordering: encoding takes longer than picking the next form field.
        conn = bot.db()
        cur = conn.cursor()
        cur.execute(
            "UPDATE chat_media_uploads SET mux_playback_id=?, playback_url=?, mux_status='ready' WHERE id=?",
            ("pbid123", "https://stream.mux.com/pbid123.m3u8", media_id),
        )
        conn.commit()
        conn.close()

        conn = bot.db()
        conn.row_factory = bot.sqlite3.Row
        cur = conn.cursor()
        row = dict(self.product_media(product_media_id))
        resolved = bot.marketplace_media_delivery_url(cur, row)
        conn.close()
        self.assertEqual(resolved, "https://stream.mux.com/pbid123.m3u8")

    def test_image_delivery_url_is_left_alone(self):
        media_id = self._make_upload(self.owner)
        product_media_id = self.attach(self.owner, media_id).get_json()["media"]["id"]
        conn = bot.db()
        conn.row_factory = bot.sqlite3.Row
        cur = conn.cursor()
        resolved = bot.marketplace_media_delivery_url(cur, dict(self.product_media(product_media_id)))
        conn.close()
        self.assertIn("cdn.example.com", resolved)


if __name__ == "__main__":
    unittest.main()
