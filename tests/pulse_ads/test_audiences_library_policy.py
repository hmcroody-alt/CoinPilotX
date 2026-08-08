"""Backend slice 2: audience manager, creative library, policy center + appeals.

Covers, against the real service modules:
- custom audiences from real first-party engagement data (live estimates,
  update recompute, archive, campaign-reference listing)
- lookalike creation with an honest banded estimate, too-small-seed rejection
- narrow/broad estimate bands
- delivery-time audience matching in select_ads (include, exclude, fail-safe)
- library overview grouping/usage/metrics, asset detail, metadata edits
  resetting moderation, copy-to-campaign with ownership enforcement
- appeals (create, duplicate-blocked, list), admin decisions flipping the
  creative back to approved, rejected -> resubmit transition
- cross-account denial everywhere
"""

import json
import os
import sqlite3
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services import (  # noqa: E402
    pulse_ads_adsets,
    pulse_ads_audiences,
    pulse_ads_library,
    pulse_ads_os,
    pulse_ads_service,
)
from services.pulse_ads_service import PulseAdsError, now_iso  # noqa: E402

SCHEMA = """
CREATE TABLE pulse_ad_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_user_id INTEGER NOT NULL,
    business_name TEXT NOT NULL,
    business_email TEXT,
    business_phone TEXT,
    business_website TEXT,
    business_type TEXT,
    status TEXT DEFAULT 'pending_verification',
    verification_status TEXT DEFAULT 'unverified',
    verification_submitted_at TEXT,
    verification_reviewed_at TEXT,
    verification_reviewer_id INTEGER,
    verification_reason TEXT,
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE pulse_ad_campaigns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ad_account_id INTEGER NOT NULL,
    campaign_name TEXT NOT NULL,
    objective TEXT DEFAULT 'awareness',
    status TEXT DEFAULT 'draft',
    budget_type TEXT DEFAULT 'daily',
    daily_budget_cents INTEGER DEFAULT 0,
    lifetime_budget_cents INTEGER DEFAULT 0,
    spent_cents INTEGER DEFAULT 0,
    start_at TEXT,
    end_at TEXT,
    priority INTEGER DEFAULT 0,
    pacing_mode TEXT DEFAULT 'standard',
    archived_at TEXT,
    submitted_at TEXT,
    approved_at TEXT,
    completed_at TEXT,
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE pulse_ad_creatives (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ad_account_id INTEGER NOT NULL,
    campaign_id INTEGER NOT NULL,
    creative_type TEXT DEFAULT 'text',
    title TEXT NOT NULL,
    body TEXT,
    media_url TEXT,
    thumbnail_url TEXT,
    destination_url TEXT NOT NULL,
    call_to_action TEXT DEFAULT 'Learn more',
    status TEXT DEFAULT 'draft',
    moderation_status TEXT DEFAULT 'draft',
    rejection_reason TEXT,
    archived_at TEXT,
    metadata_json TEXT DEFAULT '{}',
    compatibility_json TEXT DEFAULT '{}',
    moderation_history_json TEXT DEFAULT '{}',
    media_asset_id INTEGER,
    thumbnail_asset_id INTEGER,
    media_ready INTEGER DEFAULT 0,
    media_metadata_json TEXT DEFAULT '{}',
    content_ref_type TEXT DEFAULT '',
    content_ref_id INTEGER DEFAULT 0,
    headline TEXT DEFAULT '',
    primary_text TEXT DEFAULT '',
    aspect_ratio TEXT DEFAULT '',
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE pulse_ad_media_assets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id TEXT UNIQUE,
    owner_user_id INTEGER NOT NULL,
    ad_account_id INTEGER NOT NULL,
    media_upload_id INTEGER,
    asset_kind TEXT DEFAULT 'creative_media',
    media_type TEXT,
    storage_provider TEXT,
    storage_key TEXT,
    public_url TEXT,
    thumbnail_url TEXT,
    poster_url TEXT,
    playback_url TEXT,
    mime_type TEXT,
    width INTEGER DEFAULT 0,
    height INTEGER DEFAULT 0,
    duration_seconds REAL DEFAULT 0,
    file_size INTEGER DEFAULT 0,
    checksum TEXT,
    moderation_status TEXT DEFAULT 'pending',
    security_status TEXT DEFAULT 'passed',
    metadata_json TEXT DEFAULT '{}',
    created_at TEXT,
    updated_at TEXT,
    deleted_at TEXT
);
CREATE TABLE pulse_ad_placements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    placement_key TEXT UNIQUE NOT NULL,
    display_name TEXT NOT NULL,
    device_type TEXT DEFAULT 'all',
    placement_type TEXT DEFAULT 'feed',
    is_active INTEGER DEFAULT 1,
    max_frequency INTEGER DEFAULT 4,
    priority INTEGER DEFAULT 0,
    supported_creative_types TEXT DEFAULT 'image,video,text,hologram,audio',
    card_style TEXT DEFAULT 'signal-card',
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE pulse_ad_campaign_placements (
    campaign_id INTEGER NOT NULL,
    placement_id INTEGER NOT NULL,
    created_at TEXT,
    PRIMARY KEY (campaign_id, placement_id)
);
CREATE TABLE pulse_ad_targeting (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER NOT NULL,
    country TEXT,
    language TEXT,
    interests_json TEXT DEFAULT '{}',
    keywords_json TEXT DEFAULT '{}',
    device_type TEXT DEFAULT 'all',
    min_age INTEGER,
    max_age INTEGER,
    premium_audience INTEGER DEFAULT 0,
    contextual_category TEXT,
    audience_mode TEXT DEFAULT 'everyone',
    saved_audience_ids_json TEXT DEFAULT '[]',
    excluded_audience_ids_json TEXT DEFAULT '[]',
    countries_json TEXT DEFAULT '[]',
    languages_json TEXT DEFAULT '[]',
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE pulse_ad_impressions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER NOT NULL,
    creative_id INTEGER NOT NULL,
    placement_key TEXT NOT NULL,
    viewer_user_id INTEGER,
    session_id TEXT,
    device_type TEXT,
    viewport TEXT,
    rendered_at TEXT,
    visible_ms INTEGER DEFAULT 0,
    viewable INTEGER DEFAULT 0,
    created_at TEXT
);
CREATE TABLE pulse_ad_clicks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER NOT NULL,
    creative_id INTEGER NOT NULL,
    placement_key TEXT NOT NULL,
    viewer_user_id INTEGER,
    session_id TEXT,
    clicked_at TEXT,
    destination_url TEXT,
    created_at TEXT
);
CREATE TABLE pulse_ad_frequency_caps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    viewer_user_id INTEGER,
    session_id TEXT,
    campaign_id INTEGER NOT NULL,
    placement_key TEXT NOT NULL,
    impressions_count INTEGER DEFAULT 0,
    last_seen_at TEXT,
    updated_at TEXT
);
CREATE TABLE pulse_ad_moderation_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    creative_id INTEGER NOT NULL,
    submitted_by INTEGER,
    status TEXT DEFAULT 'pending',
    reviewer_id INTEGER,
    notes TEXT,
    risk_score INTEGER DEFAULT 0,
    created_at TEXT,
    reviewed_at TEXT
);
CREATE TABLE pulse_ad_policy_flags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    creative_id INTEGER NOT NULL,
    flag_type TEXT,
    severity TEXT,
    details TEXT,
    created_at TEXT
);
CREATE TABLE pulse_ad_audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_user_id INTEGER,
    action TEXT NOT NULL,
    entity_type TEXT,
    entity_id TEXT,
    before_json TEXT DEFAULT '{}',
    after_json TEXT DEFAULT '{}',
    ip_hash TEXT,
    user_agent_hash TEXT,
    created_at TEXT
);
CREATE TABLE pulse_ad_review_board (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER NOT NULL,
    creative_id INTEGER NOT NULL,
    review_status TEXT DEFAULT 'pending',
    risk_score INTEGER DEFAULT 0,
    automated_review_status TEXT DEFAULT 'pending',
    human_review_status TEXT DEFAULT 'pending',
    review_reason TEXT,
    reviewer_id INTEGER,
    reviewed_at TEXT,
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE pulse_ad_platform_settings (
    setting_key TEXT PRIMARY KEY,
    setting_value TEXT,
    updated_by INTEGER,
    updated_at TEXT
);
CREATE TABLE pulse_ad_team_members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,
    user_id INTEGER,
    role TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    invited_email TEXT,
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE pulse_ad_wallets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,
    currency TEXT DEFAULT 'usd',
    available_balance_cents INTEGER DEFAULT 0,
    pending_balance_cents INTEGER DEFAULT 0,
    promotional_credits_cents INTEGER DEFAULT 0,
    bonus_credits_cents INTEGER DEFAULT 0,
    refund_credits_cents INTEGER DEFAULT 0,
    lifetime_funded_cents INTEGER DEFAULT 0,
    lifetime_spent_cents INTEGER DEFAULT 0,
    reserved_budget_cents INTEGER DEFAULT 0,
    created_at TEXT,
    updated_at TEXT,
    UNIQUE(account_id, currency)
);
CREATE TABLE pulse_ad_wallet_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,
    campaign_id INTEGER,
    creative_id INTEGER,
    transaction_type TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    currency TEXT DEFAULT 'usd',
    status TEXT DEFAULT 'posted',
    idempotency_key TEXT UNIQUE,
    description TEXT,
    metadata_json TEXT DEFAULT '{}',
    created_at TEXT
);
CREATE TABLE pulse_ad_saved_audiences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    kind TEXT DEFAULT 'saved',
    definition_json TEXT DEFAULT '{}',
    estimated_size INTEGER DEFAULT 0,
    archived_at TEXT,
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE pulse_ad_appeals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,
    creative_id INTEGER NOT NULL,
    campaign_id INTEGER,
    submitted_by_user_id INTEGER,
    message TEXT,
    status TEXT DEFAULT 'open',
    resolution_notes TEXT,
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE pulse_ad_campaign_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER NOT NULL,
    actor_user_id INTEGER,
    action TEXT,
    before_json TEXT DEFAULT '{}',
    after_json TEXT DEFAULT '{}',
    created_at TEXT
);
CREATE TABLE pulse_ad_notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,
    campaign_id INTEGER,
    creative_id INTEGER,
    recipient_user_id INTEGER,
    notification_type TEXT,
    title TEXT,
    body TEXT,
    status TEXT DEFAULT 'unread',
    read_at TEXT,
    created_at TEXT
);
CREATE TABLE pulse_ad_idempotency (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT DEFAULT '',
    idem_key TEXT UNIQUE,
    result_json TEXT DEFAULT '{}',
    created_at TEXT
);
CREATE TABLE privacy_preferences (
    user_id INTEGER PRIMARY KEY,
    analytics_opt_out INTEGER DEFAULT 0,
    personalized_ads_opt_out INTEGER DEFAULT 1,
    public_profile INTEGER DEFAULT 1,
    creator_visibility INTEGER DEFAULT 1,
    updated_at TEXT
);
CREATE TABLE users (
    user_id INTEGER PRIMARY KEY,
    country TEXT,
    preferred_language TEXT,
    date_of_birth TEXT
);
CREATE TABLE pulse_follows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    follower_user_id INTEGER NOT NULL,
    followed_user_id INTEGER NOT NULL,
    created_at TEXT
);
CREATE TABLE pulse_posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    created_at TEXT
);
CREATE TABLE pulse_reactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    post_id INTEGER NOT NULL,
    created_at TEXT
);
CREATE TABLE pulse_comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    post_id INTEGER NOT NULL,
    deleted_at TEXT,
    created_at TEXT
);
CREATE TABLE pulse_videos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_user_id INTEGER NOT NULL,
    created_at TEXT
);
CREATE TABLE pulse_video_views (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id INTEGER NOT NULL,
    viewer_user_id INTEGER,
    created_at TEXT
);
CREATE TABLE seller_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    seller_user_id INTEGER NOT NULL,
    buyer_user_id INTEGER NOT NULL,
    item_type TEXT,
    status TEXT,
    created_at TEXT
);
CREATE TABLE pulse_live_streams (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    creator_user_id INTEGER,
    status TEXT DEFAULT 'ended',
    created_at TEXT
);
CREATE TABLE pulse_live_viewers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    live_id INTEGER,
    user_id INTEGER,
    joined_at TEXT
);
"""

