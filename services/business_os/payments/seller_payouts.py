"""Seller payout lifecycle engine (Wave B — Stripe Connect payouts).

The write side of "release": a seller with an earned ``seller_payable:<uid>``
ledger balance asks for a payout, the request is recorded and the money moved
into ``seller_payout_pending:<uid>``, and Stripe's payout webhooks drive the
row through its state machine until the funds either settle
(``platform:payouts_settled`` — money has left the platform) or bounce back to
the payable balance.

Non-negotiables inherited from the Wave A infrastructure:

* **Stripe is authoritative.** Local rows are projections of what Stripe said.
  An observed transition the local state machine does not allow is *recorded
  anyway* — refusing the fact would make the projection a fiction — but every
  such recording opens a ``PAYOUT_STATE_CONFLICT`` incident so a human sees
  that the books and the provider disagreed.
* **No network in core logic.** ``request_payout`` takes a caller-supplied
  Connect account snapshot and never calls Stripe; ``build_stripe_payout_args``
  hands a networked caller (route/worker) exactly the kwargs it should pass to
  the Stripe API server-side. Webhook appliers are pure functions over the
  event dict.
* **Integer cents, double-entry, idempotent.** All money movement goes through
  ``business_os.ledger.post_entry`` with deterministic idempotency keys, so a
  replayed webhook or a double-submitted request can never move money twice.

TABLE NAMING: the obvious name ``seller_payouts`` is already taken by a legacy
table created in ``bot.init_db()`` (different columns, rows written by the old
Connect webhook branch). That history is left intact per policy, so the new
lifecycle lives in ``seller_payout_requests`` + ``seller_payout_events``.

Engine-portable via ``services.db``; does not import ``bot.py``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from services import db
from services.business_os.ledger import ledger
from services.business_os.payments import incidents

# --- ledger accounts ---------------------------------------------------------

def seller_payable_account(user_id: Any) -> str:
    return f"seller_payable:{user_id}"


def payout_pending_account(user_id: Any) -> str:
    return f"seller_payout_pending:{user_id}"


#: Money that has actually left the platform via a paid Stripe payout.
PAYOUTS_SETTLED_ACCOUNT = "platform:payouts_settled"

# --- state machine -----------------------------------------------------------

#: Legal transitions. ``paid`` is deliberately terminal here: Stripe *can*
#: report a payout as failed after paying it (a bank return), and when that
#: happens we record the Stripe truth as ``returned`` — but via the
#: illegal-transition path, which opens a critical PAYOUT_STATE_CONFLICT,
#: because money that came back after settling is never routine.
ALLOWED_TRANSITIONS = {
    "pending": {"payout_created", "failed", "canceled"},
    "payout_created": {"in_transit", "paid", "failed", "canceled"},
    "in_transit": {"paid", "failed", "canceled"},
    "paid": set(),
    "failed": set(),
    "canceled": set(),
    "returned": set(),
}

STATUSES = set(ALLOWED_TRANSITIONS)
TERMINAL_STATUSES = {"paid", "failed", "canceled", "returned"}
NON_TERMINAL_STATUSES = STATUSES - TERMINAL_STATUSES

#: Stripe payout webhook types this engine consumes.
HANDLED_PAYOUT_EVENTS = {
    "payout.created",
    "payout.updated",
    "payout.paid",
    "payout.failed",
    "payout.canceled",
}
#: Bookkeeping-only Stripe notification; carries no state we project.
IGNORED_PAYOUT_EVENTS = {"payout.reconciliation_completed"}

_STRIPE_STATUS_MAP = {
    "pending": "payout_created",
    "in_transit": "in_transit",
    "paid": "paid",
    "failed": "failed",
    "canceled": "canceled",
}

MAX_LIST_LIMIT = 100
DEFAULT_LIST_LIMIT = 25

INCIDENT_DOMAIN = "seller_payments"


class PayoutError(ValueError):
    """Rejected payout operation. ``status_code`` maps onto the HTTP layer."""

    def __init__(self, message: str, status_code: int = 400, reason: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.reason = reason or ""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _row_to_dict(row) -> Optional[dict]:
    if row is None:
        return None
    try:
        return dict(row)
    except Exception:
        return {k: row[k] for k in row.keys()}


def _is_unique_violation(exc: Exception) -> bool:
    name = exc.__class__.__name__.lower()
    msg = str(exc).lower()
    if "integrityerror" in name or "uniqueviolation" in name:
        return True
    return "unique" in msg or "duplicate key" in msg


def ensure_schema(conn=None) -> None:
    """Create the payout tables if absent. Idempotent; safe at startup."""
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS seller_payout_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                payout_key TEXT NOT NULL UNIQUE,
                user_id TEXT NOT NULL,
                connected_account_id TEXT,
                amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
                currency TEXT NOT NULL DEFAULT 'usd',
                status TEXT NOT NULL DEFAULT 'pending',
                stripe_payout_id TEXT,
                stripe_transfer_id TEXT,
                failure_code TEXT,
                failure_message TEXT,
                requested_by TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS seller_payout_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                payout_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                stripe_event_id TEXT,
                details_json TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_seller_payout_requests_user "
            "ON seller_payout_requests (user_id, status)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_seller_payout_requests_stripe "
            "ON seller_payout_requests (stripe_payout_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_seller_payout_events_payout "
            "ON seller_payout_events (payout_id)"
        )
        if owned:
            conn.commit()
    finally:
        if owned:
            conn.close()


# ---------------------------------------------------------------------------
# reads
# ---------------------------------------------------------------------------

def get_payout(payout_id: Optional[int] = None,
               payout_key: Optional[str] = None,
               conn=None) -> Optional[dict]:
    """Fetch one payout row by numeric id or by its idempotency key."""
    if payout_id is None and not payout_key:
        return None
    owned = conn is None
    if owned:
        ensure_schema()
        conn = db.connect()
    try:
        if payout_id is not None:
            cur = conn.execute(
                "SELECT * FROM seller_payout_requests WHERE id = ?",
                (int(payout_id),),
            )
        else:
            cur = conn.execute(
                "SELECT * FROM seller_payout_requests WHERE payout_key = ?",
                (str(payout_key),),
            )
        return _row_to_dict(cur.fetchone())
    finally:
        if owned:
            conn.close()


def _get_by_stripe_payout_id(conn, stripe_payout_id: str) -> Optional[dict]:
    cur = conn.execute(
        "SELECT * FROM seller_payout_requests WHERE stripe_payout_id = ? "
        "ORDER BY id DESC LIMIT 1",
        (str(stripe_payout_id),),
    )
    return _row_to_dict(cur.fetchone())


def list_payouts(user_id: Optional[Any] = None, *,
                 status: Optional[str] = None,
                 limit: int = DEFAULT_LIST_LIMIT,
                 before_id: Optional[Any] = None) -> dict:
    """One keyset-paginated page of payout rows, newest first.

    Returns ``{"payouts", "next_before_id", "has_more"}``.
    """
    if status is not None and status not in STATUSES:
        raise PayoutError(f"unknown payout status {status!r}")
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = DEFAULT_LIST_LIMIT
    limit = max(1, min(limit, MAX_LIST_LIMIT))
    cursor_id: Optional[int] = None
    if before_id not in (None, "", 0):
        try:
            cursor_id = int(before_id)
        except (TypeError, ValueError):
            raise PayoutError("before_id must be a numeric payout id")

    where = []
    params: list = []
    if user_id not in (None, ""):
        where.append("user_id = ?")
        params.append(str(user_id))
    if status:
        where.append("status = ?")
        params.append(status)
    if cursor_id is not None:
        where.append("id < ?")
        params.append(cursor_id)
    params.append(limit + 1)

    sql = "SELECT * FROM seller_payout_requests"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ?"

    ensure_schema()
    conn = db.connect()
    try:
        rows = [_row_to_dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]
    finally:
        conn.close()
    has_more = len(rows) > limit
    rows = rows[:limit]
    return {
        "payouts": rows,
        "next_before_id": rows[-1]["id"] if (rows and has_more) else None,
        "has_more": has_more,
    }


def list_payout_events(payout_id: int, conn=None) -> list:
    """The append-only activity trail for one payout, oldest first."""
    owned = conn is None
    if owned:
        ensure_schema()
        conn = db.connect()
    try:
        rows = [
            _row_to_dict(r)
            for r in conn.execute(
                "SELECT * FROM seller_payout_events WHERE payout_id = ? "
                "ORDER BY id ASC",
                (int(payout_id),),
            ).fetchall()
        ]
        for row in rows:
            raw = row.pop("details_json", None)
            try:
                row["details"] = json.loads(raw) if raw else {}
            except (TypeError, ValueError):
                row["details"] = {}
        return rows
    finally:
        if owned:
            conn.close()


def seller_balance_summary(user_id: Any, currency: str = "usd") -> dict:
    """The three balances a payout screen needs, each one ledger-derived.

    * ``available_cents`` — ``seller_payable:<uid>`` (requestable now)
    * ``payout_pending_cents`` — ``seller_payout_pending:<uid>`` (requested,
      not yet settled or reversed)
    * ``processing_cents`` — funds still in per-order marketplace escrow,
      borrowed from :func:`marketplace.money.seller_money_overview` rather
      than re-deriving the escrow logic here. When that vertical is disabled
      (flag off / tables absent) the figure is reported as unavailable, never
      guessed at.
    """
    ledger.ensure_schema()
    cur_code = str(currency or "usd").lower()
    available = ledger.get_balance(seller_payable_account(user_id), cur_code)
    pending = ledger.get_balance(payout_pending_account(user_id), cur_code)

    processing = 0
    processing_source = "unavailable"
    try:
        from services.business_os.marketplace import money as _mkt_money

        overview = _mkt_money.seller_money_overview(user_id, cur_code)
        processing = int(overview.get("escrow_total_cents") or 0)
        processing_source = "marketplace_escrow"
    except Exception:
        # Marketplace vertical disabled or unreadable: report the absence
        # explicitly instead of a silent 0-that-means-two-things.
        processing = 0
        processing_source = "unavailable"

    return {
        "user_id": str(user_id),
        "currency": cur_code,
        "available_cents": available,
        "payout_pending_cents": pending,
        "processing_cents": processing,
        "processing_source": processing_source,
        "accounts": {
            "available": seller_payable_account(user_id),
            "payout_pending": payout_pending_account(user_id),
        },
        "computed_at": _utc_now_iso(),
    }


# ---------------------------------------------------------------------------
# internal writers
# ---------------------------------------------------------------------------

def _append_event(conn, payout_id: int, event_type: str,
                  stripe_event_id: str = "",
                  details: Optional[Mapping[str, Any]] = None) -> Optional[dict]:
    """Append one immutable activity-trail row.

    Deduplicated on (payout_id, stripe_event_id) when a Stripe event id is
    present, so a replayed webhook does not duplicate the trail. Local events
    (no Stripe id) always append.
    """
    stripe_event_id = str(stripe_event_id or "")
    if stripe_event_id:
        cur = conn.execute(
            "SELECT id FROM seller_payout_events "
            "WHERE payout_id = ? AND stripe_event_id = ? AND event_type = ? LIMIT 1",
            (int(payout_id), stripe_event_id, str(event_type)),
        )
        if cur.fetchone() is not None:
            return None
    now = _utc_now_iso()
    conn.execute(
        "INSERT INTO seller_payout_events "
        "(payout_id, event_type, stripe_event_id, details_json, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            int(payout_id), str(event_type), stripe_event_id or None,
            json.dumps(dict(details)) if details else None, now,
        ),
    )
    return {"payout_id": int(payout_id), "event_type": str(event_type),
            "stripe_event_id": stripe_event_id, "created_at": now}


def _set_status(conn, payout: Mapping[str, Any], new_status: str,
                *, failure_code: str = "", failure_message: str = "",
                stripe_payout_id: str = "", stripe_transfer_id: str = "") -> None:
    now = _utc_now_iso()
    sets = ["status = ?", "updated_at = ?"]
    params: list = [new_status, now]
    if failure_code:
        sets.append("failure_code = ?")
        params.append(failure_code[:100])
    if failure_message:
        sets.append("failure_message = ?")
        params.append(failure_message[:500])
    if stripe_payout_id and not payout.get("stripe_payout_id"):
        sets.append("stripe_payout_id = ?")
        params.append(stripe_payout_id)
    if stripe_transfer_id and not payout.get("stripe_transfer_id"):
        sets.append("stripe_transfer_id = ?")
        params.append(stripe_transfer_id)
    params.append(int(payout["id"]))
    conn.execute(
        "UPDATE seller_payout_requests SET " + ", ".join(sets) + " WHERE id = ?",
        tuple(params),
    )


def _open_conflict(payout: Mapping[str, Any], observed_status: str,
                   *, severity: str, summary: str, stripe_event_id: str = "",
                   extra: Optional[Mapping[str, Any]] = None) -> None:
    """Open a PAYOUT_STATE_CONFLICT incident; never let it break the applier."""
    details = {
        "payout_id": payout.get("id"),
        "payout_key": payout.get("payout_key"),
        "local_status": payout.get("status"),
        "observed_status": observed_status,
        "stripe_payout_id": payout.get("stripe_payout_id") or "",
    }
    if extra:
        details.update(dict(extra))
    try:
        incidents.open_incident(
            incidents.PAYOUT_STATE_CONFLICT,
            domain=INCIDENT_DOMAIN,
            severity=severity,
            summary=summary,
            details=details,
            related_object=f"seller_payout:{payout.get('id')}",
            stripe_ref=str(payout.get("stripe_payout_id") or stripe_event_id or ""),
            incident_key=(
                f"{incidents.PAYOUT_STATE_CONFLICT}:seller_payout:"
                f"{payout.get('id')}:{payout.get('status')}->{observed_status}"
            ),
        )
    except Exception:
        pass


def _reverse_to_payable(payout: Mapping[str, Any], *, reason: str,
                        idem_suffix: str) -> dict:
    """Return the pending funds to the seller's payable balance. Idempotent."""
    return ledger.post_entry(
        idempotency_key=f"{payout['payout_key']}:{idem_suffix}",
        actor="stripe" if idem_suffix.startswith("stripe") else "system",
        amount_cents=int(payout["amount_cents"]),
        currency=str(payout.get("currency") or "usd"),
        entry_type="payout_reversal",
        source=payout_pending_account(payout["user_id"]),
        destination=seller_payable_account(payout["user_id"]),
        reason=reason,
        related_object=f"seller_payout:{payout['id']}",
        provider_reference=str(payout.get("stripe_payout_id") or ""),
    )


