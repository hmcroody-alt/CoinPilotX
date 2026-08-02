# PulseSoc Permanent Livestream Audio Route — Consolidated Report

**Date:** 1 August 2026
**Scope:** LiveKit audio route for PulseSoc livestreams (host publish, viewer playback, guest/co-host publish, multi-guest, route changes, reconnects, cleanup)
**Verdict:** **PARTIAL** — see Section 19.

---

## 1. Executive summary

PulseSoc's livestream audio had been patched seven times without holding. This work stopped patching and rebuilt the route around four root causes plus one security defect, all of which are now closed in code, covered by tests, and gated behind a server-authoritative feature flag that defaults OFF.

The central finding is that the livestream audio failures were not a LiveKit configuration problem. They were three structural problems in PulseSoc's own code: a microphone publish helper that raced itself, a global audio-session owner with last-writer-wins semantics, and a broadcast lifecycle with no recovery policy at all. A fourth problem — viewers being minted tokens that let them rewrite their own LiveKit metadata, and therefore impersonate the host in every client that renders role from metadata — was found during the backend audit and is a privilege-escalation defect independent of audio.

Everything shipped in this change is additive and reversible. The legacy code path is byte-for-byte unchanged and still runs by default. The new path executes only when the backend says so, on a per-broadcast basis, with no client-side override.

What this report cannot claim: no build was produced, no physical device was exercised, and nothing was deployed. That is the entire reason the verdict is PARTIAL rather than PASS, and Section 19 is specific about what would close the gap.

---

## 2. Method

Investigation preceded implementation. The order was: trace the audio path end to end from the Go Live button to the AVAudioSession; audit backend room and token authority; map every feature that touches the shared audio session; read the prior audit scripts and the git history of the seven failed patches; run the existing suites to establish a green baseline; only then write code.

Where a claim could be tested rather than asserted, it was tested. The duplicate-publication root cause in Section 3 is not an inference from reading the code — the old helper was lifted into a proof harness and run against a simulated slow publish, and it produced the duplicate. That harness is checked in and runs in CI.

Because `bot.py` is a ~110,500-line Flask monolith whose import has side effects, backend functions were lifted out by Python `ast` and executed in an isolated namespace with stubs. This is how a pure-function contract test can exist for a monolith without importing it.

---

## 3. Root cause 1 — the publish helper raced itself

`ensureMicrophonePublished` polled on a 150 ms timer and, when it did not see a published track fast enough, toggled the microphone off and back on. Against any publish that took longer than the poll interval — which is normal on a cold radio or a congested network — the helper interrupted its own in-flight publish and started a second one.

Proof, from `mobile-native/__proof__/oldHelperDuplicate.test.ts`, run against a simulated 400 ms publish:

| Helper | `setMicrophoneEnabled` call sequence | Publications left for one speaker |
|---|---|---|
| Old `ensureMicrophonePublished` | `[true, false, true]` | **2** |
| New `publishLiveMicrophone` | `[true]` | **1** |

The live "go live" sequence made this worse. It published, called `setCameraEnabled`, published again, then slept 150 ms. Each publish call could run its own enable/toggle cycle, so a single tap could drive up to four cycles and leave duplicate audio publications on the room — which is what viewers heard as echo, doubling, and robotic audio.

**Fix.** `mobile-native/src/live/liveAudioPublisher.ts` (156 lines) replaces polling with event-driven publishing. It awaits `LocalTrackPublished` rather than guessing, holds a per-room mutex in a `WeakMap` so two callers cannot publish concurrently, reconciles any duplicate publication that does appear, and reports a structured outcome (`published` / `already` / `timeout`) with a duplicate count and a duration. The old helper is untouched and still serves the legacy path.

---

## 4. Root cause 2 — audio ownership was last-writer-wins

`claimRealtimeAudioSession` in `mobile-native/src/core/realtimeAudioEngine.ts` assigned the module-global owner unconditionally. Whoever claimed last won. Three consequences followed directly:

A livestream starting during an active call silently took the audio session, so the call kept running with no audio. The displaced feature was never told it had lost the session, so it continued believing it held one. And re-activation was not idempotent — `startAudioSession` was called again on every reacquire, producing unbalanced start/stop pairs that leaked the microphone indicator and left the route stuck after the feature exited.

