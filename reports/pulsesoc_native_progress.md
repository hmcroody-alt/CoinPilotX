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
- Saved Content + Collections Foundation: native Saved tab/route, saved item list, type filters, collection filters, create/rename/delete collection actions, remove/move saved item actions, saved search, offline cache, item deep-link routing, and `/pulse/saved` deep-link routing through existing saved APIs.
- Groups/Communities + Rooms Foundation: native Groups tab/detail route, thin read-only group JSON bridge, communities browse/search, room rail, group detail, rules/member metadata, compact group feed preview, join/leave, report, group chat open, room open, offline cache, and group/room deep-link routing through existing group, room, and Messenger APIs.
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
- `reports/pulsesoc_native_saved_progress.md`
- `reports/pulsesoc_native_groups_progress.md`
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
- `scripts/pulsesoc_native_saved_audit.py`
- `scripts/pulsesoc_native_groups_audit.py`

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
- Architecture health and shared-core consolidation

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
- Groups and rooms routes: `/pulse/groups`, `/pulse/groups/create`, `/pulse/groups/<group_slug>`, `POST /api/pulse/groups/create`, join/leave APIs, chat-open APIs, invite/report/update/moderation APIs, group post/comment APIs, `pulse_default_room_cards()`, and `pulse_ensure_default_rooms(...)`
- Current native reuse surface: repeated API wrappers, AsyncStorage cache functions, screen-level loading/empty/error/offline states, native/web fallback routing, card layouts, action busy states, media preview/viewer hooks, and tab/stack route patterns across Feed, Messenger, Notifications, Profile, Reels, Status, Marketplace, Search, Saved, and Groups.

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
- group membership, roles, moderation, invite links, group chats, and group post/comment rules
- shared native routing, caching, error, card, and media-viewer behavior that should be consolidated before deeper Live/Calls/Premium work

## Recommended Next Feature

Recommendation: produce a Native Architecture Health Report + Shared Core Consolidation checkpoint next.

This should come before Marketplace seller tools, Creator Studio, Growth Center, Premium, or native LiveKit calls.

## Why This Comes Next

- The native app now has a substantial surface area: auth, Messenger, Notifications, Feed/Post, Profile, Reels, Status, media upload/viewer, Marketplace, Search, Saved, and Groups/Rooms.
- Multiple features now repeat the same patterns: API wrapper normalization, AsyncStorage cache, loading/empty/error/offline state, card layout, action busy state, native/web fallback routing, and report/audit conventions.
- The next major features, especially Live, Calls, Creator Studio, Growth, and Premium, will increase complexity and should not be built on duplicated native infrastructure.
- A consolidation checkpoint directly follows the user's permanent rule: if three or more features use the same UI or service, promote it into `mobile-native/src/shared` or `mobile-native/src/core` with documentation and tests where appropriate.
- This recommendation is based on current native code structure and the repeated implementation patterns across the already migrated features.

## Reusable Existing PulseSoc Logic

Reuse directly:

- Existing API wrappers and normalization style in `mobile-native/src/api/*`.
- Existing AsyncStorage cache patterns in Feed, Messenger, Marketplace, Search, Saved, and Groups.
- Existing navigation and fallback routing in `notificationRouting.ts` and `linking.ts`.
- Existing reusable components: `PostCard`, `ProfileHeader`, `NativeMediaViewer`, `FeedComposer`, `StatusCreator`, `StatusViewerCard`, and `ReelPlayerCard`.
- Existing loading, empty, error, offline, refresh, and busy-action patterns across native screens.
- Existing audit/report conventions in `scripts/pulsesoc_native_*_audit.py` and `reports/pulsesoc_native_*_progress.md`.

Do not duplicate in native:

- Backend business logic.
- API response normalization patterns.
- Cache load/save boilerplate.
- Native route fallback parsing.
- Loading/empty/error/offline state rendering.
- Shared card/action UI.
- Media preview/viewer logic.

## What Must Be Rebuilt Natively

- Architecture health report covering component reuse, service reuse, navigation consistency, API wrapper consistency, cache consistency, and duplicate code.
- Shared-core consolidation plan.
- Candidate shared modules under `mobile-native/src/shared` or `mobile-native/src/core`.
- Optional low-risk extraction of clear duplicate utilities if the audit finds safe consolidation points.
- Focused tests or static audits for any shared utilities introduced.

## Dependencies And Blockers

Dependencies:

- Inspect every `mobile-native/src/api`, `mobile-native/src/screens`, `mobile-native/src/components`, `mobile-native/src/navigation`, and `mobile-native/src/utils` file.
- Identify duplicated cache, fetch, action-state, route, card, media, and empty/error UI patterns.
- Avoid broad refactors if they risk feature regressions.
- Keep any extraction additive and covered by static audits.

Blockers:

- Real-device behavior cannot be inferred from static architecture cleanup.
- Some consolidation may need to wait until shared behavior is verified across simulator/real device.
- Avoid moving feature-specific logic into shared modules before three or more consumers prove it is stable.

## Risk Level

Risk: Medium.

Reasons:

- Architecture consolidation can create regressions if done too broadly.
- Risk stays medium if the next step is mostly audit/reporting plus only small, obvious shared extractions.
- Backend risk stays low because this checkpoint does not change server business logic.

## Estimated Complexity

Complexity: Medium.

Recommended first slice:

- Inventory repeated native patterns and dependencies.
- Score each candidate shared component/service by number of consumers, risk, and extraction effort.
- Recommend `src/shared` or `src/core` structure and naming.
- Extract only the lowest-risk duplicate utilities if they are clearly used by three or more features.
- Add a native architecture health report and audit script.
- Keep feature behavior unchanged.

Defer from first slice:

- Large component rewrites.
- Styling system overhauls.
- Navigation architecture rewrites.
- Changes to backend API contracts.
- Live/Calls/Creator/Premium feature work.

## Safest Implementation Plan

1. Inspect all native API wrappers, screens, components, navigation, and utils.
2. Identify duplicated code used by three or more features.
3. Produce a health report with findings, recommended shared-core structure, and risk-ranked consolidation plan.
4. Implement only low-risk shared utilities/components if the codebase clearly supports extraction.
5. Add an audit script that enforces documentation of shared-core boundaries and confirms production WebView paths remain untouched.
6. Run the standard native verification suite.

## Recommendation Summary

Produce the Native Architecture Health Report + Shared Core Consolidation checkpoint next. The native app has enough feature surface that consolidating repeated API, cache, routing, state, and UI patterns will reduce risk before entering the more complex Live, Calls, Creator Studio, Growth, and Premium phases.
