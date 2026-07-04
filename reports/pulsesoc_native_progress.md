# PulseSoc Native Migration Progress

Date: 2026-07-04

## Current Native State

The native app lives separately under `mobile-native/`. The current production WebView app remains untouched and continues to serve existing PulseSoc users.

Completed native foundations:

- App shell: Expo React Native, native stack/tab navigation, native deep-link configuration.
- Auth/session: login, signup, restore, logout through existing mobile auth APIs.
- API base URL/session safety: normalized API base URL, cookie-backed session reuse, network failure handling.
- Push registration: Expo push token registration through existing `/api/push/subscribe`.
- Mission Control: basic native connection to `/api/dashboard/mission-control`.
- Messenger: conversation list, conversation screen, text send, retry, receipts, typing, sync polling, offline cache, image/file/voice upload paths using existing Messenger APIs.
- Messenger hardening: corrupt-cache fallback, foreground/background sync recovery, upload-in-flight guard, long-thread list settings.
- Notifications: native notification center, unread/badge sync, mark read, mark all read, delete, preferences, push permission state, foreground badge refresh, background tap routing structure, and native/web target fallback.
- Home Feed + Post Detail: native feed list, pagination, pull-to-refresh, offline cache, post detail, comments, add comment, reactions, save, repost, share hook, image media cards, and `/pulse/post/<post_id>` deep-link routing through existing PulseSoc APIs.
- Pulse AI: basic chat through existing `/api/pulse/assistant/chat`.
- Profile: native current profile, public profile route, profile posts/media/about tabs, profile edit, avatar/cover upload/remove, profile theme selection, offline cache, and profile deep links through existing PulseSoc profile/feed/theme APIs.
- Reels Player + Reel Detail: native full-screen vertical Reels feed, Expo AV video playback, Mux/R2 media URL reuse, infinite scrolling, pull-to-refresh, metadata cache, comments, reactions, save, repost, share, follow creator, not interested, report, view tracking, profile navigation, and `/pulse/reels/<reel_id>` deep-link routing through existing Reels APIs.
- Status Viewer + Status Detail: native Status rail, Status list, full-screen viewer, image/video/text rendering, tap navigation, view tracking, reactions, replies, shares, music display, offline metadata cache, and `/pulse/status/<status_id>` deep-link routing through existing Status APIs.
- Media Capture + Upload Foundation: shared native image picker, video picker, camera entry point, permission states, file validation, upload progress, retry, cancellation, processing-status polling, reusable upload hook/service, and reusable media preview component over existing PulseSoc media APIs.
- Feed Composer Foundation: native composer entry from Home Feed, text/title publishing, visibility selector, image/video/camera attachment through the shared media upload layer, upload preview/progress/retry/cancel, publish states, and feed refresh through existing PulseSoc post APIs.
- Status Creator Foundation: native Status composer entry, text/image/video Status publishing, camera/gallery integration, shared upload preview/progress/retry/cancel, privacy/duration selectors, music search/trending hooks, AI Story generation hook, and Status rail refresh through existing PulseSoc Status APIs.
- Media Viewer Foundation: shared full-screen native image/video viewer, pinch-to-zoom image structure, swipe-down close, previous/next navigation, processing-status checks, share/save/profile hooks, metadata display, and integrations for Feed/Post/Profile, Messenger attachments, and Status media hooks.
- Marketplace Browse + Listing Detail Foundation: native Marketplace tab, search/browse through existing marketplace API, listing cards, listing detail modal, media gallery through NativeMediaViewer, save/report/contact seller hooks, safe checkout routing, offline cache, and marketplace deep-link routing.
- Search + Discovery Foundation: native Search tab/route, debounced global search through existing `/api/pulse/search`, recent and suggested searches, discovery tabs, grouped result cards, pull-to-refresh, cached result fallback, native destination routing, `/pulse/search` deep-link routing, and web fallback for unsupported result URLs.
- Settings: session controls, push registration, notification preferences entry.

Completed supporting reports/audits:

