# PulseSoc Native Communications Parity Migration

Date: 2026-07-16

## Mission Gate

The mission explicitly requires the production communication implementation matrix before deeper native implementation. This report is the first data-safety gate. It does not claim native messaging or calls are replacement-ready because bidirectional WebView/native production tests, physical-device media/call tests, and cross-client voice-message compatibility are not yet proven.

## WebView / Production Sources Inspected

- `pulse_communications_v2/routes.py`
  - `GET /api/pulse/comm/v2/conversations`
  - `POST /api/pulse/comm/v2/conversations`
  - `POST /api/pulse/comm/v2/direct/open`
  - `POST /api/pulse/comm/v2/groups`
  - `POST /api/pulse/comm/v2/rooms`
  - `GET /api/pulse/comm/v2/conversations/<conversation_ref>/messages`
  - `POST /api/pulse/comm/v2/conversations/<conversation_ref>/messages`
  - `GET /api/pulse/comm/v2/realtime`
  - `GET /api/pulse/comm/v2/realtime/stream`
  - `POST /api/pulse/comm/v2/attachments/upload`
  - `GET/POST/PATCH /api/pulse/comm/v2/conversations/<conversation_ref>/control-center...`
  - `GET /api/pulse/comm/v2/search`
  - `GET /api/pulse/comm/v2/people/search`
  - `POST /api/pulse/comm/v2/conversations/<conversation_ref>/read`
  - `POST /api/pulse/comm/v2/conversations/<conversation_ref>/typing`
  - `GET /api/pulse/comm/v2/conversations/<conversation_ref>/presence`
  - `POST/PATCH/DELETE /api/pulse/comm/v2/messages/<message_id>...`
  - `POST /api/pulse/comm/v2/conversations/<conversation_ref>/voice/start`
  - `POST /api/pulse/comm/v2/conversations/<conversation_ref>/video/start`
  - `/api/calls/*` call lifecycle, LiveKit token, call events, participant controls, LiveKit webhook
- `bot.py`
  - `/pulse/messages`, `/pulse/messages/<conversation_id>`, `/pulse/groups`
  - `/api/pulse/messages/conversations`
  - `/api/pulse/messages/<conversation_id>/messages`
  - `/api/pulse/messages/<conversation_id>/send`
  - `/api/pulse/messages/<conversation_id>/sync`
  - `/api/pulse/messages/<conversation_id>/seen`
  - `/api/pulse/messages/<conversation_id>/typing`
  - `/api/pulse/messages/<conversation_id>/pin`
  - `/api/pulse/messages/<message_id>/react`
  - `/api/pulse/messages/<message_id>/delete`
  - `/api/pulse/messages/<message_id>/report`
  - `/api/pulse/messages/search`
  - `/api/pulse/messages/direct/open`
  - `/api/pulse/messages/groups/create`
  - `/api/pulse/messages/room/open`
  - `/api/pulse/messages/media/upload`
  - `/api/messages/media/*`
  - `/api/pulse/messages/<conversation_id>/presence`
  - `/api/pulse/messages/rooms*`, `/api/pulse/chatrooms*`, `/api/chat-room*`
- `templates/pulse_messages_v2.html`
- `static/js/pulse_messages_v2.js`
- `static/js/pulsesoc_calls.js`
- `static/js/pulsesoc_global_call_overlay.js`
- `static/js/pulse_chat_recovery.js`
- `static/js/pulse_realtime.js`
- `static/js/pulse_messenger_media_viewer.js`
- `static/css/pulse_messages_v2.css`
- `static/css/pulsesoc_global_call_overlay.css`
- `services/pulsesoc_communications_engine.py`
- `services/realtime_engine.py`
- `services/chat_realtime_service.py`
- `services/realtime_service.py`
- `services/messenger_media_foundation.py`
- `services/live_presence_engine.py`
- `services/world_presence_engine.py`
- `services/notification_service.py`
- `services/notification_orchestrator.py`

## Native Sources Inspected

- `mobile-native/src/api/messenger.ts`
- `mobile-native/src/api/calls.ts`
- `mobile-native/src/calls/IncomingCallLayer.tsx`
- `mobile-native/src/calls/useNativeCallRoom.ts`
- `mobile-native/src/navigation/notificationRouting.ts`
- `mobile-native/src/navigation/linking.ts`
- `mobile-native/src/screens/MessengerScreen.tsx`
- `mobile-native/src/screens/ChatScreen.tsx`
- `mobile-native/src/screens/CallScreen.tsx`
- `mobile-native/src/screens/NewChatScreen.tsx`
- `mobile-native/src/screens/GroupsScreen.tsx`
- `mobile-native/src/components/ConversationControlCenter.tsx`
- `mobile-native/src/api/groups.ts`
- `mobile-native/src/api/notifications.ts`

