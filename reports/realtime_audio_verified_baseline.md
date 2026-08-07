# PulseSoc Real-Time Audio — Verified Baseline

**Baseline ID:** `realtime-audio-stable-v1`
**Recorded:** 2026-08-02
**Status:** VERIFIED WORKING — protected platform baseline

This document records the exact known-good state of PulseSoc real-time audio. It is the
reference every later change is measured against, and the target of the rollback
procedure in `docs/realtime_audio_release_checklist.md`.

Every field below is either **repository-evidenced** (derived from git, the build
artifacts, or a committed report, and reproducible by anyone with the repository) or
**owner-attested** (reported by the device owner who physically performed the test and
not independently reproducible from the repository). The distinction is marked on every
claim. Fields that were not recorded at the time of the test are marked
**NOT RECORDED** rather than filled with a plausible value.

---

## 1. Code identity

| Field | Value | Source |
| --- | --- | --- |
| Branch | `codex/store-dashboard-live` | repository-evidenced |
| Local HEAD commit | `ce03e160eaf4649a8e02bc3b609a3182ca9d3859` | repository-evidenced |
| Remote SHA (`origin/codex/store-dashboard-live`) | `ce03e160eaf4649a8e02bc3b609a3182ca9d3859` | repository-evidenced — local and remote are identical |
| Last commit to touch any real-time audio path | `b252a255e675c1b3e065e602ef225adc3c31779a` — *fix(live-audio): reinitialize recording after camera teardown* (2026-08-02 00:55:44 -0700) | repository-evidenced |
| Working tree at baseline | Clean apart from two untracked helper scripts (`push-ads-rebuild.sh`, `push-store-rebuild.sh`) that contain no application code | repository-evidenced |

### Why HEAD and the audio commit differ, and why that is safe

The audible validation was performed against a build carrying embedded SHA
`b252a255`. HEAD has since advanced by five commits. `git diff b252a255 HEAD` restricted
to `mobile-native/src/core`, `mobile-native/src/calls`, `mobile-native/src/live`,
`mobile-native/package.json`, `mobile-native/app.json` and `bot.py` returns **only
`bot.py`, with three added lines, none of which contain the strings `livekit`, `LIVEKIT`,
`audio`, or `AUDIO`**. The intervening commits are the Business Profile, Seller Store,
Marketplace and Advertising screens plus their reports.

**Therefore the real-time audio behaviour at `ce03e160` is byte-identical to the
behaviour that was physically heard at `b252a255`.** Both SHAs are recorded here; the
snapshot tag is applied to `ce03e160` because that is the commit a rollback would
actually return to, and it carries the audio code unchanged.

| Intervening commit | Touches audio paths? |
| --- | --- |
| `cb3b1d97` docs(live-audio): record physical engine recovery evidence | No — report only |
| `171cd0ec` feat(business): add live profile and seller store dashboard | No |
| `0e7216d4` docs(store): report for the seller Store dashboard rebuild | No |
| `246ed16f` feat(marketplace): seller marketplace manager screen | No |
| `ce03e160` feat(business): two-sided ads manager behind the Advertising route | `bot.py` only, 3 lines, no audio or LiveKit content |

---

## 2. Application identity

| Field | Value | Source |
| --- | --- | --- |
| Bundle identifier | `com.pulsesoc.app` | repository-evidenced (`app.json`) and confirmed on-device |
| Application identifier (signed) | `87ZC69AGSR.com.pulsesoc.app` | owner-attested, recorded in `reports/pulsesoc_unified_realtime_audio_foundation_2026-08-01.md` |
| App version | `1.0.1` | repository-evidenced (`mobile-native/app.json` → `expo.version`) |
| iOS build number | `9` | repository-evidenced (`mobile-native/app.json` → `expo.ios.buildNumber`) |
| Android versionCode | Not set in `app.json` | repository-evidenced — **the Android build has no pinned versionCode; Android was not part of this baseline** |
| Embedded Git SHA in the validated build | `b252a255e675c1b3e065e602ef225adc3c31779a` | owner-attested via bundle inspection |
| Code signature | `codesign --verify --deep --strict` PASS | owner-attested |
| API host compiled into the bundle | `https://pulsesoc.com` — no `localhost` or `127.0.0.1` API URL present | owner-attested via bundle inspection |
| Build configuration | Release | owner-attested |
| Xcode | 26.6 (`17F113`) | owner-attested |

