# MULTI_GUEST_CALL_FOUNDATION_MAP

Stage 0 forensic map for the multi-guest audio/video call mission.
Date: 2026-09-04. Branch: `codex/emergency-live-audio-recovery`.

## 1. Canonical RTC implementation (calls)

There is exactly ONE call media engine: **Agora (`react-native-agora` 4.6.2, pinned)**,
owned by the module-singleton **`mobile-native/src/calls/callSessionStore.ts`**.

- Engine lifecycle: created in `connectCallMedia`, released ONLY on explicit hang-up,
  terminal backend status, or authoritative failure. Never on screen unmount.
- `useNativeCallRoom.ts` (PROTECTED) → re-exports `useAgoraCallRoom.ts` → thin
  subscription over the store. No screen owns media.
- Join: `ChannelProfileCommunication` + `ClientRoleBroadcaster`,
  `publishMicrophoneTrack: true`, `autoSubscribeAudio/Video: true`.
- Token renewal: `onTokenPrivilegeWillExpire` / `onRequestToken` →
  `requestCallJoinToken` → `renewToken`.
- Remote presence: `onUserJoined`/`onUserOffline` already maintain
  `remoteUids: number[]` and fire `pollNow("rtc_hint")` — backend stays authoritative.
- Signaling: polling `getCallStatus` every 4200ms (700ms while ringing).

**Livestream is a separate system** (`useLiveBroadcastRoom`, live/cohost backend
routes, `generate_agora_live_token`). It shares nothing with calls except the Agora
SDK. It is OUT OF SCOPE and must not be touched.

## 2. Identity mapping

`rtc_uid == user_id`. Backend `_agora_uid(user_id)` maps the PulseSoc user id
directly to the 32-bit Agora uid. The canonical participant registry can therefore
join backend `participants[]` to `remoteUids[]` by id with no extra mapping table.

## 3. Backend: already a participant room model

`services/pulsesoc_communications_engine.py` + `pulse_communications_v2/routes.py`
(routes at lines ~1291–1470, `/api/calls/*`).

Schema (created in engine):
- `communication_calls`: public_id, conversation_id, `call_scope` ∈
  {direct, group, live, room}, call_type ∈ {audio, video}, status, room_name =
  `pulsesoc-{public_id}`, created_by_user_id.
- `communication_call_participants`: **UNIQUE(call_id, user_id)**, role
  caller/callee, status ringing/joined/left/declined/missed, muted_audio/muted_video.
- `communication_call_events`, quality reports, device sessions.

Statuses: ACTIVE = {created, ringing, accepted, connecting, connected, active,
reconnecting}; FINAL = {ended, missed, declined, failed, canceled, cancelled,
expired, rejected, disconnected}. `ALLOWED_TRANSITIONS` enforced in `_transition`.

Key behaviors verified:
- `start_call` (line ~1056): accepts `recipient_user_ids[]` (defaults to all other
  conversation members), dedupes, blocks self-call/blocked pairs, 409 if an active
  call already exists in the conversation, inserts caller as joined + recipients as
  ringing, generates caller token, `_notify_incoming_call` per recipient.
- `join_token` (~1183): `_require_call_access` → membership check → per-user token →
  marks participant joined → ringing/accepted → connecting.
- `accept_call` (~1217): idempotent; re-accept re-issues token.
- `decline_call` (~1298): per-participant; call → declined only when active
  (joined+ringing) ≤ 1 — group-safe.
- `end_call` (~1321): per-participant leave; call ends if `active < 2` **OR the
  creator ended** — the creator clause conflicts with the Stage-18 group policy
  (remaining participants must continue). Needs group-scope handling.
- Ring timeout: `_mark_missed_stale_calls_cur` (45s) marks whole-call missed only
  while status='ringing' (i.e. nobody accepted). **Gap:** once a call is
  connecting/connected, still-ringing invitees are never individually timed out to
  missed — Stage 30 needs per-participant ring timeout.
- Stale sweep: `_expire_stale_active_calls_cur` with env-tunable per-status TTLs.
- Token: `_generate_agora_token` (~436), `RtcTokenBuilder.buildTokenWithUid`,
  TTL `AGORA_TOKEN_TTL_SECONDS` (3600), `can_publish=True` for calls. Per-user,
  bounded, membership-verified — satisfies Stage 4 as-is.

## 4. Gaps to build (backend)

