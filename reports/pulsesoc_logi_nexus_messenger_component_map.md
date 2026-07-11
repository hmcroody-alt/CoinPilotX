# PulseSoc LogiNexus Messenger Component Map

## Existing Components Reused

- `MessengerScreen`: reused as the authoritative inbox screen.
- `ChatScreen`: reused as the authoritative conversation screen.
- `GroupsScreen`: reused for groups and rooms instead of creating a separate rooms surface.
- `PulseAiScreen`: reused for UNDX conversation through the existing assistant API.
- `NativeMediaViewer`: reused for image/video message attachment handoff.
- `IncomingCallLayer`, `CallScreen`, and call APIs: preserved as the native call foundation.
- `LogiNexusScreenShell`, `LogiNexusStatePanel`, and shared navigation primitives: reused from the existing LogiNexus foundation.

## New Shared Primitive

- `components/PulseCommand.tsx`

Reason: inbox, chat, groups, rooms, and UNDX need the same command-surface language. A shared primitive avoids duplicate headers, avatar styling, segment rails, search shells, and action buttons.

## Classification

- Inbox screen: extend.
- Conversation list: extend.
- Conversation screen: extend.
- Message bubble system: extend, not replace.
- Message composer: extend with improved semantic path and safe spacing.
- Attachments: reuse existing upload and NativeMediaViewer contracts.
- Voice messages: preserve existing `expo-av` recording path; platform migration away from `expo-av` remains a separate media dependency task.
- Calls: preserve existing call route/session logic.
- Groups/Rooms: extend existing `GroupsScreen`.
- UNDX: relabel public copy while preserving existing assistant endpoint.

## Duplicate Patterns Reduced

- Shared Pulse Command header/action/search/metric/avatar treatment replaces new one-off styling across Messenger, Chat, Groups, and UNDX.
- Shared `LogiNexusStatePanel` replaces local loading/empty blocks in the transformed surfaces.
# Pulse Command Completion Update

Added / extended in this milestone:

- `mobile-native/src/api/messenger.ts`
  - local-only populated QA fixtures
  - message reaction API helper
  - message delete API helper
  - message report API helper
  - conversation pin API helper
  - richer conversation/message state normalization
- `mobile-native/src/screens/MessengerScreen.tsx`
  - in-place Chats / Calls / Groups / Rooms tabs
  - native row components for conversations, calls, groups, and rooms
  - server-backed group chat and room join routing
- `mobile-native/src/screens/ChatScreen.tsx`
  - reply state
  - reaction row
  - message action sheet
  - deleted/moderated states
  - report/delete/retry/safety actions
