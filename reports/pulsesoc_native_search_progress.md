# PulseSoc Native Search + Discovery Progress

Date: 2026-07-04

## Scope

The native Search + Discovery foundation lives only under `mobile-native/`. It does not touch production WebView paths, production templates, backend search routes, database queries, ranking, privacy, moderation, or permissions.

Server APIs stay authoritative. Native Search is a new client for the existing PulseSoc global search contract.

## Existing Web/Backend Implementation Inspected

Current PulseSoc search surfaces inspected before implementation:

- Global search API: `GET /api/pulse/search`.
- Search page: `/pulse/search`.
- Web search bridge: `static/js/pulse_search_bridge.js`.
- Home shell search overlay: search handling in `static/js/pulse_home_core.js`.
- Backend result groups: posts, creators, videos, reels, statuses, marketplace, music, groups, rooms, and comments.
- Search database sources: `pulse_posts`, `pulse_comments`, `users`, `pulse_groups`, default room cards, `pulse_reels`, `pulse_status`, `pulse_videos`, marketplace listings, and music search service.
- Native destinations: Post Detail, Profile, Reels, Status, Marketplace, Messenger, Notifications, and web fallback routing.

## Implemented Native Foundation

- Native Search tab and stack route.
- Native search API wrapper over existing `/api/pulse/search`.
- Debounced search input using the same server query contract and grouped result shape used by the web bridge.
- Recent searches stored locally in native AsyncStorage.
- Suggested/trending search chips from the backend payload with safe native defaults.
- Discovery tabs for All, People, Posts, Reels, Status, Marketplace, Communities, Events, Trending, and Hashtags.
- Result rendering for server-provided grouped results.
- Pull-to-refresh for the active query.
- Local cached result fallback for the last successful query.
- Infinite scrolling presentation over returned grouped results.
- Loading, empty, offline, unsupported-tab, and error states.
- Deep-link routing for result URLs through the existing native notification target router.
- `/pulse/search` notification/deep-link routing into native Search.
- Web fallback remains available for unsupported result URLs and unsupported native destinations.

## Reuse-First Boundaries

Native Search does not implement its own:

- Search ranking.
- Search indexing.
- Search database queries.
- Visibility filtering.
- Moderation filtering.
- Marketplace search logic.
- Creator search permissions.
- Group or room discovery authorization.
- AI search summarization.
- Server-side validation or rate limiting.

Those remain owned by the existing PulseSoc backend, database, moderation layer, privacy checks, and search route.

## Native-Only Layer

The rebuilt native layer is limited to:

- Search input UI.
- Discovery tab UI.
- Recent and suggested search chip UI.
- Result card UI.
- Cached-result display.
- Loading, empty, offline, unsupported, and error states.
- Native navigation and deep-link routing.

## Known Gaps

- The current backend search endpoint does not expose dedicated `events`, `trending`, or `hashtags` result groups, so those tabs are shown as native placeholders until matching backend groups exist.
- Search pagination is not exposed by the current `/api/pulse/search` contract. Native infinite scrolling currently reveals more of the returned grouped result set without inventing backend pagination.
- Unsupported result types such as music detail, full video detail, groups, and rooms continue to use existing web fallback routes until those native destinations are built.
- Search result media previews are not included in the current global search payload, so native result cards stay text/avatar-first.

## Device-Only Behavior Not Verified

The following are not marked as passed without device access:

- Real-device typing latency.
- Real-device keyboard behavior.
- Real-device search result scroll performance.
- Real-device deep-link tap routing from push notifications.
- Real-device fallback behavior for unsupported result URLs.
- Real-device offline cache restore after app termination.

## Next Recommendation

Recommended next native feature: Native Saved Content + Collections Foundation.

Reason: the current codebase already has mature saved-content routes and APIs, and the native app now has many features that can save content: Feed/Post, Reels, Status, Marketplace, media flows, and Search result routing. A native Saved screen would connect those save actions into a user-visible library without changing server-owned collection, ownership, or removal rules.
