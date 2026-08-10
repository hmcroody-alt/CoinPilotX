"""Rewards engine (Wave D — Pulse Credits + Cash Rewards).

Two reward kinds, one qualifying-event table, and a hard wall between them:

* **Pulse Credits** are an INTERNAL NON-CASH ledger. They are never
  withdrawable, never convert to cash, and never leave the platform. The only
  sanctioned exit is :func:`redeem_credits_to_ad_promo`, which burns credits
  into ad-wallet *promotional* credit — a non-cash → non-cash conversion.
  PulseSoc never invents spendable cash.
* **Cash Rewards** pay real money, and they do it exclusively through the
  Wave B seller payout rails: the ledger posting funds
  ``seller_payable:<user_id>`` from ``platform:rewards_expense`` and the
  disbursement itself is a ``seller_payouts.request_payout`` whose lifecycle
  Stripe drives via webhooks. This engine never grows a second payout path.

Non-negotiables inherited from the Wave A/B infrastructure:

* **One qualifying event = at most one reward.** ``reward_events.event_key``
  is UNIQUE at the DB level; a replay returns the original row with
  ``duplicate=True``. A replay that *disagrees* about the amount or kind opens
  a critical ``REWARD_DUPLICATE_ATTEMPT`` incident and the original stands.
* **Fraud state gates payment.** A reward held in ``review``/``blocked``
  grants nothing — no credits, no cash — until an admin clears it.
* **Append-only credit ledger.** ``pulse_credit_ledger`` rows are never
  updated or deleted; the balance can never go negative (enforced in code
  inside a write-locked transaction, plus a DB CHECK).
* **Integer units.** ``amount`` is credits for ``pulse_credits`` and cents
  for ``cash``. No floats anywhere.

Engine-portable via ``services.db``; does not import ``bot.py``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from services import db
from services.business_os.ledger import ledger
from services.business_os.payments import incidents

INCIDENT_DOMAIN = "rewards"

#: Non-cash → non-cash conversion rate: 1 Pulse Credit redeems into exactly
#: 1 cent of ad-wallet promotional credit. A constant, not a market.
CREDIT_TO_CENT = 1

#: The platform expense account cash rewards are funded from. ``platform:``
#: prefixed accounts may legitimately go negative in the ledger.
REWARDS_EXPENSE_ACCOUNT = "platform:rewards_expense"

REWARD_KINDS = {"pulse_credits", "cash"}
FRAUD_STATES = {"clear", "review", "blocked"}

#: Status machine. ``pulse_credits``: pending → granted | denied.
#: ``cash``: pending → approved → disbursing → disbursed, or denied.
#: ``disbursing`` → ``approved`` is the payout-bounced path: Wave B already
#: returned the fenced funds to ``seller_payable``; we only reflect status.
ALLOWED_TRANSITIONS = {
    "pending": {"granted", "approved", "denied"},
    "approved": {"disbursing", "denied"},
    "disbursing": {"disbursed", "approved"},
    "granted": set(),
    "denied": set(),
    "disbursed": set(),
}
STATUSES = set(ALLOWED_TRANSITIONS)

MAX_LIST_LIMIT = 100
DEFAULT_LIST_LIMIT = 25

#: Prefixes for the deterministic idempotency keys this engine mints.
CASH_LEDGER_KEY_PREFIX = "reward_cash:"
CASH_PAYOUT_KEY_PREFIX = "reward_payout:"
CREDIT_GRANT_KEY_PREFIX = "reward_grant:"
REDEEM_BURN_KEY_PREFIX = "redeem:"
REDEEM_PROMO_KEY_PREFIX = "reward_redeem:"


class RewardError(ValueError):
    """Rejected reward operation. ``status_code`` maps onto the HTTP layer."""

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


def _begin(conn) -> None:
    """BEGIN IMMEDIATE on SQLite so read-decide-write sequences serialize.

    Same pattern as ``ledger._begin`` / ``pulse_ad_payments._begin_immediate``:
    the balance check below reads the last ledger row and writes a new one
    computed from it; two writers interleaving that would both see the same
    balance and one of them would overdraw. On non-SQLite engines the
    connection's ordinary transaction semantics apply.
    """
    if db.ENGINE_NAME == "sqlite":
        try:
            conn.isolation_level = None
        except Exception:
            pass
        conn.execute("BEGIN IMMEDIATE")


def _commit(conn) -> None:
    if db.ENGINE_NAME == "sqlite":
        conn.execute("COMMIT")
    else:
        conn.commit()


def _rollback(conn) -> None:
    try:
        if db.ENGINE_NAME == "sqlite":
            conn.execute("ROLLBACK")
        else:
            conn.rollback()
    except Exception:
        pass


def ensure_schema(conn=None) -> None:
    """Create the reward tables if absent. Idempotent; safe at startup."""
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS reward_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_key TEXT NOT NULL UNIQUE,
                user_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                reward_kind TEXT NOT NULL,
                amount INTEGER NOT NULL CHECK (amount > 0),
                currency TEXT NOT NULL DEFAULT 'usd',
                status TEXT NOT NULL DEFAULT 'pending',
                fraud_state TEXT NOT NULL DEFAULT 'clear',
                source TEXT,
                details_json TEXT,
                payout_request_id INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pulse_credit_ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                delta INTEGER NOT NULL,
                balance_after INTEGER NOT NULL CHECK (balance_after >= 0),
                reason TEXT,
                reward_event_id INTEGER,
                redemption_ref TEXT,
                idempotency_key TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_reward_events_user "
            "ON reward_events (user_id, status)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_reward_events_payout "
            "ON reward_events (payout_request_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_pulse_credit_ledger_user "
            "ON pulse_credit_ledger (user_id, id)"
        )
        if owned:
            conn.commit()
    finally:
        if owned:
            conn.close()


def _shape(row: Optional[dict]) -> Optional[dict]:
    if row is None:
        return None
    out = dict(row)
    raw = out.pop("details_json", None)
    try:
        details = json.loads(raw) if raw else {}
        out["details"] = details if isinstance(details, dict) else {}
    except (TypeError, ValueError):
        out["details"] = {}
    return out


# ---------------------------------------------------------------------------
# reads
# ---------------------------------------------------------------------------

def get_reward(reward_id: Optional[int] = None,
               event_key: Optional[str] = None,
               conn=None) -> Optional[dict]:
    """Fetch one reward row by numeric id or by its event key."""
    if reward_id is None and not event_key:
        return None
    owned = conn is None
    if owned:
        ensure_schema()
        conn = db.connect()
    try:
        if reward_id is not None:
            cur = conn.execute(
                "SELECT * FROM reward_events WHERE id = ?", (int(reward_id),))
        else:
            cur = conn.execute(
                "SELECT * FROM reward_events WHERE event_key = ?",
                (str(event_key),))
        return _shape(_row_to_dict(cur.fetchone()))
    finally:
        if owned:
            conn.close()


def get_credit_balance(user_id: Any, conn=None) -> int:
    """The user's current Pulse Credit balance (last ``balance_after``)."""
    owned = conn is None
    if owned:
        ensure_schema()
        conn = db.connect()
    try:
        cur = conn.execute(
            "SELECT balance_after FROM pulse_credit_ledger "
            "WHERE user_id = ? ORDER BY id DESC LIMIT 1",
            (str(user_id),),
        )
        row = cur.fetchone()
        if row is None:
            return 0
        return int(row["balance_after"] if hasattr(row, "keys") else row[0])
    finally:
        if owned:
            conn.close()


