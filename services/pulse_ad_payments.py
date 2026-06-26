"""PulseSoc advertiser wallet, funding, and spend ledger.

This module keeps advertiser money state server-side and idempotent. Stripe
provider identifiers stay in server tables and are never returned to clients.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from datetime import datetime, timezone

from services import pulse_ads_service


VALID_TRANSACTION_TYPES = {
    "funding",
    "spend",
    "refund",
    "credit",
    "adjustment",
    "promo_credit",
    "chargeback",
    "reserve",
    "release_reserve",
}
VALID_CURRENCIES = {"usd"}
MIN_FUNDING_CENTS = 500
MAX_FUNDING_CENTS = 500_000


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def clean_text(value, max_len: int = 240) -> str:
    return pulse_ads_service.clean_text(value, max_len)


def clean_json(value, max_len: int = 6000) -> str:
    return pulse_ads_service.clean_json(value, max_len)


def safe_int(value, default=0, minimum=None, maximum=None) -> int:
    return pulse_ads_service.safe_int(value, default, minimum, maximum)


def money(cents) -> str:
    amount = safe_int(cents, 0)
    return f"${amount / 100:,.2f}"


def row_to_dict(row) -> dict:
    return pulse_ads_service.row_to_dict(row)


def hash_value(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()[:64]


def billing_enabled() -> bool:
    return os.getenv("PULSE_ADS_BILLING_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}


def stripe_ready() -> bool:
    return bool(os.getenv("STRIPE_SECRET_KEY") and os.getenv("APP_BASE_URL"))


def _currency(value: str) -> str:
    currency = clean_text(value or "usd", 8).lower()
    if currency not in VALID_CURRENCIES:
        raise pulse_ads_service.PulseAdsError("Unsupported ad wallet currency.")
    return currency


def _account(conn, account_id) -> dict:
    cur = conn.cursor()
    cur.execute("SELECT * FROM pulse_ad_accounts WHERE id=?", (safe_int(account_id, minimum=1),))
    account = row_to_dict(cur.fetchone())
    if not account:
        raise pulse_ads_service.PulseAdsError("Ad account not found.", 404)
    return account


def _owner_account(conn, user_id, account_id) -> dict:
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM pulse_ad_accounts WHERE id=? AND owner_user_id=?",
        (safe_int(account_id, minimum=1), safe_int(user_id, minimum=1)),
    )
    account = row_to_dict(cur.fetchone())
    if not account:
        raise pulse_ads_service.PulseAdsError("Ad account not found.", 404)
    return account


def _audit(conn, actor_user_id, action, entity_type, entity_id, before=None, after=None) -> None:
    pulse_ads_service.audit_log(
        conn,
        actor_user_id,
        clean_text(action, 80),
        clean_text(entity_type, 80),
        entity_id,
        before=before or {},
        after=after or {},
    )


def ensure_wallet(conn, account_id, currency="usd") -> dict:
    account_id = safe_int(account_id, minimum=1)
    currency = _currency(currency)
    cur = conn.cursor()
    cur.execute("SELECT * FROM pulse_ad_wallets WHERE account_id=? AND currency=?", (account_id, currency))
    wallet = row_to_dict(cur.fetchone())
    if wallet:
        return wallet
    now = now_iso()
    cur.execute(
        """
        INSERT INTO pulse_ad_wallets
        (account_id, currency, available_balance_cents, pending_balance_cents, promotional_credits_cents,
         bonus_credits_cents, refund_credits_cents, lifetime_funded_cents, lifetime_spent_cents,
         reserved_budget_cents, created_at, updated_at)
        VALUES (?, ?, 0, 0, 0, 0, 0, 0, 0, 0, ?, ?)
        """,
        (account_id, currency, now, now),
    )
    cur.execute("SELECT * FROM pulse_ad_wallets WHERE id=?", (cur.lastrowid,))
    return row_to_dict(cur.fetchone())


def wallet_summary(conn, user_id, account_id) -> dict:
    _owner_account(conn, user_id, account_id)
    wallet = ensure_wallet(conn, account_id)
    spendable = spendable_balance_cents(conn, account_id)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT transaction_type, amount_cents, currency, status, description, created_at
        FROM pulse_ad_wallet_transactions
        WHERE account_id=?
        ORDER BY id DESC LIMIT 40
        """,
        (account_id,),
    )
    transactions = [row_to_dict(row) for row in cur.fetchall()]
    cur.execute(
        """
        SELECT invoice_number, receipt_number, amount_cents, currency, status, created_at
        FROM pulse_ad_receipts
        WHERE account_id=?
        ORDER BY id DESC LIMIT 30
        """,
        (account_id,),
    )
    receipts = [row_to_dict(row) for row in cur.fetchall()]
    for item in transactions:
        item["amount"] = money(item.get("amount_cents"))
    for item in receipts:
        item["amount"] = money(item.get("amount_cents"))
    return {
        "account_id": safe_int(account_id),
        "currency": wallet.get("currency") or "usd",
        "available_balance_cents": safe_int(wallet.get("available_balance_cents")),
        "pending_balance_cents": safe_int(wallet.get("pending_balance_cents")),
        "promotional_credits_cents": safe_int(wallet.get("promotional_credits_cents")),
        "bonus_credits_cents": safe_int(wallet.get("bonus_credits_cents")),
        "refund_credits_cents": safe_int(wallet.get("refund_credits_cents")),
        "reserved_budget_cents": safe_int(wallet.get("reserved_budget_cents")),
        "lifetime_funded_cents": safe_int(wallet.get("lifetime_funded_cents")),
        "lifetime_spent_cents": safe_int(wallet.get("lifetime_spent_cents")),
        "spendable_balance_cents": spendable,
        "available_balance": money(wallet.get("available_balance_cents")),
        "reserved_budget": money(wallet.get("reserved_budget_cents")),
        "spendable_balance": money(spendable),
        "billing_enabled": billing_enabled(),
        "stripe_ready": stripe_ready(),
        "stripe_ids_visible": False,
        "transactions": transactions,
        "receipts": receipts,
    }


