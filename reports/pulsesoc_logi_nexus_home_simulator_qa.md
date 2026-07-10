# PulseSoc LogiNexus Home Simulator QA

Status: completed for this milestone with a documented app-wide warning overlay.

## Target

- Primary QA target: Xcode iPhone Simulator.
- Available simulator used: iPhone 17 Pro, iOS 26.5, UDID `7B3BEEBC-6135-497D-91CD-A3E70C927D56`.
- iPhone 16 Pro simulator was not available in the local simulator list, so iPhone 17 Pro was used.

## Expected Checks

- Native app launches as `com.pulsesoc.nativeapp`.
- Home route renders.
- Top command strip stays within safe area.
- Pulse Network hero fits on narrow iPhone width without clipping.
- Status rail remains readable.
- Transmission Console and feed tabs remain reachable.
- First feed card renders without overlap.
- Bottom dock does not permanently hide publish/feed controls.

## Evidence

Final simulator evidence:

- `reports/screenshots/logi-nexus-home-iphone17pro-reconstructed-home-final-clean.png`
- `reports/screenshots/logi-nexus-home-iphone17pro-native-runtime-clean-login.png`

## Result

- Native runtime launches after a fresh Metro cache rebuild.
- Authenticated native app state was restored through the local QA account.
- Native Home renders with the reconstructed command strip, compact Pulse Network hero, Status rail, Transmission Console entry, and shared bottom dock.
- The Expo Notifications `@ide/backoff` runtime resolver issue was fixed by adding a native Metro resolver alias to the installed transitive dependency.
- The previous redbox is no longer present.

## Remaining Simulator Caveat

- The dev-client displays an app-wide `Open debugger to view warnings` toast caused by existing warnings. Metro shows the warning source as the existing `expo-av` deprecation notice. This is not a Home layout failure and was not suppressed in this mission.
- The warning toast partially covers the bottom composer/dock in the screenshot; final release/device QA should use a production/dev build without the dev-client warning overlay.

## Limitations

- Simulator QA does not prove haptics, APNs tap behavior, physical camera/microphone capture, Bluetooth/audio routing, or physical-device performance.