---

## 3. Backend and LiveKit environment

| Field | Value | Source |
| --- | --- | --- |
| Backend host | `https://pulsesoc.com` | owner-attested (bundle inspection) |
| Backend application | `bot.py`, served by gunicorn | repository-evidenced |
| Backend deployment identifier / release ID | **NOT RECORDED** — the hosting platform's deployment ID at the time of the audible test was not captured | — |
| Backend commit deployed | **NOT RECORDED** — no deployed-SHA endpoint was read at test time. `bot.py` at `ce03e160` is the repository state; whether production ran exactly that is unconfirmed | — |
| LiveKit server URL | Supplied at runtime through the `LIVEKIT_URL` environment variable; **not stored in the repository** | repository-evidenced |
| LiveKit API key / secret | `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` environment variables; not stored in the repository | repository-evidenced |
| LiveKit environment name (cloud project / self-hosted region) | **NOT RECORDED** — resolvable only from the deployment environment, which this repository cannot read | — |
| Token issuer | `pulse_livekit_access_token(identity, room_name, *, can_publish=False, …)` in `bot.py` | repository-evidenced |
| Token claim verifier | `pulse_livekit_verify_token_claims(…)` in `bot.py` | repository-evidenced |
| Observed room during the last recorded physical Live attempt | `pulse-webrtc-df5c70c3467f4770` | owner-attested |

> **Action for repository administration:** the three NOT RECORDED rows above must be
> filled before this baseline can be used to reproduce the environment exactly. They are
> environment facts, not code facts, and inventing them would defeat the purpose of the
> document.

---

## 4. Pinned audio-critical dependency versions

All versions below are the exact resolved values at `ce03e160`. Section 8 of the hard-lock
converts these into enforced pins.

| Package | Version in `mobile-native/package.json` |
| --- | --- |
| `@livekit/react-native` | `^2.9.0` |
| `@livekit/react-native-webrtc` | `144.1.1` (already exact-pinned) |
| `livekit-client` | `^2.15.4` |
| `expo-av` | `~16.0.8` |
| `expo` | `~54.0.36` |
| `react-native` | `^0.81.5` |

Native audio configuration at baseline:

- `NSMicrophoneUsageDescription` present in `expo.ios.infoPlist`.
- `UIBackgroundModes` = `["audio", "fetch", "remote-notification"]`.

---

## 5. Feature flags in effect during the verified test

| Flag | Layer | Value at baseline | Source |
| --- | --- | --- | --- |
| `LIVESTREAM_AUDIO_V2_ENABLED` | Backend env var, master switch | **OFF** by default. `pulse_live_audio_v2_enabled()` returns `False` unless the value is one of `1`/`true`/`yes`/`on`. Its value in production at test time is **NOT RECORDED** | repository-evidenced (default); env value not captured |
| `LIVESTREAM_AUDIO_V2_QA_ONLY` | Backend env var | Default OFF | repository-evidenced |
| `LIVESTREAM_AUDIO_V2_PERCENT` | Backend env var, sticky 0–100 rollout | Default 0 | repository-evidenced |
| `LIVESTREAM_AUDIO_V2_QA_USER_IDS` | Backend env var | Default empty | repository-evidenced |
| `audioV2Enabled` (`LIVE_AUDIO_V2_FLAG_KEY`) | Client, read from the LiveKit token response | Server-driven only. `normalizeLiveAudioV2Flag` requires strict `raw === true`; there is no local override and no cached fallback. Default OFF | repository-evidenced (`src/live/liveAudioFlags.ts`) |
| Resolved audio path | `resolveLiveAudioPath()` → `"v1_legacy"` when the flag is off, `"v2_isolated"` when on | repository-evidenced |