**Fix.** `mobile-native/src/core/audioOwnershipPolicy.ts` (109 lines) is a pure, dependency-free arbitration module. It resolves a claim into one of four outcomes — `granted`, `reacquired`, `displaced`, `denied` — using an explicit priority ordering, and it is unit-tested in isolation. `realtimeAudioEngine.ts` now delegates to it. A losing claim throws `RealtimeAudioOwnershipError` instead of stealing the session. A displaced incumbent is invoked through an `onDisplaced` callback so it can stop its own media. Reacquisition preserves the original `startedAt` and skips the redundant `startAudioSession`.

**A separate leak, fixed in the same area.** The livestream `disconnect` path released the session with `ownerId || reason` — passing a *reason string* as the owner id when no owner id was present. A reason string can never match a real owner, so the release silently no-opped and the AVAudioSession leaked, blocking the next call or broadcast. The release is now guarded on a real `ownerId`.

---

## 5. Root cause 3 — no token refresh, and why raising the TTL was the wrong fix

LiveKit reuses the **original** join token on reconnect. A broadcast that outlives its token therefore cannot recover from a network drop, because the reconnect presents an expired credential.

The obvious fix — raise the guest TTL — was investigated and **rejected as a security regression**. Guest publish tokens are short (30 minutes) precisely so that a co-host who has been removed from the stage cannot rejoin on a still-valid publish token. Raising the TTL widens exactly that window.

**Fix.** Keep the TTL short and refresh in place. `mobile-native/src/live/liveAudioRecovery.ts` (162 lines) owns the policy: `millisecondsUntilRefresh` schedules a refresh five minutes before expiry. `useLiveBroadcastRoom` performs it through an injected `refreshCredentials` fetcher, and both publishing screens now supply one — `LiveHostSessionScreen.tsx` for hosts and `LiveScreen.tsx` for co-hosts. The refresh re-hits `/api/pulse/live/<id>/livekit/token`, which re-runs the server-side authority check, so a removed guest simply receives no new token. The security property is preserved *because* the refresh goes back through the server.

Refresh failures retry on a fixed 45-second interval, bounded to four consecutive attempts and only while the room still exists, so a transient blip recovers inside the five-minute margin without turning a revoked slot into a request loop. The failure body is never logged — it can contain the token.

---

## 6. Root cause 4 — no route-change, interruption, or disconnect policy

Bluetooth headphones powering off is the canonical failure: iOS moves output to the receiver and PulseSoc never reapplied the speaker route, so the broadcast went silent with no error. Separately, every disconnect was treated identically, so the client would retry a terminal condition (host ended the stream, guest removed, token expired) forever, and a genuinely recoverable drop got no structured retry at all.

**A constraint worth recording.** `@livekit/react-native`'s `AudioSession` exposes **no** route-change or interruption listener. Its surface is `configureAudio`, `startAudioSession`, `stopAudioSession`, `setDefaultRemoteAudioTrackVolume`, `getAudioOutputs`, `selectAudioOutput`, `showAudioRoutePicker`, and `setAppleAudioConfiguration` — and nothing else. Writing a new native module for `AVAudioSession` notifications would have required a native rebuild, which the brief's additive constraint rules out.

**Fix.** Route changes are surfaced through the events that *are* available: LiveKit's `MediaDevicesChanged`, `ActiveDeviceChanged`, and `AudioPlaybackStatusChanged`, plus React Native's `AppState` transition back to `active` as the portable substitute for an interruption-ended notification. `shouldReapplyAudioRoute` decides when to reassert the route. `classifyDisconnect` separates terminal from recoverable reasons, and only recoverable drops are retried, under `shouldAttemptReconnect` with `nextReconnectDelayMs` bounded exponential backoff. A terminal reason is an authorization or lifecycle decision that retrying cannot reverse.

---

## 7. Security defect — viewer-to-host impersonation

This was found during the backend audit and is **not** an audio bug.

Every LiveKit token, including subscribe-only viewer tokens, was minted with `canPublishData: true` and `canUpdateOwnMetadata: true`. PulseSoc clients render a participant's role from its LiveKit metadata. A viewer able to rewrite its own metadata could therefore set `role: "host"` and appear as the broadcaster to everyone in the room.

**Fix, applied at four layers in `bot.py`:**