1. **Mid-call invite endpoint** — none exists (`POST /api/calls/<id>/invite`).
   Must reuse start_call validation (membership, blocked, dedupe, no self), insert
   ringing callee, emit event, `_notify_incoming_call`. Same channel — never a new
   room_name.
2. **Participant limits** — no cap anywhere. Add `CALL_MAX_VIDEO_PARTICIPANTS`
   (launch 6) / `CALL_MAX_AUDIO_PARTICIPANTS` (launch 12), enforced at start+invite.
3. **Flag gating** — `PULSE_GROUP_CALLS_ENABLED` exists in
   `pulse_communications_v2/flags.py` (line 28, env subflag, "Phase 7") but is
   consumed nowhere. Wire it to group-scope start (>1 recipient) and invite.
4. **Capabilities delivery** — no mobile config endpoint. Add
   `GET /api/calls/capabilities` (flags + limits) and/or embed in call payloads.
5. **Creator-leaves policy** — fix `end_call` for group scope: creator leave =
   leave; call ends when active participants < 2 (or explicit end-for-everyone).
6. **Per-participant missed** — time out invitees still ringing on a live call.
7. **Multi-device** — UNIQUE(call_id, user_id) already yields one participation per
   user. Policy: atomic replace (new join_token supersedes prior device). Document
   + test; no schema change needed.

## 5. Gaps to build (mobile-native)

`CallScreen.tsx` already renders `remoteUids.slice(0, 4)` in 50/50 tiles with a
participant-count pill. Missing:

1. **Canonical participant registry** — NEW unprotected `src/calls/callParticipants.ts`:
   backend `participants[]` ⋈ `remoteUids` (uid==user_id) + speaking + av states.
   No UI infers identity independently.
2. **Store extensions** (unprotected `callSessionStore.ts`):
   `enableAudioVolumeIndication` + `onAudioVolumeIndication` (active speaker),
   `onRemoteAudioStateChanged`/`onRemoteVideoStateChanged` (per-participant
   mute/camera display state).
3. **Grid layouts** — 1 full / 2 split / 3–4 2×2 / 5–6 adaptive; audio-scope
   avatar grid (no empty video boxes); subtle active-speaker glow, no reordering.
4. **Add-participant** — picker (reuse identity components, exclude self/current
   participants, show pending invites), `inviteToCall()` in `api/calls.ts`.
5. **Group ringing UX** — caller enters channel immediately; independent
   accept/decline; per-participant status chips.
6. **Capabilities client** — fetch flags/limits; hide multi-guest UI when off.

## 6. Protected paths (audio gate)

Declaration `reports/realtime_audio_change_declaration.md` REQUIRED for edits to:
`useNativeCallRoom.ts`, `callSignalMedia.ts`, `callKitBridge.ts`, `CallScreen.tsx`,
`src/api/calls.ts`, `useAgoraCallRoom.test.ts`. NOT protected (free to extend):
`callSessionStore.ts`, `callParticipants.ts` (new), `callToneLifecycle.ts`.
`bot.py` gated by diff content only; engine file gated via backend_symbols note.
Gate: `python3 scripts/realtime_audio_change_gate.py --base origin/main --head HEAD`.

Forbidden regardless: screen-level AVAudioSession setup, second mic track, second
RTC engine, new audio singleton, touching livestream, 7th `Audio.setAudioModeAsync`
call site (`callSignalMedia.ts` holds the call-tones slot in the frozen allowlist).

## 7. Supporting pieces confirmed

- `callSignalMedia.ts`: single owner of ring/ringback/cue tones + haptics;
  generation-token pattern prevents orphaned loops. Reuse as-is for group ringing.
- `callKitBridge.ts`: injectable CallKit/VoIP provider; answer→acceptCall,
  end→decline-or-end. Group calls flow through unchanged (per-callee push).
- Push: `_notify_incoming_call` via intake_event, dedupe key
  `incoming-call:{public_id}:{recipient_id}` — already per-recipient-per-call.
- `MinimizedCallBanner.tsx` + AppNavigator: minimize/restore survives navigation.
- Call history: one `communication_calls` row per session (Stage 28 satisfied by
  design); missed is whole-call today (see gap 6).

## 8. Execution order

1. Backend: invite + limits + flag gating + capabilities + creator-leave fix.
2. Store: registry + volume indication + remote AV state.
3. UI: grid, audio layout, picker, ringing states.
4. Tests: backend call suites, RTC jest suites (sharded 1–12, 40s timeout),
   1:1 regression first, audio gate + declaration.
5. Physical 3-device QA — owner-blocked; simulator never proves audio.
