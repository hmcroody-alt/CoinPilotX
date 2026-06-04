# Pulse Communications 2.0 Build Plan

## Goal

Build Pulse Communications 2.0 beside the existing Pulse messages, rooms, and groups system without deleting or patching the legacy implementation. The new system was originally staged behind:

```text
PULSE_COMMUNICATIONS_V2_ENABLED=false
```

Published update, 2026-06-04: Communications V2 is now live by default. `/pulse/messages` serves V2 when `PULSE_COMMUNICATIONS_V2_ENABLED` is true, `/pulse/messages-v2` remains available directly, and `/pulse/messages?legacy=1` or `/pulse/messages-legacy` remains the rollback path.

## Delivered Foundation

- Dynamic feature flag helper in `pulse_communications_v2/flags.py`, now defaulting to true with env rollback support.
- New v2 database schema using only `comm_v2_*` tables.
- New service layer in `pulse_communications_v2/service.py`.
- New API routes in `pulse_communications_v2/routes.py`.
- New responsive UI in `templates/pulse_messages_v2.html`.
- New client assets in `static/css/pulse_messages_v2.css` and `static/js/pulse_messages_v2.js`.
- Focused audits in `pulse_communications_v2/foundation_audit.py` and `scripts/pulse_communications_v2_audit.py`.

## New Schema

The v2 schema is intentionally separate from legacy chat tables:

- `comm_v2_conversations`
- `comm_v2_participants`
- `comm_v2_messages`
- `comm_v2_attachments`
- `comm_v2_message_reactions`
- `comm_v2_read_receipts`
- `comm_v2_typing`
- `comm_v2_reports`
- `comm_v2_blocks`
- `comm_v2_moderation_events`
- `comm_v2_communities`
- `comm_v2_channels`

Legacy tables such as `pulse_conversations`, `pulse_messages`, `pulse_groups`, `private_messages`, and `conversation_members` are not used as the v2 backing store.

## API Surface

Core routes:

- `GET /api/pulse/communications/v2/health`
- `GET /api/pulse/communications/v2/conversations`
- `POST /api/pulse/communications/v2/conversations`
- `POST /api/pulse/communications/v2/direct/open`
- `POST /api/pulse/communications/v2/groups`
- `POST /api/pulse/communications/v2/rooms`
- `GET /api/pulse/communications/v2/rooms`
- `GET /api/pulse/communications/v2/conversations/<conversation_ref>/messages`
- `POST /api/pulse/communications/v2/conversations/<conversation_ref>/messages`
- `GET /api/pulse/communications/v2/conversations/<conversation_ref>/members`
- `POST /api/pulse/communications/v2/conversations/<conversation_ref>/members`
- `POST /api/pulse/communications/v2/conversations/<conversation_ref>/read`
- `POST /api/pulse/communications/v2/conversations/<conversation_ref>/typing`
- `GET /api/pulse/communications/v2/conversations/<conversation_ref>/presence`
- `POST /api/pulse/communications/v2/messages/<message_id>/reactions`
- `POST /api/pulse/communications/v2/messages/<message_id>/report`
- `POST /api/pulse/communications/v2/blocks`
- `GET /api/pulse/communications/v2/moderation`
- `POST /api/pulse/communications/v2/moderation/messages/<message_id>`

Community routes:

- `POST /api/pulse/communications/v2/communities`
- `POST /api/pulse/communications/v2/communities/<community_id>/channels`

Phase 2 placeholders:

- `POST /api/pulse/communications/v2/conversations/<conversation_ref>/voice/start`
- `POST /api/pulse/communications/v2/conversations/<conversation_ref>/video/start`

## Media Attachments

V2 uses the existing `/api/pulse/media/upload` endpoint and the existing `chat_media_uploads` persistence path. When a v2 message is sent with `media_ids`, the service verifies ownership, creates `comm_v2_attachments`, and updates the media row context to `pulse_comm_v2`.

## UI Plan

The `/pulse/messages-v2` page includes:

- Left conversation list with type filters and creation controls.
- Center message thread with attachment sending.
- Right intelligence/details panel with members, safety actions, and Phase 2 placeholders.
- Mobile layout that stacks the conversation list above the thread.
- Specific empty and disabled states instead of a generic failure message.

## Migration Plan

1. Serve V2 from `/pulse/messages` while keeping `/pulse/messages?legacy=1` and `/pulse/messages-legacy` available.
2. Monitor reports, blocks, moderation events, media attachment success, and route latency.
3. Keep `PULSE_COMMUNICATIONS_V2_ENABLED=false` as the emergency rollback switch.
4. Continue incremental improvements for notification templates and Phase 2 voice/video/live.

## Rollback Plan

1. Set `PULSE_COMMUNICATIONS_V2_ENABLED=false`.
2. Leave `comm_v2_*` tables in place for forensic review and export.
3. Stop writes to v2 API routes by leaving the flag disabled.
4. Use `/pulse/messages?legacy=1` or `/pulse/messages-legacy` for immediate legacy access.
5. If schema cleanup is required after a proven rollback window, drop only `comm_v2_*` tables.

## Audit Commands

```bash
python3 -m py_compile pulse_communications_v2/*.py scripts/pulse_communications_v2_audit.py
python3 pulse_communications_v2/foundation_audit.py
python3 scripts/pulse_communications_v2_audit.py
python3 scripts/site_functional_audit.py
python3 scripts/pulse_performance_audit.py
node --check static/js/pulse_messages_v2.js
git diff --check
```

## Validation Matrix

- Create DM: `scripts/pulse_communications_v2_audit.py`
- Send DM: `scripts/pulse_communications_v2_audit.py`
- Create group: `scripts/pulse_communications_v2_audit.py`
- Send group message: `scripts/pulse_communications_v2_audit.py`
- Create public room: `scripts/pulse_communications_v2_audit.py`
- Create private room: `scripts/pulse_communications_v2_audit.py`
- Send room message: `scripts/pulse_communications_v2_audit.py`
- Load history: `scripts/pulse_communications_v2_audit.py`
- Reload persistence: `scripts/pulse_communications_v2_audit.py`
- Permissions enforced: `scripts/pulse_communications_v2_audit.py`
- Media attachment: `scripts/pulse_communications_v2_audit.py`
- Read receipts: `scripts/pulse_communications_v2_audit.py`
- Typing indicators: `scripts/pulse_communications_v2_audit.py`
- Reactions: `scripts/pulse_communications_v2_audit.py`
- Reply/thread support: `comm_v2_messages.reply_to_message_id` and `thread_root_message_id`
- Block/report safety: `scripts/pulse_communications_v2_audit.py`
- Admin/mod tools: `scripts/pulse_communications_v2_audit.py`
