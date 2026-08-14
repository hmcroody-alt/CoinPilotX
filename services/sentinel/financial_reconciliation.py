"""Sentinel Mission 5 — read-only ledger reconciliation (Stage 19).

Compares expected vs observed values across financial sources and records a
status: MATCH / MISMATCH / PARTIAL / UNKNOWN / STALE. Reconciliation NEVER
repairs anything — a mismatch is recorded and escalated, and the numbers on
both sides remain exactly as they were found.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from services.sentinel import financial_entities, incidents, store
from services.sentinel.constitution import CONSTITUTION_VERSION

STATUSES = ("MATCH", "MISMATCH", "PARTIAL", "UNKNOWN", "STALE")

_TS = "%Y-%m-%d %H:%M:%S"


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime(_TS)


def reconcile(scope: str, subject_ref: str,
              expected_cents: Optional[int],
              observed_cents: Optional[int],
              *, components: Optional[Dict[str, Any]] = None,
              observed_at: str = "", stale: bool = False,
              partial: bool = False) -> Dict[str, Any]:
    """Pure comparison. The caller supplies the two sides (read elsewhere,
    read-only); Sentinel classifies the relationship honestly:

    - either side None            → UNKNOWN (never assumed equal)
    - stale inputs                → STALE  (never silently treated as current)
    - partial component coverage  → PARTIAL
    - equal                       → MATCH
    - different                   → MISMATCH (with the delta)
    """
    financial_entities.parse_ref(subject_ref)
    scope = str(scope or "").strip()
    if not scope:
        raise ValueError("scope is required")
    if stale:
        status, detail = "STALE", "inputs are stale — not treated as current"
    elif expected_cents is None or observed_cents is None:
        status, detail = "UNKNOWN", "one or both sides unavailable"
    elif partial:
        status, detail = "PARTIAL", "component coverage incomplete"
    elif int(expected_cents) == int(observed_cents):
        status, detail = "MATCH", ""
    else:
        delta = int(observed_cents) - int(expected_cents)
        status = "MISMATCH"
        detail = (f"observed {observed_cents}c vs expected {expected_cents}c "
                  f"(delta {delta:+d}c)")
    return {"scope": scope, "subject_ref": subject_ref, "status": status,
            "expected_cents": expected_cents, "observed_cents": observed_cents,
            "components": dict(components or {}), "detail": detail,
            "observed_at": observed_at or _utcnow(),
            "note": "read-only: Sentinel records mismatches, it never repairs them"}


def record(result: Dict[str, Any], conn=None,
           actor_id: str = "service.sentinel.financial_reconciliation") -> int:
    """Persist a reconciliation result; MISMATCH escalates an incident."""
    if result.get("status") not in STATUSES:
        raise ValueError(f"unknown reconciliation status {result.get('status')!r}")
    with store.connection(conn) as c:
        cur = c.cursor()
        cur.execute(
            """INSERT INTO sentinel_financial_reconciliations
               (scope, subject_ref, status, expected_cents, observed_cents,
                components_json, detail, observed_at, deployment_sha,
                policy_version)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (result["scope"], result["subject_ref"], result["status"],
             result.get("expected_cents"), result.get("observed_cents"),
             json.dumps(result.get("components") or {}, default=str),
             str(result.get("detail") or ""),
             str(result.get("observed_at") or _utcnow()),
             store.deployment_sha(), CONSTITUTION_VERSION))
        row_id = int(cur.lastrowid)
        if result["status"] == "MISMATCH":
            key = incidents.dedupe_key(
                "fin-recon", result["scope"], result["subject_ref"])
            incidents.open_incident(
                key, "FINANCIAL_LEDGER_MISMATCH", "high",
                f"Reconciliation mismatch [{result['scope']}] "
                f"{result['subject_ref']}: {str(result.get('detail'))[:120]}",
                actor_id,
                {"subject_ref": result["subject_ref"],
                 "scope": result["scope"],
                 "expected_cents": result.get("expected_cents"),
                 "observed_cents": result.get("observed_cents"),
                 "authority_note": ("mismatch recorded, NOT repaired — "
                                    "correction is an owner decision")},
                conn=c, owner_action_required=True)
    return row_id


def recent(scope: Optional[str] = None, conn=None,
           limit: int = 100) -> List[Dict[str, Any]]:
    limit = max(1, min(int(limit), 500))
    with store.connection(conn) as c:
        cur = c.cursor()
        base = ("SELECT scope, subject_ref, status, expected_cents, "
                "observed_cents, detail, observed_at, recorded_at "
                "FROM sentinel_financial_reconciliations ")
        if scope:
            cur.execute(base + "WHERE scope = ? ORDER BY id DESC LIMIT ?",
                        (scope, limit))
        else:
            cur.execute(base + "ORDER BY id DESC LIMIT ?", (limit,))
        rows = cur.fetchall()
    return [{"scope": r[0], "subject_ref": r[1], "status": r[2],
             "expected_cents": r[3], "observed_cents": r[4], "detail": r[5],
             "observed_at": r[6], "recorded_at": r[7]} for r in rows]


def status_counts(conn=None) -> Dict[str, int]:
    counts = {s: 0 for s in STATUSES}
    with store.connection(conn) as c:
        cur = c.cursor()
        cur.execute("SELECT status, COUNT(*) FROM "
                    "sentinel_financial_reconciliations GROUP BY status")
        for status, n in cur.fetchall():
            if status in counts:
                counts[status] = int(n)
    return counts