- `reports/pulsesoc_native_app_api_contract.md`
- `reports/pulsesoc_native_app_migration_plan.md`
- `reports/pulsesoc_native_dependency_graph.md`
- `reports/pulsesoc_native_phase1_device_qa.md`
- `reports/pulsesoc_native_messenger_progress.md`
- `reports/pulsesoc_native_messenger_device_qa.md`
- `reports/pulsesoc_native_notifications_progress.md`
- `reports/pulsesoc_native_feed_progress.md`
- `reports/pulsesoc_native_profile_progress.md`
- `reports/pulsesoc_native_reels_progress.md`
- `reports/pulsesoc_native_status_progress.md`
- `reports/pulsesoc_native_media_upload_progress.md`
- `reports/pulsesoc_native_feed_composer_progress.md`
- `reports/pulsesoc_native_status_creator_progress.md`
- `reports/pulsesoc_native_media_viewer_progress.md`
- `reports/pulsesoc_native_marketplace_progress.md`
- `reports/pulsesoc_native_search_progress.md`
- `scripts/pulsesoc_native_app_foundation_audit.py`
- `scripts/pulsesoc_native_phase1_device_qa_audit.py`
- `scripts/pulsesoc_native_messenger_audit.py`
- `scripts/pulsesoc_native_messenger_device_qa_audit.py`
- `scripts/pulsesoc_native_notifications_audit.py`
- `scripts/pulsesoc_native_feed_audit.py`
- `scripts/pulsesoc_native_profile_audit.py`
- `scripts/pulsesoc_native_reels_audit.py`
- `scripts/pulsesoc_native_status_audit.py`
- `scripts/pulsesoc_native_media_upload_audit.py`
- `scripts/pulsesoc_native_feed_composer_audit.py`
- `scripts/pulsesoc_native_status_creator_audit.py`
- `scripts/pulsesoc_native_media_viewer_audit.py`
- `scripts/pulsesoc_native_marketplace_audit.py`
- `scripts/pulsesoc_native_search_audit.py`

## Remaining Major Features

- Advanced camera/compression/editor tools
- Live discovery
- Native LiveKit calls
- Full-screen incoming calls
- Growth Center
- Crypto/market alerts
- Intelligence Center
- Premium/entitlements
- Creator Studio
- Groups/communities
- Saved content

## Codebase Reconnaissance

The next recommendation is based on the current codebase, not guesswork.

Existing backend/web surfaces inspected:

- Feed page and shell: `pulse_page_html(...)`, `/pulse`
- Feed API: `GET /api/pulse/feed`
- Post create: `POST /api/pulse/posts`
- Post detail: `GET /api/pulse/posts/<post_id>` and `GET /api/pulse/post/<post_id>`
- Post reactions: `POST /api/pulse/posts/<post_id>/react`
- Post comments: `GET/POST /api/pulse/posts/<post_id>/comments`
- Save, pin, repost, delete, report, follow: existing `/api/pulse/posts/*`, `/api/pulse/follow`, `/api/pulse/report`, saved-content APIs
- Media upload: existing `/api/pulse/media/upload` and `media_service.save_upload(...)`
- Feed engine: `services/pulse_feed_engine.py`
- Feed ranking: `services/pulse_feed_ranking_engine.py`
- Moderation: `services/pulse_moderation_engine.py`
- Notifications into feed/post targets: `static/notifications.js` and server target resolver
- Profile routes: `/pulse/profile`, `/pulse/profile/<profile_key>`, `/pulse/profile/edit`
- Profile APIs/media/theme: `/api/pulse/profile/me`, `/api/pulse/profile/update`, `/api/pulse/profile/avatar`, `/api/pulse/profile/cover`, `/api/pulse/profile/avatar/remove`, `/api/pulse/profile/cover/remove`, `/api/pulse/premium/profile-theme`
- Reels APIs: `/api/pulse/reels/feed`, `/api/pulse/reels/<reel_id>/react`, comments, save, repost, share, not-interested, follow creator
- Status APIs: `/api/pulse/status/rail`, `/api/pulse/status`, view, react, reply, share
- Status data/business logic: `pulse_status`, `pulse_status_media`, `pulse_status_music`, `pulse_status_views`, `pulse_status_reactions`, `pulse_status_replies`
- Marketplace routes: `/pulse/marketplace`, `/pulse/marketplace/create`, `/pulse/merchant/dashboard`, `/pulse/merchant/<username>`
- Marketplace APIs: `/api/pulse/marketplace/search`, `/api/pulse/marketplace/seller/apply`, `/api/pulse/marketplace/listings/create`, `/api/pulse/marketplace/media/upload`, `/api/pulse/marketplace/listings/save`, `/api/pulse/marketplace/listings/report`
- Marketplace data/business logic: `marketplace_listings`, `marketplace_product_media`, `marketplace_sellers`, `marketplace_saved_products`, `marketplace_reports`, `marketplace_orders`, seller readiness, promotions, and moderation/revenue safety services
- Search APIs and web bridge: `/api/pulse/search`, `/pulse/search`, `static/js/pulse_search_bridge.js`, and search handling in `static/js/pulse_home_core.js`
- Saved APIs and web route: `/pulse/saved`, `GET/POST /api/pulse/saved`, saved collections, delete, and move endpoints

Existing data/business logic that should remain server-authoritative:

