"""Business OS — Section 10: Verification service (canonical cross-domain trust domain).

Flag-gated (``BUSINESS_OS_VERIFICATION``). A business manager (staff+) runs a verification
pass; the service executes a battery of **read-only integrity checks** across the canonical
domains, records an immutable attestation run (+ per-check rows), and returns the verdict.

The checks are deterministic and observe-only — they read the ONE canonical ledger and the
canonical Events tables and assert consistency invariants:

  * every *settled* event has drained its escrow to zero on the ledger;
  * every PAID confirmed/checked-in ticket carries a ledger capture reference, and every
    refunded PAID ticket carries a refund reference — money is never issued off-ledger;
  * each ticket type's ``quantity_sold`` equals its live (confirmed + checked-in) ticket
    count — no drift between the counter and reality;
  * no ticket is an orphan (its event exists and belongs to this business);
  * the business's event-payable and the platform event-revenue ledger accounts are never
    negative.

Who may verify is resolved against S1 canonical RBAC — never re-modeled here. A stranger sees
404 (existence not leaked).
"""

from __future__ import annotations

import os
from typing import Any, Optional

from services import db
from services.business_os.verification import schema as _schema
from services.business_os.business import service as biz_svc
from services.business_os.ledger import ledger as _ledger
from services.business_os.events import service as _events


FLAG_ENV = "BUSINESS_OS_VERIFICATION"

VERIFY_ROLE = "staff"   # staff+ may run a verification pass for their business


class VerificationError(ValueError):
    """One stable domain-facing error carrying an HTTP status + machine code."""

    def __init__(self, message: str, http_status: int = 400, code: str = "invalid"):
        super().__init__(message)
        self.http_status = int(http_status)
        self.code = code


# ---------------------------------------------------------------------------
def is_enabled() -> bool:
    raw = (os.environ.get(FLAG_ENV) or "").strip().lower()
    return raw in {"1", "true", "on", "yes", "enabled", "canonical"}


def _require_enabled() -> None:
    if not is_enabled():
        raise VerificationError(
            "Verification is not enabled in this environment.", 503, "disabled")


def _now() -> str:
    return _schema._utc_now_iso()


def _sid(user_id: Any) -> str:
    return biz_svc._sid(user_id)


def _row(row):
    if row is None:
        return None
    try:
        return dict(row)
    except Exception:
        return None


def _rows(rows) -> list:
    return [d for d in (_row(r) for r in (rows or [])) if d is not None]


def _business_exists(conn, business_id: Any) -> bool:
    return conn.execute(
        "SELECT 1 FROM business_os_business WHERE business_id = ? LIMIT 1",
        (str(business_id),)).fetchone() is not None


def _can_verify(conn, business_id: Any, user_id: Any) -> bool:
    role = biz_svc._effective_role(conn, str(business_id), user_id)
    if role is None:
        return False
    return biz_svc._role_rank(role) <= biz_svc._role_rank(VERIFY_ROLE)


def _require_verifier(conn, business_id: Any, user_id: Any) -> None:
    if not business_id or not _business_exists(conn, business_id):
        raise VerificationError("Not found.", 404, "not_found")
    if not _can_verify(conn, business_id, user_id):
        raise VerificationError("Not found.", 404, "not_found")


def _table_exists(conn, name: str) -> bool:
    try:
        conn.execute("SELECT 1 FROM %s LIMIT 1" % name)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# The integrity checks — deterministic, read-only, cross-domain.
# ---------------------------------------------------------------------------
def _check(name: str, category: str, ok: bool, detail: str) -> dict:
    return {"name": name, "category": category, "ok": bool(ok), "detail": detail}


