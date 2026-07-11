# PulseSoc Pulse Command Code Reuse Map

Status: source inventory and reuse boundary established.

## Reuse Strategy

Pulse Command is a native evolution of the production Messenger system, not a rewrite and not a WebView wrapper.

The implementation strategy is:

- Reuse backend contracts and server-authoritative business rules.
- Extract portable state/format/action rules only when they are not coupled to DOM behavior.
- Rebuild device-facing presentation, gestures, keyboard behavior, media rendering, and accessibility in React Native.
- Preserve one authoritative native screen per surface.

## Authoritative Sources

| Capability | WebView source | Native source | Backend/service source | Contract | Reuse decision | Risk | QA status |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Inbox | `templates/pulse_messages_v2.html`, `static/js/pulse_messages_v2.js` | `mobile-native/src/screens/MessengerScreen.tsx`, `mobile-native/src/api/messenger.ts` | `bot.py`, `services/chat_realtime_service.py` | `/api/pulse/messages/conversations` | Reuse API/sorting intent; rebuild native list and tabs | Medium: WebView filter taxonomy is richer than native tabs | Simulator populated inbox verified |
| Search | `static/js/pulse_messages_v2.js` | `mobile-native/src/api/messenger.ts`, `MessengerScreen.tsx` | `bot.py` | `/api/pulse/messages/search` | Reuse endpoint and debounce/cancel intent; rebuild native search UI | Medium: message-text search depth needs QA | Basic native search present, deeper QA pending |
| Conversation row | `renderConversations`, `conversationPreview`, `presenceForConversation` | `ConversationRow` in `MessengerScreen.tsx` | `pulse_conversation_summaries` in `bot.py` | conversation summary payload | Extract preview/presence mapping later; rebuild row natively | Low | Native row states visible with fixtures |
| Message list | `loadMessages`, `renderMessages`, `messageHtml` | `ChatScreen.tsx`, `getConversation`, `syncConversation` | `api_pulse_conversation_messages`, `api_pulse_messages_sync` | `/api/pulse/messages/:id/messages`, `/sync` | Reuse pagination/order contracts; rebuild virtualized list natively | Medium: older-message anchoring needs more QA | Fixture long-thread states partial |
| Send | `sendMessage` | `sendConversationMessage`, `createLocalMessage` | `pulse_send_conversation_message`, `api_pulse_conversation_send` | `/api/pulse/messages/:id/send` | Reuse server mutation and idempotency fields; native optimistic state only | Medium | Text send path present, retry QA partial |
| Typing | `sendTypingIndicator`, `sendTypingStopped` | `sendTyping` | `api_pulse_messages_typing` | `/typing` | Reuse endpoint and timer intent; native composer emits state | Medium | Needs simulator timing proof |
| Seen/read | WebView load/read updates | `markConversationSeen` | `pulse_mark_conversation_read`, `api_pulse_messages_seen` | `/seen` | Reuse server read state | Medium | Present, unread divider still pending |
| Reactions | `reactToMessage` | `reactToMessage`, `ReactionRow` | `api_pulse_message_react` | `/api/pulse/messages/:message_id/react` | Reuse server mutation; rebuild picker/buttons natively | Low | Native reaction row present |
| Reply | `startReply` | `replyTo` state in `ChatScreen.tsx` | send payload reply fields | `reply_to_message_id`, `reply_preview` | Reuse payload contract; rebuild reply preview natively | Low | Present |
| Forward | `forwardMessage` | Not native-complete | Web contract/action intent | pending native-safe route/action | Extract action availability later | Medium | Deferred |
| Delete | `deleteMessage` | `deleteMessage` helper and action sheet | `api_pulse_message_delete` | `/delete` | Reuse server mutation and permissions | Low | Present |
| Report | `reportLast`, media viewer report | `reportMessage`, Safety Hub handoff | `api_pulse_message_report`, moderation services | `/report` | Reuse server moderation route | Low | Present |
| Block | `blockPeer`, profile block paths | Safety Hub handoff | block/safety routes in `bot.py` | server safety state | Reuse backend safety authority; native safe handoff until direct route is mapped | Medium | Partial |
| Mute | conversation control center settings | Safety Hub/settings handoff | conversation settings routes in `bot.py` | control-center settings | Extract action registry later | Medium | Partial |
| Attachments | attachment sheet, media foundation JS | `uploadMessengerMedia`, `NativeMediaViewer` | `services/messenger_media_foundation.py`, media routes in `bot.py` | `/api/pulse/messages/media/upload`, `/api/messages/media/*` | Reuse upload contracts; rebuild picker/preview natively | Medium: real camera/mic are device-only | Preview/upload partial |
| Voice | Web recorder + waveform | Native placeholder/composer controls | media foundation and message payload fields | audio media + duration fields | Reuse upload/message contracts; rebuild native audio with future media migration | High: `expo-av` deprecation | Placeholder only |
| Calls | Web call controls + LiveKit | `CallScreen.tsx`, `useNativeCallRoom`, `api/calls.ts` | call routes in `bot.py`, `static/pulsesoc_calls.js` | `/api/calls/*`, `/api/pulse/comm/v2/conversations/:id/voice/start` | Reuse call/session/token/control contracts; rebuild native shell | High: provider/hardware QA | Shared native shell verified |
| Groups | new group, group routes | `GroupsScreen.tsx`, `api/groups.ts` | group routes in `bot.py` | `/api/pulse/groups/*`, `/api/pulse/messages/group/*` | Reuse membership/role APIs; rebuild UI natively | Medium | Partial |
| Rooms | room list/open/join | `GroupsScreen.tsx`, `api/groups.ts` | room routes in `bot.py` | `/api/pulse/communications/rooms`, `/api/pulse/messages/rooms/:id/join` | Reuse room permissions and conversation handoff | Medium | Partial |
| UNDX | Pulse AI panel and endpoints | `PulseAiScreen.tsx` | Pulse AI routes/services | `/api/pulse-ai/*` | Reuse endpoint and response contracts; rebuild native identity/conversation | Medium | Partial |
| Offline/reconnect | `pulse_chat_recovery.js`, realtime polling | AsyncStorage caches, sync APIs | `chat_health_service.py`, sync endpoints | cached list/thread + `/sync` | Extract recovery semantics where safe; native cache exists | Medium | Needs disruption QA |
| Deep links | Web locations | native route registry and notification routing | notification metadata | `/pulse/messages/:id`, notification links | Reuse route intent; native route dispatch | Medium | Partial |
| Push routing | Web notification/deep links | `notificationRouting.ts`, native badges | notification services | notification metadata | Reuse metadata; native tap behavior device-only | High | Physical-device-only |

