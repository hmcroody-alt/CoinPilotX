# PulseSoc Native Bottom Navigation Scroll Behavior

## Goal

Implement the native equivalent of the PulseSoc bottom navigation behavior:

- hide the bottom tab bar while scrolling down
- reveal it immediately while scrolling up
- keep it visible near the top or on short pages
- avoid interfering with keyboards, sheets, Reels paging, safe areas, or accessibility

## Files Changed

- `mobile-native/src/navigation/BottomNavVisibility.tsx`
- `mobile-native/src/navigation/GlobalNavigation.tsx`
- `mobile-native/src/navigation/AppNavigator.tsx`
- `mobile-native/src/components/Screen.tsx`
- `mobile-native/src/screens/HomeScreen.tsx`
- `mobile-native/src/screens/MessengerScreen.tsx`
- `scripts/pulsesoc_native_bottom_nav_scroll_audit.py`
- `reports/pulsesoc_native_bottom_nav_scroll_behavior.md`

## Native Architecture

The canonical native bottom navigation is `LogiNexusBottomNavigation` in `mobile-native/src/navigation/GlobalNavigation.tsx`.

The implementation adds one shared provider:

- `BottomNavVisibilityProvider`
- `useBottomNavVisibility`
- `useBottomNavScrollVisibility`

`AppNavigator` wraps the tab navigator with the provider. The tab bar consumes the provider and animates itself with `Animated.timing(..., useNativeDriver: true)`.

## Product Behavior

- Home feed: hides on downward scroll and returns on upward scroll.
- Messenger inbox: hides while scrolling recent conversations and returns on upward scroll.
- Shared `LogiNexusScrollContainer`: scroll-aware for screens that use the canonical shared shell.
- Reels: intentionally unchanged. Reels uses vertically paged playback and should retain stable navigation unless a separate Reels-specific design approves hiding it.

## Keyboard Behavior

The provider listens to native keyboard show/hide events.

When the keyboard is visible, the tab bar is hidden so it does not compete with inputs, composer fields, search fields, or the OS keyboard area.

## Safe Areas

The existing `useSafeAreaInsets()` bottom padding remains in `LogiNexusBottomNavigation`.

The hide animation translates the full tab shell below the safe-area-aware position instead of changing layout height, so content does not jump.

## Black Cover Fix

The tab bar shell is now an absolute overlay anchored to the bottom edge instead of a normal tab-bar layout block.

This prevents the hidden dock from leaving a black reserved area or cover over the lower part of the screen while the user scrolls. Screens keep their own content bottom padding for safe touch clearance, but the hidden navigation shell no longer owns visual or layout space.

The follow-up black-cover defect came from the same navigation surface: the dock was hidden with a hard-coded translation distance that did not reliably cover the rendered shell height, safe-area padding, raised Create tab, and panel shadow on larger iPhones. The shell now measures its actual rendered height with `onLayout` and translates by that height plus extra clearance, so no rounded black navigation surface remains visible below the Home empty state.

## Accessibility

When hidden:

- pointer events are disabled
- tab bar accessibility elements are hidden
- `importantForAccessibility` is set to `no-hide-descendants`

When visible again, the existing tab labels, roles, selected state, badges, and touch targets remain unchanged.

## Sheets And Modals

The provider supports pinning by reason for sheets/modals that need to keep the dock visible. This mission does not force Reels or fullscreen media surfaces into scroll-hide behavior.

## Verification

Local verification:

- `venv/bin/python -m py_compile scripts/pulsesoc_native_bottom_nav_scroll_audit.py`
- `venv/bin/python scripts/pulsesoc_native_bottom_nav_scroll_audit.py`
- `npm run typecheck` from `mobile-native`
- `git diff --check`

WebView verification remains documented separately in `reports/pulsesoc_bottom_nav_scroll_behavior.md`.

## Physical iPhone QA

Physical iPhone QA is still required after build/install:

1. Home: scroll down through feed, confirm tab bar hides.
2. Home: scroll up slightly, confirm tab bar returns.
3. Messenger: scroll recent conversations, confirm down-hide/up-show.
4. Search or any input field: focus keyboard, confirm tab bar does not cover input/keyboard.
5. Reels: vertically page reels, confirm navigation stays stable and does not flicker.
6. Open sheets/modals: confirm no invisible tab overlay captures taps.
7. VoiceOver: confirm hidden tab bar is not focusable while hidden and returns to normal when visible.

## Remaining Limitations

- Simulator or physical-device screenshots were not captured in this implementation pass.
- Full per-screen adoption can be expanded to more tab surfaces after product review.

## P0 Black Bottom Cover Closure

- Exact component causing the black cover: `LogiNexusBottomNavigation` in `mobile-native/src/navigation/GlobalNavigation.tsx`.
- Defect type: native navigation overlay/hide-distance defect.
- Root cause: the bottom dock shell was first hidden with a hard-coded translation and later made absolute, but the hard-coded hidden distance still did not reliably account for the rendered shell height, safe-area padding, raised Create tab, and panel shadow on large iPhone layouts.
- Fix: measure the rendered bottom shell with `onLayout`, calculate an offscreen distance from that measured height plus safe-area clearance, and keep the shell as an absolute bottom overlay so it does not reserve layout space or leave a rounded black surface visible.
- Black cover removed: YES in code path.
- Invisible interception removed: YES, hidden dock keeps `pointerEvents="none"` and accessibility descendants hidden.
- Excess bottom spacing removed: YES for the navigation shell; screen content keeps intentional bottom padding for safe-area clearance.
- Bottom navigation preserved: YES.
- Physical-device verification: NOT VERIFIED in this shell.
- Local verification: `npm run typecheck`, native bottom-nav audit, audit py_compile, and `git diff --check` passed.
- Health route: NOT VERIFIED because `http://127.0.0.1:5069/health` was not reachable locally.