# ---------------------------------------------------------------------------
# request / submit (record intent — NO Stripe calls here)
# ---------------------------------------------------------------------------

def request_payout(user_id: Any, amount_cents: int, *,
                   requested_by: str, payout_key: str,
                   account_status: Mapping[str, Any],
                   currency: str = "usd") -> dict:
    """Record a seller's payout intent and fence the funds. Never calls Stripe.

    ``account_status`` is a caller-supplied snapshot of the Connect account
    (shape: at least ``connected_account_id`` + ``payouts_enabled``), so this
    core stays network-free. Validation order: account, then amount, then
    balance. The ledger posting (``seller_payable`` → ``seller_payout_pending``)
    uses ``payout_key`` as its idempotency key, so a double-submitted request
    fences the money exactly once.

    Returns ``{"payout": row, "duplicate": bool, "ledger_transaction_id"}``.
    """
    payout_key = str(payout_key or "").strip()
    if not payout_key:
        raise PayoutError("payout_key is required", 400, "payout_key_required")
    if not requested_by or not str(requested_by).strip():
        raise PayoutError("requested_by is required", 400, "requested_by_required")
    if isinstance(amount_cents, bool) or not isinstance(amount_cents, int):
        raise PayoutError("amount_cents must be an integer number of cents",
                          400, "invalid_amount")
    if amount_cents <= 0:
        raise PayoutError("amount_cents must be a positive integer",
                          400, "invalid_amount")

    status_snapshot = dict(account_status or {})
    connected_account_id = str(
        status_snapshot.get("connected_account_id")
        or status_snapshot.get("provider_account_id") or ""
    ).strip()
    if not connected_account_id:
        raise PayoutError("No connected Stripe account on file.",
                          409, "no_connected_account")
    if not status_snapshot.get("payouts_enabled"):
        raise PayoutError("Stripe payouts are not enabled for this account.",
                          409, "payouts_disabled")

    cur_code = str(currency or "usd").lower()
    ensure_schema()
    ledger.ensure_schema()

    # Idempotent replay: same key -> the original request, no new money moved.
    existing = get_payout(payout_key=payout_key)
    if existing is not None:
        return {"payout": existing, "duplicate": True,
                "ledger_transaction_id": None}

    available = ledger.get_balance(seller_payable_account(user_id), cur_code)
    if amount_cents > available:
        raise PayoutError(
            f"Requested {amount_cents} cents but only {available} are available.",
            409, "insufficient_balance")

    # Fence the funds. The ledger's own overdraft guard re-checks the balance
    # under a row lock, so a concurrent double-spend is rejected there even if
    # the read above raced.
    posting = ledger.post_entry(
        idempotency_key=payout_key,
        actor=str(requested_by),
        amount_cents=amount_cents,
        currency=cur_code,
        entry_type="payout_request",
        source=seller_payable_account(user_id),
        destination=payout_pending_account(user_id),
        reason="seller payout request",
        related_object=f"seller_payout_key:{payout_key}",
    )

    now = _utc_now_iso()
    conn = db.connect()
    try:
        try:
            conn.execute(
                """
                INSERT INTO seller_payout_requests
                    (payout_key, user_id, connected_account_id, amount_cents,
                     currency, status, requested_by, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?)
                """,
                (payout_key, str(user_id), connected_account_id, amount_cents,
                 cur_code, str(requested_by), now, now),
            )
        except Exception as exc:  # noqa: BLE001
            if not _is_unique_violation(exc):
                raise
            # Lost a race with an identical request; the winner's row stands.
            row = get_payout(payout_key=payout_key, conn=conn)
            return {"payout": row, "duplicate": True,
                    "ledger_transaction_id": posting.get("transaction_id")}
        conn.commit()
        row = get_payout(payout_key=payout_key, conn=conn)
        _append_event(conn, row["id"], "payout_requested", details={
            "amount_cents": amount_cents,
            "currency": cur_code,
            "requested_by": str(requested_by),
            "connected_account_id": connected_account_id,
            "ledger_transaction_id": posting.get("transaction_id"),
        })
        conn.commit()
        return {"payout": row, "duplicate": False,
                "ledger_transaction_id": posting.get("transaction_id")}
    finally:
        conn.close()


