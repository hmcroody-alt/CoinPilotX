# PulseSoc Native Events + Scheduled Live Gateway Foundation

Date: 2026-07-05

## Scope

Built a native Events and Scheduled Live gateway for the parallel PulseSoc native app.

This is a native client layer over existing PulseSoc Live and event gateway behavior. It does not create a new event backend, ticketing system, checkout flow, or scheduling authority.

## Production Codebase Inspection

Production PulseSoc currently exposes:

- `/pulse/events`
- `/pulse/live/schedule`
- `/pulse/live/events/create`
- `/api/pulse/live-now`
- `/pulse/live/<live_id>`
- `/pulse/live/studio`

The production `/pulse/events` route explicitly states that event discovery routes into live scheduling and community surfaces until a dedicated events database is enabled. The schedule/create gateways also state that scheduling, stream setup, eligibility, and ticketing remain backend-controlled.

## Reused Backend/API/Business Logic

- Existing `/api/pulse/live-now` data source through `listLiveNow()`.
- Existing Live item normalization, scheduled state detection, and offline cache path.
- Existing Live Viewer route for join/watch.
- Existing Live Studio web fallback for hosting, co-hosting, and stream setup.
- Existing production event gateway routes.
- Existing notification/deep-link normalization.
- Existing profile navigation through host author metadata.

## Native Work Added

- `mobile-native/src/api/events.ts`
  - Adapts scheduled Live payloads into native Event cards.
  - Uses the existing Live discovery cache as fallback.
  - Provides safe web fallback helpers for Events, Schedule Live, Create Live Event, and Live Studio.

- `mobile-native/src/screens/EventsScreen.tsx`
  - Native Events screen.
  - Scheduled Live list.
  - Event detail screen.
  - Join/watch handoff into native Live Viewer.
  - Host/Profile navigation.
  - Share hook.
  - Loading, empty, offline, and error states.
  - Safe fallback controls for schedule/create/studio.

- Navigation/deep links:
  - `/pulse/events`
  - `/pulse/events/<event_id>`
  - `/pulse/live/schedule`
  - `/pulse/live/events/create`

- Entry points:
  - Settings: Events and scheduled Live.
  - Search/Discovery Events tab shortcut.
  - Notification routing for event and scheduled-live links.

## Safe Fallbacks

The following remain on web fallback by design:

- Event creation.
- Ticketing/payment.
- Live Studio.
- Hosting/co-hosting.
- Unsupported advanced event tools.

## QA Notes

Browser route checks are practical for rendering, routing, auth, and fallback visibility. Full reminder provider behavior, ticket/payment flows, and live hosting remain release-level device/provider QA because dedicated native contracts are not present.

Authenticated QA browser route checks completed with a disposable local QA account/session:

- `/pulse/events` rendered the native Events screen.
- `/pulse/events/1` rendered the native Event detail shell.
- `/pulse/live/schedule` rendered the native Schedule Live gateway.
- `/pulse/live/events/create` rendered the native Create Live Event gateway.
- Settings rendered the `Events and scheduled Live` entry.
- Search/Discovery Events tab rendered the native gateway shortcut.
- No visible runtime error text was detected during these checks.

The local QA backend returned no scheduled events, so empty-state rendering was verified. Provider-backed scheduled event data remains pending seeded backend/device QA.

## Remaining Gaps

- Dedicated native JSON events API/database is not present in the inspected repo.
- Reminder/notify-me endpoint was not found; the native UI does not fake local reminder authority.
- Ticketing and event checkout are explicitly not configured in current production gateway copy.
- Native event creation should wait for backend event contracts.

## Next Recommendation

Recommended next highest-value action: Native Content Planner + Scheduled Publishing Gateway Foundation.

Reason: the production backend already supports `/api/dashboard/content-planner/item` plus content planner, draft studio, and post scheduler web flows. Native Creator Studio currently only saves a simple draft and opens advanced planner flows on web fallback. A native planner gateway would reuse the existing creator/content planner backend while improving the creator workflow without inventing publishing or scheduling authority.
