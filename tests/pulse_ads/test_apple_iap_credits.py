"""Apple StoreKit consumable -> Ad Wallet credit path + unified payment router.

Real-crypto tests: transactions are signed with a self-generated EC chain (the
same helper the subscription JWS tests use) and verified through the production
``verify_and_decode_jws`` with the test root injected as the trust anchor. No
Apple secrets, no network.

What must hold:
  * the server decides the provider (router policy), never the client;
  * ONE VERIFIED APPLE TRANSACTION = AT MOST ONE CREDIT (DB-unique key);
  * the credited amount comes from the server catalog, never the payload;
  * wrong bundle / unknown product / tampered JWS / sandbox-in-prod credit nothing;
  * refunds are compensating reversals: balance may go negative, campaigns pause,
    replays dedupe.

    python3 -m unittest tests.pulse_ads.test_apple_iap_credits
"""

import os
import sqlite3
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "business_os"))

from services import pulse_ad_payments  # noqa: E402
from services import pulse_apple_iap_credits as adiap  # noqa: E402
from services import pulse_payment_router as router  # noqa: E402
from services.business_os.entitlements import iap_apple as apple  # noqa: E402
from _iap_jws_util import Chain  # noqa: E402


SCHEMA = """
CREATE TABLE pulse_ad_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_user_id INTEGER,
    business_name TEXT,
    business_type TEXT,
    status TEXT DEFAULT 'active',
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE pulse_ad_wallets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER,
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
    updated_at TEXT
);
CREATE TABLE pulse_ad_wallet_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER,
    campaign_id INTEGER,
    creative_id INTEGER,
    transaction_type TEXT,
    amount_cents INTEGER,
    currency TEXT,
    status TEXT,
    idempotency_key TEXT UNIQUE,
    description TEXT,
    metadata_json TEXT,
    created_at TEXT
);
CREATE TABLE pulse_ad_wallet_funding_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER,
    user_id INTEGER,
    amount_cents INTEGER,
    currency TEXT DEFAULT 'usd',
    status TEXT DEFAULT 'pending',
    provider TEXT DEFAULT 'stripe',
    provider_session_id TEXT,
    provider_payment_intent_id TEXT,
    provider_charge_id TEXT,
    reversed_cents INTEGER DEFAULT 0,
    idempotency_key TEXT UNIQUE,
    checkout_url TEXT,
    created_at TEXT,
    updated_at TEXT
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
CREATE TABLE pulse_ad_receipts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER,
    funding_session_id INTEGER,
    invoice_number TEXT,
    receipt_number TEXT UNIQUE,
    amount_cents INTEGER,
    currency TEXT,
    status TEXT,
    provider TEXT,
    provider_reference_hash TEXT,
    created_at TEXT
);
CREATE TABLE pulse_ad_campaigns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ad_account_id INTEGER,
    campaign_name TEXT,
    status TEXT DEFAULT 'draft',
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE pulse_ad_audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_user_id INTEGER,
    action TEXT,
    entity_type TEXT,
    entity_id TEXT,
    before_json TEXT,
    after_json TEXT,
    ip_hash TEXT,
    user_agent_hash TEXT,
    created_at TEXT
);
"""

OWNER_ID = 8001
PRODUCT_TIER2 = "com.pulsesoc.adcredits.tier2"  # $9.99 -> 999c


def _signed_transaction(chain, **overrides):
    txn = {
        "transactionId": "9000000000000001",
        "originalTransactionId": "9000000000000001",
        "bundleId": "com.pulsesoc.app",
        "productId": PRODUCT_TIER2,
        "type": "Consumable",
        "environment": "Production",
        "quantity": 1,
    }
    txn.update(overrides)
    return chain.sign_jws(txn), txn


def _refund_notification(txn: dict, *, uuid_suffix="r1", ntype="REFUND") -> dict:
    """A verified-shape ASSN v2 dict, as the webhook hands to the handler."""
    return {
        "notificationType": ntype,
        "notificationUUID": f"uuid-{txn['transactionId']}-{uuid_suffix}",
        "version": "2.0",
        "data": {
            "bundleId": txn.get("bundleId", "com.pulsesoc.app"),
            "environment": txn.get("environment", "Production"),
            "transactionInfo": {**txn, "revocationReason": 0},
        },
    }


