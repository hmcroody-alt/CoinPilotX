"""Business OS — Marketplace: the seller's money **read** surface.

This module exists so that a client rendering a payments screen never has to do
money arithmetic. Every figure it returns is either a ledger balance or a
server-side sum of ledger balances, and the activity feed is a page of real
``ledger_transactions`` rows with their real ids and statuses. Nothing here is
inferred from what happens to be on screen.

It is deliberately **read-only**. There is no ``post_entry`` call in this file
and there must never be one: the write side lives in ``orders.py`` and
``refunds.py``, which own the state machine that makes a posting legal. A read
module that could also move money would be a second, ungoverned payment path.

What the accounts actually mean here — stated plainly, because the wrong mental
model is the expensive kind of bug:

* ``seller_payable:<seller_id>`` accrues the seller's **net** the moment an order
  completes. This is money the platform owes the seller. It is what
  "available for payout" means, and it is a real ledger balance.
* ``mkt_order_escrow:<order_id>`` holds a **captured but unsettled** order total.
  It is per order, so a seller's total hold is a sum across accounts — which is
  why that sum is computed here, on the server, rather than by adding up
  whatever rows a paginated list happened to return.

One thing this backend does **not** model, and this module will not pretend
otherwise: there is no pickup-versus-delivery distinction. ``fulfillment_type``
is only ``physical`` or ``digital``. So there is exactly one hold concept, not
two. What does exist is the order state machine, and it draws a real line:

* status ``paid``      — captured, the seller has not fulfilled yet
* status ``fulfilled`` — seller has fulfilled, awaiting the buyer's completion

Those two buckets are reported separately because the state machine genuinely
distinguishes them. Whether a product calls one of them "processing" is a
labelling decision for the client; this module reports the states, not the
marketing.

PAYOUT EXECUTION: moving money to a seller's bank is a provider-side transfer
that this environment does not perform (see ``refunds.seller_payout_balance``).
Nothing here initiates, quotes, or estimates a disbursement.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from services import db
from services.business_os.marketplace import service as _svc
from services.business_os.marketplace import orders as _ord
from services.business_os.ledger import ledger as _ledger

# The order states in which captured funds are still sitting in escrow. Imported
# from the state machine rather than re-declared, so a change to the machine
# cannot silently desynchronise the money view from the orders view.
ESCROW_STATUSES = tuple(sorted(_ord.IN_ESCROW_STATUSES))

# A ceiling on how many escrow accounts one overview will read. A seller with
# more open orders than this gets a truthful `escrow_truncated: True` rather than
# a slow request or, worse, a quietly wrong total.
MAX_ESCROW_ACCOUNTS = 500


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# NOTE ON THE AD WALLET — deliberately not returned here.
#
# There are two advertiser wallets in this codebase and they are different
# systems, not two views of one:
#
#   * The **Pulse Ads wallet** (``services/pulse_ad_payments.py``, served at
#     ``/api/pulse/ads/accounts/<id>/wallet``) has its own balances, its own
#     ``pulse_ad_wallet_transactions`` table, receipts, and a Stripe funding
#     path. This is the wallet the Advertising screen renders today.
#   * The **Business OS advertising wallet** (``advertising/funding.py``, ledger
#     account ``advertiser:<uid>:wallet``) is part of the newer flag-gated
#     vertical and lives in the canonical ledger.
#
# The Payments screen's ad-wallet card must read the *same* object Advertising
# reads — the Pulse Ads one — because divergence between those two screens is a
# bug. Returning the Business OS wallet from this overview would have created
# exactly that divergence: two cards labelled "ad wallet", both sourced from a
# real backend, showing different numbers, with nothing on screen to say why.
#
# So this module returns neither. The client calls the ads wallet endpoint that
# Advertising already calls, and there stays exactly one source per card.


def _open_escrow_orders(conn, seller_id: str, currency: str) -> list:
    """The seller's orders whose funds are still held, newest first."""
    placeholders = ",".join("?" * len(ESCROW_STATUSES))
    rows = conn.execute(
        "SELECT order_id, status, total_cents, currency, created_at, updated_at "
        "FROM business_os_mkt_orders "
        "WHERE seller_user_id = ? AND currency = ? AND status IN (%s) "
        "ORDER BY created_at DESC LIMIT ?" % placeholders,
        (seller_id, currency, *ESCROW_STATUSES, MAX_ESCROW_ACCOUNTS + 1),
    ).fetchall()
    return [_ord._row(r) for r in rows]


