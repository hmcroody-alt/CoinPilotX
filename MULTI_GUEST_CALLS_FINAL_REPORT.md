# Multi-Guest Calls — Final Mission Report (2026-09-04)

Commit: `6645171c` on `main` (15 files, +1,792 / −10).
Rollout switch: `PULSE_GROUP_CALLS_ENABLED` (server-owned, **default OFF** —
with the flag off the app is behaviorally identical to before this change).

## Architecture delivered

The existing call system was **converted** into a participant-based room model
— group calling was not built beside it. One `communication_calls` row, one
Agora channel (`room_name` never changes), N `communication_call_participants`
rows; 1:1 is simply the two-participant case. Identity flows one way:
backend `participants[]` → native registry (`callParticipants.ts`) → UI,
joined to Agora `remoteUids[]` strictly by `rtc_uid == user_id`. The UI never
infers identity, and no state is ever faked (remote mute renders ONLY on
RemoteMuted/RemoteUnmuted engine reasons — network stalls do not display as
muted).

## Verdicts

| # | Item | Verdict | Evidence |
|---|---|---|---|
| 1 | Participant room model (backend) | PASS | `invite_participants` rings into the SAME `room_name`; test asserts channel never changes |
| 2 | Server-owned capabilities/limits | PASS | `GET /api/calls/capabilities`; env `CALL_MAX_AUDIO/VIDEO_PARTICIPANTS`, floor 2; client hardcodes nothing |
| 3 | Feature flag gating | PASS | `PULSE_GROUP_CALLS_ENABLED` default OFF; start>1 recipient and invite both refused when off; client Add button hidden (conservative cached default = off) |
| 4 | Mid-call invite (backend-owned, idempotent, membership-checked, limit-capped) | PASS | `tests/test_call_multi_guest.py` 22/22 |
| 5 | Ringing/join lifecycle, re-invite reuses row | PASS | UNIQUE(call_id,user_id) proven; re-invite re-rings, never duplicates |
| 6 | Creator-leave policy | PASS | Creator leaving a group call ≠ end; explicit `end_for_everyone` only by creator |
| 7 | Per-participant missed | PASS | Unanswered invitee → their row 'missed'; the group keeps talking |
| 8 | Canonical native registry | PASS | `callParticipants.ts` + 9-test suite: strict id join, provisional media, no duplicates, teardown reset |
| 9 | Group call UI (grid 2/4/6, active speaker, mute badges, ringing chips, Add sheet) | PASS (code + jest) | CallScreen additive; `AddParticipantsSheet` is pure UI + one POST |
| 10 | Active-speaker indication | PASS (code) | `enableAudioVolumeIndication(400,3,false)` — pure reporting; uid 0 mapped to local |
| 11 | No fake state | PASS | Mute display flips only on RemoteMuted/RemoteUnmuted reasons; provisional uids marked from real media presence |
| 12 | Join security | PASS | Outsider `forbidden`; never-invited member `not_participant`; ended call `call_final`; token uid = authenticated user id (forge impossible); duplicate/multi-device join = same row, joined_at preserved |
| 13 | Push: minimal payload + dedupe | PASS | ids-only metadata; dedupe key `incoming-call:{public_id}:{recipient_id}`; invite reuses the same notify path |
| 14 | Call history | PASS | One `communication_calls` row per session by construction (invite inserts participants only) |
| 15 | One engine / one mic owner | PASS | Exactly 1 `createAgoraRtcEngine` site (callSessionStore); no new `setAudioModeAsync` site; expo-av allowlist unchanged |
| 16 | 1:1 regression | PASS | `test_call_two_sided_hangup` 6/6, `test_call_acceptance_sync` 10/10, jest calls 10 suites/80 tests |
| 17 | Audio protection batteries | PASS | critical 191/191; architecture (native) 22/22; architecture (backend) 19/19; token protection 13/13; tsc exit 0; i18n OK |
| 18 | Full realtime-audio suite | PASS* | 309/310 — the 1 failure is `src/live/liveSession.test.ts`, caused by **foreign uncommitted** live-mission changes; this diff has zero `src/live` lines |
| 19 | Livestream untouched | PASS | No `src/live`, LiveKit, or live-broadcast line in commit `6645171c` |
| 20 | Protected-path governance | PASS | Gate (base HEAD~1): protected files detected, declaration (2026-09-04 addendum) **accepted** |
| 21 | Push to origin | BLOCKED (env) | No repo credentials in this sandbox; `main` is ahead 2 (my `6645171c` + foreign `65c3f7c1` live commit). Owner must push. |
| 22 | Physical 3-device audio matrix | BLOCKED (owner) | REQUIRED before enabling the flag: A↔B, A↔C, B↔C must all be audible; C leaves → A↔B continues; creator leaves → B↔C continues. A grid rendering three people is NOT evidence they can hear each other. No PASS from simulators. |

## Rollout order

1. Owner pushes `main` (carries foreign live commit `65c3f7c1` too — review it first or cherry-pick `6645171c`).
2. Deploy server. Flag stays OFF — zero user-visible change.
3. Physical 3-device QA (item 22) with the flag ON for test accounts.
4. Enable `PULSE_GROUP_CALLS_ENABLED` broadly. Rollback = flip the flag off
   (instant, no deploy).

## Not staged (foreign, left untouched in the working tree)

`bot.py`, `pulse_communications_v2/service.py`, `mobile-native/src/api/messenger*.ts`,
`src/screens/ChatScreen.tsx`, `src/screens/CameraStudioScreen.tsx`, all of
`src/live/*`, i18n catalog edits (live-guest strings), and other missions'
untracked files. The engine.py commit contains ONLY the multi-guest hunks —
the two live-token hunks and the `live_participants` import were excluded and
remain uncommitted for the live mission.
