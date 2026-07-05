# PulseSoc Native iPhone Camera Studio Interaction QA

Date: 2026-07-05

## Result

Status: physical iPhone app launch, bundle load, Camera Studio deep-link launch, and process-level suspend/resume were verified. Real on-device Camera Studio interaction remains unverified.

This mission did not build Native LiveKit calls, did not modify production WebView routes, and did not weaken production authentication.

## Device And Build

Device:

- Name: `P3r7or`
- Model: iPhone 16 Pro (`iPhone17,1`)
- OS: iOS 18.7.3
- UDID: `00008140-000E2D9A2EE8801C`
- Pairing: valid
- Developer Mode: enabled
- Lock state: `unlockedSinceBoot: true`

Installed app:

- Bundle ID: `com.pulsesoc.nativeapp`
- App name: PulseSoc Native
- Version: `0.1.0`
- Build: `1`

Metro:

```text
iOS Bundled 573ms index.ts (1542 modules)
iOS Bundled 41ms index.ts (1 module)
```

Warning observed:

```text
[expo-av]: Expo AV has been deprecated and will be removed in SDK 54.
```

The Expo AV warning is not a Camera Studio blocker, but it remains native media-player technical debt for Reels, Status, and Live viewer surfaces.

## Evidence Captured

Device detection:

```bash
xcrun devicectl list devices
```

Result:

```text
P3r7or   P3r7or.coredevice.local   F45E640F-6D02-514E-877C-B764E8D6818F   connected   iPhone 16 Pro (iPhone17,1)
```

Device identity:

```bash
ideviceinfo -u 00008140-000E2D9A2EE8801C -k ProductType
ideviceinfo -u 00008140-000E2D9A2EE8801C -k ProductVersion
ideviceinfo -u 00008140-000E2D9A2EE8801C -k DeviceName
```

Result:

```text
iPhone17,1
18.7.3
P3r7or
```

Pairing:

```bash
idevicepair -u 00008140-000E2D9A2EE8801C validate
```

Result:

```text
SUCCESS: Validated pairing with device 00008140-000E2D9A2EE8801C
```

App launch:

```bash
xcrun devicectl device process launch --device 00008140-000E2D9A2EE8801C --terminate-existing com.pulsesoc.nativeapp
```

Result:

```text
Launched application with com.pulsesoc.nativeapp bundle identifier.
```

Camera Studio deep-link launch:

```bash
xcrun devicectl device process launch --device 00008140-000E2D9A2EE8801C --terminate-existing --payload-url 'pulsesoc://pulse/camera/photo?target=feed' com.pulsesoc.nativeapp
```

Result:

```text
Launched application with com.pulsesoc.nativeapp bundle identifier.
```

Running process:

```text
879   /private/var/containers/Bundle/Application/.../PulseSocNative.app/PulseSocNative
```

Process-level foreground/background check:

```bash
xcrun devicectl device process suspend --device 00008140-000E2D9A2EE8801C --pid 879
xcrun devicectl device process resume --device 00008140-000E2D9A2EE8801C --pid 879
```

Result:

```text
Signal to suspend process sent to pid 879
Sent signal to resume process sent to pid 879
879   /private/var/containers/Bundle/Application/.../PulseSocNative.app/PulseSocNative
```

## Screenshot / Video Evidence

Physical screenshot capture was attempted after installing the local Mac-side `libimobiledevice` utility package.

```bash
idevicescreenshot -u 00008140-000E2D9A2EE8801C /tmp/pulsesoc-native-physical-check.png
```

Result:

```text
Could not start screenshotr service: Invalid service
Remember that you have to mount the Developer disk image on your device if you want to use the screenshotr service.
```

Follow-up checks showed Developer Mode is enabled and a developer disk image is listed as mounted:

```text
DeveloperModeStatus: true
Status: Complete
```

No physical screenshots or videos were captured in this run. Visual quality remains unverified on the physical iPhone because there is no captured screen evidence.

## Interaction QA Matrix

| Area | Result | Evidence |
| --- | --- | --- |
| App install | Previously verified | `com.pulsesoc.nativeapp` installed as PulseSoc Native `0.1.0 (1)` |
| App launch | Passed | `devicectl process launch` succeeded |
| JS bundle load | Passed | Metro bundled `index.ts` for iOS |
| Camera Studio route/deep link | Passed at process level | Payload URL launch accepted for `pulsesoc://pulse/camera/photo?target=feed` |
| Login/session restore | Not observed | Requires manual on-device interaction or physical UI automation |
| Camera permission allowed | Not observed | Requires manual on-device interaction or physical UI automation |
| Camera permission denied | Not observed | Requires manual on-device interaction or physical UI automation |
| Microphone permission allowed | Not observed | Requires manual on-device interaction or physical UI automation |
| Microphone permission denied | Not observed | Requires manual on-device interaction or physical UI automation |
| Gallery picker | Not observed | Requires manual on-device interaction or physical UI automation |
| Photo capture | Not observed | Requires manual on-device interaction or physical UI automation |
| Video capture | Not observed | Requires manual on-device interaction or physical UI automation |
| Front/back camera switch | Not observed | Requires manual on-device interaction or physical UI automation |
| Preview flow | Not observed | Requires manual on-device interaction or physical UI automation |
| Upload progress | Not observed | Requires authenticated manual/device automation and backend upload evidence |
| Feed publish | Not observed | Requires authenticated manual/device automation and post ID |
| Status publish | Not observed | Requires authenticated manual/device automation and status ID |
| Reels publish | Not observed | Requires authenticated manual/device automation and reel ID |
| Retry/cancel under weak network | Not observed | Requires network conditioning or real weak-network setup |
| Foreground/background recovery | Passed at process level only | PID `879` suspended/resumed and remained running |
| Visual quality | Not observed | Screenshot/video capture failed; no manual visual evidence captured |

## Backend IDs

No backend media IDs, upload IDs, post IDs, status IDs, or reel IDs were captured because no authenticated physical Camera Studio upload/publish interaction was completed in this run.

## Failures And Blockers

Observed tooling blockers:

- `devicectl` can launch, deep-link, suspend, and resume the app, but it does not provide touch automation, camera interaction, gallery picker control, or screenshots.
- `idevicescreenshot` is installed but cannot start the iOS `screenshotr` service on this device, even with Developer Mode enabled and a mounted developer image reported.
- No Maestro, XCTest UI test target, Appium, idb, or other physical iOS interaction automation path is configured in the repository.
- No Android physical device is attached.

Application blockers found:

- None proven. The available tools did not reach real Camera Studio interaction, so no app-level camera/media failures can be confirmed or fixed yet.

## Fixes Applied

No production code fixes were applied.

No native feature code was changed.

## Recommendation

Do not move to Native LiveKit calls yet.

The next highest-value action is to add a reliable physical-device interaction evidence path, then rerun this same Camera Studio QA checklist. The safest options are:

1. Run a manual on-device QA session while capturing screen video from the iPhone itself or macOS Finder/QuickTime, then record media/upload/publish IDs.
2. Add a dedicated QA-only XCTest UI test target for `com.pulsesoc.nativeapp` that can drive login, Camera Studio navigation, permission prompts, gallery/capture controls, and screenshots on the physical iPhone.
3. Evaluate Appium or Maestro only if they can drive the physical iPhone in this local environment without weakening production auth.

Physical iPhone Camera Studio should remain a release blocker until camera permissions, microphone permissions, gallery picker, photo/video capture, upload progress, Feed/Status/Reels publish, retry/cancel, and visual quality have real device evidence.
