# PulseSoc Pulse Command LogiNexus Completion

Status: active vertical completion, not complete.

## Completed This Milestone

- Added the formal Pulse Command WebView/backend/native code reuse map.
- Added native rebuild boundaries so future work reuses contracts and business rules but does not copy DOM/UI debt.
- Added an audit that verifies:
  - production WebView Messenger source is inventoried
  - backend/service source is inventoried
  - native API wrappers remain the contract boundary
  - no duplicate native Messenger/Chat/Calls/Groups/Rooms surfaces are introduced
- Extracted shared Pulse Command domain utilities for:
  - conversation titles, previews, timestamps, badges, active presence, and accessibility labels
  - message previews, delivery/read state labels, accessibility labels, typing summaries, reaction icons, optimistic reaction state, and context-menu action availability
  - group titles, type labels, role labels, summaries, badges, accessibility labels, and membership/chat/report action availability
  - room titles, summaries, badges, accessibility labels, and join/open/provider-boundary action availability
- Refactored `MessengerScreen` and `ChatScreen` to consume the shared domain rules instead of interpreting the same server payloads locally.
- Refactored `GroupsScreen` to consume the shared domain rules for Groups and Rooms instead of locally formatting role, badge, room state, and action availability copy.
- Completed a scoped Pulse Command Calls foundation pass:
  - transformed `CallScreen` onto shared Pulse Command and LogiNexus layout primitives
  - preserved existing call start, accept, decline, hangup, call-control, token, event, and fallback APIs
  - added clearer native readiness, participant, mode, media-runtime, and empty active-call states
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

- Production Messenger V3 source as workflow/data-contract reference:
  - `templates/pulse_messages_v2.html`
  - `static/js/pulse_messages_v2.js`
  - `static/js/pulse_chat_recovery.js`
  - `static/js/pulse_messenger_media_viewer.js`
  - `static/pulsesoc_calls.js`
- Backend/service authority:
  - `bot.py`
  - `services/chat_realtime_service.py`
  - `services/messenger_media_foundation.py`
  - `services/chat_health_service.py`
- Existing `/api/pulse/messages/*` conversation, sync, send, seen, typing, search, media upload, react, delete, report, and pin routes.
- Existing `getActiveCalls`, call screen, and call provider boundary.
- Existing `useNativeCallRoom`, call join-token, call event, and call-control routes.
- Existing `listGroups`, `openGroupChat`, `listRooms`, and `joinRoom` APIs.
- Existing `NativeMediaViewer`.
- Existing `SafetyHub` for mute/block/report boundary workflows.
- Existing `PulseCommand` shared primitives.
- New shared `mobile-native/src/pulseCommand/domain.ts` as the native presentation-domain boundary for portable Messenger rules extracted from production workflow behavior.

## Still Incomplete

- Shared TypeScript domain utilities now cover inbox, chat, groups, and rooms presentation rules. They still need expansion for call history labels, attachment download/open actions, conversation-level mute/block/pin availability, offline/reconnect copy, and UNDX-specific suggestion/action rules.
- Message forward/share and details surfaces are not complete.
- Conversation mute/unmute is still a Safety Hub handoff unless a user-safe conversation mute API is exposed.
- Calls screen now uses the shared Pulse Command shell, but call history states and two-device provider proof remain incomplete.
- Group settings, member roles, invitations, member safety actions, and group media remain partial.
- Rooms detail/provider boundary remains partial.
- Offline/reconnect queue proof still needs simulator disruption QA.
- Physical-device checks remain for microphone, camera, push ringing, Bluetooth, LiveKit, and background audio.

## Current Estimate

- Overall Pulse Command transformation: 71%.
- Inbox: 81%.
- Conversation list: 86%.
- Conversation screen: 71%.
- Message bubbles: 70%.
- Reply/reactions/context menus: 68%.
- Composer: 68%.
- Attachments: 60%.
- Calls: 68%.
- Groups: 66%.
- Rooms: 59%.
- UNDX integration: 70%.
- Safety controls: 64%.
- Accessibility: 75%.
- Xcode Simulator QA: 70%.

## Decision

Do not move to Search / Discover yet. Pulse Command remains the current active vertical until Calls, Groups, Rooms, nested message interactions, offline/reconnect, accessibility, and simulator QA are substantially complete.

## Group / Room Detail Foundation Update

- Group Detail now has native Overview, Members, Invitations, Media, Files, Links, and Settings sections inside the existing `GroupsScreen`.
- Room Detail now has native Overview, Participants, Activity, and Provider sections.
- Group and room detail surfaces reuse the shared Pulse Command domain layer for role labels, action availability, invitation state, asset categories, provider-state labels, and accessibility copy.
- Existing group and room mutations were preserved; no parallel group/room logic was created.
- Missing backend/provider contracts are now explicit LogiNexus boundary panels instead of dead buttons or fake local success.

Updated estimate:

- Overall Pulse Command transformation: 74%.
- Groups: 74%.
- Rooms: 68%.
- Shared domain adoption: 76%.
- Shared layout adoption: 78%.
- Accessibility: 77%.
- Xcode Simulator QA: pending final screenshot pass for this slice.
