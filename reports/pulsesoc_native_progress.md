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

## Recommended Next Feature

Recommendation: build Native Groups, Communities + Rooms Foundation next.

This should come before Marketplace seller tools, Creator Studio, Growth Center, Premium, or native LiveKit calls.

## Why This Comes Next

- The backend already contains extensive group and room infrastructure: group browse/detail pages, create/join/leave/chat-open/report/update/moderation APIs, group posts/comments, invite links, default room cards, and room seeding.
- Search and Saved now surface groups and rooms, but those targets still rely on web fallback because native group/room destinations do not exist.
- Messenger is already native, so opening group chat/rooms natively can reuse the existing Messenger destination and conversation behavior.
- Groups/Rooms is lower risk than LiveKit calls and more connected than isolated premium/growth tooling because it ties Search, Saved, Messenger, Notifications, and Feed-style group posts together.
- This recommendation is based on current backend routes, default room helpers, database tables, and native migration coverage.

## Reusable Existing PulseSoc Logic

Reuse directly:

- Existing group pages: `/pulse/groups`, `/pulse/groups/create`, and `/pulse/groups/<group_slug>`.
- Existing group APIs: create, join, leave, chat open, invite links, invite, report, update, delete, moderation, member role, ban/unban, and remove-member endpoints.
- Existing group post/comment APIs and moderation/report/delete/pin flows.
- Existing default room logic: `pulse_default_room_cards()` and `pulse_ensure_default_rooms(...)`.
- Existing group database tables: `pulse_groups`, `pulse_group_members`, group post/comment tables, group invite/moderation tables, and linked conversation data.
- Existing Messenger chat route and native Chat screen for group chat handoff where conversation IDs are returned.
- Existing Search and Saved result routing for group/room URLs.

Do not duplicate in native:

- Group ownership and role checks.
- Membership authorization.
- Private group visibility.
- Invite-link generation.
- Group moderation and reporting rules.
- Group post/comment validation.
- Group chat creation/linkage rules.
- Room seeding logic.
- Server-side validation.

## What Must Be Rebuilt Natively

- Native Groups/Communities screen.
- Native group cards and group detail screen.
- Native default rooms list.
- Join/leave button states.
- Open group chat/room routing into native Messenger where supported.
- Group post list and composer hooks where existing APIs support them.
- Group post/comment display using reusable feed/comment components where safe.
- Search/filter/category UI if supported by existing APIs.
- Offline cache, pull-to-refresh, loading, empty, error, and retry states.
- Web fallback for unsupported admin/moderation/create/edit surfaces.

## Dependencies And Blockers

Dependencies:

- Confirm whether a JSON group browse/detail endpoint exists or whether the first slice should use existing search/group helper contracts plus group-specific action APIs.
- Map `/pulse/groups/<slug>` and `/pulse/messages?room=<slug>` targets into native group detail or Messenger routes.
- Reuse existing group membership, moderation, and group chat APIs.
- Keep unsupported owner/admin/moderation tools on web fallback.

Blockers:

- If no current JSON group browse/detail API exists, the safest first slice may need a thin backend JSON endpoint that reuses existing group queries without changing business logic.
- Real-device group chat routing and room recovery must be verified before replacing WebView group surfaces.

## Risk Level

Risk: Medium.

Reasons:

- Groups/Rooms touches membership, permissions, chat handoff, and moderation.
- Main risk is incorrect membership state or fallback routing.
- Backend risk stays low if native reuses server membership/chat/moderation endpoints and avoids duplicating role logic.

## Estimated Complexity

Complexity: Medium-high.

Recommended first slice:

- Inspect group browse/detail and group API response shapes.
- Add native group API wrappers only for existing endpoints or a thin read-only JSON bridge if no browse endpoint exists.
- Native Groups screen with community cards and default room cards.
- Native Group detail with metadata, posts preview, join/leave, open chat, report, and web fallback for unsupported admin tools.
- Native room open routing into Messenger where a conversation ID or room target is available.
- Pull-to-refresh and offline cache.
- Static audit proving group membership/moderation/chat rules stay backend-owned.

Defer from first slice:

- Full group creation/editing.
- Group admin dashboards.
- Member role management.
- Ban/unban and advanced moderation.
- Rich group media galleries.

## Safest Implementation Plan

1. Inspect group browse/detail pages, group action APIs, default room helpers, and native Messenger routing.
2. Prefer existing JSON endpoints. If a read-only JSON bridge is needed, keep it thin and reuse the existing group queries/business rules.
3. Add native group/room API wrappers without duplicating membership logic.
4. Add native Groups screen and Group detail screen.
5. Wire join/leave/open chat/report actions to existing APIs.
6. Route group and room URLs from Search/Saved/Notifications into native destinations where supported.
7. Keep admin, moderation, create/edit, and unsupported room surfaces on explicit web fallback.
8. Add a focused Groups audit and keep production WebView untouched.

## Recommendation Summary

Build Native Groups, Communities + Rooms Foundation next. Search and Saved can now expose group and room targets, Messenger is already native, and the backend has enough membership/chat/moderation infrastructure to reuse PulseSoc behavior while rebuilding only the native discovery, detail, and chat-entry UI.
