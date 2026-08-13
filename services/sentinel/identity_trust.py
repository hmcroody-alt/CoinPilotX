"""Sentinel identity trust model — Mission 3 (Stages 3–5, 15–18).

Detection is NOT guilt. Everything in this module OBSERVES, CORRELATES,
SCORES and EXPLAINS; nothing here bans, blocks, locks out, invalidates a
session, or touches funds (SC3). The outputs are advisory risk observations
with mandatory reasons, mandatory expiry, and mandatory room for
contradicting evidence.

Honest signal quality (Stage 4): PulseSoc's "device identity" is
sha256(salt : client_device_id : user_agent) — a CLIENT_REPORTED value that
any attacker can forge and any reinstall can rotate. It is useful for
correlation and is NEVER treated as hardware fingerprinting; device-derived
risk is capped accordingly and labeled with its true quality.

Network observations are internal-only (Stage 5): hashed network refs from
our own request logs. No external IP-reputation feed participates (that is
Mission 4, behind its own trust model).

Risk model (Stage 15): named dimensions, each in [0, 1], each carrying its
own reasons. There is no opaque magic number — the optional ``overall`` is
simply the maximum dimension, so an operator can always answer "why".

Decay (Stage 16): every stored observation has ``expires_at``. An expired
observation is INACTIVE — ``latest()`` will not return it as live risk, and
the invariant suite asserts stale high risk cannot stay active.

Fusion (Stage 17): a HIGH_RISK verdict requires at least
``HIGH_RISK_MIN_SIGNALS`` independent dimensions in agreement; a single
signal — however loud — caps at ELEVATED. Confidence never exceeds the
source-trust ceiling of the weakest load-bearing signal (SC4).

Contradiction (Stage 18): assessments carry ``contradicting`` — evidence
that argues AGAINST the risk (known device, long-lived session, normal
hours). Storage keeps it and the UNDX surface exposes it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from services.sentinel import entities, source_trust as trust_mod, store
from services.sentinel.constitution import CONSTITUTION_VERSION

_TS = "%Y-%m-%d %H:%M:%S"

# Closed vocabularies (SC15).
TRUST_STATES = ("TRUSTED", "NORMAL", "ELEVATED", "HIGH_RISK", "UNKNOWN", "STALE")

RISK_DIMENSIONS = (
    "credential_risk", "recovery_risk", "session_risk", "device_risk",
    "network_risk", "admin_risk", "behavioral_risk",
)

# Stage 4 truth-in-labeling: what our device signal actually is.
DEVICE_SIGNAL_QUALITY = "CLIENT_REPORTED"   # forgeable; not fingerprinting
DEVICE_RISK_CAP = 0.6                       # forgeable signal → capped contribution

# Fusion policy (Stage 17).
HIGH_RISK_SCORE = 0.7          # a dimension at/above this is a "high" signal
HIGH_RISK_MIN_SIGNALS = 2      # independent dimensions required for HIGH_RISK
ELEVATED_SCORE = 0.4

# Freshness policy (Stage 16).
DEFAULT_TTL_MINUTES = 240          # risk observations live 4h by default
SESSION_STALE_MINUTES = 60 * 24    # a session unseen for 24h is STALE, not judged

# Platform session facts that are direct compromise indicators — written by
# deterministic platform code (bot.py revocation paths), read-only to us.
COMPROMISE_REVOKE_REASONS = ("refresh_token_reuse", "device_mismatch")


class IdentityTrustError(ValueError):
    """Malformed identity-trust input (fail closed, SC15)."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _fmt(dt: datetime) -> str:
    return dt.strftime(_TS)


def _parse(ts) -> datetime | None:
    try:
        return datetime.strptime(str(ts).replace("T", " ")[:19], _TS).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


