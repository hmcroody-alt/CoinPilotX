# PulseSoc Call Start Diagnostics

## Root Cause Addressed

The user-visible call screen could collapse several different startup failures into one generic line:

```text
Calling is temporarily unavailable. Please try again.
```

That prevented diagnosis when LiveKit was configured but the call still failed before ringing. The generic text existed in the route exception wrapper and in the browser call-join fallback path.

## What Changed

- Backend call errors now include:
  - `error_code`
  - `error_title`
  - `error_description`
  - `remediation`
  - `correlation_id`
- Call-route backend exceptions now return `BACKEND_EXCEPTION` with a correlation ID.
- LiveKit configuration failures return `LIVEKIT_CONFIG_MISSING` instead of generic unavailable copy.
- Start-call token failures mark the call failed and return `LIVEKIT_TOKEN_FAILED`.
- The browser call UI renders a diagnostic failure panel instead of one generic status line.
- The browser logs `error_code` and `correlation_id` to the console.
- Local/development diagnostics can open `/admin/calls/<call_id>/delivery` through the call UI.

## Error Codes Added

- `LIVEKIT_CONFIG_MISSING`
- `LIVEKIT_TOKEN_FAILED`
- `MISSING_CONVERSATION`
- `RECIPIENT_OFFLINE`
- `SELF_CALL_BLOCKED`
- `RECIPIENT_NOT_IN_CONVERSATION`
- `RECIPIENT_BLOCKED`
- `CALL_ALREADY_ACTIVE`
- `CALL_NOT_FOUND`
- `CALL_ACCESS_DENIED`
- `CALL_PARTICIPANT_REQUIRED`
- `CALL_ALREADY_ENDED`
- `BACKEND_EXCEPTION`
- `UNKNOWN_ERROR`

The frontend can also show client-side codes:

- `LIVEKIT_CLIENT_NOT_LOADED`
- `LIVEKIT_ROOM_CONNECT_FAILED`
- `MEDIA_PERMISSION_DENIED`

## Startup Trace Now Available

The call path now exposes enough state to trace:

1. User taps Call.
2. `POST /api/calls/start` returns structured success or failure.
3. Failure responses include correlation IDs.
4. Call records that fail after creation are marked `failed`.
5. Token generation failures are recorded as `livekit_token_failed`.
6. Successful starts record `call_start_response_ready`.
7. Admin diagnostics expose notification, push, realtime, recipient, and overlay state.

## Remaining Production Proof Needed

If production still shows a failure, the next screenshot/log should include:

- error title
- error code
- remediation
- correlation ID
- call ID, if one was created

That will identify whether the failure is provider auth, room connection, token generation, browser permissions, stale deployment, or a backend exception.