## Authoritative Sources of Truth

| Domain | Production authority | Native rule |
| --- | --- | --- |
| User identity | `api_account_user()`, account session, `user_id`, profile/public IDs | Native must use restored authenticated session and server-returned IDs. No native-only user IDs. |
| Conversations | `pulse_conversations`, `pulse_conversation_participants`, `/api/pulse/messages/*`, `/api/pulse/comm/v2/*` | Native must use existing `conversation_id` / `conversation_ref`, never create parallel threads. |
| Messages | `pulse_messages`, `pulse_message_receipts`, reactions/deletes/reports tables | Native optimistic messages must reconcile to server `message_id`. Negative/local IDs cannot persist. |
| Attachments and voice | Existing message media upload endpoints and `chat_media_uploads`/message media metadata | Native upload must use production upload route and supported MIME/container metadata. |
| Calls | `services.pulsesoc_communications_engine`, `/api/calls/*`, LiveKit room/token contracts | Native may render/device-connect, but server owns `call_id`, status, participants, token, history. |
| Presence and typing | `/presence`, `/typing`, heartbeat routes, realtime services | Native must emit/fetch existing server presence, not a second presence model. |
| Realtime | `pulse_emit_event`, `realtime_engine`, comm v2 polling/SSE fallback | Native must process canonical events idempotently and reconcile by fetch on gaps. |
| Notifications/deep links | Pulse notification APIs and `notificationRouting.ts` route targets | Native must keep production target IDs and route aliases. |
| Privacy/safety | message report/delete/block/group permission checks in backend | Native must only expose actions allowed by server result/domain policy. |

## Implementation Matrix