def spendable_balance_cents(conn, account_id) -> int:
    account = _account(conn, account_id)
    if clean_text(account.get("business_type"), 80) == "internal_promotion":
        return 100_000_000
    wallet = ensure_wallet(conn, account_id)
    spendable = (
        safe_int(wallet.get("available_balance_cents"))
        + safe_int(wallet.get("promotional_credits_cents"))
        + safe_int(wallet.get("bonus_credits_cents"))
        + safe_int(wallet.get("refund_credits_cents"))
        - safe_int(wallet.get("reserved_budget_cents"))
    )
    return max(0, spendable)


def campaign_can_spend(conn, campaign: dict) -> bool:
    account_id = safe_int(campaign.get("ad_account_id"), minimum=1)
    if not account_id:
        return False
    if spendable_balance_cents(conn, account_id) <= 0:
        return False
    return True


def _insert_transaction(
    conn,
    account_id,
    transaction_type,
    amount_cents,
    *,
    currency="usd",
    status="posted",
    idempotency_key="",
    campaign_id=None,
    creative_id=None,
    description="",
    metadata=None,
) -> dict:
    transaction_type = clean_text(transaction_type, 40)
    if transaction_type not in VALID_TRANSACTION_TYPES:
        raise pulse_ads_service.PulseAdsError("Unsupported ad wallet transaction type.")
    amount_cents = safe_int(amount_cents, 0)
    if amount_cents < 0:
        raise pulse_ads_service.PulseAdsError("Wallet transaction amount cannot be negative.")
    currency = _currency(currency)
    idempotency_key = clean_text(idempotency_key or secrets.token_urlsafe(24), 160)
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM pulse_ad_wallet_transactions WHERE idempotency_key=?",
        (idempotency_key,),
    )
    existing = row_to_dict(cur.fetchone())
    if existing:
        return {**existing, "deduped": True}
    now = now_iso()
    cur.execute(
        """
        INSERT INTO pulse_ad_wallet_transactions
        (account_id, campaign_id, creative_id, transaction_type, amount_cents, currency, status,
         idempotency_key, description, metadata_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            account_id,
            campaign_id,
            creative_id,
            transaction_type,
            amount_cents,
            currency,
            clean_text(status, 40),
            idempotency_key,
            clean_text(description, 300),
            clean_json(metadata or {}),
            now,
        ),
    )
    cur.execute("SELECT * FROM pulse_ad_wallet_transactions WHERE id=?", (cur.lastrowid,))
    return row_to_dict(cur.fetchone())


def create_funding_session(conn, user_id, account_id, payload: dict) -> dict:
    _owner_account(conn, user_id, account_id)
    if not billing_enabled():
        raise pulse_ads_service.PulseAdsError("Advertiser wallet funding is not enabled yet.", 503)
    amount_cents = safe_int(payload.get("amount_cents"), 0, MIN_FUNDING_CENTS, MAX_FUNDING_CENTS)
    currency = _currency(payload.get("currency") or "usd")
    idempotency_key = clean_text(payload.get("idempotency_key") or secrets.token_urlsafe(24), 160)
    cur = conn.cursor()
    cur.execute("SELECT * FROM pulse_ad_wallet_funding_sessions WHERE idempotency_key=?", (idempotency_key,))
    existing = row_to_dict(cur.fetchone())
    if existing:
        return {**_safe_funding_session(existing), "deduped": True}
    now = now_iso()
    cur.execute(
        """
        INSERT INTO pulse_ad_wallet_funding_sessions
        (account_id, user_id, amount_cents, currency, provider, provider_session_id, status, idempotency_key,
         checkout_url, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'stripe', '', 'created', ?, '', ?, ?)
        """,
        (account_id, user_id, amount_cents, currency, idempotency_key, now, now),
    )
    session_id = cur.lastrowid
    _audit(conn, user_id, "ad_wallet_funding_session_created", "pulse_ad_wallet_funding_sessions", session_id, after={"amount_cents": amount_cents, "currency": currency})
    conn.commit()
    cur.execute("SELECT * FROM pulse_ad_wallet_funding_sessions WHERE id=?", (session_id,))
    return _safe_funding_session(row_to_dict(cur.fetchone()))


def attach_checkout_session(conn, funding_session_id, provider_session_id, checkout_url) -> dict:
    now = now_iso()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE pulse_ad_wallet_funding_sessions
        SET provider_session_id=?, checkout_url=?, status='checkout_created', updated_at=?
        WHERE id=?
        """,
        (clean_text(provider_session_id, 200), clean_text(checkout_url, 700), now, funding_session_id),
    )
    conn.commit()
    cur.execute("SELECT * FROM pulse_ad_wallet_funding_sessions WHERE id=?", (funding_session_id,))
    return _safe_funding_session(row_to_dict(cur.fetchone()))


