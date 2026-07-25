"""Usage limits & quotas with atomic consumption.

Metered entitlements (catalog rows with a non-NULL ``limit_value``) are enforced
here. A boolean capability (``limit_value=None``) is unlimited: if the subject
holds the entitlement, consumption always succeeds and is still counted for
observability.

Atomicity: consumption runs inside a single ``BEGIN IMMEDIATE`` transaction (on
SQLite this takes the write lock up front so concurrent consumers serialize
instead of racing past a limit; on Postgres the enclosing transaction + the
composite PK play the same role). The check-then-increment is therefore a single
atomic step — two concurrent callers cannot both slip past the last unit.

Period bucketing: ``limit_period`` controls the reset window and the default
``period_key``: ``day`` -> ``YYYY-MM-DD`` (UTC), ``month`` -> ``YYYY-MM``,
``cycle`` -> caller must pass an explicit ``period_key`` (e.g. ``cycle:<sub_ref>``).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from services import db
from services.business_os.entitlements import service as _svc


class QuotaExceeded(Exception):
    """Raised (only if ``raise_on_deny=True``) when consumption would exceed the limit."""

    def __init__(self, key: str, used: int, limit: int, amount: int):
        self.key = key
        self.used = used
        self.limit = limit
        self.amount = amount
        super().__init__(
            f"quota exceeded for {key!r}: used={used} + {amount} > limit={limit}")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def default_period_key(limit_period: Optional[str]) -> str:
    """Derive the current period bucket from the limit period. ``cycle`` and
    unknown periods require an explicit key from the caller, signalled by the
    sentinel ``''`` (empty) which the caller must replace."""
    now = datetime.now(timezone.utc)
    if limit_period == "day":
        return now.strftime("%Y-%m-%d")
    if limit_period == "month":
        return now.strftime("%Y-%m")
    if limit_period in (None, "", "cycle"):
        return ""  # boolean/unlimited or cycle -> caller supplies key
    return now.strftime("%Y-%m-%d")


def get_usage(subject_id: Any, key: str, *, period_key: str,
              subject_type: str = "user", conn=None) -> int:
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        cur = conn.execute(
            "SELECT used FROM business_os_ent_usage "
            "WHERE subject_type=? AND subject_id=? AND entitlement_key=? AND period_key=?",
            (subject_type, _svc._sid(subject_id), key, period_key),
        )
        row = cur.fetchone()
        return int(row[0] if row is not None else 0)
    finally:
        if owned:
            conn.close()


def check_and_consume(subject_id: Any, key: str, *, amount: int = 1,
                      period_key: Optional[str] = None, subject_type: str = "user",
                      raise_on_deny: bool = False, conn=None) -> dict:
    """Atomically verify entitlement + quota and, if allowed, consume ``amount``.

    Returns ``{allowed, reason, used, limit, remaining, period_key}``. ``limit``
    is None for a boolean/unlimited entitlement. Denies (without consuming) when:
      * the subject does not hold the entitlement (reason='not_entitled'), or
      * consuming would exceed the metered limit (reason='quota_exceeded').

    ``amount`` must be a positive integer.
    """
    if isinstance(amount, bool) or int(amount) <= 0:
        raise ValueError("amount must be a positive integer")
    amount = int(amount)

    owned = conn is None
    if owned:
        conn = db.connect()
    st = subject_type
    sid = _svc._sid(subject_id)
    try:
        _svc._begin(conn)

        # 1. Entitlement must resolve to ALLOW under canonical precedence.
        grants = _svc._fetch_grants(conn, st, sid, key)
        decision = _svc._resolve(grants, _svc._utc_now())
        if not decision["allowed"]:
            _svc._commit(conn)
            result = {"allowed": False, "reason": "not_entitled", "used": 0,
                      "limit": None, "remaining": 0, "period_key": None}
            if raise_on_deny:
                raise QuotaExceeded(key, 0, 0, amount)
            return result

        win = decision["grant"] or {}
        limit = win.get("limit_value")

        # 2. Boolean/unlimited entitlement: always allowed; count for telemetry.
        if limit is None:
            pk = period_key or default_period_key(win.get("limit_period")) or "_"
            used = _increment(conn, st, sid, key, pk, amount)
            _svc._commit(conn)
            return {"allowed": True, "reason": "unlimited", "used": used,
                    "limit": None, "remaining": None, "period_key": pk}

        # 3. Metered entitlement: enforce the limit atomically.
        pk = period_key or default_period_key(win.get("limit_period"))
        if not pk:
            _svc._rollback(conn)
            raise ValueError(
                f"entitlement {key!r} has period '{win.get('limit_period')}' that "
                "requires an explicit period_key")
        current = get_usage(sid, key, period_key=pk, subject_type=st, conn=conn)
        limit = int(limit)
        if current + amount > limit:
            _svc._commit(conn)
            result = {"allowed": False, "reason": "quota_exceeded", "used": current,
                      "limit": limit, "remaining": max(0, limit - current),
                      "period_key": pk}
            if raise_on_deny:
                raise QuotaExceeded(key, current, limit, amount)
            return result

        used = _increment(conn, st, sid, key, pk, amount)
        _svc._commit(conn)
        return {"allowed": True, "reason": "consumed", "used": used, "limit": limit,
                "remaining": max(0, limit - used), "period_key": pk}
    except Exception:
        _svc._rollback(conn)
        raise
    finally:
        if owned:
            conn.close()


def _increment(conn, subject_type: str, subject_id: str, key: str,
               period_key: str, amount: int) -> int:
    """UPSERT the usage counter inside the caller's open transaction. Returns the
    new ``used`` total. Portable UPDATE-else-INSERT (no engine-specific UPSERT)."""
    now = _now_iso()
    cur = conn.execute(
        "UPDATE business_os_ent_usage SET used = used + ?, updated_at = ? "
        "WHERE subject_type=? AND subject_id=? AND entitlement_key=? AND period_key=?",
        (amount, now, subject_type, subject_id, key, period_key),
    )
    if getattr(cur, "rowcount", 0) == 0:
        conn.execute(
            "INSERT INTO business_os_ent_usage "
            "(subject_type, subject_id, entitlement_key, period_key, used, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (subject_type, subject_id, key, period_key, amount, now),
        )
        return amount
    row = conn.execute(
        "SELECT used FROM business_os_ent_usage "
        "WHERE subject_type=? AND subject_id=? AND entitlement_key=? AND period_key=?",
        (subject_type, subject_id, key, period_key),
    ).fetchone()
    return int(row[0] if row is not None else amount)
