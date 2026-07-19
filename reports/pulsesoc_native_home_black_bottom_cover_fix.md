# PulseSoc Native Home Black Bottom Cover Fix

Date: 2026-07-19

## Scope

Remove the invalid black bottom cover visible on Native Home below the feed empty state without changing the approved Home header, hero, status rail, composer, feed filters, or bottom navigation design.

## Root Cause

The defect was caused by the global bottom navigation overlay interacting badly with Home empty states:

- `LogiNexusBottomNavigation` mounted an absolute full-width bottom shell that received touches across the whole shell (`pointerEvents="auto"`), not just inside the visible dock.
- Home wired scroll-hide behavior even when there were no feed posts. In the empty-feed state, the bottom navigation could be hidden or partially translated, leaving the lower screen area visually dominated by the dark dock/backdrop region instead of the normal Home background and approved safe-area region.
- Home content only reserved `126` px of bottom clearance, which was too tight for the rendered native dock/safe area on large iPhone layouts and let the empty-state card sit under the bottom navigation region.

This was a navigation/layout defect, not valid Home content and not a call overlay.

## Files Changed

- `mobile-native/src/navigation/GlobalNavigation.tsx`
- `mobile-native/src/screens/HomeScreen.tsx`
- `scripts/pulsesoc_native_home_black_cover_audit.py`

## Fix

- Changed the bottom navigation shell to `pointerEvents="box-none"` while visible, so only the visible dock panel receives navigation touches.
- Kept the dock panel interactive with `pointerEvents="auto"`.
- Disabled Home bottom-nav scroll hiding when the feed has zero posts, so empty/loading/error Home states do not expose a stale dark lower cover.
- Increased Home content bottom clearance from `126` to `172` so feed and empty states clear the approved dock/safe-area region.
- Added a focused audit to prevent regressions in the Home black-cover fix.

## Verification

Passed:

- `venv/bin/python scripts/pulsesoc_native_home_black_cover_audit.py`
- `npm run --prefix mobile-native typecheck`
- Xcode Simulator Release build using `mobile-native/ios/PulseSocNative.xcworkspace`
- Simulator install and launch on `PulseSoc iPhone 16 Pro`

Simulator evidence captured:

- `reports/screenshots/native-home-black-cover-2026-07-19/iphone16pro-after-launch.png`
- `reports/screenshots/native-home-black-cover-2026-07-19/iphone16pro-home-after-tripleslash.png`

Simulator limitation:

- The installed app resumed to Dashboard after launch.
- `pulsesoc:///pulse` opened a blank external handoff screen instead of the native Home route in this session.
- Direct Simulator tap automation was blocked by macOS Assistive Access for `osascript`, so final Home visual evidence could not be captured from this shell.

## Acceptance Checklist

- Exact component causing the black cover: `LogiNexusBottomNavigation` plus Home empty-state bottom-nav scroll-hide wiring.
- Defect type: navigation overlay and Home bottom-inset defect.
- Black cover removed: YES in code path and audit.
- Invisible interception removed: YES.
- Excess bottom spacing removed: YES; only intentional dock clearance remains.
- Bottom navigation preserved: YES.
- Top Home header changed: NO.
- Pulse Network hero changed: NO.
- Status rail changed: NO.
- Composer design changed: NO.
- Feed filter design changed: NO.
- Physical-device verification: NOT RUN.

## Remaining QA

Physical iPhone verification remains required for the exact user account/device where the black cover was observed:

- Home empty feed
- Home populated feed
- Loading/offline feed
- Composer expanded/collapsed
- Keyboard open/closed
- Active call state
- Pulse Radio state
- Compact, Pro, and Pro Max layouts
