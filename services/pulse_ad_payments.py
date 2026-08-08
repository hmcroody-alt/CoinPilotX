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
    """Format cents as a display string, negatives included.

    A reversed top-up can push a wallet negative, and that negative is the whole
    point — it is what the advertiser owes. Rendering it as `$-5.00` reads like a
    typo and rendering it as `$0.00` would be a lie, so a negative is formatted
    the way a statement formats one: `-$5.00`.
    """
    amount = safe_int(cents, 0)
    sign = "-" if amount < 0 else ""
    return f"{sign}${abs(amount) / 100:,.2f}"


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


def _safe_execute(conn, sql, params=()) -> bool:
    try:
        conn.execute(sql, params)
        return True
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return False


def _add_column_if_missing(conn, table: str, column: str, ddl_type: str) -> None:
    try:
        cur = conn.cursor()
        cur.execute(f"PRAGMA table_info({table})")
        existing = {str(row[1] if not hasattr(row, "keys") else row["name"]) for row in cur.fetchall()}
        if column in existing:
            return
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        # PRAGMA is SQLite-only; on other engines fall through and let the
        # ALTER speak for itself.
    _safe_execute(conn, f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}")


def ensure_schema(conn) -> None:
    """Idempotent DDL for the wallet-completion features.

    Everything here is defensive: production creates most of this in
    ``bot.init_db()``, tests build a minimal schema inline, and either may be
    missing a piece. Every statement tolerates the object already existing.
    """
    _safe_execute(
        conn,
        """
        CREATE TABLE IF NOT EXISTS pulse_ad_invoices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL,
            invoice_number TEXT UNIQUE,
            amount_cents INTEGER NOT NULL DEFAULT 0,
            currency TEXT DEFAULT 'usd',
            status TEXT DEFAULT 'open',
            period_start TEXT,
            period_end TEXT,
            metadata_json TEXT,
            created_at TEXT
        )
        """,
    )
    _safe_execute(
        conn,
        """
        CREATE TABLE IF NOT EXISTS pulse_ad_notifications (
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
        )
        """,
    )
    # Links a funding-completion invoice back to the checkout that paid it, and
    # is what makes the invoice write idempotent across webhook retries.
    _add_column_if_missing(conn, "pulse_ad_invoices", "funding_session_id", "INTEGER")
    # Advertiser-set guardrails and top-up reminder settings. Production also
    # adds these in bot.init_db(); tests may not.
    _add_column_if_missing(conn, "pulse_ad_wallets", "daily_limit_cents", "INTEGER DEFAULT 0")
    _add_column_if_missing(conn, "pulse_ad_wallets", "lifetime_limit_cents", "INTEGER DEFAULT 0")
    _add_column_if_missing(conn, "pulse_ad_wallets", "auto_topup_enabled", "INTEGER DEFAULT 0")
    _add_column_if_missing(conn, "pulse_ad_wallets", "auto_topup_threshold_cents", "INTEGER DEFAULT 0")
    _add_column_if_missing(conn, "pulse_ad_wallets", "auto_topup_amount_cents", "INTEGER DEFAULT 0")
    _safe_execute(
        conn,
        "CREATE INDEX IF NOT EXISTS idx_pulse_ad_invoices_account ON pulse_ad_invoices(account_id, id)",
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
    available_cents = safe_int(wallet.get("available_balance_cents"))
    # A refunded or disputed top-up debits the wallet even when the money is
    # already spent, so the balance can legitimately be negative. That shortfall
    # is a debt the advertiser owes, and it is surfaced by name rather than left
    # for a reader to infer from a minus sign they may not be looking for.
    amount_owed_cents = max(0, -available_cents)
    auto_topup_enabled = bool(safe_int(wallet.get("auto_topup_enabled"), 0))
    auto_topup_threshold = safe_int(wallet.get("auto_topup_threshold_cents"), 0)
    needs_topup = bool(auto_topup_enabled and auto_topup_threshold > 0 and spendable < auto_topup_threshold)
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
        "amount_owed_cents": amount_owed_cents,
        "available_balance": money(available_cents),
        "reserved_budget": money(wallet.get("reserved_budget_cents")),
        "spendable_balance": money(spendable),
        "amount_owed": money(amount_owed_cents),
        "billing_enabled": billing_enabled(),
        "stripe_ready": stripe_ready(),
        "stripe_ids_visible": False,
        "spending_limits": {
            "daily_limit_cents": safe_int(wallet.get("daily_limit_cents"), 0),
            "lifetime_limit_cents": safe_int(wallet.get("lifetime_limit_cents"), 0),
        },
        "auto_topup": {
            "enabled": auto_topup_enabled,
            "threshold_cents": auto_topup_threshold,
            "amount_cents": safe_int(wallet.get("auto_topup_amount_cents"), 0),
            # Honest: funding runs through Stripe Checkout with no saved payment
            # method, so nothing is ever charged automatically. When the balance
            # dips below the threshold the advertiser is notified to top up.
            "auto_charge": False,
            "note": "No card is charged automatically; a low-balance notification asks you to top up.",
        },
        "needs_topup": needs_topup,
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


def _stripe_ref(value) -> str:
    """A Stripe object reference as a plain id.

    Stripe returns these either as a bare id string or as an expanded object,
    depending on which fields the caller expanded and which API version signed the
    webhook. Reading `session["payment_intent"]` and assuming a string is how a
    reversal ends up filed against the id ``"{'id': 'pi_...'}"`` and never matches
    anything again.
    """
    if isinstance(value, dict):
        value = value.get("id")
    return clean_text(value or "", 200)


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
    # The payment intent is recorded here and nowhere else. A later
    # `charge.refunded` or `charge.dispute.created` names a charge and a payment
    # intent; it does not name this Checkout Session, and Stripe does not copy the
    # Session's metadata onto the PaymentIntent — so `purpose:
    # pulse_ad_wallet_funding`, which is how the credit found its way here, is not
    # present on the reversal at all. If this id is not captured at credit time
    # there is no path from the reversal back to the wallet it should debit, and
    # the refund lands on the advertiser's card while their PulseSoc balance stays
    # whole. See `reverse_wallet_funding`.
    cur.execute(
        """
        UPDATE pulse_ad_wallet_funding_sessions
        SET status='credited',
            provider_session_id=COALESCE(NULLIF(?, ''), provider_session_id),
            provider_payment_intent_id=COALESCE(NULLIF(?, ''), provider_payment_intent_id),
            updated_at=?
        WHERE id=?
        """,
        (
            clean_text(session.get("id") or "", 200),
            clean_text(_stripe_ref(session.get("payment_intent")), 200),
            now,
            funding_session_id,
        ),
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
    try:
        _write_funding_invoice(
            conn,
            account_id=account_id,
            funding_session_id=funding_session_id,
            amount_cents=amount_cents,
            currency=currency,
            receipt_number=receipt_number,
            now=now,
        )
    except Exception:
        # An invoice is a record of money that has already moved; failing to
        # file it must never fail the funding credit itself. Deliberately no
        # rollback here — that would undo the wallet credit sitting in the same
        # transaction. `list_invoices` backfills any credited funding session
        # that is missing an invoice.
        pass
    _audit(conn, safe_int(funding.get("user_id")), "ad_wallet_funded", "pulse_ad_wallets", account_id, after={"amount_cents": amount_cents, "currency": currency})
    conn.commit()
    return {"ok": True, "account_id": account_id, "amount_cents": amount_cents}


def _funding_session_for_reversal(conn, obj: dict) -> dict:
    """Find the wallet top-up that a refund or dispute is reversing.

    Matched on payment intent first, then charge, then Checkout Session id. Only
    sessions this module has actually credited are eligible — reversing a session
    that never added balance would invent a debt.
    """
    cur = conn.cursor()
    payment_intent = _stripe_ref(obj.get("payment_intent"))
    charge_id = _stripe_ref(obj.get("charge") or (obj.get("id") if clean_text(obj.get("object"), 40) == "charge" else ""))
    candidates = [
        ("provider_payment_intent_id", payment_intent),
        ("provider_charge_id", charge_id),
        ("provider_session_id", _stripe_ref(obj.get("checkout_session") or "")),
    ]
    for column, value in candidates:
        if not value:
            continue
        cur.execute(
            f"SELECT * FROM pulse_ad_wallet_funding_sessions WHERE {column}=? AND status IN ('credited','reversed','partially_reversed') ORDER BY id DESC LIMIT 1",
            (value,),
        )
        row = row_to_dict(cur.fetchone())
        if row:
            return row
    return {}


def reverse_wallet_funding(conn, event_id: str, obj: dict, event_type: str) -> dict:
    """Debit the advertiser wallet when a top-up is refunded or charged back.

    Before this existed, `credit_wallet_from_stripe_session` was the only thing in
    the advertising stack that moved wallet money in from Stripe, and nothing
    moved it back out. An advertiser could fund $500, let the checkout settle,
    refund or dispute the card charge, and keep spending the full $500 of PulseSoc
    inventory — the balance was never touched, no transaction was written, and
    `pulse_ad_refunds`, the table the staff finance panel counts, had no writer
    anywhere in the repository, so the staff view of it read a confident zero.

    Three things this has to get right, each of which has burned this codebase or
    a neighbour of it before:

    **Cumulative amounts.** A charge's `amount_refunded` is the running total
    across every refund on that charge, not the newest one. Debiting it directly
    means a second $10 partial refund debits $20. We store `reversed_cents` on the
    funding session and debit only the difference.

    **Reversals can exceed what is left.** The advertiser may already have spent
    the money. The balance is allowed to go negative, because a wallet clamped to
    zero is a §31 fake zero: it reads as "nothing here" when the truth is "you owe
    us this". `spendable_balance_cents` floors the *spendable* figure at zero, so
    nothing can be spent against a debt, and `wallet_summary` reports the shortfall
    explicitly as `amount_owed`.

    **Idempotency.** Stripe retries. The wallet transaction is keyed on the event
    id, so a redelivered event dedupes and the balance is not debited twice.

    A reversal that cannot be matched to a credited funding session is reported,
    not swallowed — the caller logs it. Silently ignoring an unmatched reversal is
    how money goes missing without anyone finding out.
    """
    if not isinstance(obj, dict):
        return {"ok": False, "ignored": True, "reason": "malformed_object"}
    funding = _funding_session_for_reversal(conn, obj)
    if not funding:
        return {"ok": False, "ignored": True, "reason": "not_ad_wallet_funding"}

    account_id = safe_int(funding.get("account_id"), minimum=1)
    funded_cents = safe_int(funding.get("amount_cents"), 0)
    already_reversed = safe_int(funding.get("reversed_cents"), 0)
    is_dispute = clean_text(event_type, 60).startswith("charge.dispute")
    if is_dispute:
        # A dispute freezes the whole disputed amount, not a running total. Stripe
        # reports it as the dispute's own `amount`.
        target_total = safe_int(obj.get("amount"), 0) or funded_cents
    else:
        # Cumulative. See the docstring.
        target_total = safe_int(obj.get("amount_refunded"), 0)
    # Never reverse more than was credited, whatever Stripe reports — a wallet
    # top-up cannot be undone by more than it added.
    target_total = max(0, min(target_total, funded_cents))
    delta = target_total - already_reversed
    if delta <= 0:
        return {"ok": True, "noop": True, "account_id": account_id, "already_reversed_cents": already_reversed}

    currency = _currency(funding.get("currency") or "usd")
    transaction_type = "chargeback" if is_dispute else "refund"
    tx_key = f"stripe:{clean_text(event_type, 60)}:{clean_text(event_id, 120)}:{safe_int(funding.get('id'))}"
    tx = _insert_transaction(
        conn,
        account_id,
        transaction_type,
        delta,
        currency=currency,
        idempotency_key=tx_key,
        description="Wallet top-up disputed" if is_dispute else "Wallet top-up refunded",
        metadata={"event_hash": hash_value(event_id), "funding_session_id": safe_int(funding.get("id"))},
    )
    if tx.get("deduped"):
        return {"ok": True, "deduped": True, "account_id": account_id}

    wallet = ensure_wallet(conn, account_id, currency)
    now = now_iso()
    # Deliberately not clamped at zero. See the docstring — the negative IS the
    # information, and `spendable_balance_cents` is what stops it being spent.
    new_available = safe_int(wallet.get("available_balance_cents")) - delta
    new_lifetime_funded = max(0, safe_int(wallet.get("lifetime_funded_cents")) - delta)
    cur = conn.cursor()
    cur.execute(
        "UPDATE pulse_ad_wallets SET available_balance_cents=?, lifetime_funded_cents=?, updated_at=? WHERE id=?",
        (new_available, new_lifetime_funded, now, wallet.get("id")),
    )
    total_reversed = already_reversed + delta
    cur.execute(
        "UPDATE pulse_ad_wallet_funding_sessions SET reversed_cents=?, status=?, updated_at=? WHERE id=?",
        (
            total_reversed,
            "reversed" if total_reversed >= funded_cents else "partially_reversed",
            now,
            funding.get("id"),
        ),
    )
    cur.execute(
        """
        INSERT INTO pulse_ad_refunds
        (account_id, funding_session_id, amount_cents, currency, status, reason, provider_reference_hash, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            account_id,
            funding.get("id"),
            delta,
            currency,
            "disputed" if is_dispute else "refunded",
            clean_text(obj.get("reason") or event_type, 400),
            hash_value(_stripe_ref(obj.get("id")) or event_id),
            now,
            now,
        ),
    )
    paused = _pause_campaigns_without_balance(conn, account_id)
    _audit(
        conn,
        safe_int(funding.get("user_id")),
        "ad_wallet_funding_reversed",
        "pulse_ad_wallets",
        account_id,
        before={"available_balance_cents": safe_int(wallet.get("available_balance_cents"))},
        after={
            "available_balance_cents": new_available,
            "reversed_cents": delta,
            "event_type": clean_text(event_type, 60),
            "campaigns_paused": paused,
        },
    )
    conn.commit()
    return {
        "ok": True,
        "account_id": account_id,
        "reversed_cents": delta,
        "available_balance_cents": new_available,
        "campaigns_paused": paused,
    }


def _pause_campaigns_without_balance(conn, account_id) -> int:
    """Pause active campaigns on an account that can no longer fund them.

    A reversal can leave an account owing money while its campaigns are still
    `status='active'`. `record_spend_event` would pause each one the next time it
    tried to bill, but that is one impression too late and it happens campaign by
    campaign, at delivery time, in whatever order traffic arrives. Pausing here
    means the advertiser's own dashboard tells them the truth at the moment it
    becomes true, rather than showing a live campaign that will die mid-flight.

    Paused, not archived: this is recoverable. Fund the wallet and resume.
    """
    if spendable_balance_cents(conn, account_id) > 0:
        return 0
    cur = conn.cursor()
    cur.execute(
        "SELECT id FROM pulse_ad_campaigns WHERE ad_account_id=? AND status='active'",
        (safe_int(account_id, minimum=1),),
    )
    campaign_ids = [safe_int(row_to_dict(row).get("id")) for row in cur.fetchall()]
    now = now_iso()
    for campaign_id in campaign_ids:
        cur.execute("UPDATE pulse_ad_campaigns SET status='paused', updated_at=? WHERE id=?", (now, campaign_id))
        _audit(
            conn,
            None,
            "ad_campaign_paused_funding_reversed",
            "pulse_ad_campaigns",
            campaign_id,
            after={"reason": "wallet_funding_reversed"},
        )
    return len(campaign_ids)


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


# Order in which a delivered impression consumes wallet money. Grants that the
# advertiser did not pay for go first, so their own cash is the last thing spent
# and anything left over when a grant expires is money they still control.
SPEND_DRAWDOWN_ORDER = (
    "promotional_credits_cents",
    "bonus_credits_cents",
    "refund_credits_cents",
    "available_balance_cents",
)


def _allocate_spend(wallet: dict, amount_cents: int) -> dict:
    """Split one spend across the wallet buckets that will actually pay for it.

    `spendable_balance_cents` counts promotional, bonus and refund credits as
    spendable, but until this existed `record_spend_event` only ever debited
    `available_balance_cents` — and clamped that at zero. An account holding
    $0 cash and $100 of promotional credit therefore passed the affordability
    check on every impression, debited a balance that was already zero, and
    delivered unlimited free inventory forever. The credit buckets were
    write-only: something granted them, nothing ever consumed them.

    Buckets are floored at zero individually because a reversal can leave
    `available_balance_cents` negative. A negative cash balance is a debt, not
    a source of funds, and must not be drawn against.

    Returns the per-column debit plus `unfunded_cents`, the shortfall the wallet
    could not cover. The caller refuses to record a spend it cannot back rather
    than silently delivering it.
    """
    remaining = max(0, safe_int(amount_cents, 0))
    allocation = {}
    for column in SPEND_DRAWDOWN_ORDER:
        available = max(0, safe_int(wallet.get(column)))
        take = min(available, remaining)
        allocation[column] = take
        remaining -= take
    allocation["unfunded_cents"] = remaining
    return allocation


def _spent_today_cents(conn, account_id) -> int:
    """Cents actually charged to this account's wallet since UTC midnight."""
    cur = conn.cursor()
    today = now_iso()[:10]
    cur.execute(
        """
        SELECT COALESCE(SUM(amount_cents), 0) AS c
        FROM pulse_ad_wallet_transactions
        WHERE account_id=? AND transaction_type='spend' AND status='posted' AND created_at>=?
        """,
        (safe_int(account_id, minimum=1), today),
    )
    return safe_int(row_to_dict(cur.fetchone()).get("c"), 0)


def _spend_limit_reason(conn, account_id, wallet, amount_cents) -> str:
    """Advertiser-set spending limits. Zero means no limit is set."""
    daily_limit = safe_int(wallet.get("daily_limit_cents"), 0)
    if daily_limit > 0 and _spent_today_cents(conn, account_id) + amount_cents > daily_limit:
        return "daily_limit_reached"
    lifetime_limit = safe_int(wallet.get("lifetime_limit_cents"), 0)
    if lifetime_limit > 0 and safe_int(wallet.get("lifetime_spent_cents"), 0) + amount_cents > lifetime_limit:
        return "lifetime_limit_reached"
    return ""


def _pause_active_campaigns(conn, account_id, reason) -> int:
    """Pause every active campaign on the account, mirroring the insufficient-wallet path."""
    cur = conn.cursor()
    cur.execute(
        "SELECT id FROM pulse_ad_campaigns WHERE ad_account_id=? AND status='active'",
        (safe_int(account_id, minimum=1),),
    )
    campaign_ids = [safe_int(row_to_dict(row).get("id")) for row in cur.fetchall()]
    now = now_iso()
    for campaign_id in campaign_ids:
        cur.execute("UPDATE pulse_ad_campaigns SET status='paused', updated_at=? WHERE id=?", (now, campaign_id))
        _audit(
            conn,
            None,
            "ad_campaign_auto_paused_spend_limit",
            "pulse_ad_campaigns",
            campaign_id,
            after={"reason": clean_text(reason, 80)},
        )
    return len(campaign_ids)


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
    # A spend without a caller-supplied idempotency key used to fall back to a
    # timestamp-derived key with second granularity — a retried delivery event
    # more than a second later minted a fresh key and charged the wallet twice
    # for one impression. Every real caller derives the key from the delivery
    # token / event identity, so an empty key is a bug, not a convenience.
    if not clean_text(idempotency_key, 180):
        raise pulse_ads_service.PulseAdsError(
            "A spend idempotency key derived from the delivery event is required."
        )
    amount_cents = safe_int(amount_cents, 1, 1, 10_000)
    limit_wallet = ensure_wallet(conn, campaign.get("ad_account_id"))
    limit_reason = _spend_limit_reason(conn, campaign.get("ad_account_id"), limit_wallet, amount_cents)
    if limit_reason:
        _pause_active_campaigns(conn, campaign.get("ad_account_id"), limit_reason)
        conn.commit()
        return {"ok": False, "paused": True, "reason": limit_reason}
    if spendable_balance_cents(conn, campaign.get("ad_account_id")) < amount_cents:
        cur.execute("UPDATE pulse_ad_campaigns SET status='paused', updated_at=? WHERE id=?", (now_iso(), campaign_id))
        _audit(conn, None, "ad_campaign_auto_paused_insufficient_wallet", "pulse_ad_campaigns", campaign_id, after={"placement_key": placement_key})
        conn.commit()
        return {"ok": False, "paused": True, "reason": "wallet_insufficient"}
    # Work out which buckets pay for this before writing the transaction. A
    # spend row the wallet cannot back is a delivery nobody is charged for.
    wallet = ensure_wallet(conn, campaign.get("ad_account_id"))
    allocation = _allocate_spend(wallet, amount_cents)
    if allocation["unfunded_cents"]:
        cur.execute("UPDATE pulse_ad_campaigns SET status='paused', updated_at=? WHERE id=?", (now_iso(), campaign_id))
        _audit(
            conn,
            None,
            "ad_campaign_auto_paused_insufficient_wallet",
            "pulse_ad_campaigns",
            campaign_id,
            after={"placement_key": placement_key, "unfunded_cents": allocation["unfunded_cents"]},
        )
        conn.commit()
        return {"ok": False, "paused": True, "reason": "wallet_insufficient"}
    key = clean_text(idempotency_key, 180)
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
    now = now_iso()
    # Every bucket the allocation touched is debited. No max(0, ...) here: the
    # allocation already refused to draw more than each bucket holds, so a
    # clamp could only ever hide an arithmetic error instead of correcting one.
    cur.execute(
        """
        UPDATE pulse_ad_wallets
        SET promotional_credits_cents=?, bonus_credits_cents=?, refund_credits_cents=?,
            available_balance_cents=?, lifetime_spent_cents=?, reserved_budget_cents=?, updated_at=?
        WHERE id=?
        """,
        (
            safe_int(wallet.get("promotional_credits_cents")) - allocation["promotional_credits_cents"],
            safe_int(wallet.get("bonus_credits_cents")) - allocation["bonus_credits_cents"],
            safe_int(wallet.get("refund_credits_cents")) - allocation["refund_credits_cents"],
            safe_int(wallet.get("available_balance_cents")) - allocation["available_balance_cents"],
            safe_int(wallet.get("lifetime_spent_cents")) + amount_cents,
            # The reserve is a soft hold on money that has now actually moved,
            # so releasing more than was held is a no-op rather than a debt.
            max(0, safe_int(wallet.get("reserved_budget_cents")) - amount_cents),
            now,
            wallet.get("id"),
        ),
    )
    cur.execute("UPDATE pulse_ad_campaigns SET spent_cents=COALESCE(spent_cents,0)+?, updated_at=? WHERE id=?", (amount_cents, now, campaign_id))
    try:
        _maybe_notify_low_balance(conn, campaign.get("ad_account_id"))
    except Exception:
        # A reminder must never make a billable delivery fail.
        pass
    conn.commit()
    return {
        "ok": True,
        "amount_cents": amount_cents,
        "funded_from": {
            column: allocation[column] for column in SPEND_DRAWDOWN_ORDER if allocation[column]
        },
    }


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


# --------------------------------------------------------------------------
# Wallet completion: spending limits, invoices, auto top-up, paged ledgers.
# --------------------------------------------------------------------------

SPENDING_LIMIT_MAX_CENTS = 50_000_000
NOTIFY_LOW_BALANCE_TYPE = "ad_wallet_low_balance"


def _next_invoice_number(cur, account_id: int, offset: int = 0) -> str:
    """Sequential per-account invoice number: ADINV-{account}-{seq}."""
    cur.execute("SELECT COUNT(*) FROM pulse_ad_invoices WHERE account_id=?", (safe_int(account_id, minimum=1),))
    row = cur.fetchone()
    count = safe_int(row[0] if row is not None else 0, 0)
    return f"ADINV-{safe_int(account_id)}-{count + 1 + offset:05d}"


def _write_funding_invoice(conn, *, account_id, funding_session_id, amount_cents, currency, receipt_number, now) -> int:
    """File a paid invoice for a completed wallet funding. Idempotent.

    Keyed on `funding_session_id`: a webhook retry that reaches this function a
    second time finds the existing invoice and writes nothing. The invoice
    number is sequential per account; a concurrent insert that steals the
    number is retried with the next one.
    """
    account_id = safe_int(account_id, minimum=1)
    funding_session_id = safe_int(funding_session_id, minimum=1)
    cur = conn.cursor()
    cur.execute("SELECT id FROM pulse_ad_invoices WHERE funding_session_id=?", (funding_session_id,))
    existing = cur.fetchone()
    if existing:
        return safe_int(row_to_dict(existing).get("id") if hasattr(existing, "keys") else existing[0])
    metadata = {
        "source": "wallet_funding",
        "funding_session_id": funding_session_id,
        "receipt_number": clean_text(receipt_number, 120),
    }
    day = clean_text(now, 40)[:10]
    last_error = None
    for attempt in range(5):
        invoice_number = _next_invoice_number(cur, account_id, offset=attempt)
        try:
            cur.execute(
                """
                INSERT INTO pulse_ad_invoices
                (account_id, funding_session_id, invoice_number, amount_cents, currency, status,
                 period_start, period_end, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, 'paid', ?, ?, ?, ?)
                """,
                (
                    account_id,
                    funding_session_id,
                    invoice_number,
                    safe_int(amount_cents, 0),
                    _currency(currency),
                    day,
                    day,
                    clean_json(metadata),
                    clean_text(now, 40),
                ),
            )
            return safe_int(cur.lastrowid)
        except Exception as exc:  # UNIQUE collision on invoice_number: retry with next seq
            last_error = exc
            continue
    if last_error:
        raise last_error
    return 0


def _backfill_funding_invoices(conn, account_id: int) -> int:
    """Write invoices for credited funding sessions that are missing one.

    This is what makes the invoice write in `credit_wallet_from_stripe_session`
    safe to fail: any funding that was credited before invoices existed, or
    whose invoice insert failed, gets its invoice filed on the next read.
    """
    cur = conn.cursor()
    cur.execute(
        """
        SELECT f.id, f.amount_cents, f.currency, f.updated_at, f.created_at
        FROM pulse_ad_wallet_funding_sessions f
        WHERE f.account_id=? AND f.status IN ('credited','reversed','partially_reversed')
          AND NOT EXISTS (SELECT 1 FROM pulse_ad_invoices i WHERE i.funding_session_id=f.id)
        ORDER BY f.id ASC
        """,
        (safe_int(account_id, minimum=1),),
    )
    pending = [row_to_dict(row) for row in cur.fetchall()]
    written = 0
    for funding in pending:
        receipt_number = ""
        try:
            cur.execute(
                "SELECT receipt_number FROM pulse_ad_receipts WHERE funding_session_id=? ORDER BY id DESC LIMIT 1",
                (safe_int(funding.get("id")),),
            )
            receipt = row_to_dict(cur.fetchone())
            receipt_number = clean_text(receipt.get("receipt_number") or "", 120)
        except Exception:
            receipt_number = ""
        try:
            _write_funding_invoice(
                conn,
                account_id=account_id,
                funding_session_id=funding.get("id"),
                amount_cents=funding.get("amount_cents"),
                currency=funding.get("currency") or "usd",
                receipt_number=receipt_number,
                now=clean_text(funding.get("updated_at") or funding.get("created_at") or now_iso(), 40),
            )
            written += 1
        except Exception:
            continue
    if written:
        conn.commit()
    return written


def _invoice_item(row: dict) -> dict:
    return {
        "id": safe_int(row.get("id")),
        "invoice_number": clean_text(row.get("invoice_number") or "", 120),
        "amount_cents": safe_int(row.get("amount_cents")),
        "amount": money(row.get("amount_cents")),
        "currency": clean_text(row.get("currency") or "usd", 8),
        "status": clean_text(row.get("status") or "", 40),
        "period_start": clean_text(row.get("period_start") or "", 40),
        "period_end": clean_text(row.get("period_end") or "", 40),
        "funding_session_id": safe_int(row.get("funding_session_id"), 0) or None,
        "created_at": clean_text(row.get("created_at") or "", 40),
    }


def list_invoices(conn, user_id, account_id, limit=30, before_id=0) -> dict:
    """Paid invoices for the account's wallet fundings, newest first. Owner-only."""
    _owner_account(conn, user_id, account_id)
    account_id = safe_int(account_id, minimum=1)
    limit = safe_int(limit, 30, 1, 100)
    before_id = safe_int(before_id, 0, 0)
    _backfill_funding_invoices(conn, account_id)
    cur = conn.cursor()
    params = [account_id]
    clause = "account_id=?"
    if before_id:
        clause += " AND id<?"
        params.append(before_id)
    cur.execute(
        f"SELECT * FROM pulse_ad_invoices WHERE {clause} ORDER BY id DESC LIMIT ?",
        (*params, limit),
    )
    rows = [_invoice_item(row_to_dict(row)) for row in cur.fetchall()]
    next_before_id = rows[-1]["id"] if len(rows) == limit else None
    return {"invoices": rows, "next_before_id": next_before_id, "account_id": account_id}


def list_transactions(conn, user_id, account_id, limit=50, before_id=0) -> dict:
    """Paged wallet ledger, newest first. Owner-only.

    Cursor pagination on the row id: pass `next_before_id` back as `before_id`
    to get the next page. Stripe identifiers never appear here.
    """
    _owner_account(conn, user_id, account_id)
    account_id = safe_int(account_id, minimum=1)
    limit = safe_int(limit, 50, 1, 200)
    before_id = safe_int(before_id, 0, 0)
    cur = conn.cursor()
    params = [account_id]
    clause = "account_id=?"
    if before_id:
        clause += " AND id<?"
        params.append(before_id)
    cur.execute(
        f"""
        SELECT id, campaign_id, creative_id, transaction_type, amount_cents, currency, status,
               description, created_at
        FROM pulse_ad_wallet_transactions
        WHERE {clause}
        ORDER BY id DESC LIMIT ?
        """,
        (*params, limit),
    )
    rows = []
    for raw in cur.fetchall():
        item = row_to_dict(raw)
        item["amount"] = money(item.get("amount_cents"))
        rows.append(item)
    next_before_id = safe_int(rows[-1].get("id")) if len(rows) == limit else None
    return {"transactions": rows, "next_before_id": next_before_id, "account_id": account_id}


def set_spending_limits(conn, user_id, account_id, payload: dict) -> dict:
    """Owner-only daily/lifetime spend caps. Zero (or null) clears a limit.

    Enforced by `_spend_limit_reason` inside `record_spend_event`: a spend that
    would cross a cap is refused and the account's active campaigns are paused
    with an audited reason.
    """
    _owner_account(conn, user_id, account_id)
    payload = payload or {}
    wallet = ensure_wallet(conn, account_id)

    def _limit(key: str, current: int) -> int:
        if key not in payload:
            return current
        raw = payload.get(key)
        if raw is None or raw == "":
            return 0
        value = safe_int(raw, -1)
        if value < 0:
            raise pulse_ads_service.PulseAdsError("Spending limits cannot be negative.")
        if value > SPENDING_LIMIT_MAX_CENTS:
            raise pulse_ads_service.PulseAdsError("Spending limit is above the supported maximum.")
        return value

    before = {
        "daily_limit_cents": safe_int(wallet.get("daily_limit_cents"), 0),
        "lifetime_limit_cents": safe_int(wallet.get("lifetime_limit_cents"), 0),
    }
    daily = _limit("daily_limit_cents", before["daily_limit_cents"])
    lifetime = _limit("lifetime_limit_cents", before["lifetime_limit_cents"])
    if daily > 0 and lifetime > 0 and daily > lifetime:
        raise pulse_ads_service.PulseAdsError("Daily limit cannot exceed the lifetime limit.")
    cur = conn.cursor()
    cur.execute(
        "UPDATE pulse_ad_wallets SET daily_limit_cents=?, lifetime_limit_cents=?, updated_at=? WHERE id=?",
        (daily, lifetime, now_iso(), wallet.get("id")),
    )
    after = {"daily_limit_cents": daily, "lifetime_limit_cents": lifetime}
    _audit(conn, user_id, "ad_wallet_limits_updated", "pulse_ad_wallets", safe_int(account_id), before=before, after=after)
    conn.commit()
    return {"ok": True, "account_id": safe_int(account_id), **after}


def set_auto_topup(conn, user_id, account_id, payload: dict) -> dict:
    """Owner-only auto top-up settings — honestly a low-balance reminder.

    Funding runs through Stripe Checkout, which stores no payment method on
    file, so this platform CANNOT charge a card automatically and does not
    pretend to. What "enabled" actually does: when `record_spend_event` drops
    the spendable balance below `threshold_cents`, a `pulse_ad_notifications`
    row (one per account per day) asks the owner to top up `amount_cents`, and
    `wallet_summary` reports `needs_topup: true`.
    """
    _owner_account(conn, user_id, account_id)
    payload = payload or {}
    wallet = ensure_wallet(conn, account_id)
    enabled = bool(payload.get("enabled"))
    threshold = safe_int(payload.get("threshold_cents"), safe_int(wallet.get("auto_topup_threshold_cents"), 0), 0, SPENDING_LIMIT_MAX_CENTS)
    amount = safe_int(payload.get("amount_cents"), safe_int(wallet.get("auto_topup_amount_cents"), 0), 0, MAX_FUNDING_CENTS)
    if enabled:
        if threshold <= 0:
            raise pulse_ads_service.PulseAdsError("Auto top-up needs a positive balance threshold.")
        if amount < MIN_FUNDING_CENTS:
            raise pulse_ads_service.PulseAdsError("Auto top-up amount must be at least the minimum funding amount.")
    before = {
        "auto_topup_enabled": bool(safe_int(wallet.get("auto_topup_enabled"), 0)),
        "auto_topup_threshold_cents": safe_int(wallet.get("auto_topup_threshold_cents"), 0),
        "auto_topup_amount_cents": safe_int(wallet.get("auto_topup_amount_cents"), 0),
    }
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE pulse_ad_wallets
        SET auto_topup_enabled=?, auto_topup_threshold_cents=?, auto_topup_amount_cents=?, updated_at=?
        WHERE id=?
        """,
        (1 if enabled else 0, threshold, amount, now_iso(), wallet.get("id")),
    )
    after = {
        "auto_topup_enabled": enabled,
        "auto_topup_threshold_cents": threshold,
        "auto_topup_amount_cents": amount,
    }
    _audit(conn, user_id, "ad_wallet_auto_topup_updated", "pulse_ad_wallets", safe_int(account_id), before=before, after=after)
    conn.commit()
    return {
        "ok": True,
        "account_id": safe_int(account_id),
        "enabled": enabled,
        "threshold_cents": threshold,
        "amount_cents": amount,
        "auto_charge": False,
        "note": "No card is charged automatically; a low-balance notification asks you to top up.",
    }


def _maybe_notify_low_balance(conn, account_id) -> bool:
    """Write the low-balance reminder when spend crosses the top-up threshold.

    At most one notification per account per UTC day, so a busy campaign does
    not fill the owner's inbox with one reminder per impression.
    """
    account_id = safe_int(account_id, minimum=1)
    wallet = ensure_wallet(conn, account_id)
    if not safe_int(wallet.get("auto_topup_enabled"), 0):
        return False
    threshold = safe_int(wallet.get("auto_topup_threshold_cents"), 0)
    if threshold <= 0:
        return False
    spendable = spendable_balance_cents(conn, account_id)
    if spendable >= threshold:
        return False
    cur = conn.cursor()
    today = now_iso()[:10]
    cur.execute(
        """
        SELECT id FROM pulse_ad_notifications
        WHERE account_id=? AND notification_type=? AND created_at>=?
        LIMIT 1
        """,
        (account_id, NOTIFY_LOW_BALANCE_TYPE, today),
    )
    if cur.fetchone():
        return False
    account = _account(conn, account_id)
    amount = safe_int(wallet.get("auto_topup_amount_cents"), 0)
    body = (
        f"Your ad wallet spendable balance is {money(spendable)}, below your "
        f"{money(threshold)} top-up threshold."
    )
    if amount > 0:
        body += f" Top up {money(amount)} to keep campaigns running — no card is charged automatically."
    else:
        body += " Top up to keep campaigns running — no card is charged automatically."
    cur.execute(
        """
        INSERT INTO pulse_ad_notifications
        (account_id, campaign_id, creative_id, recipient_user_id, notification_type, title, body, status, created_at)
        VALUES (?, NULL, NULL, ?, ?, ?, ?, 'unread', ?)
        """,
        (
            account_id,
            safe_int(account.get("owner_user_id"), 0) or None,
            NOTIFY_LOW_BALANCE_TYPE,
            "Ad wallet balance is low",
            clean_text(body, 500),
            now_iso(),
        ),
    )
    return True
