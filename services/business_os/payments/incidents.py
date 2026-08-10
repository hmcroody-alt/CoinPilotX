"""Canonical financial-incident engine (Wave A shared infrastructure).

One table, one write path, for every financial discrepancy the platform can
observe: ledger drift, wallet drift, dead-lettered webhooks, orphaned Stripe
objects, funds stuck in suspense. The design rules are deliberate and strict:

* **Append-only observations.** An incident NEVER mutates the financial record
  it describes. Balances are not "fixed", webhook rows are not re-queued, and
  nothing here moves money. Humans (or explicitly separate remediation code)
  act on incidents; this module only records and tracks them.
* **Idempotent by key.** ``incident_key`` is UNIQUE at the DB level, so the same
  discrepancy reported twice — by a retried worker, a cron overlap, a replayed
  reconcile — lands on one row. The repeat bumps ``updated_at`` and merges
  details instead of duplicating.
* **Resolution requires a note.** A resolved incident with no explanation is
  indistinguishable from a swept-under-the-rug one, so ``resolved`` (and
  ``ignored``) demand a human-written note.

Engine-portable via ``services.db`` (SQLite dev / PostgreSQL prod); does not
import ``bot.py`` so it can be unit tested in isolation.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from services import db

# --- canonical incident types ------------------------------------------------
BALANCE_MISMATCH = "balance_mismatch"
MISSING_WEBHOOK_EVENT = "missing_webhook_event"
DUPLICATE_CREDIT_ATTEMPT = "duplicate_credit_attempt"
ORPHAN_STRIPE_OBJECT = "orphan_stripe_object"
ORPHAN_LOCAL_RECORD = "orphan_local_record"
WEBHOOK_DLQ_EXHAUSTED = "webhook_dlq_exhausted"
PAYOUT_STATE_CONFLICT = "payout_state_conflict"
REFUND_MISMATCH = "refund_mismatch"
SUSPENSE_FUNDS_HELD = "suspense_funds_held"
RECONCILIATION_FAILURE = "reconciliation_failure"
REWARD_DUPLICATE_ATTEMPT = "reward_duplicate_attempt"
NEGATIVE_BALANCE_DETECTED = "negative_balance_detected"

INCIDENT_TYPES = {
    BALANCE_MISMATCH,
    MISSING_WEBHOOK_EVENT,
    DUPLICATE_CREDIT_ATTEMPT,
    ORPHAN_STRIPE_OBJECT,
    ORPHAN_LOCAL_RECORD,
    WEBHOOK_DLQ_EXHAUSTED,
    PAYOUT_STATE_CONFLICT,
    REFUND_MISMATCH,
    SUSPENSE_FUNDS_HELD,
    RECONCILIATION_FAILURE,
    REWARD_DUPLICATE_ATTEMPT,
    NEGATIVE_BALANCE_DETECTED,
}

SEVERITIES = {"info", "warning", "critical"}
DOMAINS = {"seller_payments", "ad_wallet", "rewards", "webhooks", "ledger"}
STATUSES = {"open", "acknowledged", "resolved", "ignored"}
#: Statuses a repeated observation may still refresh. A resolved/ignored
#: incident is a closed book: the same key reported again returns the closed
#: row untouched (callers that want a re-opened investigation must report a new
#: fact, i.e. a new key — the reconcilers bake the observed values into theirs).
_REFRESHABLE = {"open", "acknowledged"}

MAX_LIST_LIMIT = 200
DEFAULT_LIST_LIMIT = 50


class IncidentError(ValueError):
    """Rejected incident write. ``status_code`` maps onto the HTTP layer."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _is_unique_violation(exc: Exception) -> bool:
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


def _row_to_dict(row) -> Optional[dict]:
    if row is None:
        return None
    try:
        return dict(row)
    except Exception:
        return {k: row[k] for k in row.keys()}


def ensure_schema(conn=None) -> None:
    """Create the incidents table if absent. Idempotent; safe at startup."""
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS financial_incidents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                incident_key TEXT NOT NULL UNIQUE,
                incident_type TEXT NOT NULL,
                severity TEXT NOT NULL DEFAULT 'warning',
                domain TEXT NOT NULL,
                summary TEXT NOT NULL,
                details_json TEXT,
                related_object TEXT,
                stripe_ref TEXT,
                status TEXT NOT NULL DEFAULT 'open',
                resolution_note TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                resolved_at TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_financial_incidents_status "
            "ON financial_incidents (status, domain)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_financial_incidents_type "
            "ON financial_incidents (incident_type)"
        )
        if owned:
            conn.commit()
    finally:
        if owned:
            conn.close()


