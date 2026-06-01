# Pulse Communications Legacy Audit

Generated for Phase 1 of Pulse Communications 2.0. This audit is informational only; no existing routes, templates, tables, or behavior were removed or renamed.

## Executive Summary

The current Pulse Communications surface is already a layered system:

- Legacy Dashboard/private chat uses `conversations`, `conversation_members`, and `private_messages`.
- Pulse Messenger uses `pulse_conversations`, `pulse_conversation_participants`, `pulse_messages`, room tables, typing, reactions, receipts, reports, and chat health traces.
- `/pulse/messages` currently renders a restored Messenger UI and uses the unified `/api/pulse/communications/*` bridge for direct, room, and group message loading/sending.
- Compatibility endpoints remain for old dashboard threads, room aliases, and existing mobile/audit flows.
- The system includes polling/SSE-ready realtime helpers and a websocket orchestrator shim, but no full Socket.IO/Redis production transport is activated.

## Frontend Surfaces

### Pulse Messenger

- Route: `GET /pulse/messages`
- Handler: `pulse_messages_page()` returns `pulse_dashboard_messenger_page()`.
- Active page builder: `pulse_dashboard_messenger_page(active_thread_id=0)`.
- Optional thread route: `GET /pulse/messages/<conversation_id>` redirects to the same restored Messenger shell with an active conversation.
- Main UI features:
  - Left list with tabs for Conversations, Chat Room, and Group Chat.
  - Message panel with loading, empty, and error states.
  - Send composer disabled until a valid conversation/room/group is selected.
  - Mobile full-screen layout and compact list rows.
  - Create Group Chat modal.

### JavaScript

The active `/pulse/messages` JavaScript is inline in `pulse_dashboard_messenger_page()` in `bot.py`.

Important functions observed:

- `loadConversations()` calls `/api/pulse/communications/conversations?type=direct`.
- `loadRooms()` calls `/api/pulse/communications/rooms`.
- `loadGroups()` calls `/api/pulse/communications/groups`.
- `openChat()` calls `/api/pulse/communications/conversations/${id}/messages`.
- Send form posts to `/api/pulse/communications/conversations/${state.activeId}/messages`.
- Room IDs may be opened through room references, such as `room-general-pulse`.
- Legacy dashboard threads are represented as `legacy-<id>`.

Supporting static JavaScript:

- `static/js/pulse_chat_recovery.js`
  - Local cache for conversation lists and recent thread messages.
  - Offline/online presence copy.
  - Skeleton state for restoring/loading conversations.
- `static/js/pulse_realtime.js`
  - Realtime event polling/SSE-oriented Pulse infrastructure.
- `static/js/pulse_camera_engine.js`
  - Can send captured media to `/api/pulse/messages/send` when `destination === "message"`.

### Legacy Dashboard Chat

- Template: `templates/dashboard.html`.
- Legacy UI functions call:
  - `/api/chat/threads`
  - `/api/chat/thread/<thread_id>`
  - `/api/chat/thread/<thread_id>/new`
  - `/api/chat/thread/<thread_id>/send`
  - `/api/pulse/messages/room/open`
  - `/api/pulse/messages/group/create`
- This layer still serves old private chat behavior and should remain untouched until a compatibility migration is proven.

## Backend Route Map

### Legacy Private Chat

- `GET /messages`
- `GET /messages/<conversation_id>`
- `POST /api/messages/start`
- `GET /api/chat/threads`
- `POST /api/chat/start`
- `GET /api/chat/thread/<thread_id>`
- `GET /api/chat/thread/<thread_id>/new`
- `POST /api/chat/thread/<thread_id>/send`

Primary service:

- `services/chat_realtime_service.py`

Legacy tables:

- `conversations`
- `conversation_members`
- `private_messages`

### Pulse Communications Bridge

- `GET /api/pulse/communications/conversations`
- `GET /api/pulse/communications/rooms`
- `GET /api/pulse/communications/groups`
- `GET /api/pulse/communications/conversations/<conversation_ref>/messages`
- `POST /api/pulse/communications/conversations/<conversation_ref>/messages`

Supported conversation references:

- Numeric Pulse conversation ID, for example `42`.
- Legacy dashboard thread ID, for example `legacy-42`.
- Room slug reference, for example `room-general-pulse`.

The bridge normalizes:

- Direct Pulse conversations.
- Legacy Dashboard direct conversations.
- Room conversations.
- Group/community/live conversations.

### Pulse Message Compatibility Routes

- `POST /api/pulse/messages/direct/open`
- `POST /api/pulse/messages/start`
- `POST /api/pulse/messages/send`
- `GET /api/pulse/messages/conversations`
- `GET /api/pulse/messages/<conversation_id>`
- `POST /api/pulse/messages/<conversation_id>/send`
- `GET /api/pulse/messages/<conversation_id>/presence`
- `GET /api/pulse/messages/group-conversations`
- `POST /api/pulse/messages/groups/create`
- `POST /api/pulse/messages/group/create`
- `POST /api/pulse/messages/group/<conversation_id>/update`
- `POST|DELETE /api/pulse/messages/group/<conversation_id>/members`