@dataclass(frozen=True)
class SessionTrust:
    """One point-in-time trust assessment for an identity subject.

    Construction validates the whole contract:
    - closed trust-state vocabulary; UNKNOWN can never be constructed as
      TRUSTED (there is no "unknown but trusted"),
    - every non-zero dimension needs at least one reason (no unexplained risk),
    - HIGH_RISK needs >= HIGH_RISK_MIN_SIGNALS independent high dimensions
      AND evidence refs (Stage 17 + invariant),
    - confidence respects the source-trust ceiling (SC4),
    - expiry is mandatory and bounded (Stage 16).
    """
    subject_ref: str
    trust_state: str
    risk_score: float
    dimensions: dict = field(default_factory=dict)
    reasons: tuple = ()
    contradicting: tuple = ()
    evidence_refs: tuple = ()
    source_trust: str = "DERIVED"
    confidence: float | None = None
    observed_at: str = field(default_factory=lambda: _fmt(_utcnow()))
    ttl_minutes: int = DEFAULT_TTL_MINUTES
    expires_at: str = ""

    def __post_init__(self):
        if not entities.is_valid_ref(self.subject_ref):
            raise IdentityTrustError(f"invalid subject_ref {self.subject_ref!r} (SC15)")
        if self.trust_state not in TRUST_STATES:
            raise IdentityTrustError(f"unknown trust_state {self.trust_state!r} (SC15)")

        # Dimensions: closed names, [0, 1] values.
        if not isinstance(self.dimensions, dict):
            raise IdentityTrustError("dimensions must be a dict")
        dims = {}
        for name, value in self.dimensions.items():
            if name not in RISK_DIMENSIONS:
                raise IdentityTrustError(f"unknown risk dimension {name!r} (SC15)")
            v = float(value)
            if not (0.0 <= v <= 1.0):
                raise IdentityTrustError(f"dimension {name} out of [0,1]")
            dims[name] = v
        object.__setattr__(self, "dimensions", dims)

        score = float(self.risk_score)
        if not (0.0 <= score <= 1.0):
            raise IdentityTrustError("risk_score must be within [0, 1]")
        object.__setattr__(self, "risk_score", score)

        object.__setattr__(self, "reasons", tuple(str(r)[:300] for r in (self.reasons or ())))
        object.__setattr__(self, "contradicting",
                           tuple(str(r)[:300] for r in (self.contradicting or ())))
        object.__setattr__(self, "evidence_refs",
                           tuple(str(r)[:200] for r in (self.evidence_refs or ())))

        # No unexplained risk: any signal needs at least one reason.
        if (score > 0 or any(v > 0 for v in dims.values())) and not self.reasons:
            raise IdentityTrustError("risk without reasons is invalid (Stage 15)")

        # UNKNOWN never masquerades as good news; TRUSTED needs strong provenance.
        try:
            trust_mod.validate(self.source_trust)
        except trust_mod.SourceTrustError as exc:
            raise IdentityTrustError(str(exc)) from exc
        if self.trust_state == "TRUSTED":
            if self.source_trust not in trust_mod.HEALTH_CAPABLE:
                raise IdentityTrustError(
                    "TRUSTED requires AUTHORITATIVE/MEASURED provenance — "
                    "UNKNOWN or inferred evidence can never yield TRUSTED (Stage 3)")
            if score > 0.2:
                raise IdentityTrustError("TRUSTED is incompatible with material risk")

        # Fusion gate (Stage 17): HIGH_RISK needs corroboration + evidence.
        if self.trust_state == "HIGH_RISK":
            high_dims = [n for n, v in dims.items() if v >= HIGH_RISK_SCORE]
            if len(high_dims) < HIGH_RISK_MIN_SIGNALS:
                raise IdentityTrustError(
                    f"HIGH_RISK requires >= {HIGH_RISK_MIN_SIGNALS} independent "
                    f"high dimensions, got {high_dims} (Stage 17)")
            if not self.evidence_refs:
                raise IdentityTrustError(
                    "HIGH_RISK without evidence refs is invalid (Stage 32)")

        # Confidence: capped by provenance (SC4).
        ceiling = trust_mod.confidence_ceiling(self.source_trust)
        if self.confidence is None:
            object.__setattr__(self, "confidence", ceiling)
        else:
            conf = float(self.confidence)
            if not (0.0 <= conf <= 1.0):
                raise IdentityTrustError("confidence must be within [0, 1]")
            if conf > ceiling + 1e-9:
                raise IdentityTrustError(
                    f"confidence {conf} exceeds ceiling {ceiling} for "
                    f"source_trust {self.source_trust} (SC4)")

        # Mandatory bounded expiry (Stage 16).
        ttl = int(self.ttl_minutes)
        if ttl <= 0 or ttl > 60 * 24 * 7:
            raise IdentityTrustError("ttl_minutes must be bounded (0, 7d]")
        if not self.expires_at:
            base = _parse(self.observed_at) or _utcnow()
            object.__setattr__(self, "expires_at", _fmt(base + timedelta(minutes=ttl)))


# ---------------------------------------------------------------------------
# Deterministic session assessment (Stage 3)
# ---------------------------------------------------------------------------