The minter, `pulse_livekit_access_token`, now takes `can_publish_data` and `can_update_own_metadata` which default to the publish grant rather than to `True`. The endpoint derives both from `can_publish`, so a viewer gets neither. The verifier, `pulse_livekit_verify_token_claims`, checks the grants against what was *requested* rather than hard-asserting `True` — meaning it now asserts the *absence* of those grants on a viewer token, which is what stops a future change from silently re-widening viewer permissions. The JSON response echoes the actual grants.

An inconsistency introduced by an earlier pass — the verifier hard-asserting `canPublishData is True` while the minter no longer granted it, which would have returned HTTP 503 `TOKEN_CLAIMS_INVALID` to every viewer — was caught and fixed before any regression test ran.

Backend authorization was otherwise already sound: `can_publish` is derived from database state, and a client that asks for `role: "host"` without host authority receives 403.

---

## 8. Architecture decision — Option B

**Option B, shared transport with isolated ownership.** Both the call path and the livestream path already use LiveKit consistently and correctly at the transport layer. The damage was concentrated in the shared ownership global and in the livestream publish helper. Building a second, isolated transport (Option A) would have duplicated a working layer while leaving the actual defects in place, and would have doubled the surface that could regress calls.

Isolation is therefore enforced at the **ownership and lifecycle** layer, not the transport layer, which is where the failures actually lived.

---

## 9. Blast-radius containment

Calls mint their tokens through an entirely separate function — `services/pulsesoc_communications_engine.py::_generate_livekit_token` — which the livestream backend edits do not touch. The separation is structural, not conventional: a livestream token change **cannot** reach the call path.

On the client, `useV2Ref` gates every new read. When the server flag is off, the legacy branch is byte-identical to what shipped before, including the 150 ms sleep and the double publish. No shared app-startup audio configuration was modified, in line with the brief's prohibition on broad global audio-session changes.

---

## 10. Feature flag and kill switch

The rollout decision is made **server-side** and delivered on the LiveKit token response the client already fetches for every broadcast. There is no new endpoint and no client-side override — deliberately, because a client-side flag is not a kill switch.

| Environment variable | Default | Behaviour |
|---|---|---|
| `LIVESTREAM_AUDIO_V2_ENABLED` | unset (OFF) | Master switch. Anything not in `{1, true, yes, on}` disables V2. |
| `LIVESTREAM_AUDIO_V2_QA_ONLY` | unset (OFF) | When on, only QA accounts receive V2, regardless of percentage. |
| `LIVESTREAM_AUDIO_V2_PERCENT` | `0` | Sticky per-user rollout, 0–100, bucketed by SHA-256 of a salted user id. |
| `LIVESTREAM_AUDIO_V2_FALLBACK_ENABLED` | `true` | Whether the client may drop to the legacy path if V2 fails at runtime. |

The master switch genuinely dominates: a test asserts that with `ENABLED` off and `PERCENT=100`, every user runs legacy. Bucketing is sticky, so a given account does not flip paths between broadcasts. Anonymous or unknown users never receive V2.

On the client, `normalizeLiveAudioV2Flag` accepts **only** an explicit boolean `true`. A missing field, `"true"` as a string, `1`, `"1"`, `0`, `null`, and `{}` all resolve to `false`, so an older backend that does not send the field runs the legacy path.

---

## 11. Telemetry, and why it cannot leak a token

`mobile-native/src/live/liveAudioTelemetry.ts` (164 lines) is structured and privacy-safe by construction rather than by convention. Callers cannot hand it a free-form object that gets spread into a log line; they hand it a typed event, and every string field passes through `redact`, which strips JWT-shaped values, `Bearer` headers, opaque blobs of 40+ characters, and any `wss://` or `https://` URL — endpoints can carry credentials in the query string. Fields are capped at 120 characters so one event cannot flood the log.

Room and participant identifiers are hashed with FNV-1a to a 7-character base36 value, never emitted raw, so two events from one broadcast can be correlated in aggregate without the log naming the user or the room.

Two tests exist specifically to hold this: a token smuggled into `reason`, `detail`, *and* `outcome` simultaneously does not survive serialization, and the raw room name never reaches the event. `path` defaults to `"v1_legacy"`, so an unlabelled event is never miscredited to V2. Non-finite numbers are dropped rather than emitted as `NaN`. A throwing sink is swallowed — telemetry must never break a live broadcast.