def build_stripe_payout_args(payout: Mapping[str, Any]) -> dict:
    """The kwargs a networked caller passes to the Stripe API for this payout.

    The intended server-side call is ``stripe.Payout.create(**args["kwargs"],
    stripe_account=args["stripe_account"], idempotency_key=args["idempotency_key"])``
    — a payout from the connected account's Stripe balance to its bank. This
    function only shapes arguments; it never calls anything.
    """
    return {
        "method": "payout",
        "stripe_account": str(payout.get("connected_account_id") or ""),
        "idempotency_key": f"seller_payout:{payout.get('payout_key')}",
        "kwargs": {
            "amount": int(payout.get("amount_cents") or 0),
            "currency": str(payout.get("currency") or "usd"),
            "metadata": {
                "payout_key": str(payout.get("payout_key") or ""),
                "pulse_user_id": str(payout.get("user_id") or ""),
                "local_payout_id": str(payout.get("id") or ""),
            },
        },
    }


def mark_payout_submitted(payout_id: int, *, stripe_payout_id: str = "",
                          stripe_transfer_id: str = "",
                          actor: str = "system") -> dict:
    """pending → payout_created once the provider call succeeded server-side."""
    ensure_schema()
    conn = db.connect()
    try:
        payout = get_payout(payout_id=payout_id, conn=conn)
        if payout is None:
            raise PayoutError(f"payout {payout_id} not found", 404, "not_found")
        if payout["status"] == "payout_created":
            return payout  # idempotent replay
        if "payout_created" not in ALLOWED_TRANSITIONS.get(payout["status"], set()):
            raise PayoutError(
                f"cannot submit a payout in status {payout['status']!r}",
                409, "illegal_transition")
        _set_status(conn, payout, "payout_created",
                    stripe_payout_id=str(stripe_payout_id or ""),
                    stripe_transfer_id=str(stripe_transfer_id or ""))
        _append_event(conn, payout["id"], "payout_submitted", details={
            "stripe_payout_id": str(stripe_payout_id or ""),
            "stripe_transfer_id": str(stripe_transfer_id or ""),
            "actor": str(actor),
        })
        conn.commit()
        return get_payout(payout_id=payout_id, conn=conn)
    finally:
        conn.close()


