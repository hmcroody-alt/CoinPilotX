# PulseSoc Pulse Command LogiNexus Completion

Status: active vertical completion, not complete.

## Completed This Milestone

- Kept the existing native Messenger, Chat, Groups, Calls, and UNDX architecture. No replacement screens were created.
- Added local-only, explicit Messenger QA fixtures gated by `EXPO_PUBLIC_PULSESOC_QA_MESSENGER_FIXTURES=1` and localhost API base URLs only.
- Extended native Messenger API typing for pinned, muted, typing, failed, trust, sender, reply, edited, deleted, moderated, and reaction states.
- Added server-backed native helpers for message reaction, message delete, message report, and conversation pin routes.
- Completed the first native Pulse Command interaction layer:
  - reply target state
  - reaction pills
  - long-press message action sheet
  - retry failed message
  - report message
  - delete for self / delete for everyone where authorized by backend
  - mute/block handoff into Safety Hub
- Changed the Pulse Command inbox tabs so Chats, Calls, Groups, and Rooms render in the same native command surface instead of immediately bouncing away.
- Added native row presentations for calls, groups, and rooms while preserving existing call/group/room APIs and detail routes.

## Reused

- Existing `/api/pulse/messages/*` conversation, sync, send, seen, typing, search, media upload, react, delete, report, and pin routes.
- Existing `getActiveCalls`, call screen, and call provider boundary.
- Existing `listGroups`, `openGroupChat`, `listRooms`, and `joinRoom` APIs.
- Existing `NativeMediaViewer`.
- Existing `SafetyHub` for mute/block/report boundary workflows.
- Existing `PulseCommand` shared primitives.

## Still Incomplete

- Message forward/share and details surfaces are not complete.
- Conversation mute/unmute is still a Safety Hub handoff unless a user-safe conversation mute API is exposed.
- Calls screen itself still needs full LogiNexus transformation and call history states.
- Group settings, member roles, invitations, member safety actions, and group media remain partial.
- Rooms detail/provider boundary remains partial.
- Offline/reconnect queue proof still needs simulator disruption QA.
- Physical-device checks remain for microphone, camera, push ringing, Bluetooth, LiveKit, and background audio.

## Current Estimate

- Overall Pulse Command transformation: 58%.
- Inbox: 78%.
- Conversation list: 82%.
- Conversation screen: 68%.
- Message bubbles: 66%.
- Reply/reactions/context menus: 62%.
- Composer: 68%.
- Attachments: 60%.
- Calls: 46%.
- Groups: 58%.
- Rooms: 50%.
- UNDX integration: 70%.
- Safety controls: 60%.
- Accessibility: 72%.
- Xcode Simulator QA: 62%.

## Decision

Do not move to Search / Discover yet. Pulse Command remains the current active vertical until Calls, Groups, Rooms, nested message interactions, offline/reconnect, accessibility, and simulator QA are substantially complete.
