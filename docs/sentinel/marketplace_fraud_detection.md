# Sentinel Mission 5 — Marketplace Fraud Detection

Modules: `services/sentinel/financial_detections.py` (coordination and
shared-infrastructure analysis), gated by
`SENTINEL_MARKETPLACE_RISK_ENABLED` (chains on the master detection switch).

## Coordination analysis — the high bar

COORDINATED_FINANCIAL_ABUSE / MARKETPLACE_ABUSE_SUSPECTED require ALL of:

1. **≥3 accounts** — two accounts is a relationship, not a ring (tested:
   2 accounts fail).
2. **≥2 independent anomaly dimensions** — one shared dimension yields the
   explicit note "RELATIONSHIP != COLLUSION" and no finding.
3. **An actual financial anomaly** — shared infrastructure with no anomaly
   yields "sharing alone is not fraud" and no finding.

Even when all requirements are met, the conclusion is at most POSSIBLE —
never CONFIRMED (tested). Unknown dimensions raise instead of silently
counting.

## Shared-infrastructure damping

Legitimate sharing scenarios are first-class: family households, offices,
CGNAT carrier networks, and QA/test rigs all resolve to NOT_ESTABLISHED.
Among competing interpretations the most forgiving wins (tested,
parametrized across 6 factors). Shared device/network contributes at ×0.3
damping to any composite score. DEVICE/NETWORK SHARING != FRAUD.

## Volume and flash-sale posture

- A flash sale producing a single-dimension volume spike scores ≤ 0.25.
- A consistently high-volume seller is never HIGH_RISK on volume alone —
  HIGH VOLUME != FRAUD is a scoring input note, not a slogan.

## Incident gating

Findings only become incidents when the marketplace switch is on: switch
off → detection returns analysis with no IncidentRef; switch on → an
idempotent SUSPECTED incident. Either way the analysis is advisory; no
seller can be suspended and no buyer banned by Sentinel
(`suspend_seller` / `ban_buyer` are in FORBIDDEN_CAPABILITIES and do not
exist as callables anywhere in the package).
