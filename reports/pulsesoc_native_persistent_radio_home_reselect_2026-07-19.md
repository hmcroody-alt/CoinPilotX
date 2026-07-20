# PulseSoc Native Persistent Radio and Home Reselect

Date: 2026-07-19

## Executive Summary

PulseSoc Radio now uses a process-level persistent player instead of being owned by a screen-level Music component. The native media coordinator is the single arbitration point for radio, voice messages, app media, previews, Live, and calls. Home bottom-tab reselect now scrolls Home to the top and triggers one bounded refresh through the existing Home feed/status loaders.

This is a native implementation over the current PulseSoc backend contracts. No WebView radio player, duplicate radio provider, duplicate feed refresh path, or duplicate audio queue was introduced.

## Existing Radio Architecture

- Existing backend/API reuse: `mobile-native/src/api/radio.ts` remains the source for approved radio tracks and play-event recording.
- Existing native coordinator reuse: `mobile-native/src/core/mediaPlaybackCoordinator.ts` remains the single playback owner registry.
- Existing UI reuse: Home and Music continue to call `togglePulseRadio()` and subscribe to `subscribePulseRadio()`.

## Root Cause of Playback Termination

The prior radio lifecycle was screen/app-state oriented:

- `pulseRadio.ts` had an `AppState` listener that paused the radio whenever the app left active state.
- Higher-priority media callers such as voice messages, calls, and Reels directly paused Pulse Radio, clearing radio user intent.
- Music UI was not observing one persistent cross-screen radio state as the authoritative player state.

That made backgrounding and media interruption behave like a manual stop.

## Persistent Player Architecture

Implemented in `mobile-native/src/core/pulseRadio.ts`:

- `userWantsPlayback` preserves explicit user intent.
- `interruptedBy` records why playback is temporarily paused.
- `subscribeMediaPlayback()` schedules a bounded resume when higher-priority audio releases.
- Manual pause clears resume intent.
- Interruption pause preserves resume intent.
- The Music screen no longer owns a separate radio `Audio.Sound`.

## iOS Background Audio Configuration

Added tracked background audio entitlement/configuration:

- `mobile-native/app.json`

The local generated file `mobile-native/ios/PulseSocNative/Info.plist` was also checked in this workspace, but `mobile-native/ios/` is intentionally ignored. The committed audit treats `app.json` as the source of truth and validates the generated Info.plist only when it exists.

Added Expo AV radio audio mode:

- `staysActiveInBackground: true`
- `playsInSilentModeIOS: true`
- `shouldDuckAndroid: false`
- `DoNotMix` interruption modes

## Audio Priority Model

Priority remains centralized in `mediaPlaybackCoordinator.ts`:

1. call
2. recording
3. live
4. voice
5. viewer
6. status / reel / feed
7. music preview
8. radio

Radio cannot interrupt higher-priority audio. Higher-priority owners pause radio without clearing user intent.

## Video, Reels, Status, Voice, and Call Coordination

- Voice messages use `claimMediaPlayback()` and no longer directly call `pausePulseRadio()`.
- Calls claim the `call` owner and release it on exit, without directly clearing radio intent.
- Reels no longer pause radio on screen entry; unmuted active reels claim playback.
- Feed videos claim playback only when audible.
- Status videos claim playback only when audible.
- Muted active video can play visually without forcing radio off.

## Resume Rules

Radio resumes only when:

- the user explicitly started radio earlier,
- radio was paused by a higher-priority owner,
- that owner releases the media coordinator,
- and the user did not manually pause radio during the interruption.

Radio does not resume after:

- a manual pause,
- a start failure with no user intent,
- an unavailable/offline radio error,
- or an owner that remains active.

## Home Reselect Behavior

Implemented:

- `mobile-native/src/navigation/homeReselect.ts`
- `GlobalNavigation.tsx` triggers reselect only when Home is already active.
- `HomeScreen.tsx` registers one handler that scrolls the Home `FlatList` to offset `0`, refreshes statuses, and calls the existing feed `load("refresh")`.
- Refresh is guarded by `refreshingRef` so rapid taps do not create multiple feed refreshes.

## Files Changed

