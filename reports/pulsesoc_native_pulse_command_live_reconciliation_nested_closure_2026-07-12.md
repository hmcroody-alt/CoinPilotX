# PulseSoc Native Pulse Command — Live Reconciliation and Nested-State Closure

Date: 2026-07-12

## Decision

Pulse Command remains **not simulator-parity frozen**. This mission controlled-backend-verified the server idempotency and send/reload contracts, closed two concrete native reconciliation defects, restored clean-install Metro runtime reliability, and added fresh Calls and Groups/Rooms evidence. It did not complete the required interactive offline network transition or the full nested-state matrices, so those items remain blocked rather than being represented as passed.

## Sources inspected

- Production: `templates/pulse_messages_v2.html`, `static/js/pulse_messages_v2.js`, `static/css/pulse_messages_v2.css`, canonical message send/idempotency routes in `bot.py`.
- Native: Messenger API queue/cache wrapper, `ChatScreen`, Pulse Command primitives, `GroupsScreen`, `PulseAiScreen`, `CallScreen`, incoming call layer, safety API, media viewer, navigation, and QA authentication path.

## Changes

- Declared `@babel/runtime`, which a clean mandated `npm ci` proved was required for a fresh Metro bundle.
- Queued messages are now cached immediately, so reopening an offline conversation does not discard the visible queued bubble.
- Queued local state is `queued`, not `failed`, and does not expose raw exception text.
- A positive server message with the same client ID clears stale local status/error and replaces the local identity through the existing merge path.
- Successful queue drain caches the reconciled list and shows `Messages reconnected.`
- Calls empty-state copy no longer exposes the internal `/api/calls/active` path.
- Existing exact-parity audit now asserts these transitions and user-facing copy.

## Controlled-backend reconciliation

Environment: repository-controlled local SQLite backend and Flask test clients exercised by existing integration audits.

Passed:

- first idempotent send accepted;
- retry with the same `client_message_id` returned the existing message;
- retry preserved the same positive server message ID;
- reply send succeeded;
- direct, legacy direct, room, legacy room, group, and legacy group messages appeared after reload;
- realtime typing coalescing passed.

This verifies the authoritative server half of duplicate prevention and server-ID stability. It does **not** prove a simulator network radio transition, socket reconnect timing, or the complete native visual transition sequence.

## Fresh simulator evidence

Directory: `reports/screenshots/native-pulse-command-live-reconciliation-nested-closure-2026-07-12/`

- `calls-list.png`: simulator-verified Calls ready/empty state after removing internal API copy.
- `group-room-list.png`: simulator-verified populated Rooms rail and Groups/Rooms shell.

The compact simulator exposed and helped repair the missing clean-install Babel runtime dependency. Authentication/UI automation on the newly prepared compact simulator did not reach the composer reliably, so no compact keyboard screenshot is claimed.

## Classification

### Simulator verified

- Pro iPhone Calls empty state and safe fallback placement.
- Pro iPhone populated Groups/Rooms shell and room cards.

### Controlled-backend verified

- Client-ID idempotency and stable server ID.
- Direct/group/room send and history reload.
- Reply send and realtime typing coalescing.

### Code-path verified

- Immediate queued-message cache persistence.
- Authoritative server-ID replacement clears stale local failure state.
- Successful drain cache update and reconnect completion copy.
- Existing group/room role boundaries, AI request/error handling, attachment upload/viewer reuse, safety report/block handlers, and call cleanup/state transitions.

### Mock-state verified

- Existing deterministic Messenger, incoming-call, attachment, and group/room fixture states from prior evidence remain fixtures; they are not relabeled as live.

### Physical-device-only

- Microphone, camera, Bluetooth/speaker routing, real room media, lock-screen/push/app-killed calls, background call continuity, cellular transitions, and large real-media uploads.

### Blocked

- Full simulator network disable → queued text/reply/attachment → restore → socket/API/drain visual sequence.
- Compact and standard keyboard matrices.
- Complete Group owner/admin/member/unauthorized-action visual matrix.
- Complete Room role/join/leave/reconnect/error matrix.
- Complete live UNDX response/stream/error/retry/history/gating matrix.
- Complete attachment and Calls state matrices.
- Complete safety authorization rejection matrix.

## Verification

- `pulse_realtime_infra_audit.py` — passed, including idempotent retry and stable ID.
- `chat_send_receive_audit.py` — passed for direct/group/room canonical and compatibility routes.
- TypeScript — passed after reconciliation changes.
- Remaining mandatory dependency, Doctor, iOS build, and audit gates are recorded in the final command log for this commit.

## Honest parity

| Area | Status |
|---|---:|
| Production layout parity | 92% |
| Production visual parity | 87% |
| Production feature parity | 92% |
| Production interaction parity | 89% |
| Inbox parity | 94% |
| Conversation-list parity | 94% |
| Conversation-screen parity | 87% |
| Message-bubble parity | 87% |
| Reply/reaction/context-menu parity | 84% |
| Composer parity | 87% |
| Attachment parity | 78% |
| Calls parity | 70% |
| Groups parity | 76% |
| Rooms parity | 73% |
| AI/UNDX parity | 76% |
| Safety parity | 70% |
| Offline/reconnect parity | 72% |
| Responsive behavior | 91% |
| Loading/empty/error parity | 79% |
| Xcode Simulator QA | 79% |
| Device-size coverage | 100% |
| Backend/business logic reuse | 97% |
| Frontend utility reuse/extraction | 93% |
| Existing native component reuse | 95% |

## Final recommendation

Do not advance to another subsystem. The next work remains Pulse Command and must establish reliable compact/standard authenticated UI automation plus a controlled simulator network transition. WebView and native remain parallel-compatible; no production APIs, database contracts, events, controls, or sections were removed or moved.
