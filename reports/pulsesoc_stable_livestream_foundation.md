# PulseSoc Stable Livestream Foundation

Date: 2026-08-02
Branch: `codex/emergency-live-audio-recovery`
Release judgment: **PARTIAL / production NO-GO**

## Foundation delivered

`mobile-native/src/live/liveRuntime.ts` is the module-scoped authority for a Live generation. It owns:

- Immutable session identity: correlation/session ID, broadcast ID, canonical room name, host ID, authorization version, generation, flag snapshot, quality snapshot, and creation time.
- Explicit top-level state transitions from `idle` through preparation, authorization, media acquisition, connection, publication, Live, recovery, and terminal cleanup.
- Governed audio, camera, and room substates.
- Event-derived readiness. A host cannot enter `live` without authorization, connected room, current audio/camera ownership, confirmed microphone/camera tracks and publications, a nonterminal generation, and no competing path.
- Typed internal errors.
- Deduplicated concurrent start commands.
- Idempotent generation-scoped cleanup and stale-cleanup rejection.
- A module-level resource registry that retains the current Room and audio lease across screen remounts.
- Privacy-bounded transition events with state, generation, room, role, flags, quality, caller, reason, error category, and timestamp.

The existing shared `realtimeAudioEngine.ts` remains the sole AVAudioSession owner. The runtime coordinates it; it does not create a second audio implementation.

## Integration

`useLiveBroadcastRoom.ts` now registers the canonical session before media acquisition, records authorization, attaches the audio lease and single room to the runtime, advances connection/publication states from actual completion events, and invokes the readiness gate only after both required media publications are confirmed.

Unmount is no longer equivalent to broadcast termination. Navigation cleanup preserves the runtime generation, room, and lease. Explicit host stop or terminal disconnect performs cleanup.

Live UI surfaces use commands:

- Host: `startBroadcast`, `stopBroadcast`
- Viewer/reel viewer: `joinAsViewer`, `leaveViewer`

Architecture enforcement rejects direct LiveKit room transport imports in screens/components and direct transport-shaped Live room commands in the three Live UI surfaces.

## Stable rollback

Host/co-host sessions default to `v1_legacy` publisher audio even when general Live-audio V2 is enabled. Publisher V2 requires the separate, strict server field `publisher_audio_v2_enabled=true`. Media-quality V2 remains independently gated. The inactive-engine guard is unchanged on the V2 path.

## Evidence and remaining gates

Automated checks validate state, readiness, idempotency, stale cleanup, remount survival, audio ownership, publication, backend grants, and architecture boundaries. They do not prove physical audio/video.

Still required for PASS:

- Corrected physical build installed.
- Five repeated host/viewer Live cycles with audible audio and visible video.
- Camera switching, natural field of view, background/foreground, routes, Bluetooth disconnect, and interruptions.
- Network reconnect and Wi-Fi/cellular transition.
- Approved guest join/publish/remove/republication denial.
- Calls, video calls, voice messages, and Reels before/after Live.
- One 30-minute host session and multi-guest session.
- Backend deployment ID, exact app build, LiveKit environment, remote flag state, and rollback exercise.

Until those pass, the stable foundation is implemented in code but not physically certified.
