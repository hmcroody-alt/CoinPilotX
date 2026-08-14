"""Canonical, server-authoritative entitlement service.

This is the single decision point for "does subject S hold entitlement K?" in the
Business OS. It reads and writes only the additive ``business_os_ent_*`` tables
(see ``schema.py``); it never touches legacy entitlement tables. Legacy fallback
lives one layer up in ``facade.py`` so this module stays a clean canonical core.

Design mirrors the ledger slice: engine-portable via ``services.db``, integer
math, atomic writes with ``BEGIN IMMEDIATE`` on SQLite, DB-enforced idempotency
(``UNIQUE(subject_type, subject_id, entitlement_key, source, source_reference)``),
and no ``bot.py`` import so it can be unit-tested in isolation.

Precedence (fixed order, first matching rule wins) — from the migration report:

    1. suspended grant present            -> DENY  (security/compliance hold)
    2. active grant present               -> ALLOW
    3. grace-period grant present         -> ALLOW  (expired but grace_until>now)
    4. grandfathered grant present        -> ALLOW  (founder/legacy locked)
    5. revoked grant present, none above  -> DENY
    6. nothing                            -> DENY

Suspension is evaluated first and beats everything; revocation only denies when
no active/grace/grandfathered grant supersedes it (i.e. a later paid grant
restores access). Legacy fallback (rule "6. valid legacy" in the report) is a
facade concern, layered on a canonical DENY.

Public API:
    has_entitlement, get_entitlements, get_entitlement_limits, explain_entitlement,
    grant_entitlement, revoke_entitlement, suspend_entitlement,
    sync_subscription_entitlements, reconcile_entitlements.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional

from services import db
from services.business_os.entitlements import schema as _schema


class EntitlementError(ValueError):
    """Raised when an entitlement write is rejected before any state changes."""


# Grant statuses.
STATUS_ACTIVE = "active"
STATUS_EXPIRED = "expired"
STATUS_SUSPENDED = "suspended"
STATUS_PENDING = "pending"
STATUS_REVOKED = "revoked"
STATUS_GRANDFATHERED = "grandfathered"

_VALID_STATUS = {
    STATUS_ACTIVE, STATUS_EXPIRED, STATUS_SUSPENDED,
    STATUS_PENDING, STATUS_REVOKED, STATUS_GRANDFATHERED,
}

# Sources that may originate a grant. Kept explicit so a typo can't silently
# create an unrecognised provenance.
_VALID_SOURCES = {
    "stripe", "apple_app_store", "google_play", "admin", "promotion", "trial",
    "merchant_approval", "business_role", "legacy_migration", "feature_flag",
    "internal_testing",
}

# Sources that only ever grant marketplace.* keys (never premium.*), per the
# report's precedence rules.
_MARKETPLACE_ONLY_SOURCES = {"merchant_approval"}


# --- time helpers -----------------------------------------------------------
def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _utc_now().strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _to_dt(value: Any) -> Optional[datetime]:
    """Parse a stored timestamp to an aware UTC datetime, tolerant of formats.

    Accepts the canonical ``...Z`` ISO form this module writes, plain ISO, and
    ``YYYY-MM-DD HH:MM:SS`` (legacy). Returns None for empty/unparseable input
    (callers treat None as "no bound").
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    s = str(value).strip()
    if not s:
        return None
    txt = s.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(txt)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _sid(subject_id: Any) -> str:
    """Subjects are stored as TEXT so users and businesses share one column."""
    return str(subject_id)


def _row_to_dict(row) -> Optional[dict]:
    if row is None:
        return None
    try:
        return dict(row)
    except Exception:
        return {k: row[k] for k in row.keys()}


def _begin(conn) -> None:
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