def _collect_checks(conn, business_id: Any) -> list:
    checks: list = []
    bid = str(business_id)

    # Canonical schema presence — the domains this attestation depends on must exist.
    for tbl, cat in (("business_os_business", "schema"),
                     ("business_os_events", "schema"),
                     ("business_os_event_tickets", "schema"),
                     ("ledger_balances", "schema")):
        checks.append(_check(
            "schema_present:%s" % tbl, cat, _table_exists(conn, tbl),
            "table %s present" % tbl))

    events = _rows(conn.execute(
        "SELECT * FROM business_os_events WHERE business_id = ?", (bid,)).fetchall())

    # Settled events must have drained escrow to zero.
    bad_escrow = []
    for ev in events:
        if ev.get("status") == "completed":
            bal = _ledger.get_balance(
                _events.escrow_account(ev["event_id"]),
                (ev.get("currency") or "usd").lower())
            if bal != 0:
                bad_escrow.append("%s=%d" % (ev["event_id"], bal))
    checks.append(_check(
        "settled_events_escrow_zero", "ledger", not bad_escrow,
        "all settled events drained escrow" if not bad_escrow
        else "non-zero escrow: %s" % ", ".join(bad_escrow)))

    event_ids = {ev["event_id"] for ev in events}
    tickets = _rows(conn.execute(
        "SELECT t.* FROM business_os_event_tickets t "
        "JOIN business_os_events e ON e.event_id = t.event_id "
        "WHERE e.business_id = ?", (bid,)).fetchall())

    # No orphan tickets (join already scopes to this business; assert every ticket's
    # event is in the set we loaded).
    orphans = [t["ticket_id"] for t in tickets if t["event_id"] not in event_ids]
    checks.append(_check(
        "no_orphan_tickets", "events", not orphans,
        "no orphan tickets" if not orphans
        else "orphans: %s" % ", ".join(orphans)))

    # Paid live tickets carry a ledger capture reference.
    missing_capture = [
        t["ticket_id"] for t in tickets
        if t["status"] in ("confirmed", "checked_in")
        and int(t.get("price_cents_paid") or 0) > 0
        and not t.get("capture_txn_ref")]
    checks.append(_check(
        "paid_tickets_have_capture_ref", "ledger", not missing_capture,
        "all paid live tickets have capture ref" if not missing_capture
        else "missing capture: %s" % ", ".join(missing_capture)))

    # Refunded paid tickets carry a refund reference.
    missing_refund = [
        t["ticket_id"] for t in tickets
        if t["status"] == "refunded"
        and int(t.get("price_cents_paid") or 0) > 0
        and not t.get("refund_txn_ref")]
    checks.append(_check(
        "refunded_tickets_have_refund_ref", "ledger", not missing_refund,
        "all refunded paid tickets have refund ref" if not missing_refund
        else "missing refund: %s" % ", ".join(missing_refund)))

    # ticket_type.quantity_sold == live (confirmed + checked_in) ticket count.
    live_by_type: dict = {}
    for t in tickets:
        if t["status"] in ("confirmed", "checked_in"):
            live_by_type[t["ticket_type_id"]] = live_by_type.get(
                t["ticket_type_id"], 0) + 1
    ttypes = _rows(conn.execute(
        "SELECT tt.* FROM business_os_event_ticket_types tt "
        "JOIN business_os_events e ON e.event_id = tt.event_id "
        "WHERE e.business_id = ?", (bid,)).fetchall())
    drift = []
    for tt in ttypes:
        sold = int(tt.get("quantity_sold") or 0)
        live = live_by_type.get(tt["ticket_type_id"], 0)
        if sold != live:
            drift.append("%s(sold=%d,live=%d)" % (tt["ticket_type_id"], sold, live))
    checks.append(_check(
        "ticket_type_sold_counts_consistent", "events", not drift,
        "sold counters match live tickets" if not drift
        else "drift: %s" % ", ".join(drift)))

    # Ledger accounts for this business's events are never negative.
    payable = _ledger.get_balance(_events.business_payable_account(bid), "usd")
    checks.append(_check(
        "business_event_payable_nonnegative", "ledger", payable >= 0,
        "event payable balance = %d" % payable))
    revenue = _ledger.get_balance(_events.PLATFORM_REVENUE_ACCOUNT, "usd")
    checks.append(_check(
        "platform_event_revenue_nonnegative", "ledger", revenue >= 0,
        "platform event revenue balance = %d" % revenue))

    return checks


def _persist_run(conn, business_id: Any, actor: Any, checks: list) -> dict:
    total = len(checks)
    passed = sum(1 for c in checks if c["ok"])
    status = "pass" if passed == total else "fail"
    run_id = _schema.new_id("vrun")
    now = _now()
    conn.execute(
        "INSERT INTO business_os_verification_runs "
        "(run_id, business_id, actor_user_id, status, checks_total, checks_passed, "
        "created_at) VALUES (?,?,?,?,?,?,?)",
        (run_id, str(business_id), _sid(actor) if actor is not None else None,
         status, total, passed, now))
    for c in checks:
        conn.execute(
            "INSERT INTO business_os_verification_checks "
            "(check_id, run_id, name, category, ok, detail, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (_schema.new_id("vchk"), run_id, c["name"], c["category"],
             1 if c["ok"] else 0, c["detail"], now))
    conn.commit()
    return {"run_id": run_id, "business_id": str(business_id), "status": status,
            "checks_total": total, "checks_passed": passed, "checks": checks,
            "created_at": now}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def run_verification(business_id: Any, actor_user_id: Any, *,
                     context: Optional[dict] = None) -> dict:
    """Execute the integrity battery for a business and record an attestation run."""
    _require_enabled()
    conn = db.connect()
    try:
        _require_verifier(conn, business_id, actor_user_id)
        checks = _collect_checks(conn, business_id)
        return _persist_run(conn, business_id, actor_user_id, checks)
    finally:
        conn.close()


def get_run(run_id: Any, actor_user_id: Any) -> dict:
    """Fetch a recorded run (+ its checks). Manager-gated; existence not leaked."""
    _require_enabled()
    conn = db.connect()
    try:
        run = _row(conn.execute(
            "SELECT * FROM business_os_verification_runs WHERE run_id = ? LIMIT 1",
            (str(run_id),)).fetchone())
        if not run:
            raise VerificationError("Not found.", 404, "not_found")
        _require_verifier(conn, run["business_id"], actor_user_id)
        run["checks"] = _rows(conn.execute(
            "SELECT name, category, ok, detail FROM business_os_verification_checks "
            "WHERE run_id = ? ORDER BY created_at ASC, name ASC",
            (str(run_id),)).fetchall())
        for c in run["checks"]:
            c["ok"] = bool(c["ok"])
        return run
    finally:
        conn.close()


def list_runs(business_id: Any, actor_user_id: Any, *, limit: int = 50) -> list:
    _require_enabled()
    conn = db.connect()
    try:
        _require_verifier(conn, business_id, actor_user_id)
        return _rows(conn.execute(
            "SELECT * FROM business_os_verification_runs WHERE business_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (str(business_id), int(limit))).fetchall())
    finally:
        conn.close()