def _clamp_limit(limit) -> int:
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = DEFAULT_LIST_LIMIT
    return max(1, min(limit, MAX_LIST_LIMIT))


def _cursor_id(before_id) -> Optional[int]:
    if before_id in (None, "", 0):
        return None
    try:
        return int(before_id)
    except (TypeError, ValueError):
        raise RewardError("before_id must be a numeric id")


def list_credit_ledger(user_id: Any, *, limit: int = DEFAULT_LIST_LIMIT,
                       before_id: Optional[Any] = None) -> dict:
    """One keyset-paginated page of a user's credit ledger, newest first."""
    limit = _clamp_limit(limit)
    cursor = _cursor_id(before_id)
    where = ["user_id = ?"]
    params: list = [str(user_id)]
    if cursor is not None:
        where.append("id < ?")
        params.append(cursor)
    params.append(limit + 1)
    ensure_schema()
    conn = db.connect()
    try:
        rows = [
            _row_to_dict(r)
            for r in conn.execute(
                "SELECT * FROM pulse_credit_ledger WHERE "
                + " AND ".join(where) + " ORDER BY id DESC LIMIT ?",
                tuple(params),
            ).fetchall()
        ]
    finally:
        conn.close()
    has_more = len(rows) > limit
    rows = rows[:limit]
    return {
        "entries": rows,
        "next_before_id": rows[-1]["id"] if (rows and has_more) else None,
        "has_more": has_more,
    }


