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
- `reports/screenshots/logi-nexus-home-iphone17pro-size-feed-visible.png`
- `reports/screenshots/logi-nexus-home-iphone17pro-size-final-after-npm-ci.png`
- `reports/screenshots/logi-nexus-home-iphone17pro-blueprint-final.png`

## Result

- Native runtime launches after a fresh Metro cache rebuild.
- Authenticated native app state was restored through the local QA account.
- Native Home renders with the reconstructed command strip, compact Pulse Network hero, Status rail, Transmission Console entry, and shared bottom dock.
- A follow-up iPhone sizing pass reduced the command strip, Pulse Network hero, Status rail, quick-action chips, composer, and bottom dock proportions so the first viewport now shows the feed section beginning below the compact composer.
- A blueprint-inspired reconstruction pass was verified through the Xcode iPhone 17 Pro Simulator using the installed `com.pulsesoc.nativeapp` development build and a fresh Metro bundle.
- The correct custom-scheme route for Home simulator QA is `pulsesoc:///pulse`; the two-slash form can be interpreted as a host and preserve the previous screen context.
- The verified Home render shows the command strip, compact Pulse Network hero, Your Orbit rail, Transmission Console, compact feed filter rail, and the first Signal Card beginning below the fold.
- The Expo Notifications `@ide/backoff` runtime resolver issue was fixed by adding a native Metro resolver alias to the installed transitive dependency.
- The previous redbox is no longer present.

## Remaining Simulator Caveat

- The dev-client displays an app-wide `Open debugger to view warnings` toast caused by existing warnings. Metro shows the warning source as the existing `expo-av` deprecation notice. This is not a Home layout failure and was not suppressed in this mission.
- The warning toast partially covers the bottom composer/dock in the screenshot; final release/device QA should use a production/dev build without the dev-client warning overlay.

## Limitations

- Simulator QA does not prove haptics, APNs tap behavior, physical camera/microphone capture, Bluetooth/audio routing, or physical-device performance.