def _is_unique_violation(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "unique" in msg or "duplicate key" in msg


# --- resolution engine ------------------------------------------------------
def _grant_phase(grant: Mapping[str, Any], now: datetime) -> str:
    """Classify a single grant at ``now`` into one of the precedence phases:
    'suspended' | 'active' | 'grace' | 'grandfathered' | 'revoked' | 'inactive'.
    """
    status = (grant.get("status") or "").strip()
    if status == STATUS_SUSPENDED:
        return "suspended"
    if status == STATUS_REVOKED:
        return "revoked"
    if status == STATUS_GRANDFATHERED:
        return "grandfathered"

    starts = _to_dt(grant.get("starts_at"))
    if starts is not None and now < starts:
        return "inactive"  # not started yet (pending in time)

    expires = _to_dt(grant.get("expires_at"))
    if status == STATUS_ACTIVE:
        if expires is None or now < expires:
            return "active"
        # expired by clock — is it inside a grace window?
        grace = _to_dt(grant.get("grace_until"))
        if grace is not None and now < grace:
            return "grace"
        return "inactive"

    if status == STATUS_EXPIRED or status == STATUS_PENDING:
        grace = _to_dt(grant.get("grace_until"))
        if status == STATUS_EXPIRED and grace is not None and now < grace:
            return "grace"
        return "inactive"

    return "inactive"


def _resolve(grants: Iterable[Mapping[str, Any]], now: datetime) -> dict:
    """Apply the fixed precedence order across all grants for one key.

    Returns ``{allowed, mode, grant}`` where ``mode`` is the winning phase
    ('suspended'|'active'|'grace'|'grandfathered'|'revoked'|'none').
    """
    phases: dict[str, list[dict]] = {}
    for g in grants:
        phase = _grant_phase(g, now)
        phases.setdefault(phase, []).append(dict(g))

    if phases.get("suspended"):
        return {"allowed": False, "mode": "suspended", "grant": phases["suspended"][0]}
    if phases.get("active"):
        return {"allowed": True, "mode": "active", "grant": phases["active"][0]}
    if phases.get("grace"):
        return {"allowed": True, "mode": "grace", "grant": phases["grace"][0]}
    if phases.get("grandfathered"):
        return {"allowed": True, "mode": "grandfathered", "grant": phases["grandfathered"][0]}
    if phases.get("revoked"):
        return {"allowed": False, "mode": "revoked", "grant": phases["revoked"][0]}
    return {"allowed": False, "mode": "none", "grant": None}


def _fetch_grants(conn, subject_type: str, subject_id: str, key: str) -> list:
    cur = conn.execute(
        "SELECT * FROM business_os_ent_grants "
        "WHERE subject_type = ? AND subject_id = ? AND entitlement_key = ?",
        (subject_type, subject_id, key),
    )
    return [_row_to_dict(r) for r in cur.fetchall()]


# --- read API ---------------------------------------------------------------
def has_entitlement(subject_id: Any, key: str, *, subject_type: str = "user",
                    conn=None) -> bool:
    """True iff the subject currently holds ``key`` under canonical precedence.

    Canonical-only: a False here is what the facade may override with a logged
    legacy fallback during migration.
    """
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        grants = _fetch_grants(conn, subject_type, _sid(subject_id), key)
        return _resolve(grants, _utc_now())["allowed"]
    finally:
        if owned:
            conn.close()


def get_entitlements(subject_id: Any, *, subject_type: str = "user",
                     conn=None) -> list:
    """All entitlement keys the subject currently holds (allowed), with mode.

    Returns a sorted list of ``{key, mode, expires_at, source}``.
    """
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        cur = conn.execute(
            "SELECT * FROM business_os_ent_grants "
            "WHERE subject_type = ? AND subject_id = ?",
            (subject_type, _sid(subject_id)),
        )
        rows = [_row_to_dict(r) for r in cur.fetchall()]
        by_key: dict[str, list[dict]] = {}
        for r in rows:
            by_key.setdefault(r["entitlement_key"], []).append(r)
        now = _utc_now()
        out = []
        for k, grants in by_key.items():
            decision = _resolve(grants, now)
            if decision["allowed"]:
                g = decision["grant"] or {}
                out.append({
                    "key": k,
                    "mode": decision["mode"],
                    "expires_at": g.get("expires_at"),
                    "source": g.get("source"),
                })
        return sorted(out, key=lambda d: d["key"])
    finally:
        if owned:
            conn.close()


def get_entitlement_limits(subject_id: Any, key: str, *, subject_type: str = "user",
                           conn=None) -> Optional[dict]:
    """The metered limit for ``key`` if the subject holds it, else None.

    Prefers a limit on the winning grant; falls back to the catalog limit for
    the grant's plan (if the grant records one). ``{limit_value, limit_period}``
    with ``limit_value=None`` meaning boolean/unlimited.
    """
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        grants = _fetch_grants(conn, subject_type, _sid(subject_id), key)
        decision = _resolve(grants, _utc_now())
        if not decision["allowed"]:
            return None
        g = decision["grant"] or {}
        return {
            "limit_value": g.get("limit_value"),
            "limit_period": g.get("limit_period"),
            "mode": decision["mode"],
        }
    finally:
        if owned:
            conn.close()


def explain_entitlement(subject_id: Any, key: str, *, subject_type: str = "user",
                        conn=None) -> dict:
    """Full precedence trace for one key — the debugging/admin view.

    Returns ``{subject_type, subject_id, key, allowed, mode, decision_grant_id,
    grants:[{id, source, status, phase, starts_at, expires_at, grace_until}]}``.
    Does not fall back to legacy (that is the facade's ``shadow_compare``).
    """
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        now = _utc_now()
        grants = _fetch_grants(conn, subject_type, _sid(subject_id), key)
        decision = _resolve(grants, now)
        trace = []
        for g in grants:
            trace.append({
                "id": g.get("id"),
                "source": g.get("source"),
                "source_reference": g.get("source_reference"),
                "status": g.get("status"),
                "phase": _grant_phase(g, now),
                "starts_at": g.get("starts_at"),
                "expires_at": g.get("expires_at"),
                "grace_until": g.get("grace_until"),
            })
        win = decision["grant"] or {}
        return {
            "subject_type": subject_type,
            "subject_id": _sid(subject_id),
            "key": key,
            "allowed": decision["allowed"],
            "mode": decision["mode"],
            "decision_grant_id": win.get("id"),
            "grants": sorted(trace, key=lambda d: (d["id"] or 0)),
        }
    finally:
        if owned:
            conn.close()


# --- audit helper -----------------------------------------------------------
def _audit(conn, *, subject_type: str, subject_id: str, key: Optional[str],
           action: str, actor: Optional[str], reason: Optional[str],
           before: Any, after: Any) -> None:
    conn.execute(
        "INSERT INTO business_os_ent_audit "
        "(subject_type, subject_id, entitlement_key, action, actor, reason, "
        "before_json, after_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            subject_type, subject_id, key, action, actor, reason,
            json.dumps(before, sort_keys=True) if before is not None else None,
            json.dumps(after, sort_keys=True) if after is not None else None,
            _now_iso(),
        ),
    )


