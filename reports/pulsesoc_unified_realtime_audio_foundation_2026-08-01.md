# PulseSoc Unified Real-Time Audio Foundation

Date: 2026-08-01

Branch: `codex/unified-realtime-audio-foundation`

Starting SHA: `b76e8721568d6b65108b0638a10592c9e3296b33`

Foundation implementation SHA: `c49897e50a97d06c4db513abcec282e99935c53c`

Physical-failure diagnostic fix: `8bfb34a8368659359577374a1437f04ca7faab0a`

Current deployed/native artifact SHA: `5e8f7989391cd150d858f5a9c193b1084a943ef9`

Overall verdict: **PARTIAL / NO-GO for controlled rollout**

This mission established the governed code foundation and passed automated validation. It did not produce the required two-physical-device audible evidence for calls, video calls, or Live. Installation and process launch on one paired iPhone are not classified as physical audio verification.

## 1. Shared root cause

The three real-time surfaces had related lifecycle defects but did not share one complete, enforceable platform contract:

- Calls and Live did not use the same authoritative microphone publication mechanism.
- Call publication relied on legacy enable-and-inspect behavior while Live had a stronger event-driven publisher in a feature-owned file.
- Audio-session cleanup was owner-name scoped rather than acquisition-generation scoped. A delayed cleanup could release a newer session with the same semantic owner, and a Live cleanup fallback could pass a reason string that never matched the owner.
- State transitions existed implicitly across hooks and callbacks, making terminal reconnects, recovery, and cleanup difficult to constrain.
- Call grants did not consistently expose server-authoritative room type, participant role, publish sources, and rollout state to the native client.
- Shared privacy-safe telemetry did not cover ownership, publication, subscription, routing, and cleanup consistently.

## 2. Surface-specific defects

### Audio calls

- The call hook directly controlled the local participant microphone instead of using the shared publication gateway.
- Publication/republication did not use the event-driven, duplicate-reconciling path.
- Cleanup retained only an owner string, so it could not distinguish an old session instance from a newer acquisition.
- Reconnect, local media, and remote media states were not governed by explicit transition rules.

### Video calls

- Video used the same call lifecycle, but camera changes could race microphone publication.
- The backend video token could publish without an explicit source list; the new grant is limited to microphone and camera.
- Camera enable now independently re-verifies that exactly one microphone publication remains.

### Livestream

- The stronger Live V2 microphone publisher was isolated in the Live feature rather than owned by the shared real-time platform.
- Legacy Live publishing can perform repeated enable/publish cycles. The V2 path is retained behind flags and now delegates to a serialized, event-driven shared publisher.
- A cleanup fallback could use the disconnect reason as the owner ID, silently leaking the active audio session.
- A viewer now receives an explicit forbidden publication outcome and never activates the microphone path.

## 3. Previous and new architecture

Previous:

```text
call hook -> feature mic toggles/polling -> LiveKit participant
Live hook -> Live-only V2 publisher or legacy path -> LiveKit participant
both hooks -> shared audio configuration with owner-name cleanup
backend -> call token without a complete native policy contract
```

New:

```text
call / video / Live lifecycle
        |
        +-> realtimeAudioEngine
        |      - one audio-session owner
        |      - priority arbitration
        |      - generation-scoped lease
        |      - canonical iOS route/configuration
        |
        +-> realtimeMicrophonePublisher
        |      - one in-flight publish operation per room
        |      - event-driven publication verification
        |      - duplicate reconciliation
        |      - viewer/permission denial
        |
        +-> realtimeAudioStateMachine
        |      - room/local/remote transitions
        |      - terminal reconnect protection
        |
        +-> realtimeAudioTelemetry
               - typed, redacted operational events

backend call service
        -> authenticated participant role
        -> explicit room type
        -> least-privilege publish sources
        -> server-authoritative V2/fallback flags
```

## 4. Ownership, iOS session, and LiveKit policies