**Interpretation.** Because the client flag is strictly server-driven and defaults off, the
verified-working state is the **legacy (`v1_legacy`) livestream audio path** unless the
production environment had `LIVESTREAM_AUDIO_V2_ENABLED` set to a truthy value at the time
of the test. That environment value is NOT RECORDED. This is the single most important
open item in this baseline: **the flag state determines which of two code paths was
actually heard**, and the hard-lock's rollback threshold logic depends on knowing which.

---

## 6. Devices used

| Role | Device | Identifier | Model ID | iOS version | Source |
| --- | --- | --- | --- | --- | --- |
| Primary physical device | iPhone 16 Pro `P3r7or` | `F45E640F-6D02-514E-877C-B764E8D6818F` | `iPhone17,1` | **18.7.3** as last recorded on 2026-07-21; **the version at the 2026-08-02 test is NOT RECORDED** | repository-evidenced (`reports/pulsesoc_live_three_device_acceptance_2026-07-21.md`) |
| Second physical device | **NOT RECORDED** — a two-participant audible test requires a second endpoint, but its model and iOS version were not captured | — | — | — | — |
| Simulator (build/install verification only, not audible evidence) | iPhone 17 Pro Max | `E859950D-B187-4897-B389-05447C5AD796` | — | 26.5 | owner-attested |

Other devices previously seen in the account's device list but **not** used as evidence
here: `iPhone33` (iPhone 14, unavailable at last inventory) and `iPad (3)` (iPad A16,
unavailable). They are listed only so a future reader does not mistake them for test
endpoints.

---

## 7. Physical audible verification

**Test date:** 2026-08-02.
**Attested by:** the device owner (ROODY), who performed the test on physical hardware.
**Evidence class:** owner-attested. This section is a first-person report of what a human
heard. It is not reproducible from the repository and is not derived from telemetry, logs,
connection state, or a successful build.

The wording below is deliberately literal. It states what was *heard*, not what
*connected*.

### 7.1 Audio call

**PASS.** Live human speech was **physically audible in both directions** during a
one-to-one audio call: the caller heard the callee, and the callee heard the caller,
through the device speaker/receiver. This is not a report that the call connected, that
a track was published, or that the UI showed a connected state.

### 7.2 Video call

**PASS.** Live human speech was **physically audible in both directions while video was
active** during a one-to-one video call. Audio remained audible with the camera running;
it was not a video-only or audio-dropped session.

### 7.3 Livestream — host and viewer

**PASS.** A viewer on a separate endpoint **physically heard the host's live speech**
during a livestream. This is not a report that the viewer's player attached, that a
remote track was subscribed, or that a waveform was observed.

### 7.4 Livestream — guest

**NOT SEPARATELY ATTESTED.** The owner's report covers "livestream audio" as a whole and
confirms the viewer heard the host. It does **not** separately state that an approved
guest's speech was physically heard by the host and by viewers. Per the requirement that
guest audibility be stated only *if guest validation is complete*, guest audibility is
recorded here as **unverified at this baseline**.

Consequence: guest publication is inside the protected boundary and is covered by contract
tests, but it must not be described as physically verified in any release document until a
guest audible test is performed and recorded here.

### 7.5 Mixed sessions

**PARTIALLY ATTESTED.** The owner reported all three surfaces passing in the same
validation pass, which implies the app moved between call, video-call and livestream
without a restart. However, **the specific ordered transitions were NOT RECORDED** — there
is no record of which of the eight mixed-session transitions (call→Live, Live→call, video
call→Live, Live→video call, audio→video call, video→audio call, Live→Live, call→call) were
exercised, in what order, or whether the app was force-quit between any of them.

Consequence: the eight mixed-session transitions are covered by automated contract tests
(hard-lock section 5) but are **not** claimed as physically verified. A release document
must not state "mixed sessions operated without app restart" as a physical result until
the transitions are individually recorded here.

### 7.6 What this baseline does not claim

To keep this document usable as a rollback reference, the following are explicitly **not**
established by it:

- Android real-time audio. Not tested; no pinned versionCode.
- Bluetooth or wired-headset routing. No route-specific audible result was recorded.
- Interruption recovery (incoming PSTN call, Siri, alarm) audibility after resume.
- Guest livestream audibility (see 7.4).
- Behaviour with `LIVESTREAM_AUDIO_V2_ENABLED` in the opposite state to whatever it was
  during the test (see section 5).
- Sustained-duration audio. No session length was recorded.

---

## 8. Automated verification at this code state

Recorded from the exact-SHA validation run at `b252a255`, whose audio code is identical
to `ce03e160` (section 1):

| Check | Result |
| --- | --- |
| Full native Jest suite from a clean detached worktree | 114 suites, 1,903 tests PASS |
| TypeScript (`tsc --noEmit`) from the same worktree | PASS |
| Focused engine/Live audio tests | 2 suites, 20 tests PASS |
| Native call audit | PASS |
| Live guest/audio audit | PASS |
| Token-grant contract | PASS |
| Canonical webhook-owner contract | PASS |

Existing real-time audio test inventory at this baseline — **139 tests across 12 files**:

| Tests | File |
| --- | --- |
| 14 | `src/core/__tests__/realtimeAudioEngine.test.ts` |
| 14 | `src/core/__tests__/audioOwnershipPolicy.test.ts` |
| 2 | `src/core/__tests__/realtimeAudioTelemetry.test.ts` |
| 8 | `src/calls/__tests__/callAudioOwnershipRegression.test.ts` |
| 9 | `src/calls/__tests__/useNativeCallRoomAudio.test.ts` |
| 9 | `src/live/__tests__/liveAudioPublisher.test.ts` |
| 6 | `src/live/__tests__/liveAudioConfiguration.test.ts` |
| 20 | `src/live/__tests__/liveAudioRecovery.test.ts` |
| 18 | `src/live/__tests__/liveAudioTelemetry.test.ts` |
| 5 | `src/live/__tests__/cohostPublishGate.test.ts` |
| 9 | `src/live/__tests__/remoteAudioReapply.test.ts` |
| 25 | `src/live/__tests__/liveSession.test.ts` |

---

## 9. Architecture at the baseline

The verified-working system routes every real-time audio operation through a shared
platform layer. No feature screen touches `AVAudioSession` or a LiveKit local participant
directly. This shape is what the hard-lock preserves; it is not being redesigned.

```text
audio call / video call / livestream (host, guest, viewer)
        |
        +-> realtimeAudioEngine
        |      - one audio-session owner at a time
        |      - priority arbitration via audioOwnershipPolicy
        |      - generation-scoped lease (a stale release cannot kill a newer session)
        |      - canonical iOS category/mode/route configuration
        |
        +-> realtimeMicrophonePublisher
        |      - one in-flight publish operation per room
        |      - event-driven publication verification
        |      - duplicate reconciliation
        |      - viewer/permission denial (forbidden outcome)
        |
        +-> realtimeAudioTelemetry
               - privacy-safe hashed identifiers, correlation IDs
```

Adapters: `src/calls/useNativeCallRoom.ts` (audio and video calls),
`src/live/useLiveBroadcastRoom.ts` (livestream host, guest, viewer),
`src/live/liveSession.ts` (session state machine).

Recovery: `src/live/liveAudioRecovery.ts` — disconnect classification, bounded reconnect
(`LIVE_MAX_RECONNECT_ATTEMPTS = 6`), token refresh margin, route reapplication,
post-interruption resume.

---

## 10. Rollback reference

To return to this verified state:

```bash
git checkout realtime-audio-stable-v1
```

The tag points at `ce03e160eaf4649a8e02bc3b609a3182ca9d3859`. It is immutable: it must
never be moved, deleted, or re-pointed. If a later state is verified, it gets a new tag
(`realtime-audio-stable-v2`), and this one stays where it is.

The tag is only meaningful together with the environment facts in section 3 and the flag
state in section 5. A code rollback that lands on a different LiveKit environment or a
flipped `LIVESTREAM_AUDIO_V2_ENABLED` is not a rollback to this baseline.

---

## 11. Open items this baseline hands to repository administration