## Reuse Categories

### A. Reuse unchanged

- Authentication/session behavior.
- Conversation IDs, message IDs, thread relationships, and user identity mapping.
- Messenger, media, calls, groups, rooms, notification, and UNDX backend endpoints.
- Server-side send, edit/delete/report/reaction, typing, seen/read, media, block/mute/report, group/room, and call permission logic.
- Event cursor and notification invalidation contracts.

### B. Extract and share

Completed this pass:

- Conversation display title, preview text, timestamp, signal badges, active presence, and accessibility label.
- Message preview text, delivery/read label, accessibility label, typing summary, optimistic reaction state, reaction icon mapping, and message action availability.
- Group display title, type label, role label, summary, signal badges, accessibility label, and action availability.
- Room display title, summary, signal badges, accessibility label, and provider-aware open/join action availability.

Still remaining:

- Message type labels beyond preview copy.
- Attachment type mapping for open/download/provider boundaries.
- Conversation action availability.
- Safety-action eligibility.
- Call history and provider state labels.
- Offline/reconnect status mapping.

These currently exist partly in `static/js/pulse_messages_v2.js` and partly in native screens. The new `mobile-native/src/pulseCommand/domain.ts` is now the shared native extraction point and should continue absorbing portable rules during the next Pulse Command implementation slices.

### C. Refactor and extend

- Native `mobile-native/src/api/messenger.ts` should remain the single typed Messenger API boundary.
- Native `mobile-native/src/api/groups.ts` should remain the single typed groups/rooms API boundary.
- Native `mobile-native/src/api/calls.ts` should remain the single typed calls/provider boundary.
- `PulseCommand` primitives should continue replacing one-off UI chrome across nested communications screens.

### D. Native UI rebuild

- Inbox rows and tabs.
- Chat list and bubbles.
- Composer, keyboard, attachment preview, context sheet, reaction UI, and reply UI.
- Calls shell, call controls, state panels, and provider boundary presentation.
- Groups/rooms list and detail UI.
- UNDX identity and prompt UI.
- Accessibility semantics, native gestures, haptics, and safe-area behavior.

### E. Do not carry over obsolete web-only code

- DOM selectors and `document/window` access.
- CSS-only interaction logic.
- Browser file input behavior.
- Desktop-only viewport assumptions.
- Web hover behavior.
- Nested web scroll containers.
- Web visual settings that have no native equivalent.
- DOM-based media viewer internals.

### F. Provider/device-only boundaries

- Microphone capture.
- Real camera capture.
- Bluetooth and speaker routing.
- Lock-screen calls.
- Push ringing and notification taps.
- Background call audio.
- Large video upload in background.

## Current Decision

Pulse Command is not LogiNexus-complete yet. The next engineering step should extend the shared domain module into group/room/call labels and conversation-level actions before adding deeper Groups/Rooms and offline/reconnect QA.

## Group / Room Detail Reuse Update

Completed after the previous decision:

- `mobile-native/src/api/groups.ts` now accepts optional authoritative detail payloads for members, invitations, membership requests, media, files, links, room participants, room activity, provider state, privacy, host, and current-user role.
- `mobile-native/src/pulseCommand/domain.ts` now owns group member role priority, member action availability, invitation labels, asset category labels, room provider state labels, and participant accessibility labels.
- `mobile-native/src/screens/GroupsScreen.tsx` now reuses those rules for the native Group Detail and Room Detail foundations.

Reuse unchanged:

- Group IDs, room IDs, group slugs, membership status, current viewer role, join/leave, open chat, report group, room list, and room join/open contracts.

Extract and share:

- Role priority and role labels.
- Member action availability.
- Invitation state copy.
- Room provider-state copy.
- Participant role/accessibility copy.
- Media/file/link category copy.

Native UI rebuild:

- Group Detail section rail.
- Group Overview, Members, Invitations, Media, Files, Links, and Settings sections.
- Room Detail section rail.
- Room Overview, Participants, Activity, and Provider sections.

Provider/device-only boundaries:

- Live room microphone, camera, speaker, Bluetooth, background audio, and real multi-participant media.
- Full room participant roster until the provider/backend exposes safe participant identity to native.

Remaining:

- Server-backed member roster and invitation mutation endpoints for native.
- Conversation-level mute/block/pin availability.
- Attachment open/download provider boundaries.
- Offline/reconnect proof.
