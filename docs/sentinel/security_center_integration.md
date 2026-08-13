# Sentinel ↔ Existing Security Center (Stage 20, fixed in Mission 2)

Module: `services/sentinel/security_center_bridge.py`.

## One security center, not two

PulseSoc already records security happenings in platform tables
(`security_events`, `auth_events`, `admin_audit_logs`). Sentinel does
**not** replace that surface, does not duplicate its UI, and does not ask
product code to double-write. The bridge *reads* the existing tables and
normalizes rows into the canonical SentinelEventV1 envelope. Existing
features keep working untouched; Sentinel gains their signal.

## How the bridge works

`sync_security_events(conn=…)`:

1. Best-effort reads each known source table; a missing table yields
   zero counts rather than an error (local dev, partial schemas).
2. Maps row types via explicit maps (`_SECURITY_EVENT_MAP`,
   `_AUTH_EVENT_MAP`) into canonical categories and event types.
   **Unmapped types are skipped and counted, never guessed** (SC15).
3. Dedupe key is `"{table}:{row_id}"` — re-running the bridge is
   idempotent (regression-tested: second sync ingests 0, dedupes all).
4. Payloads pass through `events.ingest`, so classification redaction
   applies at the door (SC7).
5. Rows carry full provenance: `source_system="pulsesoc"`,
   `source_component="security_center_bridge"`, the source table + row id
   as `source_event_id`/`dedupe_key`, and `source_trust="AUTHORITATIVE"` —
   these are the platform's own canonical records, its system of record
   for what happened.

## Mission 2 fixes (verified against live bot.py writers)

- `security_events` stores its payload in **`details_json`** — the
  Mission 1 SELECT asked for `details` and silently returned nothing.
- `_AUTH_EVENT_MAP` now contains the REAL event types bot.py writes
  (`login_success`, `forgot_password_invalid_email`,
  `mobile_login_failed`, …), not guessed names.
- Source timestamps are ISO with a `'T'`; the bridge normalises them to
  canonical `YYYY-MM-DD HH:MM:SS` so time-window comparisons work.
- **Raw IPs never enter sentinel storage** — they are hashed into typed
  `network:` refs before ingest.
- `admin_audit_logs` is bridged into the ADMIN category the same way.

The bridge is read-only toward its sources: it never updates, deletes,
or "marks processed" rows in platform tables. Idempotency comes from the
dedupe key, not from mutating the source.

## Scheduling

V1 ships the function, not a schedule. Intended wiring (later mission): a
call from an existing worker loop (e.g. alert_worker) guarded by
`SENTINEL_INGEST_ENABLED`. No new worker process is added for this.

## Extending

To bridge another existing table: add a mapping dict entry with an
explicit category/type per source value, use `"{table}:{id}"` dedupe
keys, set honest provenance and trust (a system-of-record table is
AUTHORITATIVE; anything inferred is DERIVED), hash any network
identifiers, tolerate table absence, and
never mutate the source. New categories require an event-model change,
which is a reviewed decision.
