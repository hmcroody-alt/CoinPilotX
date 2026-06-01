# Pulse Communications 2.0 Blueprint

Phase 1 scope: blueprint and disabled feature flag only. This document proposes the new system; it does not activate it.

## Feature Flag

Default flag:

```text
PULSE_COMMUNICATIONS_V2_ENABLED=false
```

Rules:

- Keep v2 inert while the flag is false.
- Do not register v2 routes over existing route names until v2 has passed audits.
- Keep legacy bridge routes available through the full migration.
- Prefer additive schema changes and backfills over destructive changes.

## Product Scope

Pulse Communications 2.0 should support:

- Direct Messages
- Groups
- Communities
- Channels
- Voice Rooms
- Video Rooms
- File sharing
- AI assistants
- UNDX collaboration
- Wallet Guardian link/scam protection
- Notifications
- Presence
- Message search

## Architecture Principles

- One conversation abstraction, many conversation types.
- One message service for text, media, system events, calls, and future assistant output.
- Stable namespaced IDs so legacy dashboard threads cannot collide with Pulse conversation IDs.
- Compatibility adapters before migrations.
- Realtime-first service design with polling fallback.
- No private message content in logs.
- Security and moderation hooks on every send/upload/link event.

## Conversation Types

Use a canonical `conversation_type` enum:

- `direct`
- `group`
- `community`
- `channel`
- `room`
- `voice_room`
- `video_room`
- `live`
- `project`
- `undx_workspace`
- `assistant`

## Proposed Database Schema

### `pulse_comm_conversations`

- `id`
- `public_id`
- `conversation_type`
- `title`
- `description`
- `avatar_url`
- `owner_user_id`
- `created_by_user_id`
- `linked_group_id`
- `linked_community_id`
- `linked_channel_id`
- `linked_live_id`
- `linked_project_id`
- `privacy`
- `visibility`
- `status`
- `is_discoverable`
- `participant_limit`
- `member_count`
- `last_message_id`
- `last_message_at`
- `last_activity_at`
- `created_at`
- `updated_at`
- `archived_at`
- `deleted_at`

### `pulse_comm_members`

- `id`
- `conversation_id`
- `user_id`
- `role`
- `membership_state`
- `joined_at`
- `left_at`
- `muted_until`
- `notifications_level`
- `last_seen_at`
- `last_read_message_id`
- `last_read_at`
- `unread_count`
- `pinned_at`
- `created_at`
- `updated_at`

### `pulse_comm_messages`

- `id`
- `public_id`
- `conversation_id`
- `sender_user_id`
- `message_type`
- `body`
- `rich_body_json`
- `media_id`
- `reply_to_message_id`
- `thread_root_message_id`
- `client_message_id`
- `delivery_status`
- `moderation_status`
- `wallet_guardian_status`
- `metadata_json`
- `created_at`
- `updated_at`
- `edited_at`
- `deleted_at`

### `pulse_comm_attachments`

- `id`
- `message_id`
- `media_type`
- `storage_provider`
- `r2_key`
- `mux_asset_id`
- `url`
- `thumbnail_url`
- `mime_type`
- `file_size`
- `duration_seconds`
- `width`
- `height`
- `scan_status`
- `created_at`

### `pulse_comm_reactions`

- `id`
- `message_id`
- `conversation_id`
- `user_id`
- `reaction_type`
- `created_at`
- `updated_at`

### `pulse_comm_receipts`

- `id`
- `message_id`
- `conversation_id`
- `user_id`
- `delivered_at`
- `seen_at`
- `read_at`

### `pulse_comm_presence`

- `id`
- `user_id`
- `conversation_id`
- `connection_id`
- `presence_state`
- `typing_until`
- `last_seen_at`
- `metadata_json`

### `pulse_comm_calls`

- `id`
- `conversation_id`
- `call_type`
- `state`
- `host_user_id`
- `started_at`
- `ended_at`
- `recording_status`
- `mux_live_stream_id`
- `turn_region`
- `metadata_json`

### `pulse_comm_legacy_map`

- `id`
- `legacy_source`
- `legacy_id`
- `conversation_id`
- `migration_state`
- `created_at`
- `updated_at`

Use this table to bridge:

- `conversations.id`
- `private_messages.id`
- `pulse_conversations.id`
- `pulse_messages.id`

## Service Layer Proposal

Create `pulse_communications_v2/` as the service home after Phase 1:

- `flags.py`
- `models.py`
- `repository.py`
- `service.py`
- `permissions.py`
- `adapters.py`
- `media.py`
- `realtime.py`
- `wallet_guardian.py`
- `undx.py`
- `serializers.py`
- `routes.py`

Core service methods:

- `list_conversations(user_id, kind, cursor, limit)`
- `get_conversation(user_id, conversation_ref)`
- `list_messages(user_id, conversation_ref, cursor, limit)`
- `send_message(user_id, conversation_ref, payload)`
- `create_conversation(user_id, payload)`
- `join_room(user_id, room_ref)`
- `create_group(user_id, payload)`
- `start_voice_room(user_id, conversation_ref)`
- `start_video_room(user_id, conversation_ref)`
- `attach_file(user_id, conversation_ref, upload_ref)`

