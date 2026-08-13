# Sentinel ↔ Existing Security Center (Stage 20)

Module: `services/sentinel/security_center_bridge.py`.

## One security center, not two

PulseSoc already records security happenings in platform tables
(`security_events`, `auth_events`). Sentinel does **not** replace that
surface, does not duplicate its UI, and does not ask product code to
double-write. Instead, the bridge *reads* the existing tables and
normalizes rows into the canonical Sentinel envelope. Existing features
keep working untouched; Sentinel gains their signal.

## How the bridge works

`sync_security_events(conn=…)`:

1. Best-effort reads each known source table; a missing table yields
   `{ingested: 0, deduped: 0}` rather than an error (local dev,
   partial schemas).
2. Maps row types via explicit maps (`_SECURITY_EVENT_MAP`,
   `_AUTH_EVENT_MAP`) into canonical categories (SECURITY, AUTH) and
   event types (e.g. `unusual_device`). Unmapped types are skipped, not
   guessed (SC15).
3. Dedupe key is `"{table}:{row_id}"` — re-running the bridge is
   idempotent (regression-tested: second sync ingests 0, dedupes 1).
4. Payloads pass through `events.ingest`, so classification redaction
   applies at the door (SC7).

The bridge is read-only toward its sources: it never updates, deletes, or
"marks processed" rows in platform tables. Idempotency comes from the
dedupe key, not from mutating the source.

## Scheduling

V1 ships the function, not a schedule. Intended wiring (Phase 2): a call
from an existing worker loop (e.g. alert_worker) guarded by
`SENTINEL_INGEST_ENABLED`. No new worker process is added for this.

## Extending

To bridge another existing table: add a mapping dict entry with an
explicit category/type per source value, use `"{table}:{id}"` dedupe
keys, tolerate table absence, and never mutate the source. New categories
require an event-model change, which is a reviewed decision.