# --- write API --------------------------------------------------------------
def grant_entitlement(subject_id: Any, key: str, *, source: str,
                      source_reference: str = "", subject_type: str = "user",
                      status: str = STATUS_ACTIVE, starts_at: Optional[str] = None,
                      expires_at: Optional[str] = None, grace_until: Optional[str] = None,
                      limit_value: Optional[int] = None, limit_period: Optional[str] = None,
                      region: Optional[str] = None, platform: Optional[str] = None,
                      created_by: Optional[str] = None, audit_reference: Optional[str] = None,
                      reason: Optional[str] = None, metadata: Optional[Mapping] = None,
                      conn=None) -> dict:
    """Create or upsert a grant. Idempotent on the natural key.

    A repeated call with the same ``(subject, key, source, source_reference)``
    updates the existing row's mutable fields instead of inserting a duplicate,
    so replayed provider events and repeated admin actions are safe.
    """
    if source not in _VALID_SOURCES:
        raise EntitlementError(f"unknown grant source {source!r}")
    if status not in _VALID_STATUS:
        raise EntitlementError(f"invalid grant status {status!r}")
    if source in _MARKETPLACE_ONLY_SOURCES and not key.startswith("marketplace."):
        raise EntitlementError(
            f"source {source!r} may only grant marketplace.* keys, not {key!r}")
    if limit_value is not None and (isinstance(limit_value, bool) or int(limit_value) < 0):
        raise EntitlementError("limit_value must be a non-negative integer or None")

    owned = conn is None
    if owned:
        conn = db.connect()
    st = subject_type
    sid = _sid(subject_id)
    ref = source_reference or ""
    now = _now_iso()
    try:
        _begin(conn)
        cur = conn.execute(
            "SELECT * FROM business_os_ent_grants "
            "WHERE subject_type=? AND subject_id=? AND entitlement_key=? "
            "AND source=? AND source_reference=?",
            (st, sid, key, source, ref),
        )
        existing = _row_to_dict(cur.fetchone())
        meta_json = json.dumps(dict(metadata), sort_keys=True) if metadata else None

        if existing is None:
            try:
                conn.execute(
                    "INSERT INTO business_os_ent_grants "
                    "(subject_type, subject_id, entitlement_key, source, source_reference, "
                    "status, starts_at, expires_at, grace_until, limit_value, limit_period, "
                    "region, platform, revocation_reason, created_by, audit_reference, "
                    "metadata_json, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?)",
                    (st, sid, key, source, ref, status, starts_at, expires_at,
                     grace_until, limit_value, limit_period, region, platform,
                     created_by, audit_reference, meta_json, now, now),
                )
            except Exception as exc:  # racing insert -> fall through to update
                if not _is_unique_violation(exc):
                    _rollback(conn)
                    raise
                cur = conn.execute(
                    "SELECT * FROM business_os_ent_grants "
                    "WHERE subject_type=? AND subject_id=? AND entitlement_key=? "
                    "AND source=? AND source_reference=?",
                    (st, sid, key, source, ref),
                )
                existing = _row_to_dict(cur.fetchone())

        if existing is not None:
            conn.execute(
                "UPDATE business_os_ent_grants SET status=?, starts_at=?, expires_at=?, "
                "grace_until=?, limit_value=?, limit_period=?, region=?, platform=?, "
                "created_by=?, audit_reference=?, metadata_json=?, updated_at=? "
                "WHERE id=?",
                (status, starts_at, expires_at, grace_until, limit_value, limit_period,
                 region, platform, created_by, audit_reference, meta_json, now,
                 existing["id"]),
            )

        _audit(conn, subject_type=st, subject_id=sid, key=key, action="grant",
               actor=created_by, reason=reason,
               before=existing, after={"status": status, "expires_at": expires_at,
                                        "source": source})
        _commit(conn)
        return get_grant(st, sid, key, source, ref, conn=None) or {}
    except Exception:
        _rollback(conn)
        raise
    finally:
        if owned:
            conn.close()