| Capability | WebView/source | Backend source | Current native source | Reusable directly | Must be ported | Missing / risk | Data risk | Required QA |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Conversation list | `pulse_messages_v2.js`, `/pulse/messages` | `bot.py` `/api/pulse/messages/conversations`, v2 `/conversations` | `MessengerScreen.tsx`, `api/messenger.ts:listConversations` | IDs, sorting payload, unread fields | Native row presentation and virtualization | Native currently uses legacy endpoint, v2 parity still needs explicit decision | Duplicate row risk if v2 and legacy are mixed without normalization | Existing WebView conversation opens in native; no duplicate conversations |
| Direct open/new chat | `pulse_messages_v2.js` | `bot.py` `/api/pulse/messages/direct/open`, v2 `/direct/open` | `NewChatScreen.tsx`, `openDirectConversation` | Target `user_id`, server-created conversation | Native search/selection UI | User search uses `/api/pulse/users/search`; verify parity with production people search | Duplicate direct thread if idempotency breaks | Start same recipient from both clients, verify one canonical conversation |
| Group creation/list | WebView groups/messages routes | `bot.py` group routes, v2 `/groups` | `GroupsScreen.tsx`, `api/groups.ts` | Group IDs/slugs/membership | Native group detail depth | Some group admin/media/roles still partial | Role/membership corruption if client-side authority is assumed | Admin/member role matrix |
| Room list/join | WebView room/chatroom routes | `bot.py` `/api/pulse/messages/rooms*`, v2 `/rooms` | `GroupsScreen.tsx`, room tab/domain work | Room IDs and join routes | Native room detail/provider boundary | Live/provider state requires release-device QA | Phantom active room if provider state is faked | Join/open/leave with server response |
| Message history | `pulse_messages_v2.js` | `bot.py` `/api/pulse/messages/<id>/messages`, v2 messages | `ChatScreen.tsx`, `getConversation` | `conversation_id`, `message_id`, server ordering | Native virtualized bubble UI | Need cross-client historical comparison | Lost/duplicated messages if before/after cursors diverge | Compare WebView/native message count and IDs |
| Send text | WebView send flow | `bot.py` `/api/pulse/messages/<id>/send`, v2 send | `sendConversationMessage`, `ChatScreen.tsx` | Server validation, `client_message_id`, canonical `message_id` | Native composer/optimistic render | Duplicate prevention requires bidirectional test | Duplicate messages on repeated tap/offline retry | Native send appears in WebView once |
| Realtime sync | `pulse_realtime.js`, `pulse_chat_recovery.js` | `/sync`, comm v2 `/realtime`, `/realtime/stream` | `syncConversation`, polling in `ChatScreen.tsx` | Server events and fetch reconciliation | Native event processor depth | SSE not proven in simulator; polling fallback exists | Stale UI/duplicate UI if events not idempotent | Receive WebView send in native without reload |
| Read/seen | WebView read handlers | `/seen`, v2 `/read`, receipts | `markConversationSeen` | Server read state | Native receipt UI | Delivery/read visual parity incomplete | Unread counts reset incorrectly | Read in native updates WebView unread state |
| Typing/presence | WebView typing/presence | `/typing`, `/presence`, heartbeat | `sendTyping`, `typingSummary`, `presenceSummary` | Server typing/presence | Native indicators | Background/foreground presence not fully proven | False online status | Cross-client typing visibility |
| Reactions | WebView reaction handlers | `/messages/<message_id>/react`, v2 `/reactions` | `reactToMessage` | Message IDs and server reaction counts | Native picker depth | Reaction remove/change parity needs proof | Wrong reaction counts | React in both clients, compare counts |
| Delete/report | WebView menu/safety | `/delete`, `/report`, backend permission checks | `deleteMessage`, `reportMessage` | Server authority | Native confirmation/menu UI | Delete-for-everyone mapping needs exact payload review | Unauthorized delete/report state | Role/user permission QA |
| Attachments | Web media viewer/upload | `/api/pulse/messages/media/upload`, `/api/messages/media/*`, v2 `/attachments/upload` | `uploadMessengerMedia`, `NativeMediaViewer` handoff | Production media URLs, metadata, upload store | Native picker/device UI | Voice/video/document compatibility not fully proven | Incompatible media or orphan uploads | Native media visible/playable in WebView and reverse |
| Voice messages | Web voice-note upload/playback | Existing media upload with `voice`, `duration_seconds`, v2 `attachment_kind`, waveform metadata | `ChatScreen.tsx` recording/upload path | Upload route and duration metadata | Native recording/playback controls | Container/codec/MIME matrix not fully proven | WebView cannot play native voice if unsupported codec | Record/send/play both directions |
| Audio calls | Web call JS/global overlay | v2 `/voice/start`, `/api/calls/*`, `pulsesoc_communications_engine` | `api/calls.ts`, `CallScreen.tsx`, `IncomingCallLayer.tsx`, `useNativeCallRoom` | `call_id`, LiveKit token, participants/status | Native device audio controls | Physical audio route/Bluetooth/lock screen not proven | Phantom/duplicate call sessions | Native-WebView call lifecycle and no Home popup |
| Video calls | Web call JS/LiveKit | v2 `/video/start`, `/api/calls/*`, LiveKit webhook | `CallScreen.tsx`, `useNativeCallRoom` | Same signaling and token contract | Native camera preview/toggle | Camera/mic physical QA required | Duplicate media streams/session leak | Physical iPhone camera/mic call QA |
| Active call overlay | `pulsesoc_global_call_overlay.js` | `/api/calls/active` | `IncomingCallLayer.tsx` | Active-call fetch/state | Route-aware native presentation | Product says no Home floating popup | Home touch interception if overlay leaks | Active call on Home: no popup; Call screen still manages |
| Push/deep links | Web notification targets | notification APIs, route targets | `notificationRouting.ts`, `linking.ts` | Existing target IDs and route paths | Native presentation/routing | Real push/tap physical QA pending | Wrong conversation/call opens | Push to message/call route on device |

## Backend Routes Reused

Native already calls canonical production endpoints for the current implementation:

- `/api/pulse/messages/conversations`
- `/api/pulse/messages/<conversation_id>/messages`
- `/api/pulse/messages/<conversation_id>/sync`
- `/api/pulse/messages/<conversation_id>/send`
- `/api/pulse/messages/<conversation_id>/seen`
- `/api/pulse/messages/<conversation_id>/typing`
- `/api/pulse/messages/<conversation_id>/pin`
- `/api/pulse/messages/search`
- `/api/pulse/messages/direct/open`
- `/api/pulse/messages/media/upload`
- `/api/pulse/messages/<message_id>/react`
- `/api/pulse/messages/<message_id>/delete`
- `/api/pulse/messages/<message_id>/report`
- `/api/pulse/comm/v2/conversations/<conversation_ref>/voice/start`
- `/api/pulse/comm/v2/conversations/<conversation_ref>/video/start`
- `/api/calls/start`
- `/api/calls/<call_id>/accept`
- `/api/calls/<call_id>/decline`
- `/api/calls/<call_id>/end`
- `/api/calls/<call_id>/join-token`
- `/api/calls/<call_id>/status`
- `/api/calls/active`
- `/api/calls/<call_id>/connected`
- `/api/calls/<call_id>/quality`
- `/api/calls/<call_id>/events`
- `/api/calls/<call_id>/mute-audio`
- `/api/calls/<call_id>/unmute-audio`
- `/api/calls/<call_id>/enable-video`
- `/api/calls/<call_id>/disable-video`
- `/api/calls/<call_id>/switch-camera`
- `/api/calls/<call_id>/speaker`

