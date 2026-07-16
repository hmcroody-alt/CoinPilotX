# PulseSoc Native Home lightweight futuristic redesign

Date: 2026-07-16
Active subsystem: Native Home
Freeze decision: **Not frozen** — simulator implementation and signed device build pass, but the physical iPhone 16 Pro is unavailable and production-backed playing/call/low-power device evidence remains open.

## Outcome

The native Home now starts with Pulse Radio paused, requires an explicit Play press, removes the fabricated persistent bottom player, collapses the composer by default, and exposes more of the first feed card without replacing the canonical Home, Status, composer, feed, navigation, or radio contracts. The current production WebView code and routes were not changed.

## Inspection and root causes

- Production radio: `bot.py` serves the authenticated `/api/pulse/music/radio` catalog and `/api/pulse/music/<track_id>/event` analytics contract. The browser radio already waits for explicit user intent.
- Native Home: `mobile-native/src/screens/HomeScreen.tsx` rendered a hard-coded pause glyph and `Now Playing` label before any audio existed.
- Bottom player: Home contained a fabricated `Beautiful Stranger` mini-player dock with permanent layout and touch area.
- Vertical density: the composer always rendered expanded and Home retained bottom padding sized for the removed dock.
- Audio ownership: Home had presentation-only radio controls and no shared native coordinator; Reels and Call therefore could not assert radio priority.

## Implementation

### Files changed

- `mobile-native/src/api/radio.ts` — production radio catalog and play-event wrapper.
- `mobile-native/src/core/pulseRadio.ts` — one shared, process-level radio coordinator.
- `mobile-native/src/screens/HomeScreen.tsx` — truthful hero radio, compact hierarchy, wired Status View All, removed mini-player.
- `mobile-native/src/components/HomePulseComposer.tsx` — collapsed default, draft-aware expansion, collapse/reopen, success collapse.
- `mobile-native/src/screens/CallScreen.tsx` — Call takes audio priority by pausing radio.
- `mobile-native/src/screens/ReelsScreen.tsx` — Reels pauses radio before configuring Reels audio.
- `scripts/pulsesoc_native_home_lightweight_radio_audit.py` — focused state/route/lifecycle audit.
- `scripts/pulsesoc_logi_nexus_home_complete_audit.py` — removed stale fabricated-dock and internal-label requirements.

### Reuse and preserved contracts

- Reused `pulseApi`, production auth, production radio routes, `expo-av`, existing feed/status APIs, `PostCard`, `HomePulseComposer`, native media upload, draft persistence, global navigation, deep-link routing, and event invalidation.
- Preserved `/pulse`, `/pulse/music#pulse-radio`, Status, Safety, Live, Reels, Messages, Profile, Camera Studio, and production feed mutations.
- Added no database, user, Status, feed, navigation, or backend implementation.

### Radio state machine

Initial state is `{status: paused, track: null, message: Tap to play}`. No catalog request, audio-mode activation, sound creation, or analytics event occurs at module load, login, Home mount, or relaunch.

Explicit Play transitions through `connecting`, then `buffering` when reported by the real player, and only reaches `playing` after a loaded `Audio.Sound` starts. Pause increments an intent generation, unloads the shared sound, and prevents an older asynchronous connection from starting audio. Backgrounding, opening Call, or opening Reels pauses radio. Errors are reduced to friendly retry/offline copy; raw API failures are not displayed.

The controlled local backend returned no approved playable tracks. The explicit Play test therefore correctly produced `Pulse Radio is unavailable. Tap to retry.` Playing and Pause-after-playing were not claimed.

## Home surface

- Header: existing global PulseSoc header and canonical actions retained.
- Hero: lightweight static spatial layers, canonical aggregate values, paused Play affordance, no false waveform or `Now Playing` state.
- Status: compact empty/loading rail, Add, item open, and View All routes retained/wired.
- Composer: collapsed on normal entry; expanded only by user intent, route intent, or a real recovered draft. Collapse preserves the draft and selected state. Success clears/collapses; failure remains available for retry.
- Feed: filters and `PostCard` remain server-authoritative; the first post is visible in the iPhone 16 Pro and Pro Max initial viewport.
- Bottom navigation: existing safe-area-aware global tab bar remains the only bottom overlay. No mini-player view or reserved mini-player padding remains.

## Performance, motion, accessibility

- Home adds no timers, waveform loops, video background, blur pipeline, canvas, particle engine, or high-frequency animation.
- Decorative spatial layers are static and `pointerEvents="none"`; the screen remains a virtualized `FlatList`.
- Reduce Motion and Low Power Mode do not need a second branch for the new treatment because the new Home treatment has no continuous motion to disable. Interactive low-power visual evidence is still open.
- Radio exposes button roles, Play/Pause labels, busy state for connecting/buffering, friendly error text, and stable test IDs.
- Composer expand/collapse and Status View All have explicit accessible button semantics.
- Full VoiceOver traversal and Dynamic Type stress testing remain physical-device gaps.

## Build and verification