- A single `RealtimeAudioOwner` includes `ownerId`, monotonic `leaseId`, mode, start time, and whether the mode publishes a microphone.
- Every acquisition rotates the lease, including reacquisition by the same semantic owner.
- Normal cleanup requires the matching typed lease. Stale cleanup returns `false` and cannot remove the current displacement callback or stop the current session.
- The existing priority policy remains authoritative. Higher-priority acquisition displaces through the registered callback; lower-priority acquisition fails explicitly.
- Calls, video calls, Live host/guest, and voice message modes use the canonical iOS `playAndRecord` / `videoChat` profile through the shared engine. A listen-only Live viewer now uses the playback profile without acquiring microphone ownership.
- Feature hooks no longer activate/deactivate the global audio session, select an iOS route, or manipulate the participant microphone directly.
- The microphone publisher uses a per-room mutex, waits for `localTrackPublished`, reconciles duplicates to one track, and returns explicit outcomes.
- Remote subscriptions remain provider/event driven; lifecycle state records publication, subscription, playing, interruption, recovery, and terminal end.

## 5. Backend room and token policy

The call service now derives policy from authenticated server state:

- Room types: `audio_call`, `video_call`.
- Participant roles: `caller`, `callee`, or normalized `member`.
- Audio-call publish sources: microphone only.
- Video-call publish sources: microphone and camera only.
- Subscribe and publish capabilities are returned in the join contract.
- Identity and metadata contain the authenticated user identity, room type, and server-resolved role.
- Native code does not infer higher privilege from client input.

Existing Live server authorization remains intact. The existing viewer-grant protection suite continues to verify that viewers cannot publish.

## 6. State machines, recovery, and cleanup

Three explicit state domains were added:

- Room: idle, authorizing, connecting, connected, reconnecting, disconnecting, disconnected, failed.
- Local audio: idle, requesting permission, acquiring session, creating track, publishing, published, muted, recovering, unpublishing, released, failed.
- Remote audio: waiting, publication available, subscribing, subscribed, playing, interrupted, recovering, ended, failed.

Impossible transitions raise `RealtimeAudioTransitionError`. Terminal state blocks Live reconnect scheduling. Reconnect republishes through the idempotent shared publisher and reapplies the audio route. Cleanup marks terminal, disconnects the room, releases only the matching lease, and settles local/remote/room state.

## 7. Feature flags and rollback

Defaults are deliberately off:

- `REALTIME_AUDIO_PLATFORM_V2_ENABLED=false`
- `REALTIME_AUDIO_CALLS_V2_ENABLED=false`
- `REALTIME_VIDEO_CALLS_V2_ENABLED=false`
- `REALTIME_AUDIO_V2_FALLBACK_ENABLED=true`
- `LIVESTREAM_AUDIO_V2_ENABLED=false`
- `LIVESTREAM_AUDIO_V2_QA_ONLY=true`
- `LIVESTREAM_AUDIO_V2_PERCENT=0`
- `LIVESTREAM_AUDIO_V2_FALLBACK_ENABLED=true`

Rollback requires no code reversal: disable the platform/feature V2 flags and retain the enabled fallback. The replacement must not be globally enabled until physical and mixed-session gates pass.

The original foundation mission changed no Railway variables. The 2026-08-02 diagnostic follow-up deployed the focused fix and enabled Live V2 plus privacy-safe trace mode only for the two approved QA user IDs. `LIVESTREAM_AUDIO_V2_QA_ONLY=true` and `LIVESTREAM_AUDIO_V2_PERCENT=0` keep the replacement path disabled for the normal production audience.

## 8. Observability and security

The shared telemetry contract covers ownership requests/acquisition/rejection, session activation, microphone publish start/success/failure, and cleanup start/completion. It carries correlation ID, hashed session identifier, room type, role, outcome, bounded counts, duration, and failure category. Sanitization removes bearer tokens, JWT-like values, URLs, and long opaque values. Message content and credentials are not part of the event contract.

Security results:

- Least-privilege call source grants are tested.
- Viewer microphone publication fails before microphone activation.
- Architectural tests reject direct global audio-session and unmanaged microphone mutations outside the core.
- `.env.example` contains defaults/placeholders only; no secret value was added.

