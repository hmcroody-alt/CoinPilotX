# PulseSoc Unified Real-Time Audio Foundation

Date: 2026-08-01

Branch: `codex/unified-realtime-audio-foundation`

Starting SHA: `b76e8721568d6b65108b0638a10592c9e3296b33`

Implementation SHA: `c49897e50a97d06c4db513abcec282e99935c53c`
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
- Calls, video calls, Live host/guest/viewer, and voice message modes use the canonical iOS `playAndRecord` / `videoChat` profile through the shared engine. Playback retains the playback profile.
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

No Railway variables were changed and no backend deployment was performed in this mission. This branch is not evidence of production deployment.

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