- `npm ci --prefix mobile-native --no-audit --no-fund --progress=false`: PASS.
- `npm run --prefix mobile-native typecheck`: PASS.
- `EXPO_DOCTOR_ENABLE_DIRECTORY_CHECK=0 npx expo-doctor --verbose`: PASS, 17/17.
- Focused lightweight Home/radio audit: PASS.
- Existing complete Home audit after stale-check correction: PASS.
- `git diff --check`: PASS before report creation; rerun at release gate.
- Debug simulator workspace build: PASS.
- Release simulator workspace build with embedded bundle: PASS.
- Signed generic iOS Debug build: PASS with automatic Apple Development signing.
- Signed device artifact: Debug bundle `com.pulsesoc.nativeapp.dev`, display name `PulseSoc Native Dev`, embedded `main.jsbundle` (13 MB). This identity remains separate from the Release identity and cannot replace the installed production app.
- Xcode: 26.6 (17F113).
- Simulator runtime: iOS 26.5.

## Simulator matrix

| Target | Result | Verified |
| --- | --- | --- |
| Compact iPhone | PASS | paused default, no bottom player, collapsed/expanded composer, bottom navigation, no horizontal clipping |
| Standard iPhone | PASS | paused default, proportions, first post entry, bottom navigation |
| iPhone 16 Pro | PASS | Dynamic Island spacing, complete first viewport, paused default, first feed card, no bottom player |
| iPhone 16 Pro Max | PASS | non-stretched wide hero, collapsed/expanded composer, first post, bottom navigation |

Evidence: `reports/screenshots/native-home-lightweight-futuristic-radio-paused-redesign-2026-07-16/`

Important evidence limitations:

- `compact-home-final-radio-retry.png` proves explicit Play and truthful controlled-backend failure, not playing audio.
- `pro-home-offline.png` is the controlled unavailable/retry state; it is not a device-level airplane-mode capture.
- No files claim physical-device, active-call, Low Power Mode, populated Status, or production-track playing evidence.

## Physical iPhone 16 Pro

Xcode and CoreDevice remember iPhone 16 Pro devices, but every physical device is currently `unavailable`. Xcode reports that the iPhone must be unlocked and attached with a cable (or available over a Developer Mode network connection). No USB iPhone was present in the system USB inventory.

- Physical build: PASS, signed Debug development identity.
- Install: BLOCKED — device unavailable.
- Launch: BLOCKED — device unavailable.
- Side-by-side configuration: PASS by artifact inspection (`com.pulsesoc.nativeapp.dev`, `PulseSoc Native Dev`).
- Production WebView app preservation on-device: NOT RECHECKED because no device connection existed.
- Radio, background/foreground, force quit, Call interruption, Low Power Mode, VoiceOver, and media interaction: NOT VERIFIED on physical hardware.

Required user action: connect the iPhone 16 Pro by cable, unlock it, accept Trust if prompted, and leave Developer Mode enabled. The prepared Debug development app can then be installed without replacing the production app.

## Controlled behavior status

- Fresh install and relaunch remain paused: PASS in simulator.
- No Home/login/relaunch autoplay code path: PASS by audit and clean install observation.
- Explicit Play: PASS; controlled catalog had zero playable tracks.
- Connecting cancellation: PASS by state-machine audit; physical timing exercise open.
- Playing/Pause with production track: BLOCKED by controlled data and physical device availability.
- Background/foreground: code path PASS; physical evidence open.
- Call/Reels priority: code path PASS; active-call physical evidence open.
- Composer collapse/expand/draft preservation: PASS by code audit and simulator interaction.
- Composer media/publish lifecycle: existing canonical wiring preserved; full publish matrix not rerun in this visual mission.
- Status and feed: canonical loading/empty/route/feed paths PASS; populated/realtime/offline matrix not fully repeated.

## Honest completion scores

| Area | Completion |
| --- | ---: |
| Overall Home | 86% |
| UI design | 94% |
| Visual quality | 91% |
| Interaction completion | 84% |
| Deep wiring | 90% |
| Header | 96% |
| Hero | 93% |
| Radio paused-default | 100% |
| Radio state accuracy | 88% |
| Status rail | 88% |
| Composer collapsed | 100% |
| Composer expanded | 94% |
| Feed filters | 95% |
| Feed cards | 90% |
| Bottom navigation | 96% |
| Audio priority | 82% |
| Loading/empty/error | 90% |
| Offline/reconnect | 68% |
| Accessibility | 78% |
| Low Power Mode | 72% |
| Responsive behavior | 94% |
| Performance | 92% |
| Simulator QA | 91% |
| Physical iPhone QA | 15% |
| Device-size coverage | 80% |
| Backend reuse | 100% |
| Existing native component reuse | 96% |
| WebView compatibility | 98% |

## Freeze and replacement readiness

Home is **not simulator-parity frozen** because the playing/call/airplane-mode/Low-Power/VoiceOver matrices are incomplete even though the main four-size visual matrix passes. It cannot replace WebView Home yet. The next exact work remains a Home physical-device closure run: install the prepared development artifact side by side, prove real production radio Play/Pause and interruption behavior, exercise Low Power/VoiceOver, and capture the physical evidence set. No different subsystem should be started before that closure is accepted.