## 9. Files changed

Implementation commit `c49897e50a97d06c4db513abcec282e99935c53c`:

- `.env.example`
- `mobile-native/src/api/calls.ts`
- `mobile-native/src/calls/__tests__/useNativeCallRoomAudio.test.ts`
- `mobile-native/src/calls/useNativeCallRoom.ts`
- `mobile-native/src/core/__tests__/realtimeAudioEngine.test.ts`
- `mobile-native/src/core/__tests__/realtimeAudioStateMachine.test.ts`
- `mobile-native/src/core/__tests__/realtimeAudioTelemetry.test.ts`
- `mobile-native/src/core/realtimeAudioEngine.ts`
- `mobile-native/src/core/realtimeAudioStateMachine.ts`
- `mobile-native/src/core/realtimeAudioTelemetry.ts`
- `mobile-native/src/core/realtimeMicrophonePublisher.ts`
- `mobile-native/src/live/__tests__/liveAudioPublisher.test.ts`
- `mobile-native/src/live/liveAudioPublisher.ts`
- `mobile-native/src/live/useLiveBroadcastRoom.ts`
- `services/pulsesoc_communications_engine.py`
- `tests/protection/test_call_livekit_token_grants.py`
- `tests/protection/test_realtime_audio_architecture.py`

Evidence additions:

- `reports/pulsesoc_unified_realtime_audio_foundation_2026-08-01.md`
- `reports/evidence/realtime-audio-2026-08-01/simulator-home-c49897e.png`

## 10. Automated validation

All checks below ran against the implementation source at `c49897e50a97d06c4db513abcec282e99935c53c`:

| Check | Result |
|---|---:|
| `git diff --check` | PASS |
| Changed Python compilation | PASS |
| Call LiveKit token-grant contract | 4/4 PASS |
| Real-time audio architecture protection | 3/3 PASS |
| Existing Live viewer-token contract | 37 checks PASS |
| Native TypeScript typecheck | PASS |
| Focused native suites | 9 suites / 85 tests PASS |
| Full native suite | 113 suites / 1,887 tests PASS |

The full suite had no failures. Third-party compiler warnings remain, including deprecated React Native/Expo APIs and an old CocoaPods deployment target; none became a build error.

## 11. Simulator evidence

- Xcode: 26.6 (`17F113`).
- Simulator: iPhone 17 Pro Max.
- iOS runtime: 26.5.
- UUID: `E859950D-B187-4897-B389-05447C5AD796`.
- Configuration: Release, arm64, ad-hoc simulator signed.
- Build: PASS.
- Install: PASS.
- Launch: PASS.
- App: PulseSoc `1.0.1 (9)`.
- Embedded SHA: `c49897e50a97d06c4db513abcec282e99935c53c`.
- Visibly observed: authenticated PulseSoc home screen rendered after launch.
- Evidence: `reports/evidence/realtime-audio-2026-08-01/simulator-home-c49897e.png` (SHA-256 `e6d5601b272ab4d5a781b51d5a842487f27dd3737be2aea62320a580183e0ce8`).

The first unsigned simulator installation displayed the app's startup recovery screen and logged missing keychain entitlement `-34018`. The same source was rebuilt with simulator signing, freshly installed, launched, and visibly rendered. This was a build/install configuration issue, not counted as an audio pass.

No simulator microphone-to-remote audible verification was claimed. A simulator cannot satisfy the physical-device acceptance gates.

## 12. Physical iPhone evidence

- Device: paired physical iPhone 16 Pro, name `P3r7or`.
- CoreDevice ID: `F45E640F-6D02-514E-877C-B764E8D6818F`.
- Configuration: Release, Apple Development signed.
- Code-sign verification: PASS.
- Signing team: `87ZC69AGSR`.
- Provisioning profile: `iOS Team Provisioning Profile: com.pulsesoc.app`.
- Install: PASS; CoreDevice reported bundle `com.pulsesoc.app` installed.
- Launch command: PASS.
- Process observation: `PulseSocNative` running as PID `16533` after launch.
- App: PulseSoc `1.0.1 (9)`.
- Embedded artifact SHA: `c49897e50a97d06c4db513abcec282e99935c53c`.