class _Base(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        pulse_ad_payments.ensure_schema(self.conn)
        cur = self.conn.cursor()
        cur.execute(
            "INSERT INTO pulse_ad_accounts (owner_user_id, business_name, business_type,"
            " status, created_at, updated_at) VALUES (?, 'Acme', 'business', 'active', '', '')",
            (OWNER_ID,),
        )
        self.account_id = cur.lastrowid
        self.conn.commit()
        self.chain = Chain()
        self.decoder = lambda token: apple.verify_and_decode_jws(
            token, trust_anchors=[self.chain.root_der()])

    def tearDown(self):
        self.conn.close()

    def _credit(self, signed):
        return adiap.credit_ad_wallet_from_apple_transaction(
            self.conn, OWNER_ID, self.account_id, signed, decode_jws=self.decoder)

    def _wallet(self):
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM pulse_ad_wallets WHERE account_id=?", (self.account_id,))
        return dict(cur.fetchone() or {})


class PaymentRouterTests(unittest.TestCase):
    """The server decides the provider. The client's opinion does not exist."""

    def test_ios_digital_routes_to_apple_iap(self):
        d = router.route_payment(platform="ios", item_type="ad_credits")
        self.assertTrue(d["ok"])
        self.assertEqual(d["provider"], router.PROVIDER_APPLE_IAP)

    def test_web_digital_routes_to_stripe(self):
        d = router.route_payment(platform="web", item_type="ad_credits")
        self.assertEqual(d["provider"], router.PROVIDER_STRIPE)

    def test_ios_physical_routes_to_stripe_never_iap(self):
        d = router.route_payment(platform="ios", item_type="marketplace_physical")
        self.assertEqual(d["provider"], router.PROVIDER_STRIPE)

    def test_payouts_route_to_connect(self):
        d = router.route_payment(platform="web", item_type="creator_payout")
        self.assertEqual(d["provider"], router.PROVIDER_STRIPE_CONNECT)

    def test_promo_routes_to_internal_ledger(self):
        d = router.route_payment(platform="ios", item_type="promo_credit_grant")
        self.assertEqual(d["provider"], router.PROVIDER_INTERNAL_LEDGER)

    def test_ambiguous_item_is_flagged_not_guessed(self):
        d = router.route_payment(platform="ios", item_type="mystery_box")
        self.assertFalse(d["ok"])
        self.assertTrue(d["flagged"])
        self.assertNotIn("provider", d)

    def test_unknown_platform_is_flagged(self):
        d = router.route_payment(platform="vision_pro_maybe", item_type="ad_credits")
        self.assertFalse(d["ok"])
        self.assertTrue(d["flagged"])

    def test_catalog_matches_asc_products(self):
        catalog = {p["product_id"]: p["amount_cents"] for p in router.adcredit_catalog()}
        self.assertEqual(catalog, {
            "com.pulsesoc.adcredits.tier1": 499,
            "com.pulsesoc.adcredits.tier2": 999,
            "com.pulsesoc.adcredits.tier3": 2499,
            "com.pulsesoc.adcredits.tier4": 4999,
            "com.pulsesoc.adcredits.tier5": 9999,
        })


class AppleCreditTests(_Base):
    def test_verified_purchase_credits_catalog_amount(self):
        signed, _ = _signed_transaction(self.chain)
        res = self._credit(signed)
        self.assertTrue(res["ok"])
        self.assertFalse(res["deduped"])
        self.assertEqual(res["amount_cents"], 999)
        self.assertEqual(res["provenance"], "cash_apple_iap")
        w = self._wallet()
        self.assertEqual(w["available_balance_cents"], 999)
        self.assertEqual(w["lifetime_funded_cents"], 999)
        # funding session + receipt carry the provider, provider id stays server-side
        cur = self.conn.cursor()
        fs = dict(cur.execute(
            "SELECT * FROM pulse_ad_wallet_funding_sessions WHERE account_id=?",
            (self.account_id,)).fetchone())
        self.assertEqual(fs["provider"], "apple_iap")
        self.assertEqual(fs["status"], "credited")
        rc = dict(cur.execute(
            "SELECT * FROM pulse_ad_receipts WHERE account_id=?",
            (self.account_id,)).fetchone())
        self.assertEqual(rc["provider"], "apple_iap")
        self.assertEqual(rc["amount_cents"], 999)

    def test_replay_is_one_credit_only(self):
        signed, _ = _signed_transaction(self.chain)
        self._credit(signed)
        res2 = self._credit(signed)
        self.assertTrue(res2["ok"])
        self.assertTrue(res2["deduped"])
        self.assertEqual(self._wallet()["available_balance_cents"], 999)
        cur = self.conn.cursor()
        n = cur.execute(
            "SELECT COUNT(*) FROM pulse_ad_wallet_transactions WHERE account_id=?",
            (self.account_id,)).fetchone()[0]
        self.assertEqual(n, 1)

    def test_same_transaction_cannot_credit_a_second_account(self):
        cur = self.conn.cursor()
        cur.execute(
            "INSERT INTO pulse_ad_accounts (owner_user_id, business_name, business_type,"
            " status, created_at, updated_at) VALUES (?, 'Other', 'business', 'active', '', '')",
            (OWNER_ID,),
        )
        second_account = cur.lastrowid
        self.conn.commit()
        signed, _ = _signed_transaction(self.chain)
        self._credit(signed)
        res2 = adiap.credit_ad_wallet_from_apple_transaction(
            self.conn, OWNER_ID, second_account, signed, decode_jws=self.decoder)
        self.assertTrue(res2["deduped"])  # global idempotency key blocks it
        w2 = dict(cur.execute(
            "SELECT * FROM pulse_ad_wallets WHERE account_id=?",
            (second_account,)).fetchone() or {"available_balance_cents": 0})
        self.assertEqual(w2.get("available_balance_cents", 0), 0)

    def test_quantity_multiplies_credit(self):
        signed, _ = _signed_transaction(
            self.chain, transactionId="9000000000000002", quantity=2)
        res = self._credit(signed)
        self.assertEqual(res["amount_cents"], 1998)

    def test_amount_comes_from_catalog_not_payload(self):
        # A hostile payload claiming a huge price still credits the catalog amount.
        signed, _ = _signed_transaction(
            self.chain, transactionId="9000000000000003", price=99999000)
        res = self._credit(signed)
        self.assertEqual(res["amount_cents"], 999)

    def test_wrong_bundle_rejected(self):
        signed, _ = _signed_transaction(self.chain, bundleId="com.evil.app")
        with self.assertRaises(adiap.AppleIapCreditError):
            self._credit(signed)
        self.assertEqual(self._wallet(), {})  # no wallet was even created

    def test_unknown_product_rejected(self):
        signed, _ = _signed_transaction(self.chain, productId="com.pulsesoc.unknown")
        with self.assertRaises(adiap.AppleIapCreditError):
            self._credit(signed)

    def test_non_consumable_type_rejected(self):
        signed, _ = _signed_transaction(self.chain, type="Auto-Renewable Subscription")
        with self.assertRaises(adiap.AppleIapCreditError):
            self._credit(signed)

    def test_revoked_transaction_rejected(self):
        signed, _ = _signed_transaction(self.chain, revocationReason=0)
        with self.assertRaises(adiap.AppleIapCreditError):
            self._credit(signed)

    def test_tampered_jws_rejected(self):
        signed, _ = _signed_transaction(self.chain)
        h, p, s = signed.split(".")
        p2 = ("A" if p[0] != "A" else "B") + p[1:]
        with self.assertRaises(apple.AppleJWSError):
            self._credit(f"{h}.{p2}.{s}")
        self.assertEqual(self._wallet(), {})

    def test_untrusted_chain_rejected(self):
        stranger = Chain()
        signed, _ = _signed_transaction(stranger)
        with self.assertRaises(apple.AppleJWSError):
            self._credit(signed)  # decoder anchors on self.chain's root

    def test_sandbox_rejected_unless_allowed(self):
        signed, _ = _signed_transaction(self.chain, environment="Sandbox")
        old = os.environ.pop("APPLE_IAP_ALLOW_SANDBOX", None)
        try:
            with self.assertRaises(adiap.AppleIapCreditError):
                self._credit(signed)
            os.environ["APPLE_IAP_ALLOW_SANDBOX"] = "1"
            res = self._credit(signed)
            self.assertTrue(res["ok"])
            self.assertEqual(res["environment"], "Sandbox")
        finally:
            os.environ.pop("APPLE_IAP_ALLOW_SANDBOX", None)
            if old is not None:
                os.environ["APPLE_IAP_ALLOW_SANDBOX"] = old

    def test_non_owner_cannot_credit(self):
        signed, _ = _signed_transaction(self.chain)
        from services import pulse_ads_service
        with self.assertRaises(pulse_ads_service.PulseAdsError):
            adiap.credit_ad_wallet_from_apple_transaction(
                self.conn, OWNER_ID + 1, self.account_id, signed,
                decode_jws=self.decoder)

    def test_unconfigured_anchors_is_setup_required(self):
        signed, _ = _signed_transaction(self.chain)
        old = os.environ.pop("APPLE_ROOT_CA_CERTS", None)
        try:
            res = adiap.credit_ad_wallet_from_apple_transaction(
                self.conn, OWNER_ID, self.account_id, signed)
            self.assertFalse(res["ok"])
            self.assertEqual(res["status"], "setup_required")
        finally:
            if old is not None:
                os.environ["APPLE_ROOT_CA_CERTS"] = old


class AppleRefundTests(_Base):
    def _fund_and_get_txn(self):
        signed, txn = _signed_transaction(self.chain)
        self._credit(signed)
        return txn

    def test_refund_reverses_full_amount(self):
        txn = self._fund_and_get_txn()
        res = adiap.handle_apple_notification(self.conn, _refund_notification(txn))
        self.assertTrue(res["handled"] and res["ok"])
        self.assertEqual(res["reversed_cents"], 999)
        w = self._wallet()
        self.assertEqual(w["available_balance_cents"], 0)
        cur = self.conn.cursor()
        refund = dict(cur.execute(
            "SELECT * FROM pulse_ad_refunds WHERE account_id=?",
            (self.account_id,)).fetchone())
        self.assertEqual(refund["amount_cents"], 999)
        fs = dict(cur.execute(
            "SELECT * FROM pulse_ad_wallet_funding_sessions WHERE account_id=?",
            (self.account_id,)).fetchone())
        self.assertEqual(fs["status"], "reversed")

    def test_refund_replay_dedupes(self):
        txn = self._fund_and_get_txn()
        note = _refund_notification(txn)
        adiap.handle_apple_notification(self.conn, note)
        res2 = adiap.handle_apple_notification(self.conn, note)
        self.assertTrue(res2.get("deduped") or res2.get("noop"))
        self.assertEqual(self._wallet()["available_balance_cents"], 0)

    def test_refund_after_spend_goes_negative_and_pauses_campaigns(self):
        txn = self._fund_and_get_txn()
        cur = self.conn.cursor()
        # Simulate spend: drain the balance, keep a live campaign.
        cur.execute(
            "UPDATE pulse_ad_wallets SET available_balance_cents=100 WHERE account_id=?",
            (self.account_id,))
        cur.execute(
            "INSERT INTO pulse_ad_campaigns (ad_account_id, campaign_name, status,"
            " created_at, updated_at) VALUES (?, 'Live', 'active', '', '')",
            (self.account_id,))
        self.conn.commit()
        res = adiap.handle_apple_notification(self.conn, _refund_notification(txn))
        self.assertEqual(res["available_balance_cents"], 100 - 999)
        self.assertEqual(res["campaigns_paused"], 1)
        self.assertEqual(
            pulse_ad_payments.spendable_balance_cents(self.conn, self.account_id), 0)

    def test_orphan_refund_is_ignored_not_invented(self):
        txn = {"transactionId": "424242", "productId": PRODUCT_TIER2}
        res = adiap.handle_apple_notification(self.conn, _refund_notification(txn))
        self.assertTrue(res["handled"])
        self.assertTrue(res.get("ignored"))
        cur = self.conn.cursor()
        n = cur.execute("SELECT COUNT(*) FROM pulse_ad_refunds").fetchone()[0]
        self.assertEqual(n, 0)

    def test_non_adcredit_product_not_handled(self):
        note = _refund_notification(
            {"transactionId": "1", "productId": "com.pulsesoc.premium.monthly"})
        res = adiap.handle_apple_notification(self.conn, note)
        self.assertFalse(res["handled"])

    def test_webhook_entry_verifies_before_projecting(self):
        txn = self._fund_and_get_txn()
        # Full nested-JWS notification signed by the test chain.
        inner = self.chain.sign_jws({**txn, "revocationReason": 0})
        payload = {
            "notificationType": "REFUND",
            "notificationUUID": f"uuid-{txn['transactionId']}-wh",
            "version": "2.0",
            "data": {"bundleId": "com.pulsesoc.app", "environment": "Production",
                     "signedTransactionInfo": inner},
        }
        signed_payload = self.chain.sign_jws(payload)
        verifier = apple.AppleNotificationVerifier(trust_anchors=[self.chain.root_der()])
        res = adiap.handle_webhook_signed_payload(
            signed_payload, verifier=verifier, conn=self.conn)
        self.assertTrue(res["handled"] and res["ok"])
        # Tampered envelope is a flat non-handle, no wallet effect.
        h, p, s = signed_payload.split(".")
        bad = f"{h}.{('A' if p[0] != 'A' else 'B') + p[1:]}.{s}"
        res2 = adiap.handle_webhook_signed_payload(bad, verifier=verifier, conn=self.conn)
        self.assertFalse(res2["handled"])


if __name__ == "__main__":
    unittest.main()
