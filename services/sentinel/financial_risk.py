"""Sentinel Mission 5 — multidimensional financial risk (Stages 7–9, 21–24).

RISK != GUILT. A risk assessment is a bounded, explainable, expiring,
decaying, contradictable score over named dimensions. There is no permanent
fraud label anywhere: every row expires, and expired rows read as UNKNOWN.

Guards baked in:
- HIGH_RISK (score ≥ 0.7) requires ≥ 2 independent elevated dimensions AND
  non-empty reasons; otherwise the score is capped at 0.69 (no opaque
  single-signal condemnation).
- Contradicting evidence is first-class and reduces the score.
- External-provider-only risk is capped at EXTERNAL_ONLY_RISK_CAP = 0.6
  (Mission 4, Stage 24): a vendor verdict alone can never make HIGH_RISK.
- Velocity windows are bounded to ≤ 7 days (no forever-memory profiling).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field as dc_field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from services.sentinel import store
from services.sentinel.constitution import CONSTITUTION_VERSION
from services.sentinel.external_fusion import EXTERNAL_ONLY_RISK_CAP

POLICY_VERSION = "finrisk-1"
_TS = "%Y-%m-%d %H:%M:%S"

ENTITY_ROLES = ("BUYER", "SELLER", "ADVERTISER", "USER")

TRUST_STATES = ("NORMAL", "WATCH", "ELEVATED", "HIGH_RISK")

# Named, explainable dimensions per role. Weights sum to 1.0 per role.
DIMENSIONS: Dict[str, Dict[str, float]] = {
    "BUYER": {
        "payment_velocity": 0.20,
        "refund_pattern": 0.20,
        "identity_risk_link": 0.20,
        "payment_method_churn": 0.15,
        "dispute_history": 0.15,
        "coordination_signal": 0.10,
    },
    "SELLER": {
        "payout_pattern": 0.20,
        "order_authenticity": 0.20,
        "identity_risk_link": 0.20,
        "refund_inflow": 0.15,
        "settlement_anomaly": 0.15,
        "coordination_signal": 0.10,
    },
    "ADVERTISER": {
        "wallet_funding_pattern": 0.25,
        "spend_anomaly": 0.25,
        "identity_risk_link": 0.20,
        "campaign_churn": 0.15,
        "coordination_signal": 0.15,
    },
    "USER": {
        "payment_velocity": 0.25,
        "identity_risk_link": 0.25,
        "refund_pattern": 0.25,
        "coordination_signal": 0.25,
    },
}

HIGH_RISK_THRESHOLD = 0.70
HIGH_RISK_MIN_DIMENSIONS = 2       # independent elevated dimensions required
ELEVATED_DIMENSION_FLOOR = 0.5     # a dimension counts as elevated at ≥ 0.5
DEFAULT_TTL_HOURS = 72
MAX_VELOCITY_WINDOW_MINUTES = 7 * 24 * 60   # bounded: ≤ 7 days


class FinancialRiskError(ValueError):
    pass


@dataclass(frozen=True)
class FinancialRiskAssessment:
    subject_ref: str
    entity_role: str
    trust_state: str
    risk_score: float
    dimensions: Dict[str, float]
    reasons: List[str]
    contradicting_evidence: List[str]
    evidence_refs: List[str] = dc_field(default_factory=list)
    confidence: float = 0.0
    ttl_hours: int = DEFAULT_TTL_HOURS
    shared_infrastructure_factor: float = 1.0
    capped_by: List[str] = dc_field(default_factory=list)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _fmt(dt: datetime) -> str:
    return dt.strftime(_TS)


def assess(subject_ref: str, entity_role: str,
           dimension_scores: Dict[str, float],
           reasons: List[str],
           *,
           contradicting_evidence: Optional[List[str]] = None,
           evidence_refs: Optional[List[str]] = None,
           confidence: float = 0.5,
           external_only: bool = False,
           shared_infrastructure_factor: float = 1.0,
           ttl_hours: int = DEFAULT_TTL_HOURS) -> FinancialRiskAssessment:
    """Compute a bounded, explainable risk assessment. Pure function."""
    role = str(entity_role).upper()
    if role not in ENTITY_ROLES:
        raise FinancialRiskError(f"unknown entity role {entity_role!r}")
    weights = DIMENSIONS[role]
    unknown = set(dimension_scores) - set(weights)
    if unknown:
        raise FinancialRiskError(
            f"unknown dimensions for {role}: {sorted(unknown)}")

    contradicting = [str(x) for x in (contradicting_evidence or []) if str(x).strip()]
    reasons = [str(r) for r in (reasons or []) if str(r).strip()]
    capped_by: List[str] = []

    # Weighted score over named dimensions; missing dimensions contribute 0
    # (absence of evidence is not evidence of risk).
    score = 0.0
    dims: Dict[str, float] = {}
    for name, weight in weights.items():
        raw = float(dimension_scores.get(name, 0.0))
        raw = max(0.0, min(1.0, raw))
        dims[name] = raw
        score += raw * weight

    # Shared-infrastructure damping (Stage 15): family/office/CGNAT/QA
    # contexts reduce coordination-flavored certainty.
    factor = max(0.0, min(1.0, float(shared_infrastructure_factor)))
    if factor < 1.0:
        score *= factor
        capped_by.append(f"shared_infrastructure_factor={factor}")

    # Contradicting evidence reduces the score — it must MATTER (Stage 22).
    if contradicting:
        reduction = min(0.30, 0.10 * len(contradicting))
        score = max(0.0, score - reduction)
        capped_by.append(f"contradicting_evidence x{len(contradicting)}")

    # External-only risk cap (Stage 24 / Mission 4 preserved).
    if external_only and score > EXTERNAL_ONLY_RISK_CAP:
        score = EXTERNAL_ONLY_RISK_CAP
        capped_by.append(f"external_only_cap={EXTERNAL_ONLY_RISK_CAP}")

    # HIGH_RISK evidence floor: ≥2 independent elevated dimensions AND
    # non-empty reasons, else cap below the threshold.
    elevated = [d for d, v in dims.items() if v >= ELEVATED_DIMENSION_FLOOR]
    if score >= HIGH_RISK_THRESHOLD and (
            len(elevated) < HIGH_RISK_MIN_DIMENSIONS or not reasons):
        score = min(score, HIGH_RISK_THRESHOLD - 0.01)
        capped_by.append("high_risk_evidence_floor")

    score = round(max(0.0, min(1.0, score)), 4)

    if score >= HIGH_RISK_THRESHOLD:
        state = "HIGH_RISK"
    elif score >= 0.5:
        state = "ELEVATED"
    elif score >= 0.25:
        state = "WATCH"
    else:
        state = "NORMAL"

    ttl = int(ttl_hours)
    if ttl <= 0 or ttl > 24 * 30:
        raise FinancialRiskError("ttl_hours must be bounded (0, 720]")

    return FinancialRiskAssessment(
        subject_ref=subject_ref, entity_role=role, trust_state=state,
        risk_score=score, dimensions=dims, reasons=reasons,
        contradicting_evidence=contradicting,
        evidence_refs=[str(e) for e in (evidence_refs or [])],
        confidence=max(0.0, min(1.0, float(confidence))),
        ttl_hours=ttl,
        shared_infrastructure_factor=factor, capped_by=capped_by)


def record(assessment: FinancialRiskAssessment, conn=None) -> int:
    """Persist an assessment with mandatory expiry. Returns row id."""
    now = _utcnow()
    with store.connection(conn) as c:
        cur = c.cursor()
        cur.execute(
            """INSERT INTO sentinel_financial_risk
               (subject_ref, entity_role, trust_state, risk_score,
                dimensions_json, reasons_json, contradicting_json,
                evidence_refs_json, source_trust, confidence,
                observed_at, expires_at, deployment_sha, policy_version)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (assessment.subject_ref, assessment.entity_role,
             assessment.trust_state, assessment.risk_score,
             json.dumps(assessment.dimensions),
             json.dumps(assessment.reasons + (
                 [f"capped_by: {', '.join(assessment.capped_by)}"]
                 if assessment.capped_by else [])),
             json.dumps(assessment.contradicting_evidence),
             json.dumps(assessment.evidence_refs),
             "DERIVED", assessment.confidence,
             _fmt(now), _fmt(now + timedelta(hours=assessment.ttl_hours)),
             store.deployment_sha(), POLICY_VERSION))
        return int(cur.lastrowid)