This is **installation/process evidence only**. The device screen and audio output were not remotely observable through CoreDevice. No second physical authenticated participant was available. Therefore the following were not physically verified:

- bidirectional audio call;
- video-call audio before/after camera operations;
- Live host-to-viewer audio;
- guest-to-viewer audio;
- Bluetooth, speaker, receiver, wired route transitions;
- interruption/recovery;
- call-after-Live, Live-after-call, or five mixed-session cycles.

## 13. Acceptance-gate matrix

| Gate | Status | Evidence / blocker |
|---|---|---|
| 1. One session coordinator | PASS | Core engine plus architecture test |
| 2. One microphone owner model | PASS | Owner/lease model plus architecture test |
| 3. Stale cleanup protection | PASS | Generation-scoped lease regression |
| 4. Duplicate publication prevention | PASS (automated) | Mutex, event wait, reconciliation tests |
| 5. Least-privilege backend tokens | PASS | Source-grant contract tests |
| 6-8. Physical audio-call media/recovery/cleanup | BLOCKED | No second physical participant or audible observation |
| 9-11. Physical video-call audio/camera/cleanup | BLOCKED | No second physical participant or audible observation |
| 12-13. Live host/guest reaches viewers | BLOCKED | No physical host/viewer/guest session |
| 14. Unauthorized viewer cannot publish | PASS (automated) | Existing 37-check token suite plus client denial |
| 15. Live termination cleanup | PASS (automated), physical BLOCKED | Lease/terminal tests; no physical session |
| 16-20. Cross-feature and protected-media matrix | BLOCKED | Requires live mixed-session hardware run |
| 21. Flags and kill switches | PASS (contract) | Defaults off, fallback on, existing Live controls retained |
| 22. Reversible production deployment | NOT RUN | No production deployment |
| 23. Structured telemetry | PASS (automated) | Typed/sanitization tests |
| 24. No credentials/unrestricted grants | PASS | Secret-free diff and grant contracts |
| 25. Complete evidence | FAIL | Physical audible and deployment evidence missing |

## 14. Remaining risks and required closure

1. Run an authenticated caller/callee pair on two physical devices and verify audible speech in both directions.
2. Repeat for video, including camera toggle/switch without audio loss.
3. Run physical Live host, approved guest, and viewer roles; prove viewer publication denial and audible host/guest tracks.
4. Exercise speaker, receiver, Bluetooth, wired output, interruption, background/foreground, lock/unlock, and network handoff.
5. Run call-after-Live, Live-after-call, video-after-audio/Live, protected media regressions, and five mixed cycles without restart.
6. Deploy backend source with replacement flags disabled, confirm health/rollback, then enable only for controlled QA accounts.
7. Capture privacy-safe telemetry and correlated server/client evidence for each live path.

## 15. Final judgment

**IMPLEMENTED:** yes, for the governed foundation and call/video/Live integrations described above.

**TESTED:** automated suites, architecture contracts, simulator build/install/visible launch, and signed single-device install/process launch.

**DEPLOYED:** no production deployment.

**PHYSICALLY VERIFIED:** signed installation only; no real-time audio behavior verified.

Final status: **PARTIAL / NO-GO**. The code foundation is suitable for controlled QA activation after deployment with flags off, but it cannot be promoted or described as complete until all physical audible and mixed-session gates pass.

## 16. Physical Live failure diagnostic — 2026-08-02

### Required failure entry

**Physical Live test: FAIL**

Observed behavior: Host entered Live, but no Live audio was audibly verified.

Rollout impact: Live V2 remains disabled outside the approved QA allowlist. Production rollout remains **NO-GO**.

### Failed-gate determination

The prior physical run did not include a separate, observable physical viewer energy trace, so it cannot truthfully be reduced to one proven A-E media gate. The evidence narrows the failure but does not prove the audible cause:

