# Messages Media Send Failure

## Summary

Observed production error:

- Surface: Pulse Messages V2 on mobile
- Domain: pulsesoc.com
- Trace shown to user: `b0d8b479a761`
- User-visible error: `Messenger is temporarily unavailable.`
- Upload state: image upload completed before message send failed

## Root Cause

The media upload and the message send are separate steps:

1. `/api/pulse/communications/v2/attachments/upload`
2. `/api/pulse/communications/v2/conversations/<id>/messages`

The upload path could succeed, but the message create path did not validate attachment IDs before inserting the message. If attachment linking failed or raised during `comm_v2_attachments` creation, the route could return a generic 500-style Messenger error instead of an actionable attachment/message error.

## Code Locations

- Frontend upload/send flow: `static/js/pulse_messages_v2.js`
- Attachment upload route: `pulse_communications_v2/routes.py`
- Message send route: `pulse_communications_v2/routes.py`
- Message persistence/linking: `pulse_communications_v2/service.py`
- Durable media validation: `services/media_service.py`
- Upload staging: `services/upload_progress_service.py`

## Fix Applied

- Added route-level exception logging in Communications V2 with:
  - method
  - path
  - metric
  - trace ID
  - content type
  - exception type
- Added attachment validation before message insertion.
- Message send now rejects invalid/missing/failed attachments before creating a message.
- Added clearer messages:
  - `Attachment invalid or expired. Please upload it again.`
  - `Attachment could not be verified. Please upload it again.`
  - `Conversation not found.`
  - `You do not have access to this conversation.`

## Validation Evidence

- `scripts/pulse_communications_v2_audit.py`: PASS
- `scripts/pulse_comm_v2_attachments_audit.py`: PASS
- Media message audit path confirms uploaded media can be attached to a new message and persists in history.

## Production Log Follow-Up

The old trace `b0d8b479a761` must be searched in Railway logs to recover the exact historical stack trace. After this fix, future traces will log under `PULSE_COMM_V2_ROUTE_EXCEPTION` or `COMM_V2_ATTACHMENT_*` with enough context to identify the failing step.
