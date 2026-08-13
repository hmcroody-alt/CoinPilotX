"""Sentinel deterministic correlation (Stage 8).

Rules are code, thresholds are explicit, and no LLM output participates in
the decision (SC2). A single high-risk signal is never sufficient to open an
incident (SC8): every rule requires either N repetitions or 2+ DISTINCT
signal types before it fires.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from services.sentinel import incidents, store
from services.sentinel.identity import SENTINEL_CORRELATOR


@dataclass(frozen=True)
class CorrelationRule:
    rule_id: str
    description: str
    category: str                 # sentinel_events.category to scan
    event_types: tuple[str, ...]  # which event types count as signals
    window_minutes: int
    min_events: int               # repetitions required
    min_distinct_types: int       # distinct signal types required
    incident_type: str
    severity: str

    def __post_init__(self):
        # SC8: a rule that could fire on one occurrence of one signal is invalid.
        if self.min_events < 2 and self.min_distinct_types < 2:
            raise ValueError(
                f"rule {self.rule_id}: single-signal rules are forbidden (SC8)")
        if self.window_minutes <= 0 or self.window_minutes > 24 * 60:
            raise ValueError(f"rule {self.rule_id}: window must be bounded (SC14)")


RULES: tuple[CorrelationRule, ...] = (
    CorrelationRule(
        "CR1", "Repeated authentication failures for one subject",
        "AUTH", ("login_failed", "password_reset_failed", "mfa_failed"),
        window_minutes=30, min_events=5, min_distinct_types=1,
        incident_type="ACCOUNT_TAKEOVER", severity="high"),
    CorrelationRule(
        "CR2", "Unusual device AND unusual location for the same subject",
        "SECURITY", ("unusual_device", "unusual_country"),
        window_minutes=60, min_events=2, min_distinct_types=2,
        incident_type="ACCOUNT_TAKEOVER", severity="high"),
    CorrelationRule(
        "CR3", "Repeated invariant violations in the financial domain",
        "LEDGER", ("invariant_violation",),
        window_minutes=120, min_events=2, min_distinct_types=1,
        incident_type="INVARIANT_VIOLATION", severity="critical"),
    CorrelationRule(
        "CR4", "Provider capability failures across multiple capabilities",
        "PROVIDER", ("capability_down", "capability_degraded"),
        window_minutes=30, min_events=3, min_distinct_types=1,
        incident_type="PROVIDER_OUTAGE", severity="medium"),
    CorrelationRule(
        "CR5", "Prompt-injection attempts plus anomalous UNDX activity",
        "UNDX", ("injection_detected", "policy_denied"),
        window_minutes=60, min_events=3, min_distinct_types=2,
        incident_type="AI_SAFETY", severity="high"),
)


def _incident_key(rule: CorrelationRule, subject_id: str, bucket: str) -> str:
    basis = f"{rule.rule_id}|{subject_id}|{bucket}"
    return "corr_" + hashlib.sha256(basis.encode()).hexdigest()[:24]


def evaluate_rule(rule: CorrelationRule, conn=None) -> list[dict]:
    """Scan the rule's window and open incidents for qualifying subjects.
    Deterministic: same events in, same incidents out. Idempotent via
    time-bucketed incident keys."""
    placeholders = ",".join("?" for _ in rule.event_types)
    findings: list[dict] = []
    # Cutoff computed in Python so the comparison is a portable string
    # compare on both SQLite and PostgreSQL (occurred_at is stored as
    # 'YYYY-MM-DD HH:MM:SS' UTC).
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=rule.window_minutes)
              ).strftime("%Y-%m-%d %H:%M:%S")
    with store.connection(conn) as c:
        cur = c.cursor()
        cur.execute(
            f"""SELECT subject_id, COUNT(*) AS n, COUNT(DISTINCT event_type) AS distinct_types,
                       MAX(occurred_at) AS latest
                FROM sentinel_events
                WHERE category = ? AND event_type IN ({placeholders})
                  AND subject_id IS NOT NULL
                  AND occurred_at >= ?
                GROUP BY subject_id""",
            (rule.category, *rule.event_types, cutoff))
        rows = cur.fetchall()
        for row in rows:
            subject_id, n, distinct_types = str(row[0]), int(row[1]), int(row[2])
            if n < rule.min_events or distinct_types < rule.min_distinct_types:
                continue
            # Bucket by day so a persisting condition doesn't spam new incidents.
            bucket = str(row[3] or "")[:10]
            key = _incident_key(rule, subject_id, bucket)
            ref = incidents.open_incident(
                key, rule.incident_type, rule.severity,
                f"[{rule.rule_id}] {rule.description} (subject={subject_id}, "
                f"events={n}, distinct={distinct_types})",
                SENTINEL_CORRELATOR.actor_id,
                detail={"rule_id": rule.rule_id, "subject_id": subject_id,
                        "event_count": n, "distinct_types": distinct_types},
                conn=c)
            findings.append({"rule_id": rule.rule_id, "subject_id": subject_id,
                             "incident_key": key, "created": ref.created})
    return findings


def run_all(conn=None) -> list[dict]:
    findings: list[dict] = []
    for rule in RULES:
        findings.extend(evaluate_rule(rule, conn=conn))
    return findings
