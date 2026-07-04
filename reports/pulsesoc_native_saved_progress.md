# PulseSoc Native Saved Content + Collections Progress

Date: 2026-07-04

## Scope

The native Saved Content + Collections foundation lives only under `mobile-native/`. It does not touch production WebView paths, production templates, backend saved routes, collection ownership logic, saved snapshot generation, database tables, moderation, privacy, or permissions.

Server APIs stay authoritative. Native Saved is a new client for the existing PulseSoc saved-content and collection contracts.

## Existing Web/Backend Implementation Inspected

Current PulseSoc saved surfaces inspected before implementation:

- Saved page: `/pulse/saved`.
- Saved item list/create API: `GET/POST /api/pulse/saved`.
- Collection list/create API: `GET/POST /api/pulse/saved/collections`.
- Collection edit/delete API: `PATCH/DELETE /api/pulse/saved/collections/<collection_id>`.
- Saved item delete API: `DELETE /api/pulse/saved/<item_id>`.
- Saved item move API: `POST /api/pulse/saved/<item_id>/move`.
- Post save API: `POST /api/pulse/posts/<post_id>/save`.
- Reel save flow: existing Reels save endpoint and `pulse_saved_items` integration.
- Status save flow: existing Status save calls into `/api/pulse/saved`.
- Marketplace save flow: existing marketplace listing save endpoint.
- Saved tables: `pulse_saved_items`, `pulse_saved_collections`, and `pulse_saved_sounds`.
- Saved server helpers: `pulse_saved_snapshot(...)`, `pulse_saved_items_query(...)`, and `ensure_pulse_saved_collection(...)`.
- Native destination routing: Post Detail, Reels, Status, Marketplace, Profile, Messenger, Search, and web fallback.

## Implemented Native Foundation

- Native Saved tab and stack route.
- Native saved API wrapper over existing `/api/pulse/saved` and collection endpoints.
- Saved Content screen.
- Saved Posts, Reels, Status, Videos, Marketplace, Rooms, Groups, and Learning filters using existing backend query parameters.
- Collections list and collection filter chips.
- Collection create, rename, and delete actions where the existing backend supports them.
- Saved item remove action through the existing delete endpoint.
- Saved item move action through the existing move endpoint.
- Search/filter saved content using the existing `q` parameter.
- Offline cache for the most recent saved library response.
- Loading, empty, offline, and error states.
- Deep-link routing from saved items through the existing native notification target router.
- `/pulse/saved` notification/deep-link routing into native Saved.
- Web fallback remains available for unsupported saved item source URLs.

## Reuse-First Boundaries

Native Saved does not implement its own:

- Saved item ownership checks.
- Saved collection authorization.
- Default collection behavior.
- Collection deletion fallback behavior.
- Saved snapshot generation.
- Content visibility checks.
- Save/unsave business rules.
- Marketplace save persistence.
- Reel/status/video save business logic.
- Server-side validation.

Those remain owned by the existing PulseSoc backend, database, saved-content helpers, and content-specific save endpoints.

## Native-Only Layer

The rebuilt native layer is limited to:

- Saved list UI.
- Saved type filter UI.
- Collection filter and collection management UI.
- Saved item card UI.
- Button states for open, move, remove, create, rename, and delete.
- Cached-result display.
- Loading, empty, offline, and error states.
- Native navigation and deep-link routing.

## Known Gaps

- The current saved API provides snapshot fields rather than full post/reel/status/listing payloads, so native Saved uses compact cards and routes to the native destination for full detail.
- Move uses a safe single-tap "next collection" action in this foundation. A richer picker should be device-tested before replacing the WebView collection UI.
- Saved item add is exposed in the native API wrapper but the Saved screen does not invent a generic add form. Native save actions should continue to originate from the content surfaces that already own save behavior.
- Unsupported saved content types such as music, full video detail, groups, rooms, and future creator tools continue to use web fallback until those native destinations are built.

## Device-Only Behavior Not Verified

The following are not marked as passed without device access:

- Real-device saved list scroll performance.
- Real-device collection chip ergonomics.
- Real-device rename/create keyboard behavior.
- Real-device saved item routing from push/deep links.
- Real-device offline cache restore after app termination.
- Real-device fallback behavior for unsupported saved item URLs.

## Next Recommendation

Recommended next native feature: Native Groups, Communities + Rooms Foundation.

Reason: the current backend already contains extensive group routes, join/leave/chat/report/moderation APIs, group post/comment APIs, and default room logic. Search and Saved can already surface groups and rooms, but they still fall back to web. Native Groups/Rooms would close that navigation gap and reuse the existing social graph, moderation, membership, chat, and group post rules.
