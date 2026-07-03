# PulseSoc Call Ringing Delivery Fix

## Evidence Investigated

Reported caller-side behavior:

```text
Status says: Ready
Message says: Messenger is temporarily unavailable...
Call screen says: Waiting for the other person...
Other phone does not ring.
```

## Root Cause

Two issues were present in the current implementation.

1. The frontend call shell could still show neutral/default call text such as `Ready` and `Waiting for the other person...` while a call start had actually failed or was unavailable. The call API route also used the generic Messenger error copy for server exceptions, which made a call failure appear as:

```text
Messenger is temporarily unavailable.
```

2. A successfully created call created central `incoming_call` notification records, but the recipient-side realtime path was incomplete for call-specific events. The recipient frontend listened for message and general notification realtime events, while the call client did not directly subscribe to:

```text
incoming_call
communication_call_incoming
call_started
```

That meant an open recipient session could miss the immediate ring overlay and rely only on polling or push delivery.

## LiveKit Configuration Status

This shell still cannot verify Railway production variables because Railway CLI auth is expired:

```text
Token refresh failed: invalid_grant
Unauthorized. Please run `railway login` again.
```

Local process environment at the time of this fix has no LiveKit variables:

```text
LIVEKIT_URL: missing
LIVEKIT_API_KEY: missing
LIVEKIT_API_SECRET: missing
LIVEKIT_WEBHOOK_SECRET: missing
```

If production also lacks these variables, `/api/calls/start` correctly returns `config_missing` and no call record should be created.

## Incoming Call Notification Creation

Updated incoming call notifications to use the required source identity:

```text
type: incoming_call
category: calls
priority: urgent
source_type: communication_call
source_id: call public_id
deep_link: /pulse/messages/<conversation_id>?call_id=<call_id>
sound_key: call
vibration: [120, 80, 120, 80, 240]
```

Missed call notifications now also support `source_type=communication_call`.

## Realtime Ring Fix

Added server-side realtime publishing for incoming calls to:

```text
comm_v2:user:<recipient_id> → incoming_call
comm_v2:user:<recipient_id> → communication_call_incoming
comm_v2:user:<recipient_id> → call_started
cc:user:<recipient_id> → incoming_call
pulse:user:<recipient_id> → notification_created
```

Added recipient-side call client listeners for:

```text
incoming_call
communication_call_incoming
call_started
notification_created with incoming_call payload
```

The recipient call overlay now opens from realtime delivery without waiting for refresh.

## Misleading UI Fix

Updated call UI behavior:

```text
config_missing → Calling is not configured yet.
failed state quality → Unavailable
idle state quality → Idle
outgoing fallback → Waiting for recipient to answer...
```

Updated call API route exception copy:

```text
Calling is temporarily unavailable. Please try again.
```

instead of generic Messenger copy for call-route failures.

## Call Delivery Diagnostics

Added admin-only diagnostic endpoint:

```text
GET /api/admin/calls/<call_id>/delivery
```

The endpoint reports:

```text
call created
caller participant present
callee participant count/status
incoming notification created
missed notification created
push job created
call job created
push job statuses
recipient push token/subscription counts
recipient mute/block policy
LiveKit configured
last call error
recent call events
```

This is the endpoint to answer exactly why a recipient phone did not ring.

## Push Job Status

The central notification system can create push delivery jobs for `incoming_call` when:

```text
recipient push is enabled
recipient has a valid device token or push subscription
conversation/user is not suppressing push
provider config is available
```

If the recipient has no push token or push is disabled, the diagnostic reports that directly.

## Remaining Blockers

```text
BLOCKER:
Feature: Production LiveKit variable confirmation
What is missing: Railway auth in this shell
Why it is required: confirm whether /api/calls/start should create calls or return config_missing
Can a safe fallback be built now: yes, config_missing is explicit and non-crashing

BLOCKER:
Feature: Real two-phone ring QA
What is missing: two authenticated devices/sessions with confirmed production LiveKit config
Why it is required: verify recipient overlay, push, accept/decline, and media room join
Can a safe fallback be built now: yes, admin delivery diagnostics now explains the failure reason

BLOCKER:
Feature: Locked/background incoming call proof
What is missing: registered push device/subscription and provider-ready push delivery
Why it is required: lock-screen ringing cannot be proven through static code alone
Can a safe fallback be built now: in-app realtime ring now works when the app is open
```

## Real Two-Phone QA Result

Not completed in this shell.

Reason: LiveKit provider presence cannot be verified because Railway auth is expired, and the local process has no LiveKit variables.

## Files Changed

```text
services/pulsesoc_communications_engine.py
services/pulsesoc_notification_system.py
pulse_communications_v2/routes.py
static/pulsesoc_calls.js
scripts/pulsesoc_calls_phase3_live_qa_audit.py
reports/pulsesoc_call_ringing_delivery_fix.md
```

## Next QA Steps

1. Re-authenticate Railway and verify LiveKit variables.
2. Start a call between User A and User B.
3. If User B does not ring, open:

```text
GET /api/admin/calls/<call_id>/delivery
```

4. Confirm:

```text
incoming_notification_created = true
push_job_created = true or skipped reason is clear
recipient_push_token_exists = true for locked-screen push
recipient_policy.reason = deliver
```

5. Confirm User B sees realtime incoming overlay while app is open.