- `mobile-native/app.json`
- `mobile-native/ios/PulseSocNative/Info.plist` local generated file checked, not staged because `mobile-native/ios/` is ignored
- `mobile-native/package.json`
- `mobile-native/package-lock.json`
- `mobile-native/src/components/PostCard.tsx`
- `mobile-native/src/components/ReelPlayerCard.tsx`
- `mobile-native/src/components/StatusViewerCard.tsx`
- `mobile-native/src/core/mediaPlaybackCoordinator.ts`
- `mobile-native/src/core/pulseRadio.ts`
- `mobile-native/src/core/voiceMessagePlayback.ts`
- `mobile-native/src/media/nativeMediaUpload.ts`
- `mobile-native/src/navigation/GlobalNavigation.tsx`
- `mobile-native/src/navigation/homeReselect.ts`
- `mobile-native/src/screens/CallScreen.tsx`
- `mobile-native/src/screens/HomeScreen.tsx`
- `mobile-native/src/screens/MusicScreen.tsx`
- `mobile-native/src/screens/ReelsScreen.tsx`
- `scripts/pulsesoc_native_persistent_radio_home_reselect_audit.py`

## Verification

Passed:

- `npm ci --prefix mobile-native --no-audit --no-fund --progress=false`
- `npm run --prefix mobile-native typecheck`
- `cd mobile-native && EXPO_DOCTOR_ENABLE_DIRECTORY_CHECK=0 npx expo-doctor --verbose`
- `npm test --prefix mobile-native -- --runInBand`
- `python3 scripts/pulsesoc_native_persistent_radio_home_reselect_audit.py`
- `git diff --check`

Notes:

- Jest completed with all suites passing, then reported an existing open-handle warning after completion.
- `@types/jest@29.5.14` was added because TypeScript includes Jest tests and Expo Doctor expects that exact SDK-compatible version.
- `nativeMediaUpload.ts` was extended to accept the existing `"live"` camera mode so full typecheck remains green.

## Simulator QA

Simulator environment:

- Booted: `PulseSoc iPhone 16 Pro`
- Available additional devices: `PulseSoc Compact iPhone`, `PulseSoc iPhone 16 Pro Max`, `iPhone 17 Pro`, `iPhone 17 Pro Max`

Evidence captured:

- `reports/screenshots/native-persistent-radio-home-reselect-2026-07-19/booted-simulator-current.png`
- `reports/screenshots/native-persistent-radio-home-reselect-2026-07-19/pulsesoc-native-launched.png`
- `reports/screenshots/native-persistent-radio-home-reselect-2026-07-19/pulsesoc-native-home-deeplink.png`
- `reports/screenshots/native-persistent-radio-home-reselect-2026-07-19/pulsesoc-native-returned-after-deeplink.png`

Classification:

- App launch: simulator verified.
- Background audio entitlement/config presence: code-path verified.
- Radio persistence/interruption/resume: static audit and typecheck verified.
- Home reselect scroll/refresh: code-path and static audit verified.
- Deep-link attempt to Home: not accepted as visual Home proof because the installed environment opened a web surface.

## Physical-Device QA

Detected by Xcode but offline:

- `P3r7or (18.7.3)`
- `iPad (3) (26.5.2)`
- `iPhone (18.1.1)`
- `iPhone33 (18.6)`

No active USB device was reported by `system_profiler SPUSBDataType`.

Physical-device-only checks still required:

- Lock-screen radio continuation.
- App-switch/background continuation.
- Bluetooth/speaker routing.
- Call interruption and post-call radio resume on hardware.
- Real voice-message interruption and resume on hardware.
- Low Power Mode behavior.
- Physical Home reselect tap loop.

## Known Limitations

- This mission did not add a lock-screen media metadata/remote-control surface. It only keeps the persistent radio player alive and arbitrated correctly.
- True lock-screen/background audio cannot be claimed until a physical device is online and tested.
- The currently installed simulator bundle did not provide direct visual Home reselect proof in this run; the implementation is verified by code path and audit.

## Rollback Instructions

To rollback this mission only:

1. Remove `UIBackgroundModes` audio entries from `mobile-native/app.json` and `mobile-native/ios/PulseSocNative/Info.plist`.
2. Revert `pulseRadio.ts` to AppState-paused, no-intent state.
3. Remove `homeReselect.ts` and the active-Home branch in `GlobalNavigation.tsx`.
4. Revert media owners to direct radio pauses only if accepting loss of resume intent.

Do not rollback unrelated native Home, Messenger, UNDX, or production WebView work.
