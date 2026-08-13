# Sentinel Incident Model (Stage 7)

Module: `services/sentinel/incidents.py`. Tables: `sentinel_incidents`,
`sentinel_incident_transitions` (append-only).

## Incident types (10)

`SECURITY_INTRUSION`, `ACCOUNT_TAKEOVER`, `PAYMENT_ANOMALY`,
`INVARIANT_VIOLATION`, `PROVIDER_OUTAGE`, `DATA_EXPOSURE`,
`ABUSE_CAMPAIGN`, `DEPLOYMENT_REGRESSION`, `WORKER_FAILURE`,
`AI_BOUNDARY_EVENT`. Unknown type → `ValueError` (SC15).

## The 11 states

```
NEW → TRIAGED → CORRELATING ↘
        │            ↓        CONFIRMED
        │        CONFIRMED       ↓
        ↓                   CONTAINMENT_PROPOSED → CONTAINMENT_APPROVED → CONTAINED
     (direct)                    ↓ (or from CONFIRMED/CONTAINED)
                            RECOVERY_PROPOSED → RECOVERY_VERIFIED → RESOLVED → CLOSED
```

Allowed transitions are an explicit dict; anything else raises
`TransitionError`. There are no shortcuts from NEW to RESOLVED.

## Hard guards

- **Idempotent open**: `open_incident` is keyed by `incident_key`; the
  second open returns `created=False` rather than a duplicate.
- **Independent recovery verification (SC4)**: entering
  `RECOVERY_VERIFIED` requires `verified_by` set and ≠ the transition
  actor. Self-verification raises.
- **Notes are mandatory** for `RESOLVED` and `CLOSED` — no silent
  closures; the note is the human-readable root-cause record.
- **Append-only history**: every transition writes a row to
  `sentinel_incident_transitions` and an evidence-chain record (SC5).
  There is no function to edit or delete a transition.
- **Containment/recovery are proposals**: Sentinel V1 never contains or
  recovers anything itself. `CONTAINMENT_APPROVED` is a human act recorded
  in the state machine; execution belongs to governed runbooks in a later
  phase, still subject to kill switches and budgets.

## Who opens incidents

Deterministic code only: correlation rules (Stage 8) and invariant
violations (Stage 11). Models cannot open incidents — `undx_interface`
exposes no such entry point (SC2).