These are the fields this document could not honestly fill. Each blocks a specific claim.

1. **Backend deployment identifier and deployed SHA** (section 3) — blocks any claim that
   a rollback restores the same backend.
2. **LiveKit environment name** (section 3) — blocks reproducing the media path.
3. **`LIVESTREAM_AUDIO_V2_ENABLED` value at test time** (section 5) — blocks knowing which
   of two livestream audio code paths was heard.
4. **iOS version on the physical device at test time** (section 6) — blocks attributing a
   future regression to an OS update.
5. **Second physical endpoint model and iOS version** (section 6) — blocks reproducing the
   two-participant matrix.
6. **Guest livestream audible test** (section 7.4) — blocks claiming guest audio verified.
7. **Ordered mixed-session transition record** (section 7.5) — blocks claiming mixed
   sessions physically verified.

Filling item 3 is the highest priority. The other six degrade reproducibility; item 3
determines whether the protections in this hard-lock are guarding the path that actually
works.

---

## 12. Addendum — 2026-08-07 silent-host regression and fix

This section is **not** part of the original baseline. It records a livestream host
regression found and fixed after it, and the protections added so it cannot return.

### 12.1 The failure

A Live host published a microphone track that carried no audio energy. Everything the app
could observe looked healthy: the track was created and published, the `AVAudioSession` was
active and record-capable, the camera was publishing, and viewers saw video. The ADM
reported `inputEnabled=true` and `inputRunning=true`.

The engine state read off P3r7or was `outputEnabled=false`, `playoutInitialized=false`,
`engineRunning=false`. AVAudioEngine will not run without an **enabled output**, and with
the engine stopped the input delivers no buffers regardless of what the capture flags say —
so the input flags were describing a path that was not moving any audio.

Output is normally enabled as a side effect of subscribing to remote audio. **A host
subscribes to nobody**, so nothing ever enabled it. `startPlayout` cannot substitute: it
asks for `outputRunning` and leaves `outputEnabled` alone, a pair `ModifyEngineState`
rejects outright ("Output must be enabled if running"). `initPlayout` is the only call that
enables output, and the stock `@livekit/react-native-webrtc` does not bridge it to JS.

### 12.2 Physical verification

**PASS — host audible.** After the fix was installed on P3r7or, the repository owner
confirmed livestream audio works. As in section 7, this is a report of sound actually
heard, not of a track attaching or a waveform being observed.

**Scope limits, stated explicitly.** This attests the **host output-path fix on P3r7or**.
It does **not** separately re-establish §7.4 (guest audibility) or §7.5 (ordered
mixed-session transitions), which remain unverified and partially attested respectively.
The device/OS gaps in open items 4 and 5 are unchanged, so a future regression still cannot
be cleanly attributed to an OS update.

### 12.3 What now guards it

- `required_output_enable_discipline` in `config/realtime-audio-protected-paths.json` —
  requires `initNativePlayout` in the two live-audio modules, **and** requires the native
  selector `audioDeviceModuleInitPlayout` to be present in the LiveKit patch file.
- `mobile-native/patches/@livekit+react-native-webrtc+144.1.1.patch` added to
  `dependency_watch.files`. It was previously unwatched despite carrying the camera
  `AVAudioSession` fix as well.
- Four behavioural tests in `src/live-audio/__tests__/liveHostEngineRepair.test.ts` pinning
  that output is enabled, that it happens **before** the recorder repair, that playout
  starts only after init, and that a failed init does not chase playout.
- That test file added to `critical_audio_tests` and to the
  `test:realtime-audio-critical` / `test:realtime-audio` scripts, which previously ran no
  `src/live-audio/` test at all.

Each protection above was confirmed to fail when the fix is reverted, not merely to pass
while it is present.

### 12.4 The trap worth remembering

The native half of this fix lived only in `node_modules`, which is gitignored. It therefore
built green on the machine that made it and would have been absent everywhere else — and
because the JS side degrades to a **no-op rather than an error** when the bridge is missing,
the regression would have been silent all the way to a dead broadcast. That is why the
protection asserts on the **patch file contents**, not merely on the JS call site.
