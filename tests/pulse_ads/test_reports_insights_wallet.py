"""Backend slice 3: advertiser reporting, insights, and wallet completion.

Covers, against the real service modules:
- build_report campaign/placement/date breakdowns with hand-computed numbers
  from seeded impressions/clicks/spend, totals row, and drill ids
- objective-aware results (traffic-like -> clicks, video_views -> video_start
  events, marketplace_sales -> attributed purchases) and honest
  results_available=False where no result stream exists
- attribute_purchases: last-click wins, idempotent recompute, 7-day window
  boundary, paid-status filter; ROAS math in report + attribution_status
- build_insights: six rule kinds triggered by data crossing the real
  thresholds, each with a data-backed why; empty-data status; apply_insight
  approve gate, recompute-staleness, whitelist, and audit trail
- wallet completion: spending limits (owner-only, enforced by
  record_spend_event), funding invoice written + idempotent, auto top-up
  roundtrip and needs_topup flip, list_transactions cursor pagination
- ledger reconciliation invariant across funding -> reserve -> spend -> refund
- cross-account denial for reports, insights, and wallet functions
"""

import json
import os
import sqlite3
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services import (  # noqa: E402
    pulse_ad_payments,
    pulse_ads_insights,
    pulse_ads_reporting,
)
from services.pulse_ads_service import PulseAdsError, now_iso  # noqa: E402

SCHEMA = """
CREATE TABLE pulse_ad_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_user_id INTEGER NOT NULL,
    business_name TEXT NOT NULL,
    business_email TEXT,
    business_type TEXT,
    status TEXT DEFAULT 'active',
    verification_status TEXT DEFAULT 'verified',
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
    archived_at TEXT,
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE pulse_ad_creatives (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ad_account_id INTEGER NOT NULL,
    campaign_id INTEGER NOT NULL,
    adset_id INTEGER,
    creative_type TEXT DEFAULT 'text',
    title TEXT NOT NULL,
    body TEXT,
    destination_url TEXT,
    status TEXT DEFAULT 'approved',
    moderation_status TEXT DEFAULT 'approved',
    archived_at TEXT,
    content_ref_type TEXT DEFAULT '',
    content_ref_id INTEGER DEFAULT 0,
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE pulse_ad_placements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    placement_key TEXT UNIQUE NOT NULL,
    display_name TEXT NOT NULL,
    is_active INTEGER DEFAULT 1,
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
    audience_mode TEXT DEFAULT 'everyone',
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
    created_at TEXT
);
CREATE TABLE pulse_ad_clicks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER NOT NULL,
    creative_id INTEGER NOT NULL,
    placement_key TEXT NOT NULL,
    viewer_user_id INTEGER,
    session_id TEXT,
    created_at TEXT
);
CREATE TABLE pulse_ad_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER NOT NULL,
    creative_id INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    metadata_json TEXT DEFAULT '{}',
    created_at TEXT
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
CREATE TABLE pulse_ad_wallet_funding_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER,
    user_id INTEGER,
    amount_cents INTEGER,
    currency TEXT DEFAULT 'usd',
    provider TEXT DEFAULT 'stripe',
    provider_session_id TEXT,
    provider_payment_intent_id TEXT,
    provider_charge_id TEXT,
    reversed_cents INTEGER DEFAULT 0,
    status TEXT DEFAULT 'created',
    idempotency_key TEXT,
    checkout_url TEXT,
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE pulse_ad_receipts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER,
    funding_session_id INTEGER,
    invoice_number TEXT,
    receipt_number TEXT,
    amount_cents INTEGER,
    currency TEXT,
    status TEXT,
    provider TEXT,
    provider_reference_hash TEXT,
    created_at TEXT
);
CREATE TABLE pulse_ad_refunds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER,
    funding_session_id INTEGER,
    amount_cents INTEGER,
    currency TEXT,
    status TEXT,
    reason TEXT,
    provider_reference_hash TEXT,
    created_at TEXT,
    updated_at TEXT
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
CREATE TABLE seller_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    seller_user_id INTEGER,
    buyer_user_id INTEGER NOT NULL,
    item_type TEXT,
    item_id INTEGER,
    amount_cents INTEGER DEFAULT 0,
    status TEXT,
    created_at TEXT
);
"""

OWNER = 101
STRANGER = 202
ANALYST = 303


def ts(days=0, hours=0):
    """ISO timestamp `days` days and `hours` hours before now (UTC)."""
    return (
        datetime.now(timezone.utc) - timedelta(days=days, hours=hours)
    ).replace(microsecond=0).isoformat()