def _default_incident_key(incident_type: str, domain: str, related_object: str,
                          stripe_ref: str, summary: str) -> str:
    anchor = related_object or stripe_ref
    if not anchor:
        anchor = hashlib.sha256((summary or "").encode("utf-8")).hexdigest()[:16]
    return f"{incident_type}:{domain}:{anchor}"


def _parse_details(raw) -> dict:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError):
        return {}


def _get_by_key(conn, incident_key: str) -> Optional[dict]:
    cur = conn.execute(
        "SELECT * FROM financial_incidents WHERE incident_key = ?",
        (incident_key,),
    )
    return _row_to_dict(cur.fetchone())


def _shape(row: Optional[dict]) -> Optional[dict]:
    if row is None:
        return None
    out = dict(row)
    out["details"] = _parse_details(out.pop("details_json", None))
    return out


def open_incident(
    incident_type: str,
    domain: str,
    severity: str = "warning",
    summary: str = "",
    details: Optional[Mapping[str, Any]] = None,
    related_object: Optional[str] = None,
    stripe_ref: Optional[str] = None,
    incident_key: Optional[str] = None,
) -> dict:
    """Record a financial discrepancy, idempotently.

    Same ``incident_key`` twice → one row. If the existing row is still open
    (or acknowledged), the repeat bumps ``updated_at`` and merges ``details``;
    a resolved/ignored row is returned untouched. Returns the incident dict
    plus ``duplicate`` (True on repeat).

    This function observes; it never touches the records it describes.
    """
    if incident_type not in INCIDENT_TYPES:
        raise IncidentError(f"unknown incident_type {incident_type!r}")
    if domain not in DOMAINS:
        raise IncidentError(f"unknown incident domain {domain!r}")
    if severity not in SEVERITIES:
        raise IncidentError(f"unknown incident severity {severity!r}")
    if not summary or not str(summary).strip():
        raise IncidentError("summary is required")

    related_object = str(related_object or "")
    stripe_ref = str(stripe_ref or "")
    key = str(incident_key or "").strip() or _default_incident_key(
        incident_type, domain, related_object, stripe_ref, summary
    )
    new_details = dict(details) if details else {}
    now = _utc_now_iso()

    ensure_schema()
    conn = db.connect()
    try:
        _begin(conn)
        try:
            conn.execute(
                """
                INSERT INTO financial_incidents
                    (incident_key, incident_type, severity, domain, summary,
                     details_json, related_object, stripe_ref, status,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)
                """,
                (
                    key, incident_type, severity, domain, str(summary),
                    json.dumps(new_details) if new_details else None,
                    related_object or None, stripe_ref or None, now, now,
                ),
            )
        except Exception as exc:  # noqa: BLE001
            if not _is_unique_violation(exc):
                _rollback(conn)
                raise
            _rollback(conn)
            existing = _get_by_key(conn, key)
            if existing is None:
                # Unique hit but the row is not visible yet; report duplicate
                # without inventing state.
                return {"incident_key": key, "duplicate": True}
            if existing.get("status") in _REFRESHABLE:
                merged = _parse_details(existing.get("details_json"))
                merged.update(new_details)
                _begin(conn)
                try:
                    conn.execute(
                        "UPDATE financial_incidents "
                        "SET updated_at = ?, details_json = ?, severity = ?, summary = ? "
                        "WHERE incident_key = ?",
                        (
                            _utc_now_iso(),
                            json.dumps(merged) if merged else None,
                            severity, str(summary), key,
                        ),
                    )
                    _commit(conn)
                except Exception:
                    _rollback(conn)
                    raise
                refreshed = _shape(_get_by_key(conn, key)) or {}
                refreshed["duplicate"] = True
                return refreshed
            closed = _shape(existing) or {}
            closed["duplicate"] = True
            return closed
        _commit(conn)
        stored = _shape(_get_by_key(conn, key)) or {}
        stored["duplicate"] = False
        return stored
    except IncidentError:
        raise
    except Exception:
        _rollback(conn)
        raise
    finally:
        conn.close()


