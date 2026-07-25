"""Business OS — the canonical confirmation-grant service.

ONE implementation of "a human approved this exact action", shared by every governed
subsystem. Before this module existed each subsystem rolled its own approval, and the
implementations disagreed on the properties that matter:

  * marketplace + advertising derived the token as ``sha256(salt|user|tool|params)``.
    Because that is *reproducible*, the token was valid forever and reusable without
    limit — approving "publish product X" once approved every future publish of X.
  * ``undx_actions.engine`` stored a real row but treated ``expires_at`` as optional,
    so a grant written without one never expired.
  * none of them could be revoked.

An approval is therefore a ROW, never a derivable value, and it carries all seven
properties Mission XIII requires:

  bound to one action .... ``tool``
  bound to one payload ... ``params_hash`` over the caller's CANONICAL params
  bound to one actor ..... ``subject`` (namespaced, so user 7 in marketplace is not
                            user 7 in advertising)
  time-limited ........... ``expires_at``, always set, never nullable
  single-use ............. atomic ``UPDATE ... WHERE status='pending'`` + rowcount guard
  replay resistant ....... a consumed row can never return to 'pending'
  revocable .............. :func:`revoke`
  audited ................ every row retains created/consumed timestamps and the
                            canonical params that were approved

The raw token is returned exactly once and never stored — only its sha256 — so reading
the database cannot yield a usable approval.

Namespacing: every call takes a ``namespace`` (e.g. ``"marketplace"``). Grants are
scoped by it, so a token minted by one subsystem can never be redeemed in another even
if tool names collide.

This module owns its own table and creates it idempotently, so it works in any process
that can reach ``services.db`` without an ordering dependency on another schema module.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from services import db as _db


TABLE = "business_os_confirmation_grants"

TTL_ENV = "BUSINESS_OS_CONFIRMATION_TTL_SECONDS"
TTL_DEFAULT = 300
TTL_MIN = 30
TTL_MAX = 900

# Stable, subsystem-agnostic failure codes. Callers translate these into their own
# error type (MarketplaceError, AdvertisingError, ...) while keeping the code and HTTP
# status identical, so one governance contract is visible through every API.
CODE_REQUIRED = "confirmation_required"      # 428 — no token supplied at all
CODE_MISMATCH = "confirmation_mismatch"      # 409 — unknown/forged/mis-bound
CODE_EXPIRED = "confirmation_expired"        # 409 — TTL elapsed
CODE_USED = "confirmation_used"              # 409 — already redeemed (replay)
CODE_REVOKED = "confirmation_revoked"        # 409 — withdrawn before redemption

HTTP_REQUIRED = 428
HTTP_REFUSED = 409

_MISMATCH_MESSAGE = "Confirmation token does not match this exact action."


class ConfirmationError(Exception):
    """Raised when a grant cannot be minted or redeemed.

    Carries a stable ``code`` and ``http_status`` so each subsystem can re-raise it as
    its own error type without inventing new semantics.
    """

    def __init__(self, message: str, http_status: int, code: str):
        super().__init__(message)
        self.http_status = http_status
        self.code = code


# --- helpers ----------------------------------------------------------------
def ttl_seconds(override: Optional[int] = None) -> int:
    """Resolve the grant lifetime, clamped so neither env nor caller can disable expiry.

    An unbounded TTL is the same defect as no TTL, so ``TTL_MAX`` is a hard ceiling.
    """
    if override is not None:
        candidate = override
    else:
        raw = os.environ.get(TTL_ENV)
        candidate = raw if raw not in (None, "") else TTL_DEFAULT
    try:
        val = int(str(candidate).strip())
    except (TypeError, ValueError):
        val = TTL_DEFAULT
    return max(TTL_MIN, min(val, TTL_MAX))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    """Fixed-width UTC stamp so string comparison is a valid time comparison."""
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def params_hash(tool: str, canonical: Any) -> str:
    """Hash the CANONICAL params. Callers must pass the same normalized structure they
    will act on, so binding cannot be bypassed by re-ordering or padding client input."""
    payload = json.dumps({"t": str(tool), "p": canonical}, sort_keys=True,
                         separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def token_hash(raw: Any) -> str:
    return hashlib.sha256(str(raw).encode("utf-8")).hexdigest()


def _row(row) -> Optional[dict]:
    if row is None:
        return None
    try:
        return dict(row)
    except Exception:
        return {k: row[k] for k in row.keys()}


def ensure_schema(conn=None) -> None:
    """Create the grant table. Idempotent and safe to call on every request path."""
    owned = conn is None
    if owned:
        conn = _db.connect()
    try:
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE} (
                token_hash TEXT PRIMARY KEY,
                namespace TEXT NOT NULL,
                subject TEXT NOT NULL,
                tool TEXT NOT NULL,
                params_hash TEXT NOT NULL,
                params_json TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                consumed_at TEXT,
                meta_json TEXT
            )
            """
        )
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_confirm_grant_subject "
            f"ON {TABLE} (namespace, subject, tool, status)")
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_confirm_grant_expiry "
            f"ON {TABLE} (status, expires_at)")
        if owned:
            conn.commit()
    finally:
        if owned:
            conn.close()


