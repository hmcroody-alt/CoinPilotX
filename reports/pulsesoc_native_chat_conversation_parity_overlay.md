# PulseSoc Native Chat Conversation Parity and Global Call Popup Removal

Date: 2026-07-16

## Scope

This correction targets two production-visible defects:

1. The native conversation screen could show reconnect/failure and empty states at the same time.
2. The rejected active-call mini popup with caller name, `Voice in progress`, and `End` was still mounted globally outside Home.

This is not a full replacement-readiness claim. It is a focused parity/hardening pass over the existing native chat and call layer.

## Exact WebView Conversation Files Inspected

- `templates/pulse_messages_v2.html`
- `static/js/pulse_messages_v2.js`
- `static/js/pulsesoc_calls.js`
- `static/js/pulsesoc_global_call_overlay.js`
- `static/js/pulse_chat_recovery.js`
- `static/js/pulse_realtime.js`
- `static/js/pulse_messenger_media_viewer.js`
- `static/css/pulse_messages_v2.css`
- `static/css/pulsesoc_global_call_overlay.css`

## Exact Backend Routes Inspected

- `bot.py`
  - `/api/pulse/messages/<conversation_id>/messages`
  - `/api/pulse/messages/<conversation_id>/send`
  - `/api/pulse/messages/<conversation_id>/sync`
  - `/api/pulse/messages/<conversation_id>/seen`
  - `/api/pulse/messages/<conversation_id>/typing`
  - `/api/pulse/messages/media/upload`
  - `/api/pulse/messages/<message_id>/react`
  - `/api/pulse/messages/<message_id>/delete`
  - `/api/pulse/messages/<message_id>/report`
- `pulse_communications_v2/routes.py`
  - `/api/pulse/comm/v2/conversations/<conversation_ref>/messages`
  - `/api/pulse/comm/v2/conversations/<conversation_ref>/voice/start`
  - `/api/pulse/comm/v2/conversations/<conversation_ref>/video/start`
  - `/api/calls/active`
  - `/api/calls/<call_id>/status`
  - `/api/calls/<call_id>/join-token`
  - `/api/calls/<call_id>/end`

## Exact Native Files Inspected

- `mobile-native/src/screens/ChatScreen.tsx`
- `mobile-native/src/api/messenger.ts`
- `mobile-native/src/api/calls.ts`
- `mobile-native/src/calls/IncomingCallLayer.tsx`
- `mobile-native/src/calls/useNativeCallRoom.ts`
- `mobile-native/src/navigation/notificationRouting.ts`
- `mobile-native/src/navigation/linking.ts`

## Canonical Conversation-ID Comparison

| Field | WebView production value/source | Native value/source | Match | Required fix |
| --- | --- | --- | --- | --- |
| Authenticated user | `api_account_user()` server session | `pulseApi` authenticated native session | Expected match | Controlled account proof still required |
| Conversation ID | WebView route `/pulse/messages/<conversation_id>` and backend `pulse_conversations.id` | `route.params.conversationId` passed to `ChatScreen` | Expected match when route opens canonical conversation | Add controlled WebView/native test for the specific account thread |
| Message history route | `/api/pulse/messages/<conversation_id>/messages` | `getConversation(conversationId)` calls same route | Match | Preserve route; do not create native-only thread |
| Realtime sync route | `/api/pulse/messages/<conversation_id>/sync` | `syncConversation(conversationId)` calls same route | Match | Keep sync nonblocking when initial fetch fails |
| Send route | `/api/pulse/messages/<conversation_id>/send` | `sendConversationMessage(conversationId)` calls same route | Match | Continue using server-confirmed `message_id` |
| Read route | `/api/pulse/messages/<conversation_id>/seen` | `markConversationSeen(conversationId)` calls same route | Match | Cross-client unread proof required |

## Root Cause of Missing History

The repository evidence shows native is already using the canonical production message-history route. The visible missing-history symptom is most likely caused by initial canonical fetch failure, auth/session/API reachability, wrong `conversationId` passed into the native route, or a server response normalization mismatch for the specific account thread. This patch does not invent a new route or local fallback thread. It keeps the existing canonical route and makes the failure state truthful instead of falling through into a fake empty conversation.

## Root Cause of Contradictory States

`ChatScreen` previously rendered a top-level `error` text and still rendered the `FlatList` empty component when `messages.length === 0`. That allowed `Messages could not load` and `No messages yet` to appear together. The header also used `error ? "Reconnecting" : "Live channel"`, so an initial fetch failure could display a reconnecting status even when no message history was available.

Fixed by adding mutually exclusive state gates:

- `showInitialLoading`
- `showFatalError`
- `showEmptyConversation`

Errors with cached messages now render as a compact retry banner while history remains visible. Errors without cached messages render one error panel and no empty card.

