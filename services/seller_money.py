"""The seller's money, read-only, from the surface that is actually live.

Why this module exists
----------------------
There are two money systems in this codebase and only one of them is reachable
in production.

  * ``services/business_os/...`` is the canonical double-entry ledger — richer,
    better tested, with real per-order escrow. Every route in front of it is
    gated on a ``BUSINESS_OS_*`` flag, and those flags are inert in production,
    so those routes answer 404 today.
  * ``creator_wallets`` + ``creator_ledger_entries`` is the system Stripe
    webhooks actually write to (``creator_economy_service.mark_transaction_paid``
    and ``handle_refund``). It holds the seller's real balances.

The second one had **no JSON reader at all**. Its only surface was a
server-rendered HTML page, ``/pulse/creator/payouts``, which reads one wallet
type for one user. The native Payments screen therefore had no way to render a
real balance, and the alternative — deriving one on the client by adding up
order rows — is precisely the client-side balance arithmetic the money screen
forbids. So this module is the reader, and it is a reader only: nothing here
writes, moves, releases, or initiates money.

What "backend-computed" means here
----------------------------------
``creator_wallets`` stores balance columns, but they are only correct as of the
last ``reconcile_wallet`` call. Trusting a cached column is how a screen ends up
presenting a stale number as current. So this module recomputes the balance in
SQL from the ledger on every read, using the *same* aggregation
``reconcile_wallet`` uses, and reports both figures plus a ``reconciled`` flag.
It does not repair the column — a read must not write — it just declines to lie
about which number it used.

The arithmetic is still the server's. The client receives finished totals.

Findings this module reports rather than hides
----------------------------------------------
1. **Nothing releases a hold.** Grep the tree: the only ``entry_type`` values
   ever written are ``hold`` (pending), ``fee`` (platform wallet) and
   ``refund``. There is no ``credit``, ``release``, ``debit`` or ``payout``
   writer anywhere. Since ``available = max(0, credits - debits)``, a seller's
   available balance is **structurally zero** and their money sits in
   ``pending`` forever. That is reported as ``release_path: "none_in_product"``
   so the screen can say something true instead of showing an unexplained
   $0.00 next to a non-zero Processing figure.
2. **There is no payout initiation.** No endpoint, no service call, nothing
   creates a payout. ``seller_payouts`` rows are only ever inserted by the
   Stripe Connect webhook. So "Pay out now" and "Instant payout" have no path
   and ship absent.
3. **There is no stored bank destination.** ``seller_payout_accounts`` holds a
   Stripe *connected account* id (``acct_…``), not an account number. The masked
   destination the design asks for does not exist as data. What can be shown
   truthfully is the Stripe account reference, and it is masked here anyway so
   the client never holds the whole identifier.
4. **There is no escrow on this surface.** Per-order escrow lives in the dark
   Business OS ledger. A hold in this system is a wallet-level pending balance,
   not money fenced per order, and it is labelled as such.

Every one of those is returned as an explicit machine-readable field, so the
client ships the affordance *absent* rather than rendering a control that cannot
work.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

from services import db as db_service


# Wallet types that belong to a selling human. ``platform`` is the house wallet
# (user_id 0) and must never be summed into somebody's balance.
SELLER_WALLET_TYPES = ("merchant", "teacher", "creator")

# The ledger vocabulary that is actually written today. Anything outside this
# set is surfaced with its real name and NO sign, because guessing the direction
# of an entry type nobody has defined is how a refund gets rendered as income.
_KIND_BY_ENTRY_TYPE = {
    "hold": ("escrow", "none"),
    "credit": ("income", "+"),
    "release": ("income", "+"),
    "refund": ("refund", "-"),
    "fee": ("spend", "-"),
    "payout": ("payout", "-"),
    "debit": ("spend", "-"),
}

_MAX_ACTIVITY_LIMIT = 100
_DEFAULT_ACTIVITY_LIMIT = 25


class SellerMoneyError(Exception):
    """A caller mistake — a bad cursor, a bad limit. Not a server fault."""


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #

def _now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _uid(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _cur(value: Any) -> str:
    return (str(value or "USD").strip() or "USD").upper()


def _row(row: Any) -> dict:
    if row is None:
        return {}
    try:
        return dict(row)
    except (TypeError, ValueError):
        return {}


def _json_or_empty(raw: Any) -> Any:
    """Metadata written by another process is not guaranteed to be valid JSON.

    A malformed blob degrades to empty rather than taking down a whole balance
    read — the row still renders, it just renders without its extras.
    """
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def mask_account_reference(value: Any) -> str:
    """Reduce an identifier to something safe to hand a client.

    The client is given four characters and a shape, never the identifier. This
    is applied server-side on purpose: a client that received the full string
    and masked it for display would still be holding the full string.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    tail = text[-4:]
    return "····" + tail


