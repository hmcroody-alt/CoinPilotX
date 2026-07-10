# PulseSoc LogiNexus Home Component Map

## Native Components Updated

- `HomeScreen.tsx`
  - Shared global command strip.
  - Pulse Network hero.
  - server-derived hero metrics.
  - Status rail.
  - feed tabs.
  - event-sync refresh/invalidation hooks.

- `HomePulseComposer.tsx`
  - Transmission Console presentation.
  - draft persistence.
  - upload queue handoff.
  - validation, publish, success, failure, and retry states.

- `PostCard.tsx`
  - Signal Card presentation.
  - author identity hierarchy.
  - creator/verified/premium badge treatment.
  - feed interactions.
  - `NativeMediaViewer` handoff.

- `GlobalNavigation.tsx`
  - PulseSoc / LOGINEXUS command strip treatment.
  - shared bottom dock with emphasized Create action.

## Backend Contracts Reused

- Feed list/cursor cache from `../api/feed`.
- Status rail from `../api/status`.
- Post publishing from `../api/posts` through the existing composer.
- Media upload/camera handoff through existing native media helpers.
- Event sync invalidation through `../core/eventSync`.

## Native Route Handoffs Preserved

- Home to Search.
- Home to Activity Inbox.
- Home to Profile.
- Home to UNDX/Pulse AI.
- Home to Pulse Radio gateway.
- Home to Live.
- Home to Safety Hub.
- Home to Camera Studio.
- Home to Status creation/viewing.
- Feed cards to Post Detail, Profile Detail, Safety Hub, and NativeMediaViewer.
