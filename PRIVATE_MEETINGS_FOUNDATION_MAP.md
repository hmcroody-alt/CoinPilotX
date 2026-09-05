# PRIVATE_MEETINGS_FOUNDATION_MAP

Section 2 forensic map for the Private Office Private Meetings mission.

First produced 2026-09-03 (read-only session, no shell). **Revised 2026-09-05**:
every load-bearing claim below has now been re-verified by command against the
live tree (`grep -rn createAgoraRtcEngine`, direct reads of the communications
engine, the protection manifest, and the Private Office governance stack), and
the stale execution-blocker section from the first draft has been replaced with
the current execution state.

---

## 1. Verdict

**CAN THE EXISTING CALL ENGINE SUPPORT PRIVATE MEETINGS SAFELY?**

**YES — extend the canonical calls foundation. New Agora engine owners
required: 0. New mic / camera / audio-session owners required: 0. LiveKit: 0
present, 0 planned.**

| Capability | Answer | Basis |
|---|---|---|
| Multi-participant audio | **YES — extend, do not rebuild** | `callSessionStore.ts` is already a multi-participant Agora session singleton |
| Multi-participant video | **YES — extend** | same engine, `autoSubscribeVideo`, `remoteUids[]` |
| Grid / active speaker | **YES — extend** | `callParticipants.ts` registry + `enableAudioVolumeIndication(400,3,false)` already shipped |
| Roles / waiting room / lock | **YES — new backend policy above the existing room model** | `communication_calls.call_scope` already admits `"room"` (unused) |
| Reconnect identity | **YES — already correct** | `UNIQUE(call_id, user_id)`; `rtc_uid == user_id` |
| Recording | **YES — generalize** | `services/agora_cloud_recording_service.py` exists, server-only |
| **Screen sharing (iOS)** | **NO — would require a NEW Agora engine owner. ESCALATED, not built.** | §5 below |
| Transcription / captions | **NO — NOT CONFIGURED; no provider in the tree** | §6 below |

