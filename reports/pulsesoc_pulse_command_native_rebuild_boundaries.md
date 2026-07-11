# PulseSoc Pulse Command Native Rebuild Boundaries

Status: boundaries defined for the Messenger / Chat / Calls / Groups / Rooms / UNDX vertical.

## Native Rebuild Rule

Pulse Command must reuse the production PulseSoc system while rebuilding presentation natively.

The native app may not:

- Wrap the production Messenger WebView.
- Create duplicate native screens such as `MessengerV2`, `ChatScreen2`, or `CallsV2`.
- Reimplement server-authoritative messaging, safety, group, room, call, media, notification, or UNDX business logic.
- Copy DOM-specific behavior into React Native.

## Backend-Owned Logic

The backend remains authoritative for:

- authentication/session state
- conversation access
- group/room membership
- send/edit/delete/report/reaction mutations
- typing and seen/read state
- unread counts
- attachment processing and moderation
- block/mute/report permissions
- call signaling, token generation, and call-control state
- notification routing and event cursor visibility
- UNDX response generation and safety boundaries

Native must call the existing API wrappers and handle success/failure/retry safely.

## Native-Owned Presentation

Native owns:

- screen shells
- list virtualization
- message bubbles
- keyboard-safe composer behavior
- attachment picker presentation
- reply/reaction/context menus
- safe-area layout
- native gestures
- state panels
- accessibility labels and roles
- simulator/device responsive behavior
- provider fallback presentation

## Extraction Candidates

The following are portable enough to extract into shared TypeScript utilities:

- `messageTypeLabel`
- `conversationPreview`
- `presenceLabel`
- `messageDeliveryLabel`
- `attachmentKind`
- `formatDuration`
- `relativeTime`
- `shortTime`
- message action availability
- conversation action availability
- safety action eligibility
- group/room permission labels
- offline/reconnect copy

## Current Native Boundaries

- `mobile-native/src/api/messenger.ts` owns Messenger API typing and normalization.
- `mobile-native/src/api/groups.ts` owns groups and rooms API typing and normalization.
- `mobile-native/src/api/calls.ts` owns calls API typing and normalization.
- `mobile-native/src/screens/MessengerScreen.tsx` owns the native inbox presentation.
- `mobile-native/src/screens/ChatScreen.tsx` owns the native conversation presentation.
- `mobile-native/src/screens/CallScreen.tsx` owns the native call presentation.
- `mobile-native/src/screens/GroupsScreen.tsx` owns the native groups/rooms presentation.
- `mobile-native/src/screens/PulseAiScreen.tsx` owns the native UNDX presentation.
- `mobile-native/src/components/PulseCommand.tsx` owns shared Pulse Command UI primitives.

## Next Safe Refactor

Create a shared Pulse Command domain utility module that extracts:

- timestamp formatting
- message preview normalization
- message type labels
- attachment labels
- presence labels
- message delivery labels
- action availability registry

This should reduce duplicated mapping logic without changing backend behavior.
