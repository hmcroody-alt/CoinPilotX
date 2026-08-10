"""Campaign review lifecycle: pending_review → active / rejected.

Covers the transition that did not exist: `approve_creative` decided the
creative, resume refused `pending_review`, and delivery requires `active`,
so a submitted campaign whose creatives were approved was stuck forever.
Exercises the auto-activate hook on creative approval, the blocked path
(unfunded wallet stays pending_review and the owner is told why), the admin
approve/reject actions with blocker re-checks, future `start_at` behavior
(activate now, delivery waits), and the adsets list serialization regression.
"""

import json
import os
import sqlite3
import sys
import unittest
from datetime import datetime, timedelta, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO_ROOT)

from services import pulse_ad_payments, pulse_ads_adsets, pulse_ads_service, pulse_advertiser_portal  # noqa: E402
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
ADMIN = 999


def _iso(offset_days=0):
    return (datetime.now(timezone.utc) + timedelta(days=offset_days)).replace(microsecond=0).isoformat()


class CampaignActivationTestCase(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        pulse_ads_adsets.ensure_schema(self.conn)
        pulse_ads_service.seed_placements(self.conn.cursor())
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    # -- seed helpers -------------------------------------------------------

    def _account(self, owner=OWNER, business_type="local_business"):
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

    def _campaign(self, account_id, status="draft", daily=5000, lifetime=0, name="Camp", start_at="", end_at=""):
        cur = self.conn.cursor()
        now = now_iso()
        cur.execute(
            """
            INSERT INTO pulse_ad_campaigns
            (ad_account_id, campaign_name, objective, status, budget_type, daily_budget_cents,
             lifetime_budget_cents, spent_cents, start_at, end_at, created_at, updated_at)
            VALUES (?, ?, 'awareness', ?, 'daily', ?, ?, 0, ?, ?, ?, ?)
            """,
            (account_id, name, status, daily, lifetime, start_at, end_at, now, now),
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

    def _creative(self, account_id, campaign_id, title="Ad", status="draft", moderation="draft"):
        cur = self.conn.cursor()
        now = now_iso()
        cur.execute(
            """
            INSERT INTO pulse_ad_creatives
            (ad_account_id, campaign_id, creative_type, title, body, destination_url,
             status, moderation_status, media_ready, created_at, updated_at)
            VALUES (?, ?, 'text', ?, 'Body', 'https://example.com', ?, ?, 1, ?, ?)
            """,
            (account_id, campaign_id, title, status, moderation, now, now),
        )
        self.conn.commit()
        return cur.lastrowid

    def _fund(self, account_id, cents):
        wallet = pulse_ad_payments.ensure_wallet(self.conn, account_id)
        self.conn.execute(
            "UPDATE pulse_ad_wallets SET available_balance_cents=? WHERE id=?",
            (cents, wallet["id"]),
        )
        self.conn.commit()

    def _campaign_status(self, campaign_id):
        return self.conn.execute(
            "SELECT status FROM pulse_ad_campaigns WHERE id=?", (campaign_id,)
        ).fetchone()["status"]

    def _wallet(self, account_id):
        return dict(self.conn.execute(
            "SELECT * FROM pulse_ad_wallets WHERE account_id=?", (account_id,)
        ).fetchone())

    def _notifications(self, campaign_id, kind):
        rows = self.conn.execute(
            "SELECT * FROM pulse_ad_notifications WHERE campaign_id=? AND notification_type=?",
            (campaign_id, kind),
        ).fetchall()
        return [dict(row) for row in rows]

    def _submitted_campaign(self, account_id, **campaign_kwargs):
        """Draft campaign with a placement and one creative, submitted for review."""
        campaign_id = self._campaign(account_id, **campaign_kwargs)
        self._attach_placement(campaign_id)
        creative_id = self._creative(account_id, campaign_id)
        pulse_advertiser_portal.campaign_action(self.conn, OWNER, campaign_id, "submit")
        pulse_ads_service.submit_creative_for_review(self.conn, OWNER, creative_id)
        self.assertEqual(self._campaign_status(campaign_id), "pending_review")
        return campaign_id, creative_id

    # -- auto-activation on creative approval -------------------------------

    def test_creative_approval_auto_activates_eligible_campaign(self):
        account_id = self._account()
        self._fund(account_id, 10000)
        campaign_id, creative_id = self._submitted_campaign(account_id)

        result = pulse_ads_service.approve_creative(self.conn, ADMIN, creative_id)
        self.assertEqual(result["moderation_status"], "approved")
        activation = result.get("campaign_activation")
        self.assertIsNotNone(activation)
        self.assertEqual(activation["status"], "active")
        self.assertEqual(activation["campaign_id"], campaign_id)

        self.assertEqual(self._campaign_status(campaign_id), "active")
        # Budget reserved exactly the way resume reserves it: min(budget, 50000).
        self.assertEqual(self._wallet(account_id)["reserved_budget_cents"], 5000)
        # Owner is notified, activation is audited.
        self.assertEqual(len(self._notifications(campaign_id, "campaign_approved")), 1)
        audit = self.conn.execute(
            "SELECT COUNT(*) AS n FROM pulse_ad_audit_logs WHERE action='ad_campaign_approved' AND entity_id=?",
            (str(campaign_id),),
        ).fetchone()["n"]
        self.assertEqual(audit, 1)
        # And the campaign now actually delivers.
        ads = pulse_ads_service.select_ads(self.conn, context="home", device_type="desktop")
        self.assertTrue(any(ad.get("campaign_id") == campaign_id for ad in ads))

    def test_auto_activation_waits_for_undecided_sibling_creatives(self):
        account_id = self._account()
        self._fund(account_id, 10000)
        campaign_id, creative_a = self._submitted_campaign(account_id)
        creative_b = self._creative(account_id, campaign_id, title="Second")
        pulse_ads_service.submit_creative_for_review(self.conn, OWNER, creative_b)
        # An archived creative never gates activation.
        self._creative(account_id, campaign_id, title="Old", status="archived", moderation="draft")

        result = pulse_ads_service.approve_creative(self.conn, ADMIN, creative_a)
        self.assertNotIn("campaign_activation", result)
        self.assertEqual(self._campaign_status(campaign_id), "pending_review")

        result = pulse_ads_service.approve_creative(self.conn, ADMIN, creative_b)
        self.assertEqual(result["campaign_activation"]["status"], "active")
        self.assertEqual(self._campaign_status(campaign_id), "active")

    def test_blocked_unfunded_campaign_stays_pending_review_with_notification(self):
        account_id = self._account()
        campaign_id, creative_id = self._submitted_campaign(account_id)

        # No spendable balance at all: gate blocks before any money moves.
        result = pulse_ads_service.approve_creative(self.conn, ADMIN, creative_id)
        activation = result["campaign_activation"]
        self.assertEqual(activation["status"], "pending_review")
        self.assertEqual(activation["blocked_by"], "wallet_insufficient")
        self.assertEqual(self._campaign_status(campaign_id), "pending_review")
        self.assertEqual(self._wallet(account_id)["reserved_budget_cents"], 0)
        notes = self._notifications(campaign_id, "campaign_activation_blocked")
        self.assertEqual(len(notes), 1)
        self.assertIn("wallet", notes[0]["body"].lower())

        # Nonzero but below the reserve threshold: reserve refuses, still blocked.
        self._fund(account_id, 100)
        result = pulse_ads_service.approve_creative(self.conn, ADMIN, creative_id)
        activation = result["campaign_activation"]
        self.assertEqual(activation["status"], "pending_review")
        self.assertEqual(self._campaign_status(campaign_id), "pending_review")
        self.assertEqual(self._wallet(account_id)["reserved_budget_cents"], 0)

        # Funding the wallet and re-approving completes the activation.
        self._fund(account_id, 10000)
        result = pulse_ads_service.approve_creative(self.conn, ADMIN, creative_id)
        self.assertEqual(result["campaign_activation"]["status"], "active")
        self.assertEqual(self._campaign_status(campaign_id), "active")
        self.assertEqual(self._wallet(account_id)["reserved_budget_cents"], 5000)

    # -- admin approve ------------------------------------------------------

    def test_admin_approve_campaign_activates_and_reserves(self):
        account_id = self._account()
        self._fund(account_id, 10000)
        campaign_id = self._campaign(account_id, status="pending_review")
        self._attach_placement(campaign_id)
        self._creative(account_id, campaign_id, status="approved", moderation="approved")

        result = pulse_advertiser_portal.approve_campaign(self.conn, ADMIN, campaign_id)
        self.assertEqual(result["status"], "active")
        self.assertEqual(self._campaign_status(campaign_id), "active")
        self.assertEqual(self._wallet(account_id)["reserved_budget_cents"], 5000)
        self.assertEqual(len(self._notifications(campaign_id, "campaign_approved")), 1)
        row = self.conn.execute(
            "SELECT approved_at FROM pulse_ad_campaigns WHERE id=?", (campaign_id,)
        ).fetchone()
        self.assertTrue(row["approved_at"])

        # A second press is idempotent, not an error, and reserves nothing more.
        again = pulse_advertiser_portal.approve_campaign(self.conn, ADMIN, campaign_id)
        self.assertEqual(again["status"], "active")
        self.assertTrue(again.get("already_active"))
        self.assertEqual(self._wallet(account_id)["reserved_budget_cents"], 5000)

    def test_admin_approve_re_checks_blockers_and_status(self):
        account_id = self._account()
        self._fund(account_id, 10000)

        # Not pending_review: refused.
        draft_id = self._campaign(account_id, status="draft")
        with self.assertRaises(PulseAdsError) as ctx:
            pulse_advertiser_portal.approve_campaign(self.conn, ADMIN, draft_id)
        self.assertEqual(ctx.exception.status_code, 409)

        # Pending but no placement: the activation blockers are enforced.
        campaign_id = self._campaign(account_id, status="pending_review")
        self._creative(account_id, campaign_id, status="approved", moderation="approved")
        with self.assertRaises(PulseAdsError) as ctx:
            pulse_advertiser_portal.approve_campaign(self.conn, ADMIN, campaign_id)
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertIn("placement", str(ctx.exception).lower())
        self.assertEqual(self._campaign_status(campaign_id), "pending_review")
        self.assertEqual(self._wallet(account_id)["reserved_budget_cents"], 0)

        # Pending but creative undecided: also refused.
        other_id = self._campaign(account_id, status="pending_review", name="Other")
        self._attach_placement(other_id)
        self._creative(account_id, other_id, status="pending_review", moderation="pending")
        with self.assertRaises(PulseAdsError) as ctx:
            pulse_advertiser_portal.approve_campaign(self.conn, ADMIN, other_id)
        self.assertEqual(ctx.exception.status_code, 409)

        # Missing campaign: 404.
        with self.assertRaises(PulseAdsError) as ctx:
            pulse_advertiser_portal.approve_campaign(self.conn, ADMIN, 424242)
        self.assertEqual(ctx.exception.status_code, 404)

    # -- admin reject -------------------------------------------------------

    def test_admin_reject_requires_reason_and_notifies(self):
        account_id = self._account()
        campaign_id, _creative_id = self._submitted_campaign(account_id)

        with self.assertRaises(PulseAdsError):
            pulse_advertiser_portal.reject_campaign(self.conn, ADMIN, campaign_id, "")

        result = pulse_advertiser_portal.reject_campaign(self.conn, ADMIN, campaign_id, "Landing page is broken.")
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(self._campaign_status(campaign_id), "rejected")
        notes = self._notifications(campaign_id, "campaign_rejected")
        self.assertEqual(len(notes), 1)
        self.assertIn("Landing page is broken.", notes[0]["body"])
        audit = self.conn.execute(
            "SELECT COUNT(*) AS n FROM pulse_ad_audit_logs WHERE action='ad_campaign_rejected' AND entity_id=?",
            (str(campaign_id),),
        ).fetchone()["n"]
        self.assertEqual(audit, 1)

        # Idempotent second reject; and the advertiser can resubmit from rejected.
        again = pulse_advertiser_portal.reject_campaign(self.conn, ADMIN, campaign_id, "Still broken.")
        self.assertTrue(again.get("already_rejected"))
        pulse_advertiser_portal.campaign_action(self.conn, OWNER, campaign_id, "submit")
        self.assertEqual(self._campaign_status(campaign_id), "pending_review")

        # Rejecting an active campaign is refused (suspend is the tool for that).
        active_id = self._campaign(account_id, status="active", name="Live")
        with self.assertRaises(PulseAdsError) as ctx:
            pulse_advertiser_portal.reject_campaign(self.conn, ADMIN, active_id, "Nope.")
        self.assertEqual(ctx.exception.status_code, 409)

    # -- future start_at ----------------------------------------------------

    def test_future_start_at_activates_now_but_delivery_waits(self):
        account_id = self._account()
        self._fund(account_id, 10000)
        future = _iso(offset_days=2)
        campaign_id = self._campaign(account_id, status="pending_review", start_at=future, name="Scheduled")
        self._attach_placement(campaign_id)
        self._creative(account_id, campaign_id, status="approved", moderation="approved")

        # No `scheduled` status exists: approval activates immediately …
        result = pulse_advertiser_portal.approve_campaign(self.conn, ADMIN, campaign_id)
        self.assertEqual(result["status"], "active")
        self.assertEqual(self._campaign_status(campaign_id), "active")
        # … and the owner's notification says delivery is scheduled, not live.
        note = self._notifications(campaign_id, "campaign_approved")[0]
        self.assertIn("scheduled start", note["body"])

        # select_ads already gates on start_at, so the active campaign does not serve yet.
        ads = pulse_ads_service.select_ads(self.conn, context="home", device_type="desktop")
        self.assertFalse(any(ad.get("campaign_id") == campaign_id for ad in ads))

        # Once start_at passes, the same campaign serves without another transition.
        self.conn.execute(
            "UPDATE pulse_ad_campaigns SET start_at=? WHERE id=?", (_iso(offset_days=-1), campaign_id)
        )
        self.conn.commit()
        ads = pulse_ads_service.select_ads(self.conn, context="home", device_type="desktop")
        self.assertTrue(any(ad.get("campaign_id") == campaign_id for ad in ads))

        # A campaign past its end_at does not serve either.
        self.conn.execute(
            "UPDATE pulse_ad_campaigns SET end_at=? WHERE id=?", (_iso(offset_days=-1), campaign_id)
        )
        self.conn.commit()
        ads = pulse_ads_service.select_ads(self.conn, context="home", device_type="desktop")
        self.assertFalse(any(ad.get("campaign_id") == campaign_id for ad in ads))

    # -- adsets list serialization regression --------------------------------

    def test_adsets_list_endpoint_serialization(self):
        account_id = self._account()
        campaign_id = self._campaign(account_id, status="active")
        pulse_ads_adsets.create_adset(self.conn, OWNER, campaign_id, {"name": "Set A"})

        adsets = pulse_ads_adsets.list_adsets(self.conn, OWNER, campaign_id)
        self.assertIsInstance(adsets, list)
        # The shape the fixed handler returns must be JSON-serializable.
        json.dumps({"ok": True, "adsets": adsets})
        # The old handler spread the list with ** and raised TypeError on every GET.
        with self.assertRaises(TypeError):
            dict(ok=True, **adsets)

        # Regression guard on the route itself: keyed serialization, no list spread.
        with open(os.path.join(REPO_ROOT, "bot.py"), "r", encoding="utf-8", errors="ignore") as handle:
            source = handle.read()
        self.assertNotIn("**pulse_ads_adsets.list_adsets", source)
        self.assertIn('"adsets": pulse_ads_adsets.list_adsets', source)


if __name__ == "__main__":
    unittest.main()