# --- mint / consume / revoke -------------------------------------------------
def mint(namespace: str, subject: Any, tool: str, canonical: Any, *,
         ttl_override: Optional[int] = None, meta: Any = None) -> dict:
    """Create a single-use, time-limited grant.

    Returns ``{"confirmation_token", "expires_at", "ttl_seconds", "single_use"}``. The
    raw token is present only in this return value and is never persisted.
    """
    ns = str(namespace or "").strip()
    tool_name = str(tool or "").strip()
    if not ns or not tool_name:
        raise ConfirmationError("namespace and tool are required.", 400, "invalid_grant")

    raw = secrets.token_urlsafe(32)
    ttl = ttl_seconds(ttl_override)
    now = _now()
    expires = now + timedelta(seconds=ttl)
    conn = _db.connect()
    try:
        ensure_schema(conn)
        conn.execute(
            f"INSERT INTO {TABLE} (token_hash, namespace, subject, tool, params_hash, "
            "params_json, status, expires_at, created_at, meta_json) "
            "VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)",
            (token_hash(raw), ns, str(subject), tool_name,
             params_hash(tool_name, canonical),
             json.dumps(canonical, sort_keys=True, default=str),
             _iso(expires), _iso(now),
             None if meta is None else json.dumps(meta, sort_keys=True, default=str)))
        conn.commit()
    finally:
        conn.close()
    return {
        "confirmation_token": raw,
        "expires_at": _iso(expires),
        "ttl_seconds": ttl,
        "single_use": True,
    }


