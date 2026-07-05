# PulseSoc Native XCTest Camera Studio QA

Status: QA-only XCTest UI automation has been added and verified at the Xcode level for the parallel native app identity.

The XCTest path targets `com.pulsesoc.nativeapp` only. It does not target, modify, or replace the production WebView app identity `com.pulsesoc.app`.

## Scope

Mission:

- Add a QA-only XCTest UI automation path for Camera Studio.
- Keep the path dev/native/local QA only.
- Do not weaken production auth.
- Do not modify production WebView routes.
- Do not change production app identity.
- Prepare automation for launch, authentication, Camera Studio routing, mode checks, permission states, screenshots, logs, and backend ID capture.

## Files Added

- `mobile-native/ios/PulseSocNativeUITests/Info.plist`
- `mobile-native/ios/PulseSocNativeUITests/PulseSocNativeCameraStudioQATests.swift`
- `scripts/pulsesoc_native_xctest_camera_qa_audit.py`

Files updated:

- `mobile-native/ios/PulseSocNative.xcodeproj/project.pbxproj`
- `mobile-native/ios/PulseSocNative.xcodeproj/xcshareddata/xcschemes/PulseSocNative.xcscheme`
- `reports/pulsesoc_native_progress.md`

## QA-only Boundaries

The UI test bundle:

- Uses `XCUIApplication(bundleIdentifier: "com.pulsesoc.nativeapp")`.
- Builds as `PulseSocNativeUITests.xctest`.
- Uses its own bundle ID: `com.pulsesoc.nativeapp.uitests`.
- Targets the native app target `PulseSocNative`.
- Does not add backend routes.
- Does not add a production auth bypass.
- No production auth weakening was introduced.
- Does not add or modify WebView production routes.
- Does not change `com.pulsesoc.app`.

The existing native QA auth helper remains gated to:

- `__DEV__`
- native platforms only
- local API bases only: `localhost`, `127.0.0.1`, or `::1`

## XCTest Coverage Prepared

The XCTest automation can:

- Launch `com.pulsesoc.nativeapp`.
- Attempt existing login with `PULSESOC_QA_IDENTIFIER` and `PULSESOC_QA_PASSWORD` if provided.
- Attempt Camera Studio route access through a restored session, visible UI entry point, or `PULSESOC_QA_CAMERA_DEEPLINK`.
- Verify Camera Studio renders through one of:
  - `PulseSoc Camera`
  - `Camera permission needed`
  - `Camera preview requires a device build`
- Tap existing Camera Studio controls where the route is reachable:
  - `Feed`
  - `Status`
  - `Reel`
  - `Photo`
  - `Video`
  - `Allow Camera`
  - `Mic` / `Muted`
  - `Gallery`
  - `Snap`
  - `Record`
  - `Stop`
  - `Flip`
  - `Publish`
- Capture `XCTAttachment` screenshots at each major checkpoint.
- Surface permission prompt state where iOS exposes it to XCTest.
- Skip with a clear setup reason when Camera Studio cannot be reached due to missing QA session/deep-link setup.

## Commands

Build the UI test bundle:

```bash
xcodebuild build-for-testing \
  -workspace mobile-native/ios/PulseSocNative.xcworkspace \
  -scheme PulseSocNative \
  -destination 'id=7B3BEEBC-6135-497D-91CD-A3E70C927D56' \
  -only-testing:PulseSocNativeUITests/PulseSocNativeCameraStudioQATests
```

Run the UI test:

```bash
xcodebuild test \
  -workspace mobile-native/ios/PulseSocNative.xcworkspace \
  -scheme PulseSocNative \
  -destination 'id=7B3BEEBC-6135-497D-91CD-A3E70C927D56' \
  -only-testing:PulseSocNativeUITests/PulseSocNativeCameraStudioQATests
```

Run with QA credentials and Camera Studio deep link when a local QA backend/session is available:

```bash
PULSESOC_QA_IDENTIFIER='qa@example.test' \
PULSESOC_QA_PASSWORD='replace-me' \
PULSESOC_QA_CAMERA_DEEPLINK='pulsesoc://qa/simulator-login?identifier=qa@example.test&password=replace-me&redirect=/pulse/camera/photo&target=feed&mode=photo' \
xcodebuild test \
  -workspace mobile-native/ios/PulseSocNative.xcworkspace \
  -scheme PulseSocNative \
  -destination 'id=7B3BEEBC-6135-497D-91CD-A3E70C927D56' \
  -only-testing:PulseSocNativeUITests/PulseSocNativeCameraStudioQATests
```