Eighteen event names cover session claim/deny/displace/release, publish start/settle/timeout, duplicate reconciliation, route reapply, interruption begin/end, disconnect classification, reconnect schedule/exhaustion, token refresh schedule/success/failure, path selection, and fallback to legacy.

---

## 12. Changes shipped

**New files (1,746 lines including tests):**

| File | Lines | Purpose |
|---|---|---|
| `mobile-native/src/core/audioOwnershipPolicy.ts` | 109 | Pure ownership arbitration |
| `mobile-native/src/live/liveAudioPublisher.ts` | 156 | Event-driven, mutexed mic publish |
| `mobile-native/src/live/liveAudioRecovery.ts` | 162 | Disconnect classification, backoff, refresh timing |
| `mobile-native/src/live/liveAudioTelemetry.ts` | 164 | Redacting structured telemetry |
| `mobile-native/src/live/liveAudioFlags.ts` | 36 | Strict client-side flag normalisation |
| `tests/protection/test_livestream_audio_token_grants.py` | 315 | Backend grant + rollout contract |
| Five new native test suites + proof harness | 804 | See Section 13 |

**Modified files (+726 / −45):** `bot.py` (+109), `useLiveBroadcastRoom.ts` (+488), `realtimeAudioEngine.ts` (+97/−9), `liveSession.ts` (+15), `LiveHostSessionScreen.tsx` (+11), `LiveScreen.tsx` (+10), and two test fixtures.

---

## 13. Test evidence

All figures below are from runs performed in this session.

| Suite | Result |
|---|---|
| Full native Jest suite | **111 suites, 1,877 tests — all passing** |
| `npx tsc --noEmit` | **Clean, exit 0** |
| `tests/protection/test_livestream_contract.py` | Pass |
| `tests/protection/test_livestream_audio_token_grants.py` | Pass — 37 assertions |
| `tests/protection/test_core_platform_contract.py` | Pass |
| `tests/protection/test_media_playback_contract.py` | Pass |
| New audio suites (6 files, incl. proof harness) | 70 tests, all passing |
| `bot.py` syntax parse | OK |

Two failures were found and fixed during the work rather than papered over: a rollout-distribution assertion whose bounds were arithmetically wrong (50% of 199 is ≈100, not ≈50 — corrected to 70–130), and two test labels that interpolated a stale default message and therefore printed a misleading string on success.

---

## 14. Protected-feature regression matrix

The brief named fifteen features that must not regress. Verification below is by **static and test evidence only** — no device was exercised. This is the matrix's central limitation and it is stated plainly.

| # | Feature | Evidence | Status |
|---|---|---|---|
| 1 | 1:1 audio calls | Separate token function; `callAudioOwnershipRegression.test.ts` green | Verified by test |
| 2 | Video calls | Same call path; ownership policy unit-tested | Verified by test |
| 3 | Group calls | Same call path; unchanged | Verified by inspection |
| 4 | Voice messages | Untouched; media playback contract green | Verified by test |
| 5 | Reels audio | `reelMediaKind` + media playback contract green | Verified by test |
| 6 | Feed video audio | `mediaPlaybackCoordinator.test.ts` green | Verified by test |
| 7 | Status audio | Untouched | Verified by inspection |
| 8 | Music previews | `createPayloadMusic.test.ts` green | Verified by test |
| 9 | Attached music | Untouched | Verified by inspection |
| 10 | PulseSoc Radio | `pulseRadioQueueOrder.test.ts` green | Verified by test |
| 11 | Notification sounds | Untouched | Verified by inspection |
| 12 | Camera recording | Camera profile contract green | Verified by test |
| 13 | Media playback | `test_media_playback_contract.py` green | Verified by test |
| 14 | Livestream host publish | New path unit-tested; **not device-verified** | **Requires device QA** |
| 15 | Livestream viewer playback | New path unit-tested; **not device-verified** | **Requires device QA** |

Rows 14 and 15 are the two that matter most and the two that automated evidence cannot close.

---

## 15. Acceptance gates

Of the fourteen minimum gates in the brief, twelve are met by code and test evidence: root causes identified and fixed rather than patched; architecture decision justified by repository evidence; server-authoritative authorization preserved; least-privilege grants enforced and asserted; no secret exposure anywhere; ownership coordinator with priority, idempotent cleanup, and stale-ownership detection; feature-flagged with default OFF; server-authoritative kill switch; structured privacy-safe telemetry; backend and native tests; full regression green; typecheck clean.