def fail_payout(payout_id: int, *, failure_code: str, failure_message: str = "",
                actor: str = "system") -> dict:
    """Local terminal failure (e.g. the Stripe API call errored): mark failed
    and return the fenced funds to the seller's payable balance."""
    ensure_schema()
    conn = db.connect()
    try:
        payout = get_payout(payout_id=payout_id, conn=conn)
        if payout is None:
            raise PayoutError(f"payout {payout_id} not found", 404, "not_found")
        if payout["status"] == "failed":
            return payout  # idempotent replay
        if "failed" not in ALLOWED_TRANSITIONS.get(payout["status"], set()):
            raise PayoutError(
                f"cannot fail a payout in status {payout['status']!r}",
                409, "illegal_transition")
        _set_status(conn, payout, "failed",
                    failure_code=str(failure_code or "unknown"),
                    failure_message=str(failure_message or ""))
        _append_event(conn, payout["id"], "payout_failed_local", details={
            "failure_code": str(failure_code or "unknown"),
            "failure_message": str(failure_message or "")[:500],
            "actor": str(actor),
        })
        conn.commit()
    finally:
        conn.close()
    _reverse_to_payable(payout, reason=f"payout failed: {failure_code}",
                        idem_suffix="reversal")
    final = get_payout(payout_id=payout_id)
    _notify_rewards_engine(final)
    return final


