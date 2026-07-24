# PulseSoc Native — Production Livestream Audio Repair

**Date:** 2026-07-23
**Severity:** P0 (release blocker)
**Reported build:** PulseSoc 1.0.1 (2), bundle `com.pulsesoc.app`, commit `a64989fe133dc60d4d16e80f7108c36a4ac9103f`
**Symptom:** When a user starts a livestream, host video works and viewers see the
host, but **viewers cannot hear any host audio**.

> This repair does **not** rely on the earlier "Live repair" or the 1:1 calls
> fix. The production build proved livestream audio was still broken; the root
> cause below is a distinct, viewer-side audio-session defect.

---

## 1. What was proven, stage by stage

The full audio path is: host mic → iOS audio session → local LiveKit audio track
→ room publication → server forwarding → viewer subscription → remote playback →
speaker/Bluetooth. Each stage was traced against the actual code.

| Stage | Finding | Verdict |
| --- | --- | --- |
| Host token (backend) | `api_pulse_live_livekit_token` (bot.py:40405). Host/publisher → `can_publish=True`; `pulse_livekit_access_token` grants `canPublish:True` with **no `canPublishSources` restriction** → host may publish the microphone. | ✅ Correct |
| Viewer token (backend) | Viewer → `can_publish=False` but `roomJoin:True` + `canSubscribe:True`. Viewers can subscribe to host audio. | ✅ Correct |
| Host mic publication (client) | `useLiveBroadcastRoom.connect()` publishes mic+camera and then **hard-guards**: if no published audio track after connect it disconnects with `LIVE_LOCAL_AUDIO_NOT_PUBLISHED`. A host literally cannot go live without a published audio track. | ✅ Correct |
| Viewer subscription (client) | `room.connect(..., { autoSubscribe: true })` — viewers auto-subscribe to remote tracks. | ✅ Correct |
| Feed mute default | `ReelsScreen` initializes `muted = useState(false)`; not muting viewers by default. | ✅ Correct |
| **Viewer iOS audio session** | **Viewers reused the publisher's session.** `connect()` unconditionally set `playAndRecord` / `videoChat` for *every* participant, including listen-only viewers. | ❌ **ROOT CAUSE** |

## 2. Root cause

`useLiveBroadcastRoom.connect()` configured a single iOS `AVAudioSession`
profile — `playAndRecord` + `videoChat` — for **both** the host and viewers,
regardless of whether the participant publishes.

`playAndRecord` requires microphone permission. A normal viewer never grants mic
permission (they have no reason to). On a real device, activating a
`playAndRecord` session without a mic grant can **fail to activate the session at
all**. When that happens, the subscribed host audio track has no active output
route and plays silently — while video keeps rendering because the camera is
independent of the audio session. This precisely matches the production symptom
("see the host, can't hear the host") and why it only reproduces on a real
viewer device: the developer's host device already granted mic permission during
host testing, masking the defect in dev.

`videoChat` is additionally the wrong *mode* for a pure consumer — it is a
2-way communication mode that applies AGC/ducking, whereas a listen-only viewer
wants full-volume media playback.

## 3. The fix

### 3.1 Split the audio session by role — `src/live/useLiveBroadcastRoom.ts`

New pure, exported helper `resolveLiveAudioConfiguration(publish)`:

- **Publisher (host / co-host):** `playAndRecord` / `videoChat`,
  options `allowBluetooth, allowBluetoothA2DP, allowAirPlay, defaultToSpeaker`.
  (Unchanged behavior — the host must record.)
- **Listen-only viewer:** `playback` / `moviePlayback`,
  options `allowBluetooth, allowBluetoothA2DP, allowAirPlay`
  (`defaultToSpeaker` is only valid with `playAndRecord`, so it is omitted;
  `playback` already routes to the speaker). **No microphone dependency**, so the
  session always activates and subscribed host audio always has an output route.

`connect()` now selects the profile via this helper *before* `startAudioSession()`.

### 3.2 True viewer mute — `src/components/reels/ReelLiveViewerSurface.tsx`

The viewer surface previously reacted to the mute toggle with
`setSpeakerEnabled(!muted)`, which only re-routes output (and is a no-op under
the new `playback` category). It now calls `setRemoteAudioEnabled(!muted)`:

- **Unmuted (default):** explicitly enables the subscribed host audio track(s) —
  a belt-and-suspenders guarantee that host audio plays.
- **Muted:** genuinely disables the remote track rather than routing to earpiece.

The effect also re-runs on `room.remoteAudioTrackCount` changes, so a late-arriving
host audio track is enabled the moment it is subscribed.

### 3.3 Production QA diagnostic — `useLiveBroadcastRoom.ts`

The `PulseSoc Live media connected` console log now includes the chosen
`audioProfile` (e.g. `playback/moviePlayback` for viewers,
`playAndRecord/videoChat` for hosts) and the `remoteAudioTrackCount` observed at
connect — so the active audio session and subscription state are visible in a
production log capture without a debugger.

## 4. Regression tests

`src/live/__tests__/liveAudioConfiguration.test.ts` (new) locks the contract:

- publisher → `playAndRecord` + `videoChat`, includes `defaultToSpeaker`;
- viewer → `playback` + `moviePlayback`, **never** `playAndRecord`, **omits**
  `defaultToSpeaker`;
- both roles allow Bluetooth/AirPlay outputs.

## 5. Validation performed

| Check | Result |
| --- | --- |
| `npm run typecheck` (tsc --noEmit) | ✅ clean |
| `npm test -- --runInBand` | ✅ 40 suites / 381 tests pass (incl. 3 new) |
| `git diff --check` | ✅ clean |
| `expo-doctor` | 16/17; the 1 failure is a pre-existing prebuild/app-config-sync warning, unrelated to this change |

## 6. Device build

Detached Release `xcodebuild` → `xcrun devicectl install` to physical iPhone
**P3r7or** (F45E640F-6D02-514E-877C-B764E8D6818F), per the documented Xcode-26
workaround. Status recorded at commit time.

## 7. NOT OBSERVED (out of my capability — hand-off to user)

These require hardware/accounts I cannot drive and must be completed by the user
to fully close the P0:

1. **Two-device hearing test (§11).** With the new build on P3r7or:
   - Device A: start a Live and speak.
   - Device B (a *second* device where the app has **never been granted mic
     permission** — the exact failure condition): open the Live in the feed.
   - **Expected:** Device B now hears the host at full speaker volume. Confirm
     the console log on B shows `audioProfile: "playback/moviePlayback"` and
     `remoteAudioTrackCount >= 1`.
   - Also verify: toggling the feed mute button silences/restores host audio,
     and Bluetooth/AirPlay routing works.
2. **TestFlight / App Store upload (§12).** I cannot upload builds. Archive the
   Release scheme in Xcode and distribute to TestFlight from your account, or run
   `eas build --platform ios` and submit.

## 8. Files changed

- `mobile-native/src/live/useLiveBroadcastRoom.ts` — role-based audio session,
  diagnostic log.
- `mobile-native/src/components/reels/ReelLiveViewerSurface.tsx` — true remote
  mute/enable for viewers.
- `mobile-native/src/live/__tests__/liveAudioConfiguration.test.ts` — new
  regression suite.