def latest(subject_ref: str, conn=None,
           now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """Latest assessment with expiry + linear decay applied at READ time.
    Expired → None (the caller must treat the subject as UNKNOWN/NORMAL,
    never as still-risky)."""
    now = now or _utcnow()
    with store.connection(conn) as c:
        cur = c.cursor()
        cur.execute(
            "SELECT entity_role, trust_state, risk_score, dimensions_json, "
            "reasons_json, contradicting_json, evidence_refs_json, confidence, "
            "observed_at, expires_at FROM sentinel_financial_risk "
            "WHERE subject_ref = ? ORDER BY id DESC LIMIT 1", (subject_ref,))
        row = cur.fetchone()
    if not row:
        return None
    observed = datetime.strptime(str(row[8])[:19], _TS).replace(tzinfo=timezone.utc)
    expires = datetime.strptime(str(row[9])[:19], _TS).replace(tzinfo=timezone.utc)
    if now >= expires:
        return None  # expired risk is GONE, not lingering suspicion
    total = (expires - observed).total_seconds() or 1.0
    elapsed = max(0.0, (now - observed).total_seconds())
    decay = max(0.0, 1.0 - elapsed / total)   # linear decay to 0 at expiry
    raw = float(row[2])
    decayed = round(raw * decay, 4)
    if decayed >= HIGH_RISK_THRESHOLD:
        state = "HIGH_RISK"
    elif decayed >= 0.5:
        state = "ELEVATED"
    elif decayed >= 0.25:
        state = "WATCH"
    else:
        state = "NORMAL"
    return {
        "subject_ref": subject_ref, "entity_role": row[0],
        "trust_state": state, "risk_score": decayed,
        "recorded_score": raw, "recorded_state": row[1],
        "dimensions": json.loads(row[3] or "{}"),
        "reasons": json.loads(row[4] or "[]"),
        "contradicting_evidence": json.loads(row[5] or "[]"),
        "evidence_refs": json.loads(row[6] or "[]"),
        "confidence": row[7], "observed_at": row[8], "expires_at": row[9],
        "decay_factor": round(decay, 4),
        "note": "RISK != GUILT — decays linearly and expires entirely",
    }


def active_high_risk(conn=None, now: Optional[datetime] = None,
                     limit: int = 100) -> List[Dict[str, Any]]:
    """Subjects whose DECAYED score is still ≥ HIGH_RISK_THRESHOLD."""
    now = now or _utcnow()
    out: List[Dict[str, Any]] = []
    with store.connection(conn) as c:
        cur = c.cursor()
        cur.execute(
            "SELECT DISTINCT subject_ref FROM sentinel_financial_risk "
            "WHERE expires_at > ? ORDER BY id DESC LIMIT ?",
            (_fmt(now), max(1, min(int(limit) * 5, 1000))))
        subjects = [r[0] for r in cur.fetchall()]
        for ref in subjects:
            row = latest(ref, conn=c, now=now)
            if row and row["risk_score"] >= HIGH_RISK_THRESHOLD:
                out.append(row)
            if len(out) >= limit:
                break
    return out


def payment_velocity(subject_ref: str, conn=None,
                     window_minutes: int = 60,
                     now: Optional[datetime] = None) -> Dict[str, Any]:
    """Bounded-window payment velocity from sentinel events (read-only).
    HIGH VOLUME != FRAUD: this returns counts and a normalized signal, never
    a verdict, and never auto-blocks anything."""
    minutes = int(window_minutes)
    if minutes <= 0 or minutes > MAX_VELOCITY_WINDOW_MINUTES:
        raise FinancialRiskError(
            f"velocity window must be within (0, {MAX_VELOCITY_WINDOW_MINUTES}] minutes")
    now = now or _utcnow()
    since = _fmt(now - timedelta(minutes=minutes))
    from services.sentinel import financial_entities
    etype, ident = financial_entities.parse_ref(subject_ref)
    with store.connection(conn) as c:
        cur = c.cursor()
        cur.execute(
            "SELECT event_type, COUNT(*) FROM sentinel_events "
            "WHERE category IN ('PAYMENT','PAYOUT','ADVERTISING') "
            "AND ((subject_type = ? AND subject_id = ?) "
            "     OR correlation_keys_json LIKE ?) "
            "AND occurred_at >= ? GROUP BY event_type",
            (etype, ident, f'%"{subject_ref}"%', since))
        counts = {r[0]: int(r[1]) for r in cur.fetchall()}
    total = sum(counts.values())
    # Normalized signal: saturates at 20 events/hour-equivalent. Explainable
    # baseline, no ML (Stage 28).
    per_hour = total / (minutes / 60.0)
    signal = round(min(1.0, per_hour / 20.0), 4)
    return {"subject_ref": subject_ref, "window_minutes": minutes,
            "counts": counts, "total": total,
            "events_per_hour": round(per_hour, 2),
            "velocity_signal": signal,
            "note": "HIGH VOLUME != FRAUD — signal, not verdict; no auto-block"}


def fuse_with_external(internal_score: float, external_score: float,
                       *, internal_evidence_count: int) -> Dict[str, Any]:
    """Fuse internal + external risk. External evidence can raise concern
    but the Mission 4 ceiling holds: without internal corroboration the
    fused score cannot exceed EXTERNAL_ONLY_RISK_CAP."""
    internal = max(0.0, min(1.0, float(internal_score)))
    external = max(0.0, min(1.0, float(external_score)))
    fused = max(internal, min(1.0, internal + external * 0.3))
    capped = False
    if internal_evidence_count <= 0 and fused > EXTERNAL_ONLY_RISK_CAP:
        fused = EXTERNAL_ONLY_RISK_CAP
        capped = True
    return {"fused_score": round(fused, 4),
            "internal_score": internal, "external_score": external,
            "external_only_capped": capped,
            "cap": EXTERNAL_ONLY_RISK_CAP,
            "note": "external verdicts are evidence, not enforcement"}