def seller_money_overview(seller_user_id: Any, currency: str = "usd",
                          conn=None) -> dict:
    """Every balance a seller's payments screen needs, each one ledger-derived.

    Returns available (payable), the escrow total, and the escrow split by the
    order state that produced it. ``accounts`` is included so the caller can ask
    for the matching activity feed without re-deriving account names — the
    account naming scheme stays an implementation detail of the server.

    Amounts are integer cents. A missing account reads as 0, which is correct:
    an account with no postings holds nothing.
    """
    _svc._require_enabled()
    seller_id = _svc._sid(seller_user_id)
    cur_code = str(currency or "usd").lower()

    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        payable_account = _ord.seller_payable_account(seller_id)
        available_cents = _ledger.get_balance(payable_account, cur_code, conn=conn)

        orders = _open_escrow_orders(conn, seller_id, cur_code)
        truncated = len(orders) > MAX_ESCROW_ACCOUNTS
        orders = orders[:MAX_ESCROW_ACCOUNTS]

        escrow_accounts: list = []
        by_status: dict = {s: 0 for s in ESCROW_STATUSES}
        order_rows: list = []
        escrow_total = 0

        for o in orders:
            account = _ord.escrow_account(o.get("order_id"))
            # The held amount is read off the ledger, not off the order's
            # total_cents. Those differ the moment a partial refund lands, and
            # the ledger is the one that is right.
            held = _ledger.get_balance(account, cur_code, conn=conn)
            escrow_accounts.append(account)
            if held <= 0:
                # Fully refunded while still open: no money is held, so it does
                # not belong in a held total. The order still exists; it just
                # has nothing in escrow.
                continue
            status = str(o.get("status") or "")
            by_status[status] = by_status.get(status, 0) + held
            escrow_total += held
            order_rows.append({
                "order_id": o.get("order_id"),
                "status": status,
                "held_cents": held,
                "currency": cur_code,
                "created_at": o.get("created_at"),
                "updated_at": o.get("updated_at"),
            })

        return {
            "seller_user_id": seller_id,
            "currency": cur_code,
            # Money the platform owes the seller. Real ledger balance.
            "available_cents": available_cents,
            # Sum of per-order escrow balances. Summed here, on the server.
            "escrow_total_cents": escrow_total,
            "escrow_by_status": by_status,
            "escrow_order_count": len(order_rows),
            "escrow_orders": order_rows,
            "escrow_truncated": truncated,
            "accounts": {
                "payable": payable_account,
                "escrow": escrow_accounts,
            },
            # The ad wallet is intentionally NOT here. See the note above the
            # helpers: the card must read the same wallet the Advertising screen
            # reads, and that is the Pulse Ads wallet, not this vertical's.
            "ad_wallet_source": "pulse_ads_wallet_endpoint",
            # There is no disbursement engine in this environment, so there is
            # no schedule, no destination and no instant-payout quote to report.
            # Saying so explicitly keeps a client from inventing any of them.
            "payout_execution": "provider_side_out_of_scope",
            "computed_at": _now_iso(),
        }
    finally:
        if owned:
            conn.close()


