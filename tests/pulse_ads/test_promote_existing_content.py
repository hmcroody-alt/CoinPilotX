"""Promote-existing-content contracts: picker listing, eligibility, launch.

Covers the mission's hard rules for Post ads:
  * the promotable-content list is owner-scoped, paginated, filterable, and
    every item carries a server-decided eligibility status;
  * presentation-safe fields only — no raw media URLs or storage keys;
  * live replays come from finalized pulse_videos rows, and a processing
    replay is REPLAY_PROCESSING, never promotable;
  * foreign content is 403 at creation; private content is refused;
  * double-tap cannot create two promotions (idempotency key) and content
    with an active promotion cannot get a second one;
  * insufficient wallet balance surfaces funding_required with the draft
    preserved — never a fake launch;
  * launched promotions create content-REFERENCE creatives (the ad points at
    the original; nothing is duplicated into a second organic post);
  * deleting or re-moderating the source cancels the promotion and suspends
    the backing campaign so delivery stops.
"""

import os
import sqlite3
import sys
import unittest
from datetime import datetime, timedelta, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO_ROOT)

from services import pulse_ad_payments, pulse_ads_service, pulsesoc_promotions  # noqa: E402
from services.pulsesoc_promotions import PromotionError  # noqa: E402
from tests.pulse_ads.test_campaign_activation import SCHEMA as ADS_SCHEMA  # noqa: E402

OWNER = 101
STRANGER = 202

CONTENT_SCHEMA = """
CREATE TABLE pulse_ad_policy_flags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    creative_id INTEGER NOT NULL,
    flag_type TEXT,
    severity TEXT,
    details TEXT,
    created_at TEXT
);
CREATE TABLE pulse_posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    post_type TEXT,
    body TEXT,
    media_ids_json TEXT,
    title TEXT,
    visibility TEXT DEFAULT 'public',
    moderation_status TEXT DEFAULT 'approved',
    status TEXT DEFAULT 'published',
    created_at TEXT,
    updated_at TEXT,
    deleted_at TEXT
);
CREATE TABLE pulse_reels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id INTEGER,
    user_id INTEGER,
    caption TEXT,
    video_url TEXT,
    poster_url TEXT,
    mux_playback_id TEXT,
    duration_seconds REAL DEFAULT 0,
    processing_status TEXT DEFAULT 'ready',
    moderation_status TEXT DEFAULT 'approved',
    status TEXT DEFAULT 'active',
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE pulse_videos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_user_id INTEGER NOT NULL,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    title TEXT,
    description TEXT,
    thumbnail_url TEXT,
    media_url TEXT,
    playback_url TEXT,
    mux_playback_id TEXT,
    mux_status TEXT DEFAULT 'processing',
    processing_status TEXT DEFAULT 'processing',
    duration_seconds REAL DEFAULT 0,
    visibility TEXT DEFAULT 'public',
    moderation_status TEXT DEFAULT 'approved',
    status TEXT DEFAULT 'active',
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE pulse_media_assets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    media_id INTEGER,
    owner_user_id INTEGER,
    storage_key TEXT,
    public_url TEXT,
    thumbnail_url TEXT,
    poster_url TEXT,
    mux_playback_id TEXT,
    media_type TEXT,
    created_at TEXT
);
"""


def _iso(offset_minutes=0):
    return (
        datetime.now(timezone.utc) + timedelta(minutes=offset_minutes)
    ).replace(microsecond=0).isoformat()


