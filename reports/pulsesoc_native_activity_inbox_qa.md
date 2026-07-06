# PulseSoc Native Activity Inbox Authenticated QA Hardening

Date: 2026-07-05

## Scope

This pass hardened the native Activity Inbox with an authenticated local QA account/session.

Rules followed:

- Used a temporary local backend and local CORS proxy.
- Used a disposable local QA account only.
- Did not use production credentials.
- Did not touch production WebView paths.
- Fixed only scoped blockers found during QA.
- Kept backend notification/read/delete behavior server-authoritative.

## Local QA Environment

Backend:

- Temporary SQLite database: `/tmp/pulsesoc_activity_qa.sqlite`
- Local backend: `http://127.0.0.1:5107`
- Health check: `GET /health` returned `{"ok":true,"service":"coinpilotx-web"}`

Proxy:

- Local QA proxy: `http://localhost:5108`
- Expo web API base: `EXPO_PUBLIC_PULSE_API_BASE_URL=http://localhost:5108`

Native web:

- `npm run web:qa`
- `curl -I --max-time 2 http://localhost:8094/pulse/activity` returned `HTTP/1.1 200 OK`

QA account:

- Created through existing `/api/mobile/auth/register`.
- Email verification was marked complete only in the throwaway local SQLite database because local email delivery is disabled.
- Login used existing `/api/mobile/auth/login`.
- Password is intentionally not recorded.

QA fixtures:

- Eight local notification fixtures were seeded for:
  - Messages
  - Calls
  - Social
  - Safety
  - Verification
  - Marketplace
  - Creator/Growth
  - Intelligence/Alerts

The first self-notification seed attempt was suppressed by existing backend rules, which correctly prevent self-notifications. The final fixture seed used actor `0` and created visible in-app notifications through the existing notification schema.

## Verified

Authenticated Activity Inbox:

- `/pulse/activity` rendered Activity Inbox.
- All category chips rendered:
  - All
  - Messages
  - Calls
  - Social
  - Safety
  - Verification
  - Marketplace
  - Creator/Growth
  - Intelligence
- Seeded activity rows rendered in the expected lanes after fix.
- Loading/signed-in route protection behaved safely.
- Empty/read state rendered `All current signals are read.`

Routes:

- `/pulse/activity`
- `/pulse/activity/messages`
- `/pulse/activity/calls`
- `/pulse/activity/social`
- `/pulse/activity/safety`
- `/pulse/activity/verification`
- `/pulse/activity/marketplace`
- `/pulse/activity/creator_growth`
- `/pulse/activity/intelligence_alerts`
- `/pulse/notifications`
- `/pulse/inbox`
- `/dashboard/activity`
- `/dashboard/inbox`

Entry points:

- Settings showed Activity Inbox.
- Notifications tab route rendered Activity Inbox.
- Legacy `/pulse/notifications` rendered Activity Inbox.

Mutations:

- Delete removed one QA notification from the Activity Inbox and reduced total unread from `8` to `7`.
- Mark read changed the remaining activity state to `All current signals are read.`
- Row controls changed from `Mark read` to `Read`.
- Badge title changed from `(8) Activity` to `Activity Inbox` after read state cleared.

Routing:

- Open action on a Creator/Growth activity routed into native Growth Center.
- The existing notification resolve endpoint can return `/pulse/notifications` as a safe fallback when a native-supported route is not a Flask web route. Activity Inbox now preserves the original server-provided target when the resolve response reports `fallback_used`, then passes it through the existing native route sanitizer/router.

Visual quality:

- Activity Inbox retained the dark control-center layout, readable hierarchy, glowing unread signal accents, and compact category rail.
- No internal design-system terminology is exposed in user-facing UI.

## Blockers Found And Fixed

### Social Notification Grouped Into Intelligence

Root cause:

- The display classifier did not explicitly recognize `post`, `like`, `comment`, `mention`, `follow`, `reaction`, `share`, `repost`, or `social`.
- A social QA notification body containing the word `signal` was grouped into Intelligence.

Fix:

- Updated `mobile-native/src/api/activity.ts` so social terms are classified before intelligence/market signal terms.

Retest:

- Social count rendered as `1`.
- Intelligence count rendered as `1`.

### Legacy Inbox Routes Fell Back To Home

Root cause:

- `notificationRouting.ts` handled `/pulse/inbox` and `/dashboard/inbox`, but React Navigation linking did not.
- Browser direct navigation depends on the linking map.

Fix:

- Added native linking aliases for:
  - `/pulse/inbox`
  - `/dashboard/activity`
  - `/dashboard/inbox`
- Restored the Notifications tab path to `/pulse/notifications`; the tab already renders Activity Inbox.

Retest:

- `/pulse/notifications`, `/pulse/inbox`, `/dashboard/activity`, and `/dashboard/inbox` rendered Activity Inbox with seeded signals.

### Category Counts Became Stale After Delete

Root cause:

- Category unread counts were stored separately from the Activity item list.
- Delete updated the item list and total unread count, but not the per-category count state.

Fix:

- Activity Inbox now derives per-category unread counts from the current item list.

Retest:

- Deleting the Intelligence activity removed its category count while keeping remaining counts accurate.

### Native Open Routing Used Server Web Fallback

Root cause:

- `/api/pulse/notifications/<id>/resolve` is intentionally conservative and validates Flask web routes.
- Some native-supported destinations, such as `/pulse/growth`, can be valid native routes but not direct Flask routes in the local backend.

Fix:

- Activity Inbox uses the original server-provided target when the resolve response reports `fallback_used`.
- The existing native route normalizer/router still sanitizes and handles the target.

Retest:

- Opening the Creator/Growth activity routed to native Growth Center.

## Console / Runtime Notes

During hot refresh after moving category-count logic, the browser console captured a transient `unreadCountsByCategory is not defined` error.

Follow-up actions:

- Moved the helper above the component definition.
- Restarted Expo web to test a clean bundle.
- Final clean-bundle browser check showed Activity Inbox and Growth Center routing with no visible runtime error text.

## Remaining Release QA

- Physical-device APNs/FCM push tap routing.
- App icon badge synchronization on device.
- Background notification delivery behavior.
- Read/delete against a seeded provider-backed QA account.
- Offline cache restore with network disabled in simulator/device.

No critical, security, data-loss, or production-breaking blockers were found.

## Recommendation

Recommended next highest-value native action: Native Events + Scheduled Live Gateway Foundation.

Reason: production PulseSoc already references events/live scheduled states and the native app now has Activity Inbox, Notifications, Live Viewer, Search/Discovery, Groups, Profile, Creator/Growth, Marketplace, and deep-link infrastructure that can all benefit from native event discovery and event detail routes. The current repo does not expose a dedicated native JSON event database/API, so the safest next step is to reuse scheduled live data and keep event creation, ticketing, checkout, and studio scheduling on safe web fallback.
