# PulseSoc Native Music Player — Queue, Previous/Next, Shuffle, Loop, Repeat One, and Persistent Playback

Date: 2026-07-19

Scope: PulseSoc Native (`mobile-native/`) Pulse Radio / Music playback experience

Source baseline: `release/undx-nexus-core-v4` at `d9c21df3cbbcb6f64990dd760e6a43163d18d0ea`

## Mission brief note

The pasted mission brief ended with a bulleted capability list and no further sections (no explicit file list, acceptance criteria, or design constraints beyond the list itself). This report treats that list as the complete scope and calls out every place a judgment call was made because the brief did not specify exact behavior.

Requested capabilities, and where each is implemented:

| Capability | Implementation |
| --- | --- |
| Play / Pause | `togglePulseRadio`, `playPulseRadio`, `pausePulseRadio` (pre-existing, preserved) |
| Previous track | `playPreviousTrack` — restarts the current track instead of skipping back if more than 3s has played (Spotify/Apple Music convention; the brief did not specify this threshold) |
| Next track | `playNextTrack` |
| Seek backward/forward | `seekPulseRadioBy(deltaMillis)`, `seekPulseRadioTo(positionMillis)`; MusicScreen wires ±15s buttons |
| Shuffle | `setPulseRadioShuffle` / `togglePulseRadioShuffle`, Fisher–Yates order with the currently playing track pinned first so shuffling never interrupts playback |
| Repeat queue | `repeatMode: "queue"` |
| Repeat one track | `repeatMode: "one"` |
| Disable repeat | `repeatMode: "off"`; cycles off → queue → one → off |
| View and manage the queue | New `PulseQueueScreen` — tap-to-play, move up/down, remove |
| Continue listening across pages | New persistent mini-player bar in `LogiNexusBottomNavigation`, visible on every primary tab screen |
| Continue listening in the background | Pre-existing `expo-av` background-audio behavior, unchanged |
| Control playback from the lock screen | New local Expo module `pulse-now-playing` wrapping `MPNowPlayingInfoCenter` / `MPRemoteCommandCenter` (iOS only) |

No backend queue/playlist API exists (`listPulseRadioTracks()` returns a flat catalog), so the queue, shuffle order, and repeat state are entirely client-side, matching how Pulse Radio already worked before this mission.

## Architecture

- `src/core/pulseRadioQueueOrder.ts` (new) — pure, dependency-free helpers for queue-order math (sequential/shuffled order build, next/previous order position under each repeat mode, repeat-mode cycling, and reindexing the internal play order after a queue move or removal). Kept separate from `pulseRadio.ts` so this logic is unit-testable without mocking `expo-av`.
- `src/core/pulseRadio.ts` (rewritten) — adds `queue`, `queueIndex`, `shuffle`, `repeatMode`, `positionMillis`, `durationMillis` to `PulseRadioState`, plus `playNextTrack`, `playPreviousTrack`, `playQueueTrackAt`, `seekPulseRadioTo/By`, `setPulseRadioShuffle`/`togglePulseRadioShuffle`, `setPulseRadioRepeatMode`/`cyclePulseRadioRepeatMode`, `moveQueueTrack`, `removeQueueTrackAt`. All pre-existing exports keep their original signatures and behavior.
- `modules/pulse-now-playing/` (new local Expo module, iOS only) — Swift `Module` using the Expo Modules DSL. Publishes track metadata (title, artist, artwork, duration, elapsed time) to `MPNowPlayingInfoCenter` and wires `MPRemoteCommandCenter` transport commands (play, pause, toggle, next, previous, seek, skip forward/back) to an `onRemoteCommand` JS event. Registered into the app via `"pulse-now-playing": "file:./modules/pulse-now-playing"` and discovered by Expo's autolinking; the JS entry point is hand-written plain `index.js` + `index.d.ts` (not compiled TypeScript) to avoid relying on an unconfirmed Metro node_modules TS-transform guarantee.
- `src/native/nowPlayingBridge.ts` (new) — defensive app-side wrapper; every call is try/caught so a native-bridge failure can never interrupt audio playback. Safe no-op on Android/web (native module resolves to `null` there).
- `src/core/pulseRadio.ts` calls `onRemoteCommand` once at module load and dispatches lock-screen button presses back into the same queue engine functions used by the UI; `pushNowPlayingInfo`/`pushNowPlayingProgress` are called on track start and on every playback status tick.
- `src/screens/PulseQueueScreen.tsx` (new) — registered as `PulseQueue` on the root stack (`types.ts`, `AppNavigator.tsx`), mirroring the existing `Music` screen registration pattern. Shuffle/repeat toolbar, tap-to-play list with move-up/down/remove per row, and a sticky play/pause bar.
- `src/navigation/GlobalNavigation.tsx` — new `PulseMiniPlayerBar` rendered inside `LogiNexusBottomNavigation`, above the tab bar, on every primary tab screen (Home, Reels, Create, Messenger, Profile). Shows title/artist, a thin progress bar, play/pause, and next; tapping the row opens `PulseQueue`. It shares the tab bar's existing show/hide animation rather than introducing a second one.
- `src/screens/MusicScreen.tsx` — the existing Pulse Radio hero card gained a control row (previous, seek −15s, seek +15s, next, shuffle toggle, repeat cycle, and an "Open Queue" button) that only renders once a track is loaded.

## Judgment calls (brief did not specify)

