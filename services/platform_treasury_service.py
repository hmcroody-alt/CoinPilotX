"""Platform treasury, fee ledger, settlement, and payout visibility service."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from services import db as db_service


def now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def _money(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _scalar(cur, sql: str, params=()):
    cur.execute(sql, params)
    row = cur.fetchone()
    if not row:
        return 0
    return list(dict(row).values())[0]


def ensure_platform_wallet(currency: str = "USD", wallet_key: str = "platform_fee_treasury") -> dict[str, Any]:
    now = now_iso()
    currency = (currency or "USD").upper()
    conn = db_service.connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM platform_wallets WHERE wallet_key=? AND currency=? LIMIT 1", (wallet_key, currency))
    row = cur.fetchone()
    if row:
        conn.close()
        return dict(row)
    cur.execute(
        """
        INSERT INTO platform_wallets
        (wallet_key, currency, available_balance_cents, pending_balance_cents, lifetime_revenue_cents, lifetime_refunds_cents, status, metadata_json, created_at, updated_at)
        VALUES (?, ?, 0, 0, 0, 0, 'active', ?, ?, ?)
        """,
        (wallet_key, currency, json.dumps({"purpose": "CoinPilotXAI platform fee treasury"}), now, now),
    )
    wallet_id = cur.lastrowid
    conn.commit()
    cur.execute("SELECT * FROM platform_wallets WHERE id=?", (wallet_id,))
    wallet = dict(cur.fetchone() or {})
    conn.close()
    return wallet


def _open_settlement_batch(cur, currency: str = "USD") -> dict[str, Any]:
    today = datetime.utcnow().strftime("%Y-%m-%d")
    key = f"{today}:{currency.upper()}"
    cur.execute("SELECT * FROM settlement_batches WHERE batch_key=? LIMIT 1", (key,))
    row = cur.fetchone()
    if row:
        return dict(row)
    now = now_iso()
    cur.execute(
        """
        INSERT INTO settlement_batches
        (batch_key, currency, status, gross_amount_cents, platform_fee_cents, creator_net_cents, transaction_count, opened_at, metadata_json)
        VALUES (?, ?, 'open', 0, 0, 0, 0, ?, ?)
        """,
        (key, currency.upper(), now, json.dumps({"batch_type": "daily_platform_settlement"})),
    )
    cur.execute("SELECT * FROM settlement_batches WHERE id=?", (cur.lastrowid,))
    return dict(cur.fetchone() or {})


def _holding_release_after(seller_type: str) -> str:
    days = 7 if seller_type == "merchant" else 3 if seller_type == "teacher" else 3
    return (datetime.utcnow() + timedelta(days=days)).isoformat(timespec="seconds")


def sync_creator_balance(user_id: int, seller_type: str, currency: str = "USD") -> dict[str, Any]:
    now = now_iso()
    conn = db_service.connect()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            COALESCE(SUM(gross_amount_cents),0) AS gross,
            COALESCE(SUM(platform_fee_cents),0) AS fees,
            COALESCE(SUM(net_amount_cents),0) AS net
        FROM creator_transactions
        WHERE seller_user_id=? AND seller_type=? AND currency=? AND status IN ('paid','refunded','disputed')
        """,
        (int(user_id or 0), seller_type, currency.upper()),
    )
    totals = dict(cur.fetchone() or {})
    cur.execute(
        "SELECT COALESCE(SUM(pending_balance_cents),0) AS pending, COALESCE(SUM(available_balance_cents),0) AS available FROM creator_wallets WHERE user_id=? AND wallet_type=? AND currency=?",
        (int(user_id or 0), seller_type, currency.upper()),
    )
    wallet_totals = dict(cur.fetchone() or {})
    cur.execute(
        """
        INSERT INTO creator_balances
        (user_id, seller_type, currency, pending_balance_cents, available_balance_cents, lifetime_gross_cents, lifetime_fees_cents, lifetime_net_cents, frozen, risk_score, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?)
        ON CONFLICT(user_id, seller_type, currency) DO UPDATE SET
            pending_balance_cents=excluded.pending_balance_cents,
            available_balance_cents=excluded.available_balance_cents,
            lifetime_gross_cents=excluded.lifetime_gross_cents,
            lifetime_fees_cents=excluded.lifetime_fees_cents,
            lifetime_net_cents=excluded.lifetime_net_cents,
            updated_at=excluded.updated_at
        """,
        (
            int(user_id or 0),
            seller_type,
            currency.upper(),
            _money(wallet_totals.get("pending")),
            _money(wallet_totals.get("available")),
            _money(totals.get("gross")),
            _money(totals.get("fees")),
            _money(totals.get("net")),
            now,
        ),
    )
    conn.commit()
    cur.execute("SELECT * FROM creator_balances WHERE user_id=? AND seller_type=? AND currency=? LIMIT 1", (int(user_id or 0), seller_type, currency.upper()))
    row = dict(cur.fetchone() or {})
    conn.close()
    return row