def evaluate_session(facts: dict) -> SessionTrust:
    """Pure, deterministic assessment from observed session facts.

    ``facts`` keys (all optional except session_ref):
      session_ref, user_ref, device_ref, network_ref,
      status ('active'/'rotated'/'revoked'), revoked_reason,
      last_seen_at, created_at,
      device_known (bool|None — None means "no device data"),
      network_seen_before (bool|None),
      recent_failed_logins (int), recent_recovery_attempts (int),
      recovery_preceded_login (bool),
      source_trust (of the session row itself; AUTHORITATIVE for the
      platform's own mobile_security_sessions table),
      evidence_refs (tuple), now (datetime, tests only).

    The mapping facts → state is table-driven arithmetic; no model output
    participates (SC2).
    """
    session_ref = str(facts.get("session_ref") or "")
    if not entities.is_valid_ref(session_ref):
        raise IdentityTrustError(f"invalid session_ref {session_ref!r}")
    now = facts.get("now") or _utcnow()
    row_trust = str(facts.get("source_trust") or "UNKNOWN")
    trust_mod.validate(row_trust)

    dims: dict[str, float] = {}
    reasons: list[str] = []
    contradicting: list[str] = []
    evidence = tuple(facts.get("evidence_refs") or ())

    status = str(facts.get("status") or "").lower()
    revoked_reason = str(facts.get("revoked_reason") or "")

    # -- session dimension --------------------------------------------------
    if status == "revoked" and revoked_reason in COMPROMISE_REVOKE_REASONS:
        dims["session_risk"] = 0.9
        reasons.append(f"platform revoked session for {revoked_reason} "
                       "(deterministic compromise indicator)")
    elif status == "revoked":
        dims["session_risk"] = 0.3
        reasons.append(f"session revoked ({revoked_reason or 'unspecified'})")

    # -- credential dimension -----------------------------------------------
    failed = int(facts.get("recent_failed_logins") or 0)
    if failed >= 8:
        dims["credential_risk"] = 0.8
        reasons.append(f"{failed} recent failed logins before this session")
    elif failed >= 3:
        dims["credential_risk"] = 0.5
        reasons.append(f"{failed} recent failed logins before this session")
    elif failed == 0 and facts.get("recent_failed_logins") is not None:
        contradicting.append("no recent failed logins for this account")

    # -- recovery dimension --------------------------------------------------
    recoveries = int(facts.get("recent_recovery_attempts") or 0)
    if facts.get("recovery_preceded_login"):
        dims["recovery_risk"] = 0.7
        reasons.append("account recovery immediately preceded this login")
    elif recoveries >= 3:
        dims["recovery_risk"] = 0.6
        reasons.append(f"{recoveries} recent recovery attempts for this account")

    # -- device dimension (CLIENT_REPORTED — capped, Stage 4) -----------------
    device_known = facts.get("device_known")
    if device_known is False:
        dims["device_risk"] = min(0.5, DEVICE_RISK_CAP)
        reasons.append("login from a device never seen for this account "
                       f"(signal quality: {DEVICE_SIGNAL_QUALITY}, forgeable)")
    elif device_known is True:
        contradicting.append("device previously seen and trusted for this account")

    # -- network dimension (internal observations only, Stage 5) --------------
    network_seen = facts.get("network_seen_before")
    if network_seen is False:
        dims["network_risk"] = 0.4
        reasons.append("login from a network not previously observed for this "
                       "account (internal observation; no external reputation)")
    elif network_seen is True:
        contradicting.append("network previously observed for this account")

    # -- derive state ---------------------------------------------------------
    score = max(dims.values()) if dims else 0.0
    high_dims = [n for n, v in dims.items() if v >= HIGH_RISK_SCORE]

    last_seen = _parse(facts.get("last_seen_at"))
    is_stale = last_seen is not None and (now - last_seen) > timedelta(minutes=SESSION_STALE_MINUTES)

    if row_trust == "UNKNOWN":
        state = "UNKNOWN"   # unknown provenance can never be trusted (Stage 3)
        reasons.append("session provenance unknown — failing closed to UNKNOWN")
    elif len(high_dims) >= HIGH_RISK_MIN_SIGNALS and evidence:
        state = "HIGH_RISK"
    elif score >= HIGH_RISK_SCORE:
        # One loud signal without corroboration: ELEVATED, honestly (Stage 17).
        state = "ELEVATED"
        reasons.append("single high signal without corroboration — capped at "
                       "ELEVATED pending a second independent signal")
    elif score >= ELEVATED_SCORE:
        state = "ELEVATED"
    elif is_stale:
        state = "STALE"
        reasons.append(f"session unseen for > {SESSION_STALE_MINUTES}m — "
                       "trust expired, not judged")
    elif (device_known is True and status == "active"
          and row_trust in trust_mod.HEALTH_CAPABLE and score <= 0.2):
        state = "TRUSTED"
    else:
        state = "NORMAL"

    # Assessment provenance: this is computed, so at best DERIVED — even from
    # authoritative rows the JUDGMENT is ours (SC4).
    assess_trust = "DERIVED" if row_trust in ("AUTHORITATIVE", "MEASURED", "DERIVED") else row_trust
    if state == "TRUSTED":
        # TRUSTED requires the row provenance itself; keep it, score stays low.
        assess_trust = row_trust

    if not reasons and not dims:
        reasons = []  # zero risk needs no reasons; contract allows it

    return SessionTrust(
        subject_ref=session_ref, trust_state=state, risk_score=score,
        dimensions=dims, reasons=tuple(reasons), contradicting=tuple(contradicting),
        evidence_refs=evidence, source_trust=assess_trust,
        observed_at=_fmt(now))