| Gate | Evidence from the failed run | Status |
|---|---|---|
| H1-H3 permission/input/session | The host entered the room; no ordered physical client trace existed in that build | Unproven |
| H4 local track | The native client repeatedly reported publication through `/native-publish` with HTTP 200 | Client-claimed only |
| H5 provider/server publication | Every provider webhook request to `/api/livekit/webhook` returned HTTP 403 | **Confirmed broken observability/state ingestion** |
| H6 local energy | No raw audio or QA-safe level was recorded | Unproven |
| V1 room/participant | Host and viewer requested credentials for the same Live IDs (`160`, `161`, and `162`) | Partial |
| V2-V3 discovery/subscription | No viewer-side ordered trace existed | Unproven |
| V4 playback | The listen-only viewer was configured with the microphone-oriented `playAndRecord` / `videoChat` profile | **Confirmed configuration defect** |
| V5 remote energy/audibility | No remote energy trace and no audible output | Failed/unproven cause |

The exact audible failure gate therefore remains **unisolated**. Two concrete defects were repaired, but neither is presented as the sole physical root cause without the required post-fix two-device evidence.

### Root cause and focused repair

1. `pulse_communications_v2/routes.py` registered a legacy HMAC webhook at the canonical `/api/livekit/webhook` path before `bot.py` registered the LiveKit SDK-authenticated handler. Flask route order allowed the legacy handler to receive provider callbacks and reject valid LiveKit `Authorization` JWTs with HTTP 403. The legacy adapter now owns `/api/pulse/communications/v2/livekit/webhook`; the canonical provider path has one owner.
2. `live_viewer` incorrectly activated the mic-oriented audio-session category/mode. The viewer now uses iOS playback/default configuration, does not request microphone ownership, and records the current output route. Host/guest capture remains on the capture-and-playback profile.
3. A QA-only trace records the ordered host, viewer, and cleanup event contract, including hashed session/room/participant identifiers, role, state, lease owner, SID metadata, mute/enable/subscription state, route, and error category. Token-like values are redacted. Audio energy is quantized; raw audio is neither stored nor uploaded.
4. Server flags now require both the Live V2 master flag and an explicit QA user allowlist. Trace mode independently requires an approved user. A boolean from the authenticated token response enables the client trace; the client cannot self-authorize it.

The regression suite asserts that a Live host keeps capture-and-playback behavior, a viewer uses playback without microphone ownership, the viewer cannot publish but can subscribe, trace data is redacted, and the canonical LiveKit webhook is not shadowed. Existing lease-generation and publication tests continue to cover stale cleanup, duplicate reconciliation, session end, and second-session publication behavior.

### Trace timeline

Pre-fix production evidence, 2026-08-02 UTC:

1. Physical iPhone host (`PulseSocNative/9`, iOS 18.x user agent) requested credentials and entered Live IDs `160`, `161`, and `162`.
2. The simulator viewer (`PulseSocNative/9`, iOS simulator user agent) requested credentials for the same rooms shortly afterward.
3. Host `/native-publish` calls returned HTTP 200.
4. LiveKit provider callbacks to `/api/livekit/webhook` returned HTTP 403, preventing authoritative backend publication-state ingestion.
5. The old client had no QA energy/route/subscription timeline, so H6 and V2-V5 could not be reconstructed after the session.

Post-fix ordered trace capture is **not yet available** because no second physical authenticated viewer was available to execute the required session. The new trace code is installed and QA-authorized, but code presence is not reported as runtime trace evidence.

### V2/fallback collision review

- The QA token contract returns V2 only for explicitly allowlisted users; all other users remain on legacy behavior.
- A V2 session uses the shared lease and publication gateway; fallback cannot release a different generation.
- Tests enforce one viewer playback lease with no microphone ownership and preserve the existing one-track duplicate reconciliation.
- No runtime collision was observed post-fix because the required session was not run. Dual activation remains an invariant to confirm from the first complete QA trace.

### Files changed

Focused fix commit `8bfb34a8368659359577374a1437f04ca7faab0a`:

