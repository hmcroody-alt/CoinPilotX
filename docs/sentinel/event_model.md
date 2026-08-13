# Sentinel Event Model (Stage 2)

Module: `services/sentinel/events.py`. Table: `sentinel_events`.

## Canonical envelope

Every observation entering Sentinel is one immutable `Event`:

| Field | Meaning |
|-------|---------|
| `category` | One of the 15 canonical categories below. Unknown → `EventRejected` (SC15). |
| `event_type` | Snake_case machine name within the category (e.g. `login_failed`). |
| `severity` | `info` / `low` / `medium` / `high` / `critical`. |
| `actor_id` | Registered identity that produced the observation (SC12). |
| `source` | Emitting component (`test`, `bridge:security_events`, adapter id, …). |
| `subject_type` / `subject_id` | What the event is about (`user`/`42`, `provider`/`stripe`). |
| `occurred_at` | UTC `YYYY-MM-DD HH:MM:SS`; defaults to now. |
| `payload` | Dict, redacted to CONFIDENTIAL ceiling **before** persistence (SC7). |
| `dedupe_key` | UNIQUE. Explicit, or deterministic sha256 of (source, category, type, subject, occurred_at). |

## The 15 categories

`AUTH`, `SESSION`, `ADMIN`, `PRIVACY`, `SECURITY`, `PAYMENT`, `LEDGER`,
`SETTLEMENT`, `PAYOUT`, `ADVERTISING`, `PROVIDER`, `DEPLOYMENT`, `WORKER`,
`UNDX`, `SENTINEL_SELF`.

Rationale: every route family and worker in the monolith maps into exactly
one of these; a new category is a schema-visible decision, not an ad-hoc
string.

## Ingestion guarantees

1. **Kill-switchable**: `killswitches.ingest_enabled()` is checked first;
   the emergency switch stops even ingestion.
2. **Idempotent**: duplicate `dedupe_key` is a silent no-op returning
   `ingested: False` — replays and bridge re-syncs are safe.
3. **Redacted at the door**: `classification.redact(payload, CONFIDENTIAL)`
   runs before the INSERT. Raw secrets/PII never reach disk in an event row.
4. **Validated**: category, severity, and non-empty actor/source/subject are
   enforced in `Event.__post_init__`; malformed events raise `EventRejected`.

## What events are NOT

Events are evidence of observation, not verdicts. No event, by itself,
triggers enforcement (SC8). Correlation rules and invariants read events;
humans and deterministic code decide what they mean.
