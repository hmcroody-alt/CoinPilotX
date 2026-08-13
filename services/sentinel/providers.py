"""Sentinel provider→capability→status health (Stage 12) and the
circuit-breaker CONTRACT (Stage 13).

Stage 0 found provider health was config-level only ("is a key set"), which
cannot represent "Stripe checkout up, Stripe payouts degraded". This module
adds the capability-level truth table.

The circuit breaker is a contract, deliberately NOT wired into any live call
path in V1 — enforcement is a later phase, enabled per-capability, behind
kill switches.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from services.sentinel import store

STATUSES = ("up", "degraded", "down", "unknown")

# Known provider capabilities (extend as adapters land).
KNOWN_CAPABILITIES = {
    "stripe": ("checkout", "webhooks", "payouts", "refunds"),
    # Real-time media provider is tracked under a neutral key: sentinel must
    # not reference the protected audio/live stack by name (see change policy).
    "rtc_media": ("rooms", "egress"),
    "mux": ("ingest", "playback"),
    "brevo": ("email", "sms"),
    "r2": ("storage",),
    "fcm": ("push",),
    "apns": ("push",),
}


def record_status(provider: str, capability: str, status: str,
                  detail: str = "", conn=None) -> None:
    if status not in STATUSES:
        raise ValueError(f"unknown status {status!r} (SC15)")
    with store.connection(conn) as c:
        cur = c.cursor()
        cur.execute(
            "SELECT id FROM sentinel_provider_capabilities WHERE provider=? AND capability=?",
            (provider, capability))
        row = cur.fetchone()
        if row:
            cur.execute(
                "UPDATE sentinel_provider_capabilities SET status=?, detail=?, "
                "observed_at=datetime('now') WHERE id=?",
                (status, detail[:500], int(row[0])))
        else:
            cur.execute(
                "INSERT INTO sentinel_provider_capabilities (provider, capability, status, detail) "
                "VALUES (?, ?, ?, ?)", (provider, capability, status, detail[:500]))


def health_table(conn=None) -> list[dict]:
    with store.connection(conn) as c:
        cur = c.cursor()
        cur.execute(
            "SELECT provider, capability, status, observed_at, detail "
            "FROM sentinel_provider_capabilities ORDER BY provider, capability")
        rows = cur.fetchall()
    return [{"provider": r[0], "capability": r[1], "status": r[2],
             "observed_at": r[3], "detail": r[4]} for r in rows]


def capability_status(provider: str, capability: str, conn=None) -> str:
    """Fail closed: anything unrecorded is 'unknown', never 'up'."""
    with store.connection(conn) as c:
        cur = c.cursor()
        cur.execute(
            "SELECT status FROM sentinel_provider_capabilities WHERE provider=? AND capability=?",
            (provider, capability))
        row = cur.fetchone()
    return str(row[0]) if row else "unknown"


# ---------------------------------------------------------------------------
# Circuit-breaker contract (Stage 13) — CONTRACT ONLY, not enforced anywhere.
# ---------------------------------------------------------------------------

@dataclass
class CircuitBreaker:
    """Standard closed → open → half_open contract.

    V1 ships the state machine and its tests; no call path consults it yet.
    When a later phase wires it in, it must sit behind a per-capability kill
    switch and record every trip as evidence.
    """
    name: str
    failure_threshold: int = 5
    recovery_timeout_seconds: float = 60.0
    state: str = "closed"
    _failures: int = 0
    _opened_at: float = field(default=0.0)

    def __post_init__(self):
        if self.failure_threshold <= 0 or self.recovery_timeout_seconds <= 0:
            raise ValueError("breaker thresholds must be positive (SC14)")

    def allow_request(self, *, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        if self.state == "closed":
            return True
        if self.state == "open":
            if now - self._opened_at >= self.recovery_timeout_seconds:
                self.state = "half_open"
                return True
            return False
        # half_open: allow exactly the probe
        return True

    def record_success(self) -> None:
        self._failures = 0
        self.state = "closed"

    def record_failure(self, *, now: float | None = None) -> None:
        now = time.time() if now is None else now
        if self.state == "half_open":
            self.state = "open"
            self._opened_at = now
            return
        self._failures += 1
        if self._failures >= self.failure_threshold:
            self.state = "open"
            self._opened_at = now

    def snapshot(self) -> dict:
        return {"name": self.name, "state": self.state, "failures": self._failures}