def record_platform_fee_from_transaction(tx: dict[str, Any], provider_reference: str = "") -> dict[str, Any]:
    transaction_id = str(tx.get("id") or "")
    if not transaction_id:
        return {"ok": False, "message": "Transaction id required."}
    currency = (tx.get("currency") or "USD").upper()
    wallet = ensure_platform_wallet(currency)
    now = now_iso()
    conn = db_service.connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM treasury_transactions WHERE transaction_type='platform_fee' AND source_type='creator_transaction' AND source_id=? LIMIT 1",
        (transaction_id,),
    )
    existing = cur.fetchone()
    if existing:
        conn.close()
        return {"ok": True, "idempotent": True, "treasury_transaction_id": dict(existing).get("id")}
    batch = _open_settlement_batch(cur, currency)
    gross = _money(tx.get("gross_amount_cents"))
    fee = _money(tx.get("platform_fee_cents"))
    net = _money(tx.get("net_amount_cents"))
    trace_id = tx.get("trace_id") or ""
    metadata = {"creator_transaction_id": int(tx.get("id") or 0), "settlement_batch_id": batch.get("id")}
    cur.execute(
        """
        INSERT INTO treasury_transactions
        (wallet_id, transaction_type, source_type, source_id, buyer_user_id, seller_user_id, seller_type, item_type, gross_amount_cents, platform_fee_cents, creator_net_cents, currency, status, settlement_status, provider, provider_reference, trace_id, metadata_json, created_at, updated_at)
        VALUES (?, 'platform_fee', 'creator_transaction', ?, ?, ?, ?, ?, ?, ?, ?, ?, 'posted', 'batched', ?, ?, ?, ?, ?, ?)
        """,
        (
            int(wallet.get("id") or 0),
            transaction_id,
            int(tx.get("buyer_user_id") or 0),
            int(tx.get("seller_user_id") or 0),
            tx.get("seller_type") or "",
            tx.get("item_type") or "",
            gross,
            fee,
            net,
            currency,
            tx.get("provider") or "stripe",
            provider_reference or tx.get("provider_payment_id") or tx.get("provider_checkout_id") or "",
            trace_id,
            json.dumps(metadata, default=str),
            now,
            now,
        ),
    )
    treasury_id = cur.lastrowid
    cur.execute(
        """
        INSERT OR IGNORE INTO fee_ledger
        (treasury_transaction_id, source_type, source_id, fee_type, amount_cents, currency, status, provider, provider_reference, trace_id, created_at)
        VALUES (?, 'creator_transaction', ?, 'platform_fee', ?, ?, 'earned', ?, ?, ?, ?)
        """,
        (treasury_id, transaction_id, fee, currency, tx.get("provider") or "stripe", provider_reference or "", trace_id, now),
    )
    period_key = datetime.utcnow().strftime("%Y-%m")
    revenue_source = tx.get("item_type") or "creator_sale"
    cur.execute(
        """
        INSERT INTO revenue_breakdown
        (period_key, revenue_source, seller_type, item_type, currency, gross_amount_cents, platform_fee_cents, creator_net_cents, refunds_cents, transaction_count, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 1, ?)
        ON CONFLICT(period_key, revenue_source, seller_type, item_type, currency) DO UPDATE SET
            gross_amount_cents=revenue_breakdown.gross_amount_cents+excluded.gross_amount_cents,
            platform_fee_cents=revenue_breakdown.platform_fee_cents+excluded.platform_fee_cents,
            creator_net_cents=revenue_breakdown.creator_net_cents+excluded.creator_net_cents,
            transaction_count=revenue_breakdown.transaction_count+1,
            updated_at=excluded.updated_at
        """,
        (period_key, revenue_source, tx.get("seller_type") or "", tx.get("item_type") or "", currency, gross, fee, net, now),
    )
    cur.execute(
        """
        INSERT OR IGNORE INTO escrow_holds
        (creator_transaction_id, seller_user_id, seller_type, amount_cents, currency, status, hold_reason, release_after, trace_id, metadata_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, 'held', 'standard_seller_hold', ?, ?, ?, ?, ?)
        """,
        (int(tx.get("id") or 0), int(tx.get("seller_user_id") or 0), tx.get("seller_type") or "", net, currency, _holding_release_after(tx.get("seller_type") or ""), trace_id, json.dumps(metadata, default=str), now, now),
    )
    if net > 0:
        cur.execute(
            """
            INSERT INTO payout_queue
            (user_id, seller_type, wallet_id, amount_cents, currency, status, scheduled_for, attempts, provider, risk_status, trace_id, metadata_json, created_at, updated_at)
            VALUES (?, ?, 0, ?, ?, 'queued', ?, 0, 'stripe', 'clear', ?, ?, ?, ?)
            """,
            (int(tx.get("seller_user_id") or 0), tx.get("seller_type") or "", net, currency, _holding_release_after(tx.get("seller_type") or ""), trace_id, json.dumps(metadata, default=str), now, now),
        )
    cur.execute(
        "UPDATE platform_wallets SET available_balance_cents=available_balance_cents+?, lifetime_revenue_cents=lifetime_revenue_cents+?, updated_at=? WHERE id=?",
        (fee, fee, now, int(wallet.get("id") or 0)),
    )
    cur.execute(
        """
        UPDATE settlement_batches
        SET gross_amount_cents=gross_amount_cents+?,
            platform_fee_cents=platform_fee_cents+?,
            creator_net_cents=creator_net_cents+?,
            transaction_count=transaction_count+1
        WHERE id=?
        """,
        (gross, fee, net, int(batch.get("id") or 0)),
    )
    conn.commit()
    conn.close()
    sync_creator_balance(int(tx.get("seller_user_id") or 0), tx.get("seller_type") or "creator", currency)
    return {"ok": True, "treasury_transaction_id": int(treasury_id), "fee_cents": fee}