- `bot.py`
- `mobile-native/src/core/realtimeAudioEngine.ts`
- `mobile-native/src/core/__tests__/realtimeAudioEngine.test.ts`
- `mobile-native/src/live/liveAudioTrace.ts`
- `mobile-native/src/live/liveSession.ts`
- `mobile-native/src/live/useLiveBroadcastRoom.ts`
- `mobile-native/src/live/__tests__/cohostPublishGate.test.ts`
- `mobile-native/src/live/__tests__/liveAudioConfiguration.test.ts`
- `mobile-native/src/live/__tests__/liveAudioTrace.test.ts`
- `mobile-native/src/live/__tests__/liveSession.test.ts`
- `pulse_communications_v2/routes.py`
- `reports/pulsesoc_communications_engine_foundation.md`
- `scripts/pulsesoc_communications_engine_audit.py`
- `tests/protection/test_livekit_webhook_route_owner.py`
- `tests/protection/test_livestream_audio_token_grants.py`

Deployment packaging commit `5e8f7989391cd150d858f5a9c193b1084a943ef9` adds `.railwayignore` so the backend source upload excludes native build products, reports, tests, runtime media, and other non-runtime artifacts.

### Validation and deployment

| Check | Result |
|---|---:|
| Focused native Live/audio rerun | 6 suites / 55 tests PASS |
| Full native suite | 114 suites / 1,893 tests PASS |
| Native TypeScript typecheck | PASS |
| Live token and QA allowlist protection | PASS |
| Canonical LiveKit webhook owner regression | PASS |
| LiveKit webhook audit | PASS |
| Communications engine audit | 55/55 PASS |
| Affected Python compilation | PASS |
| Railway source deployment | SUCCESS |
| Production health (`database_ok`, service role) | PASS |

Railway service `CoinPilotX`, environment `production`, active diagnostic deployment `f2e63797-8189-411f-8c32-ecc19ba7d4b1`, image digest `sha256:576c7882fd8e8a46a12ff21eec090868aeb952c462033b96fd9d3a94f4af8165`. Its source deployment message identifies `5e8f7989`. A variable-triggered deployment of stale `main` was detected and superseded; Railway marks it removed. Normal users remain outside the allowlist.

### Current native build evidence

- Xcode: 26.6 (`17F113`).
- Simulator: iPhone 17 Pro Max, iOS 26.5, UUID `E859950D-B187-4897-B389-05447C5AD796`.
- Simulator Release build/install/launch: PASS.
- Simulator visible state: authenticated PulseSoc home rendered.
- Physical host device: paired iPhone 16 Pro `P3r7or`, CoreDevice ID `F45E640F-6D02-514E-877C-B764E8D6818F`, iOS 18.7.3.
- Physical Release build/sign/install/launch: PASS; running PID `16810`.
- Bundle: `com.pulsesoc.app`; version/build: `1.0.1 (9)`; application identifier: `87ZC69AGSR.com.pulsesoc.app`.
- Embedded Git SHA on both artifacts: `5e8f7989391cd150d858f5a9c193b1084a943ef9`.
- Simulator evidence: `reports/evidence/live-audio-2026-08-01/simulator-current-build.png` (SHA-256 `9be34eeea03e7bce64ed04ac9bcac81ac05bb2cb476712496734da3f8864da75`) and `simulator-relaunch.png` (SHA-256 `140fcc895deeb96c32ebff795cc6f7f931c4bc71ed5e515a892d91905b4b4d7e`).
- A custom URL attempt was intercepted by the embedded Expo development launcher; `simulator-live-screen.png` records that limitation and is not Live-screen evidence.

### Required physical result matrix

| Result | Status |
|---|---|
| Build installed | PASS |
| Host device | iPhone 16 Pro `P3r7or` |
| Viewer device | **Unavailable: no second paired physical participant** |
| Viewer audibly hears host for five minutes | NOT RUN |
| Mute/unmute | NOT RUN |
| Viewer leave/rejoin | NOT RUN |
| Cleanup/ownership release | Automated PASS; physical NOT RUN |
| Second Live without restart | Automated PASS; physical NOT RUN |
| Call after Live | NOT RUN |