def list_rewards(user_id: Optional[Any] = None, *,
                 status: Optional[str] = None,
                 fraud_state: Optional[str] = None,
                 limit: int = DEFAULT_LIST_LIMIT,
                 before_id: Optional[Any] = None) -> dict:
    """One keyset-paginated page of reward rows, newest first.

    Pass ``user_id`` for the member-facing view; omit it (admin) to see all.
    """
    if status is not None and status not in STATUSES:
        raise RewardError(f"unknown reward status {status!r}")
    if fraud_state is not None and fraud_state not in FRAUD_STATES:
        raise RewardError(f"unknown fraud_state {fraud_state!r}")
    limit = _clamp_limit(limit)
    cursor = _cursor_id(before_id)
    where = []
    params: list = []
    if user_id not in (None, ""):
        where.append("user_id = ?")
        params.append(str(user_id))
    if status:
        where.append("status = ?")
        params.append(status)
    if fraud_state:
        where.append("fraud_state = ?")
        params.append(fraud_state)
    if cursor is not None:
        where.append("id < ?")
        params.append(cursor)
    params.append(limit + 1)
    sql = "SELECT * FROM reward_events"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ?"
    ensure_schema()
    conn = db.connect()
    try:
        rows = [_shape(_row_to_dict(r))
                for r in conn.execute(sql, tuple(params)).fetchall()]
    finally:
        conn.close()
    has_more = len(rows) > limit
    rows = rows[:limit]
    return {
        "rewards": rows,
        "next_before_id": rows[-1]["id"] if (rows and has_more) else None,
        "has_more": has_more,
    }


# ---------------------------------------------------------------------------
# credit ledger writers (in-transaction primitives)
# ---------------------------------------------------------------------------

def _append_credit_row(conn, *, user_id: str, delta: int, reason: str,
                       idempotency_key: str,
                       reward_event_id: Optional[int] = None,
                       redemption_ref: str = "") -> dict:
    """Append one credit ledger row inside the caller's open transaction.

    The caller MUST hold the write lock (``_begin``) — the balance read below
    is only correct while nothing else can append. Raises :class:`RewardError`
    on an overdraw; raises the driver's unique violation on a replayed key
    (the caller decides whether that is a duplicate or a bug).
    """
    cur = conn.execute(
        "SELECT balance_after FROM pulse_credit_ledger "
        "WHERE user_id = ? ORDER BY id DESC LIMIT 1",
        (user_id,),
    )
    row = cur.fetchone()
    balance = int(row["balance_after"] if row is not None and hasattr(row, "keys")
                  else (row[0] if row is not None else 0))
    new_balance = balance + int(delta)
    if new_balance < 0:
        raise RewardError(
            f"insufficient pulse credits: balance={balance} delta={delta}",
            409, "insufficient_credits")
    now = _utc_now_iso()
    conn.execute(
        "INSERT INTO pulse_credit_ledger "
        "(user_id, delta, balance_after, reason, reward_event_id, "
        " redemption_ref, idempotency_key, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (user_id, int(delta), new_balance, reason or None,
         int(reward_event_id) if reward_event_id else None,
         redemption_ref or None, idempotency_key, now),
    )
    return {"user_id": user_id, "delta": int(delta),
            "balance_after": new_balance, "idempotency_key": idempotency_key,
            "created_at": now}


def _set_status_in_tx(conn, reward: Mapping[str, Any], new_status: str,
                      *, payout_request_id: Optional[int] = None,
                      fraud_state: str = "") -> None:
    now = _utc_now_iso()
    sets = ["status = ?", "updated_at = ?"]
    params: list = [new_status, now]
    if payout_request_id is not None:
        sets.append("payout_request_id = ?")
        params.append(int(payout_request_id))
    if fraud_state:
        sets.append("fraud_state = ?")
        params.append(fraud_state)
    params.append(int(reward["id"]))
    conn.execute(
        "UPDATE reward_events SET " + ", ".join(sets) + " WHERE id = ?",
        tuple(params),
    )