def _safe_funding_session(row: dict) -> dict:
    return {
        "id": safe_int(row.get("id")),
        "account_id": safe_int(row.get("account_id")),
        "amount_cents": safe_int(row.get("amount_cents")),
        "amount": money(row.get("amount_cents")),
        "currency": clean_text(row.get("currency") or "usd", 8),
        "provider": clean_text(row.get("provider") or "stripe", 40),
        "status": clean_text(row.get("status") or "", 40),
        "checkout_url": clean_text(row.get("checkout_url") or "", 700),
        "created_at": clean_text(row.get("created_at") or "", 40),
        "updated_at": clean_text(row.get("updated_at") or "", 40),
    }


def credit_wallet_from_stripe_session(conn, event_id: str, session: dict) -> dict:
    metadata = session.get("metadata") or {}
    if metadata.get("purpose") != "pulse_ad_wallet_funding":
        return {"ok": False, "ignored": True}
    funding_session_id = safe_int(metadata.get("funding_session_id"), 0)
    account_id = safe_int(metadata.get("ad_account_id"), 0)
    amount_cents = safe_int(session.get("amount_total") or metadata.get("amount_cents"), 0, minimum=0)
    currency = _currency(session.get("currency") or metadata.get("currency") or "usd")
    if not funding_session_id or not account_id:
        raise pulse_ads_service.PulseAdsError("Invalid ad wallet funding metadata.")
    cur = conn.cursor()
    cur.execute("SELECT * FROM pulse_ad_wallet_funding_sessions WHERE id=? AND account_id=?", (funding_session_id, account_id))
    funding = row_to_dict(cur.fetchone())
    if not funding:
        raise pulse_ads_service.PulseAdsError("Ad wallet funding session not found.", 404)
    tx_key = f"stripe:{clean_text(event_id, 120)}:{funding_session_id}"
    existing = _insert_transaction(
        conn,
        account_id,
        "funding",
        amount_cents,
        currency=currency,
        idempotency_key=tx_key,
        description="Stripe wallet funding",
        metadata={"provider_session_hash": hash_value(session.get("id") or ""), "event_hash": hash_value(event_id)},
    )
    if existing.get("deduped"):
        return {"ok": True, "deduped": True, "account_id": account_id}
    wallet = ensure_wallet(conn, account_id, currency)
    now = now_iso()
    cur.execute(
        """
        UPDATE pulse_ad_wallets
        SET available_balance_cents=?, lifetime_funded_cents=?, updated_at=?
        WHERE id=?
        """,
        (
            safe_int(wallet.get("available_balance_cents")) + amount_cents,
            safe_int(wallet.get("lifetime_funded_cents")) + amount_cents,
            now,
            wallet.get("id"),
        ),
    )
    cur.execute(
        """
        UPDATE pulse_ad_wallet_funding_sessions
        SET status='credited', provider_session_id=COALESCE(NULLIF(?, ''), provider_session_id), updated_at=?
        WHERE id=?
        """,
        (clean_text(session.get("id") or "", 200), now, funding_session_id),
    )
    receipt_number = f"AD-RCPT-{funding_session_id}-{now[:10].replace('-', '')}"
    invoice_number = f"AD-INV-{funding_session_id}-{now[:10].replace('-', '')}"
    cur.execute(
        """
        INSERT INTO pulse_ad_receipts
        (account_id, funding_session_id, invoice_number, receipt_number, amount_cents, currency, status, provider, provider_reference_hash, created_at)
        VALUES (?, ?, ?, ?, ?, ?, 'paid', 'stripe', ?, ?)
        """,
        (account_id, funding_session_id, invoice_number, receipt_number, amount_cents, currency, hash_value(session.get("id") or ""), now),
    )
    _audit(conn, safe_int(funding.get("user_id")), "ad_wallet_funded", "pulse_ad_wallets", account_id, after={"amount_cents": amount_cents, "currency": currency})
    conn.commit()
    return {"ok": True, "account_id": account_id, "amount_cents": amount_cents}


