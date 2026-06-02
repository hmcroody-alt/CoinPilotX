# Pulse Rooms and Groups Root Cause

Generated: 2026-06-01

## Summary

Direct messages work. Rooms and Groups have been reported as showing `Messages could not load.` in the user-facing Messenger panel. A local reproduction with the current code did not reproduce the failure: direct, rooms, groups, legacy bridges, send, load, 403, and 404 paths all passed.

Production/user-facing status should remain **FAIL** until the deployed app is verified after the current stabilization fixes are pushed.

## Failing Endpoint

The current `/pulse/messages` UI loads room and group messages through:

- `GET /api/pulse/communications/conversations/<id>/messages?limit=80`
- `POST /api/pulse/communications/conversations/<id>/messages`

The list endpoints are:

- `GET /api/pulse/communications/rooms`
- `GET /api/pulse/communications/groups`
- `GET /api/pulse/communications/conversations?type=direct`

Frontend source:

- `/Users/hmcherie/Desktop/CoinPilotX/bot.py` around the `/pulse/messages` template script.

Backend source:

- `/Users/hmcherie/Desktop/CoinPilotX/bot.py`
  - `pulse_comm_ref`
  - `pulse_comm_rooms`
  - `api_pulse_communications_rooms`
  - `api_pulse_communications_groups`
  - `api_pulse_communications_conversation_messages`
  - `api_pulse_communications_send_message`

## Backend Exception

No backend exception was reproduced locally.

Relevant historical production/database risk already identified in the production dashboard:

- `PULSE_VISIBLE_MESSAGE_FILTER` was previously vulnerable to PostgreSQL interpolation failure if the SQL literal used `LIKE '% joined'`.
- Current code uses the escaped form `LIKE '%% joined'`, which is safe for PostgreSQL parameter translation.
- Current local audits prove the escaped query path can load Rooms and Groups.

## Frontend Response Mismatch

The frontend now logs message failures with:

- endpoint
- status
- trace id
- reason
- error type
- response body preview

The visible copy still collapses backend failures into `Messages could not load.` for 500-class failures. For 403 and 404, the backend returns more specific JSON:

- 403: `You do not have access to this chat.`
- 404: `Conversation not found.`

## Database Query Issue

Current local tests did not find a query issue. The highest-risk production query area remains message loading:

```sql
SELECT * FROM pulse_messages
WHERE conversation_id=?
  AND COALESCE(deleted_at,'')=''
  AND NOT (
    COALESCE(message_type,'') IN ('system','system_join','chat_event')
    AND lower(COALESCE(body,'')) LIKE '%% joined'
  )
ORDER BY id DESC
LIMIT ?
```

If production still fails, the next trace to collect is the `PULSE_COMM_MESSAGES_FAILED` log line for the returned `trace_id`.

## Exact Files Involved

- `/Users/hmcherie/Desktop/CoinPilotX/bot.py`
- `/Users/hmcherie/Desktop/CoinPilotX/services/chat_health_service.py`
- `/Users/hmcherie/Desktop/CoinPilotX/scripts/pulse_communications_audit.py`
- `/Users/hmcherie/Desktop/CoinPilotX/scripts/chat_actual_load_audit.py`
- `/Users/hmcherie/Desktop/CoinPilotX/scripts/messenger_core_audit.py`
- `/Users/hmcherie/Desktop/CoinPilotX/reports/database_error_root_causes.md`

## Local Validation Evidence

Passing audits on 2026-06-01:

- `scripts/pulse_communications_audit.py`: PASS
- `scripts/chat_actual_load_audit.py`: PASS
- `scripts/messenger_core_audit.py`: PASS

Coverage included:

- direct message open/send/load
- room list/load/send
- group create/list/send/load
- legacy direct bridge
- legacy room bridge
- legacy group bridge
- unauthorized 403
- missing conversation 404

## Recommendation

Repair first, replace later.

The current implementation can pass local end-to-end audits, so a full rebuild is not justified for this stabilization mission. Keep Rooms/Groups marked FAIL in production truth until:

1. The current code is deployed.
2. A production browser session opens `/pulse/messages`.
3. A room and group are selected.
4. Server logs show no `PULSE_COMM_MESSAGES_FAILED` trace for those selections.
5. The panel renders either messages or the clean empty state.