def _assert_transition(reward: Mapping[str, Any], new_status: str) -> None:
    current = str(reward.get("status") or "pending")
    if new_status == current:
        return
    if new_status not in ALLOWED_TRANSITIONS.get(current, set()):
        raise RewardError(
            f"illegal reward transition {current!r} -> {new_status!r}",
            409, "illegal_transition")


# ---------------------------------------------------------------------------
# grant
# ---------------------------------------------------------------------------

def grant_reward(event_key: str, user_id: Any, event_type: str,
                 reward_kind: str, amount: int, source: str,
                 details: Optional[Mapping[str, Any]] = None,
                 fraud_state: str = "clear",
                 currency: str = "usd") -> dict:
    """Record one qualifying event's reward, idempotently.

    * Replay with the same ``event_key`` → the original row, ``duplicate=True``,
      NO second grant of anything.
    * Replay with the same key but a different amount/kind → a critical
      ``REWARD_DUPLICATE_ATTEMPT`` incident; the original stands untouched
      (returned with ``duplicate=True`` and ``conflict=True``).
    * ``pulse_credits`` + fraud ``clear`` → granted immediately, with the
      credit ledger row written in the same transaction as the status flip.
    * ``cash`` → always lands ``pending``; money never moves at grant time.
    * fraud ``review``/``blocked`` → held ``pending``; nothing is granted.

    Returns ``{"reward": row, "duplicate": bool}`` (+ ``conflict`` on the
    disagreeing-replay path).
    """
    event_key = str(event_key or "").strip()
    if not event_key:
        raise RewardError("event_key is required", 400, "event_key_required")
    if not str(user_id or "").strip():
        raise RewardError("user_id is required", 400, "user_id_required")
    event_type = str(event_type or "").strip()
    if not event_type:
        raise RewardError("event_type is required", 400, "event_type_required")
    if reward_kind not in REWARD_KINDS:
        raise RewardError(f"unknown reward_kind {reward_kind!r}",
                          400, "invalid_reward_kind")
    if isinstance(amount, bool) or not isinstance(amount, int):
        raise RewardError("amount must be a positive integer",
                          400, "invalid_amount")
    if amount <= 0:
        raise RewardError("amount must be a positive integer",
                          400, "invalid_amount")
    if fraud_state not in FRAUD_STATES:
        raise RewardError(f"unknown fraud_state {fraud_state!r}",
                          400, "invalid_fraud_state")
    if not str(source or "").strip():
        raise RewardError("source is required", 400, "source_required")
    cur_code = str(currency or "usd").lower()
    uid = str(user_id)
    details_json = json.dumps(dict(details)) if details else None
    now = _utc_now_iso()

    ensure_schema()
    conn = db.connect()
    try:
        _begin(conn)
        try:
            conn.execute(
                """
                INSERT INTO reward_events
                    (event_key, user_id, event_type, reward_kind, amount,
                     currency, status, fraud_state, source, details_json,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?)
                """,
                (event_key, uid, event_type, reward_kind, amount, cur_code,
                 fraud_state, str(source), details_json, now, now),
            )
        except Exception as exc:  # noqa: BLE001
            _rollback(conn)
            if not _is_unique_violation(exc):
                raise
            existing = get_reward(event_key=event_key, conn=conn)
            if existing is None:
                # Unique hit but the row is not visible yet. Never grant twice.
                return {"reward": {"event_key": event_key}, "duplicate": True}
            same = (
                int(existing.get("amount") or 0) == amount
                and str(existing.get("reward_kind") or "") == reward_kind
            )
            if not same:
                try:
                    incidents.open_incident(
                        incidents.REWARD_DUPLICATE_ATTEMPT,
                        domain=INCIDENT_DOMAIN,
                        severity="critical",
                        summary=(
                            f"Replayed reward event {event_key!r} disagrees "
                            f"with the recorded grant: recorded "
                            f"{existing.get('reward_kind')}/{existing.get('amount')}, "
                            f"replay says {reward_kind}/{amount}."
                        ),
                        details={
                            "event_key": event_key,
                            "recorded_kind": existing.get("reward_kind"),
                            "recorded_amount": int(existing.get("amount") or 0),
                            "replayed_kind": reward_kind,
                            "replayed_amount": amount,
                            "user_id": uid,
                            "source": str(source),
                        },
                        related_object=f"reward_event:{existing.get('id')}",
                        incident_key=(
                            f"{incidents.REWARD_DUPLICATE_ATTEMPT}:{event_key}:"
                            f"{reward_kind}:{amount}"
                        ),
                    )
                except Exception:
                    pass
            return {"reward": existing, "duplicate": True,
                    "conflict": not same}

        granted_now = reward_kind == "pulse_credits" and fraud_state == "clear"
        if granted_now:
            cur = conn.execute(
                "SELECT * FROM reward_events WHERE event_key = ?", (event_key,))
            row = _row_to_dict(cur.fetchone())
            _append_credit_row(
                conn,
                user_id=uid,
                delta=amount,
                reason=f"reward:{event_type}",
                idempotency_key=f"{CREDIT_GRANT_KEY_PREFIX}{event_key}",
                reward_event_id=int(row["id"]),
            )
            _set_status_in_tx(conn, row, "granted")
        _commit(conn)
        return {"reward": get_reward(event_key=event_key, conn=conn),
                "duplicate": False}
    except RewardError:
        _rollback(conn)
        raise
    except Exception:
        _rollback(conn)
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# fraud / approval
# ---------------------------------------------------------------------------

