# PulseSoc Native — CallKit + PushKit VoIP Integration

- **Stage 2 scaffolding:** 2026-07-20, branch `release/undx-nexus-core-v4` (flag OFF, inert)
- **Stage 3 implementation:** 2026-09-15, branch `claude/pushkit-callkit-voip` (this document)
- **Goal:** Ring the iOS system call UI (CallKit) when a PulseSoc call arrives while the app is backgrounded, in another app, locked, or terminated — delivered via an APNs VoIP push through PushKit.
- **Why it was needed:** Stage 1's in-app ringer (`callSignalMedia.ts` + `IncomingCallLayer.tsx`) only runs while the app is foregrounded and polling `getActiveCalls` every 4.2 s (`IncomingCallLayer.tsx:39`, gated on `appState.current === "active"`). Backgrounded and killed devices never rang.

> **Correction notice.** The 2026-07-20 revision of this file contained three
> claims that were wrong and that would have sent the next reader down a blind
> alley. They are corrected below and called out here so nobody trusts a stale
> copy: (1) a **VoIP Services certificate is not required** — PulseSoc
> authenticates to APNs with a token-based `.p8` key, which is valid for every
> topic of every app on the team, VoIP included; (2) the iOS path is
> **`ios/PulseSoc/`**, not `ios/PulseSocNative/`; (3) the backend routes
> `POST /api/calls/voip-token` and `/revoke` were listed as remaining work but
> **already existed** (`services/pulsesoc_communications_engine.py`,
> `services/pulsesoc_voip_push.py`).

## Architecture

One rule shapes the whole thing: **all orchestration lives in plain testable JS;
the pods are touched in exactly one file.**

```
APNs VoIP push
   ↓
AppDelegate.swift  (PKPushRegistryDelegate)      ← native, runs before JS exists
   ↓ reportNewIncomingCall, synchronously, no network first
CallKit system UI
   ↓ answer / decline / hangup
callKitNativeProvider.ts   ← the ONLY file importing either pod
   ↓ NativeCallKitProvider port
callKitBridge.ts   ← every decision: id mapping, gating, signalling
   ↓
existing call state  →  existing Agora session
```

`callKitBridge.ts` holds the decisions and imports no pod, which is what lets it
be unit-tested against a fake provider on a machine with no pods and no
simulator. `callKitNativeProvider.ts` holds no decisions.

The existing Agora foundation was **not** rewritten or wrapped. CallKit reports
call *state* to the system; the call still runs on the same engine through the
same `callSessionStore` after the answer.

## What shipped

| File | Purpose |
|---|---|
| `ios/PulseSoc/AppDelegate.swift` | Creates the `PKPushRegistry` at launch (the only reason iOS will relaunch a terminated app for a VoIP push) and implements `PKPushRegistryDelegate`. Reports to CallKit **synchronously**, before any network call or bridge hop. |
| `ios/PulseSoc/Info.plist`, `app.json` | `voip` appended to `UIBackgroundModes`. `audio` preserved — it is required by `dependency_watch.required_ios_configuration`. |
| `ios/PulseSoc/PulseSoc-Bridging-Header.h` | Both pods are Objective-C only; AppDelegate.swift needs them. |
| `src/calls/callKitNativeProvider.ts` | The port implementation. Pins `audioSession.mode` to `voiceChat`; sets `includesCallsInRecents: false`; subscribes to `didLoadWithEvents` for the cold-launch answer replay. |
| `src/calls/callKitBridge.ts` | Orchestration. Server-UUID identity, double-report guard, answer→`acceptCall`, decline→`declineCall`, hangup→`endCall`, token→backend, sign-out revocation. |
| `src/api/calls.ts` | `call_uuid` on `PulseCall`; VoIP token register/revoke wire contract. |
| `src/api/installationId.ts` | The installation id both push registrations must share. |
| `src/session/auth.ts` | Revokes the VoIP token on sign-out. |

## The four things that are easy to get wrong

**1. One UUID, issued by the server.** CallKit, the PushKit payload and the
backend must name the same call with the same string. The client used to mint a
UUIDv4; `generateUuidV4()` is now deleted. A VoIP push reports a call natively
before JS runs, so if the foreground poller then reported the *same* call under a
locally minted UUID, iOS would show two incoming calls and one of the answers
would resolve to a call id the server does not recognise — the user picks up and
nothing happens.

