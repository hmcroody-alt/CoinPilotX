# Sentinel Source Trust (Mission 2)

Module: `services/sentinel/source_trust.py`.

Every observation carries a trust grade describing **how the fact was
obtained**, not how much we like the source. The grade travels with the
event and every health snapshot, and it caps what the observation may
claim. The one rule that must never bend: **CONFIGURED is not HEALTHY**.

## The 7 grades (strongest first)

| Grade | Meaning | Confidence ceiling |
|-------|---------|--------------------|
| `AUTHORITATIVE` | The system of record for this fact (platform DB row). | 1.0 |
| `MEASURED` | Actively probed / directly observed just now. | 1.0 |
| `DERIVED` | Computed from other observations (correlation, rollups). | 0.8 |
| `CONFIGURED` | Inferred from configuration only ("a key is set"). | 0.4 |
| `STALE` | Was trustworthy, but past its freshness window. | 0.3 |
| `SIMULATED` | Produced by a test/drill; never production truth. | 0.2 |
| `UNKNOWN` | Provenance unknown — fail closed. | 0.1 |

Unknown grade → `SourceTrustError` (SC15). An event or health snapshot
declaring explicit confidence **above** its grade's ceiling is
**rejected at construction**, not clamped — an observation that lies
about its certainty never exists (SC4).

## effective_health(status, trust)

Health claims are capped by the trust of their evidence:

- CONFIGURED / SIMULATED / UNKNOWN trust can never yield HEALTHY — the
  claim downgrades to `UNKNOWN` (loudly different from green).
- STALE trust downgrades HEALTHY to `STALE`.
- Negative statuses (DEGRADED / FAILED / …) pass through **unchanged**:
  weak evidence may lower confidence in good news, never soften bad news.

Only `HEALTH_CAPABLE = (AUTHORITATIVE, MEASURED)` can substantiate a
green light.

## Why this exists

The characteristic monitoring failure is "BREVO_API_KEY is set, so email
is healthy." That's configuration masquerading as measurement. Under
this model such a snapshot is downgraded before it ever persists, its
confidence is capped at 0.4, and the adversarial suite
(`tests/sentinel/test_adversarial.py::TestConfiguredIsNotHealthy`)
asserts both properties permanently.