def reserve_campaign_budget(conn, user_id, campaign_id) -> dict:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT c.*, a.owner_user_id FROM pulse_ad_campaigns c
        JOIN pulse_ad_accounts a ON a.id=c.ad_account_id
        WHERE c.id=?
        """,
        (safe_int(campaign_id, minimum=1),),
    )
    campaign = row_to_dict(cur.fetchone())
    if not campaign or safe_int(campaign.get("owner_user_id")) != safe_int(user_id):
        raise pulse_ads_service.PulseAdsError("Campaign not found.", 404)
    budget = safe_int(campaign.get("lifetime_budget_cents") or campaign.get("daily_budget_cents"), 0)
    if budget <= 0:
        raise pulse_ads_service.PulseAdsError("Campaign budget must be greater than zero.")
    spendable = spendable_balance_cents(conn, campaign.get("ad_account_id"))
    if spendable < min(budget, 50_000):
        raise pulse_ads_service.PulseAdsError("Wallet balance is too low for this campaign.", 409)
    reserve_cents = min(budget, 50_000)
    wallet = ensure_wallet(conn, campaign.get("ad_account_id"))
    key = f"reserve:campaign:{campaign_id}:{reserve_cents}"
    tx = _insert_transaction(
        conn,
        campaign.get("ad_account_id"),
        "reserve",
        reserve_cents,
        idempotency_key=key,
        campaign_id=campaign_id,
        description="Campaign budget reserve",
    )
    if not tx.get("deduped"):
        cur.execute(
            "UPDATE pulse_ad_wallets SET reserved_budget_cents=?, updated_at=? WHERE id=?",
            (safe_int(wallet.get("reserved_budget_cents")) + reserve_cents, now_iso(), wallet.get("id")),
        )
        conn.commit()
    return {"ok": True, "reserved_cents": reserve_cents, "reserved": money(reserve_cents)}


def record_spend_event(conn, campaign_id, creative_id, placement_key, amount_cents=1, idempotency_key="") -> dict:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT c.*, a.business_type FROM pulse_ad_campaigns c
        JOIN pulse_ad_accounts a ON a.id=c.ad_account_id
        WHERE c.id=?
        """,
        (safe_int(campaign_id, minimum=1),),
    )
    campaign = row_to_dict(cur.fetchone())
    if not campaign:
        return {"ok": False, "skipped": "campaign_missing"}
    if clean_text(campaign.get("business_type"), 80) == "internal_promotion":
        return {"ok": True, "skipped": "internal_promotion"}
    amount_cents = safe_int(amount_cents, 1, 1, 10_000)
    if spendable_balance_cents(conn, campaign.get("ad_account_id")) < amount_cents:
        cur.execute("UPDATE pulse_ad_campaigns SET status='paused', updated_at=? WHERE id=?", (now_iso(), campaign_id))
        _audit(conn, None, "ad_campaign_auto_paused_insufficient_wallet", "pulse_ad_campaigns", campaign_id, after={"placement_key": placement_key})
        conn.commit()
        return {"ok": False, "paused": True, "reason": "wallet_insufficient"}
    key = clean_text(idempotency_key or f"spend:{campaign_id}:{creative_id}:{placement_key}:{now_iso()}", 180)
    tx = _insert_transaction(
        conn,
        campaign.get("ad_account_id"),
        "spend",
        amount_cents,
        idempotency_key=key,
        campaign_id=campaign_id,
        creative_id=creative_id,
        description=f"Ad delivery spend for {clean_text(placement_key, 80)}",
    )
    if tx.get("deduped"):
        return {"ok": True, "deduped": True}
    wallet = ensure_wallet(conn, campaign.get("ad_account_id"))
    now = now_iso()
    cur.execute(
        """
        UPDATE pulse_ad_wallets
        SET available_balance_cents=?, lifetime_spent_cents=?, reserved_budget_cents=?, updated_at=?
        WHERE id=?
        """,
        (
            max(0, safe_int(wallet.get("available_balance_cents")) - amount_cents),
            safe_int(wallet.get("lifetime_spent_cents")) + amount_cents,
            max(0, safe_int(wallet.get("reserved_budget_cents")) - amount_cents),
            now,
            wallet.get("id"),
        ),
    )
    cur.execute("UPDATE pulse_ad_campaigns SET spent_cents=COALESCE(spent_cents,0)+?, updated_at=? WHERE id=?", (amount_cents, now, campaign_id))
    conn.commit()
    return {"ok": True, "amount_cents": amount_cents}