def set_fraud_state(reward_id: int, fraud_state: str, actor: str,
                    note: str = "") -> dict:
    """Admin fraud-state change; the ONLY thing that releases a held reward.

    * ``clear`` on a held (pending) ``pulse_credits`` reward → grants it now,
      credit row + status flip in one transaction.
    * ``blocked`` on a pending or approved reward → denies it (payment gate).
    * ``review`` → holds; nothing granted, nothing denied.
    Rewards already in a terminal or disbursing state only get the fraud flag
    recorded — money that moved is the payout engine's story, not ours.
    """
    if fraud_state not in FRAUD_STATES:
        raise RewardError(f"unknown fraud_state {fraud_state!r}",
                          400, "invalid_fraud_state")
    if not str(actor or "").strip():
        raise RewardError("actor is required", 400, "actor_required")
    ensure_schema()
    conn = db.connect()
    try:
        _begin(conn)
        cur = conn.execute(
            "SELECT * FROM reward_events WHERE id = ?", (int(reward_id),))
        reward = _row_to_dict(cur.fetchone())
        if reward is None:
            _rollback(conn)
            raise RewardError(f"reward {reward_id} not found", 404, "not_found")

        status = str(reward.get("status") or "pending")
        new_status = status
        if fraud_state == "blocked" and status in {"pending", "approved"}:
            new_status = "denied"
        elif (fraud_state == "clear" and status == "pending"
              and str(reward.get("reward_kind")) == "pulse_credits"):
            new_status = "granted"

        # audit trail lives in details_json (append-only list)
        try:
            details = json.loads(reward.get("details_json") or "{}")
            if not isinstance(details, dict):
                details = {}
        except (TypeError, ValueError):
            details = {}
        history = details.get("fraud_history")
        if not isinstance(history, list):
            history = []
        history.append({
            "fraud_state": fraud_state, "actor": str(actor),
            "note": str(note or "")[:500], "at": _utc_now_iso(),
            "status_before": status, "status_after": new_status,
        })
        details["fraud_history"] = history
        conn.execute(
            "UPDATE reward_events SET details_json = ? WHERE id = ?",
            (json.dumps(details), int(reward["id"])),
        )

        if new_status == "granted":
            _append_credit_row(
                conn,
                user_id=str(reward["user_id"]),
                delta=int(reward["amount"]),
                reason=f"reward:{reward.get('event_type')}",
                idempotency_key=(
                    f"{CREDIT_GRANT_KEY_PREFIX}{reward['event_key']}"),
                reward_event_id=int(reward["id"]),
            )
        _set_status_in_tx(conn, reward, new_status, fraud_state=fraud_state)
        _commit(conn)
        return get_reward(reward_id=int(reward_id), conn=conn)
    except RewardError:
        _rollback(conn)
        raise
    except Exception as exc:  # noqa: BLE001
        _rollback(conn)
        if _is_unique_violation(exc):
            # The credit grant already happened on a previous clear — the
            # ledger's UNIQUE key refused a second one. Reflect status only.
            conn2 = db.connect()
            try:
                conn2.execute(
                    "UPDATE reward_events SET status = 'granted', "
                    "fraud_state = ?, updated_at = ? WHERE id = ?",
                    (fraud_state, _utc_now_iso(), int(reward_id)),
                )
                conn2.commit()
            finally:
                conn2.close()
            return get_reward(reward_id=int(reward_id))
        raise
    finally:
        conn.close()