def revoke_entitlement(subject_id: Any, key: str, *, reason: str = "",
                       subject_type: str = "user", source: Optional[str] = None,
                       source_reference: Optional[str] = None,
                       actor: Optional[str] = None, conn=None) -> dict:
    """Revoke matching grants for ``key`` (status -> revoked). Idempotent.

    Without ``source`` all grants for the key are revoked; with it, only grants
    from that source (optionally that exact ``source_reference``).
    """
    owned = conn is None
    if owned:
        conn = db.connect()
    st = subject_type
    sid = _sid(subject_id)
    now = _now_iso()
    try:
        _begin(conn)
        params = [st, sid, key]
        sql = ("SELECT * FROM business_os_ent_grants "
               "WHERE subject_type=? AND subject_id=? AND entitlement_key=?")
        if source is not None:
            sql += " AND source=?"
            params.append(source)
        if source_reference is not None:
            sql += " AND source_reference=?"
            params.append(source_reference)
        before = [_row_to_dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]
        affected = 0
        for g in before:
            if g["status"] == STATUS_REVOKED:
                continue
            conn.execute(
                "UPDATE business_os_ent_grants SET status=?, revocation_reason=?, "
                "updated_at=? WHERE id=?",
                (STATUS_REVOKED, reason, now, g["id"]),
            )
            affected += 1
        _audit(conn, subject_type=st, subject_id=sid, key=key, action="revoke",
               actor=actor, reason=reason, before=before, after={"status": STATUS_REVOKED})
        _commit(conn)
        return {"revoked": affected, "key": key, "subject_id": sid}
    except Exception:
        _rollback(conn)
        raise
    finally:
        if owned:
            conn.close()


def suspend_entitlement(subject_id: Any, key: str, *, reason: str = "",
                        subject_type: str = "user", actor: Optional[str] = None,
                        conn=None) -> dict:
    """Suspend all grants for ``key`` (security/compliance hold). Highest-precedence
    DENY; reversible by re-granting/reinstating. Idempotent."""
    owned = conn is None
    if owned:
        conn = db.connect()
    st = subject_type
    sid = _sid(subject_id)
    now = _now_iso()
    try:
        _begin(conn)
        before = _fetch_grants(conn, st, sid, key)
        affected = 0
        for g in before:
            if g["status"] == STATUS_SUSPENDED:
                continue
            conn.execute(
                "UPDATE business_os_ent_grants SET status=?, revocation_reason=?, "
                "updated_at=? WHERE id=?",
                (STATUS_SUSPENDED, reason, now, g["id"]),
            )
            affected += 1
        _audit(conn, subject_type=st, subject_id=sid, key=key, action="suspend",
               actor=actor, reason=reason, before=before, after={"status": STATUS_SUSPENDED})
        _commit(conn)
        return {"suspended": affected, "key": key, "subject_id": sid}
    except Exception:
        _rollback(conn)
        raise
    finally:
        if owned:
            conn.close()


def get_grant(subject_type: str, subject_id: Any, key: str, source: str,
              source_reference: str = "", *, conn=None) -> Optional[dict]:
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        cur = conn.execute(
            "SELECT * FROM business_os_ent_grants "
            "WHERE subject_type=? AND subject_id=? AND entitlement_key=? "
            "AND source=? AND source_reference=?",
            (subject_type, _sid(subject_id), key, source, source_reference or ""),
        )
        return _row_to_dict(cur.fetchone())
    finally:
        if owned:
            conn.close()