## API Routes Proposal

Keep existing routes during migration. Add v2 routes only when the feature flag is enabled.

### Conversations

- `GET /api/pulse/communications/v2/conversations`
- `POST /api/pulse/communications/v2/conversations`
- `GET /api/pulse/communications/v2/conversations/<conversation_ref>`
- `GET /api/pulse/communications/v2/conversations/<conversation_ref>/messages`
- `POST /api/pulse/communications/v2/conversations/<conversation_ref>/messages`
- `PATCH /api/pulse/communications/v2/conversations/<conversation_ref>`

### Members And Presence

- `GET /api/pulse/communications/v2/conversations/<conversation_ref>/members`
- `POST /api/pulse/communications/v2/conversations/<conversation_ref>/members`
- `DELETE /api/pulse/communications/v2/conversations/<conversation_ref>/members/<user_id>`
- `POST /api/pulse/communications/v2/conversations/<conversation_ref>/read`
- `POST /api/pulse/communications/v2/conversations/<conversation_ref>/typing`
- `GET /api/pulse/communications/v2/conversations/<conversation_ref>/presence`

### Rooms, Communities, Channels

- `GET /api/pulse/communications/v2/rooms`
- `POST /api/pulse/communications/v2/rooms/<room_ref>/join`
- `GET /api/pulse/communications/v2/groups`
- `POST /api/pulse/communications/v2/groups`
- `GET /api/pulse/communications/v2/communities`
- `GET /api/pulse/communications/v2/channels`

### Voice And Video

- `POST /api/pulse/communications/v2/conversations/<conversation_ref>/voice/start`
- `POST /api/pulse/communications/v2/conversations/<conversation_ref>/video/start`
- `POST /api/pulse/communications/v2/calls/<call_id>/join`
- `POST /api/pulse/communications/v2/calls/<call_id>/leave`

### Media

- `POST /api/pulse/communications/v2/uploads/sign`
- `POST /api/pulse/communications/v2/uploads/complete`
- `GET /api/pulse/communications/v2/media/<media_id>`

## WebSocket / Socket.IO Plan

Transport strategy:

- Keep current polling/SSE fallback.
- Add Socket.IO or websocket gateway behind `PULSE_COMMUNICATIONS_V2_ENABLED`.
- Use room channels:
  - `pulse:comm:conversation:<id>`
  - `pulse:comm:user:<id>`
  - `pulse:comm:presence:<id>`
  - `pulse:comm:call:<id>`
- Require authenticated session or signed websocket token.
- Acknowledge message delivery and replay missed events after reconnect.
- Rate-limit typing and presence events.

Event names:

- `message.created`
- `message.updated`
- `message.deleted`
- `reaction.changed`
- `conversation.updated`
- `presence.changed`
- `typing.started`
- `typing.stopped`
- `call.started`
- `call.ended`
- `wallet_guardian.warning`
- `undx.insight.created`

## Redis PubSub Plan

Use Redis for multi-instance delivery:

- PubSub channels mirror websocket channels.
- Store replay windows in Redis streams with bounded retention.
- Keep message persistence in the database as source of truth.
- Deduplicate events by `event_id` and `client_message_id`.
- Use backpressure and reconnect jitter to avoid storms.

Suggested keys:

- `pulse:comm:stream:conversation:<id>`
- `pulse:comm:presence:<conversation_id>`
- `pulse:comm:user:<user_id>:inbox`
- `pulse:comm:rate:<user_id>`

## R2 / Mux Media Plan

R2:

- Direct-to-R2 signed upload for files/images/audio.
- Store only R2 key and safe metadata in DB.
- Run MIME validation and size limits before completion.
- Use lifecycle policies for abandoned pending uploads.

Mux:

- Use Mux for video messages, live/video rooms, replay clips, and transcoding.
- Store `mux_asset_id`, `playback_id`, duration, and thumbnail.
- Never block text message sending on video processing.
- Show processing state in message payload until playable.

## TURN/STUN Live Communication Plan

- Configure STUN/TURN through environment variables.
- Never expose TURN credentials directly unless ephemeral/signed.
- Generate short-lived ICE credentials per call.
- Support region selection based on latency and user location.
- Fall back to audio-only when video bandwidth is poor.
- Persist call events, not raw media, unless recording is explicitly enabled.

## UNDX Intelligence Integration

UNDX should be an assistant participant and insight layer, not a replacement for user conversations.

Planned hooks:

- Conversation summarization for users with permission.
- Project room context extraction.
- Action item detection.
- Scam/risk signal review in public/community rooms.
- AI assistant messages with explicit `message_type='assistant'`.
- Opt-in UNDX collaboration sessions tied to `undx_workspace` conversations.

