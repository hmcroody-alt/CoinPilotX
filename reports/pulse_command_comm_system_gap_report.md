# PulseSoc Communication System Gap Report

Updated: 2026-06-20

## What Was Already Wired

- `/pulse/messages` and `/pulse/messages-v2` use Communications V2 when enabled.
- Conversations, groups, rooms, search, typing, read receipts, unread counts, presence dots, and fallback polling are backed by existing V2 routes.
- Message send persists to `comm_v2_messages`.
- Attachments and voice notes upload through the V2 attachment path and validate ownership before send.
- Message activity records are created as `comm_v2_message` notification entities.
- Pulse Shield scans message text and suspicious links without auto-deleting or auto-banning.
- AI UI stays hidden unless Command Center AI is enabled.

## What Was Missing

- The V2 message reaction UI was calling a legacy message reaction route instead of the V2 action route.
- Audio/video call controls were not present in the final thread header even though protected placeholder routes already existed.
- The visual system had several older CSS generations layered together, which made the page feel inconsistent across device sizes.
- There was no focused report separating communication UI wiring from the remaining device push-notification problem.

## Fixes Applied

- Message reactions now call the protected V2 endpoint: `/api/pulse/communications/v2/messages/<message_id>/reactions`.
- Added Audio and Video call buttons to the conversation header.
- Audio and Video buttons call the existing protected gated endpoints and return the current next-phase status instead of acting like calls are live.
- Added a final scoped Pulse Command OS CSS layer for the futuristic command-center design:
  - glass panels
  - radar/grid background
  - presence ring treatment
  - premium conversation rows
  - lightweight chat bubbles
  - responsive mobile density
  - reduced-motion support preserved

## Still Missing Before Notifications Are Complete

- Real device message push delivery must still prove provider request, provider response, OS lock-screen delivery, sound, vibration, badge, and deep link.
- Device tokens must be present for each test device and confirmed active in production.
- Push delivery should remain separate from general Alerts counts and separate from chat unread counts.

## Security Notes

- No secrets or provider credentials are exposed.
- Calls remain authenticated and gated.
- Attachments still validate media ownership and file constraints before message send.
- Suspicious links are warned before open; messages are flagged for review but not deleted automatically.
- Existing block/mute/private preview policies remain server-side concerns for notification delivery.