# --- subscription projection ------------------------------------------------
def sync_subscription_entitlements(subject_id: Any, plan_key: str, *,
                                   status: str = "active",
                                   source: str = "stripe",
                                   source_reference: str = "",
                                   period_end: Optional[str] = None,
                                   grace_until: Optional[str] = None,
                                   subject_type: str = "user",
                                   actor: Optional[str] = None,
                                   conn=None) -> dict:
    """Project a subscription's plan into canonical grants for every entitlement
    the plan confers (per ``business_os_ent_catalog``). Idempotent per event.

    ``status='active'`` grants/refreshes; ``status`` in {canceled, past_due,
    unpaid} does NOT immediately revoke — access is kept until ``period_end``
    (the report's "cancellation keeps access until period end" rule). An explicit
    refund/revocation is handled by ``revoke_entitlement`` with the event ref.
    """
    owned = conn is None
    if owned:
        conn = db.connect()
    st = subject_type
    sid = _sid(subject_id)
    try:
        cur = conn.execute(
            "SELECT entitlement_key, limit_value, limit_period "
            "FROM business_os_ent_catalog WHERE plan_key = ?",
            (plan_key,),
        )
        catalog = [_row_to_dict(r) for r in cur.fetchall()]
        if not catalog:
            raise EntitlementError(f"no catalog entries for plan {plan_key!r}")

        # For active/canceled/past_due we still keep access until period_end; the
        # grant stays 'active' with expires_at=period_end so the clock decides.
        # ``grandfathered`` is the one status that must survive the mapping: a
        # grandfathered grant (Founders) never expires and must not be swept by
        # renewal/expiry reconciliation, which keys off the 'active' phase.
        # (This line previously read ``STATUS_ACTIVE if status == "active" else
        # STATUS_ACTIVE`` — both branches identical — so grandfathered silently
        # became a plain active grant.)
        grant_status = (
            STATUS_GRANDFATHERED if status == STATUS_GRANDFATHERED else STATUS_ACTIVE
        )
        results = []
        for row in catalog:
            g = grant_entitlement(
                sid, row["entitlement_key"], source=source,
                source_reference=source_reference or plan_key,
                subject_type=st, status=grant_status,
                expires_at=period_end, grace_until=grace_until,
                limit_value=row.get("limit_value"),
                limit_period=row.get("limit_period"),
                created_by=actor,
                reason=f"sync_subscription:{plan_key}:{status}",
                metadata={"plan_key": plan_key, "sub_status": status},
                conn=conn,
            )
            results.append(g.get("entitlement_key") or row["entitlement_key"])
        return {"plan_key": plan_key, "granted_keys": results,
                "subscription_status": status}
    finally:
        if owned:
            conn.close()


def reconcile_entitlements(*, subject_type: str = "user",
                           subject_id: Optional[Any] = None,
                           conn=None) -> dict:
    """Sweep: expire grants whose clock has fully run out (past expires_at and
    past grace_until) by moving them from active -> expired. Idempotent and safe
    to run on a schedule. Optionally scoped to one subject.

    Does not delete anything; expiry is a status transition so history is kept.
    """
    owned = conn is None
    if owned:
        conn = db.connect()
    now = _utc_now()
    try:
        _begin(conn)
        sql = ("SELECT * FROM business_os_ent_grants WHERE status = ? ")
        params: list = [STATUS_ACTIVE]
        if subject_id is not None:
            sql += "AND subject_type=? AND subject_id=? "
            params += [subject_type, _sid(subject_id)]
        rows = [_row_to_dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]
        expired = 0
        for g in rows:
            exp = _to_dt(g.get("expires_at"))
            if exp is None or now < exp:
                continue
            grace = _to_dt(g.get("grace_until"))
            if grace is not None and now < grace:
                continue
            conn.execute(
                "UPDATE business_os_ent_grants SET status=?, updated_at=? WHERE id=?",
                (STATUS_EXPIRED, _now_iso(), g["id"]),
            )
            _audit(conn, subject_type=g["subject_type"], subject_id=g["subject_id"],
                   key=g["entitlement_key"], action="reconcile", actor="system",
                   reason="expired_by_clock", before={"status": STATUS_ACTIVE},
                   after={"status": STATUS_EXPIRED})
            expired += 1
        _commit(conn)
        return {"expired": expired, "scanned": len(rows)}
    except Exception:
        _rollback(conn)
        raise
    finally:
        if owned:
            conn.close()


def ensure_schema(conn=None) -> None:
    """Re-export so callers can ``from ...entitlements import service; service.ensure_schema()``."""
    _schema.ensure_ready(conn)
