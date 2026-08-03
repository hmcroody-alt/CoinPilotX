"""Canonical financial ledger — the load-bearing Stage 1 foundation.

Design goals (from the Stage 0 inventory's identified defects):

* **Immutable.** Entries are append-only. No UPDATE/DELETE of posted rows.
* **Integer cents.** No floats anywhere in money math.
* **Double-entry.** Every posting moves ``amount_cents`` from a ``source``
  account to a ``destination`` account (a debit + a credit) so the books
  always balance.
* **Idempotent.** A UNIQUE ``idempotency_key`` at the DB level makes a repeated
  ``post_entry`` with the same key a no-op that returns the original
  transaction. This is enforced by the database, not by caller discipline —
  fixing the gap the inventory found in ``creator_ledger_entries``.
* **Atomic.** The transaction guard row, both entries, and both balance
  updates are written inside a single DB transaction. Either all land or none
  do — fixing the non-atomic ledger-write defect in the legacy code.

The module is engine-portable (SQLite in dev, PostgreSQL in prod) via
``services.db``. It deliberately does not import ``bot.py`` so it can be unit
tested in isolation.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from services import db


class LedgerError(ValueError):
    """Raised when a ledger write is rejected before any state changes."""


# Accounts that may legitimately hold a negative balance (they are funding
# sources / liability accounts). Everything else is asserted non-negative unless
# the caller explicitly allows an overdraft.
_ALLOW_NEGATIVE_PREFIXES = ("platform:", "external:", "stripe:", "liability:")

_VALID_STATUS = {"posted", "pending", "void"}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _is_unique_violation(exc: Exception) -> bool:
    """Engine-agnostic detection of a UNIQUE / primary-key violation."""
    name = exc.__class__.__name__.lower()
    msg = str(exc).lower()
    if "integrityerror" in name or "uniqueviolation" in name:
        return True
    return "unique" in msg or "duplicate key" in msg


def _begin(conn) -> None:
    if db.ENGINE_NAME == "sqlite":
        try:
            conn.isolation_level = None
        except Exception:
            pass
        # IMMEDIATE takes the write lock now, so concurrent posters serialize
        # instead of racing into a "database is locked" mid-transaction.
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
    """Create ledger tables if absent. Idempotent; safe to call at startup."""
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ledger_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                idempotency_key TEXT NOT NULL UNIQUE,
                transaction_id TEXT NOT NULL UNIQUE,
                actor TEXT,
                entry_type TEXT NOT NULL,
                amount_cents INTEGER NOT NULL CHECK (amount_cents >= 0),
                currency TEXT NOT NULL,
                source_account TEXT NOT NULL,
                destination_account TEXT NOT NULL,
                reason TEXT,
                related_object TEXT,
                provider_reference TEXT,
                status TEXT NOT NULL DEFAULT 'posted',
                metadata_json TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ledger_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                transaction_id TEXT NOT NULL,
                account TEXT NOT NULL,
                direction TEXT NOT NULL CHECK (direction IN ('debit','credit')),
                amount_cents INTEGER NOT NULL CHECK (amount_cents >= 0),
                signed_amount_cents INTEGER NOT NULL,
                currency TEXT NOT NULL,
                entry_type TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ledger_balances (
                account TEXT NOT NULL,
                currency TEXT NOT NULL,
                balance_cents INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (account, currency)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ledger_entries_account "
            "ON ledger_entries (account, currency)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ledger_entries_txn "
            "ON ledger_entries (transaction_id)"
        )
        if owned:
            conn.commit()
    finally:
        if owned:
            conn.close()


def _row_to_dict(row) -> Optional[dict]:
    if row is None:
        return None
    try:
        return dict(row)
    except Exception:
        # sqlite3.Row supports keys(); fall back for other row types.
        return {k: row[k] for k in row.keys()}


def get_transaction(idempotency_key: str, conn=None) -> Optional[dict]:
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        cur = conn.execute(
            "SELECT * FROM ledger_transactions WHERE idempotency_key = ?",
            (idempotency_key,),
        )
        return _row_to_dict(cur.fetchone())
    finally:
        if owned:
            conn.close()


def _recompute_balance_in_tx(conn, account: str, currency: str) -> int:
    cur = conn.execute(
        "SELECT COALESCE(SUM(signed_amount_cents), 0) AS bal "
        "FROM ledger_entries WHERE account = ? AND currency = ?",
        (account, currency),
    )
    row = cur.fetchone()
    balance = int(row["bal"] if hasattr(row, "keys") else row[0])
    now = _utc_now_iso()
    # Portable UPSERT: try UPDATE, INSERT if nothing was updated.
    cur = conn.execute(
        "UPDATE ledger_balances SET balance_cents = ?, updated_at = ? "
        "WHERE account = ? AND currency = ?",
        (balance, now, account, currency),
    )
    if getattr(cur, "rowcount", 0) == 0:
        conn.execute(
            "INSERT INTO ledger_balances (account, currency, balance_cents, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (account, currency, balance, now),
        )
    return balance


def recompute_balance(account: str, currency: str = "usd") -> int:
    """Re-derive and persist an account balance from its entries (audit tool)."""
    conn = db.connect()
    try:
        _begin(conn)
        balance = _recompute_balance_in_tx(conn, account, str(currency).lower())
        _commit(conn)
        return balance
    except Exception:
        _rollback(conn)
        raise
    finally:
        conn.close()


def get_balance(account: str, currency: str = "usd", conn=None) -> int:
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        cur = conn.execute(
            "SELECT balance_cents FROM ledger_balances WHERE account = ? AND currency = ?",
            (account, str(currency).lower()),
        )
        row = cur.fetchone()
        if row is None:
            return 0
        return int(row["balance_cents"] if hasattr(row, "keys") else row[0])
    finally:
        if owned:
            conn.close()


MAX_LIST_LIMIT = 100
DEFAULT_LIST_LIMIT = 25


def list_account_transactions(
    accounts: Any,
    currency: str = "usd",
    *,
    limit: int = DEFAULT_LIST_LIMIT,
    before_cursor: Optional[str] = None,
    entry_types: Optional[Any] = None,
    conn=None,
) -> dict:
    """Read one page of an account set's transaction history, newest first.

    This is the read side of the ledger and the only sanctioned way to render a
    money activity feed. It exists so that no caller has to compose a feed out of
    orders + payouts + ad charges and hope the union is right: the union happens
    here, in one SQL statement, over the canonical entries table.

    ``accounts`` may be a single account string or an iterable of them. Passing
    several is the normal case — a seller's activity is their payable account
    *plus* every per-order escrow account, which are different accounts by
    design. Because the query reads ``ledger_entries`` rather than
    ``ledger_transactions``, a transaction that touches two of the requested
    accounts (escrow ─▶ payable at settlement) correctly yields a row for each
    side, each with its own direction.

    ``signed_amount_cents`` is the amount **from the requested account's point of
    view**: positive when money entered it, negative when money left it. Callers
    render that sign directly. They must not infer a sign from ``entry_type``,
    because the same type means opposite things to the two sides of a posting.

    Pagination is a keyset cursor on ``ledger_entries.id``, which is a monotonic
    unique primary key. Timestamps are not used as a cursor: two postings inside
    the same millisecond would make a timestamp cursor silently skip rows.

    Returns ``{"accounts", "currency", "transactions", "next_cursor",
    "has_more"}``. ``next_cursor`` is None when the page is the last one.
    """
    if isinstance(accounts, str):
        account_list = [accounts]
    else:
        account_list = [str(a) for a in (accounts or []) if str(a or "").strip()]
    account_list = list(dict.fromkeys(account_list))  # de-dupe, keep order
    if not account_list:
        return {"accounts": [], "currency": str(currency or "usd").lower(),
                "transactions": [], "next_cursor": None, "has_more": False}

    cur_code = str(currency or "usd").lower()

    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = DEFAULT_LIST_LIMIT
    limit = max(1, min(limit, MAX_LIST_LIMIT))

    types: list = []
    if entry_types:
        if isinstance(entry_types, str):
            types = [entry_types]
        else:
            types = [str(t) for t in entry_types if str(t or "").strip()]

    cursor_id: Optional[int] = None
    if before_cursor not in (None, ""):
        try:
            cursor_id = int(before_cursor)
        except (TypeError, ValueError):
            raise LedgerError("before_cursor must be a numeric ledger entry id.")

    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        where = ["e.currency = ?", "e.account IN (%s)" % ",".join("?" * len(account_list))]
        params: list = [cur_code, *account_list]
        if cursor_id is not None:
            where.append("e.id < ?")
            params.append(cursor_id)
        if types:
            where.append("t.entry_type IN (%s)" % ",".join("?" * len(types)))
            params.extend(types)

        # One extra row is fetched purely to answer "is there a next page?"
        # without a second COUNT query over the same predicate.
        params.append(limit + 1)

        sql = (
            "SELECT e.id AS entry_id, e.account, e.direction, "
            "       e.amount_cents AS entry_amount_cents, e.signed_amount_cents, "
            "       e.currency, e.created_at, "
            "       t.transaction_id, t.entry_type, t.amount_cents AS transaction_amount_cents, "
            "       t.source_account, t.destination_account, t.reason, t.related_object, "
            "       t.provider_reference, t.status, t.metadata_json "
            "FROM ledger_entries e "
            "JOIN ledger_transactions t ON t.transaction_id = e.transaction_id "
            "WHERE " + " AND ".join(where) + " "
            "ORDER BY e.id DESC LIMIT ?"
        )
        rows = [_row_to_dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]

        has_more = len(rows) > limit
        rows = rows[:limit]

        out = []
        for r in rows:
            metadata = None
            raw_meta = r.get("metadata_json")
            if raw_meta:
                try:
                    metadata = json.loads(raw_meta)
                except (TypeError, ValueError):
                    # A malformed blob is reported as absent rather than raised:
                    # one bad metadata row must not take down a money feed.
                    metadata = None
            out.append({
                "cursor": str(r.get("entry_id")),
                "transaction_id": r.get("transaction_id"),
                "account": r.get("account"),
                "direction": r.get("direction"),
                "entry_type": r.get("entry_type"),
                "amount_cents": int(r.get("entry_amount_cents") or 0),
                "signed_amount_cents": int(r.get("signed_amount_cents") or 0),
                "transaction_amount_cents": int(r.get("transaction_amount_cents") or 0),
                "currency": r.get("currency"),
                "source_account": r.get("source_account"),
                "destination_account": r.get("destination_account"),
                "reason": r.get("reason") or "",
                "related_object": r.get("related_object") or "",
                "provider_reference": r.get("provider_reference") or "",
                # Reported verbatim. A voided or pending transaction stays in the
                # feed wearing its real status; it is never filtered out to make
                # the list look tidier.
                "status": r.get("status") or "posted",
                "created_at": r.get("created_at"),
                "metadata": metadata,
            })

        return {
            "accounts": account_list,
            "currency": cur_code,
            "transactions": out,
            "next_cursor": out[-1]["cursor"] if (out and has_more) else None,
            "has_more": has_more,
        }
    finally:
        if owned:
            conn.close()


def post_entry(
    *,
    idempotency_key: str,
    actor: str,
    amount_cents: int,
    currency: str,
    entry_type: str,
    source: str,
    destination: str,
    reason: str = "",
    related_object: str = "",
    provider_reference: str = "",
    metadata: Optional[Mapping[str, Any]] = None,
    allow_negative: bool = False,
) -> dict:
    """Atomically post one immutable, idempotent double-entry transaction.

    Returns a dict describing the transaction. On a repeated call with the same
    ``idempotency_key`` it is a no-op and returns the original transaction with
    ``duplicate=True`` — no second entry is written.

    Raises :class:`LedgerError` (before any writes) on invalid input, e.g. a
    non-integer/negative amount, a missing account, or an overdraft on an
    account not permitted to go negative.
    """
    # ---- validation (reject before touching the DB) ----
    if not idempotency_key or not str(idempotency_key).strip():
        raise LedgerError("idempotency_key is required")
    if not actor or not str(actor).strip():
        raise LedgerError("actor is required")
    if isinstance(amount_cents, bool) or not isinstance(amount_cents, int):
        raise LedgerError("amount_cents must be an integer number of cents")
    if amount_cents <= 0:
        raise LedgerError("amount_cents must be a positive integer")
    if not source or not str(source).strip():
        raise LedgerError("source account is required")
    if not destination or not str(destination).strip():
        raise LedgerError("destination account is required")
    if source == destination:
        raise LedgerError("source and destination must differ")
    if not entry_type or not str(entry_type).strip():
        raise LedgerError("entry_type is required")
    currency = str(currency or "usd").lower()

    transaction_id = "ltx_" + uuid.uuid4().hex
    now = _utc_now_iso()
    meta_json = json.dumps(dict(metadata)) if metadata else None

    conn = db.connect()
    try:
        _begin(conn)

        # 1) Idempotency guard: one row per idempotency_key. If it already
        #    exists the UNIQUE constraint rejects this insert -> no-op.
        try:
            conn.execute(
                """
                INSERT INTO ledger_transactions
                    (idempotency_key, transaction_id, actor, entry_type,
                     amount_cents, currency, source_account, destination_account,
                     reason, related_object, provider_reference, status,
                     metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'posted', ?, ?)
                """,
                (
                    idempotency_key, transaction_id, actor, entry_type,
                    amount_cents, currency, source, destination,
                    reason or None, related_object or None,
                    provider_reference or None, meta_json, now,
                ),
            )
        except Exception as exc:  # noqa: BLE001
            if _is_unique_violation(exc):
                _rollback(conn)
                existing = get_transaction(idempotency_key, conn=conn)
                if existing is not None:
                    existing["duplicate"] = True
                    return existing
                # Extremely rare: unique hit but row not yet visible. Treat as
                # duplicate to stay safe (never double-post).
                return {"idempotency_key": idempotency_key, "duplicate": True}
            _rollback(conn)
            raise

        # 2) Overdraft guard for the debited (source) account.
        if not allow_negative and not source.startswith(_ALLOW_NEGATIVE_PREFIXES):
            current = get_balance(source, currency, conn=conn)
            if current - amount_cents < 0:
                _rollback(conn)
                raise LedgerError(
                    f"insufficient funds in {source}: balance={current} "
                    f"debit={amount_cents}"
                )

        # 3) Double-entry: debit source, credit destination.
        conn.execute(
            "INSERT INTO ledger_entries (transaction_id, account, direction, "
            "amount_cents, signed_amount_cents, currency, entry_type, created_at) "
            "VALUES (?, ?, 'debit', ?, ?, ?, ?, ?)",
            (transaction_id, source, amount_cents, -amount_cents, currency, entry_type, now),
        )
        conn.execute(
            "INSERT INTO ledger_entries (transaction_id, account, direction, "
            "amount_cents, signed_amount_cents, currency, entry_type, created_at) "
            "VALUES (?, ?, 'credit', ?, ?, ?, ?, ?)",
            (transaction_id, destination, amount_cents, amount_cents, currency, entry_type, now),
        )

        # 4) Re-derive both balances from entries inside the same transaction.
        source_balance = _recompute_balance_in_tx(conn, source, currency)
        dest_balance = _recompute_balance_in_tx(conn, destination, currency)

        _commit(conn)

        return {
            "duplicate": False,
            "transaction_id": transaction_id,
            "idempotency_key": idempotency_key,
            "actor": actor,
            "entry_type": entry_type,
            "amount_cents": amount_cents,
            "currency": currency,
            "source_account": source,
            "destination_account": destination,
            "source_balance_cents": source_balance,
            "destination_balance_cents": dest_balance,
            "status": "posted",
            "created_at": now,
        }
    except LedgerError:
        raise
    except Exception:
        _rollback(conn)
        raise
    finally:
        conn.close()
