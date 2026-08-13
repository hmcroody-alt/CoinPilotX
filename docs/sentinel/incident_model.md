# Sentinel Incident Model (Stage 7, rebuilt in Mission 2)

Module: `services/sentinel/incidents.py`. Tables: `sentinel_incidents`,
`sentinel_incident_transitions` (append-only).

## Incident types (10)

`SECURITY_INTRUSION`, `ACCOUNT_TAKEOVER`, `ABUSE`, `DATA_EXPOSURE`,
`FINANCIAL_DISCREPANCY`, `PROVIDER_OUTAGE`, `INVARIANT_VIOLATION`,
`AI_SAFETY`, `OPERATIONAL_DEGRADATION`, `COMPLIANCE`.
Unknown type → `ValueError` (SC15).

## The 11 canonical states (Mission 2)

```
DETECTED → INVESTIGATING → CONFIRMED → CONTAINING → RECOVERING
                                          ↓ (or direct)
                                      RECOVERING → VERIFYING → MONITORING → RESOLVED
Any open state → ESCALATED (and back to INVESTIGATING)
Any open state → SUPPRESSED (only via suppress(), reason + expiry required)
DETECTED / INVESTIGATING → FALSE_POSITIVE (terminal, note required)
RESOLVED → INVESTIGATING (reopen on recurrence outside cooldown)
```

Allowed transitions are an explicit dict; anything else raises
`TransitionError`. There are no shortcuts from DETECTED to RESOLVED.

## Deduplication and recurrence (Mission 2)

- `dedupe_key(*components)` — deterministic `inc_` + sha256 over **scalar**
  components only. Passing a dict/list (e.g. model output) raises: structured
  blobs never become identity.
- `open_incident` on an existing key delegates to `record_observation`:
  `observation_count` increments, `last_seen_at` advances, no duplicate row.
- **Reopen**: recurrence against a RESOLVED incident outside
  `REOPEN_COOLDOWN_MINUTES` (10) reopens it to INVESTIGATING. Inside the
  cooldown it only counts — resolution flapping is not a new investigation.
- **Suppression**: only via `suppress(key, actor, reason, until_minutes)`.
  Empty reason or non-positive/unbounded expiry (> 30 days) raises.
  Suppressed incidents stay queryable (`list_open(include_suppressed=True)`)
  and reopen automatically if the condition recurs after expiry.

## Hard guards

- **Independent verification exit (SC4)**: leaving VERIFYING (to MONITORING
  or RESOLVED) requires `verified_by` set and ≠ the transition actor.
- **Notes are mandatory** for RESOLVED and FALSE_POSITIVE; `resolution_code`
  is recorded with the closure.
- **Owner attention is a field, not a guess**: `owner_action_required` is set
  by the rule that opened the incident and surfaces in the owner summary.
- **Append-only history**: every transition writes a row to
  `sentinel_incident_transitions` and an evidence-chain record (SC5).

## Who opens incidents

Deterministic code only: correlation rules (Stage 8), deterministic
detections (Stages 21–22), and invariant violations (Stage 11). Models cannot
open incidents — `undx_interface` exposes no such entry point (SC2).
