# Sentinel Detection Model (Stages 8–10)

## Deterministic correlation (Stage 8)

Module: `services/sentinel/correlation.py`. No LLM anywhere in the
detection path — correlation is SQL + arithmetic, reproducible and
auditable.

`CorrelationRule` is unconstructible unless (SC8, SC14):

- `min_events ≥ 2` **or** `min_distinct_types ≥ 2` — no single-signal
  convictions, ever
- `window_minutes > 0` and bounded — no "forever" windows

Shipped rules:

| ID | Trigger | Opens |
|----|---------|-------|
| CR1 | ≥5 `login_failed` for one subject in 30m | ACCOUNT_TAKEOVER (high) |
| CR2 | `unusual_device` AND `unusual_country` (distinct types) in 60m | ACCOUNT_TAKEOVER (high) |
| CR3 | ≥2 `invariant_violation` in 120m | PAYMENT_ANOMALY (high) |
| CR4 | ≥3 provider capability failures in 30m | PROVIDER_OUTAGE (medium) |
| CR5 | ≥3 events, ≥2 distinct of `injection_detected`/`policy_denied` in 60m | AI_BOUNDARY_EVENT (medium) |

Incident keys are `corr_` + sha256(rule|subject|day-bucket)[:24], so
re-evaluating a rule is idempotent — same finding, same incident, never a
duplicate (regression-tested).

## Relational graph (Stage 9)

Module: `services/sentinel/graph.py`. Table: `sentinel_edges`. Plain
relational edges — deliberately **no graph database** (Stage 25).

Typed edges only (`EDGE_TYPES`; unknown type → ValueError, SC15):
`used_device`, `used_ip`, `paid_with`, `shares_session`, `referred_by`.
Re-upserting an edge accumulates weight. `shared_destination` answers the
one question V1 needs: "which users touched this same device/IP/payment
instrument?" — the raw material for cluster review by humans, not
automated mass action.

## Canonical journeys (Stage 10)

Module: `services/sentinel/journeys.py`. Six journeys describe what
"working" looks like end-to-end: `AUTH`, `CHECKOUT`, `SETTLEMENT`,
`AD_DELIVERY`, `DEPLOYMENT`, `NATIVE_API`.

`evaluate(journey_id, observed_events)` is a pure function returning
`complete`, `completed_steps`, and the first `broken_step`. Unknown
journey → ValueError (SC15). Journeys turn "users are complaining" into
"the CHECKOUT journey breaks at payment_confirmation" — a vocabulary for
incidents and, later, synthetic monitoring.