**2. Report to CallKit before doing anything else.** iOS 13+ terminates the
process if a VoIP push does not result in `reportNewIncomingCall`, and repeated
offences revoke VoIP delivery for the app. Nothing may precede it — no fetch, no
token refresh, not even a JS bridge hop, because when the app was launched *by*
the push the bridge does not exist yet. This includes the `cancel_call` payload,
which reports the call and then immediately ends it inside the completion
handler.

**3. The cold-launch lock-screen answer.** When iOS launches PulseSoc to deliver
a VoIP push and the user answers from the lock screen, the answer happens before
the JS bundle has finished evaluating — there is no listener to receive it.
CallKeep queues those actions and replays them through `didLoadWithEvents` the
moment JS subscribes. Without that subscription the single most important path in
the feature silently does nothing, and the user gets a connected CallKit UI with
no call behind it.

**4. The device id is the join.** The backend suppresses the ordinary
incoming-call alert push for exactly those device ids holding an active VoIP
token. Both registrations must therefore use the same `getPushInstallationId()`.
A mismatch does not error — it rings through CallKit *and* delivers the alert
banner to the same handset.

## Delivery policy

VoIP push is the **primary** incoming-call delivery for supported iOS devices;
the normal alert push is a **compatibility fallback only**.

- Active iOS VoIP token for the device → VoIP push, alert push suppressed **for that device**.
- No active VoIP token → existing alert push, unchanged.
- Android, web, older iOS builds without PushKit → existing alert push, unchanged.
- Never both, to the same eligible device, for the same call.

## Audio ownership

This provider never configures `AVAudioSession`. PulseSoc has one governed audio
coordinator (`src/core/realtimeAudioEngine.ts`) and a second writer is the
documented way this app goes silent mid-call.

`react-native-callkeep` does not fully honour that: its internal
`configureAudioSession` (`RNCallKeep.m:913`) is called from
`performAnswerCallAction` (`:1083`) and `didActivateAudioSession` (`:1145`), and
it writes category, mode, sample rate and buffer duration on the shared session.
**It cannot be prevented from JS.** What it *can* be told is which mode to write,
so `setup()` pins it to `voiceChat` — the mode a PulseSoc call wants anyway. That
turns a write which would otherwise contradict the engine
(`AVAudioSessionModeDefault`: no echo cancellation, wrong routing for a call)
into one that agrees with it.

**This is a mitigation, not a proof.** CallKit's `didActivateAudioSession` is
asynchronous and may land after Agora has joined. Only a physical device settles
it. See the regression-risk section of `reports/realtime_audio_change_declaration.md`.

## Account-side status

| Item | Status |
|---|---|
| VoIP Services certificate | **Not required.** Token-based `.p8` (`APNS_KEY_ID` / `APNS_TEAM_ID` / `APNS_PRIVATE_KEY`) signs alert and VoIP pushes alike. |
| APNs topic | `com.pulsesoc.app.voip` — the bundle id with a `.voip` suffix, derived by `voip_topic()`. |
| `aps-environment` | Currently `development` in `ios/PulseSoc/PulseSoc.entitlements`. TestFlight/App Store need `production`. VoIP shares this environment; the server picks the APNs host from `APNS_USE_SANDBOX`, which must agree. |
| Backend routes | Already implemented. |

## Verification

JS, Python and mutation results are recorded in
`reports/realtime_audio_change_declaration.md` (PushKit VoIP + CallKit addendum),
along with the twelve-case physical acceptance matrix that must pass on a real
iPhone before this ships. A simulator cannot receive a PushKit push, cannot run
CallKit's audio-session activation, and cannot carry the entitlements involved.

## Rollback

`EXPO_PUBLIC_NATIVE_CALLKIT_ENABLED=0`. The app stops registering a VoIP token;
suppression is conditioned on an active token, so it lapses with the
registration and the alert push rings again — the two halves fail safe together.
The one ordering that does not self-heal is a build that registered a token
followed by a build with the flag off; sign-out revocation and the server-side
`revoke_token` both clear that.