# --------------------------------------------------------------------------- #
# balances
# --------------------------------------------------------------------------- #

def _ledger_totals(cur, wallet_id: int) -> dict:
    """Recompute a wallet's balance from its entries.

    Deliberately the same aggregation ``creator_economy_service.reconcile_wallet``
    performs. If the two ever disagree the bug is one edit away from being found,
    which is the point of duplicating it here rather than paraphrasing it.
    """
    cur.execute(
        """
        SELECT
            COALESCE(SUM(CASE WHEN entry_type IN ('credit','release')
                              AND status IN ('posted','available')
                         THEN amount_cents ELSE 0 END), 0) AS credits,
            COALESCE(SUM(CASE WHEN entry_type IN ('debit','fee','refund','payout')
                              AND status IN ('posted','available')
                         THEN amount_cents ELSE 0 END), 0) AS debits,
            COALESCE(SUM(CASE WHEN entry_type='hold'
                              AND status IN ('pending','posted')
                         THEN amount_cents ELSE 0 END), 0) AS pending,
            COALESCE(SUM(CASE WHEN entry_type='fee'
                         THEN amount_cents ELSE 0 END), 0) AS fees,
            COUNT(*) AS entry_count
        FROM creator_ledger_entries
        WHERE wallet_id=?
        """,
        (int(wallet_id),),
    )
    totals = _row(cur.fetchone())
    credits = int(totals.get("credits") or 0)
    debits = int(totals.get("debits") or 0)
    return {
        "available_cents": max(0, credits - debits),
        "processing_cents": max(0, int(totals.get("pending") or 0)),
        "fees_cents": max(0, int(totals.get("fees") or 0)),
        "entry_count": int(totals.get("entry_count") or 0),
    }


def _wallets(cur, user_id: int, currency: str) -> list[dict]:
    cur.execute(
        """
        SELECT * FROM creator_wallets
        WHERE user_id=? AND currency=? AND wallet_type IN (?,?,?)
        ORDER BY wallet_type ASC
        """,
        (int(user_id), currency) + SELLER_WALLET_TYPES,
    )
    out = []
    for raw in cur.fetchall():
        wallet = _row(raw)
        wallet_id = int(wallet.get("id") or 0)
        if not wallet_id:
            continue
        computed = _ledger_totals(cur, wallet_id)
        stored_available = int(wallet.get("available_balance_cents") or 0)
        stored_processing = int(wallet.get("pending_balance_cents") or 0)
        out.append({
            "wallet_id": wallet_id,
            "wallet_type": str(wallet.get("wallet_type") or ""),
            "currency": _cur(wallet.get("currency")),
            "status": str(wallet.get("status") or "active"),
            # The figures the caller should render: recomputed from the ledger.
            "available_cents": computed["available_cents"],
            "processing_cents": computed["processing_cents"],
            "lifetime_fees_cents": computed["fees_cents"],
            "entry_count": computed["entry_count"],
            # What the cached columns claim, and whether they agree. Shipped so a
            # reconciliation drift is visible in the payload instead of silently
            # changing which number a screen shows.
            "stored_available_cents": stored_available,
            "stored_processing_cents": stored_processing,
            "reconciled": (stored_available == computed["available_cents"]
                           and stored_processing == computed["processing_cents"]),
            "lifetime_earnings_cents": int(wallet.get("lifetime_earnings_cents") or 0),
        })
    return out