def seller_disputes(seller_user_id: Any, *, status: Optional[str] = "open",
                    limit: int = 50, conn=None) -> dict:
    """The seller's own dispute cases, scoped by joining through their orders.

    The disputes table stores ``buyer_user_id`` but not ``seller_user_id``, so
    seller scoping is a join on the order. Doing it in SQL rather than by
    filtering a wider admin list in the caller is the point: an admin-shaped
    query that gets narrowed later is one forgotten filter away from showing a
    seller somebody else's case.

    Two fields are deliberately absent, because this backend does not have them:

    * **No response deadline.** There is no deadline column and no timer
      anywhere in the dispute lifecycle. A caller must therefore not render
      "respond within N days" — there is no N. ``response_deadline`` is returned
      as None and ``auto_approval_policy`` as ``"none_defined"`` so the absence
      is explicit rather than something a client has to notice.
    * **No seller resolution authority.** ``resolve_dispute`` takes an admin
      actor. A seller can read their cases here; they cannot decide them. The
      returned ``seller_can_resolve`` says so, rather than leaving a client to
      build a button that will 403.

    ``amount_cents`` is the order total the dispute is about, taken from the
    order row. ``held_cents`` is what is still in escrow for it right now, read
    from the ledger — those differ after a partial refund, and the second one is
    the money actually at stake.
    """
    _svc._require_enabled()
    seller_id = _svc._sid(seller_user_id)

    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        q = ("SELECT d.dispute_id, d.order_id, d.buyer_user_id, d.status, d.reason, "
             "       d.resolution, d.created_at, d.updated_at, "
             "       o.total_cents, o.refunded_cents, o.currency, o.status AS order_status "
             "FROM business_os_mkt_disputes d "
             "JOIN business_os_mkt_orders o ON o.order_id = d.order_id "
             "WHERE o.seller_user_id = ?")
        params: list = [seller_id]
        if status:
            q += " AND d.status = ?"
            params.append(str(status))
        q += " ORDER BY d.created_at DESC LIMIT ?"
        params.append(max(1, min(int(limit), 200)))

        rows = [_ord._row(r) for r in conn.execute(q, tuple(params)).fetchall()]

        cases = []
        for r in rows:
            cur_code = str(r.get("currency") or "usd").lower()
            cases.append({
                "dispute_id": r.get("dispute_id"),
                "order_id": r.get("order_id"),
                "buyer_user_id": r.get("buyer_user_id"),
                "status": r.get("status"),
                "reason": r.get("reason") or "",
                "resolution": r.get("resolution"),
                "order_status": r.get("order_status"),
                "amount_cents": int(r.get("total_cents") or 0),
                "refunded_cents": int(r.get("refunded_cents") or 0),
                "held_cents": _ledger.get_balance(
                    _ord.escrow_account(r.get("order_id")), cur_code, conn=conn),
                "currency": cur_code,
                "opened_at": r.get("created_at"),
                "updated_at": r.get("updated_at"),
                "response_deadline": None,
            })

        return {
            "seller_user_id": seller_id,
            "status_filter": status,
            "open_count": len(cases) if status == "open" else None,
            "cases": cases,
            "auto_approval_policy": "none_defined",
            "seller_can_resolve": False,
            "computed_at": _now_iso(),
        }
    finally:
        if owned:
            conn.close()


def seller_activity(seller_user_id: Any, currency: str = "usd", *,
                    limit: int = _ledger.DEFAULT_LIST_LIMIT,
                    before_cursor: Optional[str] = None,
                    entry_types: Optional[Any] = None,
                    conn=None) -> dict:
    """One page of the seller's money activity, newest first.

    The union of "payable plus every open escrow account" is assembled here and
    executed as a single ledger query, so the caller receives one ordered,
    correctly paginated feed instead of several lists to interleave by hand.

    Each row's ``signed_amount_cents`` is already expressed from the seller's
    point of view; a client renders that sign and does not re-derive it.

    Note the deliberate limitation: escrow accounts for orders that have already
    settled are not in the account set, because the order is no longer open. The
    settlement itself is still visible — it credited the payable account, which
    is always in the set. ``accounts_scanned`` is returned so this is auditable
    rather than mysterious.
    """
    _svc._require_enabled()
    seller_id = _svc._sid(seller_user_id)
    cur_code = str(currency or "usd").lower()

    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        accounts = [_ord.seller_payable_account(seller_id)]
        for o in _open_escrow_orders(conn, seller_id, cur_code)[:MAX_ESCROW_ACCOUNTS]:
            accounts.append(_ord.escrow_account(o.get("order_id")))

        page = _ledger.list_account_transactions(
            accounts, cur_code, limit=limit, before_cursor=before_cursor,
            entry_types=entry_types, conn=conn)
        page["seller_user_id"] = seller_id
        page["accounts_scanned"] = len(accounts)
        return page
    finally:
        if owned:
            conn.close()
