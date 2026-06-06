# Communications V2 Attachment And Voice Fix

Date: 2026-06-06

## Incident Trace

Requested trace: `3dac75efd9f3`

Result: the trace was not present in the local `coinpilotx.log`, so the original production exception, stack trace, and exact production database statement could not be extracted from this workspace.

## Fixed Pipeline

- Upload endpoint: `POST /api/pulse/communications/v2/attachments/upload`
- Send endpoint: `POST /api/pulse/communications/v2/conversations/<conversation_ref>/messages`
- Upload staging remains in `chat_media_uploads`.
- Message creation inserts into `comm_v2_messages`.
- Attachment linking inserts into `comm_v2_attachments`.
- Uploaded media rows are linked back to the message with `message_id`, `context_type='pulse_comm_v2'`, and the conversation context.
- Notification dispatch and realtime broadcast now run after commit and cannot roll back a successful message.

## Added Diagnostics

The backend now logs these steps:

- `conversation_access`
- `validate_attachments`
- `insert_message`
- `attach_media`
- `insert_attachment`
- `link_upload`
- `update_conversation`
- `update_participants`
- `mark_read`
- `message_payload`
- `commit`
- notification dispatch
- realtime broadcast

## UX Fix

- Voice note preview now appears directly above the composer.
- Recording completion is explicit: “Recording complete. Ready to send.”
- A dedicated `Send voice note` button appears inside the preview.
- The main send button switches its accessible label/title when a voice note is ready.
- Empty composer status text hides to reduce vertical space.
