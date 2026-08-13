"""Sentinel source-trust model (Mission 2).

Every observation carries a trust grade describing HOW the fact was obtained,
not how much we like the source. The grade travels with the event and with
every health snapshot, and it caps what the observation may claim.

The one rule that must never bend: **CONFIGURED is not HEALTHY**. "An API key
exists" or "a table exists" is configuration, not measurement — it can never
silently upgrade into a green light (SC4, SC15).
"""

from __future__ import annotations

# How the observation was obtained, strongest first.
SOURCE_TRUST = (
    "AUTHORITATIVE",  # the system of record for this fact (e.g. platform DB row)
    "MEASURED",       # actively probed / directly observed just now
    "DERIVED",        # computed from other observations (correlation, rollups)
    "CONFIGURED",     # inferred from configuration only ("a key is set")
    "SIMULATED",      # produced by a test/drill; never production truth
    "STALE",          # was trustworthy, but past its freshness window
    "UNKNOWN",        # provenance unknown — fail closed
)

# Trust grades that are allowed to substantiate a HEALTHY claim.
HEALTH_CAPABLE = ("AUTHORITATIVE", "MEASURED")


class SourceTrustError(ValueError):
    """Unknown or forbidden trust value (fail closed, SC15)."""


def validate(trust: str) -> str:
    if trust not in SOURCE_TRUST:
        raise SourceTrustError(f"unknown source_trust {trust!r} (SC15)")
    return trust


def effective_health(status: str, trust: str) -> str:
    """Cap a health status by the trust of its evidence.

    - CONFIGURED / SIMULATED / UNKNOWN trust can never yield HEALTHY: the
      claim is downgraded to UNKNOWN (loudly different from green).
    - STALE trust downgrades HEALTHY to STALE.
    - Negative statuses (DEGRADED/FAILED/…) pass through unchanged — weak
      evidence may lower confidence in good news, never soften bad news.
    """
    validate(trust)
    if status != "HEALTHY":
        return status
    if trust in ("CONFIGURED", "SIMULATED", "UNKNOWN"):
        return "UNKNOWN"
    if trust == "STALE":
        return "STALE"
    return status


def confidence_ceiling(trust: str) -> float:
    """Maximum confidence an observation with this trust may carry."""
    validate(trust)
    return {
        "AUTHORITATIVE": 1.0,
        "MEASURED": 1.0,
        "DERIVED": 0.8,
        "CONFIGURED": 0.4,
        "SIMULATED": 0.2,
        "STALE": 0.3,
        "UNKNOWN": 0.1,
    }[trust]