- `pulse_posts`
- `pulse_comments`
- `pulse_reactions`
- `pulse_post_saves`
- `pulse_saved_items`
- `pulse_media_assets`
- `chat_media_uploads`
- `users`
- feed ranking and visibility rules
- moderation/risk status
- premium identity rendering data
- notification fanout
- mention notifications
- media storage/Mux processing
- saved-content collections
- follow graph
- marketplace listing moderation/safety
- seller trust/readiness
- marketplace save/report behavior
- marketplace order/payment/payout rules
- PulseSoc search ranking/grouping/result routing
- saved item collection ownership and removal

## Recommended Next Feature

Recommendation: build Native Saved Content + Collections Foundation next.

This should come before Marketplace seller tools, Creator Studio, Growth Center, Premium, or native LiveKit calls.

## Why This Comes Next

- The backend already exposes mature saved-content routes and APIs: `/pulse/saved`, `GET/POST /api/pulse/saved`, saved collections, delete, and move endpoints.
- The native app now has many save-producing surfaces: Home Feed/Post Detail, Reels, Status, Marketplace, Media Viewer hooks, and Search result routing.
- A native Saved screen turns existing save actions into a visible, reusable library without inventing new business logic.
- Saved content is lower risk than Live/Calls and higher leverage than isolated seller or creator tooling because it connects most migrated surfaces.
- This recommendation is based on current backend routes, current native migration progress, and the existing saved-content database/service code.

## Reusable Existing PulseSoc Logic

Reuse directly:

- Saved page and API contracts: `/pulse/saved`, `GET/POST /api/pulse/saved`.
- Saved collection APIs: `GET/POST /api/pulse/saved/collections`, patch/delete collection routes.
- Saved item actions: `DELETE /api/pulse/saved/<item_id>` and `POST /api/pulse/saved/<item_id>/move`.
- Existing saved tables: `pulse_saved_items`, `pulse_saved_collections`, and `pulse_saved_sounds`.
- Existing saved snapshot behavior: `pulse_saved_snapshot(...)`.
- Existing saved collection ownership and fallback collection logic.
- Existing saved actions already used by posts, reels, statuses, videos, marketplace, and sounds.
- Native result routing for posts, profiles, reels, status, marketplace, messenger, media viewer, Search, and web fallback.

Do not duplicate in native:

- Saved item ownership checks.
- Collection authorization.
- Snapshot construction.
- Save/un-save persistence rules.
- Content visibility validation.
- Collection deletion fallback behavior.
- Server-side validation.

## What Must Be Rebuilt Natively

- Native Saved screen.
- Saved item list with type filters.
- Collection selector and collection chips.
- Search/filter query input.
- Remove saved item action.
- Move saved item to collection action.
- Create collection action.
- Native routing from saved items into existing native destinations.
- Offline cache, pull-to-refresh, loading, empty, error, and retry states.
- Web fallback for saved item types without native destinations.

## Dependencies And Blockers

Dependencies:

- Confirm `GET /api/pulse/saved` response shape for items and collections.
- Map saved item `source_url`, `content_type`, and `content_id` to existing native routes.
- Reuse existing save actions instead of adding client-only saved state.
- Keep unsupported content types on web fallback.

Blockers:

- Real-device collection picker ergonomics and saved-item routing must be verified before replacing the WebView saved library.
- Some saved content types such as full videos, music, groups, rooms, or future creator tools may still require web fallback until their native surfaces exist.

## Risk Level

Risk: Medium.

Reasons:

- Saved content is read/action heavy but uses mature server-owned endpoints.
- Main risk is incorrect item routing or collection move/remove state drift.
- Backend risk stays low if native does not duplicate collection ownership, snapshot, or visibility rules.

## Estimated Complexity

Complexity: Medium.

Recommended first slice:

- Native Saved route/screen.
- API wrapper for saved items and collections.
- Type filters and collection filter.
- Saved item cards.
- Pull-to-refresh and offline cache.
- Remove item and move-to-collection actions.
- Create collection action.
- Native/web fallback routing.
- Static audit proving saved business logic stays backend-owned.

Defer from first slice:

- Bulk saved-item management.
- Advanced collection editing.
- Rich media previews for unsupported saved types.
- Saved-sound player UX unless the current native audio stack is ready.

## Safest Implementation Plan

1. Inspect `/api/pulse/saved` and saved collection response shapes.
2. Add native saved API wrappers without changing backend routes.
3. Add a native Saved screen and route.
4. Render saved item cards using existing snapshot payload fields.
5. Add collection/type filtering through existing API query parameters.
6. Route supported saved URLs into existing native destinations.
7. Keep unsupported content types on explicit web fallback.
8. Add a focused Saved audit and keep production WebView untouched.

## Recommendation Summary

Build Native Saved Content + Collections Foundation next. The native app now has enough migrated content surfaces for saved items to become valuable, and the existing `/api/pulse/saved` contract lets native reuse PulseSoc ownership, collection, snapshot, visibility, and deletion behavior while rebuilding only the native library UI.
