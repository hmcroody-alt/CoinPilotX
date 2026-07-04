# PulseSoc Native Camera Studio iOS Simulator QA

Date: 2026-07-04

## Scope

This mission tested the Native Camera Studio path on the iPhone 17 Pro iOS Simulator. No new native features were built and no production WebView routes were modified.

Simulator target:

- Device: iPhone 17 Pro
- UDID: `7B3BEEBC-6135-497D-91CD-A3E70C927D56`
- Runtime: iOS 26.5

## Commands And Results

Simulator availability:

```text
xcode-select -p
/Applications/Xcode.app/Contents/Developer

xcrun simctl list devices available
-- iOS 26.5 --
    iPhone 17 Pro (7B3BEEBC-6135-497D-91CD-A3E70C927D56) (Shutdown)
```

Boot:

```text
xcrun simctl boot 7B3BEEBC-6135-497D-91CD-A3E70C927D56
xcrun simctl bootstatus 7B3BEEBC-6135-497D-91CD-A3E70C927D56 -b
Finished
```

Expo Go launch:

```text
cd mobile-native
npx expo start --ios --go --localhost --port 8082
Starting Metro Bundler
Opening exp://127.0.0.1:8082 on iPhone 17 Pro
Fetching Expo Go
Installing Expo Go on iPhone 17 Pro
iOS Bundled 23321ms index.ts (1389 modules)
```

Installed app checks:

```text
xcrun simctl get_app_container 7B3BEEBC-6135-497D-91CD-A3E70C927D56 host.exp.Exponent
.../Exponent-2.31.6.tar.app

xcrun simctl get_app_container 7B3BEEBC-6135-497D-91CD-A3E70C927D56 com.pulsesoc.nativeapp
No such file or directory
```

Foreground/background container recovery:

```text
xcrun simctl terminate 7B3BEEBC-6135-497D-91CD-A3E70C927D56 host.exp.Exponent
xcrun simctl launch 7B3BEEBC-6135-497D-91CD-A3E70C927D56 host.exp.Exponent
host.exp.Exponent: 41794
```

## Static Verification

- `npm ci --prefix mobile-native --no-audit --no-fund --progress=false`: passed.
- `npm run --prefix mobile-native typecheck`: passed.
- `git diff --check`: passed before report edits.

Expo Doctor did not pass:

```text
16/17 checks passed. 1 checks failed.
Check native tooling versions
Your Expo SDK version 51 is not compatible with Xcode 26.6.0. Required Xcode version: <=16.2.0.
```

This is an environment/toolchain compatibility blocker, not a PulseSoc business-logic bug.

## QA Evidence

Screenshots captured during the run:

- `/tmp/pulsesoc-iphone17pro-boot.png`
- `/tmp/pulsesoc-iphone17pro-expo.png`
- `/tmp/pulsesoc-iphone17pro-after-bundle.png`
- `/tmp/pulsesoc-iphone17pro-camera-deeplink.png`
- `/tmp/pulsesoc-iphone17pro-relaunch.png`
- `/tmp/pulsesoc-iphone17pro-relaunch-final.png`
- `/tmp/pulsesoc-iphone17pro-camera-route-no-overlay.png`
- `/tmp/pulsesoc-iphone17pro-after-cliclick.png`
- `/tmp/pulsesoc-iphone17pro-after-close-click.png`

Observed visual states:

- The iPhone 17 Pro simulator booted.
- Expo Go installed successfully.
- PulseSoc Native bundled and launched in Expo Go.
- The PulseSoc login screen rendered behind Expo Go's first-run developer menu.
- Foreground/background container relaunch worked, returning to Expo Go home with PulseSoc Native listed as recently opened.

## Test Matrix

| Area | Result | Notes |
| --- | --- | --- |
| App launch | Partial pass | PulseSoc Native bundled and rendered login behind Expo Go developer menu. |
| Login/session restore | Not verified | No QA credentials were used and Expo Go developer menu blocked interaction. |
| Camera Studio route | Not verified | Deep link was sent, but Expo Go developer menu remained over the app. |
| Camera permission states | Not verified | Requires interactive simulator/device access and camera-capable runtime. |
| Microphone permission states | Not verified | Requires interactive simulator/device access. |
| Gallery fallback | Not verified | Requires interactive simulator/device access. |
| Preview flow | Not verified | Requires authenticated Camera Studio interaction. |
| Caption/privacy/destination flow | Not verified | Requires authenticated Camera Studio interaction. |
| Upload handoff | Not verified | Requires authenticated flow and selected/captured media. |
| Publish destination routing | Not verified | Requires authenticated publish flow. |
| Foreground/background recovery | Partial pass | Expo Go container terminated/relaunched; authenticated Camera Studio recovery was not verified. |

## Blockers

1. Expo SDK 51 is not compatible with the currently selected Xcode 26.6 according to Expo Doctor.
2. The app was launched through Expo Go, not an installed `com.pulsesoc.nativeapp` development build.
3. Expo Go displayed its first-run developer menu over the PulseSoc app.
4. Local UI automation could not dismiss the Expo Go overlay reliably; AppleScript coordinate clicks returned a System Events error and `cliclick` did not affect the simulator framebuffer.
5. No authenticated QA session was available for simulator login/session restore.
6. Simulator camera behavior is inherently limited and cannot replace physical-device camera/mic/video/upload QA.

## What The Simulator Can Verify Later

After the toolchain and interaction blockers are resolved, the simulator can help verify:

- App launch.
- Auth gate rendering.
- Login/session restore if QA credentials are available.
- Native routing and deep links.
- Form/layout behavior.
- Gallery fallback to the photo library.
- Caption/privacy/destination UI.
- Foreground/background app lifecycle.

## What Requires Physical Device QA

Physical devices are still required for release confidence on:

- Real camera capture.
- Real microphone permission and audio capture.
- Real front/back camera switching.
- Video duration, file size, memory pressure, and orientation.
- Large image/video uploads on real networks.
- Push/deep-link notification taps.
- Lock-screen/background behavior.

## Recommended Next Action

Do not move to Native LiveKit calls yet.

Next highest-value action: resolve the iOS QA runtime mismatch by either using an Expo SDK 51-compatible Xcode path or planning an Expo SDK upgrade/dev-client path, then run Camera Studio simulator QA again with an installed development build for `com.pulsesoc.nativeapp`.
