"""Backend-managed PulseSoc Economy & Earnings state.

This module powers the user-facing Economy Center and the protected admin
Economy Command Center. It intentionally exposes owner-scoped summaries and
aggregate diagnostics only. Provider identifiers, raw payment details, card or
bank data, Stripe object IDs, and secrets must never be returned from here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from services import db as db_service


STRICT_STATES = {
    "READY",
    "ACTION REQUIRED",
    "REVIEW",
    "WARNING",
    "LOCKED",
    "PREMIUM",
    "ADMIN",
    "PARTIAL",
    "BETA",
    "COMING SOON",
}


ECONOMY_SECTIONS: tuple[dict[str, Any], ...] = (
    {"key": "wallets", "label": "Wallets", "route": "/admin/economy-command-center/wallets", "description": "Wallet balances, reserved funds, credits, holds, payment methods, fraud flags, and audit timeline."},
    {"key": "transactions", "label": "Transactions", "route": "/admin/economy-command-center/transactions", "description": "Funding, spend, refunds, credits, reserves, adjustments, idempotency, and ledger health."},
    {"key": "orders", "label": "Orders", "route": "/admin/economy-command-center/orders", "description": "Marketplace orders, pending orders, fulfillment, refunds, disputes, and payment status."},
    {"key": "sellers", "label": "Sellers", "route": "/admin/economy-command-center/sellers", "description": "Seller onboarding, KYC readiness, payout setup, store policy, trust, violations, and appeals."},
    {"key": "products", "label": "Products", "route": "/admin/economy-command-center/products", "description": "Product inventory, digital products, pricing, demand signals, review state, and marketplace safety."},
    {"key": "subscriptions", "label": "Subscriptions", "route": "/admin/economy-command-center/subscriptions", "description": "Plans, entitlements, renewals, cancellations, invoices, billing history, and iOS compliance boundaries."},
    {"key": "premium", "label": "Premium", "route": "/admin/economy-command-center/premium", "description": "Premium state, benefits, recommendations, entitlement readiness, and native iOS paid-feature restrictions."},
    {"key": "payouts", "label": "Payouts", "route": "/admin/economy-command-center/payouts", "description": "Payout readiness, bank verification state, tax review, payout history, failed payouts, and retry queue."},
    {"key": "revenue", "label": "Revenue", "route": "/admin/economy-command-center/revenue", "description": "Creator, marketplace, subscription, ad, affiliate, forecast, and trend revenue diagnostics."},
    {"key": "affiliate", "label": "Affiliate", "route": "/admin/economy-command-center/affiliate", "description": "Referrals, commissions, conversions, pending/completed payouts, and campaign performance."},
    {"key": "marketplace", "label": "Marketplace", "route": "/admin/economy-command-center/marketplace", "description": "Marketplace health, seller reputation, disputes, refunds, reviews, inventory, and fraud diagnostics."},
    {"key": "taxes", "label": "Taxes", "route": "/admin/economy-command-center/taxes", "description": "Tax status, tax-form readiness, payout tax holds, and finance compliance diagnostics."},
    {"key": "fraud", "label": "Fraud", "route": "/admin/economy-command-center/fraud", "description": "Fraud scoring, suspicious transactions, payment risk, AML readiness, disputes, chargebacks, and admin review."},
    {"key": "refunds", "label": "Refunds", "route": "/admin/economy-command-center/refunds", "description": "Refund queue, refund credits, rollback readiness, failed refunds, and audit trail."},
    {"key": "chargebacks", "label": "Chargebacks", "route": "/admin/economy-command-center/chargebacks", "description": "Chargeback queue, seller trust impact, evidence state, dispute workflow, and audit history."},
    {"key": "payment-providers", "label": "Payment Providers", "route": "/admin/economy-command-center/payment-providers", "description": "Provider health, safe configured/missing status, webhooks, retry state, and no-secret diagnostics."},
    {"key": "stripe", "label": "Stripe", "route": "/admin/economy-command-center/stripe", "description": "Stripe health and web advertiser funding status without exposing customer, subscription, price, or secret identifiers."},
    {"key": "apple-iap", "label": "Apple IAP", "route": "/admin/economy-command-center/apple-iap", "description": "Apple in-app purchase readiness, native iOS compliance, and paid-digital feature boundary checks."},
    {"key": "google-play-billing", "label": "Google Play Billing", "route": "/admin/economy-command-center/google-play-billing", "description": "Google Play Billing readiness, Android paid-digital future state, and provider health."},
    {"key": "audit", "label": "Economy Audit Logs", "route": "/admin/economy-command-center/audit", "description": "Money, wallet, campaign spend, seller, payout, refund, provider, and admin finance audit coverage."},
)


ECONOMY_SUBSYSTEM_BLUEPRINTS: tuple[dict[str, Any], ...] = (
    {
        "key": "wallet",
        "card_key": "wallet",
        "label": "Wallet",
        "route": "/dashboard/economy/wallet",
        "admin_route": "/admin/economy-command-center/wallets",
        "action": "Manage Wallet",
        "metric": "wallet_balance_cents",
        "description": "Balances, transactions, holds, reserved funds, spending, refunds, credits, payment methods, permissions, security, and fraud protection.",
        "intelligence": "Explains available, pending, reserved, and promotional balances without exposing payment-provider identifiers.",
        "automation": "Updates when funding, spending, refunds, credits, reserves, releases, chargebacks, or campaign spend events happen.",
        "protection": "No negative balance, duplicate credit, raw card data, bank data, or provider secret is surfaced.",
        "recovery": "Refunds, credits, reserves, failed funding sessions, and audit timelines support reversible finance operations.",
        "recommendations": ("Review recent transactions.", "Keep payment methods verified.", "Investigate unexpected holds before spending."),
    },
    {
        "key": "earnings",
        "card_key": "earnings",
        "label": "Earnings",
        "route": "/dashboard/economy/earnings",
        "admin_route": "/admin/economy-command-center/revenue",
        "action": "View Earnings",
        "metric": "available_earnings_cents",
        "description": "Total, monthly, yearly, source, pending, processing, completed, failed, estimated, tax, deduction, payout, and projection state.",
        "intelligence": "Summarizes earnings by safe source and separates pending, available, processing, and failed states.",
        "automation": "Updates after creator revenue, marketplace sales, subscription revenue, affiliate events, and payout processing.",
        "protection": "Owner-scoped only; other creators' earning details never appear.",
        "recovery": "Failed earning or payout states are visible as recoverable finance events.",
        "recommendations": ("Review payout readiness.", "Separate pending from available funds.", "Watch source trends before changing strategy."),
    },
    {
        "key": "marketplace",
        "card_key": "marketplace",
        "label": "Marketplace",
        "route": "/dashboard/economy/marketplace",
        "admin_route": "/admin/economy-command-center/marketplace",
        "action": "Marketplace Center",
        "metric": "marketplace_revenue_cents",
        "description": "Products, orders, inventory, shipping, digital products, refunds, disputes, reviews, seller reputation, fraud detection, analytics, and recommendations.",
        "intelligence": "Connects product, order, inventory, dispute, refund, and reputation signals.",
        "automation": "New sale updates wallet, revenue, orders, notifications, analytics, seller trust, and audit logs.",
        "protection": "Seller ownership, buyer privacy, fraud scoring, and refund state are enforced server-side.",
        "recovery": "Refund, dispute, and inventory issues route to admin and seller recovery workflows.",
        "recommendations": ("Review pending orders.", "Keep product inventory accurate.", "Resolve disputes quickly to protect seller trust."),
    },
    {
        "key": "seller-tools",
        "card_key": "seller_tools",
        "label": "Seller Tools",
        "route": "/dashboard/economy/seller-tools",
        "admin_route": "/admin/economy-command-center/sellers",
        "action": "Become a Seller",
        "metric": "seller_readiness",
        "state_signal": "seller_action_required",
        "description": "Seller onboarding, verification, KYC, business profile, tax forms, payout setup, inventory, shipping, store policies, violations, appeals, and seller trust.",
        "intelligence": "Calculates seller readiness from verification, business profile, tax, payout, product, and trust signals.",
        "automation": "Moves sellers through draft, review, approved, restricted, and appeal states.",
        "protection": "KYC and tax data remain protected; normal users only see their own seller state.",
        "recovery": "Rejected or restricted sellers receive clear safe next steps and appeal paths.",
        "recommendations": ("Complete seller profile.", "Prepare payout and tax information.", "Resolve violations before scaling inventory."),
    },
    {
        "key": "subscriptions",
        "card_key": "subscriptions",
        "label": "Subscriptions",
        "route": "/dashboard/economy/subscriptions",
        "admin_route": "/admin/economy-command-center/subscriptions",
        "action": "Manage Subscription",
        "metric": "subscription_count",
        "description": "Current plan, benefits, invoices, billing history, renewals, cancellation, upgrades, downgrades, payment history, and entitlements.",
        "intelligence": "Summarizes entitlement and billing state while honoring native iOS paid-digital restrictions.",
        "automation": "Renewals update premium, wallet, analytics, notifications, and audit history.",
        "protection": "Native iOS responses must not expose Stripe IDs or paid subscription identifiers.",
        "recovery": "Failed renewals and payment issues route to safe billing recovery without leaking secrets.",
        "recommendations": ("Review invoices and renewal state.", "Use web billing only where platform-compliant.", "Keep entitlements separate from native iOS core access."),
    },
    {
        "key": "premium",
        "card_key": "premium",
        "label": "Premium",
        "route": "/dashboard/economy/premium",
        "admin_route": "/admin/economy-command-center/premium",
        "action": "Premium Center",
        "metric": "premium_readiness",
        "description": "Premium benefits, AI, security, creator tools, history, recommendations, and upgrade guidance with App Store safe boundaries.",
        "intelligence": "Explains premium readiness and benefits without exposing paid digital unlocks in native iOS.",
        "automation": "Premium entitlement changes update recommendations, creator tools, AI visibility, security modules, and audit logs.",
        "protection": "Paid digital flows are blocked in native iOS unless Apple IAP is implemented and approved.",
        "recovery": "Entitlement mismatches can be diagnosed through admin Premium Operations.",
        "recommendations": ("Review benefits where platform-compliant.", "Keep iOS premium state core-only.", "Use admin diagnostics for entitlement issues."),
    },
    {
        "key": "creator-revenue",
        "card_key": "creator_revenue",
        "label": "Creator Revenue",
        "route": "/dashboard/economy/creator-revenue",
        "admin_route": "/admin/economy-command-center/revenue",
        "action": "Revenue Center",
        "metric": "creator_revenue_cents",
        "description": "Revenue sources, reels, videos, posts, marketplace, subscriptions, affiliate, sponsorship, trends, projections, and opportunities.",
        "intelligence": "Maps revenue sources and projected growth with owner-scoped signals.",
        "automation": "Creator revenue updates wallet, earnings, payout readiness, analytics, notifications, and audit logs.",
        "protection": "Creator revenue remains owner-only and admin-gated.",
        "recovery": "Failed or disputed revenue events are visible in admin revenue and audit surfaces.",
        "recommendations": ("Review highest-performing revenue source.", "Fix moderation issues that block monetization.", "Watch projected payout readiness."),
    },
    {
        "key": "payouts",
        "card_key": "payouts",
        "label": "Payouts",
        "route": "/dashboard/economy/payouts",
        "admin_route": "/admin/economy-command-center/payouts",
        "action": "Payout Center",
        "metric": "payout_readiness",
        "state_signal": "payout_action_required",
        "description": "Payout readiness, bank accounts, schedule, payment verification, tax review, payout history, failed payouts, and retry queue.",
        "intelligence": "Scores payout readiness from verified identity, seller status, tax, bank, fraud, and available balance.",
        "automation": "Payout events update earnings, wallet, tax, notifications, fraud review, and audit logs.",
        "protection": "Bank data and provider identifiers are never rendered; payout limits and review states are enforced.",
        "recovery": "Failed payout retry and rollback status are admin-visible.",
        "recommendations": ("Complete payout verification.", "Resolve tax or fraud holds.", "Review failed payout reasons before retrying."),
    },
    {
        "key": "revenue-analytics",
        "card_key": "revenue_analytics",
        "label": "Revenue Analytics",
        "route": "/dashboard/economy/revenue-analytics",
        "admin_route": "/admin/economy-command-center/revenue",
        "action": "Revenue Intelligence",
        "metric": "revenue_trend",
        "state": "BETA",
        "description": "Revenue charts, summaries, growth, decline, projections, seasonality, comparisons, and benchmarks.",
        "intelligence": "Turns safe revenue signals into trend and benchmark summaries.",
        "automation": "Revenue changes feed analytics, forecasts, creator revenue, marketplace, and wallet surfaces.",
        "protection": "Analytics are owner-scoped and hide other sellers' data.",
        "recovery": "Anomalies are inspectable through admin revenue and audit surfaces.",
        "recommendations": ("Compare source-level revenue before changing content strategy.", "Watch trend direction, not only balance.", "Review anomalies before payout."),
    },
    {
        "key": "ad-revenue",
        "card_key": "ad_revenue",
        "label": "Ad Revenue",
        "route": "/dashboard/economy/ad-revenue",
        "admin_route": "/admin/economy-command-center/revenue",
        "action": "Advertising Revenue",
        "metric": "ad_revenue_cents",
        "state": "PARTIAL",
        "description": "Ad eligibility, estimated earnings, RPM, CPM, impressions, fill rate, ad quality, advertiser score, and payment history.",
        "intelligence": "Connects approved ad delivery, placement performance, quality, and revenue readiness.",
        "automation": "Ad events update revenue, wallet, advertiser billing, analytics, and audit logs.",
        "protection": "Ad revenue eligibility is review-gated and does not expose advertiser-private data.",
        "recovery": "Skipped or failed ad revenue events remain diagnostics-visible.",
        "recommendations": ("Review eligibility before relying on ad revenue.", "Improve content quality and safety.", "Check fill rate and ad quality trends."),
    },
    {
        "key": "affiliate-revenue",
        "card_key": "affiliate_revenue",
        "label": "Affiliate Revenue",
        "route": "/dashboard/economy/affiliate-revenue",
        "admin_route": "/admin/economy-command-center/affiliate",
        "action": "Affiliate Center",
        "metric": "affiliate_revenue_cents",
        "state": "PARTIAL",
        "description": "Referrals, commissions, conversions, pending, completed, payouts, and campaign performance.",
        "intelligence": "Separates pending, approved, rejected, and payable affiliate revenue.",
        "automation": "Approved conversions update earnings, payout readiness, notifications, and audit logs.",
        "protection": "Fraud, duplicate conversion, and campaign tampering checks stay server-side.",
        "recovery": "Rejected or disputed conversions remain appeal/review visible.",
        "recommendations": ("Review conversion quality.", "Avoid suspicious referral spikes.", "Track completed payouts separately from pending commissions."),
    },
    {
        "key": "store-analytics",
        "card_key": "store_analytics",
        "label": "Store Analytics",
        "route": "/dashboard/economy/store-analytics",
        "admin_route": "/admin/economy-command-center/marketplace",
        "action": "Store Intelligence",
        "metric": "store_score",
        "state": "BETA",
        "description": "Sales, visitors, conversion, abandoned carts, repeat customers, refunds, heatmaps, and AI insights.",
        "intelligence": "Combines marketplace performance and seller health into actionable store insight.",
        "automation": "Order, refund, review, and inventory events update store intelligence and seller trust.",
        "protection": "Store analytics are seller-owner scoped.",
        "recovery": "Refund spikes and cart issues route to seller and admin recovery paths.",
        "recommendations": ("Review conversion and refund movement.", "Improve low-performing listings.", "Resolve review or dispute issues quickly."),
    },
    {
        "key": "product-intelligence",
        "card_key": "product_intelligence",
        "label": "Product Intelligence",
        "route": "/dashboard/economy/product-intelligence",
        "admin_route": "/admin/economy-command-center/products",
        "action": "Product Intelligence",
        "metric": "product_score",
        "state": "BETA",
        "description": "Top products, low performers, pricing, inventory alerts, demand prediction, and recommendations.",
        "intelligence": "Ranks product opportunities using marketplace, inventory, review, and demand signals.",
        "automation": "Product updates propagate to marketplace, store analytics, inventory alerts, and recommendations.",
        "protection": "Products are owner-scoped and moderation-aware.",
        "recovery": "Inventory and pricing issues are visible before they break sales.",
        "recommendations": ("Fix low inventory before promotions.", "Watch demand movement.", "Adjust pricing carefully after reviewing conversion."),
    },
    {
        "key": "revenue-forecast",
        "card_key": "revenue_forecasting",
        "label": "Revenue Forecast",
        "route": "/dashboard/economy/revenue-forecast",
        "admin_route": "/admin/economy-command-center/revenue",
        "action": "Revenue Forecast",
        "metric": "estimated_future_revenue_cents",
        "state": "PARTIAL",
        "description": "Monthly prediction, yearly prediction, creator forecast, marketplace forecast, subscription forecast, confidence, best/worst case, and recommendations.",
        "intelligence": "Projects revenue ranges from safe available signals with confidence labels.",
        "automation": "Forecast updates when revenue, sales, subscriptions, creator performance, or marketplace signals change.",
        "protection": "Forecasts are advisory and owner-scoped; no automatic financial action is taken.",
        "recovery": "Forecast anomalies are inspectable without changing wallet or payout state.",
        "recommendations": ("Treat forecasts as planning guidance.", "Review confidence before making spend decisions.", "Use best/worst cases to plan safely."),
    },
)


SUBSYSTEMS_BY_CARD = {item["card_key"]: item for item in ECONOMY_SUBSYSTEM_BLUEPRINTS}
SUBSYSTEMS_BY_KEY = {item["key"]: item for item in ECONOMY_SUBSYSTEM_BLUEPRINTS}


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except Exception:
        return default


def _row_value(row: Any, key: str, index: int = 0, default: Any = None) -> Any:
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[key]
    except Exception:
        try:
            return row[index]
        except Exception:
            return default


def _table_exists(cur: Any, table: str) -> bool:
    try:
        if db_service.IS_POSTGRES:
            cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_name=%s", (table,))
        else:
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
        return bool(cur.fetchone())
    except Exception:
        return False


def _param_sql(sql: str) -> str:
    return sql.replace("?", "%s") if db_service.IS_POSTGRES else sql


def _count(cur: Any, table: str, where: str = "1=1", params: tuple[Any, ...] = ()) -> int:
    if not _table_exists(cur, table):
        return 0
    try:
        cur.execute(_param_sql(f"SELECT COUNT(*) AS total FROM {table} WHERE {where}"), params)
        return _safe_int(_row_value(cur.fetchone(), "total", 0), 0)
    except Exception:
        return 0


def _sum_cents(cur: Any, table: str, column: str, where: str = "1=1", params: tuple[Any, ...] = ()) -> int:
    if not _table_exists(cur, table):
        return 0
    try:
        cur.execute(_param_sql(f"SELECT COALESCE(SUM({column}), 0) AS total FROM {table} WHERE {where}"), params)
        return _safe_int(_row_value(cur.fetchone(), "total", 0), 0)
    except Exception:
        return 0


def _latest_created(cur: Any, table: str, where: str = "1=1", params: tuple[Any, ...] = ()) -> str:
    if not _table_exists(cur, table):
        return ""
    try:
        cur.execute(_param_sql(f"SELECT created_at FROM {table} WHERE {where} ORDER BY created_at DESC LIMIT 1"), params)
        return str(_row_value(cur.fetchone(), "created_at", 0, "") or "")
    except Exception:
        return ""


def _metric_map(cur: Any, user_id: int) -> dict[str, Any]:
    ad_wallet_balance = _sum_cents(cur, "pulse_ad_wallets", "available_balance_cents", "owner_user_id=? OR user_id=?", (user_id, user_id))
    creator_wallet_balance = _sum_cents(cur, "creator_wallets", "available_balance_cents", "user_id=?", (user_id,))
    wallet_balance = ad_wallet_balance + creator_wallet_balance
    pending_earnings = (
        _sum_cents(cur, "creator_revenue_events", "amount_cents", "user_id=? AND lower(coalesce(status,'')) IN ('pending','processing','queued')", (user_id,))
        + _sum_cents(cur, "marketplace_orders", "seller_amount_cents", "seller_user_id=? AND lower(coalesce(status,'')) IN ('pending','processing','paid')", (user_id,))
    )
    available_earnings = (
        _sum_cents(cur, "creator_revenue_events", "amount_cents", "user_id=? AND lower(coalesce(status,'')) IN ('available','completed','settled')", (user_id,))
        + _sum_cents(cur, "seller_payouts", "amount_cents", "user_id=? AND lower(coalesce(status,'')) IN ('available','ready')", (user_id,))
    )
    marketplace_revenue = _sum_cents(cur, "marketplace_orders", "seller_amount_cents", "seller_user_id=? AND lower(coalesce(status,'')) IN ('paid','completed','fulfilled')", (user_id,))
    creator_revenue = _sum_cents(cur, "creator_revenue_events", "amount_cents", "user_id=?", (user_id,))
    subscription_revenue = _sum_cents(cur, "creator_subscription_revenue", "amount_cents", "creator_user_id=?", (user_id,))
    ad_revenue = _sum_cents(cur, "ad_revenue_events", "amount_cents", "user_id=? OR creator_user_id=?", (user_id, user_id))
    affiliate_revenue = _sum_cents(cur, "affiliate_commissions", "amount_cents", "user_id=?", (user_id,))
    active_orders = _count(cur, "marketplace_orders", "seller_user_id=? AND lower(coalesce(status,'')) IN ('paid','processing','fulfilled')", (user_id,))
    pending_orders = _count(cur, "marketplace_orders", "seller_user_id=? AND lower(coalesce(status,'')) IN ('pending','processing')", (user_id,))
    refund_queue = _count(cur, "pulse_ad_refunds", "owner_user_id=? AND lower(coalesce(status,'')) IN ('requested','pending','processing')", (user_id,)) + _count(cur, "marketplace_refunds", "seller_user_id=? AND lower(coalesce(status,'')) IN ('requested','pending','processing')", (user_id,))
    payment_failures = _count(cur, "payment_audit_logs", "user_id=? AND lower(coalesce(status,'')) IN ('failed','error','declined')", (user_id,)) + _count(cur, "pulse_ad_wallet_transactions", "owner_user_id=? AND lower(coalesce(status,'')) IN ('failed','error','declined')", (user_id,))
    disputes = _count(cur, "marketplace_disputes", "seller_user_id=? OR buyer_user_id=?", (user_id, user_id)) + _count(cur, "pulse_ad_chargebacks", "owner_user_id=?", (user_id,))
    seller_products = _count(cur, "marketplace_listings", "seller_user_id=? OR user_id=?", (user_id, user_id))
    seller_profile = _count(cur, "seller_profiles", "user_id=?", (user_id,))
    payout_accounts = _count(cur, "seller_payout_accounts", "user_id=?", (user_id,))
    subscriptions = _count(cur, "subscriptions", "user_id=?", (user_id,)) + _count(cur, "premium_subscriptions", "user_id=?", (user_id,))
    premium = _count(cur, "premium_subscriptions", "user_id=? AND lower(coalesce(status,'')) IN ('active','trialing')", (user_id,))
    fraudulent_events = _count(cur, "payment_audit_logs", "user_id=? AND lower(coalesce(event_type,'')) LIKE '%fraud%'", (user_id,)) + _count(cur, "command_center_security_events", "user_id=? AND lower(coalesce(event_type,'')) LIKE '%payment%'", (user_id,))
    trust_penalty = min(70, fraudulent_events * 15 + disputes * 8 + payment_failures * 5)
    trust_score = max(20, 100 - trust_penalty)
    fraud_risk = min(100, fraudulent_events * 20 + disputes * 10 + payment_failures * 7)
    payout_readiness = 35 + min(35, payout_accounts * 35) + min(20, seller_profile * 20) + min(10, available_earnings // 10000)
    payout_readiness = max(0, min(100, payout_readiness - min(30, fraud_risk // 3)))
    seller_readiness = min(100, seller_profile * 30 + payout_accounts * 25 + min(25, seller_products * 5) + max(0, 20 - disputes * 5))
    premium_readiness = 100 if premium else 55
    estimated_future = int((creator_revenue + marketplace_revenue + subscription_revenue + ad_revenue + affiliate_revenue) * 1.15) if (creator_revenue + marketplace_revenue + subscription_revenue + ad_revenue + affiliate_revenue) else 0
    revenue_total = creator_revenue + marketplace_revenue + subscription_revenue + ad_revenue + affiliate_revenue
    revenue_trend = min(100, 40 + revenue_total // 10000 + active_orders * 2)
    payment_health = max(35, 100 - min(65, payment_failures * 12 + disputes * 10 + fraudulent_events * 15))
    product_score = min(100, seller_products * 8 + active_orders * 4 + max(0, 30 - refund_queue * 5))
    store_score = min(100, seller_readiness // 2 + active_orders * 4 + max(0, 40 - disputes * 8 - refund_queue * 4))
    return {
        "wallet_balance_cents": wallet_balance,
        "pending_earnings_cents": pending_earnings,
        "available_earnings_cents": available_earnings,
        "marketplace_revenue_cents": marketplace_revenue,
        "creator_revenue_cents": creator_revenue,
        "subscription_revenue_cents": subscription_revenue,
        "ad_revenue_cents": ad_revenue,
        "affiliate_revenue_cents": affiliate_revenue,
        "estimated_future_revenue_cents": estimated_future,
        "seller_status": "Ready" if seller_readiness >= 80 else ("Action Required" if seller_readiness < 50 else "Review"),
        "seller_readiness": seller_readiness,
        "seller_action_required": 1 if seller_readiness < 50 else 0,
        "trust_score": trust_score,
        "payment_health": payment_health,
        "fraud_risk": fraud_risk,
        "payout_readiness": payout_readiness,
        "payout_action_required": 1 if payout_readiness < 70 and available_earnings > 0 else 0,
        "tax_status": "Ready" if _count(cur, "seller_tax_profiles", "user_id=? AND lower(coalesce(status,''))='verified'", (user_id,)) else "Review",
        "active_orders": active_orders,
        "pending_orders": pending_orders,
        "refund_queue": refund_queue,
        "payment_failures": payment_failures,
        "disputes": disputes,
        "revenue_trend": min(100, revenue_trend),
        "subscription_count": subscriptions,
        "premium_readiness": premium_readiness,
        "store_score": store_score,
        "product_score": product_score,
        "audit_events": _count(cur, "payment_audit_logs", "user_id=?", (user_id,)) + _count(cur, "pulse_ad_wallet_transactions", "owner_user_id=?", (user_id,)),
        "last_money_event": _latest_created(cur, "payment_audit_logs", "user_id=?", (user_id,)) or _latest_created(cur, "pulse_ad_wallet_transactions", "owner_user_id=?", (user_id,)),
    }


def _state_for_blueprint(blueprint: dict[str, Any], metrics: dict[str, Any]) -> str:
    explicit = blueprint.get("state")
    if explicit:
        return explicit
    signal = blueprint.get("state_signal")
    if signal and _safe_int(metrics.get(signal), 0) > 0:
        return "ACTION REQUIRED"
    if _safe_int(metrics.get("fraud_risk"), 0) >= 70:
        return "WARNING"
    if blueprint["card_key"] in {"wallet", "marketplace", "subscriptions", "premium"}:
        return "READY"
    return "BETA"


def _confidence_for_state(state: str, metrics: dict[str, Any]) -> int:
    base = {"READY": 91, "ACTION REQUIRED": 78, "WARNING": 72, "BETA": 68, "PARTIAL": 62, "PREMIUM": 64, "LOCKED": 55}.get(state, 60)
    if _safe_int(metrics.get("audit_events"), 0):
        base += 3
    if _safe_int(metrics.get("payment_failures"), 0) or _safe_int(metrics.get("disputes"), 0):
        base -= 6
    return max(35, min(96, base))


def _money(cents: Any) -> str:
    amount = _safe_int(cents, 0) / 100
    return f"${amount:,.2f}"


def _build_subsystem(blueprint: dict[str, Any], metrics: dict[str, Any]) -> dict[str, Any]:
    state = _state_for_blueprint(blueprint, metrics)
    metric_key = blueprint.get("metric") or ""
    raw_count = metrics.get(metric_key, 0)
    count = _safe_int(raw_count, 0)
    detail = blueprint.get("description") or ""
    if metric_key.endswith("_cents"):
        detail = f"{_money(raw_count)} tracked. {detail}"
    elif metric_key in {"trust_score", "payment_health", "fraud_risk", "payout_readiness", "seller_readiness", "premium_readiness", "store_score", "product_score", "revenue_trend"}:
        detail = f"{count}% signal. {detail}"
    return {
        **{key: value for key, value in blueprint.items() if key != "admin_route"},
        "state": state,
        "count": count,
        "count_display": _money(raw_count) if metric_key.endswith("_cents") else str(count),
        "detail": detail,
        "confidence": _confidence_for_state(state, metrics),
        "cta_label": blueprint.get("action"),
    }


def build_economy_state(conn: Any, user: dict[str, Any]) -> dict[str, Any]:
    cur = conn.cursor()
    user_id = _safe_int(user.get("user_id") or user.get("id"), 0)
    metrics = _metric_map(cur, user_id)
    subsystems = {item["key"].replace("-", "_"): _build_subsystem(item, metrics) for item in ECONOMY_SUBSYSTEM_BLUEPRINTS}
    cards = list(subsystems.values())
    hub = {
        "wallet_balance": _money(metrics.get("wallet_balance_cents")),
        "pending_earnings": _money(metrics.get("pending_earnings_cents")),
        "available_earnings": _money(metrics.get("available_earnings_cents")),
        "marketplace_revenue": _money(metrics.get("marketplace_revenue_cents")),
        "creator_revenue": _money(metrics.get("creator_revenue_cents")),
        "subscription_revenue": _money(metrics.get("subscription_revenue_cents")),
        "estimated_future_revenue": _money(metrics.get("estimated_future_revenue_cents")),
        "seller_status": metrics.get("seller_status") or "Review",
        "trust_score": _safe_int(metrics.get("trust_score")),
        "payment_health": _safe_int(metrics.get("payment_health")),
        "fraud_risk": _safe_int(metrics.get("fraud_risk")),
        "payout_readiness": _safe_int(metrics.get("payout_readiness")),
        "tax_status": metrics.get("tax_status") or "Review",
        "active_orders": _safe_int(metrics.get("active_orders")),
        "pending_orders": _safe_int(metrics.get("pending_orders")),
        "refund_queue": _safe_int(metrics.get("refund_queue")),
        "payment_failures": _safe_int(metrics.get("payment_failures")),
        "disputes": _safe_int(metrics.get("disputes")),
        "revenue_trend": _safe_int(metrics.get("revenue_trend")),
        "ai_financial_summary": "Financial signals are stable." if _safe_int(metrics.get("fraud_risk")) < 40 else "Payment or dispute risk needs review before payouts or campaign spend.",
        "recommended_next_actions": [
            "Review payout readiness before expecting withdrawals.",
            "Resolve refunds, disputes, or failed payments before scaling sales.",
            "Use marketplace and creator revenue trends as planning signals, not guaranteed payouts.",
        ],
    }
    return {
        "ok": True,
        "generated_at": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "user_id": user_id,
        "hub": hub,
        "metrics": metrics,
        "subsystems": subsystems,
        "cards": cards,
        "event_mesh": {
            "sale": ("wallet", "earnings", "marketplace", "notifications", "revenue-analytics"),
            "refund": ("wallet", "orders", "revenue", "fraud", "audit"),
            "subscription_renewal": ("premium", "subscriptions", "analytics", "notifications"),
            "fraud_detected": ("wallet", "risk-review", "review", "warning"),
            "chargeback": ("revenue", "seller-trust", "audit", "fraud"),
        },
        "security_boundary": "Owner-scoped money summaries only. No raw card, bank, provider identifiers, tokens, or secret data is exposed.",
    }


def state_for_widget(economy_state: dict[str, Any], widget_key: str) -> dict[str, Any]:
    blueprint = SUBSYSTEMS_BY_CARD.get(widget_key)
    if not blueprint:
        return {}
    subsystem = (economy_state.get("subsystems") or {}).get(blueprint["key"].replace("-", "_")) or {}
    return {
        "state": subsystem.get("state") or "READY",
        "route": subsystem.get("route") or blueprint.get("route"),
        "cta_label": subsystem.get("action") or blueprint.get("action"),
        "detail": subsystem.get("detail") or blueprint.get("description"),
        "count": subsystem.get("count"),
        "count_display": subsystem.get("count_display"),
        "confidence": subsystem.get("confidence"),
    }


def _admin_metric_map(cur: Any) -> dict[str, Any]:
    wallets = _count(cur, "pulse_ad_wallets") + _count(cur, "creator_wallets")
    transactions = _count(cur, "pulse_ad_wallet_transactions") + _count(cur, "payment_audit_logs")
    orders = _count(cur, "marketplace_orders")
    sellers = _count(cur, "seller_profiles")
    products = _count(cur, "marketplace_listings")
    subscriptions = _count(cur, "subscriptions") + _count(cur, "premium_subscriptions")
    payouts = _count(cur, "seller_payouts")
    refunds = _count(cur, "pulse_ad_refunds") + _count(cur, "marketplace_refunds")
    chargebacks = _count(cur, "pulse_ad_chargebacks")
    payment_failures = _count(cur, "payment_audit_logs", "lower(coalesce(status,'')) IN ('failed','error','declined')")
    disputes = _count(cur, "marketplace_disputes")
    fraud_events = _count(cur, "payment_audit_logs", "lower(coalesce(event_type,'')) LIKE '%fraud%'")
    wallet_cents = _sum_cents(cur, "pulse_ad_wallets", "available_balance_cents") + _sum_cents(cur, "creator_wallets", "available_balance_cents")
    revenue_cents = (
        _sum_cents(cur, "creator_revenue_events", "amount_cents")
        + _sum_cents(cur, "marketplace_orders", "seller_amount_cents")
        + _sum_cents(cur, "ad_revenue_events", "amount_cents")
        + _sum_cents(cur, "affiliate_commissions", "amount_cents")
    )
    payment_health = max(35, 100 - min(65, payment_failures * 4 + disputes * 5 + fraud_events * 8))
    fraud_risk = min(100, fraud_events * 10 + disputes * 5 + chargebacks * 9)
    return {
        "wallets": wallets,
        "transactions": transactions,
        "orders": orders,
        "sellers": sellers,
        "products": products,
        "subscriptions": subscriptions,
        "premium": subscriptions,
        "payouts": payouts,
        "revenue": revenue_cents,
        "affiliate": _count(cur, "affiliate_commissions"),
        "marketplace": orders + products,
        "taxes": _count(cur, "seller_tax_profiles"),
        "fraud": fraud_events,
        "refunds": refunds,
        "chargebacks": chargebacks,
        "payment_providers": _count(cur, "payment_audit_logs"),
        "stripe": _count(cur, "stripe_webhook_events") + _count(cur, "checkout_attempts"),
        "apple_iap": _count(cur, "apple_iap_events"),
        "google_play_billing": _count(cur, "google_play_billing_events"),
        "audit": transactions,
        "wallet_balance_cents": wallet_cents,
        "platform_revenue_cents": revenue_cents,
        "payment_failures": payment_failures,
        "disputes": disputes,
        "payment_health": payment_health,
        "fraud_risk": fraud_risk,
        "payout_queue": payouts,
        "refund_queue": refunds,
        "chargeback_queue": chargebacks,
    }


def build_admin_economy_state(conn: Any) -> dict[str, Any]:
    cur = conn.cursor()
    metrics = _admin_metric_map(cur)
    sections = []
    for section in ECONOMY_SECTIONS:
        count_key = str(section["key"]).replace("-", "_")
        count = _safe_int(metrics.get(count_key), 0)
        state = "WARNING" if section["key"] in {"fraud", "chargebacks", "refunds"} and count else "READY"
        if section["key"] in {"apple-iap", "google-play-billing"} and count == 0:
            state = "PARTIAL"
        sections.append({**section, "count": count, "state": state})
    return {
        "ok": True,
        "generated_at": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "metrics": metrics,
        "sections": sections,
        "privacy_boundary": "Admin surfaces show operational finance summaries. Raw card, bank, provider customer, subscription, token, and secret values are never rendered.",
    }