def _notify_rewards_engine(payout) -> None:
    """Defensively tell the rewards engine a reward-funded payout ended.

    Cash rewards ride these payout rails with keys prefixed ``reward_payout:``.
    When such a payout reaches a terminal state the rewards domain projects it
    onto the reward row (paid → disbursed, failed/canceled → approved). The
    import is lazy and every failure is swallowed: a rewards-side bug must
    never break payout webhook processing, and reconciliation catches any
    missed notification.
    """
    try:
        if not isinstance(payout, Mapping):
            return
        payout_key = str(payout.get("payout_key") or "")
        if not payout_key.startswith("reward_payout:"):
            return
        if str(payout.get("status") or "") not in TERMINAL_STATUSES:
            return
        from services.business_os.rewards import engine as _rewards_engine
        _rewards_engine.sync_from_payout(dict(payout))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Stripe webhook appliers (pure functions over the event dict)
# ---------------------------------------------------------------------------

def apply_stripe_payout_event(event: Mapping[str, Any]) -> dict:
    """Project one Stripe ``payout.*`` webhook into the local lifecycle.

    Stripe is authoritative: whatever status Stripe reports is recorded, even
    when the local state machine calls the transition illegal — but every
    illegal transition opens a PAYOUT_STATE_CONFLICT incident. A payout id we
    have no row for opens ORPHAN_STRIPE_OBJECT and is otherwise ignored (the
    legacy Connect flow also creates payouts; those are not ours to project).

    Idempotent end to end: ledger keys are derived from the Stripe payout id,
    the event trail dedupes on (payout, stripe event id), and re-applying a
    status that is already recorded is a no-op.
    """
    if not isinstance(event, Mapping):
        return {"ignored": True, "reason": "malformed_event"}
    event_type = str(event.get("type") or "").strip()
    event_id = str(event.get("id") or "").strip()
    if event_type in IGNORED_PAYOUT_EVENTS:
        return {"ignored": True, "type": event_type}
    if event_type not in HANDLED_PAYOUT_EVENTS:
        return {"ignored": True, "type": event_type}

    data = event.get("data") if isinstance(event.get("data"), Mapping) else {}
    obj = data.get("object") if isinstance(data.get("object"), Mapping) else {}
    stripe_payout_id = str(obj.get("id") or "").strip()
    if not stripe_payout_id:
        return {"ignored": True, "type": event_type, "reason": "no_payout_id"}

    ensure_schema()
    ledger.ensure_schema()
    conn = db.connect()
    try:
        payout = _get_by_stripe_payout_id(conn, stripe_payout_id)
    finally:
        conn.close()

    if payout is None:
        try:
            incidents.open_incident(
                incidents.ORPHAN_STRIPE_OBJECT,
                domain=INCIDENT_DOMAIN,
                severity="warning",
                summary=(
                    f"Stripe payout {stripe_payout_id} ({event_type}) has no "
                    "matching seller_payout_requests row."
                ),
                details={"stripe_payout_id": stripe_payout_id,
                         "event_type": event_type,
                         "stripe_event_id": event_id,
                         "amount": obj.get("amount"),
                         "currency": obj.get("currency")},
                related_object=f"stripe_payout:{stripe_payout_id}",
                stripe_ref=stripe_payout_id,
                incident_key=(
                    f"{incidents.ORPHAN_STRIPE_OBJECT}:stripe_payout:"
                    f"{stripe_payout_id}"
                ),
            )
        except Exception:
            pass
        return {"ignored": True, "orphan": True,
                "stripe_payout_id": stripe_payout_id}

    # Desired local status, per Stripe.
    if event_type == "payout.created":
        desired = "payout_created"
    elif event_type == "payout.paid":
        desired = "paid"
    elif event_type == "payout.failed":
        desired = "failed"
    elif event_type == "payout.canceled":
        desired = "canceled"
    else:  # payout.updated — read the object's own status
        desired = _STRIPE_STATUS_MAP.get(
            str(obj.get("status") or "").strip().lower(), "")
        if not desired:
            return {"ignored": True, "type": event_type,
                    "reason": f"unknown stripe payout status {obj.get('status')!r}"}

    current = str(payout.get("status") or "pending")
    failure_code = str(obj.get("failure_code") or "")
    failure_message = str(obj.get("failure_message") or "")

    returned_after_paid = current == "paid" and desired in {"failed", "canceled"}
    if returned_after_paid:
        # Money that already settled came back. Record the Stripe truth as
        # `returned` and reverse the settlement so the seller's funds reappear.
        desired = "returned"

    conflict = (
        desired != current
        and desired not in ALLOWED_TRANSITIONS.get(current, set())
    )

    conn = db.connect()
    try:
        if desired != current:
            _set_status(conn, payout, desired,
                        failure_code=failure_code,
                        failure_message=failure_message)
        _append_event(conn, payout["id"], f"stripe:{event_type}",
                      stripe_event_id=event_id, details={
                          "stripe_payout_id": stripe_payout_id,
                          "stripe_status": str(obj.get("status") or ""),
                          "local_status_before": current,
                          "local_status_after": desired,
                          "failure_code": failure_code,
                          "failure_message": failure_message[:300],
                      })
        conn.commit()
    finally:
        conn.close()

    # --- money movement, keyed on the Stripe payout id (replay-safe) ---
    if desired == "paid":
        ledger.post_entry(
            idempotency_key=f"stripe:payout_paid:{stripe_payout_id}",
            actor="stripe",
            amount_cents=int(payout["amount_cents"]),
            currency=str(payout.get("currency") or "usd"),
            entry_type="payout_settled",
            source=payout_pending_account(payout["user_id"]),
            destination=PAYOUTS_SETTLED_ACCOUNT,
            reason=f"stripe:{event_type}",
            related_object=f"seller_payout:{payout['id']}",
            provider_reference=stripe_payout_id,
        )
    elif desired in {"failed", "canceled"}:
        _reverse_to_payable(payout, reason=f"stripe:{event_type}",
                            idem_suffix=f"stripe_reversal:{stripe_payout_id}")
        # Failed payouts are normal operations, not emergencies: record the
        # fact at severity info so an operator can see patterns without pages.
        try:
            incidents.open_incident(
                incidents.PAYOUT_STATE_CONFLICT,
                domain=INCIDENT_DOMAIN,
                severity="info",
                summary=(
                    f"Stripe payout {stripe_payout_id} ended {desired} "
                    f"({failure_code or 'no failure code'}); funds returned to "
                    "the seller's payable balance."
                ),
                details={"payout_id": payout["id"],
                         "stripe_payout_id": stripe_payout_id,
                         "failure_code": failure_code,
                         "failure_message": failure_message[:300]},
                related_object=f"seller_payout:{payout['id']}",
                stripe_ref=stripe_payout_id,
                incident_key=f"payout_terminal:{stripe_payout_id}:{desired}",
            )
        except Exception:
            pass
    elif desired == "returned":
        # Settlement reversal: the settled money came back onto the books.
        ledger.post_entry(
            idempotency_key=f"stripe:payout_returned:{stripe_payout_id}",
            actor="stripe",
            amount_cents=int(payout["amount_cents"]),
            currency=str(payout.get("currency") or "usd"),
            entry_type="payout_returned",
            source=PAYOUTS_SETTLED_ACCOUNT,
            destination=seller_payable_account(payout["user_id"]),
            reason=f"stripe:{event_type}",
            related_object=f"seller_payout:{payout['id']}",
            provider_reference=stripe_payout_id,
        )
        _open_conflict(payout, "returned", severity="critical",
                       summary=(
                           f"Stripe payout {stripe_payout_id} was returned AFTER "
                           "being paid; settled funds were reversed back to the "
                           "seller's payable balance."
                       ),
                       stripe_event_id=event_id,
                       extra={"failure_code": failure_code,
                              "failure_message": failure_message[:300]})

    if conflict and desired != "returned":
        _open_conflict(payout, desired, severity="warning",
                       summary=(
                           f"Stripe reported payout {stripe_payout_id} as "
                           f"{desired!r} but the local row was {current!r}; "
                           "recorded the Stripe truth."
                       ),
                       stripe_event_id=event_id)

    _notify_rewards_engine(get_payout(payout_id=payout["id"]))

    return {
        "applied": True,
        "payout_id": payout["id"],
        "stripe_payout_id": stripe_payout_id,
        "status_before": current,
        "status_after": desired,
        "conflict": bool(conflict or returned_after_paid),
        "duplicate": desired == current,
    }