def approve_cash_reward(reward_id: int, actor: str) -> dict:
    """pending → approved for a cash reward whose fraud state is clear."""
    if not str(actor or "").strip():
        raise RewardError("actor is required", 400, "actor_required")
    ensure_schema()
    conn = db.connect()
    try:
        _begin(conn)
        cur = conn.execute(
            "SELECT * FROM reward_events WHERE id = ?", (int(reward_id),))
        reward = _row_to_dict(cur.fetchone())
        if reward is None:
            _rollback(conn)
            raise RewardError(f"reward {reward_id} not found", 404, "not_found")
        if str(reward.get("reward_kind")) != "cash":
            _rollback(conn)
            raise RewardError("only cash rewards are approved for disbursement",
                              400, "not_cash")
        if str(reward.get("status")) == "approved":
            _rollback(conn)
            return get_reward(reward_id=int(reward_id), conn=conn)
        if str(reward.get("fraud_state")) != "clear":
            _rollback(conn)
            raise RewardError(
                "fraud state must be clear before a cash reward is approved",
                409, "fraud_gate")
        _assert_transition(reward, "approved")
        _set_status_in_tx(conn, reward, "approved")
        _commit(conn)
        return get_reward(reward_id=int(reward_id), conn=conn)
    except RewardError:
        _rollback(conn)
        raise
    except Exception:
        _rollback(conn)
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# cash disbursement (via the Wave B seller payout rails)
# ---------------------------------------------------------------------------

def disburse_cash_reward(reward_id: int, actor: str) -> dict:
    """Move an approved cash reward onto the seller payout rails.

    LAZY ONBOARDING: if the user has no Connect account, or payouts are not
    enabled on it yet, this returns ``{"ok": False, "needs_onboarding": True}``
    and the reward *stays approved* — no failure, no denial, the money waits.

    When the account is ready: one idempotent ledger posting funds
    ``seller_payable:<uid>`` from ``platform:rewards_expense``
    (key ``reward_cash:<event_key>``), then one idempotent
    ``seller_payouts.request_payout`` fences it (key
    ``reward_payout:<event_key>``). Terminal payout outcomes flow back through
    :func:`sync_from_payout` — this engine never re-handles the money on a
    failed payout, because Wave B's reversal already returned it to
    ``seller_payable``.
    """
    from services.business_os.payments import connect_accounts, seller_payouts

    if not str(actor or "").strip():
        raise RewardError("actor is required", 400, "actor_required")
    ensure_schema()
    reward = get_reward(reward_id=int(reward_id))
    if reward is None:
        raise RewardError(f"reward {reward_id} not found", 404, "not_found")
    if str(reward.get("reward_kind")) != "cash":
        raise RewardError("only cash rewards can be disbursed", 400, "not_cash")
    if str(reward.get("fraud_state")) != "clear":
        raise RewardError(
            "fraud state must be clear before a cash reward is disbursed",
            409, "fraud_gate")
    status = str(reward.get("status"))
    if status in {"disbursing", "disbursed"}:
        # idempotent replay of an already-moving reward
        payout = None
        if reward.get("payout_request_id"):
            payout = seller_payouts.get_payout(
                payout_id=int(reward["payout_request_id"]))
        return {"ok": True, "duplicate": True, "needs_onboarding": False,
                "reward": reward, "payout": payout}
    if status != "approved":
        raise RewardError(
            f"cannot disburse a cash reward in status {status!r}",
            409, "illegal_transition")

    state = connect_accounts.get_state(reward["user_id"])
    connected_account_id = str((state or {}).get("connected_account_id") or "")
    payouts_enabled = bool((state or {}).get("payouts_enabled"))
    if not connected_account_id or not payouts_enabled:
        return {
            "ok": False,
            "needs_onboarding": True,
            "reward": reward,
            "connect_state": state,
            "reason": ("no_connected_account" if not connected_account_id
                       else "payouts_disabled"),
        }

    event_key = str(reward["event_key"])
    amount = int(reward["amount"])
    cur_code = str(reward.get("currency") or "usd").lower()

    # 1) Fund the payable balance from the platform expense account.
    #    Idempotent on the event key — a retried disbursement funds once.
    posting = ledger.post_entry(
        idempotency_key=f"{CASH_LEDGER_KEY_PREFIX}{event_key}",
        actor=str(actor),
        amount_cents=amount,
        currency=cur_code,
        entry_type="reward_cash_grant",
        source=REWARDS_EXPENSE_ACCOUNT,
        destination=seller_payouts.seller_payable_account(reward["user_id"]),
        reason=f"cash reward: {reward.get('event_type')}",
        related_object=f"reward_event:{reward['id']}",
    )

    # 2) Fence it into a payout request. Idempotent on the payout key.
    result = seller_payouts.request_payout(
        reward["user_id"], amount,
        requested_by=str(actor),
        payout_key=f"{CASH_PAYOUT_KEY_PREFIX}{event_key}",
        account_status=state or {},
        currency=cur_code,
    )
    payout = result["payout"]

    # 3) Reflect the movement on the reward row.
    conn = db.connect()
    try:
        _begin(conn)
        cur = conn.execute(
            "SELECT * FROM reward_events WHERE id = ?", (int(reward_id),))
        fresh = _row_to_dict(cur.fetchone())
        if fresh is not None and str(fresh.get("status")) == "approved":
            _set_status_in_tx(conn, fresh, "disbursing",
                              payout_request_id=int(payout["id"]))
        _commit(conn)
    except Exception:
        _rollback(conn)
        raise
    finally:
        conn.close()

    return {
        "ok": True,
        "duplicate": bool(result.get("duplicate")),
        "needs_onboarding": False,
        "reward": get_reward(reward_id=int(reward_id)),
        "payout": payout,
        "ledger_transaction_id": posting.get("transaction_id"),
    }


