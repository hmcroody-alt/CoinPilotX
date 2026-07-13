# PulseSoc Native Messenger — WebView Target Deep Wiring

Date: 2026-07-12

Active subsystem: PulseSoc Messenger / Pulse Command

## Outcome

The production WebView Messenger remains the target. The existing native `MessengerScreen`, `ChatScreen`, Messenger API/cache/offline queue, Pulse Command primitives, Groups, Rooms, Pulse AI, calls, media viewer, notification routing, and deep links were retained and inspected rather than replaced.

The largest current target gap—the production Conversation Control Center entry and section hierarchy—was added natively to the existing chat. It is intentionally capability-aware: actions backed by current native/server contracts are interactive; settings without inspected enforcement contracts are displayed as explicit boundaries instead of fake toggles.

Messenger is **not simulator-parity frozen** and cannot replace the WebView Messenger yet.

## Production target inspected

- `templates/pulse_messages_v2.html`: inbox, thread header, composer, action sheets, creation modals, Control Center sheet.
- `static/css/pulse_messages_v2.css`: conversation density, bubble geometry, composer geometry, Control Center treatment.
- `static/js/pulse_messages_v2.js`: inbox/thread mutations, creation, realtime, Control Center hydration and actions.
- Production Messenger/call endpoints in `bot.py` and the existing native API wrappers.

## Native implementation inspected and reused

- `mobile-native/src/screens/MessengerScreen.tsx`
- `mobile-native/src/screens/ChatScreen.tsx`
- `mobile-native/src/api/messenger.ts`
- `mobile-native/src/components/PulseCommand.tsx`
- `mobile-native/src/pulseCommand/domain.ts`
- `mobile-native/src/screens/GroupsScreen.tsx`
- `mobile-native/src/screens/PulseAiScreen.tsx`
- `mobile-native/src/screens/CallScreen.tsx`
- `mobile-native/src/calls/IncomingCallLayer.tsx`
- Existing media viewer, camera, document/image picker, voice recording, cache, offline queue, sync invalidation, deep links and notification routing.

## Production-to-native matrix

| Area | Native status | Classification |
|---|---|---|
| Inbox header/search/filters | All, Direct, Groups, Rooms, AI, Unread; cached fallback and persisted filter | Code-path and prior simulator verified |
| Conversation rows | unread, presence, typing, attachment, failed, pinned/muted signals | Code-path and prior simulator verified |
| Direct chat | virtualized messages, reply, reactions, delete/report, retry, delivery, draft | Code-path and controlled-backend verified |
| Composer | multiline, attachment menu, image/camera/file/voice, queued send, keyboard coordinate handling | Code-path; real permissions physical-device-only |
| Control Center | production section order, search, expansion, stats, export, cache, safety | Newly code-path verified |
| Notifications | global/system paths exist; per-conversation override endpoint not inspected | Boundary, incomplete |
| Appearance/privacy/accessibility | native system/production rules respected; unsupported per-chat overrides not simulated | Boundary, incomplete |
| Media/storage | conversation-derived counts, authenticated viewer, known bytes, safe local cache clear | Code-path verified |
| Productivity | server pin helper exists; archive/reminder/note/task contracts not fully exposed natively | Incomplete |
| Security/Danger Zone | honest transport boundary plus Safety Hub report/block routing | Code-path verified |
| Groups/Rooms | current native detail/domain foundation reused | Code-path and prior simulator verified; full roles incomplete |
| Pulse AI | native routing/history/error surface reused; internal model/routing hidden | Code-path verified |
| Calls | existing native incoming/outgoing/audio/video lifecycle reused | Simulator state only; real media physical-device-only |
| Realtime/offline | sync polling, cached inbox/messages, durable client-ID queue, reconciliation | Controlled-backend verified; automatic live reconnect matrix incomplete |

## New Conversation Control Center

Production section order is preserved:

1. Conversation
2. Notifications
3. Appearance
4. Privacy
5. Media
6. Productivity
7. Storage
8. Security
9. Accessibility
10. Danger Zone

Real actions include transcript export through the native share sheet, message/media/file statistics from current conversation state, local cache clearing without server deletion, and report/block routing through the existing Safety Hub. The sheet includes search, expandable sections, accessibility roles/states, a drag handle, close control, safe scrolling, and user-facing capability boundaries.

## Verification

