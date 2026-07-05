# PulseSoc Native Physical Camera Studio QA Results

Date: 2026-07-05

## Result

Status: blocked by missing physical device access.

This mission did not build Native LiveKit calls, did not modify production WebView routes, and did not claim physical Camera Studio behavior as verified.

## Device Detection

Physical iPhone:

- `xcrun xctrace list devices` listed the Mac and simulators only.
- `xcrun devicectl list devices` returned `No devices found.`
- `system_profiler SPUSBDataType` did not show an attached iPhone or iPad.

Physical Android:

- `adb devices -l` returned an empty attached-device list.
- `system_profiler SPUSBDataType` did not show an attached Android device.

Available test target:

- iPhone 17 Pro Simulator, iOS 26.5, UDID `7B3BEEBC-6135-497D-91CD-A3E70C927D56`.
- This is not a substitute for physical camera/microphone QA.

## Build Used

Physical device build: not installed because no physical iPhone or Android device was visible to the machine.

Current native QA identity remains:

- `com.pulsesoc.nativeapp`

Production identity remains protected:

- `com.pulsesoc.app`

## Requested Physical QA Matrix

| Area | iPhone physical | Android physical | Result |
| --- | --- | --- | --- |
| Photo capture | Not run | Not run | Blocked by no physical device |
| Video capture | Not run | Not run | Blocked by no physical device |
| Front/back camera | Not run | Not run | Blocked by no physical device |
| Microphone permission | Not run | Not run | Blocked by no physical device |
| Gallery picker | Not run | Not run | Blocked by no physical device |
| Large video upload | Not run | Not run | Blocked by no physical device |
| Weak-network retry/cancel | Not run | Not run | Blocked by no physical device |
| Upload progress accuracy | Not run | Not run | Blocked by no physical device |
| Feed publish | Not run | Not run | Blocked by no physical device |
| Status publish | Not run | Not run | Blocked by no physical device |
| Reels publish | Not run | Not run | Blocked by no physical device |
| Messenger handoff | Not run | Not run | Blocked by no physical device |
| Profile handoff | Not run | Not run | Blocked by no physical device |
| Foreground/background interruption | Not run | Not run | Blocked by no physical device |
| Native visual quality on device | Not run | Not run | Blocked by no physical device |

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

No physical-app failures were observed because no physical device was available.

The failure for this mission is environmental:

- No trusted iPhone was visible to Xcode/devicectl.
- No Android device was visible to adb.

## Fixes Applied

No code fixes were applied during this physical QA run because the app could not be launched on physical hardware.

The previous checkpoint already added upload progress observability for large media by showing percent plus transferred/total size when XHR provides computable upload length.

## Required Next Setup

iPhone:

1. Connect a real iPhone by USB or configure trusted wireless debugging.
2. Unlock the device.
3. Tap Trust This Computer if prompted.
4. Confirm it appears in:

```bash
xcrun devicectl list devices
xcrun xctrace list devices
```

Android:

1. Enable Developer Options.
2. Enable USB debugging.
3. Connect the device by USB.
4. Accept the RSA debugging prompt.
5. Confirm it appears in:

```bash
adb devices -l
```

## Commands To Run Once A Device Is Visible

iPhone development build:

```bash
cd mobile-native
EXPO_PUBLIC_PULSE_API_BASE_URL=<QA_API_BASE_URL> npx expo run:ios --device <DEVICE_ID>
```

Android development build:

```bash
cd mobile-native
EXPO_PUBLIC_PULSE_API_BASE_URL=<QA_API_BASE_URL> npx expo run:android --device <DEVICE_ID>
```

Metro for installed development build:

```bash
cd mobile-native
EXPO_PUBLIC_PULSE_API_BASE_URL=<QA_API_BASE_URL> npx expo start --dev-client --localhost
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

The next highest-value action is to connect and trust at least one real iPhone or Android device, rerun the physical Camera Studio QA plan, and specifically verify camera/microphone permissions, gallery picker behavior, large-video upload progress, weak-network retry/cancel, and Feed/Status/Reels publish routing on hardware.