### Diagnostic final judgment

**Code repair: PARTIAL. Physical acceptance: NO-GO.**

The focused defects are fixed, tested, deployed for QA only, and installed on the physical host. Live audio remains failed for release purposes until a distinct physical viewer audibly hears the host, H6/V5 show expected energy, the five-minute/mute/rejoin/cleanup/second-session sequence passes, and an audio call succeeds after Live ends.

## 17. Video-call and livestream audio recovery — 2026-08-02

### Starting physical evidence and protected baseline

The owner-reported physical baseline for this recovery was:

- audio call: bidirectionally audible (**PASS and protected**);
- video call: connected with video, but audio inaudible (**FAIL**);
- livestream: connected, but host/guest audio inaudible to the viewer (**FAIL**).

The production LiveKit event stream corroborated the media topology without being treated as audible proof. The working audio-call room `pulsesoc-call_3jCMdRU1NWHa0g` showed both participants publishing microphone tracks. Failing video rooms `pulsesoc-call_RF44pwNiCCcwIg` and `pulsesoc-call_kKPIp9CruLQt_g` also showed both microphone publications, and failing Live rooms `pulse-webrtc-e2d2836438684a28` and `pulse-webrtc-51822d2e743246b3` showed the host microphone/camera publications and the viewer in the same room. This ruled out a blanket token or room-join failure and moved the investigation to the native capture/playout lifecycle.

The decisive client trace was the iOS RemoteIO lifecycle. The working audio call kept RemoteIO running until call cleanup. In the failing video call, RemoteIO started at `23:38:57.850` and stopped at `23:38:58.320`, about 470 ms after camera startup, although LiveKit publication state remained present. The static sequence matched the trace: the video path started the camera before establishing the microphone, whereas the protected audio-only path established the microphone immediately.

### Runtime comparison

| Property | Audio call | Video call before repair | Live host before repair | Live viewer before repair |
|---|---|---|---|---|
| Audio owner | `audio_call` | `video_call` | `live_host` | `live_viewer` / playback |
| AVAudioSession category | `playAndRecord` | `playAndRecord` | `playAndRecord` | `playback` |
| AVAudioSession mode | `videoChat` | `videoChat` | `videoChat` | `default` |
| Category options | Bluetooth, A2DP, AirPlay, speaker | same | same | none |
| Auto-subscribe | true | true | true | true |
| Local microphone publication | two-party runtime evidence | present, but engine stopped after camera startup | present | not permitted |
| Remote subscription | working runtime result | publication present; playout inaudible | n/a as publisher | room/subscription path present; playout inaudible |
| RemoteIO state | stable until cleanup | stopped about 470 ms after camera startup | local energy not captured | remained running in the failed run |
| Audible result before repair | **PASS** | **FAIL** | **FAIL** | **FAIL** |

These are observed runtime values where traces existed. Live host local energy and viewer remote energy were not captured in the pre-repair build and remain explicitly unproven.

### Focused repair

Commit `f157d83d` (`fix(video-call-audio): restore microphone and playback lifecycle`) changes only the video-specific path plus the shared engine guard:

1. Video calls now establish the microphone before starting the camera.
2. Camera start/switch/toggle reasserts the existing microphone publication instead of creating a second track.
3. A bounded iOS audio-engine guard verifies and restores recording/playout after the camera's delayed audio-session transition and fails the initial video connection closed if the native module explicitly remains inactive.
4. Remote audio subscription and reconnect events reassert playout and speaker routing.
5. The protected audio-only initialization path remains microphone-only and does not invoke the new camera guard.

Commit `4779303f` (`fix(live-audio): restore publisher capture and viewer playout`) applies the same verified lifecycle at the Live feature adapter:

1. Host/approved-guest stabilization reasserts exactly one existing microphone publication and restores capture plus playout.
2. Viewer stabilization restores playout only and never enables recording or microphone ownership.
3. The guard runs after initial camera startup, reconnect, foreground recovery, remote subscription, camera toggle, and camera switch.
4. The existing V2 publication gateway, role grant, duplicate reconciliation, and QA allowlist remain authoritative.