class PromoteExistingContentTestCase(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(ADS_SCHEMA)
        self.conn.executescript(CONTENT_SCHEMA)
        pulse_ads_service.seed_placements(self.conn.cursor())
        pulsesoc_promotions.ensure_tables(self.conn)
        self.conn.commit()
        self._billing_env = os.environ.get("PULSE_ADS_BILLING_ENABLED")
        os.environ["PULSE_ADS_BILLING_ENABLED"] = "1"

    def tearDown(self):
        if self._billing_env is None:
            os.environ.pop("PULSE_ADS_BILLING_ENABLED", None)
        else:
            os.environ["PULSE_ADS_BILLING_ENABLED"] = self._billing_env
        self.conn.close()

    # -- seed helpers -------------------------------------------------------

    def _post(self, owner=OWNER, title="Hello", visibility="public", moderation="approved", offset=0, deleted=False):
        cur = self.conn.cursor()
        cur.execute(
            "INSERT INTO pulse_posts (user_id, post_type, body, title, visibility, moderation_status, status, created_at, deleted_at)"
            " VALUES (?, 'post', 'Body text', ?, ?, ?, 'published', ?, ?)",
            (owner, title, visibility, moderation, _iso(offset), _iso() if deleted else None),
        )
        self.conn.commit()
        return cur.lastrowid

    def _reel(self, owner=OWNER, processing="ready", offset=0):
        cur = self.conn.cursor()
        cur.execute(
            "INSERT INTO pulse_reels (user_id, caption, poster_url, mux_playback_id, duration_seconds, processing_status, created_at)"
            " VALUES (?, 'Reel caption', 'https://cdn.example/poster.jpg', 'pbk123', 12.5, ?, ?)",
            (owner, processing, _iso(offset)),
        )
        self.conn.commit()
        return cur.lastrowid

    def _replay(self, owner=OWNER, processing="ready", offset=0, visibility="public"):
        cur = self.conn.cursor()
        cur.execute(
            "INSERT INTO pulse_videos (owner_user_id, source_type, source_id, title, thumbnail_url, mux_playback_id,"
            " processing_status, mux_status, visibility, created_at)"
            " VALUES (?, 'live', ?, 'Launch night', '', 'replaypbk', ?, ?, ?, ?)",
            (owner, f"live-{offset}", processing, processing, visibility, _iso(offset)),
        )
        self.conn.commit()
        return cur.lastrowid

    def _funded_account(self, owner=OWNER, cents=20000):
        cur = self.conn.cursor()
        now = _iso()
        cur.execute(
            "INSERT INTO pulse_ad_accounts (owner_user_id, business_name, status, verification_status, created_at, updated_at)"
            " VALUES (?, 'Biz', 'active', 'verified', ?, ?)",
            (owner, now, now),
        )
        account_id = cur.lastrowid
        wallet = pulse_ad_payments.ensure_wallet(self.conn, account_id)
        self.conn.execute(
            "UPDATE pulse_ad_wallets SET available_balance_cents=? WHERE id=?", (cents, wallet["id"])
        )
        self.conn.commit()
        return account_id

    def _payload(self, content_type, content_id, launch=True, key=""):
        payload = {
            "content_type": content_type,
            "content_id": content_id,
            "goal": "more_views",
            "budget": {"type": "total", "amount_cents": 2000},
            "duration": {"days": 3},
            "launch": launch,
        }
        if key:
            payload["idempotency_key"] = key
        return payload

    # -- promotable-content listing ----------------------------------------

    def test_list_is_owner_scoped_and_stamps_eligibility(self):
        self._post(title="Mine")
        self._post(owner=STRANGER, title="Not mine")
        self._reel()
        self._replay()
        result = pulsesoc_promotions.list_promotable_content(self.conn, OWNER)
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["items"]), 3)
        types = {item["content_type"] for item in result["items"]}
        self.assertEqual(types, {"post", "reel", "live_replay"})
        for item in result["items"]:
            self.assertEqual(item["eligibility"], "PROMOTABLE")
            self.assertTrue(item["promotable"])
        titles = {item["title"] for item in result["items"]}
        self.assertNotIn("Not mine", titles)

    def test_private_processing_and_moderation_statuses(self):
        self._post(title="Private", visibility="followers")
        self._post(title="Flagged", moderation="rejected")
        self._reel(processing="processing")
        self._replay(processing="processing")
        by_status = {
            item["eligibility"]
            for item in pulsesoc_promotions.list_promotable_content(self.conn, OWNER)["items"]
        }
        self.assertEqual(by_status, {"PRIVATE", "MODERATION_BLOCKED", "PROCESSING", "REPLAY_PROCESSING"})

    def test_type_filter_and_pagination(self):
        for offset in range(5):
            self._post(title=f"P{offset}", offset=-offset)
        page1 = pulsesoc_promotions.list_promotable_content(self.conn, OWNER, "post", limit=2, offset=0)
        page2 = pulsesoc_promotions.list_promotable_content(self.conn, OWNER, "post", limit=2, offset=2)
        self.assertEqual(len(page1["items"]), 2)
        self.assertTrue(page1["has_more"])
        self.assertEqual(page1["next_offset"], 2)
        ids1 = {item["content_id"] for item in page1["items"]}
        ids2 = {item["content_id"] for item in page2["items"]}
        self.assertFalse(ids1 & ids2)
        only_replays = pulsesoc_promotions.list_promotable_content(self.conn, OWNER, "live_replay")
        self.assertEqual(only_replays["items"], [])

    def test_presentation_safe_fields_never_leak_media_urls(self):
        self._reel()
        self._replay()
        for item in pulsesoc_promotions.list_promotable_content(self.conn, OWNER)["items"]:
            self.assertNotIn("media_url", item)
            self.assertNotIn("storage_key", item)
            self.assertNotIn("playback_url", item)
            thumb = item.get("thumbnail_url") or ""
            if thumb:
                self.assertTrue(
                    thumb.startswith("https://cdn.example/") or thumb.startswith("https://image.mux.com/"),
                    thumb,
                )

    def test_deleted_posts_never_listed(self):
        self._post(title="Gone", deleted=True)
        result = pulsesoc_promotions.list_promotable_content(self.conn, OWNER)
        self.assertEqual(result["items"], [])

    # -- creation: ownership, duplicates, idempotency ----------------------

    def test_foreign_content_is_403(self):
        post_id = self._post(owner=STRANGER)
        self._funded_account()
        with self.assertRaises(PromotionError) as ctx:
            pulsesoc_promotions.create_promotion(self.conn, OWNER, self._payload("post", post_id))
        self.assertEqual(ctx.exception.status_code, 403)

    def test_private_content_is_refused(self):
        post_id = self._post(visibility="followers")
        self._funded_account()
        with self.assertRaises(PromotionError) as ctx:
            pulsesoc_promotions.create_promotion(self.conn, OWNER, self._payload("post", post_id))
        self.assertEqual(ctx.exception.status_code, 403)

    def test_processing_replay_cannot_launch(self):
        replay_id = self._replay(processing="processing")
        self._funded_account()
        with self.assertRaises(PromotionError):
            pulsesoc_promotions.create_promotion(self.conn, OWNER, self._payload("live_replay", replay_id))

    def test_idempotency_key_returns_same_promotion(self):
        post_id = self._post()
        self._funded_account()
        first = pulsesoc_promotions.create_promotion(
            self.conn, OWNER, self._payload("post", post_id, key="tap-1")
        )
        second = pulsesoc_promotions.create_promotion(
            self.conn, OWNER, self._payload("post", post_id, key="tap-1")
        )
        self.assertEqual(first["promotion_id"], second["promotion_id"])
        count = self.conn.execute("SELECT COUNT(*) AS n FROM pulse_content_promotions").fetchone()["n"]
        self.assertEqual(count, 1)
        campaigns = self.conn.execute("SELECT COUNT(*) AS n FROM pulse_ad_campaigns").fetchone()["n"]
        self.assertEqual(campaigns, 1)

    def test_active_duplicate_is_409(self):
        post_id = self._post()
        self._funded_account()
        pulsesoc_promotions.create_promotion(self.conn, OWNER, self._payload("post", post_id, key="a"))
        with self.assertRaises(PromotionError) as ctx:
            pulsesoc_promotions.create_promotion(self.conn, OWNER, self._payload("post", post_id, key="b"))
        self.assertEqual(ctx.exception.status_code, 409)
        listed = pulsesoc_promotions.list_promotable_content(self.conn, OWNER, "post")
        self.assertEqual(listed["items"][0]["eligibility"], "UNDER_REVIEW")

    # -- launch: reference creative, wallet honesty ------------------------

    def test_launch_creates_content_reference_not_duplicate(self):
        reel_id = self._reel()
        self._funded_account()
        promotion = pulsesoc_promotions.create_promotion(self.conn, OWNER, self._payload("reel", reel_id))
        self.assertEqual(promotion["status"], "pending_review")
        creative = self.conn.execute(
            "SELECT creative_type, content_ref_type, content_ref_id FROM pulse_ad_creatives WHERE id=?",
            (promotion["ad_creative_id"],),
        ).fetchone()
        self.assertEqual(creative["creative_type"], "reel")
        self.assertEqual(creative["content_ref_type"], "reel")
        self.assertEqual(creative["content_ref_id"], reel_id)
        posts = self.conn.execute("SELECT COUNT(*) AS n FROM pulse_posts").fetchone()["n"]
        self.assertEqual(posts, 0)  # nothing duplicated into organic content

    def test_launch_replay_reference(self):
        replay_id = self._replay(processing="ready")
        self._funded_account()
        promotion = pulsesoc_promotions.create_promotion(
            self.conn, OWNER, self._payload("live_replay", replay_id)
        )
        self.assertEqual(promotion["status"], "pending_review")
        creative = self.conn.execute(
            "SELECT content_ref_type, content_ref_id FROM pulse_ad_creatives WHERE id=?",
            (promotion["ad_creative_id"],),
        ).fetchone()
        self.assertEqual(creative["content_ref_type"], "live_replay")
        self.assertEqual(creative["content_ref_id"], replay_id)

    def test_insufficient_balance_preserves_draft_and_never_fakes_launch(self):
        post_id = self._post()
        self._funded_account(cents=100)  # below MIN_BUDGET_CENTS
        with self.assertRaises(PromotionError) as ctx:
            pulsesoc_promotions.create_promotion(self.conn, OWNER, self._payload("post", post_id))
        blocked = ctx.exception.promotion
        self.assertEqual(blocked.get("status"), "draft")
        self.assertEqual(blocked.get("billing_status"), "funding_required")
        campaigns = self.conn.execute("SELECT COUNT(*) AS n FROM pulse_ad_campaigns").fetchone()["n"]
        self.assertEqual(campaigns, 0)

    # -- source integrity: deletion / moderation stops delivery ------------

    def test_source_deletion_cancels_promotion_and_suspends_campaign(self):
        post_id = self._post()
        self._funded_account()
        promotion = pulsesoc_promotions.create_promotion(self.conn, OWNER, self._payload("post", post_id))
        self.conn.execute("UPDATE pulse_posts SET deleted_at=? WHERE id=?", (_iso(), post_id))
        self.conn.commit()
        revoked = pulsesoc_promotions.enforce_source_integrity(self.conn, OWNER)
        self.assertEqual(revoked, 1)
        row = self.conn.execute(
            "SELECT status FROM pulse_content_promotions WHERE id=?", (promotion["promotion_id"],)
        ).fetchone()
        self.assertEqual(row["status"], "canceled")
        campaign = self.conn.execute(
            "SELECT status FROM pulse_ad_campaigns WHERE id=?", (promotion["ad_campaign_id"],)
        ).fetchone()
        self.assertEqual(campaign["status"], "suspended")

    def test_post_launch_moderation_block_stops_delivery(self):
        reel_id = self._reel()
        self._funded_account()
        promotion = pulsesoc_promotions.create_promotion(self.conn, OWNER, self._payload("reel", reel_id))
        self.conn.execute("UPDATE pulse_reels SET moderation_status='rejected' WHERE id=?", (reel_id,))
        self.conn.commit()
        listed = pulsesoc_promotions.list_promotions(self.conn, OWNER)  # runs integrity sweep
        promo = next(p for p in listed if p["promotion_id"] == promotion["promotion_id"])
        self.assertEqual(promo["status"], "canceled")
        campaign = self.conn.execute(
            "SELECT status FROM pulse_ad_campaigns WHERE id=?", (promotion["ad_campaign_id"],)
        ).fetchone()
        self.assertEqual(campaign["status"], "suspended")


if __name__ == "__main__":
    unittest.main()