def apply_stripe_transfer_event(event: Mapping[str, Any]) -> dict:
    """Append ``transfer.created`` / ``transfer.reversed`` to a payout's trail.

    Transfers only matter here when a payout row already carries the transfer
    id (i.e. the payout was funded via a platform→connected-account transfer).
    Anything else is somebody else's transfer and is ignored without incident.
    """
    if not isinstance(event, Mapping):
        return {"ignored": True, "reason": "malformed_event"}
    event_type = str(event.get("type") or "").strip()
    if event_type not in {"transfer.created", "transfer.reversed"}:
        return {"ignored": True, "type": event_type}
    data = event.get("data") if isinstance(event.get("data"), Mapping) else {}
    obj = data.get("object") if isinstance(data.get("object"), Mapping) else {}
    transfer_id = str(obj.get("id") or "").strip()
    if not transfer_id:
        return {"ignored": True, "type": event_type, "reason": "no_transfer_id"}

    ensure_schema()
    conn = db.connect()
    try:
        cur = conn.execute(
            "SELECT * FROM seller_payout_requests WHERE stripe_transfer_id = ? "
            "ORDER BY id DESC LIMIT 1",
            (transfer_id,),
        )
        payout = _row_to_dict(cur.fetchone())
        if payout is None:
            return {"ignored": True, "type": event_type,
                    "stripe_transfer_id": transfer_id}
        _append_event(conn, payout["id"], f"stripe:{event_type}",
                      stripe_event_id=str(event.get("id") or ""), details={
                          "stripe_transfer_id": transfer_id,
                          "amount": obj.get("amount"),
                          "currency": obj.get("currency"),
                          "reversed": bool(obj.get("reversed")),
                      })
        conn.commit()
        return {"applied": True, "payout_id": payout["id"],
                "stripe_transfer_id": transfer_id}
    finally:
        conn.close()
