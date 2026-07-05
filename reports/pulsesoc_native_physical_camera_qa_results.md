# PulseSoc Native Physical Camera Studio QA Results

Date: 2026-07-05

## Result

Status: physical install and launch verified; physical Camera Studio media interaction remains unverified.

This mission did not build Native LiveKit calls, did not modify production WebView routes, and did not claim physical Camera Studio media behavior as verified without evidence.

## Device Detection

Physical iPhone:

- Connected device: `P3r7or`
- Model: iPhone 16 Pro (`iPhone17,1`)
- OS: iOS 18.7.3 (`22H217`)
- UDID: `00008140-000E2D9A2EE8801C`
- CoreDevice identifier: `F45E640F-6D02-514E-877C-B764E8D6818F`
- Transport: wired
- Pairing state: paired
- `xcrun xctrace list devices` lists `P3r7or (18.7.3) (00008140-000E2D9A2EE8801C)`.
- `xcrun devicectl list devices` lists `P3r7or` as `connected`.
- `xcrun devicectl device info details --device 00008140-000E2D9A2EE8801C` reports `developerModeStatus: enabled` and `ddiServicesAvailable: true`.

Physical Android:

- `adb devices -l` returned an empty attached-device list.
- No Android device was visible to adb.

## Signing State

WWDR G3 installation resolved the local identity blocker.

```bash
security find-identity -v -p codesigning
```

Result:

```text
1) 9AA0603693FED4F7038C1A975B3D3B4595FC4647 "Apple Development: ROODY CHERIE (HB5FV6P922)"
2) 6E0B7551E4E8509D779AFE96AA1F96E5D3DEAE6F "Apple Development: ROODY CHERIE (HB5FV6P922)"
2 valid identities found
```

The duplicate Apple Development identities remain, but Xcode/Expo selected a valid identity and completed the build.

## Build, Install, Launch

Command run:

```bash
cd mobile-native
npx expo run:ios --device 00008140-000E2D9A2EE8801C
```

Result:

```text
› Signing and building iOS app with: Apple Development: ROODY CHERIE (HB5FV6P922)
› Build Succeeded
› Installing /Users/hmcherie/Library/Developer/Xcode/DerivedData/PulseSocNative-.../PulseSocNative.app
```

The first automatic launch failed because the device locked during the final launch step:

```text
CommandError: Cannot launch PulseSocNative on P3r7or because the device is locked.
```

After confirming the device was available, the installed app was launched directly:

```bash
xcrun devicectl device process launch --device 00008140-000E2D9A2EE8801C com.pulsesoc.nativeapp
```

Result:

```text
Launched application with com.pulsesoc.nativeapp bundle identifier.
```

Installed app inventory:

```text
PulseSoc Native   com.pulsesoc.nativeapp   0.1.0   1
```

Running process evidence:

```text
/private/var/containers/Bundle/Application/.../PulseSocNative.app/PulseSocNative
```

Camera Studio deep-link process launch:

```bash
xcrun devicectl device process launch --device 00008140-000E2D9A2EE8801C --terminate-existing --payload-url 'pulsesoc://pulse/camera/photo?target=feed' com.pulsesoc.nativeapp
```

Result:

```text
Launched application with com.pulsesoc.nativeapp bundle identifier.
```

Metro physical bundle evidence:

```text
iOS Bundled 7080ms index.ts (1554 modules)
```

Warning observed:

```text
[expo-av]: Expo AV has been deprecated and will be removed in SDK 54.
```

The Expo AV warning is not a Camera Studio blocker, but it should remain on the native technical-debt list because Reels/Status/Live viewer surfaces still use native video playback.

## Requested Physical QA Matrix

| Area | iPhone physical | Android physical | Result |
| --- | --- | --- | --- |
| App install | Passed | Not run | iPhone install passed; Android not connected |
| App launch | Passed | Not run | iPhone launch passed through `devicectl`; Android not connected |
| JS bundle load | Passed | Not run | Metro bundled `index.ts` for iOS after launch |
| Camera Studio deep link | Passed at process level | Not run | `pulsesoc://pulse/camera/photo?target=feed` launch accepted |
| Login/session restore | Not observed | Not run | Requires on-device visual/manual QA or physical-device automation |
| Camera permission | Not observed | Not run | Requires on-device visual/manual QA |
| Microphone permission | Not observed | Not run | Requires on-device visual/manual QA |
| Gallery picker | Not observed | Not run | Requires on-device visual/manual QA |
| Photo capture | Not observed | Not run | Requires on-device visual/manual QA |
| Video capture | Not observed | Not run | Requires on-device visual/manual QA |
| Front/back camera | Not observed | Not run | Requires on-device visual/manual QA |
| Large video upload | Not observed | Not run | Requires on-device visual/manual QA and large fixture |
| Weak-network retry/cancel | Not observed | Not run | Requires on-device visual/manual QA and weak network setup |
| Upload progress accuracy | Not observed | Not run | Requires on-device visual/manual QA |
| Feed publish | Not observed | Not run | Requires authenticated on-device QA |
| Status publish | Not observed | Not run | Requires authenticated on-device QA |
| Reels publish | Not observed | Not run | Requires authenticated on-device QA |
| Messenger handoff | Not observed | Not run | Requires authenticated on-device QA |
| Profile handoff | Not observed | Not run | Requires authenticated on-device QA |
| Foreground/background interruption | Process launch verified only | Not run | Full recovery requires on-device visual/manual QA |
| Native visual quality on device | Not observed | Not run | Requires screenshot/screen-view/manual evidence |

