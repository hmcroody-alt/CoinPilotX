# PulseSoc Call Backend Exception 440ea1f2c5bc

## Incident

Production call startup returned:

- `error_code`: `BACKEND_EXCEPTION`
- Correlation ID: `440ea1f2c5bc`
- User-visible state: `Call backend error`

The failure occurs before the recipient can ring.

## Historical Log Access

The local Railway CLI session is expired:

```text
Unauthorized. Please run railway login again.
```

Because of that, this shell could not fetch the historical production stack trace for correlation ID `440ea1f2c5bc`. The code path was traced locally from `POST /api/calls/start` and two production-only failure defects were found in the call startup write path.

## Root Cause

The Communications Engine runtime schema and the checked-in PostgreSQL migration were not aligned.

The runtime call-start code inserts into compatibility columns:

```text
communication_calls.metadata_json
communication_call_participants.device_info_json
communication_call_events.event_payload_json
```

The original PostgreSQL migration created:

```text
communication_calls.metadata
communication_call_participants.device_info
communication_call_events.event_payload
```

On a production database initialized from the migration, call startup can fail at:

```text
services/pulsesoc_communications_engine.py:978
INSERT INTO communication_calls (..., metadata_json, ...)
```

with a database exception such as:

```text
column "metadata_json" of relation "communication_calls" does not exist
```

The second production-only defect was returned ID handling. The Postgres compatibility layer did not list the communications tables in `AUTO_PK_TABLES`, so inserts did not automatically append `RETURNING id`. On Postgres, `cur.lastrowid` can be `None`, making this line unsafe:

```text
services/pulsesoc_communications_engine.py:984
call_id = _inserted_call_id(cur, public_id)
```

Before this fix, the equivalent startup instruction depended directly on `cur.lastrowid`.

## Fix Applied

1. Added additive runtime schema healing for communications tables.
   - Adds missing `metadata_json`, `device_info_json`, `event_payload_json`, and `permissions_json` columns when tables already exist.
   - Uses `information_schema.columns` on Postgres and `PRAGMA table_info` on SQLite.

2. Updated the PostgreSQL migration to include the runtime compatibility columns and `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` repair statements.

3. Registered communications tables in the Postgres compatibility `AUTO_PK_TABLES` map so inserts receive returned IDs.

4. Removed fragile call startup dependency on raw `cur.lastrowid`.
   - Call creation now falls back to `SELECT id FROM communication_calls WHERE public_id=?`.

5. Improved backend exception logging.
   - Logs now include the correlation ID, stack trace, metric, request path, redacted request payload, authenticated user IDs, conversation ID, recipient IDs, Railway deployment metadata, and git commit where available.
   - Secrets, tokens, cookies, and stream keys are redacted.

## Verification

Commands run:

```bash
venv/bin/python -m py_compile bot.py services/db.py services/pulsesoc_communications_engine.py pulse_communications_v2/routes.py scripts/pulsesoc_communications_engine_audit.py scripts/pulsesoc_real_call_experience_audit.py scripts/pulsesoc_calls_phase3_live_qa_audit.py scripts/calls_backend_command_center_audit.py
venv/bin/python scripts/pulsesoc_communications_engine_audit.py
venv/bin/python scripts/pulsesoc_real_call_experience_audit.py
venv/bin/python scripts/pulsesoc_calls_phase3_live_qa_audit.py
venv/bin/python scripts/calls_backend_command_center_audit.py
git diff --check
curl -fsS http://127.0.0.1:5069/health
curl -fsS http://127.0.0.1:5069/health/live
curl -fsS http://127.0.0.1:5069/health/ready
```

Results:

- Communications foundation audit: passed 55/55
- Real call experience audit: passed 51/51
- Live QA activation audit: passed 43/43
- Calls Backend Command Center audit: passed 35/35
- Local health/live/ready endpoints: healthy
- Whitespace check: passed

## Remaining Production QA

The specific historical correlation ID `440ea1f2c5bc` cannot be confirmed as eliminated from this shell until Railway logs are accessible or the deployed app is retested after this patch is pushed.

After deployment, retest:

```text
User A taps audio call
Backend creates call
LiveKit token is generated
Recipient receives incoming_call notification/realtime event
Recipient rings
Recipient accepts
Both users join the same LiveKit room
```

If a new correlation ID appears, the backend log line `PULSE_COMM_V2_ROUTE_EXCEPTION` now contains the redacted request context and exact stack trace needed to fix the next failing instruction.