def consume(namespace: str, subject: Any, tool: str, canonical: Any,
            raw_token: Optional[str]) -> dict:
    """Atomically redeem a grant for exactly this (namespace, subject, tool, params).

    Raises :class:`ConfirmationError` with a stable code on every failure path. Returns
    the redeemed row on success. Callers MUST call this before performing the action.
    """
    if not raw_token:
        raise ConfirmationError(
            "This action requires confirmation. Call plan() and confirm the token.",
            HTTP_REQUIRED, CODE_REQUIRED)

    ns = str(namespace or "").strip()
    tool_name = str(tool or "").strip()
    th = token_hash(raw_token)
    expected_params = params_hash(tool_name, canonical)

    conn = _db.connect()
    try:
        ensure_schema(conn)
        row = _row(conn.execute(
            f"SELECT * FROM {TABLE} WHERE token_hash = ?", (th,)).fetchone())
        if row is None:
            raise ConfirmationError(_MISMATCH_MESSAGE, HTTP_REFUSED, CODE_MISMATCH)

        # Binding is checked BEFORE status/expiry on purpose. Reporting "expired" or
        # "already used" for a mis-bound token would confirm to a guesser that the
        # token exists, so every binding failure is indistinguishable from an unknown
        # token.
        if (str(row.get("namespace")) != ns
                or str(row.get("subject")) != str(subject)
                or str(row.get("tool")) != tool_name
                or str(row.get("params_hash")) != expected_params):
            raise ConfirmationError(_MISMATCH_MESSAGE, HTTP_REFUSED, CODE_MISMATCH)

        status = str(row.get("status") or "")
        if status == "revoked":
            raise ConfirmationError("This confirmation was revoked.",
                                    HTTP_REFUSED, CODE_REVOKED)
        if status != "pending":
            raise ConfirmationError(
                "This confirmation was already used. Confirm the action again.",
                HTTP_REFUSED, CODE_USED)
        if str(row.get("expires_at") or "") <= _iso(_now()):
            raise ConfirmationError(
                "This confirmation expired. Confirm the action again.",
                HTTP_REFUSED, CODE_EXPIRED)

        # The single-use boundary. Under concurrency, exactly one caller's UPDATE flips
        # 'pending'; every other caller sees rowcount 0 and is refused. This is what
        # makes two simultaneous redemptions of one approval impossible.
        cur = conn.execute(
            f"UPDATE {TABLE} SET status = 'consumed', consumed_at = ? "
            "WHERE token_hash = ? AND status = 'pending'", (_iso(_now()), th))
        if int(getattr(cur, "rowcount", 0) or 0) != 1:
            try:
                conn.rollback()
            except Exception:
                pass
            raise ConfirmationError(
                "This confirmation was already used. Confirm the action again.",
                HTTP_REFUSED, CODE_USED)
        conn.commit()
        row["status"] = "consumed"
        return row
    finally:
        conn.close()


def revoke(namespace: str, subject: Any, raw_token: Optional[str]) -> dict:
    """Withdraw a pending grant. Only its own subject, in its own namespace, may revoke.

    Idempotent: revoking an unknown, foreign, already-consumed or already-revoked grant
    reports ``revoked: False`` rather than raising, so a revoke-all sweep is safe.
    """
    if not raw_token:
        return {"ok": True, "revoked": False}
    conn = _db.connect()
    try:
        ensure_schema(conn)
        cur = conn.execute(
            f"UPDATE {TABLE} SET status = 'revoked' WHERE token_hash = ? "
            "AND namespace = ? AND subject = ? AND status = 'pending'",
            (token_hash(raw_token), str(namespace or "").strip(), str(subject)))
        revoked = int(getattr(cur, "rowcount", 0) or 0) == 1
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "revoked": revoked}


# --- introspection / maintenance --------------------------------------------
def describe(namespace: str, subject: Any, raw_token: Optional[str]) -> Optional[dict]:
    """Non-consuming lookup for UI/audit. Returns the grant's public shape or None.

    Never returns another subject's grant and never returns the raw token.
    """
    if not raw_token:
        return None
    conn = _db.connect()
    try:
        ensure_schema(conn)
        row = _row(conn.execute(
            f"SELECT namespace, subject, tool, status, expires_at, created_at, "
            f"consumed_at, params_json FROM {TABLE} WHERE token_hash = ? "
            "AND namespace = ? AND subject = ?",
            (token_hash(raw_token), str(namespace or "").strip(),
             str(subject))).fetchone())
        return row
    finally:
        conn.close()


def expire_stale(*, limit: int = 1000) -> int:
    """Mark elapsed pending grants 'expired'. Housekeeping only — :func:`consume`
    already refuses an elapsed grant, so correctness does not depend on this running."""
    conn = _db.connect()
    try:
        ensure_schema(conn)
        cur = conn.execute(
            f"UPDATE {TABLE} SET status = 'expired' WHERE status = 'pending' "
            "AND expires_at <= ?", (_iso(_now()),))
        n = int(getattr(cur, "rowcount", 0) or 0)
        conn.commit()
        return n
    finally:
        conn.close()
