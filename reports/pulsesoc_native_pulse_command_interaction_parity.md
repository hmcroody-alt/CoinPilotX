# PulseSoc Native Pulse Command Interaction Parity

Status: production workflow preserved, deeper nested actions still pending.

## Production interaction parity

- Current estimate: 82%.

## Preserved and reused

- Inbox refresh and search use existing Messenger APIs.
- Conversation open routes to the existing native `ChatScreen`.
- Reply uses existing reply payload fields.
- Reaction uses the server-authoritative `reactToMessage` mutation.
- Delete uses the server-authoritative `deleteMessage` mutation.
- Report uses the server-authoritative `reportMessage` mutation.
- Send uses `sendConversationMessage` with client message idempotency.
- Typing uses the existing `/typing` contract.
- Seen/read uses the existing `/seen` contract.
- Offline cached inbox/thread paths remain intact.

## Current gaps

- Context menu parity still lacks full copy, forward/share, edit, and message-details depth.
- Conversation-level mute/block/pin actions route through partial safety boundaries instead of the full production direct controls.
- Offline/reconnect parity still needs API-disruption QA evidence.
- Attachments need full preview/cancel/retry/download state parity.
- Voice-message recording/playback is still partly provider/device-release QA.

## No removed controls

No production Messenger tab or major route was removed in this pass.
