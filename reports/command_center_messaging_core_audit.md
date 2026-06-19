# PulseSoc Messaging Core Audit

Date: 2026-06-19

## Current Messaging Architecture

PulseSoc Messages V2 is implemented in `pulse_communications_v2/` and is feature-gated through its existing flags. Legacy messaging routes remain available as fallbacks.

## Conversation Tables

- `comm_v2_conversations`: conversation identity, type, privacy, membership count, and latest activity.
- `comm_v2_participants`: membership, unread count, read cursor, mute/pin state, and participant activity.
- `comm_v2_user_settings`: presence privacy, read receipts, and message preview privacy.
- `comm_v2_blocks`: participant block relationships enforced by conversation access checks.

## Message Tables

- `comm_v2_messages`: message body/type, sender, conversation, delivery status, edit/delete timestamps, and client idempotency key.
- `comm_v2_attachments`: message media metadata and storage/playback references.
- `comm_v2_read_receipts`: per-message delivered/seen/read timestamps.
- `comm_v2_message_reactions`: per-user message reactions.
- `comm_v2_message_deletions`: per-user deletion state.
- `comm_v2_typing`: temporary typing state with expiry.

## Main Routes

- `GET /pulse/messages` and `GET /pulse/messages-v2`: Messages V2 page when enabled.
- `GET /api/pulse/communications/v2/conversations`: conversation list.
- `POST /api/pulse/communications/v2/conversations/<conversation>/messages`: send message.
- `GET /api/pulse/communications/v2/conversations/<conversation>/messages`: load messages and write delivered/read receipts.
- `POST /api/pulse/communications/v2/conversations/<conversation>/read`: clear unread state and write read receipts.
- `POST /api/pulse/communications/v2/conversations/<conversation>/typing`: update typing state.
- `GET /api/pulse/communications/v2/conversations/<conversation>/presence`: participant presence/typing state.
- `GET /api/pulse/communications/v2/realtime`: current realtime event polling fallback.

## Existing Unread Logic

- Sending increments `comm_v2_participants.unread_count` for recipients.
- Opening/reading a conversation sets the current participant unread count to zero.
- Conversation list payloads include per-conversation unread counts.
- Message-specific badge nodes use `data-chat-unread`; alert badges use separate `data-alert-unread` and `data-notification-unread` selectors.

## Existing Frontend

`static/js/pulse_messages_v2.js` already provides:

- conversation rendering and latest message previews
- unread conversation badges
- sent/delivery labels from message payloads
- typing indicator rendering
- presence dots
- realtime event handling
- a 12-second poll while visible and 45-second reduced poll while hidden
- BroadcastChannel/localStorage cross-tab updates

## Existing Behavior Preserved

Messages continue to save, read, react, edit, delete, and poll through the existing Main App database/service layer. Service 2C adds Command Center event mirroring and does not replace these paths.