def record_refund_reversal(tx: dict[str, Any], refund_amount_cents: int, provider_reference: str = "") -> dict[str, Any]:
    transaction_id = str(tx.get("id") or "")
    if not transaction_id:
        return {"ok": False, "message": "Transaction id required."}
    gross = max(1, _money(tx.get("gross_amount_cents")))
    original_fee = _money(tx.get("platform_fee_cents"))
    fee_refund = min(original_fee, int(round(original_fee * (max(0, refund_amount_cents) / gross))))
    currency = (tx.get("currency") or "USD").upper()
    wallet = ensure_platform_wallet(currency)
    now = now_iso()
    conn = db_service.connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM treasury_transactions WHERE transaction_type='fee_refund' AND source_type='creator_transaction' AND source_id=? LIMIT 1",
        (transaction_id,),
    )
    if cur.fetchone():
        conn.close()
        return {"ok": True, "idempotent": True}
    cur.execute(
        """
        INSERT INTO treasury_transactions
        (wallet_id, transaction_type, source_type, source_id, buyer_user_id, seller_user_id, seller_type, item_type, gross_amount_cents, platform_fee_cents, creator_net_cents, currency, status, settlement_status, provider, provider_reference, trace_id, metadata_json, created_at, updated_at)
        VALUES (?, 'fee_refund', 'creator_transaction', ?, ?, ?, ?, ?, ?, ?, ?, ?, 'posted', 'reversed', ?, ?, ?, ?, ?, ?)
        """,
        (int(wallet.get("id") or 0), transaction_id, int(tx.get("buyer_user_id") or 0), int(tx.get("seller_user_id") or 0), tx.get("seller_type") or "", tx.get("item_type") or "", _money(refund_amount_cents), fee_refund, 0, currency, tx.get("provider") or "stripe", provider_reference, tx.get("trace_id") or "", json.dumps({"creator_transaction_id": int(tx.get("id") or 0)}, default=str), now, now),
    )
    cur.execute(
        "INSERT OR IGNORE INTO fee_ledger (treasury_transaction_id, source_type, source_id, fee_type, amount_cents, currency, status, provider, provider_reference, trace_id, created_at) VALUES (?, 'creator_transaction', ?, 'platform_fee_refund', ?, ?, 'reversed', ?, ?, ?, ?)",
        (cur.lastrowid, transaction_id, fee_refund, currency, tx.get("provider") or "stripe", provider_reference, tx.get("trace_id") or "", now),
    )
    cur.execute("UPDATE platform_wallets SET available_balance_cents=MAX(0, available_balance_cents-?), lifetime_refunds_cents=lifetime_refunds_cents+?, updated_at=? WHERE id=?", (fee_refund, fee_refund, now, int(wallet.get("id") or 0)))
    conn.commit()
    conn.close()
    sync_creator_balance(int(tx.get("seller_user_id") or 0), tx.get("seller_type") or "creator", currency)
    return {"ok": True, "fee_refund_cents": fee_refund}