def get_incident(incident_id: int, conn=None) -> Optional[dict]:
    owned = conn is None
    if owned:
        ensure_schema()
        conn = db.connect()
    try:
        cur = conn.execute(
            "SELECT * FROM financial_incidents WHERE id = ?",
            (int(incident_id),),
        )
        return _shape(_row_to_dict(cur.fetchone()))
    finally:
        if owned:
            conn.close()


def list_incidents(
    domain: Optional[str] = None,
    status: Optional[str] = None,
    incident_type: Optional[str] = None,
    limit: int = DEFAULT_LIST_LIMIT,
    before_id: Optional[int] = None,
) -> dict:
    """One keyset-paginated page of incidents, newest first.

    Returns ``{"incidents", "next_before_id", "has_more"}``. ``next_before_id``
    feeds the next call's ``before_id``; None on the last page.
    """
    if domain is not None and domain not in DOMAINS:
        raise IncidentError(f"unknown incident domain {domain!r}")
    if status is not None and status not in STATUSES:
        raise IncidentError(f"unknown incident status {status!r}")
    if incident_type is not None and incident_type not in INCIDENT_TYPES:
        raise IncidentError(f"unknown incident_type {incident_type!r}")
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
            raise IncidentError("before_id must be a numeric incident id")

    where = []
    params: list = []
    if domain:
        where.append("domain = ?")
        params.append(domain)
    if status:
        where.append("status = ?")
        params.append(status)
    if incident_type:
        where.append("incident_type = ?")
        params.append(incident_type)
    if cursor_id is not None:
        where.append("id < ?")
        params.append(cursor_id)
    params.append(limit + 1)  # one extra row answers "is there a next page?"

    sql = "SELECT * FROM financial_incidents"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ?"

    ensure_schema()
    conn = db.connect()
    try:
        rows = [_shape(_row_to_dict(r)) for r in conn.execute(sql, tuple(params)).fetchall()]
    finally:
        conn.close()

    has_more = len(rows) > limit
    rows = rows[:limit]
    return {
        "incidents": rows,
        "next_before_id": rows[-1]["id"] if (rows and has_more) else None,
        "has_more": has_more,
    }


def update_incident_status(
    incident_id: int,
    status: str,
    resolution_note: str = "",
    actor: str = "",
) -> dict:
    """Move an incident through its workflow. Resolving/ignoring needs a note.

    The status change is the ONLY mutation this module ever performs, and it
    touches nothing but the incident row itself.
    """
    if status not in STATUSES:
        raise IncidentError(f"unknown incident status {status!r}")
    note = str(resolution_note or "").strip()
    if status in {"resolved", "ignored"} and not note:
        raise IncidentError(
            f"a resolution note is required to mark an incident {status}"
        )

    ensure_schema()
    conn = db.connect()
    try:
        existing = get_incident(incident_id, conn=conn)
        if existing is None:
            raise IncidentError(f"incident {incident_id} not found", 404)
        now = _utc_now_iso()
        resolved_at = now if status == "resolved" else None
        stamped_note = note
        if note and actor:
            stamped_note = f"{note} [by {actor}]"
        _begin(conn)
        try:
            conn.execute(
                "UPDATE financial_incidents "
                "SET status = ?, resolution_note = ?, updated_at = ?, resolved_at = ? "
                "WHERE id = ?",
                (
                    status,
                    stamped_note or existing.get("resolution_note"),
                    now, resolved_at, int(incident_id),
                ),
            )
            _commit(conn)
        except Exception:
            _rollback(conn)
            raise
        return get_incident(incident_id, conn=conn) or {}
    finally:
        conn.close()


def counts_by_status() -> dict:
    """``{status: count}`` over all incidents, zero-filled for every status."""
    ensure_schema()
    conn = db.connect()
    try:
        cur = conn.execute(
            "SELECT status, COUNT(*) AS n FROM financial_incidents GROUP BY status"
        )
        rows = [_row_to_dict(r) for r in cur.fetchall()]
    finally:
        conn.close()
    out = {status: 0 for status in STATUSES}
    for row in rows:
        key = str(row.get("status") or "")
        if key:
            out[key] = int(row.get("n") or 0)
    return out