- Restart-vs-skip-back threshold for "previous track": 3000ms, matching mainstream music-app convention.
- Seek step size for the ± buttons: 15 seconds.
- Shuffle never disturbs the currently playing track (only the upcoming order changes) — this matches Spotify/Apple Music behavior and was chosen over a full re-shuffle-and-restart to avoid interrupting playback the mission also asks to preserve ("Continue listening across pages/background").
- The persistent mini-player is scoped to the primary tab screens (via the existing `LogiNexusBottomNavigation` component) rather than every root-stack screen (e.g. Chat, Settings), since that component is the app's one cross-page persistent chrome element; adding a second, separate global overlay was judged out of scope and riskier to the existing navigation stack.
- Tapping the mini-player and the new "Queue" button in `MusicScreen` both open `PulseQueueScreen` (view-and-manage requirement) rather than reopening `MusicScreen`, since `MusicScreen`'s controls are already visible when the user is on that screen.

## Files changed

New:
- `mobile-native/modules/pulse-now-playing/package.json`
- `mobile-native/modules/pulse-now-playing/expo-module.config.json`
- `mobile-native/modules/pulse-now-playing/index.js`
- `mobile-native/modules/pulse-now-playing/index.d.ts`
- `mobile-native/modules/pulse-now-playing/ios/PulseNowPlaying.podspec`
- `mobile-native/modules/pulse-now-playing/ios/PulseNowPlayingModule.swift`
- `mobile-native/src/core/pulseRadioQueueOrder.ts`
- `mobile-native/src/core/__tests__/pulseRadioQueueOrder.test.ts`
- `mobile-native/src/core/__tests__/pulseRadio.test.ts`
- `mobile-native/src/native/nowPlayingBridge.ts`
- `mobile-native/src/screens/PulseQueueScreen.tsx`
- `reports/pulsesoc_native_music_queue_lockscreen_playback_2026-07-19.md` — this report

Modified:
- `mobile-native/package.json` — added `pulse-now-playing: file:./modules/pulse-now-playing`
- `mobile-native/package-lock.json` — `npm install` lockfile update
- `mobile-native/src/core/pulseRadio.ts` — queue engine, shuffle, repeat, seek, lock-screen wiring
- `mobile-native/src/navigation/types.ts` — `PulseQueue` route added to `RootStackParamList`
- `mobile-native/src/navigation/AppNavigator.tsx` — `PulseQueue` screen registration
- `mobile-native/src/navigation/GlobalNavigation.tsx` — persistent mini-player bar
- `mobile-native/src/screens/MusicScreen.tsx` — previous/next/seek/shuffle/repeat/queue controls

`mobile-native/ios/` is gitignored and regenerated by `expo prebuild`/`pod install`; no files under it are committed. No backend files changed.

## Verification performed

Passed:
- `npx tsc --noEmit` (clean, zero errors)
- `npx jest --runInBand` — 108/108 tests passing across 11 suites (23 new: 12 in `pulseRadioQueueOrder.test.ts`, 11 in `pulseRadio.test.ts`; zero regressions in the pre-existing 97)
- `EXPO_DOCTOR_ENABLE_DIRECTORY_CHECK=0 npx expo-doctor --verbose` — 17/17 checks passed
- `pod install` (`LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8`) — 101 dependencies, 102 pods installed, `PulseNowPlaying` pod linked from `../modules/pulse-now-playing/ios`
- `npx expo run:ios --device "P3r7or"` — Xcode build succeeded (0 errors, 2 pre-existing unrelated warnings: a Metal toolchain search-path notice and an RNCAsyncStorage minimum-iOS-version notice), app installed to the physical device at 100% (`devicectl`)

New unit test coverage (`pulseRadio.test.ts`): queue load, next/previous with wrap-only-on-repeat-queue and stop-at-end-with-repeat-off, restart-vs-skip-back threshold, repeat-mode cycling, repeat-one indefinite repeat, shuffle without interrupting the current track and order restoration on disable, `moveQueueTrack` keeping `queueIndex` pinned to the currently playing track, `removeQueueTrackAt` for both a non-playing and the currently-playing track, seek clamping to duration, and auto-advance on track completion.

## Physical-device QA — honest status

Build and install succeeded and are confirmed (Xcode `Build Succeeded`, `devicectl` install reported `Complete 100%`). Interactive on-device verification (tapping lock-screen transport controls, confirming background-audio continuation, visually confirming the mini-player across pages) could **not** be observed in this session:

- iPhone Mirroring (the only way to visually drive the physical device from this environment) reported **"Unable to Connect to iPhone"** on repeated attempts — a macOS/Bluetooth-proximity condition, not a code issue. This is recorded as **BLOCKED — NOT OBSERVED**, not PASS.

| Scenario | Result |
| --- | --- |
| App builds and installs to physical iPhone 16 Pro ("P3r7or") | PASS |
| Lock-screen Now Playing metadata appears | BLOCKED — NOT OBSERVED (iPhone Mirroring could not connect) |
| Lock-screen transport controls (play/pause/next/previous/seek) | BLOCKED — NOT OBSERVED |
| Background audio continuation | BLOCKED — NOT OBSERVED |
| Mini-player visible/consistent across Home/Reels/Messenger/Profile | BLOCKED — NOT OBSERVED |
| Queue screen reorder/remove/tap-to-play on-device | BLOCKED — NOT OBSERVED |
| Shuffle/repeat toggle on-device | BLOCKED — NOT OBSERVED |

All of the above are exercised by the passing unit-test suite and by static build/link success (the `PulseNowPlaying` Swift module compiled and linked cleanly, or the overall `xcodebuild` would have failed with 0 errors reported). A follow-up physical QA pass on the device directly (not remotely mirrored) is needed to close out the on-device transport-control and background-audio scenarios before this ships.

## Rollback

Revert the scoped commit. No backend route, database migration, or App Store production bundle identity is touched. Removing the `pulse-now-playing` dependency from `package.json` and re-running `pod install` fully removes the native lock-screen integration; the queue/shuffle/repeat engine in `pulseRadio.ts` has no native dependency and can be reverted independently.