# --------------------------------------------------------------------------- #
# payout method and payout state
# --------------------------------------------------------------------------- #

def _payout_method(cur, user_id: int) -> Optional[dict]:
    """The seller's payout onboarding, masked.

    Returns ``None`` when the seller has never started onboarding — which is the
    "no payout method" state, and the design says that state outranks every other
    prompt on the screen.
    """
    cur.execute(
        """
        SELECT * FROM seller_payout_accounts
        WHERE user_id=?
        ORDER BY CASE WHEN COALESCE(payouts_enabled,0)=1 THEN 0 ELSE 1 END,
                 updated_at DESC, id DESC
        LIMIT 1
        """,
        (int(user_id),),
    )
    account = _row(cur.fetchone())
    if not account:
        return None

    missing = _json_or_empty(account.get("missing_requirements_json")) or []
    if not isinstance(missing, list):
        missing = []
    connected_id = str(account.get("connected_account_id")
                       or account.get("provider_account_id") or "")
    return {
        "provider": str(account.get("provider") or "stripe"),
        "seller_type": str(account.get("seller_type") or ""),
        "onboarding_status": str(account.get("onboarding_status") or "not_started"),
        "payouts_enabled": bool(account.get("payouts_enabled")),
        "charges_enabled": bool(account.get("charges_enabled")),
        "connected": bool(connected_id),
        # A Stripe connected-account reference, masked. NOT a bank number: this
        # platform stores no bank account number anywhere, which is why there is
        # no "•••• 4321 checking" to render.
        "destination_kind": "stripe_connected_account",
        "destination_masked": mask_account_reference(connected_id),
        "bank_destination": "not_stored",
        "missing_requirements": [str(m) for m in missing][:20],
        "last_checked_at": account.get("last_checked_at") or None,
        "updated_at": account.get("updated_at") or None,
    }


def _payouts(cur, user_id: int, currency: str) -> dict:
    """Recent payout records, and the two that change what the screen shows.

    ``in_flight`` drives the "payout on its way" state; ``last_failed`` drives the
    failure state. Both come from real rows written by the Stripe Connect
    webhook — the app has never created one.
    """
    cur.execute(
        """
        SELECT * FROM seller_payouts
        WHERE user_id=? AND UPPER(COALESCE(currency,'USD'))=?
        ORDER BY created_at DESC, id DESC
        LIMIT 20
        """,
        (int(user_id), currency),
    )
    rows = [_row(r) for r in cur.fetchall()]
    recent = []
    for row in rows:
        recent.append({
            "payout_id": int(row.get("id") or 0),
            "amount_cents": int(row.get("amount_cents") or 0),
            "currency": _cur(row.get("currency")),
            "status": str(row.get("status") or "pending").lower(),
            "provider": str(row.get("provider") or "stripe"),
            "provider_payout_reference": mask_account_reference(
                row.get("provider_payout_id")),
            "failure_reason": str(row.get("failure_reason") or "") or None,
            "created_at": row.get("created_at") or None,
            "updated_at": row.get("updated_at") or None,
        })

    in_flight = next(
        (p for p in recent if p["status"] in {"pending", "in_transit", "processing"}),
        None)
    last_failed = next(
        (p for p in recent if p["status"] in {"failed", "canceled", "cancelled"}),
        None)
    return {"recent": recent, "in_flight": in_flight, "last_failed": last_failed}


# --------------------------------------------------------------------------- #
# the overview
# --------------------------------------------------------------------------- #