def sync_from_payout(payout: Mapping[str, Any]) -> dict:
    """Reflect a reward payout's terminal state onto its reward row.

    Called by Wave B's payout engine (defensively, lazy-imported) whenever a
    payout whose key starts with ``reward_payout:`` reaches a terminal state.
    Status projection ONLY — the money was already handled by Wave B:
    ``paid`` settled it, ``failed``/``canceled`` reversed the fenced funds
    back to ``seller_payable``. This function must never move money.
    """
    if not isinstance(payout, Mapping):
        return {"ignored": True, "reason": "malformed_payout"}
    payout_key = str(payout.get("payout_key") or "")
    if not payout_key.startswith(CASH_PAYOUT_KEY_PREFIX):
        return {"ignored": True, "reason": "not_a_reward_payout"}
    payout_status = str(payout.get("status") or "")
    if payout_status == "paid":
        desired = "disbursed"
    elif payout_status in {"failed", "canceled", "returned"}:
        # Wave B already returned (or is tracking) the funds; the reward goes
        # back to approved so it can be disbursed again once resolved.
        desired = "approved"
    else:
        return {"ignored": True, "reason": f"non_terminal:{payout_status}"}

    event_key = payout_key[len(CASH_PAYOUT_KEY_PREFIX):]
    ensure_schema()
    conn = db.connect()
    try:
        _begin(conn)
        cur = conn.execute(
            "SELECT * FROM reward_events WHERE event_key = ?", (event_key,))
        reward = _row_to_dict(cur.fetchone())
        if reward is None:
            _rollback(conn)
            return {"ignored": True, "reason": "no_reward_for_key",
                    "event_key": event_key}
        current = str(reward.get("status") or "")
        if current == desired:
            _rollback(conn)
            return {"applied": False, "duplicate": True,
                    "reward_id": reward["id"], "status": current}
        if desired not in ALLOWED_TRANSITIONS.get(current, set()):
            # e.g. a payout returned after the reward was marked disbursed.
            # Record nothing here; Wave B already opened the state-conflict
            # incident for the money side.
            _rollback(conn)
            return {"applied": False, "ignored": True,
                    "reason": f"illegal_transition:{current}->{desired}",
                    "reward_id": reward["id"]}
        _set_status_in_tx(conn, reward, desired,
                          payout_request_id=int(payout.get("id") or 0) or None)
        _commit(conn)
        return {"applied": True, "reward_id": reward["id"],
                "status_before": current, "status_after": desired}
    except Exception:
        _rollback(conn)
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# redemption: Pulse Credits -> ad promotional credit (non-cash -> non-cash)
# ---------------------------------------------------------------------------

