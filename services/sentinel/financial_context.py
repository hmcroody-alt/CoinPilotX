"""Sentinel Mission 5 — SentinelFinancialTransactionContext (Stage 5).

A context is a bundle of REFERENCES and Sentinel-side observations about a
financial entity — never a duplicate of the underlying financial records
(NO second ledger). It is assembled purely from sentinel_* tables.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from services.sentinel import financial_entities, store

CONTEXT_VERSION = "fin-ctx-1"


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def build_context(subject_ref: str, conn=None, limit: int = 50) -> Dict[str, Any]:
    """Assemble the transaction context for one financial entity ref.

    Reads only sentinel_* tables. Absent data yields empty lists and honest
    'UNKNOWN' markers — never fabricated records.
    """
    etype, ident = financial_entities.parse_ref(subject_ref)
    now = _utcnow()
    ctx: Dict[str, Any] = {
        "context_version": CONTEXT_VERSION,
        "subject_ref": subject_ref,
        "entity_type": etype,
        "built_at": now,
        "events": [],
        "incidents": [],
        "risk": None,
        "reconciliations": [],
        "edges": [],
        "note": ("references only — canonical financial records live in the "
                 "platform ledger, not in Sentinel"),
    }
    with store.connection(conn) as c:
        cur = c.cursor()
        cur.execute(
            "SELECT event_id, event_type, category, severity, occurred_at, "
            "source_trust, confidence FROM sentinel_events "
            "WHERE (subject_type = ? AND subject_id = ?) "
            "   OR correlation_keys_json LIKE ? "
            "ORDER BY id DESC LIMIT ?",
            (etype, ident, f'%"{subject_ref}"%', limit))
        ctx["events"] = [
            {"event_id": r[0], "event_type": r[1], "category": r[2],
             "severity": r[3], "occurred_at": r[4], "source_trust": r[5],
             "confidence": r[6]} for r in cur.fetchall()]

        # sentinel_incidents has no subject_ref column; Mission 5 openers
        # always record {"subject_ref": ...} inside detail_json.
        cur.execute(
            "SELECT incident_key, incident_type, severity, state, opened_at "
            "FROM sentinel_incidents WHERE detail_json LIKE ? "
            "ORDER BY id DESC LIMIT ?",
            (f'%"subject_ref": "{subject_ref}"%', limit))
        ctx["incidents"] = [
            {"incident_key": r[0], "incident_type": r[1], "severity": r[2],
             "state": r[3], "opened_at": r[4]} for r in cur.fetchall()]

        cur.execute(
            "SELECT trust_state, risk_score, dimensions_json, reasons_json, "
            "contradicting_json, confidence, observed_at, expires_at "
            "FROM sentinel_financial_risk WHERE subject_ref = ? "
            "ORDER BY id DESC LIMIT 1", (subject_ref,))
        row = cur.fetchone()
        if row:
            expired = bool(row[7] and row[7] <= now)
            ctx["risk"] = {
                "trust_state": "UNKNOWN" if expired else row[0],
                "risk_score": 0.0 if expired else row[1],
                "dimensions": json.loads(row[2] or "{}"),
                "reasons": json.loads(row[3] or "[]"),
                "contradicting_evidence": json.loads(row[4] or "[]"),
                "confidence": row[5],
                "observed_at": row[6],
                "expires_at": row[7],
                "expired": expired,
            }

        cur.execute(
            "SELECT scope, status, expected_cents, observed_cents, detail, "
            "observed_at FROM sentinel_financial_reconciliations "
            "WHERE subject_ref = ? ORDER BY id DESC LIMIT ?",
            (subject_ref, limit))
        ctx["reconciliations"] = [
            {"scope": r[0], "status": r[1], "expected_cents": r[2],
             "observed_cents": r[3], "detail": r[4], "observed_at": r[5]}
            for r in cur.fetchall()]

        cur.execute(
            "SELECT src_type, src_id, edge_type, dst_type, dst_id, weight "
            "FROM sentinel_edges WHERE (src_type = ? AND src_id = ?) "
            "OR (dst_type = ? AND dst_id = ?) LIMIT ?",
            (etype, ident, etype, ident, limit))
        ctx["edges"] = [
            {"src": f"{r[0]}:{r[1]}", "edge_type": r[2],
             "dst": f"{r[3]}:{r[4]}", "weight": r[5]} for r in cur.fetchall()]
    return ctx


def context_summary(subject_ref: str, conn=None) -> Dict[str, Any]:
    """Compact summary: counts only, for surfaces that don't need detail."""
    ctx = build_context(subject_ref, conn=conn)
    return {
        "subject_ref": subject_ref,
        "event_count": len(ctx["events"]),
        "open_incident_count": sum(
            1 for i in ctx["incidents"] if i["state"] not in ("RESOLVED", "CLOSED")),
        "trust_state": (ctx["risk"] or {}).get("trust_state", "UNKNOWN"),
        "reconciliation_statuses": sorted(
            {r["status"] for r in ctx["reconciliations"]}),
    }