No broad backend, token, room, or working audio-call adapter rewrite was made.

### Regression evidence

| Check | Result |
|---|---:|
| New video mic-before-camera regression | PASS |
| Protected audio-only initialization regression | PASS |
| Video camera-removes-publication recovery | PASS |
| Engine restart and fail-closed verification | PASS |
| No duplicate microphone reassertion | PASS |
| Live publisher capture + one microphone | PASS |
| Live viewer playout-only / no recording | PASS |
| Focused native suites | 4 suites / 35 tests PASS |
| Full native suite | 114 suites / 1,901 tests PASS |
| Native TypeScript typecheck | PASS |
| Call/LiveKit token and architecture tests | 7 tests PASS |
| Livestream grant, QA allowlist, and tamper tests | PASS |
| Canonical LiveKit webhook owner test | PASS |
| Native call audit | PASS |
| Live guest audio repair audit | PASS |
| `git diff --check` | PASS |

The repository Python environment does not provide `pytest`; affected backend suites were executed through their supported `unittest` or direct script entrypoints.

### Source and release state

- Starting SHA: `ba452fc6c335435422bee688a6e7ea9b91e20bb3`.
- Video repair: `f157d83d`.
- Live repair: `4779303feb31e1bb0d1125a7674d3fed873cba87`.
- Branch: `codex/unified-realtime-audio-foundation`.
- Remote verification: local and `origin/codex/unified-realtime-audio-foundation` both resolved to `4779303feb31e1bb0d1125a7674d3fed873cba87` before this evidence update.
- Xcode: 26.6 (`17F113`).
- Simulator: iPhone 17 Pro Max, iOS 26.5, UUID `E859950D-B187-4897-B389-05447C5AD796`.
- Simulator app: fresh signed Debug build, `com.pulsesoc.nativeapp.dev`, version/build `1.0.1 (9)`, embedded SHA `4779303feb31e1bb0d1125a7674d3fed873cba87`.
- Simulator launch/auth shell: PASS; fresh install correctly reached the PulseSoc login surface. Real-time media QA was not run because no controlled QA credential/session was available after the fresh install.
- Physical target: paired iPhone 16 Pro `P3r7or`, iOS 18.7.3, CoreDevice ID `F45E640F-6D02-514E-877C-B764E8D6818F`.
- Physical Release artifact: production bundle `com.pulsesoc.app`, version/build `1.0.1 (9)`, embedded SHA `4779303feb31e1bb0d1125a7674d3fed873cba87`, application identifier `87ZC69AGSR.com.pulsesoc.app`.
- Signing verification: valid Apple Development signature, designated requirement satisfied, provisioning profile valid through 2027-08-02.
- Physical install/launch: PASS at 2026-08-02 00:27 PDT; CoreDevice confirmed installation and launched PID `17161`. The existing bundle was updated in place to preserve its app data/session.

### Post-repair acceptance status

| Gate | Status |
|---|---|
| Audio-call baseline preserved by code/tests | PASS |
| Audio-call post-repair physical regression | NOT YET OBSERVED |
| Video-call bidirectional audible audio | NOT YET OBSERVED |
| Camera switch preserves audible audio | NOT YET OBSERVED |
| Video disable preserves audible audio | NOT YET OBSERVED |
| Live viewer audibly hears host | NOT YET OBSERVED |
| Live viewer audibly hears approved guest | NOT YET OBSERVED |
| Mixed audio call -> video -> Live -> audio call | NOT YET OBSERVED |

### Recovery judgment

**Implementation and automated validation: PASS. Physical audible acceptance: NO-GO pending observation.**

The confirmed video lifecycle divergence is repaired and Live capture/playout is now guarded at every feature transition. Neither telemetry nor a successful build/install substitutes for hearing both participants and the Live viewer on physical devices. Video and Live V2 must remain under independent QA controls until the required two-participant physical matrix passes and the protected audio call is heard again afterward.