The deep-link auth path above is only valid when the native app is a development build using a local QA API base. It is intentionally disabled for production API bases.

## Verification Results

`xcodebuild -showdestinations`:

- Passed.
- Confirmed the `PulseSocNative` scheme can see the iPhone 17 Pro simulator and connected physical iPhone destinations.

`xcodebuild build-for-testing`:

- Passed after removing an invalid `XCUIApplication.bundleID` assertion.
- Confirmed the `PulseSocNativeUITests` target compiles.
- Confirmed the scheme builds the native app and UI test bundle for testing.

`xcodebuild test`:

- Passed with one skipped test.
- The test launched `com.pulsesoc.nativeapp`.
- The test added screenshot attachments:
  - `PulseSocNative-CameraStudio-01-app-launch`
  - `PulseSocNative-CameraStudio-02-camera-studio-open`
  - `PulseSocNative-CameraStudio-02-camera-studio-route-blocked`
- The test skipped because Camera Studio was not reached without a restored QA session, QA credentials, or a QA Camera Studio deep link against the dev/local QA auth path.
- Result bundle:
  - `/Users/hmcherie/Library/Developer/Xcode/DerivedData/PulseSocNative-feohqctfpnejrhcbaicgskfhmavf/Logs/Test/Test-PulseSocNative-2026.07.05_15-50-03--0400.xcresult`

The skip is intentional and honest. It proves the XCTest harness can build, launch, screenshot, and report setup blockers, but it does not prove Camera Studio controls passed end-to-end.

## What XCTest Can Verify

With a valid QA session or local QA deep link, XCTest can verify:

- App launch.
- Auth/login screen interaction.
- Camera Studio route rendering.
- Camera mode buttons.
- Destination buttons.
- Permission prompt visibility where iOS exposes it.
- Gallery picker entry point.
- Preview and publish control visibility.
- Screenshot evidence through `.xcresult` attachments.

## What XCTest Cannot Fully Verify Yet

Still requires physical-device QA or additional test harness work:

- True physical camera image quality.
- True microphone capture quality.
- Large real video memory pressure.
- Weak-network retry/cancel behavior.
- Backend media/upload IDs unless the test reaches publish with QA credentials.
- Published Feed/Status/Reels IDs unless the test reaches publish.
- Production APNs/FCM notification behavior.
- Lock-screen behavior.
- Native LiveKit calls.

## Backend media/upload/published ID capture plan

This is the backend media/upload/published ID capture plan.

When the XCTest reaches authenticated publish flows:

1. Use the existing native Camera Studio APIs and backend behavior:
   - `/api/pulse/media/upload`
   - `/api/pulse/camera/preview`
   - `/api/pulse/posts/create-from-camera`
   - `/api/pulse/reels/create-from-camera`
   - existing Status APIs
2. Keep the backend authoritative for media IDs, preview tokens, post IDs, status IDs, and reel IDs.
3. Capture IDs from:
   - XCTest screenshots of destination detail screens.
   - Metro/native logs where request payloads are safe to log.
   - backend QA logs for `/api/pulse/media/upload`, preview, and publish endpoints.
4. Update the physical Camera Studio QA reports with:
   - media ID
   - upload ID or media record ID
   - preview token if available
   - published post ID
   - published status ID
   - published reel ID

## Current Blocker

The automation path exists and runs, but the current pass did not provide the required QA session/deep-link credentials to reach Camera Studio.

Until that is supplied, XCTest can only prove:

- the UI test target builds,
- the app launches,
- screenshots can be attached,
- missing QA auth/route setup is reported as a skip.

## Recommendation

Do not move to Native LiveKit calls yet.

The next highest-value action is to run this XCTest path with a valid local QA backend/session and `PULSESOC_QA_CAMERA_DEEPLINK`, then collect the `.xcresult` screenshots plus backend media/upload/published IDs. If route access is still unreliable, add scoped native accessibility identifiers to Camera Studio controls in a separate QA-hardening change.