- `npm ci --prefix mobile-native --no-audit --no-fund --progress=false`: passed.
- `npm run --prefix mobile-native typecheck`: passed.
- Expo Doctor: 17/17 passed.
- Focused Messenger WebView-target audit: passed.
- Pulse Command exact parity audit: passed.
- Native Messenger, Groups/Rooms, calls, group/room detail and Pulse AI audits: passed.
- Controlled backend `messenger_core_audit.py`, `messenger_api_audit.py`, and `messenger_media_composer_wiring_audit.py` under `venv/bin/python`: passed. These exercised direct/room/group messages, compatibility sends, group creation, notification handoff, attachments and voice-message contracts. Push dispatch reported not configured because the controlled users had no device tokens; no delivery was falsely claimed.
- Release iOS Simulator build with updated embedded bundle: passed. The bundle contains the new Control Center.
- Simulator launch/deep link: reached the login surface because the Release simulator did not retain an authenticated session. Control Center visual interaction is therefore blocked in this mission and not counted as verified.
- Connected iPhone 16 Pro Release build with development bundle identity: passed.
- Physical installation and launch as `com.pulsesoc.nativeapp.dev`: passed. Interactive Control Center, media and call behavior remain unverified through command-line launch alone.

One older incoming-call audit failed only because it expects a stale roadmap phrase (`Full-screen incoming calls`) in the authoritative progress report; the current native calls and exact-parity audits pass. This stale documentation assertion was not hidden or repaired by changing the active roadmap.

## Evidence

`reports/screenshots/native-messenger-webview-target-deep-wiring-2026-07-12/`

- `pro-simulator-auth-blocked.png`: fresh Release simulator evidence of the authentication blocker. It is not presented as Control Center proof.
- Physical iPhone 16 Pro evidence is limited to successful signed build/install/launch command results; the installed device CLI does not provide a post-change screenshot command.

## Honest completion percentages

| Area | Completion |
|---|---:|
| Overall Messenger capability | 76% |
| UI parity | 82% |
| Visual quality | 84% |
| Interaction parity | 78% |
| Deep wiring | 74% |
| Inbox | 90% |
| Conversation list | 88% |
| Direct chat | 86% |
| Message bubbles | 88% |
| Composer | 86% |
| Reactions | 82% |
| Replies | 82% |
| Attachments | 76% |
| Voice messages | 62% |
| Groups | 74% |
| Rooms | 68% |
| AI | 72% |
| Voice calls | 62% |
| Video calls | 56% |
| Control Center | 70% |
| Notifications | 48% |
| Appearance | 38% |
| Privacy | 50% |
| Media | 68% |
| Productivity | 34% |
| Storage | 62% |
| Security | 52% |
| Accessibility | 74% |
| Danger Zone | 72% |
| Dashboard | 45% |
| Realtime | 66% |
| Offline/reconnect | 72% |
| Loading/empty/error | 82% |
| Responsive behavior | 80% |
| Performance | 82% |
| Xcode Simulator QA | 64% |
| iPhone 16 Pro QA | 20% |
| Device-size coverage | 52% |
| Backend/business reuse | 95% |
| Frontend utility reuse | 94% |
| Existing native component reuse | 96% |

## Remaining gaps

- Per-conversation notification, appearance, privacy and accessibility overrides need enforceable production API contracts before native toggles can be enabled.
- Archive, favorite, reminder, note, task, pinned-message list, message stats service and complete dashboard destinations remain incomplete.
- Forward, copy, edit, message-info, save/download and pin actions are not all exposed in the native message action sheet.
- Full group/room role mutation matrices remain incomplete.
- AI streaming/feedback and premium/access gates require deeper controlled verification.
- Physical voice/video calls, Bluetooth, speaker routing, camera, microphone, photo library, background push and lock-screen behavior remain physical-device-only.
- Complete compact/standard/Pro/Pro Max current-mission evidence remains incomplete.

## Freeze and replacement decision

- Simulator-parity frozen: **NO**.
- Physical-device frozen: **NO**.
- WebView replacement ready: **NO**.
- WebView/native parallel compatibility: **YES**; no production Messenger backend, template, CSS, JavaScript or database contract was changed.

## Next exact Messenger mission

Remain on Messenger. Add production-backed conversation preference/storage APIs or explicitly remove unsupported WebView controls at the source contract level; complete native message copy/forward/edit/info/pin actions; then execute the compact/standard/Pro/Pro Max Control Center and real iPhone 16 Pro media/call matrix.