### Room Compatibility Routes

- `POST /api/pulse/messages/room/open`
- `GET /api/pulse/messages/rooms`
- `POST /api/pulse/messages/rooms/<room_id>/join`
- `GET|POST /api/pulse/messages/rooms/<room_id>/messages`
- Alias routes include `/api/pulse/chatrooms*`, `/api/chat-room`, and `/api/chat-room/<room_id>/messages`.

## Current Database Model

### Pulse Core Tables

- `pulse_message_threads`
  - Links old direct thread concepts to Pulse conversations.
- `pulse_conversations`
  - `conversation_type`, `group_id`, `linked_group_id`, `linked_space_id`, `linked_live_id`, `title`, `description`, `avatar_url`, `privacy`, `is_public`, `participant_limit`, `member_count`, status/deletion timestamps.
- `pulse_conversation_participants`
  - User membership, role, muted/archive state, joined/left timestamps, read state, unread count, pinned state.
- `pulse_messages`
  - Body, sender, conversation ID, message type, media URL, thumbnail URL, metadata, reply, delivery/seen/read state, idempotency client ID.
- `pulse_message_reactions`
- `pulse_message_reports`
- `pulse_message_receipts`
- `pulse_conversation_typing`
- `pulse_chat_rooms`
- `pulse_chat_room_members`
- `pulse_chat_room_messages`
- `pulse_chat_health_traces`
- `pulse_chat_recovery_events`

The schema is guarded by `ensure_pulse_messenger_schema(cur, conn)`, which creates missing tables and adds missing columns for production databases that lag behind migrations.

## Permissions And Auth

- Frontend pages use `require_account()`.
- JSON APIs use `api_account_user()`.
- Direct conversations require membership.
- Rooms can auto-join public room conversations.
- Group/community chats require participant membership, group membership, or elevated role depending on the action.
- Group update/member management checks owner/admin role or app admin privilege.
- Legacy dashboard private messages check `conversation_members`.
- Private chat blocking checks are present for direct conversation creation and old private message sending.

## Direct, Rooms, And Groups Behavior

### Direct Messages

Current direct messages can originate from:

- Legacy dashboard direct thread tables.
- Pulse direct conversations.
- Bridge responses that merge both into the direct list.

The active `/pulse/messages` frontend uses `/api/pulse/communications/conversations?type=direct`, then opens messages through `/api/pulse/communications/conversations/<id>/messages`.

### Rooms

Default rooms are created/updated on demand through `pulse_ensure_default_rooms()`. Room messages are stored in `pulse_messages` with room linkage via `pulse_chat_room_messages`. Public room membership is added as users open/join rooms.

Default room keys:

- `general-pulse`
- `crypto-education`
- `ai-builders`
- `cybersecurity`
- `creator-lounge`
- `marketplace-help`
- `reels-music`
- `live-stage`

### Groups

Group chats use `pulse_conversations` with group-like conversation types such as `group`, `community_group`, `creator`, and `live`. Group conversations may be linked to `pulse_groups` through `group_id` or `linked_group_id`.

## Realtime And Health

- `services/realtime_engine.py`
  - In-memory channel buffer, coalescing for typing/presence/heartbeat events, online session tracking, polling health snapshot.
- `services/websocket_orchestrator.py`
  - Connection registration, heartbeat, reconnect token, ack tracking, reconnect policy, health snapshot.
- `services/chat_health_service.py`
  - Trace recording, recovery event recording, table health counts, recovery payloads.

These are production-safe foundations but are not a full Redis/Socket.IO cluster transport.

## Risks And Duplications

- There are two direct-message data models: legacy `conversations/private_messages` and Pulse `pulse_conversations/pulse_messages`.
- `/pulse/messages` currently depends on inline JavaScript in `bot.py`, which makes isolated frontend testing harder.
- Compatibility route aliases are broad and must stay stable during migration.
- Room open/list/message flows can create/update database rows on GET/List actions.
- Realtime infrastructure is in-memory; multi-instance deployments need Redis PubSub or another shared broker before live socket behavior can be considered production-complete.
- Legacy direct thread IDs can conflict numerically with Pulse conversation IDs unless references are namespaced; the current bridge correctly uses `legacy-<id>`.

## Phase 1 Safety Finding

No old route, template, model, or behavior should be removed in Phase 1. Pulse Communications 2.0 should be built beside the current system and gated behind `PULSE_COMMUNICATIONS_V2_ENABLED=false` until migrations, audits, and rollback tests pass.

