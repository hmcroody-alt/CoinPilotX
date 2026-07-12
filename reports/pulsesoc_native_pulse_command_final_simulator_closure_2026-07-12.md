# PulseSoc Native Pulse Command — Final Simulator Closure

Date: 2026-07-12
Branch: `main`
Evidence: `reports/screenshots/native-pulse-command-final-simulator-closure-2026-07-12/`

## Decision

Pulse Command is **not simulator-parity frozen**. This mission closed the keyboard obstruction and selected-filter visibility gaps and added a durable reuse-first outbound text queue, but it did not produce honest end-to-end simulator proof of network loss, automatic server reconciliation, server-ID replacement, reactions, unread reconciliation, or the complete nested Group/Room/AI state matrices. A focused simulator mission remains necessary before physical-device-only release verification.

## Production and native sources inspected

- Production `/pulse/messages` was reinspected for the Pulse Command/Messenger V3 header, search, `All / Direct / Groups / Rooms / AI / Unread` hierarchy, quick actions, conversation rows, composer, calls, details, and safety placement.
- Production sources: `templates/pulse_messages_v2.html`, `static/css/pulse_messages_v2.css`, `static/js/pulse_messages_v2.js`.
- Native sources: `MessengerScreen`, `ChatScreen`, shared `PulseCommand` primitives, Messenger API/cache wrapper, `GroupsScreen`, group/room detail primitives, `PulseAiScreen`, navigation and linking.

## Files changed

- `mobile-native/src/components/PulseCommand.tsx`
- `mobile-native/src/screens/MessengerScreen.tsx`
- `mobile-native/src/screens/ChatScreen.tsx`
- `mobile-native/src/screens/PulseAiScreen.tsx`
- `mobile-native/src/api/messenger.ts`
- `scripts/pulsesoc_native_pulse_command_exact_parity_audit.py`
- `scripts/pulsesoc_native_messenger_audit.py`
- `scripts/pulsesoc_native_groups_audit.py`
- this report and the authoritative progress report

No WebView API, database contract, production event, or production route was changed.

## Implemented and reused

- Reused the shared horizontal Pulse Command rail; added selected-index scrolling, stable test IDs, and native selected accessibility state.
- Reused AsyncStorage already present in the Messenger layer to persist the selected filter.
- Reused `sendConversationMessage`, existing client message IDs, existing conversation sync, existing message merging, and existing message cache. The outbound queue is a small persistence boundary around those existing mechanisms, not a second transport stack.
- Queue insertion deduplicates by `client_message_id`; drain retains failed items and returns successful server messages to the existing merge path.
- Reused the existing composer. Its keyboard position now follows the actual iOS keyboard end coordinate, while the status banner is suppressed during keyboard presentation so it cannot cover the input.
- Removed the visible internal `Powered by LogiNexus Intelligence` subtitle from UNDX.

## Keyboard blocker resolution

- Root cause: iOS first-use keyboard tutorial overlaid the composer on the prepared simulator.
- Repair: completed the tutorial once on the affected simulator and verified `DidShowContinuousPathIntroduction = 1`; no simulator erase or unrelated data deletion occurred.
- Persistence: persisted across subsequent launches.
- Evidence: `promax-keyboard-reply.png` shows the full keyboard, reply preview, attachment control, input, and Send control unobscured.
- Limitation: the mission captured the previously blocked Pro Max reply state, but did not recapture every requested empty/short/multiline state on all three widths.

## Evidence classification

### Simulator verified

- Pro Max keyboard open with reply preview and composer above the keyboard: `promax-keyboard-reply.png`.
- Restored Unread filter selected and fully auto-scrolled into view with six unread conversations: `pro-unread-filter-restored.png`.
- Groups/Rooms loading surface: `pro-group-room-nested.png`.
- Populated Rooms rail with room identity, previews, active/unread/energy metadata and Open Room actions: `pro-group-room-state.png`.
- UNDX nested empty-history/composer state with production-facing label and no visible internal branding: `pro-ai-conversation.png`.

