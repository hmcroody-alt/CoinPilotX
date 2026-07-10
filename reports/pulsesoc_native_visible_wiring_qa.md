# PulseSoc Native Visible Wiring QA

Date: 2026-07-09

## Scope

Visible QA targeted representative action wiring across the native app after the full-wiring pass.

## What Was Prepared for Visible QA

- Home top bar:
  - Menu
  - Search
  - Activity
  - Profile
- Home drawer:
  - Core
  - Create
  - Network
  - Commerce
  - Trust
  - Intelligence
- Bottom navigation:
  - Dashboard
  - Home
  - Search
  - Saved
  - Groups
  - Live
  - Reels
  - Create
  - Status
  - Messenger
  - Notifications
  - Pulse AI
  - Profile
  - Marketplace
  - Settings
- Settings legal/provider entries:
  - Support Center
  - Privacy Policy
  - Terms of Service
  - Telegram companion setup

## Browser QA Result

The built-in QA browser was used visibly. No Chrome Incognito was used.

Runtime result:

- `http://127.0.0.1:8094/pulse` returned `200`.
- The clean Metro rebuild rendered the native Login screen in the built-in QA browser.
- No fatal browser console errors were captured.
- Non-fatal warnings were limited to Expo web/runtime warnings: shadow style deprecation, `expo-av` deprecation, and web push-listener support.

Authenticated click-through result:

- Blocked in this run.
- The local QA API proxy expected at `http://127.0.0.1:5108` was not listening.
- Because the web build requires a local API base for QA-only browser login, drawer/home/dashboard/settings click-through could not be honestly marked as authenticated-visible in this run.
- This report does not claim Roody saw signed-in Home drawer actions, Settings fallback actions, or Dashboard module clicks.

## Notes

- The visible pass is representative, not exhaustive across all 332 static action surfaces.
- Dashboard module shells and legacy aliases remain covered by their existing route audit plus this mission's wiring inventory.
- Provider-owned paths remain safe fallback boundaries.

## Remaining QA Work

- Restart the local QA API/proxy at `http://127.0.0.1:5108`.
- Restart Metro with `EXPO_PUBLIC_PULSE_API_BASE_URL=http://127.0.0.1:5108 npm run --prefix mobile-native web:qa -- --clear`.
- Authenticate through the visible built-in QA browser without committing credentials.
- Click representative Home drawer, bottom nav, Settings, Dashboard, Marketplace, Profile, Messages, and Support/Privacy/Terms actions.
- Physical-device-only routing remains release QA.
