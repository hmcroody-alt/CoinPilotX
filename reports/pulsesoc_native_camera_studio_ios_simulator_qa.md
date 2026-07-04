# PulseSoc Native Camera Studio iOS Simulator QA

Date: 2026-07-04

## Scope

This mission tested the Native Camera Studio path through the installed `com.pulsesoc.nativeapp` development build on the iPhone 17 Pro iOS Simulator.

No LiveKit calls were built. No production WebView routes were modified. The native app remains a parallel client for the existing PulseSoc backend.

Simulator target:

- Device: iPhone 17 Pro
- UDID: `7B3BEEBC-6135-497D-91CD-A3E70C927D56`
- Runtime: iOS 26.5
- Native app identity: `com.pulsesoc.nativeapp`

## Commands And Results

Static setup:

```text
npm ci --prefix mobile-native --no-audit --no-fund --progress=false
added 811 packages in 13s

npm run --prefix mobile-native typecheck
tsc --noEmit
passed

cd mobile-native && EXPO_DOCTOR_ENABLE_DIRECTORY_CHECK=0 npx expo-doctor --verbose
17/17 checks passed. No issues detected.
```

Simulator availability:

```text
xcrun simctl list devices available | rg -n "iPhone 17 Pro|iOS 26.5|7B3BEEBC"
-- iOS 26.5 --
iPhone 17 Pro (7B3BEEBC-6135-497D-91CD-A3E70C927D56) (Booted)
```

Installed development build:

```text
cd mobile-native
npx expo run:ios --device 7B3BEEBC-6135-497D-91CD-A3E70C927D56 --no-bundler
Build Succeeded
0 error(s), and 1 warning(s)
Installing .../PulseSocNative.app
Opening on iPhone 17 Pro (com.pulsesoc.nativeapp)
```

The Xcode build emitted one simulator search-path warning for a missing Metal toolchain Swift path, but the build, signing, install, and launch all completed.

Metro/dev-client bundle:

```text
cd mobile-native
npx expo start --dev-client --localhost --port 8081 -c
Waiting on http://localhost:8081

xcrun simctl openurl 7B3BEEBC-6135-497D-91CD-A3E70C927D56 \
  'pulsesoc://expo-development-client/?url=http%3A%2F%2F127.0.0.1%3A8081'

iOS Bundled 9251ms index.ts (1546 modules)
```

Installed app container:

```text
xcrun simctl get_app_container 7B3BEEBC-6135-497D-91CD-A3E70C927D56 com.pulsesoc.nativeapp
.../PulseSocNative.app
```

Foreground/background recovery:

```text
xcrun simctl terminate 7B3BEEBC-6135-497D-91CD-A3E70C927D56 com.pulsesoc.nativeapp
xcrun simctl launch 7B3BEEBC-6135-497D-91CD-A3E70C927D56 com.pulsesoc.nativeapp
com.pulsesoc.nativeapp: 96807
```

## Simulator Evidence

Screenshots captured during the run:

- `/tmp/pulsesoc-camera-ios-sim/01-launch.png`
- `/tmp/pulsesoc-camera-ios-sim/02-bundled.png`
- `/tmp/pulsesoc-camera-ios-sim/03-camera-deeplink-signed-out.png`
- `/tmp/pulsesoc-camera-ios-sim/04-relaunch.png`
- `/tmp/pulsesoc-camera-ios-sim/05-deeplink-warning.png`
- `/tmp/pulsesoc-camera-ios-sim/06-post-routing-fix.png`

Observed states:

- Initial app frame opened from the installed build.
- Metro bundled the native app into the installed development build.
- Native login screen rendered inside `com.pulsesoc.nativeapp`, not Expo Go.
- Signed-out Camera Studio deep link remained on the auth gate.
- Foreground/background terminate and relaunch returned to the native login/auth gate.
- Production WebView routes were not touched.

## Scoped Blocker Fixed

The first signed-out Camera Studio deep-link attempt produced a React Navigation development warning:

```text
The navigation state parsed from the URL contains routes not present in the root navigator.
```

Root cause:

- The app conditionally renders `AuthNavigator` while signed out.
- `AuthNavigator` intentionally contains only `Login` and `Signup`.
- The protected Camera Studio route exists only in the signed-in app navigator.
- Parsing protected deep links while signed out produced a warning even though the user correctly stayed on the auth gate.

Fix:

- `mobile-native/App.tsx` now enables the protected linking config only after `authState.status === "signedIn"`.
- Signed-out users still land on the login screen.
- Protected deep links no longer produce the route-mismatch warning during signed-out simulator QA.
- This does not add native-only auth logic and does not bypass backend/session authority.

Remaining improvement:

- Post-login intended-route restoration is still not implemented for protected deep links opened while signed out. That should be planned separately instead of being hidden inside this QA fix.

## Test Matrix

| Area | Simulator Result | Notes |
| --- | --- | --- |
| App launch | Passed | Installed `com.pulsesoc.nativeapp` opened on the iPhone 17 Pro simulator. |
| Metro bundle | Passed | Dev-client bundle loaded through `pulsesoc://expo-development-client` and rendered native Login. |
| Login screen | Passed | Native Login rendered with PulseSoc branding, fields, sign-in button, and create-account link. |
| Session restore | Signed-out restore only | Relaunch restored the signed-out auth gate. No authenticated simulator credentials were available. |
| Logout | Not verified | Requires authenticated simulator session. |
| Camera Studio route | Auth-gate verified | Signed-out `/pulse/camera/photo?target=feed` remained protected and no longer produced the route-mismatch warning after the scoped fix. |
| Camera permission state | Not verified | Requires authenticated Camera Studio interaction; simulator cannot prove physical camera behavior. |
| Microphone permission state | Not verified | Requires authenticated video flow and physical-device follow-up. |
| Gallery fallback | Not verified | Requires authenticated Camera Studio interaction and simulator photo-library test media. |
| Preview flow | Not verified | Requires authenticated media selection/capture. |
| Caption/privacy/destination flow | Not verified | Requires authenticated Camera Studio route. |
| Upload handoff | Not verified | Requires authenticated media selection/capture and backend session. |
| Publish destination routing | Not verified | Requires authenticated publish flow. |
| Foreground/background recovery | Passed at auth gate | Terminate/relaunch returned to native Login. Authenticated Camera Studio recovery remains unverified. |
| LogiNexus visual quality | Partial pass | Login/auth gate is visually stable, dark, simple, and on-brand. Camera Studio visual quality could not be judged without authentication. |

## Warnings Observed

Metro emitted the known SDK 54 media warning:

```text
[expo-av]: Expo AV has been deprecated and will be removed in SDK 54.
Use the expo-audio and expo-video packages to replace the required functionality.
```

This warning comes from the current native media player dependency, not from Camera Studio directly. It should be tracked before media-heavy release hardening, especially Reels/Status/Live playback.

No Expo SDK/Xcode compatibility error remained after the installed development build path.

## What The Simulator Verified

- Full native build can compile for iOS Simulator under Xcode 26.6.
- `com.pulsesoc.nativeapp` installs and launches.
- Metro can bundle the installed development build without Expo Go.
- Signed-out session recovery is stable.
- Protected Camera Studio deep links do not bypass auth.
- Foreground/background relaunch at the auth gate is stable.
- The prior Expo Go overlay/toolchain blocker is resolved.

## What The Simulator Did Not Verify

- Authenticated login/session restore with a durable QA account.
- Authenticated Camera Studio route rendering.
- Camera permission allow/deny UI inside Camera Studio.
- Microphone permission allow/deny UI inside Camera Studio.
- Real camera preview.
- Photo capture.
- Video capture.
- Front/back camera switching against real hardware.
- Gallery fallback with real media.
- Preview flow after selected/captured media.
- Caption/privacy/destination publish flow.
- Upload progress, retry, cancel, and backend handoff.
- Publish to Feed, Status, Reels, Profile avatar/cover, or Messenger.
- Background recovery during capture or upload.

## What Requires Physical Device QA

Physical iPhone and Android devices are still required before release claims for:

- Real camera capture.
- Real microphone permission and audio capture.
- Front/back camera switching.
- Video duration, file size, memory pressure, orientation, and thermal behavior.
- Large image/video upload behavior over real networks.
- Push/deep-link notification taps into Camera Studio or media flows.
- Lock-screen/background behavior.
- App Store/TestFlight release confidence.

## Next Recommendation

Do not move to Native LiveKit calls yet.

Next highest-value action: create or connect a QA-safe authenticated simulator/device account, then run the authenticated Camera Studio simulator pass. After that, execute physical iPhone and Android Camera Studio QA for camera, microphone, gallery, compression, upload, and publish behavior.

Native LiveKit calls depend on the same camera/microphone/device-permission layer plus push/ringing/background behavior. Proceeding before authenticated Camera Studio QA and physical-device media QA would compound unknowns.