Two gates are **not met**: physical-device validation on iOS and Android hardware, and staged production deployment with observed metrics. Both require a build and a deploy, neither of which was performed.

---

## 16. Deployment plan

Seven stages, each with an explicit stop condition. Nothing below has been executed.

1. **Merge with the flag off.** `LIVESTREAM_AUDIO_V2_ENABLED` unset. Every user runs legacy. Confirm no change in live-audio error rates over 24 hours.
2. **QA-only.** Set `ENABLED=true` and `QA_ONLY=true`. Run the device matrix in Section 17 on internal accounts.
3. **1%.** `QA_ONLY=false`, `PERCENT=1`. Watch `live_audio_publish_timeout`, `live_audio_duplicate_reconciled`, and `live_audio_reconnect_exhausted` for 48 hours.
4. **10%.** Hold 72 hours. Duplicate reconciliation should trend toward zero; a rising count means the publisher mutex is being bypassed somewhere.
5. **50%.** Hold one week. Compare call-audio error rates across the two buckets — the sticky bucketing makes this a clean A/B.
6. **100%.** `PERCENT=100`. Keep `FALLBACK_ENABLED=true`.
7. **Remove the legacy path.** Only after 100% has been stable for two weeks. Delete `ensureMicrophonePublished`, the 150 ms sleep, and the `useV2` branches.

**Rollback at any stage:** set `LIVESTREAM_AUDIO_V2_ENABLED` to `false`. It takes effect on the next token fetch — no app release, no client action, no deploy.

---

## 17. Device QA checklist

Per platform (iOS and Android), per role (host, viewer, co-host):

Go live and confirm exactly one audio publication. Have a viewer confirm no echo or doubling. Power off Bluetooth headphones mid-broadcast and confirm audio continues on the speaker. Take an incoming phone call mid-broadcast and confirm the broadcast stops cleanly rather than continuing silently. Background and foreground the app. Force a network drop and confirm reconnect. Run a broadcast past the token TTL — over 30 minutes as a co-host, over 2 hours as a host — and force a drop after expiry to confirm the refresh worked. End the broadcast and immediately start a 1:1 call to confirm the audio session was released. Have the host remove a co-host and confirm the co-host cannot rejoin.

---

## 18. Known gaps and follow-ups

`mobile-native/__proof__/oldHelperDuplicate.test.ts` still lives outside `src/` because this session lacked permission to move it. It runs in CI and its header documents that relocating it to `src/live/__tests__/` is safe.

Route-change detection is inferred from LiveKit device events and `AppState` rather than from native `AVAudioSession` notifications, because the RN SDK does not expose them. This is a real fidelity limit: a route change that produces no device event and no app-state transition will not be detected. A native module would close it, at the cost of a native rebuild.

The viewer path in `ReelLiveViewerSurface.tsx` does not supply a `refreshCredentials` fetcher. Viewer tokens carry a 1-hour TTL and viewers do not publish, so the exposure is a long-lived passive viewer failing to reconnect after expiry. Worth adding, but lower priority than the publishing paths, which are done.

---

## 19. Verdict — PARTIAL

**PARTIAL**, and the reason is narrow and specific.

Everything that can be established without a build and a device has been established. Four root causes and one privilege-escalation defect are fixed at the source rather than patched around them. 111 suites and 1,877 native tests pass, the typecheck is clean, all four Python protection contracts pass, and the duplicate-publication root cause is demonstrated by an executable proof rather than asserted. The route is reversible by a single environment variable and observable through telemetry that cannot leak a credential.

What is missing is the part that no amount of static analysis substitutes for: **no physical device ran this code, and nothing was deployed.** Livestream audio is a hardware-and-radio problem as much as a software one. Bluetooth handoff, call interruption, and route restoration behave differently on real hardware than in any test double, and rows 14 and 15 of the regression matrix — the two rows that matter most — are exactly the rows automated evidence cannot close.

Calling this PASS would mean asserting device behaviour that was never observed. The honest position is that the code is ready for stage 2 of Section 16, and the verdict should be revisited after the Section 17 checklist has been run on real hardware.

No production deployment and no Railway configuration change was made, consistent with the operating agreement for this work.
