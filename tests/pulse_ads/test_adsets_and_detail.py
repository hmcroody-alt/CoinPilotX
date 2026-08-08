"""Ad sets, campaign detail, and server-side drafts.

Covers the ad-set slice end to end against the real service modules:
default ad set backfill, ad set CRUD and the delivery-eligibility rule
(paused/archived ad sets do not serve), creative assignment, campaign
detail metrics from seeded impressions/clicks/spend, draft upsert
idempotency, drafts never touching money, and cross-account isolation.
"""

import json
import os
import sqlite3
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services import pulse_ads_adsets, pulse_ads_os, pulse_ads_service  # noqa: E402
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
"""

OWNER = 101
STRANGER = 202


class AdsetsAndDetailTestCase(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        # Exercise the real DDL/ALTER path (adds pulse_ad_adsets,
        # pulse_ad_creatives.adset_id, pulse_ad_campaigns.draft_key, indexes).
        pulse_ads_adsets.ensure_schema(self.conn)
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

    def _campaign(self, account_id, status="active", objective="awareness", daily=0, lifetime=0, name="Camp"):
        cur = self.conn.cursor()
        now = now_iso()
        cur.execute(
            """
            INSERT INTO pulse_ad_campaigns
            (ad_account_id, campaign_name, objective, status, budget_type, daily_budget_cents,
             lifetime_budget_cents, spent_cents, start_at, end_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'daily', ?, ?, 0, '', '', ?, ?)
            """,
            (account_id, name, objective, status, daily, lifetime, now, now),
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

    def _creative(self, account_id, campaign_id, adset_id=None, title="Ad", approved=True):
        cur = self.conn.cursor()
        now = now_iso()
        cur.execute(
            """
            INSERT INTO pulse_ad_creatives
            (ad_account_id, campaign_id, creative_type, title, body, destination_url,
             status, moderation_status, media_ready, adset_id, created_at, updated_at)
            VALUES (?, ?, 'text', ?, 'Body', 'https://example.com',
                    ?, ?, 1, ?, ?, ?)
            """,
            (
                account_id, campaign_id, title,
                "approved" if approved else "draft",
                "approved" if approved else "draft",
                adset_id, now, now,
            ),
        )
        self.conn.commit()
        return cur.lastrowid

    def _impression(self, campaign_id, creative_id, created_at=None):
        self.conn.execute(
            """
            INSERT INTO pulse_ad_impressions (campaign_id, creative_id, placement_key, created_at)
            VALUES (?, ?, 'feed_inline', ?)
            """,
            (campaign_id, creative_id, created_at or now_iso()),
        )
        self.conn.commit()

    def _click(self, campaign_id, creative_id, created_at=None):
        self.conn.execute(
            """
            INSERT INTO pulse_ad_clicks (campaign_id, creative_id, placement_key, created_at)
            VALUES (?, ?, 'feed_inline', ?)
            """,
            (campaign_id, creative_id, created_at or now_iso()),
        )
        self.conn.commit()

    def _spend(self, account_id, campaign_id, creative_id, cents, idem):
        self.conn.execute(
            """
            INSERT INTO pulse_ad_wallet_transactions
            (account_id, campaign_id, creative_id, transaction_type, amount_cents, status, idempotency_key, created_at)
            VALUES (?, ?, ?, 'spend', ?, 'posted', ?, ?)
            """,
            (account_id, campaign_id, creative_id, cents, idem, now_iso()),
        )
        self.conn.commit()

    # -- default ad set -----------------------------------------------------

    def test_default_adset_backfill_snapshots_targeting(self):
        account_id = self._account()
        campaign_id = self._campaign(account_id)
        now = now_iso()
        self.conn.execute(
            """
            INSERT INTO pulse_ad_targeting
            (campaign_id, countries_json, min_age, max_age, created_at, updated_at)
            VALUES (?, ?, 18, 45, ?, ?)
            """,
            (campaign_id, json.dumps(["US", "CA"]), now, now),
        )
        self.conn.commit()
        adsets = pulse_ads_adsets.list_adsets(self.conn, OWNER, campaign_id)
        self.assertEqual(len(adsets), 1)
        default = adsets[0]
        self.assertTrue(default["is_default"])
        self.assertEqual(default["status"], "active")
        self.assertEqual(default["name"], "US, CA — 18-45")
        self.assertEqual(default["targeting"].get("countries"), ["US", "CA"])
        # Idempotent: a second listing does not create a second default.
        adsets = pulse_ads_adsets.list_adsets(self.conn, OWNER, campaign_id)
        self.assertEqual(len(adsets), 1)
        # A concurrent double-backfill also converges on one row.
        again = pulse_ads_adsets.ensure_default_adset(self.conn, campaign_id)
        self.assertEqual(again["id"], default["id"])

    def test_default_adset_name_falls_back_to_all(self):
        account_id = self._account()
        campaign_id = self._campaign(account_id)
        adsets = pulse_ads_adsets.list_adsets(self.conn, OWNER, campaign_id)
        self.assertEqual(adsets[0]["name"], "All")

    # -- CRUD ---------------------------------------------------------------

    def test_adset_crud_lifecycle(self):
        account_id = self._account()
        campaign_id = self._campaign(account_id)
        created = pulse_ads_adsets.create_adset(
            self.conn, OWNER, campaign_id,
            {"name": "Miami — 18-45", "targeting": {"countries": ["us"], "min_age": 18, "max_age": 45}},
        )
        self.assertEqual(created["name"], "Miami — 18-45")
        self.assertEqual(created["status"], "active")
        self.assertFalse(created["is_default"])
        self.assertEqual(created["targeting"]["countries"], ["US"])

        updated = pulse_ads_adsets.update_adset(self.conn, OWNER, created["id"], {"name": "Renamed"})
        self.assertEqual(updated["name"], "Renamed")

        paused = pulse_ads_adsets.adset_action(self.conn, OWNER, created["id"], "pause")
        self.assertEqual(paused["status"], "paused")
        # Idempotent repeat.
        paused = pulse_ads_adsets.adset_action(self.conn, OWNER, created["id"], "pause")
        self.assertEqual(paused["status"], "paused")
        resumed = pulse_ads_adsets.adset_action(self.conn, OWNER, created["id"], "resume")
        self.assertEqual(resumed["status"], "active")
        archived = pulse_ads_adsets.adset_action(self.conn, OWNER, created["id"], "archive")
        self.assertEqual(archived["status"], "archived")
        # Archived is terminal.
        with self.assertRaises(PulseAdsError) as ctx:
            pulse_ads_adsets.adset_action(self.conn, OWNER, created["id"], "resume")
        self.assertEqual(ctx.exception.status_code, 409)

        # The default ad set can never be archived.
        default = [a for a in pulse_ads_adsets.list_adsets(self.conn, OWNER, campaign_id) if a["is_default"]][0]
        with self.assertRaises(PulseAdsError):
            pulse_ads_adsets.adset_action(self.conn, OWNER, default["id"], "archive")
        with self.assertRaises(PulseAdsError):
            pulse_ads_adsets.update_adset(self.conn, OWNER, default["id"], {"status": "archived"})

    def test_adset_cap_per_campaign(self):
        account_id = self._account()
        campaign_id = self._campaign(account_id)
        pulse_ads_adsets.ensure_default_adset(self.conn, campaign_id)
        self.conn.commit()
        for index in range(pulse_ads_adsets.MAX_ADSETS_PER_CAMPAIGN - 1):
            pulse_ads_adsets.create_adset(self.conn, OWNER, campaign_id, {"name": f"Set {index}"})
        with self.assertRaises(PulseAdsError) as ctx:
            pulse_ads_adsets.create_adset(self.conn, OWNER, campaign_id, {"name": "One too many"})
        self.assertEqual(ctx.exception.status_code, 409)

    def test_locked_campaign_blocks_adset_writes(self):
        account_id = self._account()
        campaign_id = self._campaign(account_id, status="active")
        adset = pulse_ads_adsets.create_adset(self.conn, OWNER, campaign_id, {"name": "Set"})
        self.conn.execute("UPDATE pulse_ad_campaigns SET status='completed' WHERE id=?", (campaign_id,))
        self.conn.commit()
        with self.assertRaises(PulseAdsError) as ctx:
            pulse_ads_adsets.update_adset(self.conn, OWNER, adset["id"], {"name": "Nope"})
        self.assertEqual(ctx.exception.status_code, 409)
        with self.assertRaises(PulseAdsError):
            pulse_ads_adsets.create_adset(self.conn, OWNER, campaign_id, {"name": "Nope"})
        with self.assertRaises(PulseAdsError):
            pulse_ads_adsets.adset_action(self.conn, OWNER, adset["id"], "pause")

    # -- delivery eligibility ----------------------------------------------

    def test_paused_adset_excluded_from_delivery(self):
        account_id = self._account()
        campaign_id = self._campaign(account_id, daily=10000)
        self._attach_placement(campaign_id, "feed_inline")
        adset = pulse_ads_adsets.create_adset(self.conn, OWNER, campaign_id, {"name": "Set A"})
        creative_id = self._creative(account_id, campaign_id, adset_id=adset["id"])

        ads = pulse_ads_service.select_ads(self.conn, context="home", device_type="desktop")
        self.assertTrue(any(ad.get("creative_id") == creative_id for ad in ads))

        pulse_ads_adsets.adset_action(self.conn, OWNER, adset["id"], "pause")
        ads = pulse_ads_service.select_ads(self.conn, context="home", device_type="desktop")
        self.assertFalse(any(ad.get("creative_id") == creative_id for ad in ads))

        # NULL adset_id (default ad set) stays eligible.
        default_creative = self._creative(account_id, campaign_id, adset_id=None, title="Default ad")
        ads = pulse_ads_service.select_ads(self.conn, context="home", device_type="desktop")
        self.assertTrue(any(ad.get("creative_id") == default_creative for ad in ads))

        # Resuming brings the ad set's creative back.
        pulse_ads_adsets.adset_action(self.conn, OWNER, adset["id"], "resume")
        ads = pulse_ads_service.select_ads(self.conn, context="home", device_type="desktop", limit=10)
        self.assertTrue(any(ad.get("creative_id") == creative_id for ad in ads))

    # -- creative assignment ------------------------------------------------

    def test_assign_creative_between_adsets(self):
        account_id = self._account()
        campaign_id = self._campaign(account_id)
        other_campaign = self._campaign(account_id, name="Other")
        adset = pulse_ads_adsets.create_adset(self.conn, OWNER, campaign_id, {"name": "Set A"})
        foreign_adset = pulse_ads_adsets.create_adset(self.conn, OWNER, other_campaign, {"name": "Set B"})
        creative_id = self._creative(account_id, campaign_id)

        result = pulse_ads_adsets.assign_creative(self.conn, OWNER, creative_id, adset["id"])
        self.assertEqual(result["adset_id"], adset["id"])
        row = self.conn.execute("SELECT adset_id FROM pulse_ad_creatives WHERE id=?", (creative_id,)).fetchone()
        self.assertEqual(row["adset_id"], adset["id"])

        # Back to the default ad set (adset_id NULL).
        result = pulse_ads_adsets.assign_creative(self.conn, OWNER, creative_id, 0)
        self.assertIsNone(result["adset_id"])
        row = self.conn.execute("SELECT adset_id FROM pulse_ad_creatives WHERE id=?", (creative_id,)).fetchone()
        self.assertIsNone(row["adset_id"])

        # Cross-campaign assignment is rejected.
        with self.assertRaises(PulseAdsError) as ctx:
            pulse_ads_adsets.assign_creative(self.conn, OWNER, creative_id, foreign_adset["id"])
        self.assertEqual(ctx.exception.status_code, 400)

        # Archived ad set cannot receive creatives.
        pulse_ads_adsets.adset_action(self.conn, OWNER, adset["id"], "archive")
        with self.assertRaises(PulseAdsError) as ctx:
            pulse_ads_adsets.assign_creative(self.conn, OWNER, creative_id, adset["id"])
        self.assertEqual(ctx.exception.status_code, 409)

    # -- campaign detail ----------------------------------------------------

    def test_campaign_detail_metrics(self):
        account_id = self._account()
        campaign_id = self._campaign(account_id, daily=5000, objective="awareness")
        self._attach_placement(campaign_id, "feed_inline")
        adset = pulse_ads_adsets.create_adset(self.conn, OWNER, campaign_id, {"name": "Set A"})
        creative_a = self._creative(account_id, campaign_id, adset_id=adset["id"], title="A")
        creative_b = self._creative(account_id, campaign_id, adset_id=None, title="B")
        for _ in range(5):
            self._impression(campaign_id, creative_a)
        for _ in range(3):
            self._impression(campaign_id, creative_b)
        self._click(campaign_id, creative_a)
        self._click(campaign_id, creative_b)
        self._spend(account_id, campaign_id, creative_a, 120, "sp-1")
        self._spend(account_id, campaign_id, creative_b, 80, "sp-2")

        detail = pulse_ads_adsets.campaign_detail(self.conn, OWNER, campaign_id)
        self.assertEqual(detail["campaign"]["id"], campaign_id)
        self.assertEqual(detail["lifecycle"]["status"], "active")
        self.assertTrue(detail["lifecycle"]["can_edit"])
        self.assertEqual(detail["budget"]["daily_budget_cents"], 5000)
        self.assertEqual(detail["budget"]["remaining_cents"], 5000)
        self.assertEqual(detail["placements"], ["feed_inline"])

        self.assertEqual(detail["totals"]["impressions"], 8)
        self.assertEqual(detail["totals"]["clicks"], 2)
        self.assertEqual(detail["totals"]["spend_cents"], 200)
        self.assertEqual(detail["totals"]["ctr"], 0.25)

        by_creative = {c["id"]: c["metrics"] for c in detail["creatives"]}
        self.assertEqual(by_creative[creative_a]["impressions"], 5)
        self.assertEqual(by_creative[creative_a]["clicks"], 1)
        self.assertEqual(by_creative[creative_a]["spend_cents"], 120)
        self.assertEqual(by_creative[creative_b]["impressions"], 3)

        by_adset = {a["id"]: a for a in detail["adsets"]}
        self.assertEqual(by_adset[adset["id"]]["metrics"]["impressions"], 5)
        self.assertEqual(by_adset[adset["id"]]["metrics"]["spend_cents"], 120)
        default = [a for a in detail["adsets"] if a["is_default"]][0]
        self.assertEqual(default["metrics"]["impressions"], 3)
        self.assertEqual(default["metrics"]["spend_cents"], 80)

        # Daily series: 7 days, today carries the seeded counts.
        self.assertEqual(len(detail["daily_series"]), 7)
        today = now_iso()[:10]
        today_bucket = [d for d in detail["daily_series"] if d["date"] == today][0]
        self.assertEqual(today_bucket["impressions"], 8)
        self.assertEqual(today_bucket["clicks"], 2)
        self.assertEqual(today_bucket["spend_cents"], 200)

        # Awareness maps to impressions and there are real impressions.
        self.assertEqual(detail["estimated_results"], {"metric": "impressions", "value": 8})

    def test_campaign_detail_omits_estimated_results_without_data(self):
        account_id = self._account()
        campaign_id = self._campaign(account_id, objective="awareness")
        detail = pulse_ads_adsets.campaign_detail(self.conn, OWNER, campaign_id)
        self.assertNotIn("estimated_results", detail)
        # Objective with no honestly derivable metric omits the field too.
        other = self._campaign(account_id, objective="lead_generation", name="Leads")
        creative = self._creative(account_id, other, title="L")
        self._impression(other, creative)
        detail = pulse_ads_adsets.campaign_detail(self.conn, OWNER, other)
        self.assertNotIn("estimated_results", detail)

    # -- drafts -------------------------------------------------------------

    def _draft_payload(self, account_id, key="draft-abc", name="My draft"):
        return {
            "draft_key": key,
            "ad_account_id": account_id,
            "campaign": {"campaign_name": name, "objective": "awareness", "daily_budget_cents": 500},
            "targeting": {"countries": ["US"], "min_age": 18},
            "placements": ["feed_inline"],
        }

    def test_draft_upsert_idempotent(self):
        account_id = self._account()
        first = pulse_ads_os.save_campaign_draft(self.conn, OWNER, self._draft_payload(account_id))
        self.assertEqual(first["status"], "draft")
        campaign_id = first["campaign"]["id"]
        self.assertEqual(first["campaign"]["draft_key"], "draft-abc")

        # Same draft_key again: same campaign, updated fields, still one row.
        second = pulse_ads_os.save_campaign_draft(
            self.conn, OWNER, self._draft_payload(account_id, name="Renamed draft")
        )
        self.assertEqual(second["campaign"]["id"], campaign_id)
        self.assertEqual(second["campaign"]["campaign_name"], "Renamed draft")
        count = self.conn.execute(
            "SELECT COUNT(*) AS n FROM pulse_ad_campaigns WHERE ad_account_id=? AND draft_key='draft-abc'",
            (account_id,),
        ).fetchone()["n"]
        self.assertEqual(count, 1)
        self.assertEqual(second["campaign"]["status"], "draft")
        self.assertEqual(second["targeting"]["countries"], ["US"])
        self.assertEqual(second["campaign"]["placements"], ["feed_inline"])

        # A different key creates a different draft.
        third = pulse_ads_os.save_campaign_draft(
            self.conn, OWNER, self._draft_payload(account_id, key="draft-def", name="Other")
        )
        self.assertNotEqual(third["campaign"]["id"], campaign_id)

    def test_draft_requires_key_and_creative_dedupe(self):
        account_id = self._account()
        with self.assertRaises(PulseAdsError):
            pulse_ads_os.save_campaign_draft(self.conn, OWNER, {"ad_account_id": account_id})

        payload = self._draft_payload(account_id)
        payload["creative"] = {"creative_type": "text", "title": "Hello", "destination_url": "https://example.com"}
        first = pulse_ads_os.save_campaign_draft(self.conn, OWNER, payload)
        self.assertIsNotNone(first["creative"])
        campaign_id = first["campaign"]["id"]
        # Autosave retry with the creative again does not create a second one.
        pulse_ads_os.save_campaign_draft(self.conn, OWNER, payload)
        count = self.conn.execute(
            "SELECT COUNT(*) AS n FROM pulse_ad_creatives WHERE campaign_id=?", (campaign_id,)
        ).fetchone()["n"]
        self.assertEqual(count, 1)

    def test_draft_never_reserves_budget_or_charges(self):
        account_id = self._account()
        self.conn.execute(
            "INSERT INTO pulse_ad_wallets (account_id, available_balance_cents, created_at, updated_at) VALUES (?, 10000, ?, ?)",
            (account_id, now_iso(), now_iso()),
        )
        self.conn.commit()
        result = pulse_ads_os.save_campaign_draft(self.conn, OWNER, self._draft_payload(account_id))
        self.assertEqual(result["campaign"]["status"], "draft")
        wallet = self.conn.execute(
            "SELECT reserved_budget_cents, available_balance_cents FROM pulse_ad_wallets WHERE account_id=?",
            (account_id,),
        ).fetchone()
        self.assertEqual(wallet["reserved_budget_cents"], 0)
        self.assertEqual(wallet["available_balance_cents"], 10000)
        tx_count = self.conn.execute(
            "SELECT COUNT(*) AS n FROM pulse_ad_wallet_transactions WHERE account_id=?", (account_id,)
        ).fetchone()["n"]
        self.assertEqual(tx_count, 0)
        # Never submitted for review either.
        self.assertEqual(self.conn.execute("SELECT COUNT(*) AS n FROM pulse_ad_review_board").fetchone()["n"], 0)

    def test_submitted_draft_key_can_no_longer_autosave(self):
        account_id = self._account()
        saved = pulse_ads_os.save_campaign_draft(self.conn, OWNER, self._draft_payload(account_id))
        self.conn.execute(
            "UPDATE pulse_ad_campaigns SET status='pending_review' WHERE id=?", (saved["campaign"]["id"],)
        )
        self.conn.commit()
        with self.assertRaises(PulseAdsError) as ctx:
            pulse_ads_os.save_campaign_draft(self.conn, OWNER, self._draft_payload(account_id))
        self.assertEqual(ctx.exception.status_code, 409)

    def test_list_campaign_drafts(self):
        account_id = self._account()
        pulse_ads_os.save_campaign_draft(self.conn, OWNER, self._draft_payload(account_id))
        pulse_ads_os.save_campaign_draft(self.conn, OWNER, self._draft_payload(account_id, key="draft-2", name="Two"))
        # A non-draft campaign never shows up.
        self._campaign(account_id, status="active", name="Live one")
        listing = pulse_ads_os.list_campaign_drafts(self.conn, OWNER)
        keys = {d["draft_key"] for d in listing["drafts"]}
        self.assertEqual(keys, {"draft-abc", "draft-2"})
        for draft in listing["drafts"]:
            self.assertEqual(draft["status"], "draft")
            self.assertIn("targeting", draft)
            self.assertIn("creative_count", draft)

    # -- isolation ----------------------------------------------------------

    def test_cross_account_access_denied(self):
        account_id = self._account(owner=OWNER)
        campaign_id = self._campaign(account_id)
        adset = pulse_ads_adsets.create_adset(self.conn, OWNER, campaign_id, {"name": "Mine"})
        creative_id = self._creative(account_id, campaign_id)

        for call in (
            lambda: pulse_ads_adsets.list_adsets(self.conn, STRANGER, campaign_id),
            lambda: pulse_ads_adsets.create_adset(self.conn, STRANGER, campaign_id, {"name": "Theirs"}),
            lambda: pulse_ads_adsets.update_adset(self.conn, STRANGER, adset["id"], {"name": "Theirs"}),
            lambda: pulse_ads_adsets.adset_action(self.conn, STRANGER, adset["id"], "pause"),
            lambda: pulse_ads_adsets.assign_creative(self.conn, STRANGER, creative_id, adset["id"]),
            lambda: pulse_ads_adsets.campaign_detail(self.conn, STRANGER, campaign_id),
            lambda: pulse_ads_os.save_campaign_draft(
                self.conn, STRANGER, self._draft_payload(account_id, key="steal")
            ),
        ):
            with self.assertRaises(PulseAdsError) as ctx:
                call()
            self.assertIn(ctx.exception.status_code, (403, 404))

        # The stranger's draft listing does not leak the owner's drafts.
        pulse_ads_os.save_campaign_draft(self.conn, OWNER, self._draft_payload(account_id))
        listing = pulse_ads_os.list_campaign_drafts(self.conn, STRANGER)
        self.assertEqual(listing["drafts"], [])

        # A viewer-role team member can read but not write.
        self.conn.execute(
            "INSERT INTO pulse_ad_team_members (account_id, user_id, role, status, created_at, updated_at) VALUES (?, ?, 'viewer', 'active', ?, ?)",
            (account_id, STRANGER, now_iso(), now_iso()),
        )
        self.conn.commit()
        adsets = pulse_ads_adsets.list_adsets(self.conn, STRANGER, campaign_id)
        self.assertTrue(adsets)
        with self.assertRaises(PulseAdsError) as ctx:
            pulse_ads_adsets.create_adset(self.conn, STRANGER, campaign_id, {"name": "Still no"})
        self.assertEqual(ctx.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
