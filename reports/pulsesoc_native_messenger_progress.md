# PulseSoc Native Messenger Progress

Date: 2026-07-04

## Scope

Native Messenger Phase 1 is implemented as a new React Native client surface inside `mobile-native/`. It does not change the production WebView app, web templates, static WebView bridge files, backend authorization logic, database schema, moderation rules, notification services, or Communications V2 backend behavior.

## Reuse-First Implementation

The native Messenger foundation reuses the existing PulseSoc backend contract instead of rebuilding Messenger business logic in the app.

Routes used by the native client:

- `GET /api/pulse/messages/conversations`
- `GET /api/pulse/messages/<conversation_id>/messages`
- `POST /api/pulse/messages/<conversation_id>/send`
- `GET /api/pulse/messages/<conversation_id>/sync`
- `POST /api/pulse/messages/<conversation_id>/typing`
- `POST /api/pulse/messages/<conversation_id>/seen`
- `GET /api/pulse/messages/search`
- `POST /api/pulse/messages/media/upload`

The backend remains authoritative for:

- authentication and session cookies
- conversation membership and permissions
- message validation and persistence
- delivery/read receipt state
- typing and presence payloads
- media upload validation and storage
- moderation and reporting rules
- notification fanout
- Communications V2 compatibility

## Native Client Milestones

Completed in this milestone:

- Conversation list with native `FlatList`
- Pull to refresh
- Messenger search using the existing search endpoint
- Conversation screen with native chat layout
- Message bubbles with sent/delivered/read/failed labels
- Optimistic local send with server reconciliation
- Retry for failed sends
- Offline cache for conversations and recent messages using `AsyncStorage`
- Incremental sync polling against the existing sync endpoint
- Read receipt marking through the existing seen endpoint
- Typing indicator send/clear behavior through the existing typing endpoint
- Presence/typing display from the backend presence payload
- Image message rendering with native `Image`
- Voice/audio/file attachment rendering
- Native image picker upload through existing Messenger media upload route
- Native document picker upload through existing Messenger media upload route
- Native microphone recording upload path through existing Messenger media upload route
- Push deep link route support for `pulsesoc://pulse/messages/:conversationId` and `https://pulsesoc.com/pulse/messages/:conversationId`

## Communications V2 Compatibility

The native client intentionally targets the stable legacy/native bridge routes first because those routes already wrap current PulseSoc permissions, persistence, receipts, upload behavior, and event emission. The documented Communications V2 route family remains the forward-compatible backend layer:

- `/api/pulse/comm/v2/conversations`
- `/api/pulse/comm/v2/conversations/<conversation_ref>/messages`
- `/api/pulse/comm/v2/realtime`
- `/api/pulse/comm/v2/realtime/stream`
- `/api/pulse/comm/v2/search`
- `/api/pulse/comm/v2/conversations/<conversation_ref>/read`

No native-only fork of Messenger authorization, database writes, moderation, delivery rules, or notification logic was introduced.

## Not Yet Device-Verified

The following are implemented in code but still require real-device or simulator QA:

- real keyboard behavior on iOS and Android
- 60fps long-thread scrolling under production message volume
- image picker permission prompts and upload on device
- document picker upload on device
- microphone permission prompt, recording, and upload on device
- push notification tap into a conversation
- background/foreground sync behavior during poor network conditions
- attachment playback beyond native preview rows

## Current Status

The native Messenger foundation is now a production-shaped client surface, but it is not ready to replace the WebView Messenger until device QA confirms permissions, uploads, scrolling, push deep links, and long-thread behavior on real iOS and Android hardware.
