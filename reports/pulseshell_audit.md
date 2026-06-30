# PulseShell + Apple Review Readiness Audit

Date: 2026-06-30

## Scope

This audit covers the current PulseSoc hybrid shell foundation in:

- `mobile/pulse-react-native/App.tsx`
- `mobile/pulse-react-native/app.json`
- `mobile/pulse-react-native/components/NativeLiveBroadcast.tsx`
- `mobile/pulse-react-native/services/push.ts`
- `static/js/pulseshell_bridge.js`
- PulseSoc web shell script includes in `bot.py`

This pass does not add product features. It strengthens the native bridge contract, fallback behavior, performance mode hooks, and App Review readiness documentation.

## Current WebView limitations found

- The native app already provides a real WebView shell, push registration, notification deep-link routing, native share, offline fallback, and Native Live broadcast screen.
- The website did not have a stable `window.PulseShell` API contract for checking native bridge availability.
- Browser sessions had no shared safe fallback for native-only calls, which creates risk of future dead buttons if web code assumes native support.
- iOS camera and microphone permission copy did not explicitly mention Live co-host sessions.
- App Review readiness existed in prior metadata, but there was no PulseShell-specific review notes template tied to this bridge mission.
- Real-device QA remains required for camera, microphone, push, file picker, haptics, deep links, and long Live sessions.

## PulseShell modules added or strengthened

Implemented now:

- `DeviceInfoBridge`: `window.PulseShell.device.getInfo()`
- `PermissionBridge`: `window.PulseShell.permissions.check()` and `window.PulseShell.permissions.request()`
- `CameraBridge`: `window.PulseShell.camera.requestPermission()`
- `MicrophoneBridge`: `window.PulseShell.microphone.requestPermission()`
- `LiveStreamingBridge`: `window.PulseShell.live.startHostSession()`
- `PushNotificationBridge`: `window.PulseShell.push.registerDevice()`
- `ShareBridge`: `window.PulseShell.share.openNativeShareSheet()`
- `FilePickerBridge`: `window.PulseShell.filePicker.open()`
- `HapticsBridge`: `window.PulseShell.haptics.impact()`
- `DeepLinkBridge`: `window.PulseShell.deepLinks.open()`
- `PerformanceBridge`: `window.PulseShell.performance.getMode()` and `window.PulseShell.performance.setMode()`
- `SafeAreaBridge`: `window.PulseShell.safeArea.getInsets()`

Planned or safely unavailable:

- `BackgroundAudioBridge`
- `PaymentBridge`
- `OfflineCacheBridge`
- `CrashRecoveryBridge` beyond the current native offline fallback
- Full native `KeyboardBridge`

Unavailable modules return structured unavailable results. They do not fake success.

## Native shell files changed

- `mobile/pulse-react-native/App.tsx`
- `mobile/pulse-react-native/app.json`

## JS bridge API added

- Native-injected bridge in `App.tsx`
- Browser fallback in `static/js/pulseshell_bridge.js`
- Web shell includes in `bot.py`

The native bridge uses request IDs and dispatches `PulseShellNativeResult` back to the web layer. Web code receives confirmed success, confirmed unavailable, or timeout/error states.

## iOS permission strings verified

Updated:

- `NSCameraUsageDescription`
- `NSMicrophoneUsageDescription`
- `NSPhotoLibraryUsageDescription`
- `NSPhotoLibraryAddUsageDescription`

The camera/microphone strings now explicitly cover going live and joining co-host sessions.

## Android permissions verified

Current Android permissions remain scoped to app behavior:

- `CAMERA`
- `RECORD_AUDIO`
- `READ_MEDIA_IMAGES`
- `READ_MEDIA_VIDEO`
- `POST_NOTIFICATIONS`

No server secrets or stream keys are exposed in the mobile source.

## Live camera/mic improvements

This pass exposes native permission helpers and keeps the existing `NativeLiveBroadcast` LiveKit/VisionCamera path. It does not claim full freeze recovery real-device validation. Existing browser Live freeze guardrails remain covered by `scripts/live_camera_freeze_audit.py`.

## Push/deep-link behavior

Existing push behavior remains:

- Expo push token request on physical devices.
- Token registration to `/api/push/subscribe` through the WebView session.
- Notification tap deep links route back into PulseSoc.
- Message notifications route to conversation-specific destinations where available.

PulseShell now exposes `window.PulseShell.push.registerDevice()` and does not return the push token to web callers.

## Native UX bridges

Implemented or preserved:

- Native splash screen through Expo config.
- Native share sheet.
- Native file picker via Expo Image Picker.
- Native haptics using device vibration.
- Safe-area handling through `react-native-safe-area-context`.
- Hardware back behavior on Android.
- Offline fallback screen.
- Pull-to-refresh.

## Performance and futuristic city strategy

Implemented:

- `window.PulseShell.performance.getMode()`
- `window.PulseShell.performance.setMode()`
- Browser fallback auto-selects reduced-motion, low-end, battery-saver, or balanced using media query, device memory, and connection hints when available.
- Native shell dispatches performance mode changes to the WebView.

Strategy:

- Keep futuristic effects CSS-first and throttled.
- Reduce/disable atmospheric effects in reduced-motion, low-end, and battery-saver modes.
- Avoid constant large-container blur, unbounded canvas loops, and always-on heavy particle systems.
- Use transforms and opacity over layout animations.

## Apple Review risks found

- Real reviewer credentials still must be entered in App Store Connect for a submission.
- Native real-device QA remains required before claiming Apple Review readiness.
- Payment/IAP compliance must be rechecked before enabling any paid digital content in iOS.
- Push delivery and notification tap routing require physical-device proof.
- Live co-hosting requires two-account physical-device proof.

## App Review notes drafted

See:

- `mobile/pulse-react-native/store-metadata/en-US/app-review-notes-pulseshell.md`

## Test accounts needed

Before submission, prepare non-admin accounts only:

- Standard reviewer account
- Host-capable reviewer account
- Viewer/co-host requester account

Do not include admin, owner, billing, or production-secret credentials in source control.

## QA results

Static QA performed in this pass:

- Python compile/audit commands are expected through `scripts/pulseshell_app_review_audit.py`.
- JavaScript syntax check is expected for `static/js/pulseshell_bridge.js`.
- Existing mobile and native Live audits should continue to pass.

Not completed in this pass:

- iPhone physical-device QA
- Android physical-device QA
- WebView build QA
- Push delivery live test
- Camera/mic long-session thermal and background/foreground QA
- Two-device Live co-host QA

## Known limitations

- PulseShell is a bridge foundation, not a full native rewrite.
- Several bridge modules are safely unavailable until dedicated native releases implement them.
- No App Store submission was performed.
- No fake native capability was added.
