# Mission B — Agora Live Viewer P0 — Recovery Manifest
Date: 2026-09-04 · Branch at last check: main (post a7490add) · Sandbox shell DOWN all session (`useradd: input/output error`), so no `git diff` patch could be produced. This manifest + the file copy below are the recovery artifacts.

## Root cause (confirmed by code evidence)
Audience path in `mobile-native/src/live/useAgoraLiveBroadcastRoom.ts` never called `engine.enableVideo()` — it lived inside the `if (publishingVideoRef.current)` publisher gate. In Agora 4.x that call enables the video MODULE (required to DECODE remote video). Viewers joined (`channel_joined` fired), subscribed (`autoSubscribeVideo: true`), but `onFirstRemoteVideoDecoded` could never fire → `hasVideo` stayed false → "Waiting for host media" → timeout → Web fallback. Classification: REMOTE_VIDEO_NOT_SUBSCRIBED (module disabled on subscriber).

## Mission-owned changes (stage ONLY these)

### 1. useAgoraLiveBroadcastRoom.ts — ⚠ file also carries FOREIGN pre-existing dirty hunks; use `git add -p`
Exactly two mission hunks in `connect()`:
- **Hunk A (~line 229-238):** after `engine.enableAudio();` on the `createAgoraRtcEngine()` line, inserted an 8-line comment ("The video MODULE is not the camera…") + `engine.enableVideo();` — unconditional, before the publisher gate.
- **Hunk B (~line 268):** removed the old `engine.enableVideo();` from inside `if (publishingVideoRef.current) {` — that block now starts with the Stage 24 comment, then `await applyPublisherEncoder(1); engine.enableDualStreamMode(true); engine.startPreview();`.
Nothing else in this file is mission work. A verbatim post-fix copy is saved next to this manifest as `useAgoraLiveBroadcastRoom.POST_FIX_COPY.ts` (preserves foreign hunks too — do not `git checkout` the real file without consulting it).

### 2. liveStreamQuality.ts — whole diff is mission-owned
- `LivePublishPlan`: new field `enableVideoModule: boolean` (with doc comment) before `enableVideo`.
- `AUDIENCE_PLAN`: `enableVideoModule: true` (with comment "The one thing an audience member DOES initialise: the decode path.").
- `resolvePublishPlan` broadcaster return: `enableVideoModule: true,` (with comment "Every client decodes remote video; publishers additionally capture.").
- `planTouchesCaptureHardware` unchanged — deliberately ignores the module field.

### 3. __tests__/liveStreamQuality.test.ts — mission hunk only
New test inserted in the Stage 25 describe, before "returns a fresh object each time": `it("enables the video MODULE for every client, audience included", …)` — asserts `enableVideoModule === true` for all roles × authorized/unauthorized, and audience plan still has `planTouchesCaptureHardware === false`.

### 4. __tests__/viewerRemoteVideoModule.test.ts — NEW file, entirely mission-owned
Source-level guard (fs.readFileSync — hook can't run under jest, dynamic `import("react-native-agora")` not transpiled). Asserts: `engine.enableVideo()` in the unconditional setup segment before the publisher gate; `engine.startPreview()` only inside the gate; gate has no `enableVideo()`; join options retain `publishCameraTrack: Boolean(localTrack)`, `autoSubscribeVideo: true`, `autoSubscribeAudio: true`. Fails against pre-fix source.

## Landing steps for the fresh session (shell required)
1. `git diff > /tmp/agora-viewer-video-module-recovery.patch` (Stage 1, now possible).
2. `cd mobile-native && npx jest src/live/__tests__/liveStreamQuality.test.ts src/live/__tests__/viewerRemoteVideoModule.test.ts --silent`
3. `npx tsc --noEmit`
4. Affected suites: liveAudioMatrix, liveParticipantRegistry, liveSeatReconciliation, liveEchoControlWiring, liveStreamQuality + Agora token/role/viewer suites under src/live/__tests__/.
5. Protection: `python3 scripts/realtime_audio_change_gate.py --base origin/main --head HEAD` (hook is a protected path — expect protected-path handling; declaration: Agora only; audience now enables video module for remote decode; NO camera capture/publish, mic, engine-ownership, or joinChannel-owner changes; new owners = 0).
6. Stage: `git add mobile-native/src/live/liveStreamQuality.ts mobile-native/src/live/__tests__/liveStreamQuality.test.ts mobile-native/src/live/__tests__/viewerRemoteVideoModule.test.ts` then `git add -p mobile-native/src/live/useAgoraLiveBroadcastRoom.ts` (only Hunks A+B). Remove stale `.git/index.lock`/`HEAD.lock` if present.
7. Commit (separate, exact message): `fix(live): enable Agora video module for audience playback`
8. Post-commit gate `--base HEAD~1 --head HEAD`. Do NOT stage this recovery folder.

## Invariants the fix must keep (verified true in current working tree)
- Audience: enableVideoModule=true, publishCameraTrack=false, publishMicrophoneTrack=false, no startPreview, no encoder config, no camera permission for viewing.
- MULTI_GUEST default OFF; single-host contract untouched.
- New Agora engine/mic/camera/joinChannel owners: 0.