# ---------------------------------------------------------------------------
# Persistence + freshness (Stage 16)
# ---------------------------------------------------------------------------

def record(assessment: SessionTrust, conn=None) -> int:
    """Append one risk observation. Append-only; no update path exists."""
    with store.connection(conn) as c:
        cur = c.cursor()
        cur.execute(
            """INSERT INTO sentinel_identity_risk
               (subject_ref, trust_state, risk_score, dimensions_json,
                reasons_json, contradicting_json, evidence_refs_json,
                source_trust, confidence, observed_at, expires_at,
                deployment_sha, policy_version)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (assessment.subject_ref, assessment.trust_state,
             float(assessment.risk_score),
             json.dumps(assessment.dimensions),
             json.dumps(list(assessment.reasons)),
             json.dumps(list(assessment.contradicting)),
             json.dumps(list(assessment.evidence_refs)),
             assessment.source_trust, float(assessment.confidence),
             assessment.observed_at, assessment.expires_at,
             store.deployment_sha(), CONSTITUTION_VERSION))
        return int(cur.lastrowid or 0)


def _row_to_dict(r) -> dict:
    return {"subject_ref": r[0], "trust_state": r[1], "risk_score": float(r[2]),
            "dimensions": json.loads(r[3] or "{}"),
            "reasons": json.loads(r[4] or "[]"),
            "contradicting": json.loads(r[5] or "[]"),
            "evidence_refs": json.loads(r[6] or "[]"),
            "source_trust": r[7], "confidence": float(r[8]),
            "observed_at": r[9], "expires_at": r[10]}


_SELECT = ("SELECT subject_ref, trust_state, risk_score, dimensions_json, "
           "reasons_json, contradicting_json, evidence_refs_json, source_trust, "
           "confidence, observed_at, expires_at FROM sentinel_identity_risk ")


def latest(subject_ref: str, conn=None, *, now: datetime | None = None) -> dict | None:
    """Newest LIVE observation for a subject. An expired observation is not
    live risk: it is returned with trust_state degraded to STALE, risk_score
    zeroed, and ``expired: True`` — stale high risk never stays active."""
    now = now or _utcnow()
    with store.connection(conn) as c:
        cur = c.cursor()
        cur.execute(_SELECT + "WHERE subject_ref = ? ORDER BY id DESC LIMIT 1",
                    (str(subject_ref),))
        row = cur.fetchone()
    if not row:
        return None
    out = _row_to_dict(row)
    expiry = _parse(out["expires_at"])
    if expiry is not None and now >= expiry:
        out.update({"expired": True, "trust_state": "STALE", "risk_score": 0.0,
                    "note": "observation expired — risk no longer active (Stage 16)"})
    else:
        out["expired"] = False
    return out


def active_high_risk(conn=None, *, now: datetime | None = None, limit: int = 100) -> list[dict]:
    """All subjects whose NEWEST observation is HIGH_RISK and unexpired.
    Bounded query for the owner summary (Stage 23) — real counts only."""
    now = now or _utcnow()
    limit = max(1, min(int(limit), 500))
    with store.connection(conn) as c:
        cur = c.cursor()
        cur.execute(
            _SELECT + "WHERE id IN (SELECT MAX(id) FROM sentinel_identity_risk "
            "GROUP BY subject_ref) AND trust_state = 'HIGH_RISK' "
            "AND expires_at > ? ORDER BY id DESC LIMIT ?",
            (_fmt(now), limit))
        rows = cur.fetchall()
    return [dict(_row_to_dict(r), expired=False) for r in rows]