def treasury_summary() -> dict[str, Any]:
    conn = db_service.connect()
    cur = conn.cursor()
    today = datetime.utcnow().strftime("%Y-%m-%d")
    month = datetime.utcnow().strftime("%Y-%m")
    summary = {
        "total_platform_revenue_cents": _scalar(cur, "SELECT COALESCE(SUM(platform_fee_cents),0) AS total FROM treasury_transactions WHERE transaction_type='platform_fee'"),
        "today_revenue_cents": _scalar(cur, "SELECT COALESCE(SUM(platform_fee_cents),0) AS total FROM treasury_transactions WHERE transaction_type='platform_fee' AND created_at>=?", (today,)),
        "monthly_revenue_cents": _scalar(cur, "SELECT COALESCE(SUM(platform_fee_cents),0) AS total FROM treasury_transactions WHERE transaction_type='platform_fee' AND created_at>=?", (month,)),
        "creator_sales_volume_cents": _scalar(cur, "SELECT COALESCE(SUM(gross_amount_cents),0) AS total FROM creator_transactions WHERE status IN ('paid','refunded','disputed')"),
        "pending_payouts_cents": _scalar(cur, "SELECT COALESCE(SUM(amount_cents),0) AS total FROM payout_queue WHERE status IN ('queued','processing')"),
        "failed_payouts": _scalar(cur, "SELECT COUNT(*) AS total FROM payout_failures"),
        "refund_rate_bps": 0,
        "merchant_revenue_cents": _scalar(cur, "SELECT COALESCE(SUM(platform_fee_cents),0) AS total FROM treasury_transactions WHERE seller_type='merchant' AND transaction_type='platform_fee'"),
        "teacher_revenue_cents": _scalar(cur, "SELECT COALESCE(SUM(platform_fee_cents),0) AS total FROM treasury_transactions WHERE seller_type='teacher' AND transaction_type='platform_fee'"),
        "premium_revenue_cents": _scalar(cur, "SELECT COALESCE(SUM(platform_fee_cents),0) AS total FROM treasury_transactions WHERE item_type='premium' AND transaction_type='platform_fee'"),
        "livestream_revenue_cents": _scalar(cur, "SELECT COALESCE(SUM(platform_fee_cents),0) AS total FROM treasury_transactions WHERE item_type IN ('live_class','livestream','live_ticket') AND transaction_type='platform_fee'"),
    }
    tx_count = _scalar(cur, "SELECT COUNT(*) AS total FROM creator_transactions WHERE status IN ('paid','refunded','disputed')")
    refund_count = _scalar(cur, "SELECT COUNT(*) AS total FROM creator_transactions WHERE status='refunded'")
    summary["refund_rate_bps"] = int((refund_count / tx_count) * 10000) if tx_count else 0
    cur.execute("SELECT * FROM platform_wallets ORDER BY updated_at DESC LIMIT 10")
    wallets = [dict(row) for row in cur.fetchall()]
    cur.execute("SELECT * FROM treasury_transactions ORDER BY id DESC LIMIT 50")
    treasury_transactions = [dict(row) for row in cur.fetchall()]
    cur.execute("SELECT * FROM fee_ledger ORDER BY id DESC LIMIT 50")
    fee_ledger = [dict(row) for row in cur.fetchall()]
    cur.execute("SELECT * FROM payout_queue ORDER BY id DESC LIMIT 50")
    payout_queue = [dict(row) for row in cur.fetchall()]
    cur.execute("SELECT * FROM escrow_holds ORDER BY id DESC LIMIT 50")
    escrow_holds = [dict(row) for row in cur.fetchall()]
    cur.execute("SELECT * FROM revenue_breakdown ORDER BY period_key DESC, platform_fee_cents DESC LIMIT 24")
    revenue_breakdown = [dict(row) for row in cur.fetchall()]
    cur.execute("SELECT * FROM creator_balances ORDER BY lifetime_net_cents DESC LIMIT 20")
    creator_balances = [dict(row) for row in cur.fetchall()]
    conn.close()
    return {
        "summary": summary,
        "wallets": wallets,
        "treasury_transactions": treasury_transactions,
        "fee_ledger": fee_ledger,
        "payout_queue": payout_queue,
        "escrow_holds": escrow_holds,
        "revenue_breakdown": revenue_breakdown,
        "creator_balances": creator_balances,
    }
