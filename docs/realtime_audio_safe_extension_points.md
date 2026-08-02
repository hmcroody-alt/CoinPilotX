# Safe extension points for real-time audio

You want to add something audio-related. This document tells you where to put it
so that CI does not stop you and production audio does not break.

The organizing idea is simple: **the device has exactly one audio session and
exactly one microphone.** Every allowed extension below passes a policy or an
argument to the module that already owns that resource. Every forbidden pattern
below creates a second owner. Ordering between two owners is not deterministic,
so the resulting failure is intermittent, device-specific, and invisible in
every test and every code review.

## Allowed

### A new output policy

**Where:** the route controller inside
`mobile-native/src/core/realtimeAudioEngine.ts`
(`selectRealtimeAudioOutput`, `showRealtimeAudioRoutePicker`).

**How:** add the policy as a mode or an option the coordinator understands, and
have your feature request it. The coordinator already knows the platform
differences — iOS uses `force_speaker` / `default`, Android uses `speaker` /
`earpiece` — and a caller that hardcodes one set breaks on the other platform.

**Not:** calling `overrideOutputAudioPort` from a screen.

### Route telemetry

**Where:** `mobile-native/src/core/realtimeAudioTelemetry.ts`,
`mobile-native/src/live/liveAudioTelemetry.ts`,
`mobile-native/src/live/liveAudioTrace.ts`.

**How:** add an event alongside the existing ones. These modules already carry
hashed identifiers and correlation IDs; keep to that shape. Emitting a raw user
identifier is a privacy regression, and emitting nothing removes the only signal
a rollback decision can be based on.

### Additional interruption recovery

**Where:** `mobile-native/src/live/liveAudioRecovery.ts`.

**How:** extend the disconnect classification. The bounded reconnect, the
backoff, the terminal-disconnect detection, and the token-refresh margin already
exist; add your case to the classifier rather than adding a second retry loop.
Two retry loops produce a session that looks alive and carries no audio.

### Improved noise suppression / echo cancellation

**Where:** the capture options applied by
`mobile-native/src/core/realtimeMicrophonePublisher.ts`.

**How:** as options on the single publication path, so every surface — call,
video call, host, guest — gets them consistently and the publisher's in-flight
serialization still applies.

### Additional participant mixing

**Where:** the remote subscription reconciliation in
`realtimeAudioEngine.ts#applyRemoteAudioEnabled`, and playback ownership in
`mobile-native/src/live/livePlaybackOwnership.ts`.

**How:** through the existing single reconciliation pass over remote tracks. It
already re-runs after reconnects; a subscription made outside it will be undone
by the next pass, which is heard as audio that works until the first network
blip.

## Forbidden

### Screen-level AVAudioSession setup

Any `setCategory`, `setMode`, `setActive`, or `overrideOutputAudioPort` outside
the coordinator. **Why it is fatal:** the session is global. A media screen that
sets `playback` while a call is running takes the microphone away from the call.
The call's own code is untouched and its tests still pass.

*Enforced by:* `realtime_audio_session_mutation` in the manifest.

### A new unmanaged microphone track

`createLocalAudioTrack`, `createLocalTracks`, `new LocalAudioTrack`, or a direct
`localParticipant.setMicrophoneEnabled` / `publishTrack`. **Why it is fatal:**
publication must be serialized per room and confirmed by LiveKit's
`localTrackPublished` event. A direct call races the publisher and produces two
microphone tracks — an echo or silence, depending on which one the server keeps.

*Enforced by:* `unmanaged_microphone_publication`.

### A new feature-specific LiveKit publication path

**Why it is fatal:** the publisher keeps an in-flight promise map keyed by room.
A second publisher cannot see it, so the serialization that prevents duplicates
stops working the moment two features are live at once.

### A new global audio singleton

**Why it is fatal:** two coordinators are worse than one imperfect coordinator,
because which one wins depends on mount order. The bug reproduces on the tester's
phone one time in five and never on yours.

### Bypassing ownership arbitration

Calling `resetRealtimeAudioOwnership` from a feature, or reintroducing the
`audioOwnerIdRef` name-scoped pattern. **Why it is fatal:**
`resetRealtimeAudioOwnership` skips generation checking, so a cleanup that fires
late releases a session a *newer* feature has since acquired. Release through
`releaseRealtimeAudioSession` with the lease you were given; the lease
generation is what makes a stale release a no-op.

*Enforced by:* `direct_realtime_cleanup` and `required_lease_discipline`.

### Copying the audio-call implementation into another screen

The most tempting and the most damaging. **Why it is fatal:** the copy does not
receive the next fix, and the two copies compete for the same session and the
same microphone. If a new surface needs call-like audio, it needs a mode in the
existing adapter — not a second adapter.

## The expo-av exception, and why it is frozen rather than fixed

Six files call `Audio.setAudioModeAsync`, which mutates the same
`AVAudioSession` the coordinator owns: `core/pulseRadio.ts`,
`core/reelsAudioSession.ts`, `core/voiceMessagePlayback.ts`,
`calls/callSignalMedia.ts`, `screens/MusicScreen.tsx`, `screens/ChatScreen.tsx`.

They were already doing this at the verified baseline, and the baseline is
audible-working. Rewriting them would change runtime behavior that is currently
verified — which this hard-lock is explicitly not permitted to do. So the six
are frozen as an enumerated legacy allowlist with a hard cap of six. A seventh
call site fails CI.

New media playback must route through one of the six, not add a seventh.

## Before you open the pull request

```bash
python3 scripts/realtime_audio_change_gate.py --base origin/main --head HEAD
```

If it says no protected path changed, you found a safe extension point and you
are done. If it names files, you are inside the boundary: read
`docs/realtime_audio_change_policy.md`, fill in
`reports/realtime_audio_change_declaration.md`, and plan for physical
validation — because the tests can show the invariants still hold, and they
cannot show that a person still hears sound.