## Captured iPhone Camera Studio QA Attempt

Status: machine-captured launch, bundle, deep-link, display, process, and syslog evidence was collected. Physical Camera Studio interaction remains unverified.

Captured evidence:

- iPhone display was active, portrait, and unlocked since boot.
- Metro bundled `index.ts` for iOS after app launch and Camera Studio deep-link launch.
- `xcrun devicectl device process launch` succeeded for `com.pulsesoc.nativeapp`.
- `xcrun devicectl device process launch --payload-url 'pulsesoc://pulse/camera/photo?target=feed'` succeeded.
- `xcrun devicectl device info processes` listed `PulseSocNative` as PID `952`.
- `idevicesyslog` included `fgApp: com.pulsesoc.nativeapp` and `FBWorkspace (ForegroundFocal)` for `com.pulsesoc.nativeapp`.
- `idevicesyslog` also showed `CameraException(None)`, but the camera service remained cold: `Cam(Cold:<private>)`.

Screenshot/video evidence:

- No screenshot or video evidence was captured.
- `idevicescreenshot` still failed with `Could not start screenshotr service: Invalid service`.

Backend evidence:

- No backend media/upload/post/status/reel IDs were produced because no authenticated physical Camera Studio media upload or publish flow was completed.

Conclusion:

- App launch, bundle load, foreground process, and Camera Studio deep-link dispatch are physically evidenced.
- Login/session restore, camera/microphone permission prompts, gallery picker, photo/video capture, preview, upload progress, Feed publish, Status publish, Reels publish, retry/cancel, and visual quality remain unverified.

## What Remains Verified From Earlier Gates

Simulator QA has already verified:

- Installed dev build launch.
- Authenticated Camera Studio access through a QA-only localhost deep link.
- Camera Studio route access for Feed, Status, and Reels.
- Simulator media injection and selected-media preview.
- Upload handoff through `/api/pulse/media/upload`.
- Preview handoff through `/api/pulse/camera/preview`.
- Feed publish through `/api/pulse/posts/create-from-camera`.
- Status publish through existing Status APIs.
- Reel publish through `/api/pulse/reels/create-from-camera`.
- Foreground/background session recovery in simulator.
- iPhone 17 Pro simulator safe-area visual hardening.

These remain simulator-verification results only.

## Failures Found

No Camera Studio media-flow failures were observed because physical camera/media interaction could not be driven or observed from the available tooling.

Resolved blockers:

- A trusted/paired iPhone is visible to Xcode/CoreDevice.
- Developer Mode is enabled and DDI services are available.
- Local codesigning identities are valid after WWDR G3 installation.
- `npx expo run:ios --device 00008140-000E2D9A2EE8801C` built and installed the app.
- `xcrun devicectl device process launch` launched `com.pulsesoc.nativeapp`.

Remaining blockers:

- This environment has no reliable physical iPhone screen capture/touch automation path configured.
- `devicectl` can launch the app and pass a payload URL, but it does not provide tap/camera/gallery automation.
- No on-device screenshots/videos or manual pass/fail observations were captured for camera permission, microphone permission, gallery picker, capture, upload, retry/cancel, or publish flows.
- No Android device was visible to adb.

## Fixes Applied

No production code fixes were applied during this physical QA run.

The previous checkpoint already added upload progress observability for large media by showing percent plus transferred/total size when XHR provides computable upload length.

## Required Next Setup

iPhone:

1. Keep the iPhone connected, unlocked, and awake.
2. Keep Metro running in LAN mode:

```bash
cd mobile-native
EXPO_PUBLIC_PULSE_API_BASE_URL=https://pulsesoc.com npx expo start --dev-client --host lan --port 8081
```

3. Launch the installed app:

```bash
xcrun devicectl device process launch --device 00008140-000E2D9A2EE8801C --terminate-existing com.pulsesoc.nativeapp
```

4. Perform the on-device checklist manually or with a real device automation tool:
   - Login/session restore.
   - Camera permission allow/deny.
   - Microphone permission allow/deny.
   - Gallery picker.
   - Photo capture.
   - Video capture.
   - Front/back camera switch.
   - Upload progress.
   - Feed/Status/Reels publish.
   - Retry/cancel.
   - Foreground/background recovery.
   - Native visual quality.

Android:

1. Enable Developer Options.
2. Enable USB debugging.
3. Connect the device by USB.
4. Accept the RSA debugging prompt.
5. Confirm it appears in:

```bash
adb devices -l
```

## Required Evidence For The Next Attempt

For each physical device:

- Device model.
- OS version.
- App build identity and build number.
- API base URL.
- Screenshots or short videos of permission prompts, camera preview, gallery picker, upload progress, cancel/retry, and publish routing.
- Backend media IDs and destination IDs for Feed, Status, and Reel publishes.
- Device logs for failed uploads or permission issues.
- Clear separation of iPhone physical, Android physical, simulator, and browser results.

## Recommendation

Do not move to Native LiveKit calls yet.

The next highest-value action is to capture a real manual iPhone Camera Studio QA video or implement a QA-only XCTest UI target. Specifically verify login/session restore, camera/microphone permissions, gallery picker behavior, photo/video capture, large-video upload progress, weak-network retry/cancel, foreground/background recovery, Feed/Status/Reels publish routing, backend IDs, and visual quality on hardware.
