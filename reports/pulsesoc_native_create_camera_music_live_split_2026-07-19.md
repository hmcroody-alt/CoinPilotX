# PulseSoc Native Create / Camera / Music / Live Split

Date: 2026-07-19

## Navigation flow

- Reels top-right `+` now opens the Home Create composer in Reel mode.
- Global bottom Create already opens the Home Create composer.
- The Home composer opens `CameraStudio` only when the user explicitly taps Camera.
- Camera receives `returnToComposer: true` only from the Create composer path.
- Existing direct Camera routes still keep the older capture review/publish panel to avoid breaking deep links.

## Create composer

- Primary destinations are now Feed, Status, and Reel.
- Composer includes caption/context, audience selector, Gallery, Video, Music, Camera, selected media queue, and publish.
- Composer no longer exposes a fake Live publish mode.
- Composer persists draft state before opening Camera so caption, destination, audience, selected media, and selected music survive navigation.
- Captured media is consumed from a persisted camera handoff and restored into the existing native media queue.

## Camera capture

- Camera has a dedicated full-screen capture surface for composer-launched capture.
- Composer-launched Camera hides destination selection, caption input, audience picker, and Publish controls.
- Photo and Video capture return media to the composer instead of publishing immediately.
- Old non-composer Camera flows still support review/publish for feed/status/reel/avatar/cover/message routes.

## Music reuse

- Music continues to use the existing native `MusicScreen` and `mobile-native/src/api/music.ts` API.
- Full Music selection returns to the Home composer with Feed/Status/Reel destination preserved.
- Composer inline music search uses approved backend suggestions and does not duplicate the catalog.
- Music-only Status is allowed through the existing Status backend path; Feed/Reel still require media when music is attached.

## Live readiness

- The Camera mode selector now includes Live.
- Native Camera does not fake a broadcast or request a native host publishing token.
- Live mode displays an explicit readiness message and opens the existing production Live Studio fallback.
- Production backend evidence exists for `/api/pulse/live/start`, LiveKit token, browser publish, Mux egress, and co-host request flows, but native Camera host publishing remains unverified.

## QA evidence

- Passed:
  - `npm --prefix mobile-native run typecheck`
  - `npm --prefix mobile-native test -- --runTestsByPath src/create/__tests__/createComposerHandoff.test.ts --runInBand`
  - `.venv/bin/python -m py_compile scripts/pulsesoc_native_create_camera_music_live_split_audit.py`
  - `.venv/bin/python scripts/pulsesoc_native_create_camera_music_live_split_audit.py`
  - `git diff --check -- <mission files>`
- Xcode simulator/device evidence:
  - Booted simulator found: `PulseSoc iPhone 16 Pro (C980AEE0-2D07-4D98-8A37-D0447A6A908B)`.
  - Physical iPhone found: `P3r7or (F45E640F-6D02-514E-877C-B764E8D6818F)`, iPhone 16 Pro.
  - Workspace build used the `mobile-native/ios/PulseSocNative.xcworkspace` entrypoint and produced simulator/device app bundles.
  - Simulator install and launch passed: `xcrun simctl install ...` and `xcrun simctl launch ... com.pulsesoc.nativeapp.dev`.
  - Physical iPhone install and launch passed: `xcrun devicectl device install app ...` and `xcrun devicectl device process launch ... com.pulsesoc.nativeapp.dev`.

## Known limitations

- Native Live host publishing is intentionally not implemented in Camera because the verified production host flow is still the web Live Studio / LiveKit / Mux path.
- Physical iPhone build/install/launch passed on the paired iPhone 16 Pro; full manual camera capture still requires user-side camera permission interaction on the device.
- The direct legacy Camera route keeps its publish panel to preserve existing behavior outside the Create composer flow.