### Mock-state verified

- Inbox rows and unread counts in the deterministic Messenger QA fixture used for the restored-filter screenshot.
- Populated local Groups/Rooms data shown in the simulator. This proves rendering, not live membership/audio authorization.

### Code-path verified

- Filter order, selected accessibility state, persistence, restore, and rail auto-scroll.
- Durable text queue insertion, client-ID duplicate prevention, failed-item retention, automatic drain on foreground sync, and merge into the existing server-message path.
- Existing draft persistence, reply/composer paths, cached conversation fallback, group/room detail routes, and UNDX request handler.

### Physical-device-only

- Real camera/microphone capture, Bluetooth and speaker routing, real room audio/video, push-triggered/lock-screen/app-killed calls, cellular/Wi-Fi transitions, background call continuity, and large real-media uploads.

### Blocked / not proven

- Live deterministic network disable/restore sequence with automatic queue drain and server identifier replacement.
- Live reaction/read/unread/inbox-preview reconciliation and socket missed-event recovery.
- Failed attachment retry across a real connection transition.
- Complete nested Group details/member/admin/non-admin/moderation/error/reconnect matrix.
- Complete Room joined/leave/host/speaker/listener/private/full/ended/error/reconnect matrix.
- Live UNDX response/streaming/error/retry/history/access-gate/reconnect matrix.
- Compact and standard-width keyboard evidence requested by the mission.

## Verification

Passed:

- `npm ci --prefix mobile-native --no-audit --no-fund --progress=false`
- `npm run --prefix mobile-native typecheck`
- `EXPO_DOCTOR_ENABLE_DIRECTORY_CHECK=0 npx expo-doctor --verbose` — 17/17
- native Debug iPhone Simulator `xcodebuild` — `BUILD SUCCEEDED`
- `scripts/pulsesoc_native_pulse_command_exact_parity_audit.py`
- `scripts/pulsesoc_native_messenger_audit.py`
- `scripts/pulsesoc_native_groups_audit.py`
- `scripts/pulsesoc_pulse_command_group_room_detail_audit.py`
- `scripts/pulsesoc_native_global_navigation_audit.py`
- `scripts/pulsesoc_native_mission_standard_audit.py`
- `git diff --check`

Pre-existing broader audit drift, not suppressed:

- `pulsesoc_native_intelligence_audit.py` expects an older intelligence fallback route and older progress-report title unrelated to Pulse Command.
- `pulsesoc_native_architecture_health_audit.py` expects an older next-subsystem recommendation.

## Honest parity status

| Area | Status |
|---|---:|
| Production layout parity | 92% |
| Production visual parity | 87% |
| Production feature parity | 92% |
| Production interaction parity | 88% |
| Inbox parity | 94% |
| Conversation-list parity | 94% |
| Conversation-screen parity | 86% |
| Message-bubble parity | 86% |
| Reply/reaction/context-menu parity | 84% |
| Composer parity | 87% |
| Attachment parity | 78% |
| Calls parity | 68% |
| Groups parity | 76% |
| Rooms parity | 72% |
| AI/UNDX parity | 76% |
| Safety parity | 70% |
| Offline/reconnect parity | 68% |
| Responsive behavior | 91% |
| Loading/empty/error parity | 78% |
| Xcode Simulator QA | 78% |
| Device-size simulator coverage | 100% |
| Backend/business logic reuse | 96% |
| Frontend utility reuse/extraction | 93% |
| Existing native component reuse | 95% |

## Final inspection and next recommendation

- Production design hierarchy remains unchanged; no production controls were removed or moved.
- WebView and native clients can continue operating in parallel because the work is native-only and preserves existing API contracts.
- Current users would not experience a Messenger redesign from these changes.
- Replacement readiness: not ready. Simulator closure still needs one evidence-first Pulse Command reconciliation and nested-state mission; physical-device verification follows after that.
- Next exact subsystem: **remain on Pulse Command**. Repository and evidence inspection do not support selecting another page while automatic reconnect and nested Group/Room/AI matrices remain unproven.
