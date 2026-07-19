# PulseSoc Native Conversation Control Center Report

## Scope

Implemented the native Messenger Conversation Control Center as a production-wired surface instead of a static settings sheet.

## Backend reuse

- Reused canonical Communications V2 routes under `/api/pulse/communications/v2`.
- Added native API helpers for:
  - control-center load and settings PATCH
  - members
  - shared media
  - shared links
  - pinned messages
  - export
  - pin, mute, archive, mark unread
  - control-center safety/destructive actions
  - conversation-scoped message search
- Extended the existing message search service to honor `conversation_id`/`conversation_ref` after verifying the requesting user has access to that conversation.

## Native behavior changes

- Removed the duplicate generic Search quick action.
- Replaced permanent `Locked` and developer-facing contract text with product-facing unavailable states.
- Loaded server stats/settings/capabilities when the sheet opens.
- Added refresh state and retry behavior.
- Added real server-backed rows for notification, appearance, privacy, media, accessibility, and productivity settings.
- Added real detail panels for members, shared media, shared links, pinned messages, message stats, storage, and chat search.
- Added native export through the production export endpoint and the native share sheet.
- Added destructive confirmations before archive/delete/clear/reset/delete-media actions.

## Permission and safety model

- Every server mutation goes through the authenticated Communications V2 backend.
- The backend verifies conversation membership before returning control center data, search results, media, pins, links, export, or running actions.
- Block is only exposed for direct conversations.
- Leave Group is unavailable outside group conversations.
- Account-level security controls are explicitly unavailable from per-chat control center instead of pretending to mutate per-chat state.

## Known limitations

- Device-level visual QA still needs to be completed on simulator and physical iPhone after commit-ready verification.
- Account-level Trusted Devices, Active Sessions, and Security Log remain routed to global account security and are shown as unavailable in this per-conversation sheet.
- Native note/task prompts depend on iOS `Alert.prompt`; if unavailable on a device, the UI reports that text entry must be done from the web control center.

## Verification checklist

- `npm run --prefix mobile-native typecheck`
- `venv/bin/python -m py_compile pulse_communications_v2/service.py scripts/pulsesoc_native_conversation_control_center_audit.py`
- `venv/bin/python scripts/pulsesoc_native_conversation_control_center_audit.py`
- `git diff --check`