The mission's preferred outcome holds for the meeting itself and fails for
exactly one sub-feature. Per mission §3 — *"If architecture absolutely requires
a new engine owner: STOP AND REPORT BEFORE IMPLEMENTATION"* — screen share is
escalated in this map rather than silently introduced. It does not block the
rest of the system: the meeting surface ships with screen share in a truthful
NOT AVAILABLE state (§9's honest-surface rule).

---

## 2. RTC provider inventory

**Agora is the only RTC provider in the mobile app. LiveKit is absent.**

- `mobile-native/package.json` contains **no** LiveKit dependency (grep for
  `livekit|LiveKit|LIVEKIT` → no matches). The patch set on main is Hermes +
  Stripe only.
- `react-native-agora` 4.6.2 is pinned; `AgoraReplayKitExtension.framework` is
  vendored by the pod (`ios/PulseSoc.xcodeproj/project.pbxproj:289,317`) but no
  extension target consumes it.

Note for the record: `CLAUDE.md` still describes LiveKit as a call/live
dependency with a load-bearing WebRTC patch. **That claim is stale.** The
mission's `NO LIVEKIT` rule is satisfied by the current architecture, not by
anything this mission needs to remove.

### Engine owners — the hard count (re-verified 2026-09-05)

`createAgoraRtcEngine` appears in exactly **two** production call sites:

| # | Owner | Line | Domain |
|---|---|---|---|
| 1 | `mobile-native/src/calls/callSessionStore.ts` | 606 | audio + video calls |
| 2 | `mobile-native/src/live/useAgoraLiveBroadcastRoom.ts` | 229 | livestream |

This is asserted, not merely observed: `docs/realtime_audio_change_policy.md`
freezes the pair, `tests/protection/test_realtime_audio_gate_coverage.py` gates
both, and `tests/protection/test_agora_rtc_provider_contract.py` asserts that
`useAgoraCallRoom.ts` does **not** contain `createAgoraRtcEngine` — it is a thin
adapter, not a third owner. `callSessionStore.test.ts` asserts
`toHaveBeenCalledTimes(1)` on engine creation.

**Target for this mission: owners stay at 2. Private Meetings consumes owner #1
(the calls engine) exactly the way `CallScreen.tsx` does.**

### Why the calls owner, not the live owner

`callSessionStore.ts` is a module singleton (listener `Set` +
`useSyncExternalStore`, not Zustand) whose lifecycle already matches a meeting:
the engine is released only on hang-up, terminal status, or authoritative
failure — never on unmount — with a `joinRequested` latch and
`terminalHandledFor` idempotency, and token renewal deduped through a single
`renewalPromise`. `callParticipants.ts` already separates *WHO* (backend
`participants[]`) from *WHOSE MEDIA* (Agora `remoteUids[]`) with provisional
tiles for poll lag. Active-speaker volume events already flow. A meeting room
is this machinery with a different authority layer above it.

---

## 3. Media ownership (mic / camera / audio session)

Ownership is centralised and protected:

- `mobile-native/src/core/realtimeAudioEngine.ts` — canonical audio engine.
- `mobile-native/src/live/liveMediaOwnership.ts`, `liveAudioMatrix.ts` —
  arbitration.
- `config/realtime-audio-protected-paths.json` names `callSessionStore.ts` as
  the holder of call-audio ownership: *"drives enableAudio,
  muteLocalAudioStream, setEnableSpeakerphone and enableAudioVolumeIndication
  for every voice and video call."*
- The `expo-av` legacy allowlist is capped at six files; `callSignalMedia.ts`
  holds the call-tones slot. (The manifest's `allowed_paths` is a
  forbidden-API allowlist, not a protection grant — being named there does not
  make a file protected, and being protected does not require being named
  there.)

**Consequence for Private Meetings:** the meeting layer requests media through
`callSessionStore` only. No `Audio.setAudioModeAsync`, no
`AVAudioSession.setCategory`, no `engine.enableAudio()` from any meeting screen
or meeting module. A participant in the WAITING_ROOM state must not trigger
media connection at all — the `shouldConnectCallMedia`-style gate applies
before the store is asked to join. Zero new owners is the design constraint,
and it is achievable.

---

## 4. Backend foundation

`services/pulsesoc_communications_engine.py` already implements a participant
room model and is the **sole Agora token minter** in the tree.

- `VALID_CALL_SCOPES = {"direct", "group", "live", "room"}` (line 128).
  **`room` exists and is currently unused by any product surface** — it is the
  natural scope for a meeting.
- `communication_calls`: `public_id`, `conversation_id`, `call_scope`,
  `call_type`, `status`, `room_name = pulsesoc-{public_id}`,
  `created_by_user_id`; `ALLOWED_TRANSITIONS` state machine; caps
  `CALL_MAX_VIDEO_PARTICIPANTS=6` / `CALL_MAX_AUDIO_PARTICIPANTS=12`.
- `communication_call_participants`: **`UNIQUE(call_id, user_id)`** — one
  logical participant per user, which is precisely mission §8's
  no-duplicate-rows-on-reconnect requirement and §43's concurrency
  requirement, already enforced at the database level.
- Token authority: `_generate_agora_token` (calls — all-publisher) and
  `generate_agora_live_token` (role-differentiated: privilege `1 if
  can_publish else 2`, role-scoped TTLs — cohost/guest 1800s, host 7200s,
  audience 3600s). Both server-side only, membership-verified,
  `RtcTokenBuilder.buildTokenWithUid`. **The role-differentiated pattern is the
  mechanism for §47 least privilege**: a waiting-room occupant receives *no
  token at all*; an admitted participant receives a token whose publish
  privilege reflects their role at mint time.
- Identity: `_agora_uid(user_id)` → `rtc_uid == user_id`, matched on the native
  side by `callParticipants.ts` and on the backend by `live_participants.py`.
  Mission §5's mapping chain collapses to a join with no extra table. The
  recorder-uid convention depends on this and is preserved.
- Blocking is split across two systems and **both must be checked**:
  `comm_service._blocked_between` (comm_v2_blocks) and the `blocked_users`
  check used by `pulse_live_viewer_authorized`. Meeting invite/admit paths
  enforce both; a meeting code never bypasses either (§45).
- Signaling reality: there is **no SocketIO**, SSE is flagged off. Calls run on
  REST polling (4200 ms idle / 700 ms ringing) plus
  `services/realtime_engine.py`'s in-memory per-worker bus and push
  notifications (`pulsesoc_notification_system.intake_event` with
  `dedupe_key`). Waiting-room admit/deny and lock changes ride the same
  polling; the ringing-cadence fast path bounds admit latency.

### The one structural gap

`start_call` requires a **`conversation_id`** (line 1208–1210: missing →
`"conversation_id is required."`). A meeting is not a conversation: it has
invitees who may share no thread, a scheduled time, a waiting room, a meeting
code, and a lifecycle that outlives any chat. Meetings therefore need their own
entity — `private_meetings` — that **owns** a `communication_calls` row of
scope `room` rather than *being* one. Agora transport and session mechanics
stay on the proven path; PulseSoc owns meeting authority (mission §4's split,
honoured by construction).

Recommended shape: `private_meetings.call_id → communication_calls.id`; reuse
`communication_call_participants` for RTC-level presence while
`private_meeting_participants` owns role, invite status, and admission state.
No second participant registry (mission §59: reuse first).

---

## 5. SCREEN SHARE — STOP AND REPORT

**Screen sharing does not exist in this app today.** The only reference is a
disabled stub: `mobile-native/src/screens/LiveHostSessionScreen.tsx:81` —
`screen_share: "Screen Share is landing in an upcoming native build."` — and
the tile at line 1184 calls `flagComingSoon("screen_share")`. Grep for
`startScreenCapture|ScreenCapture|screenShare` across `mobile-native/src`
returns that one file. There is no capture path.

**Why this cannot be built within the zero-new-engine-owner rule.** On iOS,
Agora screen capture requires a **ReplayKit Broadcast Upload Extension**: a
separate process with its own bundle target, its own App Group entitlement,
and — the blocking part — **its own Agora engine instance inside the
extension**, publishing a second video track under a distinct uid. The app
process cannot capture the system screen on iOS.

Current state: `AgoraReplayKitExtension.framework` is vendored by the pod but
**no `.appex` target exists** — grep for `\.appex`, `BroadcastUpload`,
`com.apple.broadcast-services` in the pbxproj returns only the two
framework-copy lines.

Delivering §24 therefore requires: (1) a new Xcode app-extension target, (2) a
new App Group entitlement, (3) a **third `createAgoraRtcEngine` owner** in the
extension process, (4) a provisioning-profile change affecting the signed
build. Item 3 is exactly what mission §3 and the audio governance forbid
without separate approval; item 4 touches release signing.

**Ruling recorded in this map: escalated, not implemented.** The meeting
surface ships with screen share truthfully marked unavailable. A non-blocking
alternative — in-meeting **document/content presentation** of a Private Office
document (§32) — delivers much of the presentation value with zero new engine
owners and no signing change, but it is not screen share and will not be
labelled as such.

---

## 6. Transcription / captions — NOT CONFIGURED

Grep for `transcri` (case-insensitive) across `services/` returns only UNDX
policy/taxonomy/registry text. There is no speech-to-text provider, no
captions pipeline, and no transcript store.

Per mission §30 this is a design-the-contract-and-mark-unavailable outcome, not
a failure. But it propagates: **§33–§36/§70 UNDX meeting intelligence is
transcript-derived.** With no transcript there is nothing to derive summaries,
decisions, or obligations *from*. Mission §70 anticipates this — *"If
transcript unavailable: UNDX says so."* The honest deliverable is the contract,
the storage model (`private_meeting_artifacts`), provenance tagging
(TRANSCRIPT-DERIVED / USER-CONFIRMED / SYSTEM FACT), and a truthful
`TRANSCRIPT_UNAVAILABLE` state. UNDX can still produce SYSTEM-FACT intelligence
(who attended, duration, chat-derived notes the user confirms). Producing "3
decisions, 5 action items" without a transcript would be fabrication and is
refused.

---

## 7. Recording — reusable

`services/agora_cloud_recording_service.py` is a complete server-only Agora
Cloud Recording lifecycle (`acquire`, mode `mix`, R2 storage, `recorder_uid`,
server-side `_rtc_token` with subscriber role). It exposes `diagnostics()`
returning `configured: bool` over nine env vars, so §55's fail-closed
requirement is already expressible: unconfigured → recording controls report
truthfully unavailable.

It is currently keyed on `live_id`; generalising to a channel + artifact owner
is a bounded change behind `PRIVATE_MEETINGS_RECORDING_ENABLED`. No recording
credential reaches the client today and must not (§29). The DB stores metadata
only (§6).

---

## 8. Private Office security — satisfies §44/§72 as-is

`services/private_office/security.py` implements the second lock properly: a
logged-in session is not an unlocked Office; data flows only to a request
carrying a valid, unexpired, unrevoked unlock grant. `verify_and_unlock`,
`validate_grant`, `verify_step_up`, `revoke_grants`,
`on_account_security_event`, grant TTL, session+device binding,
failure/cooldown ladder. **The owner does not bypass this** — the module's
design intent is explicit, and mission §44 restates it.

`verify_step_up` is the right gate for high-impact meeting actions (start
recording, save derived obligations to the Office). No new security work
required — Meetings routes sit behind the same `_office_lock_gate` (423
LOCKED) as every other Private Office feature.

## 9. Private Office governance template — the honest-surface constraint

The governance stack Meetings must slot into, all existing:

- `feature_matrix.py` — canonical registration; `__post_init__` raises unless a
  non-IMPLEMENTED feature carries a note; availability resolves from
  implementation FIRST, entitlement second. `private_meetings` enters with
  truthful per-capability states: core meeting IMPLEMENTED (once built),
  transcription PROVIDER_REQUIRED, screen share NOT_IMPLEMENTED.
  `PRIVATE_MEETINGS_ENABLED` default FALSE (§54) maps to `flag_env`.
- Route discipline: authn 401 → `_resolve_for` → `_gate` (503/404/403 +
  minimum_tier) → `_office_lock_gate` (423) → work + inline audit +
  `_no_store`.
- `audit.py` — closed `ACTION_*` set; meeting actions extend the enum, not
  free-form strings.
- `telemetry.py` — allowlisted event emission; no meeting content, no tokens,
  no channel names in telemetry (§49).
- `schema.py` — package-local idempotent ensure; the six `private_meeting_*`
  tables (§6) follow this pattern, not `bot.init_db()`.
- `backend_management_registry.py` — `BackendFeature` + `verify_features`
  measured readiness for the Backend OS entry (§73: measured, not typed).
- Routes register via the existing `/api/private-office/*` route pack
  (`services/private_office_routes.py`) — no new route family, no new auth
  path.

## 10. Existing assets to reuse (§59)

| Need | Existing asset |
|---|---|
| Engine + session lifecycle | `mobile-native/src/calls/callSessionStore.ts` |
| Participant registry | `mobile-native/src/calls/callParticipants.ts` |
| Capabilities gating | `mobile-native/src/calls/callCapabilities.ts` |
| Add-participants mid-meeting | multi-guest calls' add-participant flow |
| Grid layout | CallScreen grid + `live/liveStageLayout.ts` pattern |
| Active speaker | `enableAudioVolumeIndication` already wired in the store |
| Telemetry privacy | `mobile-native/src/live/liveTelemetryPrivacy.ts` |
| Recording | `services/agora_cloud_recording_service.py` |
| Second lock / step-up | `services/private_office/security.py` |
| Feature honesty | `services/private_office/feature_matrix.py` |
| Audit / telemetry / schema | `services/private_office/{audit,telemetry,schema}.py` |
| Invites + push | `pulsesoc_notification_system.intake_event` (dedupe_key) |
| Document picker | `src/native/documents.ts` (ownership-guard enforced) |

No new invitation system, no new participant registry, no new chat primitive
before these are shown insufficient.

---

## 11. Protection obligations for this mission

- Any edit to a protected path (`callSessionStore.ts`, `callParticipants.ts`,
  `CallScreen.tsx`, `api/calls.ts`, …) requires the declaration
  (`reports/realtime_audio_change_declaration.md`), the FULL audio battery
  (`npm run test:realtime-audio-critical`, `test:realtime-audio`,
  `test:realtime-audio-architecture`; `python3 -m unittest
  tests.protection.test_realtime_audio_architecture`; pytest
  `test_agora_token_generation.py` + `test_agora_rtc_provider_contract.py`),
  and physical validation recorded in
  `reports/realtime_audio_verified_baseline.md`.
- `bot.py` is gated by diff *content* (`backend_diff_patterns`: `pulse_rtc_`,
  `AGORA_`, `can_publish`, …) — meeting routes registering there will trip the
  gate legitimately; run `scripts/realtime_audio_change_gate.py --base
  origin/main --head HEAD` locally before every commit.
- `dependency_watch` covers `package.json`/`Podfile`/patches — the design adds
  no dependencies, so this should stay silent.

## 12. Known risks

1. **Admit latency** — waiting-room admission rides REST polling; use the
   700 ms ringing-cadence path while in WAITING_ROOM so admission feels
   immediate.
2. **Host disconnect** — calls have no interruption-recovery window today; the
   meeting layer adds a bounded host-reconnect window server-side (§40)
   without touching engine lifecycle.
3. **Vestigial 1:1 fields** — `communication_calls` carries direct-call
   assumptions (e.g. `conversation_id`) that are cosmetic for scope `room`;
   nullable, not repurposed.
4. **Module-scope AppState trap** — meeting polling must not gate on a
   module-scope `AppState.currentState` snapshot (always "inactive" at
   launch); read it inside the tick or subscribe.
5. **WAITING must not connect media** — the waiting room is a backend state
   with no token; the native layer must never ask `callSessionStore` to join
   until admitted.

---

## 13. Execution state and order (2026-09-05)

The 2026-09-03 draft recorded a dead workspace shell blocking all tests and
commits. **That blocker is gone**: this session has a working shell, the
`.venv` runs the backend suites (143 Private Office tests green), the mobile
verify pipeline runs (353 suites / 5874 tests green at baseline commit
`2b13357a`), and the audio gate runs clean locally. Delivery follows mission
§77 consolidated-release mode: implement, test, **commit locally, do not
push**.

1. ✅ This map (re-verified).
2. Backend: `private_meetings` + participants + invites + messages +
   recordings + artifacts, owning a `communication_calls` row of scope
   `room`; server-authoritative lifecycle (§7) and participant states (§8);
   idempotent writes.
3. Backend: waiting room, admission, lock, roles, non-sequential revocable
   meeting codes — never an authorization bypass (§14, §45); block/privacy
   enforced on both blocking systems.
4. Backend: token authority — publish permission derived from admission state
   + role; waiting = no token (§15, §46, §47).
5. Backend: flags (`PRIVATE_MEETINGS_ENABLED` default FALSE), feature matrix
   row, Backend OS registry with measured readiness, audit actions,
   allowlisted telemetry.
6. Native: `src/privateOffice/meetings/` layered over `callSessionStore`; zero
   new engine/mic/camera/audio-session owners.
7. Native: Meetings home, schedule, join-by-code, waiting room, meeting room
   (grid / active speaker / controls), participants panel, summary; chat,
   reactions, raise hand; recording indicator; screen share + captions
   truthfully unavailable.
8. UNDX meeting intelligence contract with provenance tags and
   `TRANSCRIPT_UNAVAILABLE`; governed save-to-office behind step-up.
9. i18n ×11 locales; a11y labels.
10. Tests §60/§61; protection suite; full audio battery if any protected path
    changed.
11. Physical 3-participant acceptance on P3r7or + iPhone 17 Pro Max simulator
    — a simulator joining itself is not a pass.
12. Scoped local commits per §77; §78 final report.

---

## 14. Declaration (mission format)

```
PRIVATE MEETINGS
  → Agora-only multi-user meeting surface, layered over callSessionStore

NEW ENGINE OWNERS:          0           (screen share would require 1 — ESCALATED, not built)
NEW MIC OWNERS:             0
NEW CAMERA OWNERS:          0
NEW AUDIO SESSION OWNERS:   0
LIVEKIT:                    0 present, 0 planned
EXISTING AUDIO CALL:        untouched
EXISTING VIDEO CALL:        untouched
EXISTING LIVESTREAM:        untouched
```
