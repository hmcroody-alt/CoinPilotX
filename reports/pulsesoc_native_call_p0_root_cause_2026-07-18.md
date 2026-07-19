# PulseSoc Native Call P0 Root-Cause and Repair Report

Date: 2026-07-18

## Release judgment

The phantom-call and route-mismatch defects are repaired in source. Simulator and connected-iPhone build/install/launch gates pass. A controlled second authenticated participant is still required before audio/video quality or cross-client interoperability can be certified. No unmeasured quality claim is made.

## Phantom `Vilson` trace

| Layer | File/component | State or event | Observed value | Why it activated | Corrective action |
| --- | --- | --- | --- | --- | --- |
| Database | `communication_calls` | Non-terminal call status | accepted/connecting/connected/active/reconnecting | Old rows had no non-ringing expiry validation | Server now expires stale non-ringing sessions and makes teardown terminal |
| Database | `communication_call_participants` joined to `users` | `display_name` | `Vilson` | `_serialize_call` populated the remote name from the canonical production user row | Identity remains canonical; no fixture or hardcoded `Vilson` exists in the repository |
| API | `services/pulsesoc_communications_engine.py::active_calls` | `/api/calls/active` response | First stale active session | The old query trusted call status without requiring the current participant to remain joined/ringing | Active query now performs stale cleanup and requires current participant state `joined` or `ringing` |
| Native polling | `IncomingCallLayer.refreshActiveCalls` | `floatingCall` | First accepted/connecting/connected/active/reconnecting call | Cached/server metadata was treated as sufficient proof of an active call | Global active-call restoration was deleted; the layer only presents canonical incoming ringing calls |
| Root render | `mobile-native/App.tsx` | `IncomingCallLayer` | Global active-call capsule | The globally mounted layer contained both incoming UI and an active-call mini-controller | Mini-controller branch, state, controls, styles, timers, and touch target were removed; incoming-call UI remains |

The exact production call row ID could not be recorded because the browser session had expired and `/api/calls/active` returned `Login required`. The value origin is nevertheless deterministic: `Vilson` is absent from source and fixtures, while `_serialize_call` reads `COALESCE(users.display_name, users.username)` for call participants.

## Incorrect upload error trace

| Operation | WebView route/service | Backend handler | Native route before | Correct native route |
| --- | --- | --- | --- | --- |
| Create audio call | production call client / Communications V2 | `start_conversation_call` | `/api/pulse/comm/v2/conversations/:id/voice/start` | `/api/pulse/communications/v2/conversations/:id/voice/start` |
| Create video call | production call client / Communications V2 | `start_conversation_call` | `/api/pulse/comm/v2/conversations/:id/video/start` | `/api/pulse/communications/v2/conversations/:id/video/start` |
| Fetch session | `/api/calls/:id/status` | `api_call_status` | same | same |
| Accept | `/api/calls/:id/accept` | `api_accept_call` | same | same |
| Decline | `/api/calls/:id/decline` | `api_decline_call` | same | same |
| Cancel/end | `/api/calls/:id/end` | `api_end_call` | same | same |
| Join/reconnect credentials | `/api/calls/:id/join-token` | `api_call_join_token` | same | same |
| Offer/answer/ICE | LiveKit room established by signed join token | LiveKit provider | no native upload route | same LiveKit room and credentials |
| Participant/status events | `/api/calls/:id/events`, realtime call events | Communications engine/realtime engine | same | same |

The wrong native prefix produced an HTTP 404. `bot.py` then mislabeled every 404 as `Upload endpoint was not found.` Call setup does not require the generic composer/media uploader. The prefix is corrected and call-path 404s now receive a call-specific diagnostic code and safe copy.

## Production foundation reused

- WebView: `static/pulsesoc_calls.js`, `static/js/pulsesoc_global_call_overlay.js`
- Backend: `pulse_communications_v2/routes.py`, `services/pulsesoc_communications_engine.py`, global JSON error handler in `bot.py`
- Native: `src/api/calls.ts`, `useNativeCallRoom.ts`, `CallScreen.tsx`, `IncomingCallLayer.tsx`, `ChatScreen.tsx`
- Preserved: canonical user, conversation, call, room, and participant IDs; production authorization; Communications V2 states; LiveKit signed token and room; call history; push/deep-link contracts
- Not created: native-only user store, call database, signaling protocol, status vocabulary, or media-upload call path

## UI and lifecycle repairs

- Removed the global active-call banner from the native root presentation on every route, including during legitimate calls.
- Retained full-screen incoming-call presentation and the dedicated Call route.
- Removed local active-call cache as authority; old `pulsesoc.native.calls.active` data is deleted when read.
- CallScreen now uses canonical status fetches and tears media down for all terminal states, including expired/rejected/disconnected.
- Removed generic `PulseSoc Voice`, `PulseSoc Video`, `UNKNOWN`, and participant `P` fallbacks from authoritative identity/status positions.
- Replaced internal route/upload failures with accurate user-safe call copy.
- Compacted call header, participant area, and control dock while preserving 44-point-or-larger controls.
- Rebuilt the Messenger composer around `KeyboardAvoidingView`, eliminating the manual keyboard-height offset and dead panel space.
- Added a compact `PULSE LINK` readiness/reconnect/sending/recording strip and retained canonical attachment, emoji, microphone, and send actions.
- Bounded multiline composer growth to 76 points, removed the keyboard-time safe-area gap, and kept the software keyboard flush with the illuminated composer panel.

## Verification matrix

| Check | Result |
| --- | --- |
| TypeScript typecheck | PASS |
| Python compile | PASS |
| Expo Doctor | PASS, 17/17 |
| Static P0 call audit | PASS |
| Behavior-level stale-call audit | PASS: stale `connected` row expired, participant left, fresh joined call retained |
| No global banner code/mount branch | PASS |
| Correct conversation call route | PASS |
| Generic upload 404 copy removed | PASS |
| Server stale-call cleanup | PASS in isolated engine behavior audit; production deploy pending |
| Production route reachability | PASS: unauthenticated `GET` receives 405 on both canonical POST-only voice/video start paths |
| Simulator build/visual QA | PASS: Release login rendered; Debug cold launch had no phantom call; keyboard composer rendered compactly |
| Simulator evidence | `reports/screenshots/native-call-p0-2026-07-18/` |
| Physical iPhone build/install/launch | PASS: iPhone 16 Pro, iOS 18.7.3, USB, paired, Developer Mode enabled |
| Device app identity | PASS: `com.pulsesoc.nativeapp.dev`, `PulseSoc Native Dev`, Apple Development team `87ZC69AGSR` |
| Side-by-side safety | PASS by bundle targeting: production `com.pulsesoc.app` was never built, installed, removed, or replaced. The current device app listing did not expose `com.pulsesoc.app`, so its present installation is not claimed. |
| Real native-to-native call | BLOCKED: second authenticated device/account required |
| Native-to-WebView call | BLOCKED: second authenticated peer and deployed backend required |
| Bluetooth/headset quality | BLOCKED: physical controlled call required |
| Packet loss/jitter/RTT/video metrics | NOT MEASURED; no real two-party media session |

## Next exact remediation

Deploy the backend stale-session/404 correction from this commit, authenticate two controlled production test accounts on separate clients, then run WebView-to-native and native-to-native audio/video calls while collecting LiveKit WebRTC statistics and iOS route/interruption evidence. Calls must remain blocked from internal-beta approval until that matrix passes.
