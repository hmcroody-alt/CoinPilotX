# PulseSoc Native Captured iPhone Camera Studio QA

Date: 2026-07-05

## Result

Status: machine-captured iPhone launch, bundle, deep-link, display, process, and syslog evidence was collected. Real Camera Studio interaction QA remains blocked.

This mission did not build Native LiveKit calls, did not modify production WebView routes, and did not claim camera/media behavior as verified without screen recording, screenshots, backend IDs, or manual pass evidence.

## Device And Build

Device:

- Name: `P3r7or`
- Model: iPhone 16 Pro (`iPhone17,1`)
- OS: iOS 18.7.3
- UDID: `00008140-000E2D9A2EE8801C`
- State: connected
- Lock state: `unlockedSinceBoot: true`
- Display: LCD, `1206 x 2622`, portrait, backlight active

App:

- Bundle ID: `com.pulsesoc.nativeapp`
- Native QA identity remains separate from production `com.pulsesoc.app`.
- API base URL used for Metro run: `https://pulsesoc.com`

## Evidence Captured

Device detection:

```text
P3r7or   P3r7or.coredevice.local   F45E640F-6D02-514E-877C-B764E8D6818F   connected   iPhone 16 Pro (iPhone17,1)
```

Display state:

```text
Current Displays:
LCD (primary)
bounds: (0.0, 0.0, 1206.0, 2622.0)
currentOrientation: rot0
pointScale: 3
Main display backlight state: backlight is on and active
Main display orientation: portrait
```

Metro bundle:

```text
iOS Bundled 584ms index.ts (1531 modules)
iOS Bundled 38ms index.ts (1 module)
```

Warning observed:

```text
[expo-av]: Expo AV has been deprecated and will be removed in SDK 54.
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
952   /private/var/containers/Bundle/Application/.../PulseSocNative.app/PulseSocNative
```

Screenshot attempt:

```bash
idevicescreenshot -u 00008140-000E2D9A2EE8801C /tmp/pulsesoc-native-captured-qa-check.png
```

Result:

```text
Could not start screenshotr service: Invalid service
Remember that you have to mount the Developer disk image on your device if you want to use the screenshotr service.
```

No screenshot file was produced.

## Syslog Excerpts

The raw `idevicesyslog` capture was intentionally not committed because it contained more than 10,000 lines of noisy device output. Relevant excerpts were copied here.

Foreground app evidence:

```text
fgApp: com.pulsesoc.nativeapp
```

Native app foreground assertion:

```text
app<com.pulsesoc.nativeapp(E564D7E9-254E-414F-A683-54951285E51F)> ['FBWorkspace (ForegroundFocal)']
```

Native app process:

```text
PulseSocNative(Network)[952]
```

Camera service state during this attempt:

```text
cameracaptured(MediaSafetyNet): UI(Elsewhere) Cam(Cold:<private>) Mic(N/A) CameraException(None) consistency: Consistent
audiomxd(MediaSafetyNet): UI(Elsewhere) Cam(N/A) Mic(Cold:<private>) CameraException(None) consistency: Consistent
```

Interpretation: the app was foregrounded, but no real in-app camera interaction was observed. The camera service remained cold, so photo/video capture cannot be claimed verified.

## Requested QA Matrix

| Area | Result | Evidence |
| --- | --- | --- |
| App launch | Passed at process level | `devicectl process launch` succeeded |
| Login/session restore | Not tested | No manual input or UI automation path was available |
| Camera Studio open | Passed at process/deep-link level only | `pulsesoc://pulse/camera/photo?target=feed` accepted |
| Camera permission | Not tested | No manual interaction or screenshot/video evidence |
| Microphone permission | Not tested | No manual interaction or screenshot/video evidence |
| Gallery picker | Not tested | No manual interaction or screenshot/video evidence |
| Photo capture | Not tested | Camera service remained cold in syslog |
| Video capture | Not tested | Camera service remained cold in syslog |
| Front/back camera switch | Not tested | No manual interaction or UI automation path was available |
| Preview flow | Not tested | No media captured/selected |
| Upload progress | Not tested | No upload attempted |
| Feed publish | Not tested | No media upload or post ID |
| Status publish | Not tested | No media upload or status ID |
| Reels publish | Not tested | No media upload or reel ID |
| Retry/cancel under weak network | Not tested | No upload attempted |
| Foreground/background recovery | Previously passed at process level | Prior PID suspend/resume evidence remains process-level only |
| Visual quality | Not verified | No screenshot/video/manual evidence captured |

## Backend IDs

No backend IDs were produced during this captured attempt:

- Media upload IDs: none
- Upload IDs: none
- Published post IDs: none
- Published status IDs: none
- Published reel IDs: none

Reason: no authenticated physical Camera Studio media upload or publish flow was completed.

## Failures And Fixes

Failures/blockers:

- Physical iPhone screen recording or QuickTime video was not provided during this run.
- `idevicescreenshot` still fails with `Invalid service`.
- `devicectl` cannot drive taps, permission prompts, gallery picker, camera capture, or upload/publish interactions.
- No `PulseSocNativeUITests` target exists yet.

Fixes:

- No production code fixes were applied.
- No native feature code was changed.

## Recommendation

Do not move to Native LiveKit calls yet.

The next highest-value action is still to capture a real manual iPhone Camera Studio QA video or implement a QA-only XCTest UI target. Without video/screenshots and backend IDs, camera permission, microphone permission, gallery picker, photo/video capture, upload progress, Feed publish, Status publish, Reels publish, retry/cancel, and visual quality remain unverified.