def redeem_credits_to_ad_promo(user_id: Any, credits_amount: int,
                               account_id: int, redemption_key: str) -> dict:
    """Burn Pulse Credits into ad-wallet promotional credit. Never cash.

    Ownership is enforced the way the ad wallet does it: the ad account must
    belong to ``user_id``. The burn (negative credit ledger row) and the
    wallet promo credit are written on ONE connection in ONE transaction, with
    the promo credit first and the burn last — if either fails, both roll
    back, so a failed wallet credit can never leave credits burned.

    Idempotent on ``redemption_key``: a replay returns the original with
    ``duplicate=True`` and moves nothing (the wallet side is also keyed).
    Conversion: 1 credit == ``CREDIT_TO_CENT`` cents of *promotional* credit.
    """
    redemption_key = str(redemption_key or "").strip()
    if not redemption_key:
        raise RewardError("redemption_key is required", 400,
                          "redemption_key_required")
    if isinstance(credits_amount, bool) or not isinstance(credits_amount, int):
        raise RewardError("credits_amount must be a positive integer",
                          400, "invalid_amount")
    if credits_amount <= 0:
        raise RewardError("credits_amount must be a positive integer",
                          400, "invalid_amount")
    try:
        account_id = int(account_id)
    except (TypeError, ValueError):
        raise RewardError("account_id must be a numeric ad account id",
                          400, "invalid_account")
    if account_id <= 0:
        raise RewardError("account_id must be a numeric ad account id",
                          400, "invalid_account")

    from services import pulse_ad_payments

    uid = str(user_id)
    burn_key = f"{REDEEM_BURN_KEY_PREFIX}{redemption_key}"
    promo_key = f"{REDEEM_PROMO_KEY_PREFIX}{redemption_key}"
    amount_cents = credits_amount * CREDIT_TO_CENT

    ensure_schema()
    conn = db.connect()
    try:
        # Ownership gate, before any lock is taken.
        try:
            cur = conn.execute(
                "SELECT id FROM pulse_ad_accounts "
                "WHERE id = ? AND owner_user_id = ?",
                (account_id, int(uid) if uid.isdigit() else uid),
            )
            owned = cur.fetchone()
        except Exception:
            raise RewardError("Ad promo redemption is unavailable.", 503,
                              "ad_tables_missing")
        if owned is None:
            raise RewardError("Ad account not found.", 404, "account_not_found")

        _begin(conn)
        # Idempotent replay: the burn row is the record of the redemption.
        cur = conn.execute(
            "SELECT * FROM pulse_credit_ledger WHERE idempotency_key = ?",
            (burn_key,),
        )
        existing = _row_to_dict(cur.fetchone())
        if existing is not None:
            _rollback(conn)
            return {"ok": True, "duplicate": True, "redemption": existing,
                    "credits_burned": abs(int(existing.get("delta") or 0)),
                    "promo_credit_cents":
                        abs(int(existing.get("delta") or 0)) * CREDIT_TO_CENT,
                    "credit_balance": get_credit_balance(uid, conn=conn)}

        # Promo credit first, burn LAST — a wallet failure rolls both back;
        # a burn failure (overdraw) rolls the wallet credit back too.
        wallet_tx = pulse_ad_payments.grant_promotional_credits(
            conn, account_id, amount_cents,
            reason=f"Pulse Credit redemption ({credits_amount} credits)",
            idempotency_key=promo_key,
            commit=False,
        )
        burn = _append_credit_row(
            conn,
            user_id=uid,
            delta=-credits_amount,
            reason="redeem:ad_promo",
            idempotency_key=burn_key,
            redemption_ref=f"ad_promo:{account_id}",
        )
        _commit(conn)
        return {
            "ok": True,
            "duplicate": False,
            "redemption": burn,
            "credits_burned": credits_amount,
            "promo_credit_cents": amount_cents,
            "wallet_transaction_id": int((wallet_tx.get("transaction") or {}).get("id") or 0),
            "credit_balance": int(burn["balance_after"]),
        }
    except RewardError:
        _rollback(conn)
        raise
    except Exception:
        _rollback(conn)
        raise
    finally:
        conn.close()