class BaseCase(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        pulse_ad_payments.ensure_schema(self.conn)
        pulse_ads_reporting.ensure_schema(self.conn)
        cur = self.conn.cursor()
        now = now_iso()
        for key in ("feed_inline", "reels_tab"):
            cur.execute(
                "INSERT INTO pulse_ad_placements (placement_key, display_name, is_active, created_at) VALUES (?, ?, 1, ?)",
                (key, key, now),
            )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    # -- seed helpers -------------------------------------------------------

    def _account(self, owner=OWNER, business_type="internal_promotion"):
        cur = self.conn.cursor()
        now = now_iso()
        cur.execute(
            "INSERT INTO pulse_ad_accounts (owner_user_id, business_name, business_type, status, created_at, updated_at) VALUES (?, 'Biz', ?, 'active', ?, ?)",
            (owner, business_type, now, now),
        )
        self.conn.commit()
        return cur.lastrowid

    def _campaign(self, account_id, name="Camp", objective="awareness", status="active",
                  daily=0, lifetime=0, spent=0, created_at=None):
        cur = self.conn.cursor()
        now = now_iso()
        cur.execute(
            """
            INSERT INTO pulse_ad_campaigns
            (ad_account_id, campaign_name, objective, status, daily_budget_cents,
             lifetime_budget_cents, spent_cents, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (account_id, name, objective, status, daily, lifetime, spent, created_at or now, now),
        )
        self.conn.commit()
        return cur.lastrowid

    def _creative(self, account_id, campaign_id, title="Ad", listing_id=0):
        cur = self.conn.cursor()
        now = now_iso()
        cur.execute(
            """
            INSERT INTO pulse_ad_creatives
            (ad_account_id, campaign_id, title, destination_url, status, content_ref_type, content_ref_id, created_at, updated_at)
            VALUES (?, ?, ?, 'https://example.com', 'approved', ?, ?, ?, ?)
            """,
            (account_id, campaign_id, title, "listing" if listing_id else "", listing_id, now, now),
        )
        self.conn.commit()
        return cur.lastrowid

    def _impressions(self, campaign_id, creative_id, placement, viewers, created_at=None):
        """viewers: list of ints (viewer ids) or strings (anonymous session ids)."""
        created_at = created_at or now_iso()
        rows = []
        for viewer in viewers:
            if isinstance(viewer, str):
                rows.append((campaign_id, creative_id, placement, None, viewer, created_at))
            else:
                rows.append((campaign_id, creative_id, placement, viewer, f"s-{viewer}", created_at))
        self.conn.executemany(
            "INSERT INTO pulse_ad_impressions (campaign_id, creative_id, placement_key, viewer_user_id, session_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
        self.conn.commit()

    def _clicks(self, campaign_id, creative_id, placement, viewers, created_at=None):
        created_at = created_at or now_iso()
        rows = []
        last_id = None
        cur = self.conn.cursor()
        for viewer in viewers:
            if isinstance(viewer, str):
                cur.execute(
                    "INSERT INTO pulse_ad_clicks (campaign_id, creative_id, placement_key, viewer_user_id, session_id, created_at) VALUES (?, ?, ?, NULL, ?, ?)",
                    (campaign_id, creative_id, placement, viewer, created_at),
                )
            else:
                cur.execute(
                    "INSERT INTO pulse_ad_clicks (campaign_id, creative_id, placement_key, viewer_user_id, session_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (campaign_id, creative_id, placement, viewer, f"s-{viewer}", created_at),
                )
            last_id = cur.lastrowid
        self.conn.commit()
        return last_id

    def _spend(self, account_id, campaign_id, creative_id, cents, placement="feed_inline", created_at=None, idem=None):
        self.conn.execute(
            """
            INSERT INTO pulse_ad_wallet_transactions
            (account_id, campaign_id, creative_id, transaction_type, amount_cents, status, idempotency_key, description, created_at)
            VALUES (?, ?, ?, 'spend', ?, 'posted', ?, ?, ?)
            """,
            (
                account_id, campaign_id, creative_id, cents,
                idem or f"sp-{account_id}-{campaign_id}-{cents}-{placement}-{created_at or ''}-{os.urandom(4).hex()}",
                f"Ad delivery spend for {placement}",
                created_at or now_iso(),
            ),
        )
        self.conn.commit()

    def _event(self, campaign_id, creative_id, event_type, count=1, created_at=None):
        self.conn.executemany(
            "INSERT INTO pulse_ad_events (campaign_id, creative_id, event_type, created_at) VALUES (?, ?, ?, ?)",
            [(campaign_id, creative_id, event_type, created_at or now_iso())] * count,
        )
        self.conn.commit()

    def _purchase(self, buyer, listing_id, amount_cents, status="paid", created_at=None):
        cur = self.conn.cursor()
        cur.execute(
            "INSERT INTO seller_transactions (seller_user_id, buyer_user_id, item_type, item_id, amount_cents, status, created_at) VALUES (1, ?, 'marketplace_product', ?, ?, ?, ?)",
            (buyer, listing_id, amount_cents, status, created_at or now_iso()),
        )
        self.conn.commit()
        return cur.lastrowid

    def _fund(self, account_id, amount_cents, event_id="evt_1", pi="pi_1", sess="cs_1"):
        cur = self.conn.cursor()
        now = now_iso()
        cur.execute(
            "INSERT INTO pulse_ad_wallet_funding_sessions (account_id, user_id, amount_cents, currency, status, created_at, updated_at) VALUES (?, ?, ?, 'usd', 'created', ?, ?)",
            (account_id, OWNER, amount_cents, now, now),
        )
        self.conn.commit()
        session_id = cur.lastrowid
        result = pulse_ad_payments.credit_wallet_from_stripe_session(
            self.conn,
            event_id,
            {
                "id": sess,
                "payment_intent": pi,
                "amount_total": amount_cents,
                "currency": "usd",
                "metadata": {
                    "purpose": "pulse_ad_wallet_funding",
                    "funding_session_id": session_id,
                    "ad_account_id": account_id,
                },
            },
        )
        return session_id, result

    def _wallet(self, account_id):
        row = self.conn.execute(
            "SELECT * FROM pulse_ad_wallets WHERE account_id=?", (account_id,)
        ).fetchone()
        return dict(row) if row else {}

    def _by_kind(self, insights, kind):
        return [r for r in insights["recommendations"] if r["kind"] == kind]


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

class ReportBreakdownTests(BaseCase):
    def _seed_two_campaigns(self):
        account_id = self._account()
        c1 = self._campaign(account_id, name="Traffic", objective="website_traffic")
        c2 = self._campaign(account_id, name="Aware", objective="awareness")
        cr1 = self._creative(account_id, c1, title="T-ad")
        cr2 = self._creative(account_id, c2, title="A-ad")
        today, yesterday = ts(0), ts(1)
        # C1 today: feed 6 impressions (2 anonymous), reels 4 impressions.
        self._impressions(c1, cr1, "feed_inline", [1, 1, 2, 3, "anon-1", "anon-1"], today)
        self._impressions(c1, cr1, "reels_tab", [1, 2, 4, 5], today)
        # C1 clicks: feed 2, reels 1.
        self._clicks(c1, cr1, "feed_inline", [1, 2], today)
        self._clicks(c1, cr1, "reels_tab", [3], today)
        # C1 spend: feed 200, reels 100.
        self._spend(account_id, c1, cr1, 200, "feed_inline", today)
        self._spend(account_id, c1, cr1, 100, "reels_tab", today)
        # C2 yesterday: 4 impressions, no clicks, 50 spend.
        self._impressions(c2, cr2, "feed_inline", [6, 7, 8, 9], yesterday)
        self._spend(account_id, c2, cr2, 50, "feed_inline", yesterday)
        return account_id, c1, c2

    def test_campaign_breakdown_hand_computed(self):
        account_id, c1, c2 = self._seed_two_campaigns()
        report = pulse_ads_reporting.build_report(self.conn, OWNER, account_id, {"breakdown": "campaign"})
        rows = {row["key"]: row for row in report["rows"]}
        self.assertEqual(set(rows), {c1, c2})

        r1 = rows[c1]
        self.assertEqual(r1["label"], "Traffic")
        self.assertEqual(r1["campaign_id"], c1)  # drill id present
        self.assertEqual(r1["impressions"], 10)
        self.assertEqual(r1["clicks"], 3)
        self.assertEqual(r1["spend_cents"], 300)
        # reach = distinct COALESCE(viewer, session): {1,2,3,4,5,'anon-1'}
        self.assertEqual(r1["reach"], 6)
        self.assertEqual(r1["frequency"], round(10 / 6, 2))
        self.assertEqual(r1["ctr"], 0.3)
        self.assertEqual(r1["cpc_cents"], 100)  # 300 // 3
        # website_traffic is traffic-like: results are clicks.
        self.assertEqual(r1["results"], 3)
        self.assertEqual(r1["results_metric"], "clicks")
        self.assertTrue(r1["results_available"])
        self.assertEqual(r1["cost_per_result_cents"], 100)
        # 2 of 10 impressions anonymous >= 10% share -> flagged estimate.
        self.assertTrue(r1["reach_estimated"])

        r2 = rows[c2]
        self.assertEqual(r2["impressions"], 4)
        self.assertEqual(r2["clicks"], 0)
        self.assertEqual(r2["spend_cents"], 50)
        self.assertEqual(r2["reach"], 4)
        self.assertEqual(r2["frequency"], 1.0)
        self.assertFalse(r2["reach_estimated"])
        # Awareness has no honest result stream: marked unavailable, never invented.
        self.assertFalse(r2["results_available"])
        self.assertEqual(r2["results_metric"], "")
        self.assertEqual(r2["results"], 0)

        totals = report["totals"]
        self.assertEqual(totals["label"], "Totals")
        self.assertEqual(totals["impressions"], 14)
        self.assertEqual(totals["clicks"], 3)
        self.assertEqual(totals["spend_cents"], 350)
        # Distinct people across the whole account: {1..9, 'anon-1'}
        self.assertEqual(totals["reach"], 10)
        self.assertEqual(totals["frequency"], round(14 / 10, 2))
        self.assertEqual(totals["ctr"], round(3 / 14, 4))
        self.assertEqual(totals["cpc_cents"], 350 // 3)
        self.assertEqual(totals["results"], 3)
        self.assertEqual(totals["results_metric"], "clicks")
        self.assertTrue(totals["results_available"])
        self.assertTrue(totals["reach_estimated"])
        self.assertNotIn("campaign_id", totals)

    def test_placement_breakdown_hand_computed(self):
        account_id, _c1, _c2 = self._seed_two_campaigns()
        report = pulse_ads_reporting.build_report(self.conn, OWNER, account_id, {"breakdown": "placement"})
        rows = {row["key"]: row for row in report["rows"]}
        self.assertEqual(set(rows), {"feed_inline", "reels_tab"})

        feed = rows["feed_inline"]
        self.assertEqual(feed["placement_key"], "feed_inline")  # drill id present
        self.assertIsNone(feed["campaign_id"])
        self.assertEqual(feed["impressions"], 10)  # 6 from C1 + 4 from C2
        self.assertEqual(feed["clicks"], 2)
        self.assertEqual(feed["spend_cents"], 250)
        # Distinct on feed: {1,2,3,'anon-1',6,7,8,9}
        self.assertEqual(feed["reach"], 8)
        self.assertEqual(feed["ctr"], 0.2)
        self.assertEqual(feed["cpc_cents"], 125)
        self.assertEqual(feed["results"], 2)  # traffic-like clicks only
        self.assertTrue(feed["reach_estimated"])

        reels = rows["reels_tab"]
        self.assertEqual(reels["impressions"], 4)
        self.assertEqual(reels["clicks"], 1)
        self.assertEqual(reels["spend_cents"], 100)
        self.assertEqual(reels["reach"], 4)
        self.assertEqual(reels["results"], 1)
        self.assertFalse(reels["reach_estimated"])

        self.assertEqual(report["totals"]["spend_cents"], 350)
        self.assertEqual(report["breakdown"], "placement")

    def test_date_breakdown_hand_computed(self):
        account_id, _c1, _c2 = self._seed_two_campaigns()
        today_key, yesterday_key = ts(0)[:10], ts(1)[:10]
        report = pulse_ads_reporting.build_report(self.conn, OWNER, account_id, {"breakdown": "date"})
        rows = {row["key"]: row for row in report["rows"]}
        self.assertEqual(set(rows), {today_key, yesterday_key})

        today_row = rows[today_key]
        self.assertEqual(today_row["impressions"], 10)
        self.assertEqual(today_row["clicks"], 3)
        self.assertEqual(today_row["spend_cents"], 300)
        self.assertEqual(today_row["results"], 3)
        self.assertTrue(today_row["results_available"])

        yesterday_row = rows[yesterday_key]
        self.assertEqual(yesterday_row["impressions"], 4)
        self.assertEqual(yesterday_row["clicks"], 0)
        self.assertEqual(yesterday_row["spend_cents"], 50)
        # Only the awareness campaign ran yesterday: no result stream.
        self.assertFalse(yesterday_row["results_available"])

    def test_invalid_breakdown_rejected(self):
        account_id = self._account()
        with self.assertRaises(PulseAdsError):
            pulse_ads_reporting.build_report(self.conn, OWNER, account_id, {"breakdown": "vibes"})


class ObjectiveAwareResultsTests(BaseCase):
    def test_video_views_uses_video_events_not_clicks(self):
        account_id = self._account()
        campaign_id = self._campaign(account_id, name="Vid", objective="video_views")
        creative_id = self._creative(account_id, campaign_id)
        self._impressions(campaign_id, creative_id, "feed_inline", [1, 2, 3, 4])
        self._clicks(campaign_id, creative_id, "feed_inline", [1, 2, 3])
        self._event(campaign_id, creative_id, "video_start", count=5)
        # A non-result event type never counts.
        self._event(campaign_id, creative_id, "video_25", count=9)

        report = pulse_ads_reporting.build_report(self.conn, OWNER, account_id, {"breakdown": "campaign"})
        row = report["rows"][0]
        self.assertEqual(row["results"], 5)
        self.assertEqual(row["results_metric"], "video_start_events")
        self.assertTrue(row["results_available"])
        self.assertEqual(row["clicks"], 3)

        # Events carry no placement dimension: the placement breakdown does not
        # invent one, results stay 0 while the metric stays honestly labelled.
        placement = pulse_ads_reporting.build_report(self.conn, OWNER, account_id, {"breakdown": "placement"})
        feed = [r for r in placement["rows"] if r["key"] == "feed_inline"][0]
        self.assertEqual(feed["results"], 0)
        self.assertEqual(feed["results_metric"], "video_start_events")
        self.assertEqual(feed["clicks"], 3)

    def test_marketplace_sales_uses_attribution_and_roas(self):
        account_id = self._account()
        campaign_id = self._campaign(account_id, name="Shop", objective="marketplace_sales")
        creative_id = self._creative(account_id, campaign_id, listing_id=555)
        self._clicks(campaign_id, creative_id, "reels_tab", [7], created_at=ts(hours=2))
        self._purchase(7, 555, 5000, created_at=ts(hours=1))
        self._spend(account_id, campaign_id, creative_id, 1000, "reels_tab")

        report = pulse_ads_reporting.build_report(self.conn, OWNER, account_id, {"breakdown": "campaign"})
        row = report["rows"][0]
        self.assertEqual(row["purchases"], 1)
        self.assertEqual(row["revenue_cents"], 5000)
        self.assertEqual(row["results"], 1)
        self.assertEqual(row["results_metric"], "purchases")
        self.assertTrue(row["results_available"])
        self.assertEqual(row["roas"], 5.0)
        self.assertEqual(row["cost_per_result_cents"], 1000)

        # Purchases keep their click's placement in the placement breakdown.
        placement = pulse_ads_reporting.build_report(self.conn, OWNER, account_id, {"breakdown": "placement"})
        reels = [r for r in placement["rows"] if r["key"] == "reels_tab"][0]
        self.assertEqual(reels["purchases"], 1)
        self.assertEqual(reels["revenue_cents"], 5000)
        self.assertEqual(reels["roas"], 5.0)

    def test_awareness_marked_unavailable(self):
        account_id = self._account()
        campaign_id = self._campaign(account_id, name="Aw", objective="awareness")
        creative_id = self._creative(account_id, campaign_id)
        self._impressions(campaign_id, creative_id, "feed_inline", [1, 2])
        report = pulse_ads_reporting.build_report(self.conn, OWNER, account_id, {"breakdown": "campaign"})
        row = report["rows"][0]
        self.assertFalse(row["results_available"])
        self.assertEqual(row["results_metric"], "")
        self.assertEqual(row["results"], 0)


# ---------------------------------------------------------------------------
# Attribution
# ---------------------------------------------------------------------------

class AttributionTests(BaseCase):
    def test_idempotent_and_last_click_wins(self):
        account_id = self._account()
        campaign_id = self._campaign(account_id, objective="marketplace_sales")
        creative_id = self._creative(account_id, campaign_id, listing_id=777)
        self._clicks(campaign_id, creative_id, "feed_inline", [7], created_at=ts(days=2))
        last_click_id = self._clicks(campaign_id, creative_id, "feed_inline", [7], created_at=ts(days=1))
        order_id = self._purchase(7, 777, 4200, created_at=ts(hours=23))

        first = pulse_ads_reporting.attribute_purchases(self.conn, account_id=account_id)
        self.assertEqual(first["new"], 1)
        self.assertEqual(first["attributed"], 1)
        self.assertEqual(first["model"], "last_click_7d")

        # Recompute: nothing duplicated, nothing reassigned.
        second = pulse_ads_reporting.attribute_purchases(self.conn, account_id=account_id)
        self.assertEqual(second["new"], 0)
        self.assertEqual(second["attributed"], 1)

        rows = self.conn.execute("SELECT * FROM pulse_ad_attributions").fetchall()
        self.assertEqual(len(rows), 1)
        row = dict(rows[0])
        self.assertEqual(row["click_id"], last_click_id)  # last click before purchase
        self.assertEqual(row["order_ref"], str(order_id))
        self.assertEqual(row["revenue_cents"], 4200)
        self.assertEqual(row["campaign_id"], campaign_id)
        self.assertEqual(row["buyer_user_id"], 7)

    def test_seven_day_window_boundary(self):
        account_id = self._account()
        campaign_id = self._campaign(account_id, objective="marketplace_sales")
        # Buyer 11: purchase 8 days after the click -> outside the window.
        late_creative = self._creative(account_id, campaign_id, listing_id=601)
        self._clicks(campaign_id, late_creative, "feed_inline", [11], created_at=ts(days=9))
        self._purchase(11, 601, 1000, created_at=ts(days=1))
        # Buyer 12: purchase exactly 7 days after the click -> inside.
        edge_creative = self._creative(account_id, campaign_id, listing_id=602)
        self._clicks(campaign_id, edge_creative, "feed_inline", [12], created_at=ts(days=8))
        self._purchase(12, 602, 2000, created_at=ts(days=1))
        # Buyer 13: unpaid status never attributes.
        pend_creative = self._creative(account_id, campaign_id, listing_id=603)
        self._clicks(campaign_id, pend_creative, "feed_inline", [13], created_at=ts(days=2))
        self._purchase(13, 603, 3000, status="pending", created_at=ts(days=1))

        result = pulse_ads_reporting.attribute_purchases(self.conn, account_id=account_id)
        self.assertEqual(result["attributed"], 1)
        rows = [dict(r) for r in self.conn.execute("SELECT * FROM pulse_ad_attributions").fetchall()]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["buyer_user_id"], 12)
        self.assertEqual(rows[0]["revenue_cents"], 2000)

    def test_attribution_status_roas_math(self):
        account_id = self._account()
        campaign_id = self._campaign(account_id, objective="marketplace_sales")
        creative_id = self._creative(account_id, campaign_id, listing_id=888)
        self._clicks(campaign_id, creative_id, "feed_inline", [7], created_at=ts(hours=3))
        self._purchase(7, 888, 5000, created_at=ts(hours=1))
        self._spend(account_id, campaign_id, creative_id, 2000)

        status = pulse_ads_reporting.attribution_status(self.conn, OWNER, campaign_id)
        self.assertEqual(status["purchases"], 1)
        self.assertEqual(status["revenue_cents"], 5000)
        self.assertEqual(status["spend_cents"], 2000)
        self.assertEqual(status["roas"], 2.5)
        self.assertEqual(status["window_days"], 7)
        self.assertEqual(status["model"], "last_click_7d")

        # Campaign with zero spend never fabricates a ROAS.
        other = self._campaign(account_id, name="NoSpend", objective="marketplace_sales")
        status = pulse_ads_reporting.attribution_status(self.conn, OWNER, other)
        self.assertIsNone(status["roas"])
        self.assertEqual(status["purchases"], 0)


# ---------------------------------------------------------------------------
# Insights
# ---------------------------------------------------------------------------

class InsightTests(BaseCase):
    def test_empty_data_gives_no_recommendations_and_status(self):
        account_id = self._account()
        insights = pulse_ads_insights.build_insights(self.conn, OWNER, account_id)
        self.assertEqual(insights["recommendations"], [])
        self.assertEqual(insights["data_status"]["campaigns"], 0)
        self.assertEqual(insights["data_status"]["impressions"], 0)
        self.assertIn("note", insights["data_status"])

    def test_audience_saturation_then_apply_pause_then_stale(self):
        account_id = self._account()
        campaign_id = self._campaign(account_id, name="Sat", status="active")
        creative_id = self._creative(account_id, campaign_id)
        # 40 impressions over 10 viewers in the trailing 7 days: frequency 4.0.
        for viewer in range(1, 11):
            self._impressions(campaign_id, creative_id, "feed_inline", [viewer] * 4, created_at=ts(days=1))

        insights = pulse_ads_insights.build_insights(self.conn, OWNER, account_id)
        matches = self._by_kind(insights, "audience_saturation")
        self.assertEqual(len(matches), 1)
        item = matches[0]
        self.assertTrue(item["requires_approval"])
        self.assertIn("40 impressions reached 10 viewers", item["why"])
        self.assertIn("frequency 4.00", item["why"])
        self.assertEqual(item["action"]["type"], "pause_campaign")

        # Without approve=true nothing may mutate.
        with self.assertRaises(PulseAdsError) as ctx:
            pulse_ads_insights.apply_insight(
                self.conn, OWNER, {"account_id": account_id, "insight_id": item["id"]}
            )
        self.assertIn("approve", str(ctx.exception))
        status = self.conn.execute(
            "SELECT status FROM pulse_ad_campaigns WHERE id=?", (campaign_id,)
        ).fetchone()["status"]
        self.assertEqual(status, "active")

        # With approve: applied + audited.
        applied = pulse_ads_insights.apply_insight(
            self.conn, OWNER, {"account_id": account_id, "insight_id": item["id"], "approve": True}
        )
        self.assertTrue(applied["applied"])
        self.assertEqual(applied["after"], {"status": "paused"})
        status = self.conn.execute(
            "SELECT status FROM pulse_ad_campaigns WHERE id=?", (campaign_id,)
        ).fetchone()["status"]
        self.assertEqual(status, "paused")
        audit = self.conn.execute(
            "SELECT * FROM pulse_ad_audit_logs WHERE action='ad_insight_applied'"
        ).fetchall()
        self.assertEqual(len(audit), 1)
        self.assertEqual(audit[0]["entity_id"], item["id"])
        self.assertEqual(audit[0]["actor_user_id"], OWNER)
        self.assertIn("audience_saturation", audit[0]["after_json"])

        # The campaign is paused now, so the insight is stale on recompute.
        with self.assertRaises(PulseAdsError) as ctx:
            pulse_ads_insights.apply_insight(
                self.conn, OWNER, {"account_id": account_id, "insight_id": item["id"], "approve": True}
            )
        self.assertEqual(ctx.exception.status_code, 409)

    def test_high_cpc_needs_outlier_above_twice_median(self):
        account_id = self._account()
        cheap_a = self._campaign(account_id, name="CheapA", status="paused")
        cheap_b = self._campaign(account_id, name="CheapB", status="paused")
        pricey = self._campaign(account_id, name="Pricey", status="paused")
        for cid in (cheap_a, cheap_b, pricey):
            self._clicks(cid, 0, "feed_inline", list(range(1, 21)))
        self._spend(account_id, cheap_a, 0, 2000)   # CPC 100
        self._spend(account_id, cheap_b, 0, 2000)   # CPC 100
        self._spend(account_id, pricey, 0, 50000)   # CPC 2500 > 2x median (100)

        insights = pulse_ads_insights.build_insights(self.conn, OWNER, account_id)
        kinds = {r["kind"] for r in insights["recommendations"]}
        self.assertEqual(kinds, {"high_cpc"})
        item = self._by_kind(insights, "high_cpc")[0]
        self.assertEqual(item["campaign_id"], pricey)
        self.assertIn("50000 cents over 20 clicks", item["why"])
        self.assertIn("CPC 25.00", item["why"])
        self.assertEqual(item["action"]["type"], "pause_campaign")

    def test_placement_opportunity_and_focus_placement_apply(self):
        account_id = self._account()
        campaign_id = self._campaign(account_id, name="Places", status="active")
        creative_id = self._creative(account_id, campaign_id)
        cur = self.conn.cursor()
        for key in ("feed_inline", "reels_tab"):
            placement_id = cur.execute(
                "SELECT id FROM pulse_ad_placements WHERE placement_key=?", (key,)
            ).fetchone()["id"]
            cur.execute(
                "INSERT INTO pulse_ad_campaign_placements (campaign_id, placement_id, created_at) VALUES (?, ?, ?)",
                (campaign_id, placement_id, now_iso()),
            )
        self.conn.commit()
        # feed: 300 impressions / 30 clicks (CTR 10%); reels: 700 / 10 (1.4%).
        # Campaign CTR 4%; feed >= 1.3x campaign -> opportunity.
        self._impressions(campaign_id, creative_id, "feed_inline", [f"f{i}" for i in range(300)])
        self._impressions(campaign_id, creative_id, "reels_tab", [f"r{i}" for i in range(700)])
        self._clicks(campaign_id, creative_id, "feed_inline", list(range(1, 31)))
        self._clicks(campaign_id, creative_id, "reels_tab", list(range(31, 41)))

        insights = pulse_ads_insights.build_insights(self.conn, OWNER, account_id)
        matches = self._by_kind(insights, "placement_opportunity")
        self.assertEqual(len(matches), 1)
        item = matches[0]
        self.assertEqual(item["id"], f"placement_opportunity:{campaign_id}:feed_inline")
        self.assertIn("300 impressions, 30 clicks", item["why"])
        self.assertIn("CTR 10.00% vs campaign 4.00%", item["why"])
        self.assertEqual(item["action"]["type"], "focus_placement")

        applied = pulse_ads_insights.apply_insight(
            self.conn, OWNER, {"account_id": account_id, "insight_id": item["id"], "approve": True}
        )
        self.assertTrue(applied["applied"])
        remaining = self.conn.execute(
            """
            SELECT p.placement_key FROM pulse_ad_campaign_placements cp
            JOIN pulse_ad_placements p ON p.id=cp.placement_id WHERE cp.campaign_id=?
            """,
            (campaign_id,),
        ).fetchall()
        self.assertEqual([r["placement_key"] for r in remaining], ["feed_inline"])

    def test_creative_fatigue_then_archive_then_stale(self):
        account_id = self._account()
        campaign_id = self._campaign(account_id, name="Tired", status="paused")
        creative_id = self._creative(account_id, campaign_id, title="Old banner")
        # First 3-day window (8 days ago): 200 impressions, 20 clicks -> CTR 10%.
        self._impressions(campaign_id, creative_id, "feed_inline", [f"a{i}" for i in range(200)], created_at=ts(days=8))
        self._clicks(campaign_id, creative_id, "feed_inline", [f"a{i}" for i in range(20)], created_at=ts(days=8))
        # Trailing 3 days: 200 impressions, 4 clicks -> CTR 2% < 60% of launch.
        self._impressions(campaign_id, creative_id, "feed_inline", [f"b{i}" for i in range(200)], created_at=ts(days=1))
        self._clicks(campaign_id, creative_id, "feed_inline", [f"b{i}" for i in range(4)], created_at=ts(days=1))

        insights = pulse_ads_insights.build_insights(self.conn, OWNER, account_id)
        matches = self._by_kind(insights, "creative_fatigue")
        self.assertEqual(len(matches), 1)
        item = matches[0]
        self.assertEqual(item["creative_id"], creative_id)
        self.assertIn("10.00%", item["why"])
        self.assertIn("2.00%", item["why"])
        self.assertEqual(item["action"]["type"], "archive_creative")

        applied = pulse_ads_insights.apply_insight(
            self.conn, OWNER, {"account_id": account_id, "insight_id": item["id"], "approve": True}
        )
        self.assertEqual(applied["after"]["status"], "archived")
        row = self.conn.execute("SELECT status FROM pulse_ad_creatives WHERE id=?", (creative_id,)).fetchone()
        self.assertEqual(row["status"], "archived")
        # Archived creatives leave the fatigue scan: the same insight is stale.
        with self.assertRaises(PulseAdsError) as ctx:
            pulse_ads_insights.apply_insight(
                self.conn, OWNER, {"account_id": account_id, "insight_id": item["id"], "approve": True}
            )
        self.assertEqual(ctx.exception.status_code, 409)

    def test_strong_creative_budget_raise(self):
        account_id = self._account()
        campaign_id = self._campaign(account_id, name="Winner", status="active", daily=1000)
        star = self._creative(account_id, campaign_id, title="Star")
        filler = self._creative(account_id, campaign_id, title="Filler")
        # Star: 300 impr / 30 clicks (10%); Filler: 700 / 10. Account CTR 4%.
        self._impressions(campaign_id, star, "feed_inline", [f"s{i}" for i in range(300)])
        self._clicks(campaign_id, star, "feed_inline", [f"s{i}" for i in range(30)])
        self._impressions(campaign_id, filler, "feed_inline", [f"g{i}" for i in range(700)])
        self._clicks(campaign_id, filler, "feed_inline", [f"g{i}" for i in range(10)])

        insights = pulse_ads_insights.build_insights(self.conn, OWNER, account_id)
        matches = self._by_kind(insights, "strong_creative")
        self.assertEqual(len(matches), 1)
        item = matches[0]
        self.assertEqual(item["creative_id"], star)
        self.assertIn("CTR 10.00%", item["why"])
        self.assertIn("account average 4.00%", item["why"])
        self.assertEqual(item["action"]["type"], "increase_daily_budget")
        self.assertEqual(item["action"]["params"]["daily_budget_cents"], 1200)

        applied = pulse_ads_insights.apply_insight(
            self.conn, OWNER, {"account_id": account_id, "insight_id": item["id"], "approve": True}
        )
        self.assertEqual(applied["after"]["daily_budget_cents"], 1200)
        row = self.conn.execute(
            "SELECT daily_budget_cents FROM pulse_ad_campaigns WHERE id=?", (campaign_id,)
        ).fetchone()
        self.assertEqual(row["daily_budget_cents"], 1200)

    def test_audience_expansion_apply_and_stale(self):
        account_id = self._account()
        campaign_id = self._campaign(account_id, name="Narrow", status="active", created_at=ts(days=5))
        now = now_iso()
        self.conn.execute(
            "INSERT INTO pulse_ad_targeting (campaign_id, audience_mode, created_at, updated_at) VALUES (?, 'custom', ?, ?)",
            (campaign_id, now, now),
        )
        self.conn.commit()

        insights = pulse_ads_insights.build_insights(self.conn, OWNER, account_id)
        matches = self._by_kind(insights, "audience_expansion")
        self.assertEqual(len(matches), 1)
        item = matches[0]
        self.assertIn("'custom'", item["why"])
        self.assertIn("only 0 impressions", item["why"])
        self.assertEqual(item["action"]["type"], "expand_audience")

        applied = pulse_ads_insights.apply_insight(
            self.conn, OWNER, {"account_id": account_id, "insight_id": item["id"], "approve": True}
        )
        self.assertEqual(applied["after"], {"audience_mode": "everyone"})
        row = self.conn.execute(
            "SELECT audience_mode FROM pulse_ad_targeting WHERE campaign_id=?", (campaign_id,)
        ).fetchone()
        self.assertEqual(row["audience_mode"], "everyone")
        # Mode is 'everyone' now: recompute drops the insight -> stale.
        with self.assertRaises(PulseAdsError) as ctx:
            pulse_ads_insights.apply_insight(
                self.conn, OWNER, {"account_id": account_id, "insight_id": item["id"], "approve": True}
            )
        self.assertEqual(ctx.exception.status_code, 409)

    def test_budget_constrained_topup_action_not_auto_applicable(self):
        # Real (non-internal) account with an empty wallet and an active campaign.
        account_id = self._account(business_type="brand")
        self._campaign(account_id, name="Dry", status="active")
        insights = pulse_ads_insights.build_insights(self.conn, OWNER, account_id)
        matches = self._by_kind(insights, "budget_constrained")
        self.assertEqual(len(matches), 1)
        item = matches[0]
        self.assertIn("spendable balance of 0 cents", item["why"])
        self.assertEqual(item["action"]["type"], "topup_wallet")
        # topup_wallet is not whitelisted: even with approval it is refused.
        with self.assertRaises(PulseAdsError) as ctx:
            pulse_ads_insights.apply_insight(
                self.conn, OWNER, {"account_id": account_id, "insight_id": item["id"], "approve": True}
            )
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("cannot be applied automatically", str(ctx.exception))

    def test_unknown_insight_id_is_stale(self):
        account_id = self._account()
        with self.assertRaises(PulseAdsError) as ctx:
            pulse_ads_insights.apply_insight(
                self.conn, OWNER,
                {"account_id": account_id, "insight_id": "audience_saturation:9999", "approve": True},
            )
        self.assertEqual(ctx.exception.status_code, 409)


# ---------------------------------------------------------------------------
# Wallet completion
# ---------------------------------------------------------------------------

class WalletTests(BaseCase):
    def test_funding_writes_invoice_idempotently(self):
        account_id = self._account(business_type="brand")
        session_id, result = self._fund(account_id, 10000, event_id="evt_a")
        self.assertTrue(result["ok"])
        invoices = self.conn.execute(
            "SELECT * FROM pulse_ad_invoices WHERE account_id=?", (account_id,)
        ).fetchall()
        self.assertEqual(len(invoices), 1)
        invoice = dict(invoices[0])
        self.assertEqual(invoice["funding_session_id"], session_id)
        self.assertEqual(invoice["amount_cents"], 10000)
        self.assertEqual(invoice["status"], "paid")
        self.assertEqual(invoice["invoice_number"], f"ADINV-{account_id}-00001")
        self.assertIn("wallet_funding", invoice["metadata_json"])

        # Webhook retry: same event -> deduped, no second credit, no second invoice.
        replay = pulse_ad_payments.credit_wallet_from_stripe_session(
            self.conn, "evt_a",
            {"id": "cs_1", "payment_intent": "pi_1", "amount_total": 10000, "currency": "usd",
             "metadata": {"purpose": "pulse_ad_wallet_funding", "funding_session_id": session_id,
                          "ad_account_id": account_id}},
        )
        self.assertTrue(replay.get("deduped"))
        self.assertEqual(self._wallet(account_id)["available_balance_cents"], 10000)
        count = self.conn.execute(
            "SELECT COUNT(*) AS n FROM pulse_ad_invoices WHERE account_id=?", (account_id,)
        ).fetchone()["n"]
        self.assertEqual(count, 1)

        listing = pulse_ad_payments.list_invoices(self.conn, OWNER, account_id)
        self.assertEqual(len(listing["invoices"]), 1)
        self.assertEqual(listing["invoices"][0]["invoice_number"], f"ADINV-{account_id}-00001")
        self.assertIsNone(listing["next_before_id"])

    def test_list_invoices_backfills_missing_invoice(self):
        account_id = self._account(business_type="brand")
        session_id, _ = self._fund(account_id, 7000, event_id="evt_bf", pi="pi_bf", sess="cs_bf")
        self.conn.execute("DELETE FROM pulse_ad_invoices WHERE funding_session_id=?", (session_id,))
        self.conn.commit()
        listing = pulse_ad_payments.list_invoices(self.conn, OWNER, account_id)
        self.assertEqual(len(listing["invoices"]), 1)
        self.assertEqual(listing["invoices"][0]["funding_session_id"], session_id)
        self.assertEqual(listing["invoices"][0]["amount_cents"], 7000)

    def test_spending_limits_owner_only_validated_and_enforced(self):
        account_id = self._account(business_type="brand")
        # Owner-only.
        with self.assertRaises(PulseAdsError) as ctx:
            pulse_ad_payments.set_spending_limits(self.conn, STRANGER, account_id, {"daily_limit_cents": 100})
        self.assertEqual(ctx.exception.status_code, 404)
        # Validation.
        with self.assertRaises(PulseAdsError):
            pulse_ad_payments.set_spending_limits(self.conn, OWNER, account_id, {"daily_limit_cents": -5})
        with self.assertRaises(PulseAdsError):
            pulse_ad_payments.set_spending_limits(
                self.conn, OWNER, account_id, {"daily_limit_cents": 500, "lifetime_limit_cents": 100}
            )
        result = pulse_ad_payments.set_spending_limits(self.conn, OWNER, account_id, {"daily_limit_cents": 100})
        self.assertEqual(result["daily_limit_cents"], 100)

        # Enforcement inside record_spend_event.
        self._fund(account_id, 10000, event_id="evt_lim", pi="pi_lim", sess="cs_lim")
        campaign_id = self._campaign(account_id, name="Capped", status="active")
        first = pulse_ad_payments.record_spend_event(
            self.conn, campaign_id, None, "feed_inline", amount_cents=60, idempotency_key="lim-1"
        )
        self.assertTrue(first["ok"])
        second = pulse_ad_payments.record_spend_event(
            self.conn, campaign_id, None, "feed_inline", amount_cents=60, idempotency_key="lim-2"
        )
        self.assertFalse(second["ok"])
        self.assertTrue(second["paused"])
        self.assertEqual(second["reason"], "daily_limit_reached")
        status = self.conn.execute(
            "SELECT status FROM pulse_ad_campaigns WHERE id=?", (campaign_id,)
        ).fetchone()["status"]
        self.assertEqual(status, "paused")
        audit = self.conn.execute(
            "SELECT COUNT(*) AS n FROM pulse_ad_audit_logs WHERE action='ad_campaign_auto_paused_spend_limit'"
        ).fetchone()["n"]
        self.assertEqual(audit, 1)
        # Only the first spend was charged.
        self.assertEqual(self._wallet(account_id)["available_balance_cents"], 10000 - 60)
        # Zero clears the limit and delivery can resume.
        pulse_ad_payments.set_spending_limits(self.conn, OWNER, account_id, {"daily_limit_cents": 0})
        third = pulse_ad_payments.record_spend_event(
            self.conn, campaign_id, None, "feed_inline", amount_cents=60, idempotency_key="lim-3"
        )
        self.assertTrue(third["ok"])

    def test_auto_topup_roundtrip_and_needs_topup_flip(self):
        account_id = self._account(business_type="brand")
        self._fund(account_id, 5000, event_id="evt_top", pi="pi_top", sess="cs_top")
        # Validation: enabling needs a threshold and a fundable amount.
        with self.assertRaises(PulseAdsError):
            pulse_ad_payments.set_auto_topup(self.conn, OWNER, account_id, {"enabled": True})
        with self.assertRaises(PulseAdsError):
            pulse_ad_payments.set_auto_topup(
                self.conn, OWNER, account_id, {"enabled": True, "threshold_cents": 1000, "amount_cents": 100}
            )
        result = pulse_ad_payments.set_auto_topup(
            self.conn, OWNER, account_id, {"enabled": True, "threshold_cents": 1000, "amount_cents": 500}
        )
        self.assertTrue(result["enabled"])
        self.assertFalse(result["auto_charge"])

        summary = pulse_ad_payments.wallet_summary(self.conn, OWNER, account_id)
        self.assertEqual(
            {k: summary["auto_topup"][k] for k in ("enabled", "threshold_cents", "amount_cents")},
            {"enabled": True, "threshold_cents": 1000, "amount_cents": 500},
        )
        self.assertFalse(summary["needs_topup"])  # 5000 spendable >= 1000

        # Spend the balance below the threshold: needs_topup flips true and a
        # low-balance notification is filed.
        campaign_id = self._campaign(account_id, name="Burn", status="active")
        spend = pulse_ad_payments.record_spend_event(
            self.conn, campaign_id, None, "feed_inline", amount_cents=4500, idempotency_key="burn-1"
        )
        self.assertTrue(spend["ok"])
        summary = pulse_ad_payments.wallet_summary(self.conn, OWNER, account_id)
        self.assertEqual(summary["spendable_balance_cents"], 500)
        self.assertTrue(summary["needs_topup"])
        notes = self.conn.execute(
            "SELECT * FROM pulse_ad_notifications WHERE account_id=? AND notification_type='ad_wallet_low_balance'",
            (account_id,),
        ).fetchall()
        self.assertEqual(len(notes), 1)

        # Disable: settings persist, flag clears.
        pulse_ad_payments.set_auto_topup(self.conn, OWNER, account_id, {"enabled": False})
        summary = pulse_ad_payments.wallet_summary(self.conn, OWNER, account_id)
        self.assertFalse(summary["auto_topup"]["enabled"])
        self.assertFalse(summary["needs_topup"])

    def test_list_transactions_cursor_pagination(self):
        account_id = self._account(business_type="brand")
        for index in range(5):
            self._spend(account_id, 0, None, 10 + index, idem=f"page-{index}")
        page1 = pulse_ad_payments.list_transactions(self.conn, OWNER, account_id, limit=2)
        self.assertEqual(len(page1["transactions"]), 2)
        ids1 = [t["id"] for t in page1["transactions"]]
        self.assertEqual(ids1, sorted(ids1, reverse=True))  # newest first
        self.assertEqual(page1["next_before_id"], ids1[-1])

        page2 = pulse_ad_payments.list_transactions(
            self.conn, OWNER, account_id, limit=2, before_id=page1["next_before_id"]
        )
        ids2 = [t["id"] for t in page2["transactions"]]
        self.assertEqual(len(ids2), 2)
        self.assertTrue(max(ids2) < min(ids1))  # no overlap between pages

        page3 = pulse_ad_payments.list_transactions(
            self.conn, OWNER, account_id, limit=2, before_id=page2["next_before_id"]
        )
        ids3 = [t["id"] for t in page3["transactions"]]
        self.assertEqual(len(ids3), 1)
        self.assertIsNone(page3["next_before_id"])
        self.assertEqual(set(ids1 + ids2 + ids3), set(range(min(ids3), max(ids1) + 1)))
        # Stripe identifiers never leave the server.
        for item in page1["transactions"]:
            self.assertNotIn("idempotency_key", item)
            self.assertNotIn("metadata_json", item)

    def test_ledger_reconciliation_invariant(self):
        """funding -> reserve -> spend -> refund keeps the wallet equal to its ledger.

        Invariant proved (all sums over posted pulse_ad_wallet_transactions):
          available == funding - spend - refund - chargeback
          lifetime_spent == spend
          lifetime_funded == funding - reversed
          reserved == reserve - spend (floored at 0)
          spendable == max(0, available + credits - reserved)
        """
        account_id = self._account(business_type="brand")
        session_id, _ = self._fund(account_id, 10000, event_id="evt_r", pi="pi_r", sess="cs_r")
        campaign_id = self._campaign(account_id, name="Flow", status="active", daily=2000)

        reserve = pulse_ad_payments.reserve_campaign_budget(self.conn, OWNER, campaign_id)
        self.assertEqual(reserve["reserved_cents"], 2000)

        for index in range(3):
            result = pulse_ad_payments.record_spend_event(
                self.conn, campaign_id, None, "feed_inline", amount_cents=500,
                idempotency_key=f"flow-{index}",
            )
            self.assertTrue(result["ok"])
            self.assertEqual(result["funded_from"], {"available_balance_cents": 500})
        # A replayed delivery event dedupes and moves no money.
        replay = pulse_ad_payments.record_spend_event(
            self.conn, campaign_id, None, "feed_inline", amount_cents=500, idempotency_key="flow-0"
        )
        self.assertTrue(replay.get("deduped"))

        # Partial refund of the top-up (Stripe reports cumulative amount_refunded).
        reversal = pulse_ad_payments.reverse_wallet_funding(
            self.conn, "evt_refund_1",
            {"object": "charge", "id": "ch_r", "payment_intent": "pi_r", "amount_refunded": 2000},
            "charge.refunded",
        )
        self.assertTrue(reversal["ok"])
        self.assertEqual(reversal["reversed_cents"], 2000)
        # Replayed webhook: deduped, not double-debited.
        again = pulse_ad_payments.reverse_wallet_funding(
            self.conn, "evt_refund_1",
            {"object": "charge", "id": "ch_r", "payment_intent": "pi_r", "amount_refunded": 2000},
            "charge.refunded",
        )
        self.assertTrue(again.get("deduped") or again.get("noop"))

        wallet = self._wallet(account_id)
        sums = {
            row["transaction_type"]: row["total"]
            for row in self.conn.execute(
                """
                SELECT transaction_type, COALESCE(SUM(amount_cents),0) AS total
                FROM pulse_ad_wallet_transactions
                WHERE account_id=? AND status='posted' GROUP BY transaction_type
                """,
                (account_id,),
            ).fetchall()
        }
        funding = sums.get("funding", 0)
        spend = sums.get("spend", 0)
        refund = sums.get("refund", 0) + sums.get("chargeback", 0)
        reserve_total = sums.get("reserve", 0)
        self.assertEqual((funding, spend, refund, reserve_total), (10000, 1500, 2000, 2000))

        # The invariant, from the ledger alone:
        self.assertEqual(wallet["available_balance_cents"], funding - spend - refund)   # 6500
        self.assertEqual(wallet["lifetime_spent_cents"], spend)                          # 1500
        self.assertEqual(wallet["lifetime_funded_cents"], funding - refund)              # 8000
        self.assertEqual(wallet["reserved_budget_cents"], max(0, reserve_total - spend)) # 500
        spendable = pulse_ad_payments.spendable_balance_cents(self.conn, account_id)
        credits = (
            wallet["promotional_credits_cents"]
            + wallet["bonus_credits_cents"]
            + wallet["refund_credits_cents"]
        )
        self.assertEqual(
            spendable,
            max(0, wallet["available_balance_cents"] + credits - wallet["reserved_budget_cents"]),
        )
        self.assertEqual(spendable, 6000)

        # The refund is also on the staff-facing refunds table, matching the ledger.
        refund_rows = self.conn.execute(
            "SELECT * FROM pulse_ad_refunds WHERE account_id=?", (account_id,)
        ).fetchall()
        self.assertEqual(len(refund_rows), 1)
        self.assertEqual(refund_rows[0]["amount_cents"], 2000)
        self.assertEqual(refund_rows[0]["funding_session_id"], session_id)

        # Nothing owed while the balance is positive.
        summary = pulse_ad_payments.wallet_summary(self.conn, OWNER, account_id)
        self.assertEqual(summary["amount_owed_cents"], 0)
        self.assertEqual(summary["available_balance_cents"], 6500)


# ---------------------------------------------------------------------------
# Isolation
# ---------------------------------------------------------------------------

class CrossAccountTests(BaseCase):
    def test_stranger_denied_everywhere(self):
        account_id = self._account(owner=OWNER, business_type="brand")
        campaign_id = self._campaign(account_id, name="Mine", status="active")
        for call in (
            lambda: pulse_ads_reporting.build_report(self.conn, STRANGER, account_id, {}),
            lambda: pulse_ads_reporting.attribution_status(self.conn, STRANGER, campaign_id),
            lambda: pulse_ads_insights.build_insights(self.conn, STRANGER, account_id),
            lambda: pulse_ads_insights.apply_insight(
                self.conn, STRANGER, {"account_id": account_id, "insight_id": "x", "approve": True}
            ),
            lambda: pulse_ad_payments.wallet_summary(self.conn, STRANGER, account_id),
            lambda: pulse_ad_payments.list_invoices(self.conn, STRANGER, account_id),
            lambda: pulse_ad_payments.list_transactions(self.conn, STRANGER, account_id),
            lambda: pulse_ad_payments.set_spending_limits(self.conn, STRANGER, account_id, {"daily_limit_cents": 1}),
            lambda: pulse_ad_payments.set_auto_topup(self.conn, STRANGER, account_id, {"enabled": False}),
        ):
            with self.assertRaises(PulseAdsError) as ctx:
                call()
            self.assertIn(ctx.exception.status_code, (403, 404))

    def test_campaign_filter_cannot_cross_accounts(self):
        account_id = self._account(owner=OWNER)
        other_account = self._account(owner=STRANGER)
        foreign_campaign = self._campaign(other_account, name="Theirs")
        with self.assertRaises(PulseAdsError) as ctx:
            pulse_ads_reporting.build_report(
                self.conn, OWNER, account_id, {"campaign_id": foreign_campaign}
            )
        self.assertEqual(ctx.exception.status_code, 404)

    def test_analyst_can_read_reports_but_not_wallet_or_apply(self):
        account_id = self._account(owner=OWNER, business_type="brand")
        now = now_iso()
        self.conn.execute(
            "INSERT INTO pulse_ad_team_members (account_id, user_id, role, status, created_at, updated_at) VALUES (?, ?, 'analyst', 'active', ?, ?)",
            (account_id, ANALYST, now, now),
        )
        self.conn.commit()
        report = pulse_ads_reporting.build_report(self.conn, ANALYST, account_id, {})
        self.assertEqual(report["rows"], [])
        insights = pulse_ads_insights.build_insights(self.conn, ANALYST, account_id)
        self.assertIn("recommendations", insights)
        # Wallet endpoints stay owner-only; applying insights needs a write role.
        with self.assertRaises(PulseAdsError):
            pulse_ad_payments.wallet_summary(self.conn, ANALYST, account_id)
        with self.assertRaises(PulseAdsError) as ctx:
            pulse_ads_insights.apply_insight(
                self.conn, ANALYST, {"account_id": account_id, "insight_id": "x", "approve": True}
            )
        self.assertEqual(ctx.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
