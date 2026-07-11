# PulseSoc Pulse Command Interactions

Status: first interaction-depth milestone complete.

## Added Native Interaction Coverage

- Long-press message action sheet.
- Reply target and composer reply preview.
- Reaction picker with server-backed `/api/pulse/messages/:message_id/react`.
- Optimistic reaction count with rollback on failure.
- Retry failed outbound messages using the existing send pipeline.
- Delete for self and delete for everyone through `/api/pulse/messages/:message_id/delete`.
- Report message through `/api/pulse/messages/:message_id/report`.
- Mute/block safety handoff to the existing Safety Hub.
- Deleted and moderated message visual states.
- Reply preview inside message bubbles.
- Reaction row inside message bubbles.

## Server Authority

All persistent mutations remain server-authoritative. The only optimistic behavior is temporary local reaction display while the native client waits for the existing backend response.

## Remaining Interaction Work

- Forward/share message action.
- Copy text action after a supported clipboard dependency is approved.
- Message details panel.
- Native conversation mute/unmute mutation if the backend exposes a user-safe endpoint.
- More complete reactions accessibility labels and VoiceOver ordering QA.
