"""Sentinel self-metrics and self-health (Stage 28 + Stage 24 containment).

Sentinel monitors itself with the same rigor it applies to the platform: if
its own store, evidence chain, or ingest path degrades, that is reported —
never hidden (SC5). All failures here are contained: a broken metrics write
must never take down a caller, so record() swallows and reports via return
value rather than raising into hot paths (fail-safe, Stage 24).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from services.sentinel import killswitches, store

log = logging.getLogger("sentinel.observability")

METRICS = (
    "events_ingested", "events_deduped", "incidents_opened",
    "runbooks_executed", "runbooks_denied", "verifications_passed",
    "verifications_failed", "injection_flags", "invariant_violations",
)


def record(metric: str, value: float = 1.0, conn=None) -> bool:
    """Best-effort metric write. Returns False instead of raising (Stage 24)."""
    if metric not in METRICS:
        log.warning("sentinel metric rejected (unknown): %s", metric)
        return False
    try:
        with store.connection(conn) as c:
            c.cursor().execute(
                "INSERT INTO sentinel_metrics (metric, value) VALUES (?, ?)",
                (metric, float(value)))
        return True
    except Exception as exc:
        log.warning("sentinel metric write failed metric=%s error=%s", metric, exc)
        return False


def summary(hours: int = 24, conn=None) -> dict:
    hours = max(1, min(int(hours), 24 * 7))
    try:
        with store.connection(conn) as c:
            cur = c.cursor()
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)
                      ).strftime("%Y-%m-%d %H:%M:%S")
            cur.execute(
                "SELECT metric, COUNT(*), COALESCE(SUM(value), 0) FROM sentinel_metrics "
                "WHERE recorded_at >= ? GROUP BY metric",
                (cutoff,))
            rows = cur.fetchall()
        return {str(r[0]): {"count": int(r[1]), "sum": float(r[2])} for r in rows}
    except Exception as exc:
        return {"error": str(exc)[:200]}


def self_health(conn=None) -> dict:
    """Sentinel's own vital signs. Every probe is independent so one failure
    cannot mask another."""
    health: dict = {"switches": killswitches.switch_state()}
    try:
        with store.connection(conn) as c:
            cur = c.cursor()
            cur.execute("SELECT COUNT(*) FROM sentinel_events")
            health["events_stored"] = int(cur.fetchone()[0])
            cur.execute("SELECT COUNT(*) FROM sentinel_incidents WHERE state NOT IN ('RESOLVED','CLOSED')")
            health["open_incidents"] = int(cur.fetchone()[0])
            cur.execute("SELECT COUNT(*) FROM sentinel_evidence")
            health["evidence_records"] = int(cur.fetchone()[0])
        health["store_ok"] = True
    except Exception as exc:
        health["store_ok"] = False
        health["store_error"] = str(exc)[:200]
    try:
        from services.sentinel import evidence
        chain = evidence.verify_chain(conn=conn)
        health["evidence_chain_ok"] = bool(chain["ok"])
    except Exception as exc:
        health["evidence_chain_ok"] = False
        health["evidence_error"] = str(exc)[:200]
    health["ok"] = bool(health.get("store_ok") and health.get("evidence_chain_ok"))
    return health
