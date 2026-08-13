"""Sentinel health freshness contract (Mission 2, Stage 7).

A health claim is an observation with an expiry, not a flag. Every snapshot
records WHAT was measured, HOW (source_trust), WHEN (observed_at), and until
WHEN it may be believed (expires_at). Reading current health applies three
non-negotiable rules:

- UNKNOWN is not HEALTHY (absence of data is not good news)
- STALE is not HEALTHY (expired good news is not good news)
- CONFIGURED is not HEALTHY ("a key is set" is not a measurement)

Snapshots are append-only; ``current()`` is the newest row per component with
freshness applied at read time, so a dead reporter decays to STALE by itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from services.sentinel import source_trust as trust_mod, store

HEALTH_STATES = ("HEALTHY", "DEGRADED", "FAILED", "UNKNOWN", "RECOVERING", "STALE")

_TS = "%Y-%m-%d %H:%M:%S"
DEFAULT_TTL_SECONDS = 900  # 15 minutes: unrefreshed health decays fast


class HealthError(ValueError):
    """Contract violation in a health snapshot (fail closed, SC15)."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _fmt(dt: datetime) -> str:
    return dt.strftime(_TS)


def _parse(ts: str) -> datetime | None:
    try:
        return datetime.strptime(str(ts), _TS).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


@dataclass(frozen=True)
class HealthSnapshot:
    component: str                # e.g. "provider:stripe/webhook", "worker:alert_worker"
    status: str
    source_trust: str
    observed_at: str = field(default_factory=lambda: _fmt(_utcnow()))
    expires_at: str = ""          # default: observed_at + DEFAULT_TTL_SECONDS
    measurement: str = ""         # what was actually measured ("last_run_at=…")
    threshold: str = ""           # the rule it was judged against
    confidence: float | None = None
    evidence_ref: str = ""

    def __post_init__(self):
        if not str(self.component or "").strip():
            raise HealthError("component is required")
        if self.status not in HEALTH_STATES:
            raise HealthError(f"unknown health status {self.status!r} (SC15)")
        try:
            trust_mod.validate(self.source_trust)
        except trust_mod.SourceTrustError as exc:
            raise HealthError(str(exc)) from exc
        observed = _parse(self.observed_at)
        if observed is None:
            raise HealthError(f"malformed observed_at {self.observed_at!r}")
        if not self.expires_at:
            object.__setattr__(
                self, "expires_at", _fmt(observed + timedelta(seconds=DEFAULT_TTL_SECONDS)))
        expires = _parse(self.expires_at)
        if expires is None:
            raise HealthError(f"malformed expires_at {self.expires_at!r}")
        if expires <= observed:
            raise HealthError("expires_at must be after observed_at (SC14)")
        ceiling = trust_mod.confidence_ceiling(self.source_trust)
        if self.confidence is None:
            object.__setattr__(self, "confidence", ceiling)
        elif not (0.0 <= float(self.confidence) <= 1.0):
            raise HealthError("confidence must be within [0, 1]")
        elif float(self.confidence) > ceiling + 1e-9:
            raise HealthError(
                f"confidence {self.confidence} exceeds ceiling {ceiling} for "
                f"source_trust {self.source_trust} (SC4)")
        # Trust caps status at WRITE time too: a CONFIGURED "HEALTHY" is
        # stored as what it really is.
        capped = trust_mod.effective_health(self.status, self.source_trust)
        if capped != self.status:
            object.__setattr__(self, "status", capped)


def record(snapshot: HealthSnapshot, conn=None) -> None:
    with store.connection(conn) as c:
        c.cursor().execute(
            """INSERT INTO sentinel_health_snapshots
               (component, status, source_trust, observed_at, expires_at,
                measurement, threshold, confidence, deployment_sha, evidence_ref)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (snapshot.component, snapshot.status, snapshot.source_trust,
             snapshot.observed_at, snapshot.expires_at,
             snapshot.measurement[:500], snapshot.threshold[:500],
             float(snapshot.confidence), store.deployment_sha(),
             snapshot.evidence_ref[:200]))


def current(component: str, conn=None, *, now: datetime | None = None) -> dict:
    """Newest snapshot for a component with freshness applied at read time.
    No snapshot at all → UNKNOWN (never HEALTHY-by-default)."""
    now = now or _utcnow()
    with store.connection(conn) as c:
        cur = c.cursor()
        cur.execute(
            "SELECT status, source_trust, observed_at, expires_at, measurement, "
            "threshold, confidence, deployment_sha, evidence_ref "
            "FROM sentinel_health_snapshots WHERE component = ? "
            "ORDER BY id DESC LIMIT 1", (component,))
        row = cur.fetchone()
    if not row:
        return {"component": component, "status": "UNKNOWN", "source_trust": "UNKNOWN",
                "observed_at": None, "expires_at": None, "measurement": "",
                "threshold": "", "confidence": 0.0, "deployment_sha": "",
                "evidence_ref": "", "fresh": False}
    status = str(row[0])
    expires = _parse(str(row[3]))
    fresh = bool(expires and expires > now)
    if not fresh and status == "HEALTHY":
        status = "STALE"
    status = trust_mod.effective_health(status, str(row[1]))
    return {"component": component, "status": status, "source_trust": row[1],
            "observed_at": row[2], "expires_at": row[3], "measurement": row[4],
            "threshold": row[5], "confidence": row[6], "deployment_sha": row[7],
            "evidence_ref": row[8], "fresh": fresh}


def overview(conn=None, *, now: datetime | None = None, limit: int = 200) -> list[dict]:
    """Current health for every known component (bounded)."""
    with store.connection(conn) as c:
        cur = c.cursor()
        cur.execute(
            "SELECT DISTINCT component FROM sentinel_health_snapshots "
            "ORDER BY component LIMIT ?", (max(1, min(int(limit), 500)),))
        components = [str(r[0]) for r in cur.fetchall()]
        return [current(comp, conn=c, now=now) for comp in components]


def stale_count(conn=None, *, now: datetime | None = None) -> int:
    """How many components currently have no fresh, believable signal."""
    return sum(1 for row in overview(conn=conn, now=now)
               if row["status"] in ("STALE", "UNKNOWN"))
