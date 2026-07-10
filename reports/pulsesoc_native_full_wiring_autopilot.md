# PulseSoc Native Full Wiring Autopilot

Date: 2026-07-09

## Mission

Wire the native app action surfaces so visible and nested controls open a native route, native module shell, server-authoritative action, safe fallback, or documented deferred path.

## Completed

- Added a native `Create` bottom-tab action that opens the existing Home composer instead of acting as a dead tab.
- Added a Home top bar with native actions for menu, search, activity, and profile.
- Added a Home hamburger drawer with 32 classified actions across Core, Create, Network, Commerce, Trust, and Intelligence.
- Routed Home drawer actions to existing native screens, existing native module shells, or safe fallback boundaries.
- Added explicit Settings entries for Support Center, Privacy Policy, Terms of Service, and Telegram companion setup.
- Preserved production WebView routes and backend authority.
- Added `scripts/pulsesoc_native_full_wiring_autopilot_audit.py` to keep the wiring coverage reproducible.

## Classification Summary

- Native complete: Home, Dashboard, Search, Activity, Messages, Profile, Settings, Status, Reels, Groups, Live, Events, Marketplace, Seller Store, Buyer Orders, Premium, Growth, Safety Hub, Scam Shield, Verification, Account Health, Pulse AI, Intelligence, Alerts, Courses.
- Native shell: dashboard module detail shells and legacy dashboard aliases.
- Server mutation: existing composer publish, feed reactions, save/repost/follow, hide, mute, report handoff, and account/session actions.
- Safe fallback: Pulse Radio/music, Live Studio/go-live, legal/provider-owned routes, and Telegram companion setup through existing account settings.
- Permission/provider gated: premium, seller approval, creator access, Live Studio, payout/provider flows.
- Intentionally deferred: final UI/UX polish, Android release work, physical-device-only hardware QA, provider-owned checkout/push behavior not available in browser QA.

## Static Audit Result

The audit discovered 332 static wiring surfaces:

- Dashboard modules: 146
- Dashboard quick actions: 12
- Bottom tabs: 15
- Stack screens: 77
- Home drawer actions: 32
- Settings buttons: 26
- Home composer pressables: 9
- Feed card pressables: 15

The audit reported no missing required wiring seams.

## Visible QA Status

Visible QA is covered in `reports/pulsesoc_native_visible_wiring_qa.md`.

The code-level wiring audit passed. Authenticated visible click-through remains blocked until the local QA API/proxy is running again.

## Remaining Risk

- This pass verifies representative wiring, not every possible backend state.
- Some advanced provider-owned flows intentionally remain safe web fallbacks.
- Authenticated browser click-through was not completed in this run because the local QA API/proxy was unavailable.
- Physical-device-only surfaces remain release QA items, not foundation blockers.
