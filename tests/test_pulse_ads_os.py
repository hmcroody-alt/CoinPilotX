"""Route-level tests for the Advertising OS layer (services/pulse_ads_os.py).

Covers the fixed mobile contract: one-shot campaign creation with idempotent
replay, targeting upsert with real-count estimates, saved audiences, the
content inventory, honest reports (zero rows -> zeros; seeded rows -> exact
math), rule-based insights, wallet depth (transactions + spending limits with
enforcement), the policy center with appeals, and rejected-creative editing
plus resubmission through the existing lifecycle.

Runs against a temp sqlite file so nothing touches coinpilotx.db.

Run: python3 -m unittest tests.test_pulse_ads_os -v
"""

import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="pulse_ads_os_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

import bot  # noqa: E402
from services import pulse_ad_payments  # noqa: E402

OWNER = 96001
OTHER = 96002
VIEWER_A = 96011
VIEWER_B = 96012

NOW = "2026-08-05T12:00:00+00:00"
DAY_LATER = "2026-08-06T12:00:00+00:00"


class PulseAdsOsTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db_path = _DB_PATH
        bot.init_db()
        conn = sqlite3.connect(cls.db_path)
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='pulse_ad_campaigns'")
        if not cur.fetchone():
            conn.close()
            raise unittest.SkipTest("init_db did not create the ads schema in the temp database")
        conn.close()
        cls._real_require_account = bot.require_account
        cls._real_verify_write = bot.pulse_ads_verify_write
        cls._real_rate_limited = bot.pulse_ads_rate_limited
        cls._current_user = {"user_id": OWNER, "username": "ads_owner"}
        bot.require_account = lambda: dict(cls._current_user)
        bot.pulse_ads_verify_write = lambda: True
        bot.pulse_ads_rate_limited = lambda *args, **kwargs: False
        bot.webhook_app.config["TESTING"] = True
        cls.client = bot.webhook_app.test_client()

    @classmethod
    def tearDownClass(cls):
        bot.require_account = cls._real_require_account
        bot.pulse_ads_verify_write = cls._real_verify_write
        bot.pulse_ads_rate_limited = cls._real_rate_limited

    def setUp(self):
        type(self)._current_user = {"user_id": OWNER, "username": "ads_owner"}
        conn = self.db()
        cur = conn.cursor()
        for user_id, username in ((OWNER, "ads_owner"), (OTHER, "ads_other"), (VIEWER_A, "viewer_a"), (VIEWER_B, "viewer_b")):
            cur.execute(
                "INSERT OR IGNORE INTO users (user_id, username, display_name) VALUES (?,?,?)",
                (user_id, username, username),
            )
        conn.commit()
        conn.close()

    def login(self, user_id, username="switched"):
        type(self)._current_user = {"user_id": user_id, "username": username}

    # -- helpers --------------------------------------------------------------

    def db(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def make_account(self, owner=OWNER, status="active", verification="verified"):
        conn = self.db()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO pulse_ad_accounts
            (owner_user_id, business_name, business_type, status, verification_status, created_at, updated_at)
            VALUES (?, 'OS Test Advertiser', 'business', ?, ?, ?, ?)
            """,
            (owner, status, verification, NOW, NOW),
        )
        account_id = cur.lastrowid
        conn.commit()
        conn.close()
        return account_id

    def make_campaign(self, account_id, objective="engagement", status="draft", name="OS Campaign",
                      lifetime_budget_cents=0, daily_budget_cents=1000, spent_cents=0, created_at=NOW):
        conn = self.db()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO pulse_ad_campaigns
            (ad_account_id, campaign_name, objective, status, budget_type, daily_budget_cents,
             lifetime_budget_cents, spent_cents, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'daily', ?, ?, ?, ?, ?)
            """,
            (account_id, name, objective, status, daily_budget_cents, lifetime_budget_cents, spent_cents, created_at, created_at),
        )
        campaign_id = cur.lastrowid
        conn.commit()
        conn.close()
        return campaign_id

    def make_creative(self, account_id, campaign_id, status="draft", moderation_status="draft",
                      title="OS Creative", creative_type="text", content_ref_type="", content_ref_id=0):
        conn = self.db()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO pulse_ad_creatives
            (ad_account_id, campaign_id, creative_type, title, body, destination_url, call_to_action,
             status, moderation_status, media_ready, content_ref_type, content_ref_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'body', 'https://example.com', 'Learn more', ?, ?, 1, ?, ?, ?, ?)
            """,
            (account_id, campaign_id, creative_type, title, status, moderation_status,
             content_ref_type, content_ref_id, NOW, NOW),
        )
        creative_id = cur.lastrowid
        conn.commit()
        conn.close()
        return creative_id

    def fund_wallet(self, account_id, cents):
        conn = self.db()
        pulse_ad_payments.ensure_wallet(conn, account_id)
        conn.execute("UPDATE pulse_ad_wallets SET available_balance_cents=? WHERE account_id=?", (cents, account_id))
        conn.commit()
        conn.close()

    def make_listing(self, seller=OWNER, status="active", approval="approved", title="OS Listing"):
        conn = self.db()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO marketplace_listings (seller_user_id, title, description, status, approval_status, created_at, updated_at)
            VALUES (?, ?, 'desc', ?, ?, ?, ?)
            """,
            (seller, title, status, approval, NOW, NOW),
        )
        listing_id = cur.lastrowid
        conn.commit()
        conn.close()
        return listing_id

    # -- item 6: full create ---------------------------------------------------

    def test_full_create_and_idempotent_replay(self):
        account_id = self.make_account()
        payload = {
            "idempotency_key": f"full-{account_id}",
            "ad_account_id": account_id,
            "campaign": {
                "campaign_name": "Traffic Push",
                "objective": "website_traffic",
                "budget_type": "daily",
                "daily_budget_cents": 1500,
            },
            "targeting": {"countries": ["NG"], "audience_mode": "everyone"},
            "placements": [],
            "creative": {"creative_type": "text", "title": "Visit us", "destination_url": "https://example.com"},
            "submit": False,
        }
        response = self.client.post("/api/pulse/ads/campaigns/full", json=payload)
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data["ok"])
        self.assertNotIn("duplicate", data)
        campaign = data["campaign"]
        self.assertEqual(campaign["objective_canonical"], "website_traffic")
        self.assertEqual(sorted(campaign["placements"]), ["feed_inline", "search_sponsored_result"])
        self.assertEqual(data["creative"]["title"], "Visit us")
        self.assertIn("estimate", data["targeting"])
        self.assertIsInstance(data["blockers"], list)

        replay = self.client.post("/api/pulse/ads/campaigns/full", json=payload)
        self.assertEqual(replay.status_code, 200)
        replay_data = replay.get_json()
        self.assertTrue(replay_data["ok"])
        self.assertTrue(replay_data["duplicate"])
        self.assertEqual(replay_data["campaign"]["id"], campaign["id"])
        conn = self.db()
        count = conn.execute(
            "SELECT COUNT(*) FROM pulse_ad_campaigns WHERE ad_account_id=?", (account_id,)
        ).fetchone()[0]
        conn.close()
        self.assertEqual(count, 1, "the replay must not create a second campaign")

    # -- item 2: targeting -----------------------------------------------------

    def test_targeting_upsert_and_estimate_shape(self):
        conn = self.db()
        for index in range(3):
            conn.execute(
                "UPDATE users SET country='NG', preferred_language='en', date_of_birth='1995-01-01' WHERE user_id=?",
                ((OWNER, OTHER, VIEWER_A)[index],),
            )
        conn.commit()
        conn.close()
        account_id = self.make_account()
        campaign_id = self.make_campaign(account_id)
        body = {
            "countries": ["ng", "GH"],
            "languages": ["EN"],
            "min_age": 18,
            "max_age": 40,
            "device_type": "mobile",
            "interests": ["music"],
            "keywords": ["afrobeats"],
            "audience_mode": "everyone",
        }
        response = self.client.put(f"/api/pulse/ads/campaigns/{campaign_id}/targeting", json=body)
        self.assertEqual(response.status_code, 200)
        targeting = response.get_json()["targeting"]
        self.assertEqual(targeting["countries"], ["NG", "GH"])
        self.assertEqual(targeting["languages"], ["en"])
        self.assertEqual(targeting["device_type"], "mobile")
        self.assertEqual(targeting["audience_mode"], "everyone")
        estimate = targeting["estimate"]
        self.assertLessEqual(estimate["estimated_min"], estimate["estimated_max"])
        self.assertGreaterEqual(estimate["estimated_max"], 3, "the three seeded NG users must be counted")
        self.assertIn(estimate["band"], {"narrow", "good", "broad"})

        # Second PUT replaces, it does not stack rows.
        second = self.client.put(
            f"/api/pulse/ads/campaigns/{campaign_id}/targeting",
            json={"countries": ["KE"], "audience_mode": "followers"},
        )
        self.assertEqual(second.status_code, 200)
        conn = self.db()
        rows = conn.execute("SELECT COUNT(*) FROM pulse_ad_targeting WHERE campaign_id=?", (campaign_id,)).fetchone()[0]
        conn.close()
        self.assertEqual(rows, 1)
        fetched = self.client.get(f"/api/pulse/ads/campaigns/{campaign_id}/targeting").get_json()["targeting"]
        self.assertEqual(fetched["countries"], ["KE"])
        self.assertEqual(fetched["audience_mode"], "followers")

    def test_targeting_rejects_bad_modes(self):
        account_id = self.make_account()
        campaign_id = self.make_campaign(account_id)
        response = self.client.put(
            f"/api/pulse/ads/campaigns/{campaign_id}/targeting", json={"audience_mode": "everybody"}
        )
        self.assertEqual(response.status_code, 400)
        response = self.client.put(
            f"/api/pulse/ads/campaigns/{campaign_id}/targeting", json={"device_type": "tv"}
        )
        self.assertEqual(response.status_code, 400)

    # -- item 3: saved audiences ----------------------------------------------

    def test_saved_audience_crud(self):
        account_id = self.make_account()
        created = self.client.post(
            "/api/pulse/ads/audiences",
            json={"account_id": account_id, "name": "VIP buyers", "definition": {"countries": ["NG"]}},
        )
        self.assertEqual(created.status_code, 200)
        audience = created.get_json()["audience"]
        self.assertEqual(audience["name"], "VIP buyers")
        audience_id = audience["id"]

        listing = self.client.get(f"/api/pulse/ads/audiences?account_id={account_id}").get_json()
        self.assertTrue(any(item["id"] == audience_id for item in listing["audiences"]))
        self.assertIsInstance(listing["engagement_presets"], list)
        for preset in listing["engagement_presets"]:
            self.assertIn("key", preset)
            self.assertIn("estimated_size", preset)

        patched = self.client.patch(f"/api/pulse/ads/audiences/{audience_id}", json={"name": "VIP buyers v2"})
        self.assertEqual(patched.status_code, 200)
        self.assertEqual(patched.get_json()["audience"]["name"], "VIP buyers v2")

        # Another user cannot touch it.
        self.login(OTHER)
        stranger = self.client.patch(f"/api/pulse/ads/audiences/{audience_id}", json={"name": "mine now"})
        self.assertEqual(stranger.status_code, 404)
        self.login(OWNER)

        archived = self.client.post(f"/api/pulse/ads/audiences/{audience_id}/archive", json={})
        self.assertEqual(archived.status_code, 200)
        after = self.client.get(f"/api/pulse/ads/audiences?account_id={account_id}").get_json()
        self.assertFalse(any(item["id"] == audience_id for item in after["audiences"]))

    # -- item 4: content inventory --------------------------------------------

    def test_content_inventory_lists_only_own_content(self):
        conn = self.db()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO pulse_posts (user_id, post_type, title, body, visibility, moderation_status, created_at)
            VALUES (?, 'post', 'My own post', 'hello', 'public', 'approved', ?)
            """,
            (OWNER, NOW),
        )
        own_post = cur.lastrowid
        cur.execute(
            """
            INSERT INTO pulse_posts (user_id, post_type, title, body, visibility, moderation_status, created_at)
            VALUES (?, 'post', 'Someone elses post', 'hi', 'public', 'approved', ?)
            """,
            (OTHER, NOW),
        )
        other_post = cur.lastrowid
        try:
            cur.execute("UPDATE pulse_posts SET status='published' WHERE id IN (?, ?)", (own_post, other_post))
        except sqlite3.OperationalError:
            pass
        conn.commit()
        conn.close()
        own_listing = self.make_listing(seller=OWNER)
        self.make_listing(seller=OTHER, title="Not mine")

        response = self.client.get("/api/pulse/ads/content-inventory?kinds=post,listing&limit=25")
        self.assertEqual(response.status_code, 200)
        items = response.get_json()["items"]
        keys = {(item["kind"], item["id"]) for item in items}
        self.assertIn(("post", own_post), keys)
        self.assertIn(("listing", own_listing), keys)
        self.assertNotIn(("post", other_post), keys)
        for item in items:
            for field in ("kind", "id", "title", "thumbnail_url", "created_at", "metrics", "eligible", "ineligible_reason"):
                self.assertIn(field, item)
            self.assertEqual(sorted(item["metrics"]), ["comments", "likes", "views"])

    # -- item 8: reports -------------------------------------------------------

    def test_reports_with_no_data_are_all_zeros(self):
        account_id = self.make_account()
        response = self.client.get(f"/api/pulse/ads/reports?account_id={account_id}")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["rows"], [])
        totals = data["totals"]
        for field in ("spend_cents", "impressions", "reach", "clicks", "results", "purchases", "revenue_cents", "roas"):
            self.assertEqual(totals[field], 0, field)

    def _seed_delivery(self, account_id, campaign_id, creative_id):
        conn = self.db()
        cur = conn.cursor()
        impressions = [
            (VIEWER_A, ""),
            (VIEWER_A, ""),
            (VIEWER_B, ""),
            (None, "anon-session-1"),
        ]
        for viewer, session_id in impressions:
            cur.execute(
                """
                INSERT INTO pulse_ad_impressions
                (campaign_id, creative_id, placement_key, viewer_user_id, session_id, created_at)
                VALUES (?, ?, 'feed_inline', ?, ?, ?)
                """,
                (campaign_id, creative_id, viewer, session_id, NOW),
            )
        for viewer in (VIEWER_A, VIEWER_B):
            cur.execute(
                """
                INSERT INTO pulse_ad_clicks
                (campaign_id, creative_id, placement_key, viewer_user_id, session_id, clicked_at, created_at)
                VALUES (?, ?, 'feed_inline', ?, '', ?, ?)
                """,
                (campaign_id, creative_id, viewer, NOW, NOW),
            )
        for index in range(2):
            cur.execute(
                """
                INSERT INTO pulse_ad_wallet_transactions
                (account_id, campaign_id, creative_id, transaction_type, amount_cents, status,
                 idempotency_key, description, created_at)
                VALUES (?, ?, ?, 'spend', 50, 'posted', ?, 'Ad delivery spend for feed_inline', ?)
                """,
                (account_id, campaign_id, creative_id, f"report-spend-{campaign_id}-{index}", NOW),
            )
        conn.commit()
        conn.close()

    def test_reports_math_with_seeded_delivery_and_attribution(self):
        listing_id = self.make_listing(seller=OWNER)
        account_id = self.make_account()
        campaign_id = self.make_campaign(account_id, objective="marketplace_sales", name="Sales Push")
        creative_id = self.make_creative(
            account_id, campaign_id, status="approved", moderation_status="approved",
            creative_type="listing", content_ref_type="listing", content_ref_id=listing_id,
        )
        self._seed_delivery(account_id, campaign_id, creative_id)
        conn = self.db()
        conn.execute(
            """
            INSERT INTO seller_transactions
            (buyer_user_id, seller_user_id, seller_type, item_type, item_id, amount_cents, currency, status, created_at, updated_at)
            VALUES (?, ?, 'merchant', 'marketplace_product', ?, 2000, 'USD', 'paid', ?, ?)
            """,
            (VIEWER_A, OWNER, listing_id, DAY_LATER, DAY_LATER),
        )
        conn.commit()
        conn.close()

        response = self.client.get(f"/api/pulse/ads/reports?account_id={account_id}&breakdown=campaign")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(len(data["rows"]), 1)
        row = data["rows"][0]
        self.assertEqual(row["label"], "Sales Push")
        self.assertEqual(row["spend_cents"], 100)
        self.assertEqual(row["impressions"], 4)
        self.assertEqual(row["reach"], 3)
        self.assertAlmostEqual(row["frequency"], 1.33)
        self.assertEqual(row["clicks"], 2)
        self.assertAlmostEqual(row["ctr"], 0.5)
        self.assertEqual(row["cpc_cents"], 50)
        self.assertEqual(row["purchases"], 1)
        self.assertEqual(row["revenue_cents"], 2000)
        self.assertEqual(row["results"], 1, "marketplace_sales results are attributed purchases")
        self.assertEqual(row["cost_per_result_cents"], 100)
        self.assertAlmostEqual(row["roas"], 20.0)
        self.assertEqual(data["totals"]["revenue_cents"], 2000)

        by_placement = self.client.get(
            f"/api/pulse/ads/reports?account_id={account_id}&breakdown=placement"
        ).get_json()
        self.assertEqual([r["key"] for r in by_placement["rows"]], ["feed_inline"])
        by_date = self.client.get(
            f"/api/pulse/ads/reports?account_id={account_id}&breakdown=date"
        ).get_json()
        self.assertEqual([r["key"] for r in by_date["rows"]], ["2026-08-05"])

    # -- item 9: insights ------------------------------------------------------

    def test_insights_empty_when_no_data(self):
        account_id = self.make_account()
        response = self.client.get(f"/api/pulse/ads/insights?account_id={account_id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["recommendations"], [])

    def test_insights_budget_exhaustion_rule_fires(self):
        account_id = self.make_account()
        campaign_id = self.make_campaign(
            account_id, status="active", lifetime_budget_cents=1000, spent_cents=950,
            created_at=bot.pulse_ads_service.now_iso() if hasattr(bot, "pulse_ads_service") else NOW,
        )
        self.fund_wallet(account_id, 5_000)
        response = self.client.get(f"/api/pulse/ads/insights?account_id={account_id}")
        self.assertEqual(response.status_code, 200)
        recommendations = response.get_json()["recommendations"]
        kinds = {item["kind"] for item in recommendations}
        self.assertIn("budget_exhaustion", kinds)
        match = next(item for item in recommendations if item["kind"] == "budget_exhaustion")
        self.assertEqual(match["campaign_id"], campaign_id)
        self.assertIn(match["severity"], {"info", "opportunity", "warning"})

    # -- item 7: wallet depth --------------------------------------------------

    def test_wallet_transactions_listing_and_pagination(self):
        account_id = self.make_account()
        conn = self.db()
        pulse_ad_payments.ensure_wallet(conn, account_id)
        for index in range(3):
            conn.execute(
                """
                INSERT INTO pulse_ad_wallet_transactions
                (account_id, transaction_type, amount_cents, status, idempotency_key, description, created_at)
                VALUES (?, 'fund', 1000, 'posted', ?, 'Wallet funding', ?)
                """,
                (account_id, f"fund-{account_id}-{index}", NOW),
            )
        conn.commit()
        conn.close()
        response = self.client.get(f"/api/pulse/ads/accounts/{account_id}/wallet/transactions")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(len(data["transactions"]), 3)
        first_page = self.client.get(
            f"/api/pulse/ads/accounts/{account_id}/wallet/transactions?limit=2"
        ).get_json()
        self.assertEqual(len(first_page["transactions"]), 2)
        self.assertIsNotNone(first_page["next_before_id"])
        second_page = self.client.get(
            f"/api/pulse/ads/accounts/{account_id}/wallet/transactions?limit=2&before_id={first_page['next_before_id']}"
        ).get_json()
        self.assertEqual(len(second_page["transactions"]), 1)
        # Another user's account is invisible.
        self.login(OTHER)
        denied = self.client.get(f"/api/pulse/ads/accounts/{account_id}/wallet/transactions")
        self.assertEqual(denied.status_code, 404)
        self.login(OWNER)

    def test_invoices_and_receipts_endpoints_answer(self):
        account_id = self.make_account()
        invoices = self.client.get(f"/api/pulse/ads/accounts/{account_id}/invoices")
        self.assertEqual(invoices.status_code, 200)
        self.assertEqual(invoices.get_json()["invoices"], [])
        receipts = self.client.get(f"/api/pulse/ads/accounts/{account_id}/receipts")
        self.assertEqual(receipts.status_code, 200)
        self.assertEqual(receipts.get_json()["receipts"], [])

    def test_spending_limit_set_and_enforced_on_spend(self):
        account_id = self.make_account()
        campaign_id = self.make_campaign(account_id, status="active")
        self.fund_wallet(account_id, 10_000)
        response = self.client.post(
            f"/api/pulse/ads/accounts/{account_id}/wallet/spending-limit",
            json={"daily_limit_cents": 100, "lifetime_limit_cents": None},
        )
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["daily_limit_cents"], 100)
        self.assertEqual(data["lifetime_limit_cents"], 0)

        conn = self.db()
        first = pulse_ad_payments.record_spend_event(
            conn, campaign_id, None, "feed_inline", amount_cents=60, idempotency_key=f"limit-{campaign_id}-1"
        )
        self.assertTrue(first["ok"])
        second = pulse_ad_payments.record_spend_event(
            conn, campaign_id, None, "feed_inline", amount_cents=60, idempotency_key=f"limit-{campaign_id}-2"
        )
        self.assertFalse(second["ok"])
        self.assertTrue(second["paused"])
        self.assertEqual(second["reason"], "daily_limit_reached")
        status = conn.execute("SELECT status FROM pulse_ad_campaigns WHERE id=?", (campaign_id,)).fetchone()[0]
        conn.close()
        self.assertEqual(status, "paused")

    def test_auto_topup_stores_settings_only(self):
        account_id = self.make_account()
        bad = self.client.post(
            f"/api/pulse/ads/accounts/{account_id}/wallet/auto-topup",
            json={"enabled": True, "threshold_cents": 0, "amount_cents": 0},
        )
        self.assertEqual(bad.status_code, 400)
        response = self.client.post(
            f"/api/pulse/ads/accounts/{account_id}/wallet/auto-topup",
            json={"enabled": True, "threshold_cents": 500, "amount_cents": 2000},
        )
        self.assertEqual(response.status_code, 200)
        conn = self.db()
        wallet = dict(conn.execute("SELECT * FROM pulse_ad_wallets WHERE account_id=?", (account_id,)).fetchone())
        transactions = conn.execute(
            "SELECT COUNT(*) FROM pulse_ad_wallet_transactions WHERE account_id=?", (account_id,)
        ).fetchone()[0]
        conn.close()
        self.assertEqual(wallet["auto_topup_enabled"], 1)
        self.assertEqual(wallet["auto_topup_threshold_cents"], 500)
        self.assertEqual(wallet["auto_topup_amount_cents"], 2000)
        self.assertEqual(transactions, 0, "auto-topup settings must never move money")

    # -- item 10: policy center + appeals + rejected edit ----------------------

    def test_policy_center_shape_and_appeal_flow(self):
        account_id = self.make_account()
        campaign_id = self.make_campaign(account_id)
        creative_id = self.make_creative(
            account_id, campaign_id, status="rejected", moderation_status="rejected", title="Rejected Ad"
        )
        response = self.client.get(f"/api/pulse/ads/policy-center?account_id={account_id}")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        for field in ("account_status", "verification_status", "counts", "rejected", "appeals", "restrictions"):
            self.assertIn(field, data)
        self.assertEqual(sorted(data["counts"]), ["approved", "in_review", "rejected", "restricted"])
        self.assertEqual(data["counts"]["rejected"], 1)
        self.assertTrue(any(item["id"] == creative_id for item in data["rejected"]))
        self.assertEqual(data["appeals"], [])

        appeal = self.client.post(
            f"/api/pulse/ads/creatives/{creative_id}/appeal",
            json={"message": "The ad follows every policy; please take another look."},
        )
        self.assertEqual(appeal.status_code, 200)
        appeal_data = appeal.get_json()["appeal"]
        self.assertEqual(appeal_data["status"], "open")
        self.assertEqual(appeal_data["creative_id"], creative_id)

        duplicate = self.client.post(
            f"/api/pulse/ads/creatives/{creative_id}/appeal", json={"message": "Again!"}
        )
        self.assertEqual(duplicate.status_code, 409)

        after = self.client.get(f"/api/pulse/ads/policy-center?account_id={account_id}").get_json()
        self.assertEqual(len(after["appeals"]), 1)
        conn = self.db()
        notified = conn.execute(
            "SELECT COUNT(*) FROM pulse_ad_notifications WHERE creative_id=? AND notification_type='creative_appeal_submitted'",
            (creative_id,),
        ).fetchone()[0]
        conn.close()
        self.assertEqual(notified, 1)

    def test_rejected_creative_edit_and_resubmit(self):
        account_id = self.make_account()
        campaign_id = self.make_campaign(account_id)
        creative_id = self.make_creative(
            account_id, campaign_id, status="rejected", moderation_status="rejected", title="Old Title"
        )
        response = self.client.patch(
            f"/api/pulse/ads/creatives/{creative_id}",
            json={"title": "Fixed Title", "primary_text": "Now policy compliant."},
        )
        self.assertEqual(response.status_code, 200)
        creative = response.get_json()["creative"]
        self.assertEqual(creative["title"], "Fixed Title")
        self.assertEqual(creative["status"], "draft")

        resubmit = self.client.post(
            f"/api/pulse/ads/creatives/{creative_id}/action", json={"action": "submit"}
        )
        self.assertEqual(resubmit.status_code, 200)
        conn = self.db()
        row = dict(conn.execute("SELECT status, moderation_status FROM pulse_ad_creatives WHERE id=?", (creative_id,)).fetchone())
        conn.close()
        self.assertEqual(row["status"], "pending_review")
        self.assertEqual(row["moderation_status"], "pending")

    def test_approved_creative_cannot_be_edited(self):
        account_id = self.make_account()
        campaign_id = self.make_campaign(account_id)
        creative_id = self.make_creative(
            account_id, campaign_id, status="approved", moderation_status="approved"
        )
        response = self.client.patch(f"/api/pulse/ads/creatives/{creative_id}", json={"title": "Sneaky"})
        self.assertEqual(response.status_code, 409)


if __name__ == "__main__":
    unittest.main(verbosity=2)