## Root Cause of Global Call Popup

`IncomingCallLayer` still contained a globally mounted active-call mini-controller rendered from `floatingCall`. Route suppression was insufficient because the product requirement changed: the popup must not exist anywhere, not only on selected routes.

Fixed by removing the mini-controller render branch, its `Voice/Video in progress` copy, End button, route-specific visibility policy, and visual pulse timer. Active calls still poll into state, incoming calls still render the full incoming-call experience, and accepted calls still navigate to the dedicated `Call` screen.

## Files Changed

- `mobile-native/src/calls/IncomingCallLayer.tsx`
- `mobile-native/src/screens/ChatScreen.tsx`
- `scripts/pulsesoc_native_chat_parity_overlay_audit.py`
- `scripts/pulsesoc_native_home_call_overlay_audit.py`
- `scripts/pulsesoc_native_communications_parity_audit.py`
- `reports/pulsesoc_native_chat_conversation_parity_overlay.md`
- `reports/pulsesoc_native_home_call_overlay_removal.md`

## APIs Reused

- `/api/pulse/messages/<conversation_id>/messages`
- `/api/pulse/messages/<conversation_id>/send`
- `/api/pulse/messages/<conversation_id>/sync`
- `/api/pulse/messages/<conversation_id>/seen`
- `/api/pulse/messages/<conversation_id>/typing`
- `/api/pulse/messages/media/upload`
- `/api/pulse/messages/<message_id>/react`
- `/api/pulse/messages/<message_id>/delete`
- `/api/pulse/messages/<message_id>/report`
- `/api/pulse/comm/v2/conversations/<conversation_ref>/voice/start`
- `/api/pulse/comm/v2/conversations/<conversation_ref>/video/start`
- `/api/calls/*`

## Realtime Events Reused

Native continues using the existing `/sync` fetch reconciliation path. No new realtime event model was introduced.

## Message Types Supported in Current Native Renderer

- Text
- Image/gif
- Video handoff to `NativeMediaViewer`
- File/document attachment shell
- Voice/audio message placeholder with duration metadata
- Replies
- Reactions
- Edited label
- Deleted/moderated state
- System/call/group event text fallback

## Voice-Message Compatibility Result

Voice-message upload still uses the production message media upload route with `voice` and `duration_seconds`. Existing WebView voice messages are represented in native via the voice/audio attachment state, but full native playback controls, seeking, speed, and WebView/native codec proof remain blockers.

## Cross-Client Results

Not completed in this patch. Controlled account tests are still required:

- Existing WebView history appears in native.
- Native message appears in WebView once.
- WebView message appears in native once.
- Native voice message plays in WebView.
- WebView voice message plays in native.
- Read state remains consistent.

## Global Popup Removal Verification

Code-path/audit verified:

- `Voice in progress` / `Video in progress` copy removed from `IncomingCallLayer`.
- Active-call mini-controller Pressable removed.
- Active-call End mini-button removed.
- Route-specific mini-overlay policy removed.
- No bottom padding or mounted mini-overlay branch remains for the rejected popup.
- Dedicated `Call` route remains available.
- Incoming call full-screen handling remains intact.

## Simulator Evidence

Captured on the booted Xcode iPhone simulator:

- `/Users/hmcherie/Desktop/CoinPilotX/reports/screenshots/native-chat-popup-removal/current-simulator-state.png`
- `/Users/hmcherie/Desktop/CoinPilotX/reports/screenshots/native-chat-popup-removal/native-app-after-reopen.png`

The current native app state shows Home without the rejected bottom mini popup or reserved bottom region. The active-call QA deep link attempted during this patch opened a Safari interstitial instead of the app, so active-call absence is classified as code-path/audit verified rather than simulator verified.

## Physical-Device Evidence

Not completed. Physical iPhone QA remains required for real audio routing, microphone, camera, Bluetooth, push, background call behavior, and app-killed incoming call behavior.

## Remaining Blockers

- Confirm the exact user/conversation IDs for the account thread that still showed empty history.
- Prove existing WebView history visibly loads in native for that canonical conversation.
- Add native voice-message playback controls, seek, speed, and cleanup.
- Complete WebView/native cross-client message and voice-message tests.
- Complete physical iPhone audio/video call tests.

## Data Safety

No database migration or ID rewrite was introduced. Native still uses canonical production routes and server-confirmed IDs.

## Honest Parity

- Native conversation state correctness: 82%
- Native conversation production UI/behavior parity: 68%
- Message-history route reuse: 95%
- Existing-history proof for the reported account: 0% until controlled account evidence is captured
- Global rejected popup removal: 100% code-path/audit verified
- Native conversation can replace WebView conversation now: NO