def seller_money_overview(user_id: Any, currency: str = "USD") -> dict:
    """Every balance the money hub renders, plus what this platform cannot source.

    The absences are as load-bearing as the figures. A client that reads only the
    numbers and ignores ``release_path`` / ``payout_initiation`` will render a
    payout button that no code path can honour.
    """
    uid = _uid(user_id)
    cur_code = _cur(currency)
    conn = db_service.connect()
    try:
        cur = conn.cursor()
        wallets = _wallets(cur, uid, cur_code)
        payout_method = _payout_method(cur, uid)
        payouts = _payouts(cur, uid, cur_code)

        available = sum(w["available_cents"] for w in wallets)
        processing = sum(w["processing_cents"] for w in wallets)
        fees = sum(w["lifetime_fees_cents"] for w in wallets)
        lifetime = sum(w["lifetime_earnings_cents"] for w in wallets)

        return {
            "seller_user_id": uid,
            "currency": cur_code,
            "as_of": _now_iso(),

            # --- the figures, all summed server-side ---
            "available_cents": available,
            "processing_cents": processing,
            "lifetime_fees_cents": fees,
            "lifetime_earnings_cents": lifetime,
            "wallets": wallets,
            "reconciled": all(w["reconciled"] for w in wallets) if wallets else True,
            "has_wallet": bool(wallets),

            # --- payout state ---
            "payout_method": payout_method,
            "payout_in_flight": payouts["in_flight"],
            "last_failed_payout": payouts["last_failed"],
            "recent_payouts": payouts["recent"],

            # --- what this platform cannot source, stated rather than omitted ---
            # Each of these means "ship the affordance ABSENT". See the module
            # docstring for how each was established.
            "release_path": "none_in_product",
            "payout_initiation": "unsupported",
            "instant_payout": "unsupported",
            "statements": "unsupported",
            "tax_documents": "unsupported",
            "escrow": {
                "supported": False,
                "reason": "per_order_escrow_is_business_os_only",
            },
            # The ad wallet is deliberately not here: it must be read from the
            # same endpoint the Advertising screen reads, or the two screens
            # diverge. See docs/business_os/PAYMENTS_MONEY_SOURCE_MAP.md §5.
            "ad_wallet_source": "pulse_ads_wallet_endpoint",
        }
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# the activity feed
# --------------------------------------------------------------------------- #

def _classify(entry_type: str, status: str) -> tuple[str, str]:
    kind, sign = _KIND_BY_ENTRY_TYPE.get(
        (entry_type or "").strip().lower(), ("other", "none"))
    # A hold that has been settled is no longer money being held. Nothing writes
    # that transition today, but if something starts to, the row must stop
    # rendering as an open hold on the strength of its type alone.
    if kind == "escrow" and (status or "").strip().lower() in {
            "released", "settled", "void", "cancelled", "canceled"}:
        return ("other", "none")
    return (kind, sign)


def _entry_title(entry: dict) -> str:
    """Prefer what the writer said. Fall back to the entry's own vocabulary.

    Never invents a counterparty name or an item title — this table stores
    neither, and a plausible-looking label is worse than a plain one.
    """
    described = str(entry.get("description") or "").strip()
    if described:
        return described[:200]
    entry_type = str(entry.get("entry_type") or "").strip().lower()
    source = str(entry.get("source_type") or "").strip().replace("_", " ")
    if source:
        return (source + " " + entry_type).strip()[:200]
    return entry_type or "Ledger entry"