OWNER = 101
STRANGER = 202
ADMIN = 999
VIEWER = 301
OUTSIDER = 302


class AudiencesLibraryPolicyTestCase(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        # Exercise the real DDL/ALTER paths: ad sets (slice 1) and appeal
        # decision columns (slice 2).
        pulse_ads_adsets.ensure_schema(self.conn)
        pulse_ads_os.ensure_schema(self.conn)
        pulse_ads_service.seed_placements(self.conn.cursor())
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    # -- seed helpers -------------------------------------------------------

    def _account(self, owner=OWNER, business_type="internal_promotion"):
        cur = self.conn.cursor()
        now = now_iso()
        cur.execute(
            """
            INSERT INTO pulse_ad_accounts
            (owner_user_id, business_name, business_type, status, verification_status, created_at, updated_at)
            VALUES (?, 'Test Biz', ?, 'active', 'verified', ?, ?)
            """,
            (owner, business_type, now, now),
        )
        self.conn.commit()
        return cur.lastrowid

    def _campaign(self, account_id, status="active", name="Camp"):
        cur = self.conn.cursor()
        now = now_iso()
        cur.execute(
            """
            INSERT INTO pulse_ad_campaigns
            (ad_account_id, campaign_name, objective, status, budget_type, daily_budget_cents,
             lifetime_budget_cents, spent_cents, start_at, end_at, created_at, updated_at)
            VALUES (?, ?, 'awareness', ?, 'daily', 0, 0, 0, '', '', ?, ?)
            """,
            (account_id, name, status, now, now),
        )
        self.conn.commit()
        return cur.lastrowid

    def _attach_placement(self, campaign_id, key="feed_inline"):
        cur = self.conn.cursor()
        cur.execute("SELECT id FROM pulse_ad_placements WHERE placement_key=?", (key,))
        placement_id = cur.fetchone()["id"]
        cur.execute(
            "INSERT OR IGNORE INTO pulse_ad_campaign_placements (campaign_id, placement_id, created_at) VALUES (?, ?, ?)",
            (campaign_id, placement_id, now_iso()),
        )
        self.conn.commit()

    def _creative(self, account_id, campaign_id, creative_type="text", title="Ad",
                  status="approved", moderation="approved"):
        cur = self.conn.cursor()
        now = now_iso()
        cur.execute(
            """
            INSERT INTO pulse_ad_creatives
            (ad_account_id, campaign_id, creative_type, title, body, destination_url,
             status, moderation_status, media_ready, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'Body', 'https://example.com', ?, ?, 1, ?, ?)
            """,
            (account_id, campaign_id, creative_type, title, status, moderation, now, now),
        )
        self.conn.commit()
        return cur.lastrowid

    def _user(self, user_id, country="US", language="en"):
        self.conn.execute(
            "INSERT OR IGNORE INTO users (user_id, country, preferred_language) VALUES (?, ?, ?)",
            (user_id, country, language),
        )
        self.conn.commit()

    def _follow(self, follower, followed=OWNER):
        self.conn.execute(
            "INSERT INTO pulse_follows (follower_user_id, followed_user_id, created_at) VALUES (?, ?, ?)",
            (follower, followed, now_iso()),
        )
        self.conn.commit()

    def _engagement(self, user_id, owner=OWNER):
        cur = self.conn.cursor()
        cur.execute("INSERT INTO pulse_posts (user_id, created_at) VALUES (?, ?)", (owner, now_iso()))
        post_id = cur.lastrowid
        cur.execute(
            "INSERT INTO pulse_reactions (user_id, post_id, created_at) VALUES (?, ?, ?)",
            (user_id, post_id, now_iso()),
        )
        self.conn.commit()

    def _opt_in(self, user_id):
        self.conn.execute(
            "INSERT OR REPLACE INTO privacy_preferences (user_id, personalized_ads_opt_out) VALUES (?, 0)",
            (user_id,),
        )
        self.conn.commit()

    def _impression(self, campaign_id, creative_id, n=1):
        for _ in range(n):
            self.conn.execute(
                """
                INSERT INTO pulse_ad_impressions
                (campaign_id, creative_id, placement_key, viewer_user_id, created_at)
                VALUES (?, ?, 'feed_inline', 0, ?)
                """,
                (campaign_id, creative_id, now_iso()),
            )
        self.conn.commit()

    def _click(self, campaign_id, creative_id, n=1):
        for _ in range(n):
            self.conn.execute(
                """
                INSERT INTO pulse_ad_clicks
                (campaign_id, creative_id, placement_key, viewer_user_id, created_at)
                VALUES (?, ?, 'feed_inline', 0, ?)
                """,
                (campaign_id, creative_id, now_iso()),
            )
        self.conn.commit()

    def _custom_audience(self, account_id, kind="engaged_with_content", name="Engaged", window_days=30):
        return pulse_ads_os.create_audience(self.conn, OWNER, {
            "account_id": account_id,
            "name": name,
            "kind": kind,
            "definition": {"source": kind, "window_days": window_days},
        })

    # -- audience manager ---------------------------------------------------

    def test_custom_audience_estimate_comes_from_real_rows(self):
        account_id = self._account()
        for uid in (301, 302, 303):
            self._engagement(uid)
        audience = self._custom_audience(account_id)
        self.assertEqual(audience["kind"], "engaged_with_content")
        self.assertEqual(audience["estimated_size"], 3)
        self.assertEqual(audience["definition"]["window_days"], 30)

    def test_create_audience_ignores_client_estimated_size_for_custom_kinds(self):
        account_id = self._account()
        audience = pulse_ads_os.create_audience(self.conn, OWNER, {
            "account_id": account_id,
            "name": "Fake numbers",
            "kind": "video_viewers",
            "definition": {"source": "video_viewers", "window_days": 30},
            "estimated_size": 999999,
        })
        self.assertEqual(audience["estimated_size"], 0)

    def test_create_audience_rejects_lookalike_kind(self):
        account_id = self._account()
        with self.assertRaises(PulseAdsError):
            pulse_ads_os.create_audience(self.conn, OWNER, {
                "account_id": account_id, "name": "LAL", "kind": "lookalike",
            })

    def test_update_audience_recomputes_estimate_and_ignores_client_size(self):
        account_id = self._account()
        for uid in (311, 312):
            self._engagement(uid)
        audience = self._custom_audience(account_id)
        updated = pulse_ads_audiences.update_audience(self.conn, OWNER, audience["id"], {
            "name": "Renamed",
            "definition": {"source": "engaged_with_content", "window_days": 7},
            "estimated_size": 555555,
        })
        self.assertEqual(updated["name"], "Renamed")
        self.assertEqual(updated["definition"]["window_days"], 7)
        self.assertEqual(updated["estimated_size"], 2)
        self.assertEqual(updated["estimate"]["band"], "narrow")

    def test_audience_detail_lists_referencing_campaigns(self):
        account_id = self._account()
        self._engagement(321)
        audience = self._custom_audience(account_id)
        include_campaign = self._campaign(account_id, name="Include")
        exclude_campaign = self._campaign(account_id, name="Exclude")
        pulse_ads_os.put_targeting(self.conn, OWNER, include_campaign, {"saved_audience_ids": [audience["id"]]})
        pulse_ads_os.put_targeting(self.conn, OWNER, exclude_campaign, {"excluded_audience_ids": [audience["id"]]})
        detail = pulse_ads_audiences.audience_detail(self.conn, OWNER, audience["id"])
        roles = {ref["campaign_id"]: ref["roles"] for ref in detail["referenced_by_campaigns"]}
        self.assertEqual(roles[include_campaign], ["included"])
        self.assertEqual(roles[exclude_campaign], ["excluded"])
        self.assertEqual(detail["estimate"]["estimated_size"], 1)
        self.assertEqual(detail["estimate"]["band"], "narrow")
        self.assertTrue(detail["warnings"])

    def test_archived_audience_rejected_in_targeting_and_edit(self):
        account_id = self._account()
        audience = self._custom_audience(account_id)
        campaign_id = self._campaign(account_id)
        pulse_ads_os.archive_audience(self.conn, OWNER, audience["id"])
        with self.assertRaises(PulseAdsError) as ctx:
            pulse_ads_os.put_targeting(self.conn, OWNER, campaign_id, {"saved_audience_ids": [audience["id"]]})
        self.assertEqual(ctx.exception.status_code, 404)
        with self.assertRaises(PulseAdsError) as ctx:
            pulse_ads_audiences.update_audience(self.conn, OWNER, audience["id"], {"name": "Nope"})
        self.assertEqual(ctx.exception.status_code, 409)

    def test_audience_cross_account_denied(self):
        account_id = self._account()
        audience = self._custom_audience(account_id)
        with self.assertRaises(PulseAdsError) as ctx:
            pulse_ads_audiences.audience_detail(self.conn, STRANGER, audience["id"])
        self.assertEqual(ctx.exception.status_code, 404)
        with self.assertRaises(PulseAdsError):
            pulse_ads_audiences.update_audience(self.conn, STRANGER, audience["id"], {"name": "Steal"})

    # -- bands --------------------------------------------------------------

    def test_estimate_bands_narrow_and_broad(self):
        account_id = self._account()
        cur = self.conn.cursor()
        now = now_iso()
        # 1,600 users, 1,100 of whom follow OWNER: matched >= 1000 and
        # > 50% of the base -> broad.
        cur.executemany(
            "INSERT INTO users (user_id, country, preferred_language) VALUES (?, 'US', 'en')",
            [(1000 + i,) for i in range(1600)],
        )
        cur.executemany(
            "INSERT INTO pulse_follows (follower_user_id, followed_user_id, created_at) VALUES (?, ?, ?)",
            [(1000 + i, OWNER, now) for i in range(1100)],
        )
        self.conn.commit()
        broad = pulse_ads_audiences.estimate_for_audience(
            self.conn, OWNER, "profile_engagers", {"source": "profile_engagers", "window_days": 30})
        self.assertEqual(broad["estimated_size"], 1100)
        self.assertEqual(broad["band"], "broad")
        narrow = pulse_ads_audiences.estimate_for_audience(
            self.conn, OWNER, "video_viewers", {"source": "video_viewers", "window_days": 30})
        self.assertEqual(narrow["estimated_size"], 0)
        self.assertEqual(narrow["band"], "narrow")

    # -- lookalike ----------------------------------------------------------

    def _seed_followers(self, count, start=5000):
        cur = self.conn.cursor()
        now = now_iso()
        cur.executemany(
            "INSERT INTO users (user_id, country, preferred_language) VALUES (?, 'US', 'en')",
            [(start + i,) for i in range(count)],
        )
        cur.executemany(
            "INSERT INTO pulse_follows (follower_user_id, followed_user_id, created_at) VALUES (?, ?, ?)",
            [(start + i, OWNER, now) for i in range(count)],
        )
        self.conn.commit()

    def test_lookalike_requires_100_seed_members(self):
        account_id = self._account()
        self._seed_followers(40)
        seed = self._custom_audience(account_id, kind="profile_engagers", name="Fans")
        with self.assertRaises(PulseAdsError) as ctx:
            pulse_ads_audiences.create_lookalike(self.conn, OWNER, {
                "account_id": account_id, "seed_audience_id": seed["id"], "breadth_pct": 5,
            })
        self.assertIn("at least 100", str(ctx.exception))

    def test_lookalike_creation_banded_estimate(self):
        account_id = self._account()
        self._seed_followers(120)
        # 3,000 extra users sharing the seed's (US, en) profile who do NOT
        # follow OWNER: the reachable pool.
        cur = self.conn.cursor()
        cur.executemany(
            "INSERT INTO users (user_id, country, preferred_language) VALUES (?, 'US', 'en')",
            [(20000 + i,) for i in range(3000)],
        )
        self.conn.commit()
        seed = self._custom_audience(account_id, kind="profile_engagers", name="Fans")
        lookalike = pulse_ads_audiences.create_lookalike(self.conn, OWNER, {
            "account_id": account_id, "seed_audience_id": seed["id"], "breadth_pct": 10,
        })
        self.assertEqual(lookalike["kind"], "lookalike")
        self.assertEqual(lookalike["definition"]["seed_audience_id"], seed["id"])
        self.assertEqual(lookalike["definition"]["breadth_pct"], 10)
        self.assertEqual(lookalike["seed_count"], 120)
        # pool = 3000, 10% = 300, already a multiple of the 100 band.
        self.assertEqual(lookalike["estimated_size"], 300)
        self.assertEqual(lookalike["estimated_size"] % 100, 0)

    def test_lookalike_rejects_saved_seed_and_foreign_seed(self):
        account_id = self._account()
        saved = pulse_ads_os.create_audience(self.conn, OWNER, {
            "account_id": account_id, "name": "Manual", "kind": "saved",
            "definition": {"countries": ["US"]},
        })
        with self.assertRaises(PulseAdsError):
            pulse_ads_audiences.create_lookalike(self.conn, OWNER, {
                "account_id": account_id, "seed_audience_id": saved["id"],
            })
        stranger_account = self._account(owner=STRANGER)
        self._seed_followers(120)
        seed = self._custom_audience(account_id, kind="profile_engagers")
        with self.assertRaises(PulseAdsError) as ctx:
            pulse_ads_audiences.create_lookalike(self.conn, STRANGER, {
                "account_id": stranger_account, "seed_audience_id": seed["id"],
            })
        self.assertEqual(ctx.exception.status_code, 404)

    # -- delivery-time audience matching ------------------------------------

    def _serving_campaign(self, account_id):
        campaign_id = self._campaign(account_id)
        self._attach_placement(campaign_id)
        creative_id = self._creative(account_id, campaign_id)
        return campaign_id, creative_id

    def test_select_ads_honors_included_audience(self):
        account_id = self._account()
        self._engagement(VIEWER)
        self._user(VIEWER)
        self._user(OUTSIDER)
        self._opt_in(VIEWER)
        self._opt_in(OUTSIDER)
        audience = self._custom_audience(account_id)
        campaign_id, _ = self._serving_campaign(account_id)
        pulse_ads_os.put_targeting(self.conn, OWNER, campaign_id, {"saved_audience_ids": [audience["id"]]})
        member_ads = pulse_ads_service.select_ads(self.conn, user_id=VIEWER, context="home")
        self.assertEqual(len(member_ads), 1)
        outsider_ads = pulse_ads_service.select_ads(self.conn, user_id=OUTSIDER, context="home")
        self.assertEqual(outsider_ads, [])

    def test_select_ads_honors_excluded_audience(self):
        account_id = self._account()
        self._engagement(VIEWER)
        self._user(VIEWER)
        self._user(OUTSIDER)
        self._opt_in(VIEWER)
        self._opt_in(OUTSIDER)
        audience = self._custom_audience(account_id)
        campaign_id, _ = self._serving_campaign(account_id)
        pulse_ads_os.put_targeting(self.conn, OWNER, campaign_id, {"excluded_audience_ids": [audience["id"]]})
        member_ads = pulse_ads_service.select_ads(self.conn, user_id=VIEWER, context="home")
        self.assertEqual(member_ads, [])
        outsider_ads = pulse_ads_service.select_ads(self.conn, user_id=OUTSIDER, context="home")
        self.assertEqual(len(outsider_ads), 1)

    def test_select_ads_fail_safe_for_unevaluable_audience(self):
        account_id = self._account()
        self._user(VIEWER)
        self._opt_in(VIEWER)
        manual = pulse_ads_os.create_audience(self.conn, OWNER, {
            "account_id": account_id, "name": "Manual list", "kind": "saved",
            "definition": {"countries": ["US"]},
        })
        campaign_id, _ = self._serving_campaign(account_id)
        pulse_ads_os.put_targeting(self.conn, OWNER, campaign_id, {"saved_audience_ids": [manual["id"]]})
        # A 'saved' rule audience has no cheap per-viewer membership check:
        # include lists treat it as non-matching, so the ad is withheld.
        self.assertEqual(pulse_ads_service.select_ads(self.conn, user_id=VIEWER, context="home"), [])

    def test_select_ads_withholds_constrained_ads_from_opted_out_viewers(self):
        account_id = self._account()
        self._engagement(VIEWER)
        self._user(VIEWER)
        # VIEWER never opted in: personalized_ads_opt_out defaults to on.
        audience = self._custom_audience(account_id)
        campaign_id, _ = self._serving_campaign(account_id)
        pulse_ads_os.put_targeting(self.conn, OWNER, campaign_id, {"saved_audience_ids": [audience["id"]]})
        self.assertEqual(pulse_ads_service.select_ads(self.conn, user_id=VIEWER, context="home"), [])

    def test_select_ads_unconstrained_campaign_still_serves(self):
        account_id = self._account()
        self._serving_campaign(account_id)
        ads = pulse_ads_service.select_ads(self.conn, user_id=None, session_id="anon", context="home")
        self.assertEqual(len(ads), 1)

    # -- creative library ---------------------------------------------------

    def test_library_overview_groups_counts_and_metrics(self):
        account_id = self._account()
        campaign_id = self._campaign(account_id, name="Spring Push")
        image_id = self._creative(account_id, campaign_id, creative_type="image", title="Img")
        self._creative(account_id, campaign_id, creative_type="video", title="Vid")
        text_id = self._creative(account_id, campaign_id, creative_type="text", title="Txt")
        # Stranger's creative must never appear.
        stranger_account = self._account(owner=STRANGER)
        stranger_campaign = self._campaign(stranger_account)
        self._creative(stranger_account, stranger_campaign, title="Hidden")
        self._impression(campaign_id, image_id, n=4)
        self._click(campaign_id, image_id, n=1)
        overview = pulse_ads_library.library_overview(self.conn, OWNER)
        self.assertEqual(overview["counts"], {"all": 3, "images": 1, "videos": 1, "posts": 1})
        by_id = {item["id"]: item for item in overview["creatives"]}
        self.assertNotIn("Hidden", [item["title"] for item in overview["creatives"]])
        self.assertEqual(by_id[image_id]["performance"], {"impressions": 4, "clicks": 1, "ctr": 0.25})
        self.assertEqual(by_id[text_id]["performance"], {"impressions": 0, "clicks": 0, "ctr": 0.0})
        self.assertEqual(by_id[image_id]["campaign"]["campaign_name"], "Spring Push")
        images_only = pulse_ads_library.library_overview(self.conn, OWNER, "images")
        self.assertEqual([item["id"] for item in images_only["creatives"]], [image_id])
        with self.assertRaises(PulseAdsError):
            pulse_ads_library.library_overview(self.conn, OWNER, "bogus")

    def test_asset_detail_includes_moderation_history_and_previews(self):
        account_id = self._account()
        campaign_id = self._campaign(account_id)
        creative_id = self._creative(account_id, campaign_id, status="draft", moderation="draft")
        pulse_ads_service.submit_creative_for_review(self.conn, OWNER, creative_id)
        pulse_ads_service.reject_creative(self.conn, ADMIN, creative_id, "Broken landing URL")
        detail = pulse_ads_library.asset_detail(self.conn, OWNER, creative_id)
        sources = {entry["source"] for entry in detail["moderation_history"]}
        self.assertEqual(sources, {"moderation_queue", "review_board"})
        self.assertIn("previews", detail)
        self.assertEqual(detail["moderation_status"], "rejected")
        self.assertTrue(detail["editable"])
        with self.assertRaises(PulseAdsError) as ctx:
            pulse_ads_library.asset_detail(self.conn, STRANGER, creative_id)
        self.assertEqual(ctx.exception.status_code, 404)

    def test_metadata_edit_resets_moderation_and_requires_editable_status(self):
        account_id = self._account()
        campaign_id = self._campaign(account_id)
        rejected_id = self._creative(account_id, campaign_id, status="rejected", moderation="rejected")
        updated = pulse_ads_library.update_creative_metadata(self.conn, OWNER, rejected_id, {"title": "Fixed"})
        self.assertEqual(updated["title"], "Fixed")
        self.assertEqual(updated["status"], "draft")
        self.assertEqual(updated["moderation_status"], "draft")
        approved_id = self._creative(account_id, campaign_id)
        with self.assertRaises(PulseAdsError) as ctx:
            pulse_ads_library.update_creative_metadata(self.conn, OWNER, approved_id, {"title": "Nope"})
        self.assertEqual(ctx.exception.status_code, 409)

    def test_rejected_creative_can_be_resubmitted(self):
        account_id = self._account()
        campaign_id = self._campaign(account_id)
        creative_id = self._creative(account_id, campaign_id, status="rejected", moderation="rejected")
        pulse_ads_library.update_creative_metadata(self.conn, OWNER, creative_id, {"title": "Second try"})
        resubmitted = pulse_ads_service.submit_creative_for_review(self.conn, OWNER, creative_id)
        self.assertEqual(resubmitted["status"], "pending_review")
        self.assertEqual(resubmitted["moderation_status"], "pending")
        cur = self.conn.cursor()
        cur.execute("SELECT COUNT(*) AS n FROM pulse_ad_moderation_queue WHERE creative_id=?", (creative_id,))
        self.assertGreaterEqual(cur.fetchone()["n"], 1)

    def test_use_in_campaign_copies_and_enforces_ownership(self):
        account_id = self._account()
        source_campaign = self._campaign(account_id, name="Source")
        target_campaign = self._campaign(account_id, name="Target", status="draft")
        creative_id = self._creative(account_id, source_campaign, title="Winner")
        copy = pulse_ads_library.duplicate_creative_to_campaign(self.conn, OWNER, creative_id, target_campaign)
        self.assertEqual(copy["campaign_id"], target_campaign)
        self.assertEqual(copy["title"], "Winner copy")
        self.assertEqual(copy["status"], "draft")
        self.assertEqual(copy["moderation_status"], "draft")
        self.assertNotEqual(copy["id"], creative_id)
        # Stranger cannot copy the owner's creative anywhere.
        stranger_account = self._account(owner=STRANGER)
        stranger_campaign = self._campaign(stranger_account)
        with self.assertRaises(PulseAdsError) as ctx:
            pulse_ads_library.duplicate_creative_to_campaign(self.conn, STRANGER, creative_id, stranger_campaign)
        self.assertEqual(ctx.exception.status_code, 404)
        # Owner cannot copy into someone else's campaign.
        with self.assertRaises(PulseAdsError) as ctx:
            pulse_ads_library.duplicate_creative_to_campaign(self.conn, OWNER, creative_id, stranger_campaign)
        self.assertEqual(ctx.exception.status_code, 404)
        # Archived campaigns refuse new creatives.
        archived_campaign = self._campaign(account_id, status="archived")
        with self.assertRaises(PulseAdsError) as ctx:
            pulse_ads_library.duplicate_creative_to_campaign(self.conn, OWNER, creative_id, archived_campaign)
        self.assertEqual(ctx.exception.status_code, 409)

    def test_use_in_campaign_rejects_cross_account_copy_and_bad_adset(self):
        account_a = self._account()
        account_b = self._account()
        campaign_a = self._campaign(account_a)
        campaign_b = self._campaign(account_b)
        creative_id = self._creative(account_a, campaign_a)
        with self.assertRaises(PulseAdsError) as ctx:
            pulse_ads_library.duplicate_creative_to_campaign(self.conn, OWNER, creative_id, campaign_b)
        self.assertEqual(ctx.exception.status_code, 400)
        other_campaign = self._campaign(account_a)
        adset = pulse_ads_adsets.create_adset(self.conn, OWNER, other_campaign, {"name": "Elsewhere"})
        with self.assertRaises(PulseAdsError) as ctx:
            pulse_ads_library.duplicate_creative_to_campaign(
                self.conn, OWNER, creative_id, campaign_a, adset_id=adset["id"])
        self.assertEqual(ctx.exception.status_code, 404)
        target_adset = pulse_ads_adsets.create_adset(self.conn, OWNER, campaign_a, {"name": "Here"})
        copy = pulse_ads_library.duplicate_creative_to_campaign(
            self.conn, OWNER, creative_id, campaign_a, adset_id=target_adset["id"])
        self.assertEqual(copy["adset_id"], target_adset["id"])

    # -- policy center + appeals --------------------------------------------

    def _rejected_creative(self, account_id, campaign_id, reason="Broken destination url"):
        creative_id = self._creative(account_id, campaign_id, status="draft", moderation="draft")
        pulse_ads_service.submit_creative_for_review(self.conn, OWNER, creative_id)
        pulse_ads_service.reject_creative(self.conn, ADMIN, creative_id, reason)
        return creative_id

    def test_policy_center_components_and_appealable_flag(self):
        account_id = self._account()
        campaign_id = self._campaign(account_id)
        url_creative = self._rejected_creative(account_id, campaign_id, "Landing URL is broken")
        text_creative = self._rejected_creative(account_id, campaign_id, "Misleading claims in copy")
        pulse_ads_os.create_appeal(self.conn, OWNER, url_creative, {"message": "The URL works now."})
        center = pulse_ads_os.policy_center(self.conn, OWNER, account_id)
        self.assertEqual(center["counts"]["rejected"], 2)
        by_id = {item["id"]: item for item in center["rejected"]}
        self.assertEqual(by_id[url_creative]["affected_component"], "destination")
        self.assertEqual(by_id[text_creative]["affected_component"], "creative_text")
        self.assertFalse(by_id[url_creative]["appealable"])
        self.assertTrue(by_id[text_creative]["appealable"])
        self.assertEqual(len(center["appeals"]), 1)

    def test_appeal_create_snapshot_and_duplicate_blocked(self):
        account_id = self._account()
        campaign_id = self._campaign(account_id)
        creative_id = self._rejected_creative(account_id, campaign_id, "Blurry image asset")
        appeal = pulse_ads_os.create_appeal(self.conn, OWNER, creative_id, {"message": "Please look again."})
        self.assertEqual(appeal["status"], "open")
        self.assertEqual(appeal["appeal_type"], "creative_rejection")
        self.assertEqual(appeal["reason"]["message"], "Please look again.")
        self.assertEqual(appeal["reason"]["snapshot"]["rejection_reason"], "Blurry image asset")
        with self.assertRaises(PulseAdsError) as ctx:
            pulse_ads_os.create_appeal(self.conn, OWNER, creative_id, {"message": "Again!"})
        self.assertEqual(ctx.exception.status_code, 409)
        with self.assertRaises(PulseAdsError):
            pulse_ads_os.create_appeal(self.conn, STRANGER, creative_id, {"message": "Not mine."})

    def test_list_appeals_scopes_to_owner(self):
        account_id = self._account()
        campaign_id = self._campaign(account_id)
        creative_id = self._rejected_creative(account_id, campaign_id)
        pulse_ads_os.create_appeal(self.conn, OWNER, creative_id, {"message": "Reconsider."})
        listed = pulse_ads_os.list_appeals(self.conn, OWNER)
        self.assertEqual(len(listed["appeals"]), 1)
        scoped = pulse_ads_os.list_appeals(self.conn, OWNER, account_id)
        self.assertEqual(len(scoped["appeals"]), 1)
        self.assertEqual(pulse_ads_os.list_appeals(self.conn, STRANGER)["appeals"], [])
        with self.assertRaises(PulseAdsError):
            pulse_ads_os.list_appeals(self.conn, STRANGER, account_id)

    def test_admin_approves_appeal_and_creative_returns_to_approved(self):
        account_id = self._account()
        campaign_id = self._campaign(account_id)
        creative_id = self._rejected_creative(account_id, campaign_id)
        appeal = pulse_ads_os.create_appeal(self.conn, OWNER, creative_id, {"message": "It complies."})
        decided = pulse_ads_os.admin_decide_appeal(self.conn, ADMIN, appeal["id"], "approved", "Verified manually.")
        self.assertEqual(decided["status"], "approved")
        self.assertEqual(decided["decision"], "approved")
        self.assertEqual(decided["decision_reason"], "Verified manually.")
        creative = pulse_ads_service.get_creative(self.conn, OWNER, creative_id)
        self.assertEqual(creative["status"], "approved")
        self.assertEqual(creative["moderation_status"], "approved")
        with self.assertRaises(PulseAdsError) as ctx:
            pulse_ads_os.admin_decide_appeal(self.conn, ADMIN, appeal["id"], "rejected", "Twice?")
        self.assertEqual(ctx.exception.status_code, 409)

    def test_admin_rejects_appeal_leaves_creative_rejected(self):
        account_id = self._account()
        campaign_id = self._campaign(account_id)
        creative_id = self._rejected_creative(account_id, campaign_id)
        appeal = pulse_ads_os.create_appeal(self.conn, OWNER, creative_id, {"message": "Look again."})
        decided = pulse_ads_os.admin_decide_appeal(self.conn, ADMIN, appeal["id"], "rejected", "Still violates policy.")
        self.assertEqual(decided["status"], "rejected")
        creative = pulse_ads_service.get_creative(self.conn, OWNER, creative_id)
        self.assertEqual(creative["status"], "rejected")
        with self.assertRaises(PulseAdsError):
            pulse_ads_os.admin_decide_appeal(self.conn, ADMIN, 424242, "approved")
        with self.assertRaises(PulseAdsError):
            pulse_ads_os.admin_decide_appeal(self.conn, ADMIN, appeal["id"], "maybe")


if __name__ == "__main__":
    unittest.main()