## Reuse and Extraction Findings

Directly reused:

- Authenticated `pulseApi` session behavior.
- Existing conversation/message/call IDs returned by production endpoints.
- Server-side conversation creation and direct-open behavior.
- Server-side send/read/typing/reaction/delete/report/pin mutations.
- Server-side active call, call token, call status, and control endpoints.
- LiveKit token/room contract from production call service.
- Existing native cache helpers for cached calls/messages.

Extract or normalize next:

- One shared communication domain map for conversation/message/call status labels.
- One action registry for conversation/message/call safety actions.
- One idempotency/reconciliation helper for optimistic messages and offline queue.
- One media/voice compatibility registry for container/MIME/codec constraints.
- One realtime event reducer that consumes legacy and comm v2 events idempotently.

Port natively:

- Message list virtualization and long-history rendering.
- Conversation row presentation, context menus, reaction picker, reply/edit states.
- Voice recording/playback controls and single-playback coordination.
- Native LiveKit UI, camera/microphone/speaker/Bluetooth route controls.
- Push tap handling and call notification presentation.

## Data Model Changes

None in this gate. No database migration, destructive schema change, or ID rewrite was introduced.

## Data-Loss Safeguards

- Native continues using server-returned `user_id`, `conversation_id`, `message_id`, and `call_id`.
- Native caches are local presentation/cache only and do not replace canonical server records.
- Native optimistic messages use `client_message_id` and must reconcile to server-confirmed `message_id`.
- No native-only conversation, room, call, presence, push, or attachment backend was introduced.
- Existing WebView routes remain untouched by this gate.
- Home active-call floating popup remains suppressed through route policy while active call state and canonical Call screen remain intact.

## Verification Status

Simulator:

- Not rerun for this matrix-only gate. Existing committed Home call-overlay simulator evidence remains in `reports/screenshots/native-home-call-overlay-removal/`.

Code-path/audit:

- New audit validates production route contracts, native API reuse, call endpoints, LiveKit native connection path, and Home call-overlay suppression.

Cross-client:

- Not complete. Requires controlled production-compatible test accounts.

Physical-device-only:

- Real microphone.
- Real camera.
- Speaker/Bluetooth routing.
- Lock-screen push.
- Background call behavior.
- App-killed incoming call.
- Large real-world media upload.

## Remaining Blockers

- Prove WebView-native bidirectional text, media, voice, read state, unread count, and reaction compatibility with controlled accounts.
- Prove physical iPhone audio/video call lifecycle, including permissions, Bluetooth/speaker routing, backgrounding, and push taps.
- Complete voice-message codec/container/MIME compatibility matrix.
- Complete realtime event reducer hardening against duplicate/stale events.
- Complete long-history performance measurements.
- Complete accessibility audit for message states, upload states, recording states, and call states.

## Honest Parity Snapshot

- Messaging backend/ID reuse: 88%
- Messaging native presentation parity: 70%
- Cross-client message compatibility evidence: 35%
- Voice-message compatibility evidence: 25%
- Audio-call backend/signaling reuse: 85%
- Audio-call physical-device readiness: 35%
- Video-call backend/signaling reuse: 80%
- Video-call physical-device readiness: 25%
- Realtime compatibility: 55%
- Data-loss safeguard confidence: 80%
- Safe to replace WebView messaging: NO
- Safe to replace WebView calls: NO

## Next Highest-Value Native Work

Complete controlled cross-client messaging proof before adding new communication features:

1. Existing WebView conversation opens in native with matching historical message IDs.
2. Native text send appears in WebView once.
3. WebView text send appears in native once.
4. Native media upload appears in WebView.
5. WebView media appears in native.
6. Native read state updates WebView unread count.
7. Reaction/delete/report sync both ways.

This should be followed by voice-message codec proof and then physical iPhone audio/video call proof.