def seller_activity(user_id: Any, currency: str = "USD", *,
                    limit: int = _DEFAULT_ACTIVITY_LIMIT,
                    before_cursor: Optional[str] = None) -> dict:
    """One page of the seller's real ledger rows, newest first.

    Pagination is keyset on ``id``, not on ``created_at``. ``created_at`` here is
    a second-resolution string written by the application, so several entries
    routinely share one value and a timestamp cursor would skip whichever of them
    fell on the page boundary. ``id`` is a monotonic primary key and cannot.

    Rows are returned as they are. A failed, refunded or disputed entry keeps its
    real status and stays in the list — money that vanishes from a ledger because
    it looked untidy is the worst failure this screen can have.
    """
    uid = _uid(user_id)
    cur_code = _cur(currency)

    try:
        page = int(limit)
    except (TypeError, ValueError):
        page = _DEFAULT_ACTIVITY_LIMIT
    page = max(1, min(_MAX_ACTIVITY_LIMIT, page))

    cursor_id = None
    if before_cursor not in (None, ""):
        try:
            cursor_id = int(str(before_cursor).strip())
        except (TypeError, ValueError):
            raise SellerMoneyError("cursor must be a ledger entry id")
        if cursor_id < 0:
            raise SellerMoneyError("cursor must be a ledger entry id")

    conn = db_service.connect()
    try:
        cur = conn.cursor()
        # Scoped by user_id AND by the caller's own wallets. user_id alone would
        # be enough today, but the platform wallet lives in the same table and a
        # single mistaken row with the wrong user_id must not become somebody
        # else's transaction.
        cur.execute(
            "SELECT id FROM creator_wallets WHERE user_id=? AND currency=? "
            "AND wallet_type IN (?,?,?)",
            (uid, cur_code) + SELLER_WALLET_TYPES,
        )
        wallet_ids = [int(_row(r).get("id") or 0) for r in cur.fetchall()]
        wallet_ids = [w for w in wallet_ids if w]
        if not wallet_ids:
            return {"seller_user_id": uid, "currency": cur_code, "entries": [],
                    "next_cursor": None, "has_more": False, "as_of": _now_iso()}

        placeholders = ",".join("?" for _ in wallet_ids)
        params: list[Any] = [uid, cur_code] + wallet_ids
        where_cursor = ""
        if cursor_id is not None:
            where_cursor = " AND id < ?"
            params.append(cursor_id)
        # Fetch one extra row to learn whether another page exists without a
        # second COUNT query over the same range.
        params.append(page + 1)
        cur.execute(
            "SELECT * FROM creator_ledger_entries "
            "WHERE user_id=? AND UPPER(COALESCE(currency,'USD'))=? "
            "AND wallet_id IN (%s)%s "
            "ORDER BY id DESC LIMIT ?" % (placeholders, where_cursor),
            tuple(params),
        )
        raw_rows = [_row(r) for r in cur.fetchall()]
    finally:
        conn.close()

    has_more = len(raw_rows) > page
    raw_rows = raw_rows[:page]

    entries = []
    for row in raw_rows:
        entry_type = str(row.get("entry_type") or "").strip().lower()
        status = str(row.get("status") or "").strip().lower()
        kind, sign = _classify(entry_type, status)
        source_id = str(row.get("source_id") or "").strip()
        source_type = str(row.get("source_type") or "").strip()
        entries.append({
            # Stable and real: the ledger row's own primary key. Nothing here is
            # a synthesised or positional id.
            "id": int(row.get("id") or 0),
            "kind": kind,
            "sign": sign,
            "entry_type": entry_type,
            "status": status or "posted",
            "amount_cents": int(row.get("amount_cents") or 0),
            "currency": _cur(row.get("currency")),
            "title": _entry_title(row),
            "reference": ({"type": source_type, "id": source_id}
                          if source_id else None),
            "counterparty_user_id": (int(row.get("related_user_id") or 0) or None),
            "provider": str(row.get("provider") or "") or None,
            "provider_reference": mask_account_reference(
                row.get("provider_reference")) or None,
            "trace_id": str(row.get("trace_id") or "") or None,
            "created_at": row.get("created_at") or None,
        })

    return {
        "seller_user_id": uid,
        "currency": cur_code,
        "entries": entries,
        "next_cursor": (str(entries[-1]["id"]) if entries and has_more else None),
        "has_more": has_more,
        "as_of": _now_iso(),
    }
