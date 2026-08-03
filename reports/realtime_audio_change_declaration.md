# Real-Time Audio Change Declaration

Change: Emergency PulseSoc Live audio regression recovery  
Base: `c5e523d625166414573e618c1c043092794e7163`  
Baseline: `realtime-audio-stable-v1` (`fc25cd163b8802113df1b3b3d98cb7aab10891bb`)  
Required label: `audio-critical-change`

## Why the change is required

Physical Live startup failed with `The native real-time audio engine did not remain active.` The stable rollback was already present, but the failed run lacked generation- and caller-complete evidence, and the release-blocking suite omitted the post-camera engine lifecycle test. The guard must remain fail-closed while the transition that invalidates the engine becomes observable and regression-tested.

## Which feature required it

Emergency Live host/co-host audio recovery. No Marketplace, Advertising, Premium, Crypto, feed, or unrelated UI files are included.

## Which protected files changed

| File | Category | Change |
|---|---|---|
| `mobile-native/src/live/useLiveBroadcastRoom.ts` | livestream audio adapter | Emits ordered policy/owner/generation/camera/publication/verification events and stop-capable lifecycle events; snapshots flags and policy once per session. |
| `mobile-native/src/live/liveAudioTrace.ts` | audio telemetry | Adds required event names and privacy-safe correlation, generation, owner, room, screen, profile, flag, caller, reason, and timestamp fields. |
| `mobile-native/src/live/__tests__/liveAudioConfiguration.test.ts` | critical tests | Reproduces camera startup followed by native-engine loss and proves the guard fails closed. |
| `mobile-native/package.json` | dependency watch | Adds Live configuration and trace suites to the critical command; no dependency version changed. |
| `config/realtime-audio-protected-paths.json` | audio governance | Declares the required startup trace schema and critical lifecycle suite. |
| `tests/protection/test_realtime_audio_architecture.py` | critical tests | Enforces the trace contract and critical-suite inclusion. |

## Expected behavior change

Live publishers remain on the stable profile unless the server explicitly enables `live_publisher_quality_enabled`. Audio ownership, microphone publication, routing, cleanup, and the inactive-engine guard are unchanged. When QA tracing is enabled, a failed startup now identifies the active generation and initiating module instead of reporting only the terminal guard error.

## Regression risk

The runtime change is diagnostic around an existing sequence. It resolves the pure quality plan before AVAudioSession activation, but still constructs the Room only after ownership is acquired. Trace sinks remain non-throwing and carry hashed identifiers. Residual risk is physical iOS camera/RemoteIO behavior, which automated tests cannot retire.

## Tests run

- Focused Jest: 4 suites / 112 tests passed.
- Critical audio Jest: 15 suites / 295 tests passed.
- Python architecture: 15 tests passed.
- TypeScript: passed.
- Change gate: rerun after this declaration.
- Native build and two-device audible QA: not run.

## Physical validation required

Install the corrected build on a physical iPhone; run two consecutive five-minute Live sessions without app restart; confirm a separate viewer hears the host both times; then confirm bidirectional audio call and video-call audio. Repeat stable and QA-only elite profiles. Automated or simulator results do not count as audible proof.

## Rollback procedure

Keep `REALTIME_MEDIA_QUALITY_V2_ENABLED=0` and `live_publisher_quality_enabled=false` server-side. If this recovery must be reverted, revert the three focused recovery commits together and retain `c5e523d6`, which defaults Live publishers to stable. Confirm rollback on two physical devices before rollout.
