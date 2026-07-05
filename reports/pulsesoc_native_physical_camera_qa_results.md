# PulseSoc Native Physical Camera Studio QA Results

Date: 2026-07-05

## Result

Status: blocked by missing local iOS code-signing identity.

This mission did not build Native LiveKit calls, did not modify production WebView routes, and did not claim physical Camera Studio behavior as verified.

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

Available test target:

- iPhone 17 Pro Simulator, iOS 26.5, UDID `7B3BEEBC-6135-497D-91CD-A3E70C927D56`.
- This is not a substitute for physical camera/microphone QA.

## Build Used

Physical device build: not installed on the connected iPhone because local iOS code signing is unavailable.

Install attempt:

```bash
cd mobile-native
npx expo run:ios --device 00008140-000E2D9A2EE8801C
```

Result:

```text
CommandError: No code signing certificates are available to use.
```

Signing check:

```bash
security find-identity -v -p codesigning
```

Result:

```text
0 valid identities found
```

No existing `.ipa`, `.app`, or `.xcarchive` artifact was found in `mobile-native/` for direct install.

Current native QA identity remains:

- `com.pulsesoc.nativeapp`

Production identity remains protected:

- `com.pulsesoc.app`

## Requested Physical QA Matrix

| Area | iPhone physical | Android physical | Result |
| --- | --- | --- | --- |
| Photo capture | Not run | Not run | iPhone blocked by missing code-signing identity; Android not connected |
| Video capture | Not run | Not run | iPhone blocked by missing code-signing identity; Android not connected |
| Front/back camera | Not run | Not run | iPhone blocked by missing code-signing identity; Android not connected |
| Microphone permission | Not run | Not run | iPhone blocked by missing code-signing identity; Android not connected |
| Gallery picker | Not run | Not run | iPhone blocked by missing code-signing identity; Android not connected |
| Large video upload | Not run | Not run | iPhone blocked by missing code-signing identity; Android not connected |
| Weak-network retry/cancel | Not run | Not run | iPhone blocked by missing code-signing identity; Android not connected |
| Upload progress accuracy | Not run | Not run | iPhone blocked by missing code-signing identity; Android not connected |
| Feed publish | Not run | Not run | iPhone blocked by missing code-signing identity; Android not connected |
| Status publish | Not run | Not run | iPhone blocked by missing code-signing identity; Android not connected |
| Reels publish | Not run | Not run | iPhone blocked by missing code-signing identity; Android not connected |
| Messenger handoff | Not run | Not run | iPhone blocked by missing code-signing identity; Android not connected |
| Profile handoff | Not run | Not run | iPhone blocked by missing code-signing identity; Android not connected |
| Foreground/background interruption | Not run | Not run | iPhone blocked by missing code-signing identity; Android not connected |
| Native visual quality on device | Not run | Not run | iPhone blocked by missing code-signing identity; Android not connected |

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

No physical-app failures were observed because the connected iPhone cannot install or run the development build until local iOS code signing is configured.

The failure for this mission is environmental:

- A trusted/paired iPhone is visible to Xcode/CoreDevice.
- Developer Mode is enabled and DDI services are available.
- Expo/Xcode cannot install `com.pulsesoc.nativeapp` because no local code-signing identity is available.
- `security find-identity -v -p codesigning` returns `0 valid identities found`.
- No Android device was visible to adb.

## Fixes Applied

No code fixes were applied during this physical QA run because the app could not be installed or launched on physical hardware.

The previous checkpoint already added upload progress observability for large media by showing percent plus transferred/total size when XHR provides computable upload length.

## Required Next Setup

iPhone:

1. Keep the iPhone connected and unlocked.
2. Configure iOS code signing through one of these safe paths:
   - Sign into Xcode with an Apple developer account and let Xcode create an Apple Development certificate.
   - Import an existing Apple Development certificate and matching private key into the login keychain.
   - Use an EAS development build signed for `com.pulsesoc.nativeapp` and this device.
3. Confirm a valid signing identity exists:

```bash
security find-identity -v -p codesigning
```

4. Re-run:

```bash
xcrun devicectl list devices
xcrun xctrace list devices
xcrun devicectl device info details --device F45E640F-6D02-514E-877C-B764E8D6818F
```

5. Continue only when `developerModeStatus` is enabled, `ddiServicesAvailable` is true, and at least one valid Apple Development signing identity exists.

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

The next highest-value action is to configure iOS code signing for `com.pulsesoc.nativeapp`, install the development build on the connected iPhone 16 Pro, then rerun the physical Camera Studio QA plan. Specifically verify camera/microphone permissions, gallery picker behavior, large-video upload progress, weak-network retry/cancel, foreground/background recovery, and Feed/Status/Reels publish routing on hardware.
