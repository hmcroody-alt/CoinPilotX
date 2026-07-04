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
- Search/discovery

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

Recommendation: build Native Search + Discovery Foundation next.

This should come before Marketplace seller tools, Creator Studio, Growth Center, or advanced Camera tools.

## Why This Comes Next

- The backend already exposes `/api/pulse/search`, and the web bridge searches posts, creators, videos, reels, statuses, marketplace listings, music, groups, rooms, and comments.
- The native app now has real destinations for many result types: Post Detail, Profile, Reels, Status, Marketplace, Messenger, media viewer, and web fallback for unsupported targets.
- Search is a high-leverage navigation layer across every feature already migrated, and it reduces dependence on broad WebView fallbacks.
- Search can reuse existing server ranking/grouping and result URLs without duplicating discovery logic in native.
- This recommendation is based on current backend routes, current native destination coverage, and migration progress.

## Reusable Existing PulseSoc Logic

Reuse directly:

- Existing global search API: `GET /api/pulse/search`.
- Existing grouped result contract used by `pulse_search_bridge.js` and `pulse_home_core.js`.
- Existing result URLs for posts, profiles, videos, reels, statuses, marketplace listings, music, groups, rooms, and comments.
- Existing server-side search ranking/filtering/grouping.
- Existing native notification target routing and deep-link helpers.
- Existing native destinations for Post Detail, Profile, Reels, Status, Marketplace, Messenger, and web fallback.
- Existing saved/search history behavior if exposed by the backend payload.

Do not duplicate in native:

- Search ranking.
- Search indexing.
- Search permissions/visibility filtering.
- Marketplace/search moderation.
- Result authorization.
- Destination business logic.
- Server-side query validation.
- Server-side validation.

## What Must Be Rebuilt Natively

- Native Search screen.
- Search input with debounced query.
- Grouped result sections.
- Recent/trending search chips where supported.
- Result routing into existing native destinations.
- Unsupported-result web fallback.
- Loading, empty, offline, error, retry, and cancelled-query states.
- Optional local cache of last successful search results.

## Dependencies And Blockers

Dependencies:

- Confirm `/api/pulse/search` response shape and grouped result keys.
- Map result URLs to existing native navigation targets.
- Preserve backend visibility/ranking rules.
- Keep unsupported result types on web fallback until native surfaces exist.

Blockers:

- Real-device search typing latency and result routing must be verified before production replacement.
- Some result types may not have native destinations yet and must remain explicit web fallback.

## Risk Level

Risk: Medium.

Reasons:

- Search is read-mostly and can reuse server result grouping and native/web routing.
- Main risk is incorrect result routing or stale native fallback behavior.
- Backend risk stays low if native does not duplicate ranking, permissions, or indexing.

## Estimated Complexity

Complexity: Medium.

Recommended first slice:

- Native Search route/screen.
- Query input and submit/debounce behavior.
- Grouped result rendering from existing `/api/pulse/search`.
- Native routing for posts, profiles, reels, status, marketplace, messenger, and notifications where supported.
- Web fallback for unsupported result URLs.
- Loading, empty, error, offline, and retry states.
- Static audit proving search ranking/permissions stay backend-owned.

Defer from first slice:

- New search ranking.
- New search index tables.
- AI search summarization.
- Saved search sync unless the backend already returns it.
- Advanced filters not exposed by the current API.

## Safest Implementation Plan

1. Inspect `/api/pulse/search` response shape and current web search bridge renderer.
2. Add native search API wrappers without changing backend routes.
3. Add a native Search screen and route.
4. Render grouped results using existing result payload fields.
5. Map supported result URLs into existing native destinations.
6. Keep unsupported targets on explicit web fallback.
7. Add a focused Search audit and keep production WebView untouched.

## Recommendation Summary

Build Native Search + Discovery Foundation next. The native app now has enough migrated destinations for search to become useful, and `/api/pulse/search` lets native reuse existing PulseSoc discovery, ranking, visibility, result URLs, and grouping while rebuilding only the native search UI and routing layer.