def admin_finance_summary(conn) -> dict:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT COALESCE(SUM(available_balance_cents + promotional_credits_cents + bonus_credits_cents + refund_credits_cents), 0) AS total_wallet_cents,
               COALESCE(SUM(lifetime_funded_cents), 0) AS lifetime_funded_cents,
               COALESCE(SUM(lifetime_spent_cents), 0) AS lifetime_spent_cents,
               COALESCE(SUM(reserved_budget_cents), 0) AS reserved_cents
        FROM pulse_ad_wallets
        """
    )
    totals = row_to_dict(cur.fetchone())
    today = now_iso()[:10]
    cur.execute(
        """
        SELECT transaction_type, COUNT(*) AS total, COALESCE(SUM(amount_cents),0) AS amount_cents
        FROM pulse_ad_wallet_transactions
        WHERE created_at>=?
        GROUP BY transaction_type
        """,
        (today,),
    )
    today_rows = [row_to_dict(row) for row in cur.fetchall()]
    cur.execute(
        """
        SELECT a.id, a.business_name, w.available_balance_cents, w.lifetime_funded_cents, w.lifetime_spent_cents, w.reserved_budget_cents
        FROM pulse_ad_wallets w
        JOIN pulse_ad_accounts a ON a.id=w.account_id
        ORDER BY w.updated_at DESC LIMIT 50
        """
    )
    accounts = [row_to_dict(row) for row in cur.fetchall()]
    return {
        "total_wallet_cents": safe_int(totals.get("total_wallet_cents")),
        "lifetime_funded_cents": safe_int(totals.get("lifetime_funded_cents")),
        "lifetime_spent_cents": safe_int(totals.get("lifetime_spent_cents")),
        "reserved_cents": safe_int(totals.get("reserved_cents")),
        "total_wallet": money(totals.get("total_wallet_cents")),
        "lifetime_funded": money(totals.get("lifetime_funded_cents")),
        "lifetime_spent": money(totals.get("lifetime_spent_cents")),
        "reserved": money(totals.get("reserved_cents")),
        "today": today_rows,
        "accounts": accounts,
        "billing_enabled": billing_enabled(),
        "stripe_ready": stripe_ready(),
    }
