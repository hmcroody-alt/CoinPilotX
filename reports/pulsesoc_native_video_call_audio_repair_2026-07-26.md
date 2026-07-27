# PulseSoc Native Video Call And Live Audio Repair

Date: 2026-07-26
Branch: `release/undx-nexus-core-v4`

## Release Status

`PulseSoc 1.0.1 (5) must not be submitted to App Store review until video-call audio and Live host audio are physically verified on real devices.`

No new TestFlight build was created or submitted during this repair mission.

## Root Cause

The native call room treated a successful LiveKit connection and visible video publications as sufficient proof that a call was healthy. The call hook only tracked local and remote video publications. It did not verify that:

- the local microphone publication actually existed after the video camera started,
- remote audio publications were subscribed and enabled,
- reconnect restored the microphone publication,
- camera toggle/flip preserved the microphone publication.

That allowed a video call to appear connected, with both cameras visible, while the audio chain could remain silent. Audio calls were less exposed because they did not start camera publication immediately after the microphone path.

A second shared-audio defect existed in real-time media ownership. `mediaPlaybackCoordinator` released active owners on app background except Pulse Radio, which meant a real call or Live room could lose ownership of the shared iOS audio session during background/inactive transitions. The full native Live WebRTC viewer path also released its playback owner instead of claiming a Live owner, and the in-feed Live viewer had no Live owner around the LiveKit room. That allowed Radio, Reels, media viewers, or other playback surfaces to reclaim or reconfigure the shared audio session during a healthy real-time room.

## Repair

- Added call-specific audio publication helpers to `mobile-native/src/calls/useNativeCallRoom.ts`.
- Counted verified local microphone publications and subscribed remote audio publications.
- Reapplied remote audio enablement when audio tracks subscribe.
- Reasserted microphone publication after reconnect.
- Verified microphone publication after camera startup.
- Prevented a connected call from reporting success if local microphone publication cannot be verified.
- Made mute, camera toggle, and camera flip verify the real media state rather than only updating React state.
- Preserved the existing LiveKit room, call API, CallKit bridge, call tone lifecycle, and working audio-call route.
- Added real-time background retention in `mediaPlaybackCoordinator` for `call`, `live`, `recording`, and existing `radio` owners.
- Made the full native Live WebRTC viewer claim a `live` playback owner while joined instead of releasing ownership.
- Made the in-feed Reels Live viewer claim a `live` playback owner while its LiveKit room is active.
- Preserved HLS playback ownership through the existing video ref path.

## Pipeline Comparison

| Concern | Working audio call reference | Broken video/Live path before repair | Repaired behavior |
| --- | --- | --- | --- |
| Microphone publication | Audio calls relied on LiveKit mic enablement. | Video calls could publish camera and report connected without verifying a mic publication. | Video calls now verify local audio publications after camera startup and after reconnect. |
| Remote audio subscription | Audio calls were not blocked by camera startup. | Remote audio tracks were not tracked, re-enabled, or counted as a health signal. | Remote audio publications are counted and audio tracks are re-enabled on subscribe/reconnect. |
| Camera toggle/flip | Not applicable to audio calls. | Camera operations could leave audio state unverified. | Camera toggle and flip refresh/verify mic and remote-audio state. |
| Live host audio | Host uses publisher audio session and mic publishing. | Shared coordinator could release Live ownership on background/inactive. | Live owners are retained across background/inactive coordinator events. |
| Live viewer audio | Viewer uses playback-only iOS audio configuration. | Full WebRTC viewer released ownership; in-feed Live had no ownership claim. | Full and in-feed WebRTC viewers claim `live` ownership while connected. |
| HLS fallback | Media playback owns the video player. | HLS was the only Live viewer path with explicit ownership. | HLS remains video-ref owned; WebRTC has separate real-time ownership. |
| Competing media | Higher-priority call/live should preempt ordinary playback. | Radio/Reels/media could reclaim ownership after background release or missing Live claim. | Coordinator priority plus retained call/live ownership prevents ordinary playback from stealing the session. |

## Media Lifecycle

Expected repaired path:

```text
permission
-> audio-session activation
-> room join
-> camera publication for video calls
-> microphone publication verification
-> remote audio subscription
-> remote audio enabled
-> speaker route selection
```

## Audio-Call Regression

The audio-call API and call screen flow were not redesigned. The shared call room now verifies microphone publication for both audio and video calls, but no separate audio-call pathway was replaced.

Regression coverage:

- focused call API and call lifecycle tests passed,
- tone lifecycle tests passed,
- CallKit bridge tests passed,
- full mobile-native Jest suite passed.
- shared media coordinator regression tests passed.
- Live remote-audio reapply and iOS audio-profile tests passed.

## Validation

- `npx tsc --noEmit`: passed.
- Focused shared call/Live media Jest: 4 suites passed, 18 tests passed.
- Focused call Jest: 5 suites passed, 39 tests passed.
- Full `npm test -- --runInBand`: 97 suites passed, 1675 tests passed.
- `npm run verify`: passed. TypeScript passed, i18n validation passed, Jest passed.
- `EXPO_DOCTOR_ENABLE_DIRECTORY_CHECK=0 npx expo-doctor --verbose`: 16/16 checks passed.
- `scripts/pulsesoc_call_system_full_functionality_audit.py`: passed.
- `scripts/pulsesoc_real_call_experience_audit.py`: passed.
- `scripts/pulsesoc_native_calls_audit.py`: passed.
- `scripts/pulsesoc_native_call_p0_behavior_audit.py`: passed.
- `scripts/pulsesoc_native_calls_qa_audit.py`: passed.
- `scripts/pulse_audio_calls_audit.py`: passed.
- `scripts/pulse_video_calls_audit.py`: passed.
- `scripts/audio_pipeline_audit.py`: failed on unrelated music-rights assertion: `direct uploaded music requires rights review`.

## Simulator And Physical QA

Simulator proof is limited to build/UI/code-path validation and cannot prove audible two-device media.

Physical two-device validation remains required:

- Device A hears Device B during video call.
- Device B hears Device A during video call.
- Device A -> Device B and Device B -> Device A both pass.
- Audio call after video repair still works.
- Bluetooth, background/foreground, reconnect, mute/unmute, camera off/on, camera flip, and end-call cleanup are verified on real devices.

## Final Judgment

PARTIAL — code is repaired and validated through automated/static checks, but complete two-device audible validation remains.
