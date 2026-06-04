# Pulse Communications V2 Phase 1 Messaging

## Scope

Phase 1 completes the production messaging foundation while keeping later voice, video, group call, and large media reliability work behind future phases.

## Implemented

- Presence heartbeat with online, offline, last seen, and active-now states.
- Presence privacy settings: everyone, contacts, nobody.
- Typing indicators for DMs and groups with compact multi-user summaries.
- Read receipt states: sent, delivered, seen.
- Read receipt privacy setting.
- Emoji reactions with add/remove and aggregated counts.
- Replies with reply preview and jump-to-message behavior.
- Message forwarding to other conversations.
- Message editing with an owner-only time window and edited indicator.
- Message deletion for self and owner-only delete-for-everyone with a time window.
- Message lifecycle actions are available from the message action menu to avoid clutter.

## Phase Gate

Phase 1 intentionally does not ship voice notes, audio calls, video calls, group calls, or realtime WebSocket infrastructure. Those remain in later phases behind:

- `PULSE_VOICE_NOTES_ENABLED`
- `PULSE_AUDIO_CALLS_ENABLED`
- `PULSE_VIDEO_CALLS_ENABLED`
- `PULSE_GROUP_CALLS_ENABLED`

## Validation

Required validation for Phase 1:

- Python compile
- JavaScript parse
- Communications V2 audit
- Presence audit
- Typing indicator audit
- Read receipt audit
- Security audit
- Mobile QA
- Desktop QA
- Site functional audit
- Performance audit
- `git diff --check`
