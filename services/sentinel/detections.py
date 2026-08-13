"""Sentinel deterministic incident rules (Mission 2, Stages 21–22).

Every rule here is SQL + arithmetic over existing platform tables and
sentinel's own stores. No LLM participates (SC2/SC8), no rule takes any
action beyond opening an incident and recording health — no restarts, no
blocks, no bans, no session invalidation (SC3: observation before action).

Every rule:
- reads bounded windows with explicit thresholds,
- tolerates absent source tables (SKIPPED, not crashed — Stage 24),
- opens incidents through the deterministic dedupe key (rule|subject|day),
- records what it MEASURED and against WHICH threshold, so an operator can
  re-derive the verdict by hand.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from services.sentinel import health, incidents, store
from services.sentinel.identity import SENTINEL_CORRELATOR

_TS = "%Y-%m-%d %H:%M:%S"

# Explicit thresholds — the rulebook, in one place.
STALE_WORKER_MINUTES = 15
DEAD_LETTER_SPIKE_THRESHOLD = 10
FAILED_LOGIN_SPIKE_THRESHOLD = 8       # per subject, per window
FAILED_LOGIN_WINDOW_MINUTES = 30
RECOVERY_ABUSE_THRESHOLD = 5           # invalid-email recovery attempts
RECOVERY_ABUSE_WINDOW_MINUTES = 60
ADMIN_ACTION_SPIKE_THRESHOLD = 30      # actions by one admin, per window
ADMIN_ACTION_WINDOW_MINUTES = 30


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _cutoff(minutes: int, now: datetime | None = None) -> str:
    return ((now or _utcnow()) - timedelta(minutes=minutes)).strftime(_TS)


def _day(now: datetime | None = None) -> str:
    return (now or _utcnow()).strftime("%Y-%m-%d")


def _rows(cur, sql: str, params=()):
    try:
        cur.execute(sql, params)
        return cur.fetchall()
    except Exception:
        return None  # source absent → rule reports skipped, never crashes


def _open(c, rule_id: str, subject: str, incident_type: str, severity: str,
          title: str, measurement: str, threshold: str, now=None,
          owner_action: bool = False) -> dict:
    key = incidents.dedupe_key(rule_id, subject, _day(now))
    ref = incidents.open_incident(
        key, incident_type, severity, title, SENTINEL_CORRELATOR.actor_id,
        detail={"rule_id": rule_id, "subject": subject,
                "measurement": measurement, "threshold": threshold},
        owner_action_required=owner_action, conn=c)
    return {"rule_id": rule_id, "subject": subject, "incident_key": key,
            "created": ref.created, "measurement": measurement,
            "threshold": threshold}


# ---------------------------------------------------------------------------
# Operational rules (Stage 21)
# ---------------------------------------------------------------------------

def detect_stale_workers(conn=None, *, now: datetime | None = None) -> dict:
    """Workers whose last_success_at is older than the freshness window.
    Also records a health snapshot per worker — MEASURED, with expiry."""
    now = now or _utcnow()
    findings, skipped = [], False
    with store.connection(conn) as c:
        rows = _rows(c.cursor(),
                     "SELECT worker_name, last_run_at, last_success_at, error_count, "
                     "last_error FROM alert_worker_heartbeat")
        if rows is None:
            return {"rule": "stale_worker", "skipped": True, "findings": []}
        cutoff = _cutoff(STALE_WORKER_MINUTES, now)
        for row in rows:
            name = str(row[0] or "unknown")
            last_success = str(row[2] or "").replace("T", " ")[:19]
            stale = (not last_success) or last_success < cutoff
            health.record(health.HealthSnapshot(
                component=f"worker:{name}",
                status="FAILED" if stale else "HEALTHY",
                source_trust="MEASURED",
                observed_at=now.strftime(_TS),
                measurement=f"last_success_at={last_success or 'never'}",
                threshold=f"fresh within {STALE_WORKER_MINUTES}m"), conn=c)
            if stale:
                findings.append(_open(
                    c, "OP1_STALE_WORKER", f"worker:{name}",
                    "OPERATIONAL_DEGRADATION", "medium",
                    f"Worker {name} has no successful run since "
                    f"{last_success or 'ever'}",
                    f"last_success_at={last_success or 'never'}",
                    f"fresh within {STALE_WORKER_MINUTES}m", now=now,
                    owner_action=True))
    return {"rule": "stale_worker", "skipped": skipped, "findings": findings}


def detect_dead_letter_spike(conn=None, *, now: datetime | None = None) -> dict:
    """Dead-lettered outbound messages piling up in failed_email_queue."""
    now = now or _utcnow()
    with store.connection(conn) as c:
        rows = _rows(c.cursor(),
                     "SELECT COUNT(*) FROM failed_email_queue WHERE status = 'dead_letter'")
        if rows is None:
            return {"rule": "dead_letter_spike", "skipped": True, "findings": []}
        count = int(rows[0][0] or 0)
        findings = []
        if count >= DEAD_LETTER_SPIKE_THRESHOLD:
            findings.append(_open(
                c, "OP2_DEAD_LETTER_SPIKE", "queue:failed_email_queue",
                "OPERATIONAL_DEGRADATION", "medium",
                f"{count} dead-lettered messages in failed_email_queue",
                f"dead_letter_count={count}",
                f"< {DEAD_LETTER_SPIKE_THRESHOLD}", now=now, owner_action=True))
    return {"rule": "dead_letter_spike", "skipped": False, "findings": findings}


def detect_provider_degradation(conn=None, *, now: datetime | None = None) -> dict:
    """Providers with any capability recorded down/degraded in sentinel's own
    capability truth table (which fails closed to 'unknown', never 'up')."""
    now = now or _utcnow()
    findings = []
    with store.connection(conn) as c:
        cur = c.cursor()
        rows = _rows(cur,
                     "SELECT provider, capability, status, observed_at "
                     "FROM sentinel_provider_capabilities "
                     "WHERE status IN ('down','degraded') ORDER BY provider")
        if rows is None:
            return {"rule": "provider_degraded", "skipped": True, "findings": []}
        by_provider: dict[str, list] = {}
        for row in rows:
            by_provider.setdefault(str(row[0]), []).append(
                (str(row[1]), str(row[2])))
        for provider, caps in by_provider.items():
            worst = "down" if any(s == "down" for _, s in caps) else "degraded"
            findings.append(_open(
                c, "OP3_PROVIDER_DEGRADED", f"provider:{provider}",
                "PROVIDER_OUTAGE", "high" if worst == "down" else "medium",
                f"Provider {provider}: {len(caps)} capability(ies) {worst} "
                f"({', '.join(f'{cap}={s}' for cap, s in caps)})",
                json.dumps(dict(caps)), "all capabilities up", now=now))
    return {"rule": "provider_degraded", "skipped": False, "findings": findings}


def detect_deployment_mismatch(conn=None, *, now: datetime | None = None) -> dict:
    """Two different deployment SHAs writing sentinel events inside one hour —
    either a stuck rollout or two code versions running at once."""
    now = now or _utcnow()
    with store.connection(conn) as c:
        cur = c.cursor()
        rows = _rows(cur,
                     "SELECT DISTINCT deployment_sha FROM sentinel_events "
                     "WHERE recorded_at >= ? AND deployment_sha != 'unknown' LIMIT 10",
                     (_cutoff(60, now),))
        if rows is None:
            return {"rule": "deployment_mismatch", "skipped": True, "findings": []}
        shas = sorted({str(r[0]) for r in rows})
        findings = []
        if len(shas) > 1:
            findings.append(_open(
                c, "OP4_DEPLOYMENT_MISMATCH", "deployment:multiple",
                "OPERATIONAL_DEGRADATION", "medium",
                f"{len(shas)} deployment SHAs active within 60m: "
                f"{', '.join(s[:12] for s in shas)}",
                f"distinct_shas={len(shas)} ({', '.join(s[:12] for s in shas)})",
                "exactly 1 active SHA", now=now, owner_action=True))
    return {"rule": "deployment_mismatch", "skipped": False, "findings": findings}


# ---------------------------------------------------------------------------
# Security rules (Stage 22) — READ-ONLY: no blocks, no bans, no invalidation.
# ---------------------------------------------------------------------------

def detect_failed_login_spike(conn=None, *, now: datetime | None = None) -> dict:
    """Subjects with a burst of failed logins in the bridged event stream."""
    now = now or _utcnow()
    with store.connection(conn) as c:
        cur = c.cursor()
        rows = _rows(cur,
                     "SELECT subject_id, COUNT(*) FROM sentinel_events "
                     "WHERE category = 'AUTH' AND event_type = 'login_failed' "
                     "AND subject_id IS NOT NULL AND occurred_at >= ? "
                     "GROUP BY subject_id HAVING COUNT(*) >= ?",
                     (_cutoff(FAILED_LOGIN_WINDOW_MINUTES, now),
                      FAILED_LOGIN_SPIKE_THRESHOLD))
        if rows is None:
            return {"rule": "failed_login_spike", "skipped": True, "findings": []}
        findings = [
            _open(c, "SEC1_FAILED_LOGIN_SPIKE", f"user:{row[0]}",
                  "ACCOUNT_TAKEOVER", "high",
                  f"{int(row[1])} failed logins for subject {row[0]} within "
                  f"{FAILED_LOGIN_WINDOW_MINUTES}m",
                  f"failed_logins={int(row[1])}",
                  f"< {FAILED_LOGIN_SPIKE_THRESHOLD} per {FAILED_LOGIN_WINDOW_MINUTES}m",
                  now=now)
            for row in rows]
    return {"rule": "failed_login_spike", "skipped": False, "findings": findings}


def detect_recovery_abuse(conn=None, *, now: datetime | None = None) -> dict:
    """Repeated password-recovery attempts against invalid/unknown emails —
    enumeration probing. Observation only; nothing is blocked."""
    now = now or _utcnow()
    with store.connection(conn) as c:
        cur = c.cursor()
        rows = _rows(cur,
                     "SELECT COALESCE(network_ref, subject_id), COUNT(*) FROM sentinel_events "
                     "WHERE category = 'AUTH' AND event_type IN "
                     "('password_reset_invalid_email','password_reset_no_match') "
                     "AND occurred_at >= ? "
                     "GROUP BY COALESCE(network_ref, subject_id) HAVING COUNT(*) >= ?",
                     (_cutoff(RECOVERY_ABUSE_WINDOW_MINUTES, now),
                      RECOVERY_ABUSE_THRESHOLD))
        if rows is None:
            return {"rule": "recovery_abuse", "skipped": True, "findings": []}
        findings = [
            _open(c, "SEC2_RECOVERY_ABUSE", str(row[0] or "unattributed"),
                  "ABUSE", "medium",
                  f"{int(row[1])} invalid-email recovery attempts from "
                  f"{row[0] or 'unattributed source'} within "
                  f"{RECOVERY_ABUSE_WINDOW_MINUTES}m",
                  f"invalid_recovery_attempts={int(row[1])}",
                  f"< {RECOVERY_ABUSE_THRESHOLD} per {RECOVERY_ABUSE_WINDOW_MINUTES}m",
                  now=now)
            for row in rows]
    return {"rule": "recovery_abuse", "skipped": False, "findings": findings}


def detect_admin_action_anomaly(conn=None, *, now: datetime | None = None) -> dict:
    """One admin identity performing an unusual volume of audited actions.
    Anomalous ≠ guilty: the incident asks a human to look, nothing more."""
    now = now or _utcnow()
    with store.connection(conn) as c:
        cur = c.cursor()
        rows = _rows(cur,
                     "SELECT actor_id, COUNT(*) FROM sentinel_events "
                     "WHERE category = 'ADMIN' AND event_type = 'admin_action' "
                     "AND occurred_at >= ? GROUP BY actor_id HAVING COUNT(*) >= ?",
                     (_cutoff(ADMIN_ACTION_WINDOW_MINUTES, now),
                      ADMIN_ACTION_SPIKE_THRESHOLD))
        if rows is None:
            return {"rule": "admin_action_anomaly", "skipped": True, "findings": []}
        findings = [
            _open(c, "SEC3_ADMIN_ACTION_ANOMALY", str(row[0]),
                  "SECURITY_INTRUSION", "high",
                  f"{int(row[1])} audited admin actions by {row[0]} within "
                  f"{ADMIN_ACTION_WINDOW_MINUTES}m",
                  f"admin_actions={int(row[1])}",
                  f"< {ADMIN_ACTION_SPIKE_THRESHOLD} per {ADMIN_ACTION_WINDOW_MINUTES}m",
                  now=now, owner_action=True)
            for row in rows]
    return {"rule": "admin_action_anomaly", "skipped": False, "findings": findings}


ALL_RULES = (
    detect_stale_workers,
    detect_dead_letter_spike,
    detect_provider_degradation,
    detect_deployment_mismatch,
    detect_failed_login_spike,
    detect_recovery_abuse,
    detect_admin_action_anomaly,
)


def run_all(conn=None, *, now: datetime | None = None) -> list[dict]:
    """Run every rule; one rule's failure never blocks the others (Stage 24)."""
    results = []
    for rule in ALL_RULES:
        try:
            results.append(rule(conn=conn, now=now))
        except Exception as exc:  # noqa: BLE001 — containment by design
            results.append({"rule": rule.__name__, "skipped": True,
                            "error": str(exc)[:200], "findings": []})
    return results