Privacy rules:

- Do not train on or summarize private direct messages without explicit user action.
- Respect conversation membership.
- Log metadata and trace IDs, not private message bodies.

## Wallet Guardian Link And Scam Protection

Every outbound message with a URL should pass through a Wallet Guardian check:

- Extract URLs server-side.
- Score domain and address risk.
- Flag wallet drainers, suspicious contracts, phishing domains, and impersonation.
- Return inline warnings without blocking safe messages.
- Block clearly dangerous payloads in public rooms when policy requires it.
- Store risk status in `wallet_guardian_status` and `metadata_json`, not raw secrets.

## Migration Plan

1. Keep current system live.
2. Add v2 tables with no route changes.
3. Backfill `pulse_comm_legacy_map` for old direct and Pulse conversations.
4. Dual-read from v2 adapters in shadow mode.
5. Dual-write new messages to old and v2 tables for test users only.
6. Compare counts, message order, permissions, and read state.
7. Enable v2 for staff/admin accounts.
8. Enable v2 for a small percentage of users.
9. Keep rollback switch available.
10. After sustained parity, route `/pulse/messages` to v2.
11. Keep old read bridge for historical messages.
12. Only deprecate old writes after backups and audits pass.

## Rollback Plan

- Set `PULSE_COMMUNICATIONS_V2_ENABLED=false`.
- Stop registering v2 routes/UI entry points.
- Keep legacy writes active during rollout.
- If dual-write was enabled, continue reading old tables as source of truth.
- Preserve `pulse_comm_legacy_map` for future retry.
- Do not delete v2 tables during rollback.
- Run messenger core, site functional, Pulse feed, UNDX, admin, auth, and Wallet Guardian audits after rollback.

## Validation Plan

Required before activation:

- Python compile check.
- JavaScript parse check.
- Site functional audit.
- Pulse route audit.
- Messenger core audit.
- Direct message send/load audit.
- Room list/join/send/load audit.
- Group create/list/send/load audit.
- 403/404/500 error-state audit.
- Mobile and desktop browser QA.
- UNDX homepage audit.
- Admin route audit.
- Auth/session audit.
- Wallet Guardian link scan audit.
- Realtime reconnect/replay audit.
- Redis PubSub staging test.
- R2 upload and Mux playback staging test.
- TURN/STUN staging test for voice/video rooms.

## Phase 1 Non-Goals

- Do not modify production data.
- Do not migrate rows.
- Do not activate v2 UI.
- Do not remove old routes.
- Do not rename old routes.
- Do not replace `/pulse/messages`.
- Do not change Direct Messages, Pulse feed, UNDX, Wallet Guardian, admin, or auth behavior.

## Phase 2 Implementation Notes

Phase 2 added the inactive backend/data foundation only.

Added package modules:

- `pulse_communications_v2/models.py`
  - Defines v2-prefixed table contracts for `CommV2Conversation`, `CommV2Message`, `CommV2Participant`, `CommV2Community`, `CommV2Channel`, `CommV2Attachment`, `CommV2MessageReaction`, and `CommV2ReadReceipt`.
  - Table names use the `comm_v2_` prefix to avoid collisions with `conversations`, `private_messages`, `pulse_conversations`, and `pulse_messages`.
  - Includes `ensure_schema(cur)` for a future explicit migration call; it is not invoked by current app boot.
- `pulse_communications_v2/schemas.py`
  - Adds small dataclass contracts for future service payloads and responses.
- `pulse_communications_v2/service.py`
  - Adds no-op methods for `create_conversation`, `list_conversations`, `send_message`, `list_messages`, `create_community`, and `create_channel`.
  - Methods return disabled responses while `PULSE_COMMUNICATIONS_V2_ENABLED=false`.
- `pulse_communications_v2/permissions.py`
  - Adds closed-by-default permission stubs for viewing, sending, community management, and channel moderation.
- `pulse_communications_v2/routes.py`
  - Adds the disabled health blueprint at `/api/pulse/comm/v2/health`.
  - The route returns `{"enabled": false, "status": "disabled"}` while the flag is false.
- `pulse_communications_v2/foundation_audit.py`
  - Verifies the flag default, disabled health response, prefixed model contracts, closed permission stubs, no-op services, and absence of v2 navigation on production pages.

What remains inactive:

- No v2 UI is routed from `/pulse/messages`.
- No current Direct, Rooms, or Groups endpoints are replaced.
- No destructive database operation runs.
- No v2 schema installer is called during app startup.
- No v2 service writes to legacy or production data.
- No websocket, Redis, R2, Mux, TURN/STUN, UNDX, or Wallet Guardian v2 integration is active yet.

Next activation gates:

- Add explicit migration command for `comm_v2_*` tables.
- Add shadow-read adapters from legacy and Pulse tables.
- Add dual-write only for controlled staff testing.
- Add route registration guards for future non-health v2 endpoints.
- Add browser QA before any production UI opt-in.
