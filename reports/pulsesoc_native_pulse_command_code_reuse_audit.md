# PulseSoc Native Pulse Command Code Reuse Audit

Status: scoped first exact-parity pass.

## What was reused

- `MessengerScreen`
- `ChatScreen`
- `GroupsScreen`
- `CallScreen`
- `PulseAiScreen`
- `PulseCommand` shared primitives
- `pulseCommand/domain.ts`
- `mobile-native/src/api/messenger.ts`
- `mobile-native/src/api/groups.ts`
- `mobile-native/src/api/calls.ts`
- Existing server-authoritative send, upload, typing, seen/read, reaction, delete, report, group, room, call, and UNDX contracts

## What was refined

- Shared Pulse Command avatar/search/tab/panel sizing.
- Messenger top-level copy and active-user rail label.
- Native conversation row density.
- Native message bubble radius, text, metadata, and color treatment.
- Native chat composer geometry.

## What was rebuilt natively

- React Native row, bubble, composer, and press-state presentation.
- No DOM/CSS interaction code was moved into React Native.

## No duplicate implementation

No `MessengerV2`, `ChatScreen2`, `PulseCommandNew`, `ConversationList2`, `CallsV2`, `GroupsV2`, `RoomsV2`, or `UNDXChatNew` was introduced.

## Current reuse confidence

- Backend/business logic reuse: 95%.
- Frontend utility reuse/extraction: 72%.
- Existing native component reuse: 96%.
