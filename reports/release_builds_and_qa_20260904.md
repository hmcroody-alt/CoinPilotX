# Release builds and device/simulator QA — 2026-09-04

Items 26–30. Two Release builds of `mobile-native` from the integration
worktree at `d9efc4f4`, installed and launched on both targets, **sequentially
— never in parallel**.

The sequencing is not a preference. ReactCodegen generates into the shared
source tree rather than into derived data, so two concurrent builds corrupt each
other's generated sources; separate `-derivedDataPath` does not isolate them.
Each build was confirmed fully exited before the next was started.

## Result

| Target | Build | Install | Launch | Process |
|---|---|---|---|---|
| P3r7or — iPhone 16 Pro, `F45E640F…818F` | SUCCEEDED | `com.pulsesoc.app` | launched | PID 5353, alive |
| iPhone 17 Pro Max simulator, `E859950D…D796` | SUCCEEDED | `com.pulsesoc.app` | launched | PID 98282, alive, **0 crash reports** |

App identity, device build: `com.pulsesoc.app`, version **1.0.1**, build **22**,
with a 13.4 MB `main.jsbundle` embedded (Release is self-contained — no Metro).

## Agora-only, verified in the shipped binary

The standing rule is that RTC is Agora and only Agora. This was checked against
the built artefact rather than the source tree, which is the only place the
claim can actually be falsified:

- **25 embedded frameworks**, of which 24 are Agora (`AgoraRtcKit`,
  `AgoraRtcWrapper`, the AI echo-cancellation and noise-suppression extensions,
  spatial audio, video codecs) plus `hermes`.
- **Zero LiveKit frameworks.**
- **Zero occurrences of `livekit`** anywhere in the 13.4 MB JS bundle.

## Two build failures, both environmental

Neither was a code defect, and it is worth recording how each presented, because
both look like source errors at first glance.

### 1. ReactCodegen input race (device, attempt 1)

14 failures, all of the form:

```
error: Build input file cannot be found:
  '…/ios/build/generated/ios/react/renderer/components/rnsvg/States.cpp'
```

across `rnsvg`, `rnscreens`, `rnstripe`, `safeareacontext`,
`rngesturehandler_codegen`, `RNDateTimePickerCGen` and `AgoraRtcNgSpec`.

Reading it as "the Agora spec is missing" would have been the wrong conclusion.
The files **existed** when inspected afterwards, timestamped *during* the failed
build — the codegen script phase wrote them, but `CompileC` had already been
scheduled against them. A pure ordering race in a cold `build/generated`.

Fix: rerun. Attempt 2 succeeded with the inputs now present. No source change.

### 2. Disk exhaustion (simulator, attempt 1)

```
error: unable to open output file '…/UIManagerUpdateShadowTree.o':
  'No space left on device'
```

The volume was at 100% with 1.1 GiB free. Reclaimed 4.6 GiB without touching
the shared checkout or anything version-controlled:

| Freed | Why it was safe |
|---|---|
| `~/Library/Caches/CocoaPods` (2.0 G) | Pure download cache, regenerates on demand |
| integration `ios/build/ddp` (device derived data) | The device build was already **delivered** — compiled, installed and launched — so its intermediates were spare |
| `~/Library/Developer/Xcode/iOS DeviceSupport` (4.6 G) | Symbol cache, re-extracted from the device on next debug |

Deliberately **not** touched: the 7.7 G `call-acceptance-release` directory in
`~/Desktop/CoinPilotX`. It is the largest single win available, and it is in the
checkout shared with other sessions — deleting build state out from under a
concurrent build to save a few minutes is not a trade worth making.

The build resumed incrementally from the intact `ddp-sim` and succeeded.

### 3. The simulator ad-hoc signing step is mandatory

Not a failure this time, because it was anticipated, but it would have been:
`CODE_SIGNING_ALLOWED=NO` signs nothing, and Agora ships `AgoraRtcKit.framework`
**unsigned**. Confirmed directly before signing:

```
AgoraRtcKit.framework: code object is not signed at all
```

arm64 simulator dylibs on Apple Silicon require at least an ad-hoc signature, so
without this the app installs cleanly, returns a PID from `simctl launch`, and
then dies immediately in dyld with `Library not loaded: @rpath/AgoraRtcKit…` —
which reads like a missing or mis-sliced framework and is not. All 25
frameworks and the app bundle were ad-hoc signed before install.

**A returned PID proves nothing on the simulator.** Verified instead by crash
report count (0 before, 0 after), `launchctl list` showing the process live, and
a screenshot.

## What QA actually established

`reports/evidence/sim_launch_20260904.png`

The simulator app launches and renders the sign-in screen correctly: branding
and logo intact, localized English copy, email field populated, Face ID and
sign-in controls laid out, no red-box and no blank frame.

The screen carries a green **"Connected"** indicator, which is the most useful
single fact in this report: the app reached the production backend on the
release SHA. Frontend and backend of this release are talking to each other.

## What QA did NOT establish — stated plainly

**Everything behind authentication is unverified.** Signing in requires entering
a password, which is out of scope for this role, so the QA boundary is the
sign-in screen. Feed, Reels, Live, Calls, Messenger, Premium and Private Office
were all verified as *deployed and correctly gated* (Items 23–25) and as
*passing their contract suites* (Items 13–17) — but not as working end-to-end
for a signed-in user.

**No audio was validated.** No screenshot is audible and no test in this
repository can hear anything. The physical real-time-audio matrix remains
undischarged, exactly as the release-range addendum states. This report does not
change that and should not be cited as if it did.

**The device screen was not observed.** `devicectl` confirms the process is
alive on P3r7or; it does not render its screen. Visual acceptance on the
physical device — including Decision 8, owner sign-off on the five Premium
tiles — is still owed by a human.

## Reproduction

```
cd mobile-native/ios

# device — first, alone
xcodebuild -workspace PulseSoc.xcworkspace -scheme PulseSoc -configuration Release \
  -destination 'id=F45E640F-6D02-514E-877C-B764E8D6818F' -allowProvisioningUpdates \
  -derivedDataPath build/ddp DEVELOPMENT_TEAM=87ZC69AGSR \
  CODE_SIGN_IDENTITY="Apple Development" CODE_SIGN_STYLE=Automatic \
  PROVISIONING_PROFILE_SPECIFIER="" build
xcrun devicectl device install app --device F45E640F-… \
  build/ddp/Build/Products/Release-iphoneos/PulseSoc.app
xcrun devicectl device process launch --device F45E640F-… com.pulsesoc.app

# simulator — only after the above has exited
xcodebuild -workspace PulseSoc.xcworkspace -scheme PulseSoc -configuration Release \
  -destination 'platform=iOS Simulator,id=E859950D-B187-4897-B389-05447C5AD796' \
  -derivedDataPath build/ddp-sim CODE_SIGNING_ALLOWED=NO build
APP=build/ddp-sim/Build/Products/Release-iphonesimulator/PulseSoc.app
for fw in "$APP"/Frameworks/*.framework; do codesign --force --sign - --timestamp=none "$fw"; done
codesign --force --sign - --timestamp=none "$APP"
xcrun simctl uninstall E859950D-… com.pulsesoc.app   # install-over keeps the old bundle
xcrun simctl install  E859950D-… "$APP"
xcrun simctl launch   E859950D-… com.pulsesoc.app
```

Both builds run detached (`nohup … & disown`) — a cold Release compile exceeds
the 10-minute foreground cap and is SIGKILLed mid-compile, which leaves no
error line at all and looks like a silent failure rather than a timeout.
